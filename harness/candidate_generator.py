#!/usr/bin/env python3
"""Three-lane candidate generator for the headless auto-loop.

The generator consumes the durable evidence surface the harness already writes
(``profile.tsv``, ``hotspots.tsv``, ``attempts.tsv``, ``cooldown.tsv``,
``neutral_pool.tsv`` and ``timing_history.tsv``) and emits a ranked list of
candidates split into three lanes:

- ``evidence``   - top-K profile-driven hotspots that already have evidence.
- ``insight``    - small, narrow Class A / cache-locality candidates that are
                   not necessarily the top hotspot.
- ``combination`` - compatible ``neutral_pool`` stacks whose combined
                   semantic risk stays below ``high``. Overlap is not
                   hard-rejected here; the agent may resolve it in the final
                   composite patch.

Each candidate dict carries the contract the task spec demands:
``candidate_id``, ``lane``, ``hypothesis``, ``target``, ``expected_effect``,
``semantic_risk``, ``touched_files`` (predicted) and ``source_evidence`` (a
structured pointer back to the originating profile row, hotspot rank or
neutral-pool member).
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SEMANTIC_RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "unknown": 2}
PROJECTION_PRUNE_CLASS = "projection_prune_with_manual_column_remap"


def _rank_risk(value: str) -> int:
    return SEMANTIC_RISK_ORDER.get((value or "").strip().lower(), 2)


def _stack_risk(members: Iterable[str]) -> str:
    # Combined stack risk is bounded below by the highest member risk.
    worst = 0
    for member in members:
        worst = max(worst, _rank_risk(member))
    for label, rank in SEMANTIC_RISK_ORDER.items():
        if rank == worst and label != "unknown":
            return label
    return "medium"


@dataclass
class Candidate:
    candidate_id: str
    lane: str
    hypothesis: str
    target: str
    expected_effect: str
    semantic_risk: str
    touched_files: list[str] = field(default_factory=list)
    source_evidence: dict[str, Any] = field(default_factory=dict)
    stack_members: list[str] = field(default_factory=list)
    stack_compatibility: str = "single"
    rank_score: float = 0.0
    measure_runs_override: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "lane": self.lane,
            "hypothesis": self.hypothesis,
            "target": self.target,
            "expected_effect": self.expected_effect,
            "semantic_risk": self.semantic_risk,
            "touched_files": list(self.touched_files),
            "source_evidence": dict(self.source_evidence),
            "stack_members": list(self.stack_members),
            "stack_compatibility": self.stack_compatibility,
            "rank_score": self.rank_score,
            "generator_model": os.environ.get("GENERATOR_MODEL", ""),
            "generator_session": os.environ.get("GENERATOR_SESSION", ""),
        }
        if self.measure_runs_override is not None:
            payload["measure_runs_override"] = self.measure_runs_override
        return payload


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _retry_file_payload(ledger: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    retry_file = str(row.get("retry_file") or "").strip()
    if not retry_file:
        return {}
    ledger_dir_raw = str(ledger.get("_ledger_dir") or "").strip()
    retry_path = Path(retry_file)
    if not retry_path.is_absolute() and ledger_dir_raw:
        retry_path = Path(ledger_dir_raw) / retry_path
    return read_json(retry_path)


def _ledger_blocked_ids(ledger: dict[str, Any]) -> set[str]:
    blocked: set[str] = set()
    for section in ("non_retry_candidates", "not_run_candidates"):
        for row in ledger.get(section, []) or []:
            candidate_id = str(row.get("candidate_id") or "").strip()
            if candidate_id:
                blocked.add(candidate_id)
    for row in ledger.get("blocked_candidate_classes", []) or []:
        for example in row.get("examples", []) or []:
            example_id = str(example or "").strip()
            if example_id:
                blocked.add(example_id)
    return blocked


def _ledger_retry_only_identifiers(ledger: dict[str, Any]) -> set[str]:
    retry_only: set[str] = set()
    for row in ledger.get("quiet_window_retry_queue", []) or []:
        for payload in (row, _retry_file_payload(ledger, row)):
            for key in ("candidate_id", "target", "source_candidate_id"):
                value = str(payload.get(key) or "").strip()
                if value:
                    retry_only.add(value)
    return retry_only


def _ledger_blocked_classes(ledger: dict[str, Any]) -> set[str]:
    return {
        str(row.get("class") or "").strip()
        for row in ledger.get("blocked_candidate_classes", []) or []
        if str(row.get("class") or "").strip()
    }


def _candidate_text(candidate: Candidate | dict[str, Any]) -> str:
    if isinstance(candidate, Candidate):
        payload = candidate.to_dict()
    else:
        payload = candidate
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()


def _is_projection_prune_manual_remap(candidate: Candidate | dict[str, Any]) -> bool:
    text = _candidate_text(candidate)
    return (
        ("projection" in text or "_columns" in text)
        and ("prune" in text or "remove column" in text or "drop column" in text)
        and ("remap" in text or "table->column" in text or "column index" in text)
    )


def _candidate_allowed_by_ledger(candidate: Candidate, ledger: dict[str, Any]) -> bool:
    if not ledger:
        return True
    if (candidate.source_evidence or {}).get("kind") == "quiet_retry":
        return True

    blocked_ids = _ledger_blocked_ids(ledger)
    retry_only_ids = _ledger_retry_only_identifiers(ledger)
    identifiers = {
        candidate.candidate_id,
        candidate.target,
        *candidate.stack_members,
    }
    source = candidate.source_evidence or {}
    for key in ("candidate_id", "target", "source_candidate_id"):
        value = str(source.get(key) or "").strip()
        if value:
            identifiers.add(value)
    if any(identifier in blocked_ids for identifier in identifiers if identifier):
        return False
    if any(identifier in retry_only_ids for identifier in identifiers if identifier):
        return False

    blocked_classes = _ledger_blocked_classes(ledger)
    if PROJECTION_PRUNE_CLASS in blocked_classes and _is_projection_prune_manual_remap(candidate):
        return False

    return True


def _apply_candidate_ledger(
    lanes: dict[str, list[Candidate]],
    ledger: dict[str, Any],
) -> dict[str, list[Candidate]]:
    if not ledger:
        return lanes
    return {
        lane: [candidate for candidate in candidates if _candidate_allowed_by_ledger(candidate, ledger)]
        for lane, candidates in lanes.items()
    }


def _safe_token(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (text or "").strip())
    return cleaned.strip("_") or "candidate"


def _parse_float(raw: str | None, default: float = 0.0) -> float:
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def _summary_float(summary: str | None, key: str, default: float = 0.0) -> float:
    if not summary:
        return default
    prefix = f"{key}="
    for part in str(summary).replace(",", ";").split(";"):
        part = part.strip()
        if not part.startswith(prefix):
            continue
        return _parse_float(part[len(prefix) :], default)
    return default


def _split_touched(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in str(raw).split("|") if part.strip()]


def _is_blocked(target: str, cooldown_rows: list[dict[str, str]]) -> bool:
    target_clean = (target or "").strip()
    for row in cooldown_rows:
        if (row.get("target") or "").strip() != target_clean:
            continue
        status = (row.get("status") or "").strip().lower()
        if status in {"blocked"}:
            return True
        if status == "cooldown":
            remaining = _parse_float(row.get("cooldown_runs_remaining"), 0.0)
            if remaining > 0:
                return True
    return False


def _recent_attempt_counts(
    attempts_rows: list[dict[str, str]],
    limit_window: int = 12,
) -> dict[str, dict[str, int]]:
    """Count recent verdicts per target across the last ``limit_window`` rows."""

    tail = attempts_rows[-limit_window:] if attempts_rows else []
    buckets: dict[str, dict[str, int]] = {}
    for row in tail:
        target = (row.get("target") or "").strip()
        if not target:
            continue
        verdict = (row.get("verdict") or row.get("timing_verdict") or "").strip()
        counts = buckets.setdefault(target, {})
        counts[verdict] = counts.get(verdict, 0) + 1
    return buckets


def _evidence_lane(
    hotspot_rows: list[dict[str, str]],
    profile_rows: list[dict[str, str]],
    cooldown_rows: list[dict[str, str]],
    recent_counts: dict[str, dict[str, int]],
    *,
    top_k: int,
) -> list[Candidate]:
    profile_by_stage = {
        (row.get("stage") or "").strip(): row for row in profile_rows if (row.get("stage") or "").strip()
    }
    candidates: list[Candidate] = []
    for row in hotspot_rows:
        stage = (row.get("stage") or "").strip()
        if not stage:
            continue
        if _is_blocked(stage, cooldown_rows):
            continue
        if recent_counts.get(stage, {}).get("rejected", 0) >= 2:
            continue
        total_ms = _parse_float(row.get("total_ms"))
        avg_ms = _parse_float(row.get("avg_ms"))
        score = _parse_float(row.get("score_evidence") or row.get("score"))
        expected_delta_s = _parse_float(
            row.get("expected_delta_seconds"),
            total_ms / 1000.0 * 0.03,
        )
        hypothesis = (
            f"profile top hotspot {stage}: total_ms={total_ms:.1f}, avg_ms={avg_ms:.1f};"
            " narrow the change to this stage before widening."
        )
        touched = _split_touched(row.get("touched_files") or stage)
        candidate = Candidate(
            candidate_id=f"evidence_{_safe_token(stage)}",
            lane="evidence",
            hypothesis=hypothesis,
            target=stage,
            expected_effect=f"reduce {stage} median by ~{expected_delta_s:.3f}s",
            semantic_risk=(row.get("notes") or "").split(";")[0].strip().lower() or "low",
            touched_files=touched or [stage],
            source_evidence={
                "kind": "hotspot",
                "rank": row.get("rank", ""),
                "stage": stage,
                "total_ms": total_ms,
                "avg_ms": avg_ms,
                "score_evidence": score,
                "notes": row.get("notes", ""),
                "touched_files": _split_touched(row.get("touched_files") or stage),
                "symbols": _split_touched(row.get("symbols") or ""),
                "profile_row": profile_by_stage.get(stage, {}),
            },
            rank_score=score or expected_delta_s,
        )
        # defensive normalization of risk text
        if candidate.semantic_risk not in SEMANTIC_RISK_ORDER:
            candidate.semantic_risk = "low"
        candidates.append(candidate)
        if len(candidates) >= top_k:
            break
    return candidates


def _latest_attempt_by_target(attempts_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for row in attempts_rows:
        target = (row.get("target") or "").strip()
        if target:
            latest[target] = row
    return latest


def _retry_lane(
    retry_rows: list[dict[str, str]],
    attempts_rows: list[dict[str, str]],
    cooldown_rows: list[dict[str, str]],
    *,
    retry_ready_targets: set[str],
    top_k: int,
) -> list[Candidate]:
    """Emit noisy candidates only after the controller's quiet-window gate passes."""

    if not retry_ready_targets:
        return []
    latest_attempts = _latest_attempt_by_target(attempts_rows)
    candidates: list[Candidate] = []
    for row in retry_rows:
        target = (row.get("target") or "").strip()
        if not target or target not in retry_ready_targets:
            continue
        if _is_blocked(target, cooldown_rows):
            continue
        status = (row.get("status") or "").strip()
        if status != "ESCALATE":
            continue
        next_measure_runs = int(_parse_float(row.get("next_measure_runs"), 0.0))
        attempt = latest_attempts.get(target, {})
        touched = _split_touched(attempt.get("touched_files") or target)
        candidate = Candidate(
            candidate_id=f"retry_{_safe_token(target)}",
            lane="evidence",
            hypothesis=(
                f"quiet-window retry for prior {status} target {target}; "
                "rerun only when host noise gates pass."
            ),
            target=target,
            expected_effect=attempt.get("notes") or "repeat under quiet-host gate",
            semantic_risk=attempt.get("semantic_risk") or "low",
            touched_files=touched or [target],
            source_evidence={
                "kind": "quiet_retry",
                "retry_condition": row,
                "latest_attempt": attempt,
            },
            rank_score=10_000.0 + len(candidates),
            measure_runs_override=next_measure_runs if next_measure_runs > 0 else None,
        )
        candidates.append(candidate)
        if len(candidates) >= top_k:
            break
    return candidates


