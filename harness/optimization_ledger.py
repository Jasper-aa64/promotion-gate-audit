#!/usr/bin/env python3
"""Append-only evidence ledger helpers for optimization harness runs."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

from attempts_schema import OPTIMIZATION_LEDGER_FIELDNAMES


LEDGER_FILENAME = "optimization_ledger.tsv"


def constants_hash(
    *,
    delta_min_ms_used: Any = "",
    decisive_k: Any = "",
    sign_min: Any = "",
    escalation_steps: Any = "",
) -> str:
    payload = {
        "delta_min_ms_used": _clean(delta_min_ms_used),
        "decisive_k": _clean(decisive_k),
        "sign_min": _clean(sign_min),
        "escalation_steps": _clean(escalation_steps),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def build_ledger_row(
    *,
    candidate: dict[str, Any],
    batch_state: dict[str, Any],
    verdict: str,
    artifact_path: str = "",
) -> dict[str, str]:
    naive_first, naive_accept = _naive_from_state(batch_state)
    delta_min = _first(batch_state, "delta_min_ms_used", "delta_min_ms")
    decisive_k = _first(batch_state, "decisive_k", "decisive_k_used")
    sign_min = _first(batch_state, "sign_min", "sign_min_used")
    escalation_steps = _clean(batch_state.get("escalation_steps", ""))
    row = {
        "candidate_id": _first(candidate, "candidate_id") or _first(batch_state, "candidate_id"),
        "lane": _first(candidate, "lane") or _first(batch_state, "lane"),
        "judge_kind": _judge_kind(batch_state),
        "verdict": _clean(verdict or _first(batch_state, "verdict", "timing_verdict")),
        "naive_k1_would_accept": _clean(batch_state.get("naive_k1_would_accept")) or ("true" if naive_accept else "false"),
        "naive_k1_first_delta_ms": _clean(batch_state.get("naive_k1_first_delta_ms")) or _format_optional_ms(naive_first),
        "delta_min_ms_used": _clean(delta_min),
        "decisive_k": _clean(decisive_k),
        "sign_min": _clean(sign_min),
        "escalation_steps": escalation_steps,
        "constants_hash": constants_hash(
            delta_min_ms_used=delta_min,
            decisive_k=decisive_k,
            sign_min=sign_min,
            escalation_steps=escalation_steps,
        ),
        "host_id": _first(batch_state, "host_id"),
        "env_class": _first(batch_state, "env_class"),
        "control_stdev_ms": _first(batch_state, "control_stdev_ms"),
        "control_range_ms": _first(batch_state, "control_range_ms"),
        "generator_model": _first(candidate, "generator_model") or _first(batch_state, "generator_model"),
        "generator_session": _first(candidate, "generator_session") or _first(batch_state, "generator_session"),
        "replicated": _replicated_text(batch_state),
        "artifact_path": _clean(artifact_path or _first(batch_state, "comparison_summary_path", "artifact_path")),
        "recorded_at": _first(batch_state, "recorded_at"),
    }
    return {field: row.get(field, "") for field in OPTIMIZATION_LEDGER_FIELDNAMES}


def append_ledger_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPTIMIZATION_LEDGER_FIELDNAMES, delimiter="\t", lineterminator="\n")
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in OPTIMIZATION_LEDGER_FIELDNAMES})


def read_ledger_rows_from_artifacts(roots: Iterable[Path]) -> Iterator[dict[str, str]]:
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("attempts.tsv")):
            for row in _read_tsv(path):
                if not _clean(row.get("candidate_id")):
                    continue
                if not _clean(row.get("verdict") or row.get("timing_verdict")):
                    continue
                yield _row_from_artifact(row, path)
        for path in sorted(root.rglob("timing_history.tsv")):
            for row in _read_tsv(path):
                if _clean(row.get("kind")) and _clean(row.get("kind")) != "candidate":
                    continue
                if not _clean(row.get("candidate_id") or row.get("target") or row.get("stage")):
                    continue
                if not _clean(row.get("verdict") or row.get("timing_verdict")):
                    continue
                yield _row_from_artifact(row, path)


def _row_from_artifact(row: dict[str, str], path: Path) -> dict[str, str]:
    candidate = {
        "candidate_id": row.get("candidate_id") or row.get("target") or row.get("stage") or "",
        "lane": row.get("lane") or row.get("policy_bucket", ""),
        "generator_model": row.get("generator_model", ""),
        "generator_session": row.get("generator_session", ""),
    }
    state = dict(row)
    return build_ledger_row(
        candidate=candidate,
        batch_state=state,
        verdict=row.get("verdict") or row.get("timing_verdict") or "",
        artifact_path=str(path),
    )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _first(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and _clean(value):
            return _clean(value)
    return ""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _format_optional_ms(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _parse_samples(text: Any) -> list[float]:
    if isinstance(text, list):
        out: list[float] = []
        for value in text:
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                continue
        return out
    out = []
    for part in _clean(text).split(","):
        if not part.strip():
            continue
        try:
            out.append(float(part))
        except ValueError:
            continue
    return out


def _naive_from_state(batch_state: dict[str, Any]) -> tuple[float | None, bool]:
    deltas = _parse_samples(batch_state.get("paired_deltas_ms"))
    if not deltas:
        return (None, False)
    first = deltas[0]
    return (first, first > 0)


def _judge_kind(batch_state: dict[str, Any]) -> str:
    explicit = _clean(batch_state.get("judge_kind"))
    if explicit:
        return explicit
    if batch_state.get("case_deltas") or batch_state.get("case_deltas"):
        return "threshold_consistency"
    method = _clean(batch_state.get("timing_verdict_method"))
    if method.startswith("paired_") or batch_state.get("confidence_tier"):
        return "confidence_tier"
    return ""


def _replicated_text(batch_state: dict[str, Any]) -> str:
    raw = _clean(batch_state.get("replicated"))
    if raw:
        return raw.lower()
    return "false"
