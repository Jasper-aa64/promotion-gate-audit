#!/usr/bin/env python3
"""Prepare and record the control loop artifacts.

This script keeps the local HFT-wf tree as the evidence plane. It converts an
existing profile artifact directory into a stable control-loop state with:

* profile.tsv
* hotspots.tsv
* attempts.tsv
* cooldown.tsv
* timing_history.tsv

The profile runs recorded here are diagnostic only. They are not PASS/FAIL_PERF
events; they are planning and evidence rows for the next remote experiment.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from timing_history import (
    bundle_id_for_path,
    default_host_key,
    history_rows_from_attempt_rows,
    sample_statistics_from_ms,
    per_run_history_path,
    shared_history_path_for_output,
    write_history_artifacts,
)
from timing_analysis import PairedTimingEvidence, evidence_fields
from attempts_schema import ATTEMPTS_FIELDNAMES


DEFAULT_ACCEPTANCE_POLICY = (
    "compare pass; evaluate candidate against the run-specific control baseline recorded in attempts.tsv"
)
DEFAULT_NOISE_RANGE_THRESHOLD = 3.0
DEFAULT_NOISE_STDEV_THRESHOLD = 1.0
DEFAULT_ATTEMPT_COST_SECONDS = 1800.0
DEFAULT_EXPECTED_CAPTURE_RATE = 0.03
DEFAULT_EVIDENCE_LAMBDA = 0.05
DEFAULT_UNCERTAINTY = 0.10


@dataclass(frozen=True)
class TargetSpec:
    target: str
    stage_aliases: tuple[str, ...]
    ownership_confidence: float
    correctness_safety: float
    locality: float
    bucket: str
    notes: str
    gate_confidence: float = 0.92
    cost_attempt_seconds: float = DEFAULT_ATTEMPT_COST_SECONDS
    uncertainty: float = DEFAULT_UNCERTAINTY


STAGE_NORMALIZATION: dict[str, str] = {
    # Generic example aliases for the curated public subset. The original
    # harness carries operator-specific stage names; these keep the
    # normalization machinery demonstrable without exposing them.
    "row_parse": "handler.row_parse",
    "row_field_copy": "handler.field_copy",
    "row_timestamp_filter": "handler.timestamp",
    "sort_ticks": "handler.sort_ticks",
    "allocation_setup": "handler.allocation_setup",
    "valid_compaction": "handler.valid_compaction",
    "table_generate": "write.table_generate",
    "table_write": "write.table_write",
    "cleanup_reset": "write.cleanup_reset",
    "handler_data": "handler.coarse",
    "compute_total": "compute.factor_on_tick",
}

# Generic example target/cooldown specs (illustrative; operators supply their
# own profile-derived rows in real runs).
GENERIC_TARGETS = (
    ("handler.row_parse", 0.90, 0.84, 1.00, "exploit",
     "Fine-grained row-loop hotspot in the handler read path."),
    ("handler.field_copy", 0.84, 0.96, 0.97, "reserve",
     "Field copy work in the row path."),
    ("handler.timestamp", 0.82, 0.95, 0.96, "reserve",
     "Timestamp filtering and conversion work."),
    ("handler.sort_ticks", 0.88, 0.97, 0.96, "reserve",
     "Tick sorting as a narrow, measurable read-path target."),
    ("handler.allocation_setup", 0.90, 0.98, 0.97, "reserve",
     "Allocation setup is low cost but safe for neutral-stack exploration."),
    ("handler.valid_compaction", 0.90, 0.99, 0.98, "reserve",
     "Valid-record compaction tracked as a safe fallback when present."),
    ("handler.coarse", 0.90, 0.84, 1.00, "exploit",
     "Coarse handler fallback when only bundled profile rows are available."),
    ("write.table_generate", 0.82, 0.96, 0.98, "exploit",
     "Fine-grained table generation cost inside the write path."),
    ("write.table_write", 0.76, 0.96, 0.96, "exploit",
     "Table write call and writer close path."),
    ("write.cleanup_reset", 0.90, 0.99, 0.98, "reserve",
     "Low-cost write cleanup/reset fallback for neutral-stack exploration."),
    ("read_table", 0.92, 0.96, 0.95, "reserve",
     "Low-risk read-path candidate reserved for exploration."),
    ("clear_data", 0.88, 0.97, 0.95, "reserve",
     "Low-risk cleanup candidate reserved for exploration."),
    ("compute.factor_on_tick", 0.65, 0.72, 0.75, "reserve",
     "Coarse compute fallback."),
)

TARGET_SPECS: tuple[TargetSpec, ...] = tuple(
    TargetSpec(
        target=target,
        stage_aliases=(target,),
        ownership_confidence=o,
        correctness_safety=r,
        locality=loc,
        bucket=bucket,
        notes=notes,
    )
    for target, o, r, loc, bucket, notes in GENERIC_TARGETS
)

COOLDOWN_SPECS = (
    {
        "target": "table_generate",
        "status": "cooldown",
        "cooldown_runs_remaining": 3,
        "reason": "cooldown unless profiler gives a narrower hypothesis",
        "notes": "Broader rewrites were already exhausted on earlier runs.",
    },
    {
        "target": "write_table",
        "status": "cooldown",
        "cooldown_runs_remaining": 2,
        "reason": "library / I/O dominated until a narrower split proves otherwise",
        "notes": "Keep this on hold until a finer profile isolates code-owned cost.",
    },
    {
        "target": "tick_string_skipping",
        "status": "cooldown",
        "cooldown_runs_remaining": 4,
        "reason": "semantic risk for future consumers that may read tick strings",
        "notes": "Measured as a gain, but not safe enough for automatic promotion.",
    },
    {
        "target": "index_lookup",
        "status": "cooldown",
        "cooldown_runs_remaining": 1,
        "reason": "already neutral under the current safe head",
        "notes": "Keep it available for review, but do not spend another greedy attempt yet.",
    },
    {
        "target": "prepass_code_set",
        "status": "cooldown",
        "cooldown_runs_remaining": 1,
        "reason": "neutral in prior run",
        "notes": "Earlier removal attempts were neutral and should not be retried blindly.",
    },
    {
        "target": "fast_code_lookup_array",
        "status": "cooldown",
        "cooldown_runs_remaining": 1,
        "reason": "neutral in prior run",
        "notes": "Keep it cooled until the profile changes materially.",
    },
    {
        "target": "comparator_after_compute",
        "status": "blocked",
        "cooldown_runs_remaining": 99,
        "reason": "current factor gate does not cover the active benchmark",
        "notes": "The active benchmark does not exercise this comparator.",
    },
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows: list[dict[str, str]] = []
        for row in reader:
            cleaned = {
                (key or "").strip().strip('"'): (value or "").strip().strip('"')
                for key, value in row.items()
                if key is not None
            }
            if any(cleaned.values()):
                rows.append(cleaned)
        return rows


def ensure_float(raw: str | None, default: float = 0.0) -> float:
    if raw is None or raw == "":
        return default
    return float(raw)


def ensure_int(raw: str | None, default: int = 0) -> int:
    if raw is None or raw == "":
        return default
    return int(float(raw))


def parse_samples(raw: str) -> list[float]:
    samples = [float(part.strip()) for part in raw.split(",") if part.strip()]
    if not samples:
        raise ValueError("at least one sample is required")
    return samples


def sample_stats(
    samples: list[float],
    range_threshold: float,
    stdev_threshold: float,
    sample_unit: str = "seconds",
) -> dict[str, str]:
    if not samples:
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
            "noise_flag": "unknown",
        }

    samples_seconds = samples if sample_unit == "seconds" else [sample / 1000.0 for sample in samples]
    samples_ms = [sample * 1000.0 for sample in samples_seconds]
    stats_ms = sample_statistics_from_ms(samples_ms)
    stdev_seconds = float(stats_ms["stdev_seconds"])
    range_seconds = float(stats_ms["range_seconds"])
    noisy = range_seconds >= range_threshold or stdev_seconds >= stdev_threshold

    return {
        "sample_count": stats_ms["sample_count"],
        "samples_ms": stats_ms["samples_ms"],
        "samples": stats_ms["samples"],
        "mean_ms": stats_ms["mean_ms"],
        "mean_seconds": stats_ms["mean_seconds"],
        "median_ms": stats_ms["median_ms"],
        "median_seconds": stats_ms["median_seconds"],
        "mad_ms": stats_ms["mad_ms"],
        "mad_seconds": stats_ms["mad_seconds"],
        "iqr_ms": stats_ms["iqr_ms"],
        "iqr_seconds": stats_ms["iqr_seconds"],
        "stdev_ms": stats_ms["stdev_ms"],
        "stdev_seconds": stats_ms["stdev_seconds"],
        "range_ms": stats_ms["range_ms"],
        "range_seconds": stats_ms["range_seconds"],
        "noise_flag": "noisy" if noisy else "ok",
    }


def profile_source_label(path: Path) -> str:
    try:
        return str(path.relative_to(repo_root()))
    except ValueError:
        return path.name


def normalize_stage(stage: str) -> str:
    cleaned = stage.strip()
    if cleaned in STAGE_NORMALIZATION:
        return STAGE_NORMALIZATION[cleaned]
    if cleaned.startswith("compute_") and len(cleaned) > len("compute_"):
        return f"compute.{cleaned.removeprefix('compute_')}"
    return cleaned


def score_evidence(
    observed_cost_ms: int,
    p_owned: float,
    p_safe: float,
    p_gate: float,
    p_local: float,
    cost_attempt_seconds: float,
    uncertainty: float,
    lambda_weight: float = DEFAULT_EVIDENCE_LAMBDA,
) -> dict[str, float]:
    expected_delta_seconds = max(0.0, observed_cost_ms / 1000.0 * DEFAULT_EXPECTED_CAPTURE_RATE)
    cost_seconds = max(cost_attempt_seconds, 1.0)
    score = (
        expected_delta_seconds
        * p_owned
        * p_safe
        * p_gate
        * p_local
        / cost_seconds
    ) + lambda_weight * uncertainty
    return {
        "expected_delta_seconds": expected_delta_seconds,
        "p_owned": p_owned,
        "p_safe": p_safe,
        "p_gate": p_gate,
        "p_local": p_local,
        "cost_attempt_seconds": cost_seconds,
        "uncertainty": uncertainty,
        "lambda": lambda_weight,
        "score_evidence": score,
    }


def legacy_product_score(observed_cost_ms: int, p_owned: float, p_safe: float, p_local: float) -> float:
    return observed_cost_ms * p_owned * p_safe * p_local


def read_profile_rows(input_path: Path) -> list[dict[str, str]]:
    rows = load_tsv(input_path)
    aggregates: dict[str, dict[str, object]] = {}
    source_label = profile_source_label(input_path)

    for row in rows:
        stage = normalize_stage(row.get("stage", ""))
        if not stage:
            continue
        total_ms = ensure_int(row.get("total_ms"))
        count = ensure_int(row.get("count"))
        avg_ms = ensure_float(row.get("avg_ms"), total_ms / count if count else 0.0)
        source = row.get("source", source_label) or source_label

        aggregate = aggregates.setdefault(
            stage,
            {
                "stage": stage,
                "total_ms": 0,
                "count": 0,
                "weighted_avg_ms": 0.0,
                "sources": [],
            },
        )
        aggregate["total_ms"] = int(aggregate["total_ms"]) + total_ms
        aggregate["count"] = int(aggregate["count"]) + count
        aggregate["weighted_avg_ms"] = float(aggregate["weighted_avg_ms"]) + avg_ms * count
        sources = aggregate["sources"]
        if isinstance(sources, list) and source not in sources:
            sources.append(source)

    normalized: list[dict[str, str]] = []
    for aggregate in aggregates.values():
        count = int(aggregate["count"])
        total_ms = int(aggregate["total_ms"])
        avg_ms = float(aggregate["weighted_avg_ms"]) / count if count else 0.0
        sources = aggregate["sources"]
        source = ";".join(str(item) for item in sources) if isinstance(sources, list) else source_label
        normalized.append(
            {
                "stage": str(aggregate["stage"]),
                "total_ms": str(total_ms),
                "count": str(count),
                "avg_ms": f"{avg_ms:.3f}",
                "source": source,
            }
        )

    if not normalized:
        raise ValueError(f"no stage rows found in {input_path}")

    normalized.sort(key=lambda row: (-int(row["total_ms"]), row["stage"]))
    return normalized


def write_profile(profile_path: Path, rows: list[dict[str, str]]) -> None:
    with profile_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("stage", "total_ms", "count", "avg_ms", "source"),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_hotspots(hotspots_path: Path, rows: list[dict[str, str]]) -> None:
    top_total = int(rows[0]["total_ms"]) if rows else 0
    with hotspots_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("rank", "stage", "total_ms", "avg_ms", "count", "score", "notes"),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            total_ms = int(row["total_ms"])
            score = total_ms / top_total if top_total else 0.0
            writer.writerow(
                {
                    "rank": rank,
                    "stage": row["stage"],
                    "total_ms": row["total_ms"],
                    "avg_ms": row["avg_ms"],
                    "count": row["count"],
                    "score": f"{score:.6f}",
                    "notes": "relative_to_top_total",
                }
            )


def target_rows(profile_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    profile_by_stage = {normalize_stage(row["stage"]): {**row, "stage": normalize_stage(row["stage"])} for row in profile_rows}
    selected: list[dict[str, str]] = []

    for spec in TARGET_SPECS:
        matched_stage = next((normalize_stage(alias) for alias in spec.stage_aliases if normalize_stage(alias) in profile_by_stage), None)
        if matched_stage is None:
            continue
        row = profile_by_stage[matched_stage]
        c = int(row["total_ms"])
        o = spec.ownership_confidence
        r = spec.correctness_safety
        locality = spec.locality
        scoring = score_evidence(
            observed_cost_ms=c,
            p_owned=o,
            p_safe=r,
            p_gate=spec.gate_confidence,
            p_local=locality,
            cost_attempt_seconds=spec.cost_attempt_seconds,
            uncertainty=spec.uncertainty,
        )
        legacy_score = legacy_product_score(c, o, r, locality)
        selected.append(
            {
                "target": spec.target,
                "stage": matched_stage,
                "observed_cost_ms": str(c),
                "expected_delta_seconds": f"{scoring['expected_delta_seconds']:.3f}",
                "p_owned": f"{scoring['p_owned']:.3f}",
                "p_safe": f"{scoring['p_safe']:.3f}",
                "p_gate": f"{scoring['p_gate']:.3f}",
                "p_local": f"{scoring['p_local']:.3f}",
                "cost_attempt_seconds": f"{scoring['cost_attempt_seconds']:.1f}",
                "uncertainty": f"{scoring['uncertainty']:.3f}",
                "lambda": f"{scoring['lambda']:.3f}",
                "score_evidence": f"{scoring['score_evidence']:.6f}",
                "ownership_confidence": f"{o:.3f}",
                "correctness_safety": f"{r:.3f}",
                "locality": f"{locality:.3f}",
                "legacy_corl_score": f"{legacy_score:.3f}",
                "score": f"{scoring['score_evidence']:.6f}",
                "policy_bucket": spec.bucket,
                "notes": spec.notes,
            }
        )

    for stage, row in profile_by_stage.items():
        if not stage.startswith("compute.") or stage == "compute.factor_on_tick":
            continue
        c = int(row["total_ms"])
        o = 0.65
        r = 0.80
        locality = 0.70
        scoring = score_evidence(
            observed_cost_ms=c,
            p_owned=o,
            p_safe=r,
            p_gate=0.85,
            p_local=locality,
            cost_attempt_seconds=DEFAULT_ATTEMPT_COST_SECONDS,
            uncertainty=0.25,
        )
        legacy_score = legacy_product_score(c, o, r, locality)
        selected.append(
            {
                "target": stage,
                "stage": stage,
                "observed_cost_ms": str(c),
                "expected_delta_seconds": f"{scoring['expected_delta_seconds']:.3f}",
                "p_owned": f"{scoring['p_owned']:.3f}",
                "p_safe": f"{scoring['p_safe']:.3f}",
                "p_gate": f"{scoring['p_gate']:.3f}",
                "p_local": f"{scoring['p_local']:.3f}",
                "cost_attempt_seconds": f"{scoring['cost_attempt_seconds']:.1f}",
                "uncertainty": f"{scoring['uncertainty']:.3f}",
                "lambda": f"{scoring['lambda']:.3f}",
                "score_evidence": f"{scoring['score_evidence']:.6f}",
                "ownership_confidence": f"{o:.3f}",
                "correctness_safety": f"{r:.3f}",
                "locality": f"{locality:.3f}",
                "legacy_corl_score": f"{legacy_score:.3f}",
                "score": f"{scoring['score_evidence']:.6f}",
                "policy_bucket": "reserve",
                "notes": "Per-factor compute row derived from compute_<id> profiling.",
            }
        )

    selected.sort(key=lambda row: (-float(row["score_evidence"]), row["target"]))
    return selected


def profile_to_candidates(profile_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return target_rows(profile_rows)


def build_explore_stack(targets: list[dict[str, str]]) -> dict[str, str] | None:
    safe_targets = [
        row
        for row in targets
        if float(row["correctness_safety"]) >= 0.95 and float(row["locality"]) >= 0.95
    ]
    if len(safe_targets) < 2:
        return None

    first, second = safe_targets[-2:]
    observed_cost_ms = int(first["observed_cost_ms"]) + int(second["observed_cost_ms"])
    stacked_score = (float(first["score"]) + float(second["score"])) / 2.0
    return {
        "target": f"{first['target']} + {second['target']}",
        "stage": f"{first['stage']}|{second['stage']}",
        "stack_members": f"{first['target']}|{second['target']}",
        "observed_cost_ms": str(observed_cost_ms),
        "expected_delta_seconds": f"{float(first['expected_delta_seconds']) + float(second['expected_delta_seconds']):.3f}",
        "p_owned": f"{min(float(first['p_owned']), float(second['p_owned'])):.3f}",
        "p_safe": f"{min(float(first['p_safe']), float(second['p_safe'])):.3f}",
        "p_gate": f"{min(float(first['p_gate']), float(second['p_gate'])):.3f}",
        "p_local": f"{min(float(first['p_local']), float(second['p_local'])):.3f}",
        "cost_attempt_seconds": f"{float(first['cost_attempt_seconds']) + float(second['cost_attempt_seconds']):.1f}",
        "uncertainty": f"{max(float(first['uncertainty']), float(second['uncertainty'])):.3f}",
        "lambda": f"{max(float(first['lambda']), float(second['lambda'])):.3f}",
        "ownership_confidence": f"{min(float(first['ownership_confidence']), float(second['ownership_confidence'])):.3f}",
        "correctness_safety": f"{min(float(first['correctness_safety']), float(second['correctness_safety'])):.3f}",
        "locality": f"{min(float(first['locality']), float(second['locality'])):.3f}",
        "score_evidence": f"{stacked_score:.6f}",
        "legacy_corl_score": "",
        "score": f"{stacked_score:.6f}",
        "policy_bucket": "explore",
        "notes": "Neutral stack exploration quota: combine two low-risk candidates instead of greedily chasing only the top hotspot.",
    }


def build_attempts(
    profile_rows: list[dict[str, str]],
    control_head: str,
    control_median: float,
    control_samples: list[float],
    control_sample_unit: str,
    acceptance_policy: str,
    recorded_at: str,
    range_threshold: float,
    stdev_threshold: float,
    paired_evidence_by_target: dict[str, PairedTimingEvidence] | None = None,
) -> list[dict[str, str]]:
    candidates = profile_to_candidates(profile_rows)
    exploit_rows = candidates[:2]
    reserve_rows = candidates[2:]
    stack_row = build_explore_stack(candidates)

    rows: list[dict[str, str]] = []
    control_stats = sample_stats(control_samples, range_threshold, stdev_threshold, control_sample_unit)
    rows.append(
        {
            "rank": "0",
            "kind": "control",
            "policy_bucket": "control",
            "experiment_kind": "control_bundle",
            "target": f"{control_head} control baseline",
            "stack_members": "",
            "stage": "baseline",
            "observed_cost_ms": str(int(control_median * 1000)),
            "expected_delta_seconds": "0.000",
            "p_owned": "1.000",
            "p_safe": "1.000",
            "p_gate": "1.000",
            "p_local": "1.000",
            "cost_attempt_seconds": "0.0",
            "uncertainty": "0.000",
            "lambda": f"{DEFAULT_EVIDENCE_LAMBDA:.3f}",
            "score_evidence": "0.000000",
            "ownership_confidence": "1.000",
            "correctness_safety": "1.000",
            "locality": "1.000",
            "legacy_corl_score": "",
            "score": "0.000000",
            "recorded_at": recorded_at,
            "sample_count": control_stats["sample_count"],
            "samples_ms": control_stats["samples_ms"],
            "samples": control_stats["samples"],
            "mean_ms": control_stats["mean_ms"],
            "mean_seconds": control_stats["mean_seconds"],
            "median_ms": control_stats["median_ms"],
            "median_seconds": control_stats["median_seconds"],
            "mad_ms": control_stats["mad_ms"],
            "mad_seconds": control_stats["mad_seconds"],
            "iqr_ms": control_stats["iqr_ms"],
            "iqr_seconds": control_stats["iqr_seconds"],
            "stdev_ms": control_stats["stdev_ms"],
            "stdev_seconds": control_stats["stdev_seconds"],
            "range_ms": control_stats["range_ms"],
            "range_seconds": control_stats["range_seconds"],
            "noise_flag": control_stats["noise_flag"],
            "verdict": "DIAGNOSTIC_ONLY",
            "control_head": control_head,
            "control_median_ms": f"{control_median * 1000.0:.3f}",
            "control_median_seconds": f"{control_median:.3f}",
            "delta_ms": "0.000",
            "delta_seconds": "0.000",
            "acceptance_policy": acceptance_policy,
            "notes": "Control baseline for the neutral stack plan.",
        }
    )

    rank = 1
    for row in exploit_rows:
        rows.append(
            {
                "rank": str(rank),
                "kind": "single",
                "policy_bucket": "exploit",
                "experiment_kind": "single",
                "target": row["target"],
                "stack_members": row["target"],
                "stage": row["stage"],
                "observed_cost_ms": row["observed_cost_ms"],
                "expected_delta_seconds": row["expected_delta_seconds"],
                "p_owned": row["p_owned"],
                "p_safe": row["p_safe"],
                "p_gate": row["p_gate"],
                "p_local": row["p_local"],
                "cost_attempt_seconds": row["cost_attempt_seconds"],
                "uncertainty": row["uncertainty"],
                "lambda": row["lambda"],
                "score_evidence": row["score_evidence"],
                "ownership_confidence": row["ownership_confidence"],
                "correctness_safety": row["correctness_safety"],
                "locality": row["locality"],
                "legacy_corl_score": row.get("legacy_corl_score", ""),
                "score": row["score"],
                "recorded_at": recorded_at,
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
                "noise_flag": "planned",
                "verdict": "DIAGNOSTIC_ONLY",
                "control_head": control_head,
                "control_median_ms": f"{control_median * 1000.0:.3f}",
                "control_median_seconds": f"{control_median:.3f}",
                "delta_ms": "",
                "delta_seconds": "",
                "acceptance_policy": acceptance_policy,
                "notes": row["notes"],
            }
        )
        rank += 1

    if stack_row is not None:
        rows.append(
            {
                "rank": str(rank),
                "kind": "neutral_stack",
                "policy_bucket": "explore",
                "experiment_kind": "neutral_stack",
                "target": stack_row["target"],
                "stack_members": stack_row["stack_members"],
                "stage": stack_row["stage"],
                "observed_cost_ms": stack_row["observed_cost_ms"],
                "expected_delta_seconds": stack_row["expected_delta_seconds"],
                "p_owned": stack_row["p_owned"],
                "p_safe": stack_row["p_safe"],
                "p_gate": stack_row["p_gate"],
                "p_local": stack_row["p_local"],
                "cost_attempt_seconds": stack_row["cost_attempt_seconds"],
                "uncertainty": stack_row["uncertainty"],
                "lambda": stack_row["lambda"],
                "score_evidence": stack_row["score_evidence"],
                "ownership_confidence": stack_row["ownership_confidence"],
                "correctness_safety": stack_row["correctness_safety"],
                "locality": stack_row["locality"],
                "legacy_corl_score": stack_row.get("legacy_corl_score", ""),
                "score": stack_row["score"],
                "recorded_at": recorded_at,
                "sample_count": "",
                "samples_ms": "",
                "samples": "",
                "mean_ms": "",
                "mean_seconds": "",
                "median_ms": "",
                "median_seconds": "",
                "stdev_ms": "",
                "stdev_seconds": "",
                "range_ms": "",
                "range_seconds": "",
                "noise_flag": "planned",
                "verdict": "DIAGNOSTIC_ONLY",
                "control_head": control_head,
                "control_median_ms": f"{control_median * 1000.0:.3f}",
                "control_median_seconds": f"{control_median:.3f}",
                "delta_ms": "",
                "delta_seconds": "",
                "acceptance_policy": acceptance_policy,
                "notes": stack_row["notes"],
            }
        )
        rank += 1

    for row in reserve_rows:
        rows.append(
            {
                "rank": str(rank),
                "kind": "single",
                "policy_bucket": "reserve",
                "experiment_kind": "single",
                "target": row["target"],
                "stack_members": row["target"],
                "stage": row["stage"],
                "observed_cost_ms": row["observed_cost_ms"],
                "expected_delta_seconds": row["expected_delta_seconds"],
                "p_owned": row["p_owned"],
                "p_safe": row["p_safe"],
                "p_gate": row["p_gate"],
                "p_local": row["p_local"],
                "cost_attempt_seconds": row["cost_attempt_seconds"],
                "uncertainty": row["uncertainty"],
                "lambda": row["lambda"],
                "score_evidence": row["score_evidence"],
                "ownership_confidence": row["ownership_confidence"],
                "correctness_safety": row["correctness_safety"],
                "locality": row["locality"],
                "legacy_corl_score": row.get("legacy_corl_score", ""),
                "score": row["score"],
                "recorded_at": recorded_at,
                "sample_count": "",
                "samples_ms": "",
                "samples": "",
                "mean_ms": "",
                "mean_seconds": "",
                "median_ms": "",
                "median_seconds": "",
                "stdev_ms": "",
                "stdev_seconds": "",
                "range_ms": "",
                "range_seconds": "",
                "noise_flag": "planned",
                "verdict": "DIAGNOSTIC_ONLY",
                "control_head": control_head,
                "control_median_ms": f"{control_median * 1000.0:.3f}",
                "control_median_seconds": f"{control_median:.3f}",
                "delta_ms": "",
                "delta_seconds": "",
                "acceptance_policy": acceptance_policy,
                "notes": row["notes"],
            }
        )
        rank += 1

    if paired_evidence_by_target:
        for row in rows:
            if row.get("kind") == "control":
                continue
            target = row.get("target")
            if not target or target not in paired_evidence_by_target:
                continue
            evidence = paired_evidence_by_target[target]
            fields = evidence_fields(evidence)
            for key, value in fields.items():
                if value in (None, ""):
                    continue
                existing = row.get(key, "")
                if existing in (None, "", "planned", "DIAGNOSTIC_ONLY"):
                    row[key] = value
                elif key in {
                    "timing_verdict",
                    "timing_verdict_reason",
                    "timing_verdict_method",
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
                    "control_sample_count",
                    "candidate_sample_count",
                    "paired_sample_count",
                }:
                    row[key] = value
            row["verdict"] = evidence.verdict
            row["noise_flag"] = evidence.noise_flag

    return rows


def write_attempts(attempts_path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ATTEMPTS_FIELDNAMES
    with attempts_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_cooldown(cooldown_path: Path, source_profile: str) -> None:
    fieldnames = [
        "target",
        "status",
        "cooldown_runs_remaining",
        "reason",
        "source_profile",
        "notes",
    ]
    with cooldown_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in COOLDOWN_SPECS:
            writer.writerow(
                {
                    "target": row["target"],
                    "status": row["status"],
                    "cooldown_runs_remaining": row["cooldown_runs_remaining"],
                    "reason": row["reason"],
                    "source_profile": source_profile,
                    "notes": row["notes"],
                }
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare control-loop artifacts from an existing profile snapshot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_profile",
        type=Path,
        help="Path to profile.tsv or bundle_profile_from_logs.tsv produced from a previous run.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Control-loop output directory for this run, for example experiments/<run-id>/control_loop.",
    )
    parser.add_argument(
        "--control-head",
        required=True,
        help="Safe control head label recorded in attempts.tsv for this run.",
    )
    parser.add_argument(
        "--control-median",
        type=float,
        required=True,
        help="Control median in seconds recorded in attempts.tsv for this run.",
    )
    parser.add_argument(
        "--control-samples",
        default="",
        help="Comma-separated sample list for the control row, for example <s1>,<s2>,<s3>.",
    )
    parser.add_argument(
        "--control-sample-unit",
        choices=("seconds", "ms"),
        default="seconds",
        help="Unit for --control-samples. Use ms when replaying timing_samples.tsv values.",
    )
    parser.add_argument(
        "--host-key",
        default="",
        help="Host key recorded in timing_history.tsv. Defaults to the local host name if omitted.",
    )
    parser.add_argument(
        "--active-gate",
        default="unknown",
        help="Active gate or compatibility group recorded in timing_history.tsv.",
    )
    parser.add_argument(
        "--warm-or-cold",
        default="measured",
        choices=("warm", "cold", "measured"),
        help="Warm/cold bucket for the timing-history row.",
    )
    parser.add_argument(
        "--acceptance-policy",
        default=DEFAULT_ACCEPTANCE_POLICY,
        help="Acceptance policy text stored in attempts.tsv.",
    )
    parser.add_argument(
        "--noise-range-threshold",
        type=float,
        default=DEFAULT_NOISE_RANGE_THRESHOLD,
        help="Mark a recorded run noisy when the sample range reaches this threshold.",
    )
    parser.add_argument(
        "--noise-stdev-threshold",
        type=float,
        default=DEFAULT_NOISE_STDEV_THRESHOLD,
        help="Mark a recorded run noisy when the sample stddev reaches this threshold.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_profile = args.input_profile.resolve()
    if not input_profile.exists():
        parser.error(f"input profile does not exist: {input_profile}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    profile_rows = read_profile_rows(input_profile)
    profile_out = output_dir / "profile.tsv"
    hotspots_out = output_dir / "hotspots.tsv"
    attempts_out = output_dir / "attempts.tsv"
    cooldown_out = output_dir / "cooldown.tsv"

    write_profile(profile_out, profile_rows)
    write_hotspots(hotspots_out, profile_rows)

    control_samples = parse_samples(args.control_samples) if args.control_samples else []
    recorded_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    attempts_rows = build_attempts(
        profile_rows,
        control_head=args.control_head,
        control_median=args.control_median,
        control_samples=control_samples,
        control_sample_unit=args.control_sample_unit,
        acceptance_policy=args.acceptance_policy,
        recorded_at=recorded_at,
        range_threshold=args.noise_range_threshold,
        stdev_threshold=args.noise_stdev_threshold,
    )
    write_attempts(attempts_out, attempts_rows)
    write_cooldown(cooldown_out, profile_source_label(input_profile))

    host_key = args.host_key or default_host_key()
    bundle_id = bundle_id_for_path(output_dir)
    history_sample_unit = "ms" if args.control_sample_unit == "ms" else "seconds_compat"
    history_rows = history_rows_from_attempt_rows(
        attempts_rows,
        bundle_id=bundle_id,
        run_id=bundle_id,
        host_key=host_key,
        control_head=args.control_head,
        active_gate=args.active_gate,
        warm_or_cold=args.warm_or_cold,
        sample_unit=history_sample_unit,
        source_attempts_path=str(attempts_out),
        recorded_at=recorded_at,
        default_noise_flag="ok",
    )
    shared_history_out = shared_history_path_for_output(output_dir)
    per_run_history_out = per_run_history_path(output_dir)
    write_history_artifacts(shared_history_out, per_run_history_out, history_rows)

    exploit_rows = [row for row in attempts_rows if row["policy_bucket"] == "exploit"]
    explore_rows = [row for row in attempts_rows if row["policy_bucket"] == "explore"]
    control_row = attempts_rows[0]

    print(f"profile={profile_out}")
    print(f"hotspots={hotspots_out}")
    print(f"attempts={attempts_out}")
    print(f"cooldown={cooldown_out}")
    print(f"control_head={args.control_head}")
    print(f"control_median={args.control_median}")
    print(f"control_noise={control_row['noise_flag']}")
    print(f"host_key={host_key}")
    print(f"active_gate={args.active_gate}")
    print(f"timing_history={shared_history_out}")
    print(f"timing_history_run_copy={per_run_history_out}")
    print(f"exploit_targets={'; '.join(row['target'] for row in exploit_rows)}")
    print(f"explore_targets={'; '.join(row['target'] for row in explore_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