def _insight_lane(
    profile_rows: list[dict[str, str]],
    cooldown_rows: list[dict[str, str]],
    recent_counts: dict[str, dict[str, int]],
    *,
    exclude_targets: set[str],
    top_k: int,
) -> list[Candidate]:
    """Small-window Class A / cache-locality candidates.

    The generator prefers rows with moderate cost but high ``count`` (many
    invocations), since those tend to benefit from cache-locality or inlining
    tweaks even if they are not the top hotspot.
    """

    ranked: list[tuple[float, dict[str, str]]] = []
    for row in profile_rows:
        stage = (row.get("stage") or "").strip()
        if not stage or stage in exclude_targets:
            continue
        if _is_blocked(stage, cooldown_rows):
            continue
        if recent_counts.get(stage, {}).get("rejected", 0) >= 2:
            continue
        total_ms = _parse_float(row.get("total_ms"))
        count = _parse_float(row.get("count"), 1.0)
        if count <= 0:
            continue
        # insight score favors repeated small work (high count, moderate total)
        score = (count ** 0.5) * (total_ms + 1.0) ** 0.25
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)

    candidates: list[Candidate] = []
    for score, row in ranked[:top_k]:
        stage = (row.get("stage") or "").strip()
        total_ms = _parse_float(row.get("total_ms"))
        count = int(_parse_float(row.get("count"), 0.0))
        avg_ms = _parse_float(row.get("avg_ms"))
        hypothesis = (
            f"insight lane (Class A / cache-locality) for {stage}: count={count}, "
            f"avg_ms={avg_ms:.3f}; try narrow locality or inlining tweak, not a rewrite."
        )
        touched = _split_touched(row.get("touched_files") or stage)
        candidate = Candidate(
            candidate_id=f"insight_{_safe_token(stage)}",
            lane="insight",
            hypothesis=hypothesis,
            target=stage,
            expected_effect=f"small-window locality improvement on {stage}",
            semantic_risk="low",
            touched_files=touched or [stage],
            source_evidence={
                "kind": "profile_row",
                "stage": stage,
                "total_ms": total_ms,
                "count": count,
                "avg_ms": avg_ms,
                "score": score,
                "source": row.get("source", ""),
                "notes": row.get("notes", ""),
                "touched_files": _split_touched(row.get("touched_files") or stage),
                "symbols": _split_touched(row.get("symbols") or ""),
            },
            rank_score=score,
        )
        candidates.append(candidate)
    return candidates


