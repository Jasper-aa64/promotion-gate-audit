#!/usr/bin/env python3
"""Block final performance reports until convergence and rerun evidence exist."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


REQUIRED_DELIVERY_STATES = (
    "CODE_COMPLETE",
    "CORRECTNESS_PASS",
    "PERF_PASS",
    "REPORT_READY",
)
TARGET_OPERATORS = frozenset({"<", "<=", ">", ">="})
METRIC_UNITS = frozenset({"ns", "us", "ms", "s"})
METRIC_CONTRACT_FIELDS = (
    "name",
    "start_event",
    "end_event",
    "percentile",
    "unit",
)
EVIDENCE_SCHEMA_VERSION = 1


def _performance_contract_version(performance: Mapping[str, Any]) -> int | None:
    """Return legacy v1 only for an absent version; fail closed otherwise."""
    if "contract_version" not in performance:
        return 1
    value = performance.get("contract_version")
    if isinstance(value, int) and not isinstance(value, bool) and value == 2:
        return 2
    return None


def _delivery_status(task_data: Mapping[str, Any], state: str) -> str:
    delivery = task_data.get("delivery")
    if not isinstance(delivery, Mapping):
        return "pending"
    row = delivery.get(state)
    if not isinstance(row, Mapping):
        return "pending"
    status = row.get("status")
    return status if isinstance(status, str) else "pending"


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value.lower())
    )


def _metric_contract(metric: Mapping[str, Any]) -> dict[str, Any] | None:
    if any(_missing(metric.get(field)) for field in METRIC_CONTRACT_FIELDS):
        return None
    return {field: metric[field] for field in METRIC_CONTRACT_FIELDS}


def _metric_contract_sha256(contract: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(contract), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _percentile_rank(percentile: str) -> int | None:
    match = re.fullmatch(r"p([1-9][0-9]?|100)", percentile)
    return int(match.group(1)) if match else None


def _nearest_rank(samples: list[float], percentile: str) -> float | None:
    rank = _percentile_rank(percentile)
    if rank is None or not samples:
        return None
    index = max(0, math.ceil(len(samples) * rank / 100) - 1)
    return sorted(samples)[index]


def _resolve_evidence_path(value: str, evidence_root: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (evidence_root or Path.cwd()) / path


def _measurement_evidence_issues(
    performance: Mapping[str, Any],
    metric: Mapping[str, Any],
    role: str,
    evidence_root: Path | None,
) -> tuple[list[str], float | None, int | None]:
    """Read one normalized artifact and return its recomputed measurement."""
    issues: list[str] = []
    expected_contract = _metric_contract(metric)
    if expected_contract is None:
        return issues, None, None
    expected_hash = _metric_contract_sha256(expected_contract)
    record_name = "baseline_measurement" if role == "baseline" else "final_measurement"
    record = performance.get(record_name)
    if not isinstance(record, Mapping):
        return [f"{role} measurement is required"], None, None

    evidence = record.get("evidence")
    if _missing(evidence):
        issues.append(f"{role} measurement evidence is required")
        return issues, None, None
    if not _is_sha256(record.get("evidence_sha256")):
        issues.append(f"{role} measurement evidence SHA-256 is required")
        return issues, None, None
    if record.get("metric_contract_sha256") != expected_hash:
        issues.append(f"{role} measurement contract hash does not match primary metric")

    path = _resolve_evidence_path(str(evidence), evidence_root)
    try:
        raw = path.read_bytes()
    except OSError:
        return issues + [f"{role} measurement evidence file is missing"], None, None
    if hashlib.sha256(raw).hexdigest() != record.get("evidence_sha256"):
        return issues + [f"{role} measurement evidence digest does not match"], None, None
    try:
        artifact = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return issues + [f"{role} measurement evidence JSON is invalid"], None, None
    if not isinstance(artifact, Mapping):
        return issues + [f"{role} measurement evidence must be an object"], None, None
    if artifact.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        issues.append(f"{role} measurement evidence schema_version is invalid")
    artifact_contract = artifact.get("measurement_contract")
    if not isinstance(artifact_contract, Mapping):
        return issues + [f"{role} measurement contract is required"], None, None
    if dict(artifact_contract) != expected_contract:
        issues.append(f"{role} measurement contract does not match primary metric")
    artifact_hash = _metric_contract_sha256(artifact_contract)
    if artifact.get("measurement_contract_sha256") != artifact_hash:
        issues.append(f"{role} measurement evidence contract hash is invalid")
    if artifact_hash != expected_hash:
        issues.append(f"{role} measurement contract does not match primary metric")
    if artifact.get("correctness_status") != "PASS":
        issues.append(f"{role} measurement correctness_status must be PASS")

    raw_samples = artifact.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples or not all(
        _number(value) for value in raw_samples
    ):
        return issues + [f"{role} measurement samples are invalid"], None, None
    samples = [float(value) for value in raw_samples]
    summary = artifact.get("summary")
    if not isinstance(summary, Mapping):
        return issues + [f"{role} measurement summary is required"], None, None
    percentile = expected_contract["percentile"]
    if summary.get("percentile") != percentile:
        issues.append(f"{role} measurement summary percentile does not match contract")
    if summary.get("sample_count") != len(samples):
        issues.append(f"{role} measurement summary sample count does not match samples")
    observed = _nearest_rank(samples, percentile)
    if observed is None:
        issues.append(f"{role} measurement percentile is invalid")
    elif not _number(summary.get("value")) or not math.isclose(
        float(summary["value"]), observed, rel_tol=0.0, abs_tol=1e-9
    ):
        issues.append(f"{role} measurement summary value does not match samples")
    return issues, observed, len(samples)


def _target_met(operator: str, observed: float, target: float) -> bool:
    comparisons = {
        "<": observed < target,
        "<=": observed <= target,
        ">": observed > target,
        ">=": observed >= target,
    }
    return comparisons.get(operator, False)


def _v2_target_issues(
    performance: Mapping[str, Any], evidence_root: Path | None
) -> list[str]:
    """Independently recheck a v2 metric contract before report generation."""
    issues: list[str] = []
    metric = performance.get("primary_metric")
    if not isinstance(metric, Mapping):
        return ["performance.primary_metric is required"]
    for field in ("name", "start_event", "end_event", "percentile", "unit", "target_operator"):
        if _missing(metric.get(field)):
            issues.append(f"performance.primary_metric.{field} is required")
    target = metric.get("target_value")
    if not _number(target):
        issues.append("performance.primary_metric.target_value is required")
    operator = metric.get("target_operator")
    if not _missing(operator) and operator not in TARGET_OPERATORS:
        issues.append("performance.primary_metric.target_operator is invalid")
    metric_unit = metric.get("unit")
    if not _missing(metric_unit) and metric_unit not in METRIC_UNITS:
        issues.append("performance.primary_metric.unit is invalid")
    expected_contract = _metric_contract(metric)
    if expected_contract is not None:
        expected_hash = _metric_contract_sha256(expected_contract)
        if performance.get("metric_contract_sha256") != expected_hash:
            issues.append("metric contract hash does not match primary metric")

    minimum = performance.get("minimum_final_samples")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum <= 0:
        issues.append("performance.minimum_final_samples must be greater than zero")

    measurement = performance.get("final_measurement")
    if not isinstance(measurement, Mapping):
        return issues + ["final measurement is required"]
    observed = measurement.get("value")
    if not _number(observed):
        issues.append("final measurement value is required")
    if _missing(measurement.get("evidence")):
        issues.append("final measurement evidence is required")
    if measurement.get("unit") != metric_unit:
        issues.append("final measurement unit must match primary metric unit")
    sample_count = measurement.get("sample_count")
    if (
        not isinstance(sample_count, int)
        or isinstance(sample_count, bool)
        or not isinstance(minimum, int)
        or sample_count < minimum
    ):
        issues.append("final measurement sample count is below the required minimum")
    baseline_issues, _, _ = _measurement_evidence_issues(
        performance, metric, "baseline", evidence_root
    )
    final_issues, evidence_value, evidence_sample_count = _measurement_evidence_issues(
        performance, metric, "final", evidence_root
    )
    issues.extend(baseline_issues)
    issues.extend(final_issues)
    if evidence_value is not None and _number(observed) and not math.isclose(
        float(observed), evidence_value, rel_tol=0.0, abs_tol=1e-9
    ):
        issues.append("final measurement value does not match evidence")
    if evidence_sample_count is not None and sample_count != evidence_sample_count:
        issues.append("final measurement sample count does not match evidence")
    if (
        evidence_value is not None
        and _number(target)
        and isinstance(operator, str)
        and not _target_met(operator, evidence_value, float(target))
    ):
        issues.append("final measurement does not meet the primary metric target")
    return issues


def report_gate_issues(
    task_data: Mapping[str, Any], evidence_root: Path | None = None
) -> list[str]:
    """Return reasons a final report must not be generated."""
    issues: list[str] = []
    for state in REQUIRED_DELIVERY_STATES:
        if _delivery_status(task_data, state) != "pass":
            issues.append(f"{state} must pass")

    meta = task_data.get("meta")
    performance = meta.get("performance") if isinstance(meta, Mapping) else None
    if not isinstance(performance, Mapping):
        return issues + ["performance metadata is required"]
    if performance.get("optimization_status") != "converged":
        issues.append("optimization_status must be converged")
    if not str(performance.get("convergence_evidence") or "").strip():
        issues.append("convergence_evidence is required")
    if not str(performance.get("final_rerun_evidence") or "").strip():
        issues.append("final_rerun_evidence is required")
    version = _performance_contract_version(performance)
    if version is None:
        issues.append("performance.contract_version must be exactly integer 2")
    elif version == 2:
        if not str(performance.get("paper_annex_evidence") or "").strip():
            issues.append("paper_annex_evidence is required")
        for field, label in (
            ("baseline_evidence", "baseline evidence"),
            ("stage_evidence", "stage evidence"),
            ("profile_evidence", "profile evidence"),
            ("candidate_audit_evidence", "candidate audit evidence"),
        ):
            if _missing(performance.get(field)):
                issues.append(f"{label} is required")
        issues.extend(_v2_target_issues(performance, evidence_root))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-json", type=Path, required=True)
    args = parser.parse_args()

    try:
        task_data = json.loads(args.task_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot read task json: {exc}")
        return 2

    workspace_root = args.task_json.parent
    for parent in args.task_json.resolve().parents:
        if parent.name == ".trellis":
            workspace_root = parent.parent
            break
    issues = report_gate_issues(task_data, workspace_root)
    if issues:
        print("BLOCKED: final performance report gate failed")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("PASS: final performance report gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
