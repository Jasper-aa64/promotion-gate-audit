#!/usr/bin/env python3
"""Shared timing-history helpers for workflow scripts."""

from __future__ import annotations

import csv
import os
import platform
import statistics
from pathlib import Path
from typing import Iterable, Sequence


HISTORY_FILE_NAME = "timing_history.tsv"
HISTORY_VERSION = "timing_history_v2"
DEFAULT_WARM_OR_COLD = "measured"
DEFAULT_SAMPLE_UNIT = "ms"

HISTORY_FIELDNAMES = [
    "history_key",
    "recorded_at",
    "time_window",
    "bundle_id",
    "run_id",
    "source_attempts_path",
    "host_key",
    "control_head",
    "active_gate",
    "compatibility_group",
    "compatibility_tag",
    "warm_or_cold",
    "sample_unit",
    "kind",
    "policy_bucket",
    "experiment_kind",
    "target",
    "stage",
    "sample_count",
    "samples_ms",
    "samples",
    "mean_ms",
    "mean_seconds",
    "median_ms",
    "median_seconds",
    "mad_ms",
    "mad_seconds",
    "iqr_ms",
    "iqr_seconds",
    "stdev_ms",
    "stdev_seconds",
    "range_ms",
    "range_seconds",
    "delta_ms",
    "delta_seconds",
    "timing_verdict",
    "timing_verdict_reason",
    "timing_verdict_method",
    "control_sample_count",
    "candidate_sample_count",
    "paired_sample_count",
    "control_median_ms",
    "control_median_seconds",
    "control_samples_ms",
    "candidate_samples_ms",
    "paired_deltas_ms",
    "paired_deltas_seconds",
    "median_delta_ms",
    "median_delta_seconds",
    "bootstrap_ci_low_ms",
    "bootstrap_ci_high_ms",
    "bootstrap_ci_low_seconds",
    "bootstrap_ci_high_seconds",
    "permutation_p_value",
    "paired_stdev_ms",
    "paired_range_ms",
    "paired_mean_ms",
    "noise_flag",
    "verdict",
    "notes",
]