def _combination_lane(
    neutral_rows: list[dict[str, str]],
    cooldown_rows: list[dict[str, str]],
    *,
    max_combinations: int,
    max_members: int,
) -> list[Candidate]:
    """Build compatible neutral-pool stacks.

    A pair/triple is compatible only if:
    - none of the members are blocked in cooldown;
    - combined semantic risk stays strictly below ``high``.
    """

    if not neutral_rows:
        return []

    eligible = []
    for row in neutral_rows:
        target = (row.get("target") or row.get("candidate_id") or "").strip()
        if not target or _is_blocked(target, cooldown_rows):
            continue
        if (row.get("lane") or "").strip().lower() == "combination":
            continue
        if (row.get("candidate_id") or "").strip().startswith("stack_"):
            continue
        compat = (row.get("stack_compatibility") or "").strip().lower()
        if compat in {"single"}:
            continue
        row = dict(row)
        row.setdefault("target", target)
        eligible.append(row)

    if len(eligible) < 2:
        return []

    candidates: list[Candidate] = []
    # Simple greedy pairing: pair the highest-gain neutral with the next
    # compatible one, then remove the pair and repeat. Triples extend the pair
    # by trying one more compatible member.
    def gain(row: dict[str, str]) -> float:
        return (
            _parse_float(row.get("aggregate_gain_seconds"))
            or _parse_float(row.get("median_delta_ms")) / 1000.0
            or _summary_float(row.get("timing_summary"), "median_delta_ms") / 1000.0
            or _summary_float(row.get("timing_summary"), "delta_ms") / 1000.0
        )

    eligible.sort(key=gain, reverse=True)
    used_ids: set[str] = set()

    def row_id(row: dict[str, str]) -> str:
        return (row.get("candidate_id") or row.get("target") or "").strip()

    for i, row in enumerate(eligible):
        if row_id(row) in used_ids:
            continue
        members = [row]
        touched = set(_split_touched(row.get("touched_files") or row.get("target")))
        member_risks = [(row.get("semantic_risk") or "low").lower()]
        for j in range(i + 1, len(eligible)):
            peer = eligible[j]
            if row_id(peer) in used_ids:
                continue
            candidate_risks = member_risks + [(peer.get("semantic_risk") or "low").lower()]
            if _rank_risk(_stack_risk(candidate_risks)) >= SEMANTIC_RISK_ORDER["high"]:
                continue
            members.append(peer)
            touched.update(_split_touched(peer.get("touched_files") or peer.get("target")))
            member_risks = candidate_risks
            if len(members) >= max_members:
                break
        if len(members) < 2:
            continue
        for member in members:
            used_ids.add(row_id(member))
        stack_members = [row_id(m) for m in members]
        targets = "|".join((m.get("target") or "").strip() for m in members)
        expected = sum(gain(m) for m in members)
        candidate = Candidate(
            candidate_id=f"stack_{_safe_token(stack_members[0])}_{len(stack_members)}",
            lane="combination",
            hypothesis=(
                f"neutral stack of {len(members)} compatible members: {targets};"
                " combined effect audited before promotion."
            ),
            target=targets,
            expected_effect=f"aggregate gain ~{expected:.3f}s if all members hold",
            semantic_risk=_stack_risk(member_risks),
            touched_files=sorted(touched),
            source_evidence={
                "kind": "neutral_stack",
                "members": [
                    {
                        "candidate_id": row_id(m),
                        "target": m.get("target", ""),
                        "aggregate_gain_seconds": gain(m),
                        "semantic_risk": m.get("semantic_risk", ""),
                        "touched_files": _split_touched(m.get("touched_files") or m.get("target")),
                    }
                    for m in members
                ],
            },
            stack_members=stack_members,
            stack_compatibility="stackable",
            rank_score=expected,
        )
        candidates.append(candidate)
        if len(candidates) >= max_combinations:
            break
    return candidates


