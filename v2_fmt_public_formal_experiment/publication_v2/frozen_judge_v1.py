#!/usr/bin/env python3
"""Pure-stdlib extraction of the frozen paired timing judge used by the study."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from typing import Any


BOOTSTRAP_RESAMPLES = 2000
PERMUTATION_RESAMPLES = 2000
CONFIDENCE = 0.95
REQUIRED_PAIRS = 5
BUNDLE_AUDIT_SAMPLE_FLOOR = 7
NOISE_RANGE_RATIO = 0.02
NOISE_STDEV_RATIO = 0.005
NOISE_CV_THRESHOLD = 0.02
DECISIVE_K = 1.0
SIGN_MIN = 0.9
SAMPLE_ESCALATION_STEPS = (4, 8, 12)
PROMOTION_VERDICTS = frozenset({"accepted", "accepted_noisy_replicated"})
SIGNAL_VERDICTS = frozenset(
    {"accepted", "accepted_noisy_single", "accepted_noisy_replicated"}
)
JUDGE_FIELDS = (
    "control_sample_count",
    "candidate_sample_count",
    "paired_sample_count",
    "control_median_ms",
    "control_samples_ms",
    "candidate_samples_ms",
    "paired_deltas_ms",
    "median_delta_ms",
    "bootstrap_ci_low_ms",
    "bootstrap_ci_high_ms",
    "permutation_p_value",
    "paired_stdev_ms",
    "paired_range_ms",
    "paired_mean_ms",
    "noise_flag",
    "confidence_tier",
    "confidence_margin_ms",
    "confidence_ci_width_ms",
    "confidence_decisiveness",
    "confidence_sign_consistency",
    "delta_min_ms_used",
    "decisive_k",
    "sign_min",
    "escalation_steps",
    "naive_k1_first_delta_ms",
    "naive_k1_would_accept",
    "mde_ms",
    "power_at_observed_effect",
    "raw_verdict",
    "verdict",
)

JUDGE_INTEGER_FIELDS = frozenset(
    {
        "candidate_sample_count",
        "control_sample_count",
        "paired_sample_count",
    }
)
JUDGE_NUMBER_FIELDS = frozenset(
    {
        "bootstrap_ci_low_ms",
        "bootstrap_ci_high_ms",
        "confidence_ci_width_ms",
        "confidence_decisiveness",
        "confidence_margin_ms",
        "confidence_sign_consistency",
        "control_median_ms",
        "decisive_k",
        "delta_min_ms_used",
        "mde_ms",
        "median_delta_ms",
        "naive_k1_first_delta_ms",
        "paired_mean_ms",
        "paired_range_ms",
        "paired_stdev_ms",
        "permutation_p_value",
        "power_at_observed_effect",
        "sign_min",
    }
)
JUDGE_BOOLEAN_FIELDS = frozenset({"naive_k1_would_accept"})
JUDGE_STRING_FIELDS = (
    frozenset(JUDGE_FIELDS)
    - JUDGE_INTEGER_FIELDS
    - JUDGE_NUMBER_FIELDS
    - JUDGE_BOOLEAN_FIELDS
)
assert frozenset(JUDGE_FIELDS) == (
    JUDGE_INTEGER_FIELDS
    | JUDGE_NUMBER_FIELDS
    | JUDGE_BOOLEAN_FIELDS
    | JUDGE_STRING_FIELDS
)


def _seed_from_parts(*parts: object) -> int:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return int.from_bytes(digest.digest()[:8], "big", signed=False)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * (pct / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _bootstrap_interval(
    values_ms: list[float], *, confidence: float, resamples: int, seed_parts: tuple[object, ...]
) -> tuple[float, float]:
    if len(values_ms) == 1:
        return float(values_ms[0]), float(values_ms[0])
    rng = random.Random(_seed_from_parts("bootstrap", confidence, resamples, *seed_parts))
    medians: list[float] = []
    for _ in range(max(1, resamples)):
        sample = [values_ms[rng.randrange(len(values_ms))] for _ in values_ms]
        medians.append(float(statistics.median(sample)))
    medians.sort()
    alpha = (1.0 - confidence) / 2.0
    return (
        _percentile(medians, alpha * 100.0),
        _percentile(medians, (1.0 - alpha) * 100.0),
    )


def _permutation_p_value(
    values_ms: list[float], *, observed: float, resamples: int, seed_parts: tuple[object, ...]
) -> float:
    if len(values_ms) == 1:
        return 1.0 if observed <= 0.0 else 0.5
    rng = random.Random(_seed_from_parts("permutation", observed, resamples, *seed_parts))
    extreme = 0
    total = max(1, resamples)
    for _ in range(total):
        flipped = [value if rng.getrandbits(1) else -value for value in values_ms]
        if float(statistics.median(flipped)) >= observed:
            extreme += 1
    return (extreme + 1.0) / (total + 1.0)


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _norm_ppf(probability: float) -> float:
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    low = 0.02425
    high = 1.0 - low
    if probability < low:
        q = math.sqrt(-2.0 * math.log(probability))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if probability <= high:
        q = probability - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    q = math.sqrt(-2.0 * math.log(1.0 - probability))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


def _formatted(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _csv(values: list[float]) -> str:
    return ",".join(f"{value:g}" for value in values)


def _noise_flag(deltas: list[float], control_median: float) -> str:
    if len(deltas) < 2:
        return "ok"
    stdev = statistics.stdev(deltas)
    spread = max(deltas) - min(deltas)
    mean_abs = statistics.mean(abs(value) for value in deltas)
    cv = stdev / mean_abs if mean_abs else 0.0
    if (
        spread / control_median >= NOISE_RANGE_RATIO
        or stdev / control_median >= NOISE_STDEV_RATIO
        or cv >= NOISE_CV_THRESHOLD
    ):
        return "NOISY"
    return "ok"


def _mde(stdev: float | None, pair_count: int, delta_min: float) -> float | None:
    if stdev is None or pair_count < 2:
        return None
    z = _norm_ppf(0.975) + _norm_ppf(0.80)
    return delta_min + z * stdev / math.sqrt(pair_count)


def _power(effect: float | None, stdev: float | None, pair_count: int, delta_min: float) -> float | None:
    if effect is None or stdev is None or pair_count < 2:
        return None
    gap = effect - delta_min
    standard_error = stdev / math.sqrt(pair_count)
    if standard_error == 0.0:
        return 1.0 if gap > 0.0 else 0.0
    return _norm_cdf(gap / standard_error - _norm_ppf(0.975))


def evaluate_stage(
    pairs: list[dict[str, Any]], *, verdict_context: str, screening_only: bool = False
) -> dict[str, str]:
    """Recompute one cumulative m4, m8, or m12 judge result."""

    if not pairs:
        raise ValueError("at least one pair is required")
    control = [float(pair["control_ms"]) for pair in pairs]
    candidate = [float(pair["candidate_ms"]) for pair in pairs]
    deltas = [control_ms - candidate_ms for control_ms, candidate_ms in zip(control, candidate)]
    pair_count = len(deltas)
    control_median = float(statistics.median(control))
    median_delta = float(statistics.median(deltas))
    mean_delta = float(statistics.mean(deltas))
    stdev = float(statistics.stdev(deltas)) if pair_count > 1 else None
    spread = float(max(deltas) - min(deltas)) if pair_count > 1 else 0.0
    noise_flag = _noise_flag(deltas, control_median)
    seed_parts = (
        control,
        candidate,
        verdict_context,
        REQUIRED_PAIRS,
        BUNDLE_AUDIT_SAMPLE_FLOOR,
    )
    ci_low, ci_high = _bootstrap_interval(
        deltas,
        confidence=CONFIDENCE,
        resamples=BOOTSTRAP_RESAMPLES,
        seed_parts=seed_parts,
    )
    p_value = _permutation_p_value(
        deltas,
        observed=median_delta,
        resamples=PERMUTATION_RESAMPLES,
        seed_parts=seed_parts,
    )
    delta_min = control_median * 0.005 if control_median > 0.0 else 0.0
    margin = ci_low - delta_min
    ci_width = ci_high - ci_low
    if ci_width > 0.0:
        decisiveness: float | None = margin / ci_width
    elif margin > 0.0:
        decisiveness = None
    else:
        decisiveness = 0.0
    if median_delta != 0.0:
        positive_median = median_delta > 0.0
        matching = sum(1 for value in deltas if (value > 0.0) == positive_median)
        sign_consistency = matching / pair_count
    else:
        sign_consistency = 1.0
    decisive = decisiveness is None or decisiveness >= DECISIVE_K
    if margin > 0.0 and decisive and sign_consistency >= SIGN_MIN:
        confidence_tier = "decisive"
    elif margin > 0.0 and p_value <= 0.05:
        confidence_tier = "marginal"
    else:
        confidence_tier = "weak"

    clear_non_improvement = (
        pair_count >= REQUIRED_PAIRS and median_delta <= 0.0 and ci_high <= 0.0
    )
    if clear_non_improvement:
        raw_verdict = "rejected"
    elif pair_count < REQUIRED_PAIRS:
        raw_verdict = "neutral"
    elif median_delta <= 0.0:
        raw_verdict = "rejected"
    elif confidence_tier == "decisive":
        raw_verdict = "accepted"
    elif confidence_tier == "marginal":
        raw_verdict = "accepted_noisy_single"
    elif noise_flag == "NOISY":
        raw_verdict = "NOISY_PENDING"
    else:
        raw_verdict = "neutral"

    first_delta = deltas[0]
    return {
        "control_sample_count": str(len(control)),
        "candidate_sample_count": str(len(candidate)),
        "paired_sample_count": str(pair_count),
        "control_median_ms": _formatted(control_median),
        "control_samples_ms": _csv(control),
        "candidate_samples_ms": _csv(candidate),
        "paired_deltas_ms": _csv(deltas),
        "median_delta_ms": _formatted(median_delta),
        "bootstrap_ci_low_ms": _formatted(ci_low),
        "bootstrap_ci_high_ms": _formatted(ci_high),
        "permutation_p_value": f"{p_value:.6f}",
        "paired_stdev_ms": _formatted(stdev),
        "paired_range_ms": _formatted(spread),
        "paired_mean_ms": _formatted(mean_delta),
        "noise_flag": noise_flag,
        "confidence_tier": confidence_tier,
        "confidence_margin_ms": _formatted(margin),
        "confidence_ci_width_ms": _formatted(ci_width),
        "confidence_decisiveness": "" if decisiveness is None else f"{decisiveness:.4f}",
        "confidence_sign_consistency": f"{sign_consistency:.4f}",
        "delta_min_ms_used": _formatted(delta_min),
        "decisive_k": f"{DECISIVE_K:.3f}",
        "sign_min": f"{SIGN_MIN:.3f}",
        "escalation_steps": ",".join(str(step) for step in SAMPLE_ESCALATION_STEPS),
        "naive_k1_first_delta_ms": f"{first_delta:.3f}",
        "naive_k1_would_accept": "true" if first_delta > 0.0 else "false",
        "mde_ms": _formatted(_mde(stdev, pair_count, delta_min)),
        "power_at_observed_effect": (
            ""
            if (power := _power(median_delta, stdev, pair_count, delta_min)) is None
            else f"{power:.4f}"
        ),
        "raw_verdict": raw_verdict,
        "verdict": "screening_only" if screening_only else raw_verdict,
    }


def to_typed_judge(rendered: dict[str, str]) -> dict[str, Any]:
    """Project one rendered (all-string) judge dict into native JSON types.

    - count fields become ``int``;
    - other number fields become ``float`` (empty rendered string becomes ``None``);
    - boolean fields become ``bool``;
    - the remaining categorical/CSV fields stay strings.
    """

    typed: dict[str, Any] = {}
    for field, value in rendered.items():
        if field in JUDGE_BOOLEAN_FIELDS:
            if value == "true":
                typed[field] = True
            elif value == "false":
                typed[field] = False
            else:
                raise ValueError(f"non-boolean rendered value for {field}: {value!r}")
        elif field in JUDGE_INTEGER_FIELDS:
            typed[field] = None if value == "" else int(value)
        elif field in JUDGE_NUMBER_FIELDS:
            typed[field] = None if value == "" else float(value)
        else:
            typed[field] = value
    return typed


def typed_stage(
    pairs: list[dict[str, Any]], *, verdict_context: str, screening_only: bool = False
) -> dict[str, Any]:
    """Native-JSON-typed variant of :func:`evaluate_stage`.

    Derived from the same rendered output so the string and typed
    representations cannot drift apart.
    """

    return to_typed_judge(
        evaluate_stage(
            pairs,
            verdict_context=verdict_context,
            screening_only=screening_only,
        )
    )
