#!/usr/bin/env python3
"""Recompute the public fmt study from the self-contained evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any

sys.dont_write_bytecode = True

from frozen_judge_v1 import (  # noqa: E402
    JUDGE_FIELDS,
    PROMOTION_VERDICTS,
    SIGNAL_VERDICTS,
    evaluate_stage,
)


class PublicRecomputeError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicRecomputeError(message)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"JSON root must be an object: {path.name}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    _require(all(isinstance(row, dict) for row in rows), "candidate rows must be objects")
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_inventory(package: Path) -> int:
    inventory = _load_json(package / "artifact_inventory.json")
    files = inventory.get("files")
    _require(isinstance(files, list), "artifact inventory has no file list")
    expected_paths: set[str] = set()
    for row in files:
        relative = str(row["path"])
        expected_paths.add(relative)
        path = package / relative
        _require(path.is_file(), f"inventory file missing: {relative}")
        _require(path.stat().st_size == int(row["bytes"]), f"inventory size mismatch: {relative}")
        _require(_sha256(path) == row["sha256"], f"inventory hash mismatch: {relative}")
    actual_paths = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path.name != "artifact_inventory.json" and "__pycache__" not in path.parts
    }
    _require(
        actual_paths == expected_paths,
        f"artifact inventory path set mismatch: "
        f"missing={sorted(expected_paths - actual_paths)} "
        f"extra={sorted(actual_paths - expected_paths)}",
    )
    return len(files)


def _verdict_context(binding: dict[str, Any], candidate_id: str, depth: int) -> str:
    return json.dumps(
        {
            "study_id": binding["study_id"],
            "candidate_id": candidate_id,
            "pair_index": list(range(1, depth + 1)),
            "stage": f"m{depth}",
            "depth": depth,
            "control_commit": binding["source_commit"],
            "manifest_sha256": binding["final_manifest_sha256"],
            "run_order_seed": binding["run_order_seed"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _values_equal(stored: Any, rendered: str) -> bool:
    """Compare one typed stored field against the frozen judge string output."""

    if stored is None:
        return rendered in ("", None)
    if isinstance(stored, bool):
        return rendered == ("true" if stored else "false")
    if isinstance(stored, int):
        return rendered == str(stored)
    if isinstance(stored, float):
        try:
            return float(rendered) == stored
        except ValueError:
            return False
    return str(stored) == str(rendered)


def _assert_judge_match(
    candidate_id: str, depth: int, stored: dict[str, Any], recomputed: dict[str, str]
) -> None:
    mismatches = {
        field: {"stored": stored.get(field), "recomputed": recomputed.get(field)}
        for field in JUDGE_FIELDS
        if not _values_equal(stored.get(field, ""), recomputed.get(field, ""))
    }
    _require(not mismatches, f"{candidate_id} m{depth} judge mismatch: {mismatches}")


def _binomial_cdf(successes: int, trials: int, probability: float) -> float:
    return math.fsum(
        math.comb(trials, value)
        * probability**value
        * (1.0 - probability) ** (trials - value)
        for value in range(successes + 1)
    )


def _clopper_pearson_upper(successes: int, trials: int, alpha: float = 0.05) -> float:
    if successes == trials:
        return 1.0
    if successes == 0:
        return 1.0 - alpha ** (1.0 / trials)
    low = successes / trials
    high = 1.0
    while high - low > 1e-13:
        mid = (low + high) / 2.0
        if _binomial_cdf(successes, trials, mid) > alpha:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _control_stats(samples: list[float]) -> dict[str, Any]:
    _require(bool(samples), "preflight samples must not be empty")
    median = statistics.median(samples)
    stdev = statistics.stdev(samples) if len(samples) > 1 else 0.0
    range_ms = max(samples) - min(samples) if len(samples) > 1 else 0.0
    return {
        "control_median_ms": median,
        "control_mean_ms": statistics.mean(samples),
        "control_stdev_ms": stdev,
        "control_range_ms": range_ms,
        "control_cov": stdev / median if median else None,
        "control_range_ratio": range_ms / median if median else None,
    }


def _verify_preflight_and_cross_session(
    package: Path,
    expected: dict[str, Any],
    real_final_judge: dict[str, Any],
) -> dict[str, str]:
    """Recompute the preflight jitter statistics from the custody samples and
    verify the published cross-session figures against the binding, the typed
    real-candidate judge and the execution custody."""
    custody = _load_json(package / "execution_custody.json")
    binding = _load_json(package / "study_binding.json")
    expected_preflights = expected.get("preflight_jitter_comparison")
    _require(
        isinstance(expected_preflights, list) and len(expected_preflights) == 2,
        "published preflight comparison is missing",
    )
    attempts = custody.get("weather_preflights")
    _require(
        isinstance(attempts, list) and len(attempts) == 2,
        "custody preflights are missing",
    )
    for index, (entry, expected_entry) in enumerate(
        zip(attempts, expected_preflights), start=1
    ):
        samples = [float(value) for value in entry["control_samples_ms"]]
        stats = _control_stats(samples)
        _require(
            int(expected_entry.get("attempt", -1)) == index,
            f"preflight {index}: attempt index mismatch",
        )
        _require(
            str(expected_entry.get("decision")) == str(entry.get("decision")),
            f"preflight {index}: decision mismatch",
        )
        stored_stats = entry.get("control_stats")
        _require(
            isinstance(stored_stats, dict),
            f"preflight {index}: custody control_stats missing",
        )
        for field, value in stats.items():
            _require(
                stored_stats.get(field) is not None
                and abs(float(stored_stats[field]) - float(value)) <= 1e-9,
                f"preflight {index}: custody control_stats drift on {field}",
            )
            _require(
                expected_entry.get(field) is not None
                and abs(float(expected_entry[field]) - float(value)) <= 1e-9,
                f"preflight {index}: published preflight drift on {field}",
            )
    calibration = binding.get("calibration_control_median_ms")
    _require(calibration is not None, "calibration control median is not bound")
    _require(
        abs(
            float(calibration)
            - float(expected.get("calibration_control_median_ms"))
        )
        <= 1e-9,
        "calibration control median binding mismatch",
    )
    formal = float(real_final_judge["control_median_ms"])
    _require(
        abs(formal - float(expected.get("formal_real_run_control_median_ms")))
        <= 1e-9,
        "formal run control median mismatch",
    )
    delta = (formal - float(calibration)) / float(calibration) * 100.0
    _require(
        abs(
            delta
            - float(
                expected.get("calibration_to_formal_control_median_delta_pct")
            )
        )
        <= 1e-9,
        "calibration-to-formal delta percent mismatch",
    )
    return {
        "preflight_jitter_consistency": "EXACT_MATCH",
        "cross_session_binding_consistency": "EXACT_MATCH",
    }


def _replay_judge_differential(package: Path) -> dict[str, Any]:
    """Replay the embedded fixed-seed differential cases with the frozen judge."""

    document = _load_json(package / "judge_differential_cases.json")
    seed = int(document.get("seed", -1))
    cases = document.get("cases")
    _require(isinstance(cases, list), "differential cases must be a list")
    _require(len(cases) == int(document.get("case_count", -1)), "differential case count mismatch")
    mismatches: list[dict[str, Any]] = []
    for case in cases:
        rendered = evaluate_stage(
            case["pairs"],
            verdict_context=str(case["verdict_context"]),
            screening_only=bool(case.get("screening_only")),
        )
        expected = case.get("expected", {})
        for field in JUDGE_FIELDS:
            if str(rendered.get(field, "")) != str(expected.get(field, "")):
                mismatches.append(
                    {
                        "case_id": case.get("case_id"),
                        "field": field,
                        "rendered": rendered.get(field),
                        "expected": expected.get(field),
                    }
                )
    return {
        "random_differential_status": (
            "EXACT_MATCH" if not mismatches else "MISMATCH"
        ),
        "random_differential_case_count": len(cases),
        "random_differential_seed": seed,
        "random_differential_mismatch_count": len(mismatches),
        "random_differential_mismatches": mismatches[:8],
    }


def recompute_package(package_root: Path) -> dict[str, Any]:
    package = Path(package_root).resolve()
    inventory_count = _verify_inventory(package)
    binding = _load_json(package / "study_binding.json")
    _require(binding.get("schema") == "fmt_public_formal_binding_v2", "binding schema mismatch")
    _require(
        binding.get("candidate_evidence_schema") == "fmt_public_candidate_evidence_v2",
        "candidate evidence schema mismatch",
    )
    expected = _load_json(package / "independent_recompute.json")
    evidence_path = package / "candidate_evidence.jsonl"
    _require(
        _sha256(evidence_path) == binding["v2_typed_jsonl_sha256"],
        "typed evidence hash mismatch",
    )
    rows = _load_jsonl(evidence_path)
    expected_ids = [f"fmt-{index:03d}" for index in range(1, 31)] + ["fmt_buffer_reuse"]
    _require([row.get("candidate_id") for row in rows] == expected_ids, "candidate inventory mismatch")

    recomputed_rows: list[dict[str, Any]] = []
    stage_match_count = 0
    typed_consistency_count = 0
    real_final_judge: dict[str, Any] | None = None
    for row in rows:
        _require(
            row.get("schema") == "fmt_public_candidate_evidence_v2",
            f"{row.get('candidate_id')}: typed schema mismatch",
        )
        candidate_id = str(row["candidate_id"])
        pairs = row["pairs"]
        depths = row["sample_depths_attempted"]
        stage_judges = row["stage_judges"]
        _require(len(depths) == len(stage_judges), f"{candidate_id}: stage structure mismatch")
        _require(depths[-1] == len(pairs), f"{candidate_id}: terminal depth mismatch")
        final: dict[str, str] | None = None
        for depth_value, stored in zip(depths, stage_judges):
            depth = int(depth_value)
            _require(depth in {4, 8, 12}, f"{candidate_id}: invalid depth")
            selected = pairs[:depth]
            _require(len(selected) == depth, f"{candidate_id}: incomplete m{depth}")
            final = evaluate_stage(
                selected,
                verdict_context=_verdict_context(binding, candidate_id, depth),
                screening_only=(depth == 4),
            )
            _assert_judge_match(candidate_id, depth, stored, final)
            stage_match_count += 1
        assert final is not None
        _assert_judge_match(candidate_id, int(depths[-1]), row["final_judge"], final)
        if row.get("oracle_label") == "REAL":
            real_final_judge = row["final_judge"]
        typed_consistent = (
            str(row.get("verdict")) == final["verdict"]
            and _values_equal(row.get("median_delta_ms"), final["median_delta_ms"])
            and _values_equal(row.get("bootstrap_ci_low_ms"), final["bootstrap_ci_low_ms"])
            and _values_equal(row.get("bootstrap_ci_high_ms"), final["bootstrap_ci_high_ms"])
            and _values_equal(row.get("permutation_p_value"), final["permutation_p_value"])
            and _values_equal(row.get("paired_sample_count"), final["paired_sample_count"])
            and _values_equal(row.get("naive_k1_would_accept"), final["naive_k1_would_accept"])
            and row.get("correctness_pass") is True
            and row.get("oracle_label") in {"FALSE", "REAL"}
        )
        _require(typed_consistent, f"{candidate_id}: typed ledger projection disagrees with final judge")
        typed_consistency_count += 1
        recomputed_rows.append(
            {
                "candidate_id": candidate_id,
                "oracle_label": row["oracle_label"],
                "verdict": final["verdict"],
                "naive": final["naive_k1_would_accept"] == "true",
                "bootstrap_only": float(final["bootstrap_ci_low_ms"]) > 0.0,
                "terminal_depth": int(depths[-1]),
                "correctness_pass": bool(row["correctness_pass"]),
            }
        )

    nulls = [row for row in recomputed_rows if row["oracle_label"] == "FALSE"]
    reals = [row for row in recomputed_rows if row["oracle_label"] == "REAL"]
    _require(len(nulls) == 30 and len(reals) == 1, "frozen denominator mismatch")
    false_promotions = sum(row["verdict"] in PROMOTION_VERDICTS for row in nulls)
    naive_false_promotions = sum(row["naive"] for row in nulls)
    all_naive_accepts = sum(row["naive"] for row in recomputed_rows)
    bootstrap_false_ids = [row["candidate_id"] for row in nulls if row["bootstrap_only"]]
    real_promotions = sum(row["verdict"] in PROMOTION_VERDICTS for row in reals)
    real_signals = sum(row["verdict"] in SIGNAL_VERDICTS for row in reals)
    aggregate = {
        "null_gate_false_promotions": false_promotions,
        "null_naive_false_promotions": naive_false_promotions,
        "null_gate_one_sided_95_upper": _clopper_pearson_upper(false_promotions, len(nulls)),
        "real_gate_promotions": real_promotions,
        "all_candidate_naive_accepts": all_naive_accepts,
        "real_naive_accept": bool(reals[0]["naive"]),
        "posthoc_bootstrap_only_false_promotions": len(bootstrap_false_ids),
        "posthoc_bootstrap_only_false_promotion_ids": bootstrap_false_ids,
        "posthoc_bootstrap_only_real_accepts": sum(row["bootstrap_only"] for row in reals),
    }
    for key, value in aggregate.items():
        _require(expected.get(key) == value, f"published aggregate mismatch: {key}")

    assert real_final_judge is not None
    cross_session = _verify_preflight_and_cross_session(
        package, expected, real_final_judge
    )

    correctness_passes = sum(row["correctness_pass"] for row in recomputed_rows)
    differential = _replay_judge_differential(package)
    return {
        "schema": "fmt_public_self_contained_recompute_v2",
        "status": "PUBLIC_RECOMPUTE_EXACT_MATCH",
        "inventory_file_count": inventory_count,
        "candidate_count": len(recomputed_rows),
        "typed_schema": "fmt_public_candidate_evidence_v2",
        "typed_ledger_judge_consistency_count": typed_consistency_count,
        "stage_judge_match_count": stage_match_count,
        **aggregate,
        "real_signal_detected_count": real_signals,
        "real_automatic_promotion_count": real_promotions,
        "posthoc_analysis_label": "post-hoc exploratory; not preregistered",
        **cross_session,
        **differential,
        "m4_terminal_candidate_count": sum(row["terminal_depth"] == 4 for row in recomputed_rows),
        "m8_terminal_candidate_count": sum(row["terminal_depth"] == 8 for row in recomputed_rows),
        "m12_terminal_candidate_count": sum(row["terminal_depth"] == 12 for row in recomputed_rows),
        "total_paired_sample_count": sum(row["terminal_depth"] for row in recomputed_rows),
        "correctness_pass_count": correctness_passes,
        "correctness_failure_count": len(recomputed_rows) - correctness_passes,
        "correctness_rejection_effectiveness_evaluable": correctness_passes != len(recomputed_rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    print(json.dumps(recompute_package(args.package), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