def generate_candidates(
    run_dir: Path,
    *,
    evidence_top_k: int = 3,
    insight_top_k: int = 2,
    combination_top_k: int = 2,
    combination_max_members: int = 3,
    retry_ready_targets: set[str] | None = None,
    retry_top_k: int = 1,
    candidate_ledger_path: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Produce the lane-split candidate queue for this run.

    Returns a dict keyed by lane name so the auto-loop can iterate in the
    configured priority order. Each candidate is already a plain dict so it
    can be written straight to JSON or a TSV row.
    """

    run_dir = Path(run_dir)
    profile_rows = read_tsv(run_dir / "profile.tsv")
    hotspot_rows = read_tsv(run_dir / "hotspots.tsv")
    attempts_rows = read_tsv(run_dir / "attempts.tsv")
    cooldown_rows = read_tsv(run_dir / "cooldown.tsv")
    neutral_rows = read_tsv(run_dir / "neutral_pool.tsv")
    retry_rows = read_tsv(run_dir / "retry_conditions.tsv")
    recent_counts = _recent_attempt_counts(attempts_rows)

    retry = _retry_lane(
        retry_rows,
        attempts_rows,
        cooldown_rows,
        retry_ready_targets=retry_ready_targets or set(),
        top_k=retry_top_k,
    )
    retry_targets = {candidate.target for candidate in retry}
    evidence_regular = _evidence_lane(
        hotspot_rows,
        profile_rows,
        cooldown_rows,
        recent_counts,
        top_k=evidence_top_k,
    )
    evidence = retry + [candidate for candidate in evidence_regular if candidate.target not in retry_targets]
    exclude_targets = {c.target for c in evidence}
    insight = _insight_lane(
        profile_rows,
        cooldown_rows,
        recent_counts,
        exclude_targets=exclude_targets,
        top_k=insight_top_k,
    )
    combination = _combination_lane(
        neutral_rows,
        cooldown_rows,
        max_combinations=combination_top_k,
        max_members=combination_max_members,
    )
    lanes = _apply_candidate_ledger(
        {
            "evidence": evidence,
            "insight": insight,
            "combination": combination,
        },
        {
            **read_json(candidate_ledger_path),
            "_ledger_dir": str(Path(candidate_ledger_path).resolve().parent),
        }
        if candidate_ledger_path
        else {},
    )
    return {
        "evidence": [c.to_dict() for c in lanes["evidence"]],
        "insight": [c.to_dict() for c in lanes["insight"]],
        "combination": [c.to_dict() for c in lanes["combination"]],
    }


def flatten_for_tsv(
    lanes: dict[str, list[dict[str, Any]]],
) -> list[dict[str, str]]:
    """Render the lane-split queue as a flat list suitable for ``patch_queue.tsv``."""

    out: list[dict[str, str]] = []
    rank = 0
    for lane in ("evidence", "insight", "combination"):
        for candidate in lanes.get(lane, []):
            rank += 1
            out.append(
                {
                    "rank": str(rank),
                    "candidate_id": candidate["candidate_id"],
                    "target": candidate["target"],
                    "patch_path": f"patches/{candidate['candidate_id']}.patch",
                    "policy_bucket": lane,
                    "experiment_kind": "neutral_stack" if lane == "combination" else "single",
                    "stack_members": "|".join(candidate.get("stack_members", [])),
                    "touched_files": "|".join(candidate.get("touched_files", [])),
                    "hypothesis": candidate.get("hypothesis", ""),
                    "compare_result": "pending",
                    "timing_summary": "planned",
                    "semantic_risk": candidate.get("semantic_risk", ""),
                    "stack_compatibility": candidate.get("stack_compatibility", "single"),
                    "queue_state": "candidate_planned",
                    "build_status": "pending",
                    "compare_status": "pending",
                    "timing_status": "pending",
                    "retry_condition": "",
                    "notes": candidate.get("expected_effect", ""),
                    "generator_model": candidate.get("generator_model", ""),
                    "generator_session": candidate.get("generator_session", ""),
                }
            )
    return out


__all__ = [
    "Candidate",
    "generate_candidates",
    "flatten_for_tsv",
]