HISTORY_TABLE_COLUMN_PRIORITY = [
    "bundle_id",
    "run_id",
    "recorded_at",
    "time_window",
    "host_key",
    "control_head",
    "active_gate",
    "compatibility_group",
    "warm_or_cold",
    "sample_unit",
    "kind",
    "policy_bucket",
    "experiment_kind",
    "target",
    "stage",
    "sample_count",
    "samples_ms",
    "samples",
    "mean_ms",
    "median_ms",
    "mad_ms",
    "iqr_ms",
    "stdev_ms",
    "range_ms",
    "delta_ms",
    "timing_verdict",
    "timing_verdict_reason",
    "paired_sample_count",
    "control_median_ms",
    "control_median_seconds",
    "median_delta_ms",
    "bootstrap_ci_low_ms",
    "bootstrap_ci_high_ms",
    "permutation_p_value",
    "noise_flag",
    "verdict",
    "notes",
    "compatibility_tag",
    "source_attempts_path",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_host_key() -> str:
    for env_name in ("TIMING_HOST_KEY", "HARNESS_HOST_KEY", "HOST_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    node = platform.node().strip()
    return node or "unknown"


def clean_text(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_float(raw: str | None) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_int(raw: str | None) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def format_float(value: float | None, digits: int = 3) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def read_optional_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return read_tsv(path)


def write_tsv(path: Path, rows: list[dict[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def percentile(sorted_values: Sequence[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * (pct / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    lower_value = float(sorted_values[lower])
    upper_value = float(sorted_values[upper])
    return lower_value * (1.0 - fraction) + upper_value * fraction


def median_absolute_deviation(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    center = statistics.median(values)
    return statistics.median([abs(value - center) for value in values])


def interquartile_range(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return percentile(ordered, 75.0) - percentile(ordered, 25.0)


def sample_unit_to_ms_factor(sample_unit: str) -> float:
    normalized = clean_text(sample_unit).lower()
    if normalized in {"ms", "millisecond", "milliseconds"}:
        return 1.0
    if normalized in {"seconds", "second", "s", "seconds_compat"}:
        return 1000.0
    return 1.0


def samples_to_ms(samples: Sequence[float], sample_unit: str) -> list[float]:
    factor = sample_unit_to_ms_factor(sample_unit)
    return [sample * factor for sample in samples]


def parse_samples_text(raw: str | None, sample_unit: str = DEFAULT_SAMPLE_UNIT) -> list[float]:
    if raw in (None, ""):
        return []
    parsed = [float(part.strip()) for part in str(raw).split(",") if part.strip()]
    if sample_unit_to_ms_factor(sample_unit) == 1.0:
        return parsed
    return samples_to_ms(parsed, sample_unit)


def history_time_window(recorded_at: str | None) -> str:
    text = clean_text(recorded_at)
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return text or "unknown"


def history_compatibility_group(host_key: str, control_head: str, active_gate: str) -> str:
    return (
        f"{HISTORY_VERSION}|host_key={clean_text(host_key) or 'unknown'}"
        f"|control_head={clean_text(control_head) or 'unknown'}"
        f"|active_gate={clean_text(active_gate) or 'unknown'}"
    )


def legacy_history_compatibility_group(control_head: str, sample_unit: str) -> str:
    return f"timing_history_v1|control_head={clean_text(control_head) or 'unknown'}|sample_unit={clean_text(sample_unit) or DEFAULT_SAMPLE_UNIT}"


def history_compatibility_tag(
    compatibility_group: str,
    *,
    warm_or_cold: str,
    sample_unit: str,
    time_window: str,
    bundle_id: str,
    kind: str,
    target: str,
    stage: str = "",
) -> str:
    parts = [
        compatibility_group,
        f"warm_or_cold={clean_text(warm_or_cold) or DEFAULT_WARM_OR_COLD}",
        f"sample_unit={clean_text(sample_unit) or DEFAULT_SAMPLE_UNIT}",
        f"time_window={clean_text(time_window) or 'unknown'}",
        f"bundle_id={clean_text(bundle_id) or 'unknown'}",
        f"kind={clean_text(kind) or 'unknown'}",
        f"target={clean_text(target) or 'unknown'}",
    ]
    if clean_text(stage):
        parts.append(f"stage={clean_text(stage)}")
    return "|".join(parts)


def history_row_identity(row: dict[str, str]) -> str:
    for key in ("history_key", "compatibility_tag"):
        value = clean_text(row.get(key))
        if value:
            return value
    return "|".join(
        [
            clean_text(row.get("bundle_id")),
            clean_text(row.get("run_id")),
            clean_text(row.get("control_head")),
            clean_text(row.get("target")),
            clean_text(row.get("kind")),
            clean_text(row.get("recorded_at")),
        ]
    )


def history_row_sort_key(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        clean_text(row.get("recorded_at")),
        clean_text(row.get("bundle_id")),
        clean_text(row.get("kind")),
        clean_text(row.get("target")),
        clean_text(row.get("history_key")),
    )


def experiment_root_for_path(path: Path) -> Path:
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(repo_root())
    except ValueError:
        return resolved.parent
    if len(rel.parts) >= 2 and rel.parts[0] == "experiments":
        return repo_root() / rel.parts[0] / rel.parts[1]
    return resolved.parent


def bundle_id_for_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(repo_root())
    except ValueError:
        return resolved.name
    if len(rel.parts) >= 2 and rel.parts[0] == "experiments":
        experiment_root = repo_root() / rel.parts[0] / rel.parts[1]
        try:
            return resolved.relative_to(experiment_root).as_posix()
        except ValueError:
            return resolved.name
    return rel.as_posix()


def shared_history_path_for_output(output_dir: Path) -> Path:
    return experiment_root_for_path(output_dir) / HISTORY_FILE_NAME


def per_run_history_path(output_dir: Path) -> Path:
    return output_dir / HISTORY_FILE_NAME


def history_path_candidates(base_dir: Path) -> list[Path]:
    resolved = base_dir.resolve()
    candidates = [resolved / HISTORY_FILE_NAME]
    if resolved.parent != resolved:
        candidates.append(resolved.parent / HISTORY_FILE_NAME)
    experiment_root = experiment_root_for_path(resolved)
    candidates.append(experiment_root / HISTORY_FILE_NAME)
    if experiment_root.parent != experiment_root:
        candidates.append(experiment_root.parent / HISTORY_FILE_NAME)

    seen: set[str] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return ordered


def sample_statistics_from_ms(samples_ms: Sequence[float], noise_flag: str | None = None) -> dict[str, str]:
    if not samples_ms:
        return {
            "sample_count": "",
            "samples_ms": "",
            "samples": "",
            "mean_ms": "",
            "mean_seconds": "",
            "median_ms": "",
            "median_seconds": "",
            "mad_ms": "",
            "mad_seconds": "",
            "iqr_ms": "",
            "iqr_seconds": "",
            "stdev_ms": "",
            "stdev_seconds": "",
            "range_ms": "",
            "range_seconds": "",
            "noise_flag": noise_flag or "",
        }

    ordered = [float(sample) for sample in samples_ms]
    samples_seconds = [sample / 1000.0 for sample in ordered]
    mean_ms = statistics.mean(ordered)
    median_ms = statistics.median(ordered)
    mad_ms = median_absolute_deviation(ordered)
    iqr_ms = interquartile_range(ordered)
    stdev_ms = statistics.stdev(ordered) if len(ordered) > 1 else 0.0
    range_ms = max(ordered) - min(ordered)
    return {
        "sample_count": str(len(ordered)),
        "samples_ms": ",".join(f"{sample:g}" for sample in ordered),
        "samples": ",".join(f"{sample:g}" for sample in samples_seconds),
        "mean_ms": f"{mean_ms:.3f}",
        "mean_seconds": f"{mean_ms / 1000.0:.3f}",
        "median_ms": f"{median_ms:.3f}",
        "median_seconds": f"{median_ms / 1000.0:.3f}",
        "mad_ms": f"{mad_ms:.3f}",
        "mad_seconds": f"{mad_ms / 1000.0:.3f}",
        "iqr_ms": f"{iqr_ms:.3f}",
        "iqr_seconds": f"{iqr_ms / 1000.0:.3f}",
        "stdev_ms": f"{stdev_ms:.3f}",
        "stdev_seconds": f"{stdev_ms / 1000.0:.3f}",
        "range_ms": f"{range_ms:.3f}",
        "range_seconds": f"{range_ms / 1000.0:.3f}",
        "noise_flag": noise_flag or "",
    }


def normalize_history_row(
    row: dict[str, str],
    *,
    fallback_host_key: str | None = None,
    default_control_head: str | None = None,
    default_active_gate: str | None = None,
    default_sample_unit: str | None = None,
    default_warm_or_cold: str | None = None,
    default_bundle_id: str | None = None,
    default_run_id: str | None = None,
    default_time_window: str | None = None,
    default_source_attempts_path: str | None = None,
    default_recorded_at: str | None = None,
) -> dict[str, str]:
    normalized = {str(key): clean_text(value) for key, value in row.items() if key is not None}

    recorded_at = normalized.get("recorded_at") or clean_text(default_recorded_at)
    bundle_id = normalized.get("bundle_id") or normalized.get("run_id") or clean_text(default_bundle_id) or clean_text(default_run_id)
    run_id = normalized.get("run_id") or bundle_id or clean_text(default_run_id)
    host_key = normalized.get("host_key") or clean_text(fallback_host_key)
    control_head = normalized.get("control_head") or clean_text(default_control_head)
    active_gate = normalized.get("active_gate") or clean_text(default_active_gate)
    sample_unit = normalized.get("sample_unit") or clean_text(default_sample_unit) or (
        "ms" if normalized.get("samples_ms") else "seconds_compat" if normalized.get("samples") else ""
    )
    warm_or_cold = normalized.get("warm_or_cold") or clean_text(default_warm_or_cold)
    time_window = normalized.get("time_window") or clean_text(default_time_window) or history_time_window(recorded_at)
    source_attempts_path = normalized.get("source_attempts_path") or clean_text(default_source_attempts_path)

    compatibility_group = normalized.get("compatibility_group")
    if not compatibility_group:
        if clean_text(fallback_host_key) or clean_text(default_control_head) or clean_text(default_active_gate):
            compatibility_group = history_compatibility_group(host_key, control_head, active_gate)
        else:
            compatibility_tag = normalized.get("compatibility_tag")
            if compatibility_tag and "|bundle_id=" in compatibility_tag:
                compatibility_group = compatibility_tag.split("|bundle_id=", 1)[0]
            else:
                compatibility_group = legacy_history_compatibility_group(control_head or "unknown", sample_unit or DEFAULT_SAMPLE_UNIT)

    compatibility_tag = normalized.get("compatibility_tag")

    samples_ms_raw = normalized.get("samples_ms")
    sample_values_ms: list[float] = []
    if samples_ms_raw:
        sample_values_ms = [float(part.strip()) for part in samples_ms_raw.split(",") if part.strip()]
    elif normalized.get("samples"):
        sample_values_ms = parse_samples_text(normalized.get("samples"), sample_unit or DEFAULT_SAMPLE_UNIT)

    stats = sample_statistics_from_ms(sample_values_ms, normalized.get("noise_flag"))
    if sample_values_ms:
        parsed_sample_count = parse_int(normalized.get("sample_count"))
        if parsed_sample_count is None or parsed_sample_count <= 0:
            normalized["sample_count"] = stats["sample_count"]
        normalized["samples_ms"] = stats["samples_ms"]
        normalized["samples"] = stats["samples"]
        for key in (
            "mean_ms",
            "mean_seconds",
            "median_ms",
            "median_seconds",
            "mad_ms",
            "mad_seconds",
            "iqr_ms",
            "iqr_seconds",
            "stdev_ms",
            "stdev_seconds",
            "range_ms",
            "range_seconds",
        ):
            normalized[key] = stats[key]
        if not normalized.get("noise_flag"):
            normalized["noise_flag"] = stats["noise_flag"]

    history_key = normalized.get("history_key") or compatibility_tag or history_compatibility_tag(
        compatibility_group,
        warm_or_cold=warm_or_cold,
        sample_unit=sample_unit or DEFAULT_SAMPLE_UNIT,
        time_window=time_window,
        bundle_id=bundle_id or run_id or "unknown",
        kind=normalized.get("kind") or "unknown",
        target=normalized.get("target") or normalized.get("stage") or "unknown",
        stage=normalized.get("stage", ""),
    )
    normalized["history_key"] = history_key
    normalized["recorded_at"] = recorded_at
    normalized["time_window"] = time_window
    normalized["bundle_id"] = bundle_id
    normalized["run_id"] = run_id
    normalized["source_attempts_path"] = source_attempts_path
    normalized["host_key"] = host_key
    normalized["control_head"] = control_head
    normalized["active_gate"] = active_gate
    normalized["compatibility_group"] = compatibility_group
    normalized["compatibility_tag"] = compatibility_tag or history_key
    normalized["warm_or_cold"] = warm_or_cold
    normalized["sample_unit"] = sample_unit
    normalized["sample_count"] = normalized.get("sample_count", "")
    normalized["kind"] = normalized.get("kind") or normalized.get("lane") or normalized.get("policy_bucket") or ""
    normalized["policy_bucket"] = normalized.get("policy_bucket", "")
    normalized["experiment_kind"] = normalized.get("experiment_kind", "")
    normalized["target"] = normalized.get("target") or normalized.get("stage") or ""
    normalized["stage"] = normalized.get("stage", "")
    normalized["notes"] = normalized.get("notes", "")
    normalized["verdict"] = normalized.get("verdict", "")
    normalized["delta_ms"] = normalized.get("delta_ms", "")
    normalized["delta_seconds"] = normalized.get("delta_seconds", "")
    normalized["timing_verdict"] = normalized.get("timing_verdict", "")
    normalized["timing_verdict_reason"] = normalized.get("timing_verdict_reason", "")
    normalized["timing_verdict_method"] = normalized.get("timing_verdict_method", "")
    normalized["control_sample_count"] = normalized.get("control_sample_count", "")
    normalized["candidate_sample_count"] = normalized.get("candidate_sample_count", "")
    normalized["paired_sample_count"] = normalized.get("paired_sample_count", "")
    normalized["control_median_ms"] = normalized.get("control_median_ms", "")
    normalized["control_median_seconds"] = normalized.get("control_median_seconds", "")
    normalized["control_samples_ms"] = normalized.get("control_samples_ms", "")
    normalized["candidate_samples_ms"] = normalized.get("candidate_samples_ms", "")
    normalized["paired_deltas_ms"] = normalized.get("paired_deltas_ms", "")
    normalized["paired_deltas_seconds"] = normalized.get("paired_deltas_seconds", "")
    normalized["median_delta_ms"] = normalized.get("median_delta_ms", "")
    normalized["median_delta_seconds"] = normalized.get("median_delta_seconds", "")
    normalized["bootstrap_ci_low_ms"] = normalized.get("bootstrap_ci_low_ms", "")
    normalized["bootstrap_ci_high_ms"] = normalized.get("bootstrap_ci_high_ms", "")
    normalized["bootstrap_ci_low_seconds"] = normalized.get("bootstrap_ci_low_seconds", "")
    normalized["bootstrap_ci_high_seconds"] = normalized.get("bootstrap_ci_high_seconds", "")
    normalized["permutation_p_value"] = normalized.get("permutation_p_value", "")
    normalized["paired_stdev_ms"] = normalized.get("paired_stdev_ms", "")
    normalized["paired_range_ms"] = normalized.get("paired_range_ms", "")
    normalized["paired_mean_ms"] = normalized.get("paired_mean_ms", "")
    normalized["noise_flag"] = normalized.get("noise_flag", "")
    control_median_ms = parse_float(normalized.get("control_median_ms"))
    control_median_seconds = parse_float(normalized.get("control_median_seconds"))
    if control_median_ms is None and control_median_seconds is not None:
        normalized["control_median_ms"] = f"{control_median_seconds * 1000.0:.3f}"
    if control_median_seconds is None and control_median_ms is not None:
        normalized["control_median_seconds"] = f"{control_median_ms / 1000.0:.3f}"
    for key in HISTORY_FIELDNAMES:
        normalized.setdefault(key, "")
    return normalized


def history_rows_from_attempt_rows(
    attempt_rows: Sequence[dict[str, str]],
    *,
    bundle_id: str,
    run_id: str,
    host_key: str | None = None,
    control_head: str | None = None,
    active_gate: str | None = None,
    warm_or_cold: str = DEFAULT_WARM_OR_COLD,
    sample_unit: str = DEFAULT_SAMPLE_UNIT,
    source_attempts_path: str | None = None,
    recorded_at: str | None = None,
    default_noise_flag: str | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for attempt in attempt_rows:
        sample_count = parse_int(attempt.get("sample_count"))
        samples_ms_raw = clean_text(attempt.get("samples_ms"))
        samples_raw = clean_text(attempt.get("samples"))
        if sample_count in (None, 0) and not samples_ms_raw and not samples_raw:
            continue
        row = {
            "bundle_id": bundle_id,
            "run_id": run_id,
            "source_attempts_path": source_attempts_path or "",
            "recorded_at": clean_text(attempt.get("recorded_at")) or clean_text(recorded_at),
            "host_key": clean_text(attempt.get("host_key")) or clean_text(host_key),
            "control_head": clean_text(attempt.get("control_head")) or clean_text(control_head),
            "active_gate": clean_text(attempt.get("active_gate")) or clean_text(active_gate),
            "warm_or_cold": clean_text(attempt.get("warm_or_cold")) or clean_text(warm_or_cold),
            "sample_unit": clean_text(attempt.get("sample_unit")) or clean_text(sample_unit),
            "kind": clean_text(attempt.get("kind")) or clean_text(attempt.get("lane")) or clean_text(attempt.get("policy_bucket")),
            "policy_bucket": clean_text(attempt.get("policy_bucket")),
            "experiment_kind": clean_text(attempt.get("experiment_kind")),
            "target": clean_text(attempt.get("target")) or clean_text(attempt.get("stage")),
            "stage": clean_text(attempt.get("stage")),
            "sample_count": clean_text(attempt.get("sample_count")),
            "samples_ms": samples_ms_raw,
            "samples": samples_raw,
            "mean_ms": clean_text(attempt.get("mean_ms")),
            "mean_seconds": clean_text(attempt.get("mean_seconds")),
            "median_ms": clean_text(attempt.get("median_ms")),
            "median_seconds": clean_text(attempt.get("median_seconds")),
            "mad_ms": clean_text(attempt.get("mad_ms")),
            "mad_seconds": clean_text(attempt.get("mad_seconds")),
            "iqr_ms": clean_text(attempt.get("iqr_ms")),
            "iqr_seconds": clean_text(attempt.get("iqr_seconds")),
            "stdev_ms": clean_text(attempt.get("stdev_ms")),
            "stdev_seconds": clean_text(attempt.get("stdev_seconds")),
            "range_ms": clean_text(attempt.get("range_ms")),
            "range_seconds": clean_text(attempt.get("range_seconds")),
            "delta_ms": clean_text(attempt.get("delta_ms")),
            "delta_seconds": clean_text(attempt.get("delta_seconds")),
            "timing_verdict": clean_text(attempt.get("timing_verdict")),
            "timing_verdict_reason": clean_text(attempt.get("timing_verdict_reason")),
            "timing_verdict_method": clean_text(attempt.get("timing_verdict_method")),
            "control_sample_count": clean_text(attempt.get("control_sample_count")),
            "candidate_sample_count": clean_text(attempt.get("candidate_sample_count")),
            "paired_sample_count": clean_text(attempt.get("paired_sample_count")),
            "control_median_ms": clean_text(attempt.get("control_median_ms")),
            "control_median_seconds": clean_text(attempt.get("control_median_seconds")),
            "control_samples_ms": clean_text(attempt.get("control_samples_ms")),
            "candidate_samples_ms": clean_text(attempt.get("candidate_samples_ms")),
            "paired_deltas_ms": clean_text(attempt.get("paired_deltas_ms")),
            "paired_deltas_seconds": clean_text(attempt.get("paired_deltas_seconds")),
            "median_delta_ms": clean_text(attempt.get("median_delta_ms")),
            "median_delta_seconds": clean_text(attempt.get("median_delta_seconds")),
            "bootstrap_ci_low_ms": clean_text(attempt.get("bootstrap_ci_low_ms")),
            "bootstrap_ci_high_ms": clean_text(attempt.get("bootstrap_ci_high_ms")),
            "bootstrap_ci_low_seconds": clean_text(attempt.get("bootstrap_ci_low_seconds")),
            "bootstrap_ci_high_seconds": clean_text(attempt.get("bootstrap_ci_high_seconds")),
            "permutation_p_value": clean_text(attempt.get("permutation_p_value")),
            "paired_stdev_ms": clean_text(attempt.get("paired_stdev_ms")),
            "paired_range_ms": clean_text(attempt.get("paired_range_ms")),
            "paired_mean_ms": clean_text(attempt.get("paired_mean_ms")),
            "noise_flag": clean_text(attempt.get("noise_flag")) or clean_text(default_noise_flag),
            "verdict": clean_text(attempt.get("verdict")),
            "notes": clean_text(attempt.get("notes")),
        }
        rows.append(
            normalize_history_row(
                row,
                fallback_host_key=host_key,
                default_control_head=control_head,
                default_active_gate=active_gate,
                default_sample_unit=sample_unit,
                default_warm_or_cold=warm_or_cold,
                default_bundle_id=bundle_id,
                default_run_id=run_id,
                default_source_attempts_path=source_attempts_path,
                default_recorded_at=recorded_at,
            )
        )
    rows.sort(key=history_row_sort_key)
    return rows


def read_history_rows(paths: Iterable[Path]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for row in read_tsv(path):
            normalized = normalize_history_row(row)
            identity = history_row_identity(normalized)
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(normalized)
    merged.sort(key=history_row_sort_key)
    return merged


def select_history_rows(
    rows: Sequence[dict[str, str]],
    *,
    host_key: str,
    control_head: str,
    active_gate: str,
    sample_unit: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    candidates = [
        history_compatibility_group(host_key, control_head, active_gate),
        legacy_history_compatibility_group(control_head, sample_unit),
    ]

    def matches(row: dict[str, str], group: str) -> bool:
        compatibility_group = clean_text(row.get("compatibility_group"))
        compatibility_tag = clean_text(row.get("compatibility_tag"))
        return compatibility_group == group or compatibility_tag == group

    selected_group = candidates[-1]
    compatible: list[dict[str, str]] = []
    for group in candidates:
        group_rows = [row for row in rows if matches(row, group)]
        if group_rows:
            compatible = group_rows
            selected_group = group
            break

    compatible_ids = {history_row_identity(row) for row in compatible}
    incompatible = [row for row in rows if history_row_identity(row) not in compatible_ids and clean_text(row.get("compatibility_group"))]
    return compatible, incompatible, selected_group


def history_table_columns(rows: Sequence[dict[str, str]]) -> list[str]:
    columns: list[str] = []
    for column in HISTORY_TABLE_COLUMN_PRIORITY:
        if any(clean_text(row.get(column)) for row in rows):
            columns.append(column)
    return columns


def history_context_from_sources(
    control_row: dict[str, str] | None,
    run_state: dict[str, object] | None,
    history_rows: Sequence[dict[str, str]],
) -> dict[str, str]:
    control_row = control_row or {}
    run_state = run_state or {}
    control_head = clean_text(control_row.get("control_head")) or clean_text(run_state.get("control_head"))
    matching_rows = [row for row in history_rows if clean_text(row.get("control_head")) == control_head] if control_head else []
    matching_rows.sort(key=history_row_sort_key)
    latest_row = matching_rows[-1] if matching_rows else {}

    recorded_at = (
        clean_text(control_row.get("recorded_at"))
        or clean_text(run_state.get("updated_at"))
        or clean_text(run_state.get("started_at"))
        or clean_text(latest_row.get("recorded_at"))
    )
    sample_unit = (
        clean_text(control_row.get("sample_unit"))
        or clean_text(latest_row.get("sample_unit"))
        or ("ms" if clean_text(control_row.get("samples_ms")) else "seconds_compat" if clean_text(control_row.get("samples")) else DEFAULT_SAMPLE_UNIT)
    )
    return {
        "host_key": clean_text(run_state.get("host_key")) or clean_text(control_row.get("host_key")) or clean_text(latest_row.get("host_key")) or default_host_key(),
        "control_head": control_head or "unknown",
        "active_gate": clean_text(run_state.get("active_gate")) or clean_text(control_row.get("active_gate")) or clean_text(latest_row.get("active_gate")) or "unknown",
        "warm_or_cold": clean_text(run_state.get("warm_or_cold")) or clean_text(control_row.get("warm_or_cold")) or clean_text(latest_row.get("warm_or_cold")) or DEFAULT_WARM_OR_COLD,
        "sample_unit": sample_unit,
        "time_window": clean_text(run_state.get("time_window")) or clean_text(control_row.get("time_window")) or clean_text(latest_row.get("time_window")) or history_time_window(recorded_at),
        "recorded_at": recorded_at,
    }


def write_history_artifacts(
    shared_path: Path,
    per_run_path: Path,
    rows: Sequence[dict[str, str]],
) -> tuple[Path, Path]:
    normalized_rows = [normalize_history_row(row) for row in rows]
    upsert_history_rows(shared_path, normalized_rows)
    if shared_path.resolve() != per_run_path.resolve():
        write_tsv(per_run_path, normalized_rows, HISTORY_FIELDNAMES)
    return shared_path, per_run_path


def upsert_history_rows(path: Path, new_rows: Sequence[dict[str, str]]) -> None:
    existing_rows: list[dict[str, str]] = []
    if path.exists():
        existing_rows = read_tsv(path)

    merged: dict[str, dict[str, str]] = {}
    ordered: list[str] = []
    for row in existing_rows:
        normalized = normalize_history_row(row)
        identity = history_row_identity(normalized)
        if identity not in merged:
            ordered.append(identity)
        merged[identity] = normalized

    for row in new_rows:
        normalized = normalize_history_row(row)
        identity = history_row_identity(normalized)
        if identity not in merged:
            ordered.append(identity)
        merged[identity] = normalized

    fieldnames = list(HISTORY_FIELDNAMES)
    extra_fields: list[str] = []
    seen_fields = set(fieldnames)
    for row in list(existing_rows) + list(new_rows):
        for key in row.keys():
            if key not in seen_fields:
                seen_fields.add(key)
                extra_fields.append(key)
    fieldnames.extend(sorted(extra_fields))

    sorted_rows = [merged[identity] for identity in ordered]
    sorted_rows.sort(key=history_row_sort_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in sorted_rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp_path.replace(path)
