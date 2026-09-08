#!/usr/bin/env python3
"""Paired timing analysis helpers for headless optimization runs."""

from __future__ import annotations

import datetime
import hashlib
import math
import random
import socket
import statistics
from dataclasses import dataclass


DEFAULT_PROMOTION_SAMPLE_FLOOR = 5
DEFAULT_BUNDLE_AUDIT_SAMPLE_FLOOR = 7
# Deprecated absolute-ms fallback thresholds; retained for back-compat with
# callers that cannot yet pass a control-median scale. Prefer the relative
# ratios (DEFAULT_NOISE_RANGE_RATIO, DEFAULT_NOISE_STDEV_RATIO) below.
DEFAULT_NOISE_RANGE_THRESHOLD_MS = 5.0
DEFAULT_NOISE_STDEV_THRESHOLD_MS = 1.5
# Relative noise thresholds, expressed as fractions of the control median.
DEFAULT_NOISE_RANGE_RATIO = 0.02
DEFAULT_NOISE_STDEV_RATIO = 0.005
DEFAULT_NOISE_CV_THRESHOLD = 0.02
DEFAULT_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_PERMUTATION_RESAMPLES = 2000
DEFAULT_CONFIDENCE = 0.95
VERDICT_METHOD = "paired_bootstrap_permutation_v1"
# SNR-native confidence tier constants (B redesign, Option A / CI-native, locked 2026-06-02).
# decisive_k: CI-widths the margin must clear above delta_min_ms.
# sign_min: minimum fraction of paired deltas sharing the sign of the median delta.
DEFAULT_DECISIVE_K = 1.0
DEFAULT_SIGN_MIN = 0.9
DEFAULT_MIN_NORMAL_P95_IMPROVEMENT_MS = 1.0
DEFAULT_MAX_NORMAL_P95_REGRESSION_MS = 1.0
DEFAULT_MAX_STRESS_P95_REGRESSION_MS = 5.0
DEFAULT_SAMPLE_ESCALATION_STEPS = (4, 8, 12)
# Independence verification: weather-bucket boundaries and minimum time gap.
# Two audit records are "independent" when their paired-delta noise profiles
# fall in *different* buckets AND they are separated by at least this gap.
# Bucket edges are module constants so they appear in the evidence ledger.
WEATHER_BUCKET_STDEV_THRESHOLD_MS = 200.0
WEATHER_BUCKET_RANGE_THRESHOLD_MS = 400.0
INDEPENDENCE_MIN_GAP_SECONDS = 1800.0  # 30 minutes


@dataclass(frozen=True)
class PairedTimingEvidence:
    control_samples_ms: list[float]
    candidate_samples_ms: list[float]
    paired_deltas_ms: list[float]
    verdict: str
    reason: str
    noise_flag: str
    required_pairs: int
    paired_sample_count: int
    control_sample_count: int
    candidate_sample_count: int
    control_median_ms: float | None
    control_median_seconds: float | None
    median_delta_ms: float | None
    bootstrap_ci_low_ms: float | None
    bootstrap_ci_high_ms: float | None
    permutation_p_value: float | None
    paired_stdev_ms: float | None
    paired_range_ms: float | None
    paired_mean_ms: float | None
    verdict_method: str = VERDICT_METHOD
    # Confidence tier fields (B redesign, 2026-06-02). Added with defaults so
    # existing callers that construct PairedTimingEvidence directly are unaffected.
    confidence_tier_name: str | None = None
    confidence_margin_ms: float | None = None
    confidence_ci_width_ms: float | None = None
    confidence_decisiveness: float | None = None
    confidence_sign_consistency: float | None = None
    # G2 — decision constants stamped per verdict (paper §A.4 auditability).
    delta_min_ms_used: float | None = None
    decisive_k_used: float | None = None
    sign_min_used: float | None = None
    escalation_steps_used: tuple[int, ...] = DEFAULT_SAMPLE_ESCALATION_STEPS
    # G4 — control-sample jitter stats for weather / env fingerprint.
    control_stdev_ms: float | None = None
    control_range_ms: float | None = None
    # Detectability floor (2.14, 2026-06-18). Additive; verdict-neutral.
    # mde_ms: smallest true effect resolvable at this host/depth (power=0.80).
    # power_at_observed_effect: power for the observed median delta.
    mde_ms: float | None = None
    power_at_observed_effect: float | None = None


def _seed_from_parts(*parts: object) -> int:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return int.from_bytes(digest.digest()[:8], "big", signed=False)


def _csv(values: list[float]) -> str:
    return ",".join(f"{value:g}" for value in values)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * (pct / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def paired_samples(control_samples_ms: list[float], candidate_samples_ms: list[float]) -> list[dict[str, float]]:
    paired = []
    for index, (control_ms, candidate_ms) in enumerate(
        zip(control_samples_ms, candidate_samples_ms, strict=False),
        start=1,
    ):
        paired.append(
            {
                "pair_index": float(index),
                "control_ms": float(control_ms),
                "candidate_ms": float(candidate_ms),
                "delta_ms": float(control_ms) - float(candidate_ms),
            }
        )
    return paired


def bootstrap_interval(
    values_ms: list[float],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed_parts: tuple[object, ...] = (),
) -> tuple[float | None, float | None]:
    if not values_ms:
        return None, None
    if len(values_ms) == 1:
        value = float(values_ms[0])
        return value, value
    rng = random.Random(_seed_from_parts("bootstrap", confidence, resamples, *seed_parts))
    ordered: list[float] = []
    for _ in range(max(1, resamples)):
        sample = [values_ms[rng.randrange(len(values_ms))] for _ in values_ms]
        ordered.append(float(statistics.median(sample)))
    ordered.sort()
    alpha = (1.0 - confidence) / 2.0
    return _percentile(ordered, alpha * 100.0), _percentile(ordered, (1.0 - alpha) * 100.0)


def permutation_p_value(
    values_ms: list[float],
    *,
    observed_statistic: float,
    resamples: int = DEFAULT_PERMUTATION_RESAMPLES,
    seed_parts: tuple[object, ...] = (),
) -> float | None:
    if not values_ms:
        return None
    if len(values_ms) == 1:
        return 1.0 if observed_statistic <= 0.0 else 0.5
    rng = random.Random(_seed_from_parts("permutation", observed_statistic, resamples, *seed_parts))
    extreme = 0
    total = max(1, resamples)
    for _ in range(total):
        flipped = [value if rng.getrandbits(1) else -value for value in values_ms]
        statistic_value = float(statistics.median(flipped))
        if statistic_value >= observed_statistic:
            extreme += 1
    return (extreme + 1.0) / (total + 1.0)


# --- Detectability floor: MDE + power (2.14, 2026-06-18). -------------------
# Additive, verdict-neutral. These answer "what is the smallest true effect this
# host + this sample budget can resolve, and how powered was this run?" — they do
# NOT change the accept/reject/neutral ladder. First-order paired-design
# (normal/CLT) guides for the selection layer and paper ledger; the production
# verdict still ranks on the median + bootstrap CI + permutation p. See 2.14 §2.
DEFAULT_MDE_ALPHA = 0.05
DEFAULT_MDE_POWER = 0.80


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF Phi(x) via math.erf (no third-party dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation).

    Accurate to ~1.1e-9 over (0, 1); no third-party dependency.
    """
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")
    a = (
        -3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
        1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00,
    )
    b = (
        -5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
        6.680131188771972e+01, -1.328068155288572e+01,
    )
    c = (
        -7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
        -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00,
    )
    d = (
        7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
        3.754408661907416e+00,
    )
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


def min_detectable_effect_ms(
    paired_stdev_ms: float | None,
    n_pairs: int,
    *,
    delta_min_ms: float = 0.0,
    alpha: float = DEFAULT_MDE_ALPHA,
    power: float = DEFAULT_MDE_POWER,
) -> float | None:
    """Smallest true mean shift this host + sample budget could reliably detect.

    Paired design, normal (CLT) approximation for the mean delta::

        MDE = delta_min + (z_{1-alpha/2} + z_power) * s_d / sqrt(n)

    Returns None when no standard error is defined (n < 2, or stdev missing /
    negative).  Budgeting guide, not the production gate boundary (2.14 §2.2).
    """
    if paired_stdev_ms is None or n_pairs < 2 or paired_stdev_ms < 0.0:
        return None
    se = paired_stdev_ms / math.sqrt(n_pairs)
    z = _norm_ppf(1.0 - alpha / 2.0) + _norm_ppf(power)
    return float(delta_min_ms) + z * se


def power_at_effect_ms(
    effect_ms: float | None,
    paired_stdev_ms: float | None,
    n_pairs: int,
    *,
    delta_min_ms: float = 0.0,
    alpha: float = DEFAULT_MDE_ALPHA,
) -> float | None:
    """Probability the paired test rejects the null if the true effect is ``effect_ms``::

        power = Phi( (effect - delta_min)/se - z_{1-alpha/2} )

    A non-accepting run with low power here is "under-powered," not a proven
    null.  Returns None when no standard error is defined.
    """
    if effect_ms is None or paired_stdev_ms is None or n_pairs < 2 or paired_stdev_ms < 0.0:
        return None
    se = paired_stdev_ms / math.sqrt(n_pairs)
    threshold = float(effect_ms) - float(delta_min_ms)
    if se == 0.0:
        return 1.0 if threshold > 0.0 else 0.0
    z = _norm_ppf(1.0 - alpha / 2.0)
    return _norm_cdf(threshold / se - z)


def pairs_needed_for_effect(
    target_effect_ms: float,
    paired_stdev_ms: float | None,
    *,
    delta_min_ms: float = 0.0,
    alpha: float = DEFAULT_MDE_ALPHA,
    power: float = DEFAULT_MDE_POWER,
) -> int | None:
    """Pairs required to resolve ``target_effect_ms`` at the configured power::

        n* = ceil( ( (z_{1-alpha/2} + z_power) * s_d / (effect - delta_min) )^2 )

    Returns None when the target does not clear ``delta_min`` (unresolvable at
    any depth) or the stdev is unavailable.
    """
    if paired_stdev_ms is None or paired_stdev_ms < 0.0:
        return None
    gap = float(target_effect_ms) - float(delta_min_ms)
    if gap <= 0.0:
        return None
    if paired_stdev_ms == 0.0:
        return 2
    z = _norm_ppf(1.0 - alpha / 2.0) + _norm_ppf(power)
    return max(2, int(math.ceil((z * paired_stdev_ms / gap) ** 2)))


def noise_flag_from_deltas(
    paired_deltas_ms: list[float],
    *,
    range_threshold_ms: float = DEFAULT_NOISE_RANGE_THRESHOLD_MS,
    stdev_threshold_ms: float = DEFAULT_NOISE_STDEV_THRESHOLD_MS,
    cv_threshold: float = DEFAULT_NOISE_CV_THRESHOLD,
) -> str:
    """Deprecated: absolute-ms noise check retained as a fallback.

    Prefer :func:`noise_flag_from_paired` which scales the range/stdev
    thresholds by the control median so they stay meaningful across
    sub-second and multi-second workloads.
    """
    if len(paired_deltas_ms) < 2:
        return "ok"
    ordered = [float(value) for value in paired_deltas_ms]
    stdev_ms = statistics.stdev(ordered)
    range_ms = max(ordered) - min(ordered)
    mean_abs_ms = statistics.mean(abs(value) for value in ordered)
    cv = stdev_ms / mean_abs_ms if mean_abs_ms else 0.0
    if range_ms >= range_threshold_ms or stdev_ms >= stdev_threshold_ms or cv >= cv_threshold:
        return "NOISY"
    return "ok"


def noise_flag_from_paired(
    paired_deltas_ms: list[float],
    control_median_ms: float,
    *,
    range_ratio: float = DEFAULT_NOISE_RANGE_RATIO,
    stdev_ratio: float = DEFAULT_NOISE_STDEV_RATIO,
    cv_threshold: float = DEFAULT_NOISE_CV_THRESHOLD,
) -> str:
    """Relative-ratio noise check. Flags NOISY when paired delta range or
    stdev exceeds a configured fraction of the control median, or when the
    coefficient of variation of the deltas is too large.

    Guards ``control_median_ms <= 0`` by falling back to an absolute-ms
    check using the default deprecated thresholds.
    """
    if len(paired_deltas_ms) < 2:
        return "ok"
    ordered = [float(value) for value in paired_deltas_ms]
    stdev_ms = statistics.stdev(ordered)
    range_ms = max(ordered) - min(ordered)
    mean_abs_ms = statistics.mean(abs(value) for value in ordered)
    cv = stdev_ms / mean_abs_ms if mean_abs_ms else 0.0
    if control_median_ms is None or control_median_ms <= 0.0:
        return noise_flag_from_deltas(ordered, cv_threshold=cv_threshold)
    if (
        range_ms / control_median_ms >= range_ratio
        or stdev_ms / control_median_ms >= stdev_ratio
        or cv >= cv_threshold
    ):
        return "NOISY"
    return "ok"


@dataclass(frozen=True)
class ConfidenceTierResult:
    """Result of the SNR-native confidence tier assessment (B redesign, 2026-06-02).

    tier: "decisive" | "marginal" | "weak"
    margin: bootstrap_ci_low_ms - delta_min_ms  (positive = CI floor clears the worthwhile line)
    ci_width: bootstrap_ci_high_ms - bootstrap_ci_low_ms  (spread already absorbed by the CI)
    decisiveness: margin / ci_width  (CI-widths of clearance; None when ci_width == 0)
    sign_consistency: fraction of paired_deltas sharing the sign of median_delta
    """

    tier: str
    margin: float | None
    ci_width: float | None
    decisiveness: float | None
    sign_consistency: float | None


@dataclass(frozen=True)
class SampleEscalationDecision:
    """Decision for weak-but-promising timing evidence.

    action:
      - ESCALATE: collect the next configured sample depth before final park.
      - PARK: do not collect deeper samples for this evidence shape.
      - NO_ACTION: verdict is already decided or not in the escalation path.
    """

    action: str
    next_sample_count: int | None
    reason: str


@dataclass(frozen=True)
class RegressionCheck:
    name: str
    measured: float | None
    limit: float | None
    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class ScorecardPrimary:
    name: str
    samples_ms: list[float]
    required_sample_count: int
    control_median_ms: float
    median_delta_ms: float | None
    bootstrap_ci_low_ms: float | None
    bootstrap_ci_high_ms: float | None
    permutation_p_value: float | None
    paired_stdev_ms: float | None
    paired_range_ms: float | None
    noise_flag: str
    confidence_tier: ConfidenceTierResult


@dataclass(frozen=True)
class Scorecard:
    scenario_id: str
    correctness_pass: bool
    correctness_reason: str
    primary: ScorecardPrimary
    regressions: tuple[RegressionCheck, ...] = ()
    performance_bypass_verdict: str = ""
    performance_bypass_reason: str = ""


@dataclass(frozen=True)
class ScorecardVerdict:
    verdict: str
    reason: str


@dataclass(frozen=True)
class ThresholdCaseDelta:
    case: str
    p95_delta_ms: float | None
    lane: str


@dataclass(frozen=True)
class ThresholdConsistencyResult:
    decision: str
    accepted: bool
    lost_failure_count: int
    has_control: bool
    normal_frequency_p95_improved: bool
    normal_frequency_p95_regression_ok: bool
    stress_p95_regression_ok: bool
    normal_cases: tuple[ThresholdCaseDelta, ...]
    stress_cases: tuple[ThresholdCaseDelta, ...]


def confidence_tier(
    bootstrap_ci_low_ms: float | None,
    bootstrap_ci_high_ms: float | None,
    median_delta_ms: float | None,
    paired_deltas_ms: list[float],
    permutation_p_value_arg: float | None,
    *,
    delta_min_ms: float = 0.0,
    decisive_k: float = DEFAULT_DECISIVE_K,
    sign_min: float = DEFAULT_SIGN_MIN,
) -> ConfidenceTierResult:
    """SNR-native confidence tier — Option A / CI-native, locked 2026-06-02.

    Replaces the ``noise_flag`` gate in the verdict ladder.  The CI already
    absorbed the paired-delta spread; requiring ``noise_flag != NOISY`` on top
    double-taxes the same spread.  This function asks instead: *how many
    CI-widths does the CI floor sit above the minimum-worthwhile delta?*

    Tier rules (Option A — CI-native; permutation p gates only the marginal tier):
    - **decisive**: margin > 0  AND  decisiveness >= decisive_k  AND
                    sign_consistency >= sign_min.
                    Accepts without replication even on a noisy host.
    - **marginal**: margin > 0  AND  permutation_p <= 0.05  AND  not decisive.
                    Requires replication for the upgraded verdict.
    - **weak**: otherwise (margin <= 0, or p > 0.05, or sign mixed).

    delta_min_ms: minimum worthwhile effect — human-frozen contract value;
      defaults to 0.0 for backward compatibility.  Mark "→ contract field when
      A lands" when wiring into the unified scorecard.
    """
    if bootstrap_ci_low_ms is None or bootstrap_ci_high_ms is None:
        return ConfidenceTierResult(
            tier="weak",
            margin=None,
            ci_width=None,
            decisiveness=None,
            sign_consistency=None,
        )

    margin = bootstrap_ci_low_ms - delta_min_ms
    ci_width = bootstrap_ci_high_ms - bootstrap_ci_low_ms

    # decisiveness = margin / ci_width.  When ci_width == 0 the CI is a point
    # estimate; store None to signal "degenerate" but treat as >= decisive_k
    # (zero uncertainty with positive margin is maximally decisive).
    if ci_width > 0.0:
        decisiveness: float | None = margin / ci_width
    elif margin > 0.0:
        decisiveness = None  # zero-width CI, positive margin → effectively ∞
    else:
        decisiveness = 0.0

    # Sign consistency: fraction of deltas sharing the sign of median_delta.
    if paired_deltas_ms and median_delta_ms is not None and median_delta_ms != 0.0:
        positive_median = median_delta_ms > 0.0
        matching = sum(1 for d in paired_deltas_ms if (d > 0.0) == positive_median)
        sign_consistency: float | None = matching / len(paired_deltas_ms)
    elif paired_deltas_ms:
        sign_consistency = 1.0
    else:
        sign_consistency = None

    # Tier assignment.
    decisiveness_ok = decisiveness is None or decisiveness >= decisive_k
    sign_ok = sign_consistency is not None and sign_consistency >= sign_min

    if margin > 0.0 and decisiveness_ok and sign_ok:
        tier = "decisive"
    elif (
        margin > 0.0
        and permutation_p_value_arg is not None
        and permutation_p_value_arg <= 0.05
    ):
        tier = "marginal"
    else:
        tier = "weak"

    return ConfidenceTierResult(
        tier=tier,
        margin=margin,
        ci_width=ci_width,
        decisiveness=decisiveness,
        sign_consistency=sign_consistency,
    )


def next_sample_escalation_depth(
    current_sample_count: int,
    *,
    steps: tuple[int, ...] = DEFAULT_SAMPLE_ESCALATION_STEPS,
) -> int | None:
    for step in steps:
        if current_sample_count < step:
            return step
    return None


def sample_escalation_decision(
    *,
    verdict: str,
    correctness_pass: bool,
    paired_sample_count: int,
    median_delta_ms: float | None,
    bootstrap_ci_low_ms: float | None,
    bootstrap_ci_high_ms: float | None,
    delta_min_ms: float,
    decisiveness: float | None = None,
    decisive_k: float = DEFAULT_DECISIVE_K,
) -> SampleEscalationDecision:
    """Classify weak timing evidence as deeper-sampling eligible or terminal.

    This is deliberately downstream of the confidence-tier verdict. It never
    accepts a candidate; it only decides whether a NOISY_PENDING candidate
    should be retried at the next sample depth before being parked.
    """

    if verdict != "NOISY_PENDING":
        return SampleEscalationDecision("NO_ACTION", None, f"verdict={verdict} is already decided")
    if not correctness_pass:
        return SampleEscalationDecision("PARK", None, "correctness did not pass")

    next_depth = next_sample_escalation_depth(paired_sample_count)
    if next_depth is None:
        return SampleEscalationDecision("PARK", None, f"sample depth {paired_sample_count} is at escalation cap")

    if median_delta_ms is None or median_delta_ms <= 0.0:
        return SampleEscalationDecision("PARK", None, "median delta is not positive")
    if bootstrap_ci_low_ms is None or bootstrap_ci_high_ms is None:
        return SampleEscalationDecision("PARK", None, "bootstrap CI is unavailable")

    margin = bootstrap_ci_low_ms - delta_min_ms
    if margin < 0.0:
        return SampleEscalationDecision(
            "PARK",
            None,
            f"CI floor does not clear delta_min_ms (margin={margin:.3f}ms)",
        )

    ci_width = bootstrap_ci_high_ms - bootstrap_ci_low_ms
    if decisiveness is None:
        if ci_width > 0.0:
            decisiveness = margin / ci_width
        elif margin > 0.0:
            decisiveness = None
        else:
            decisiveness = 0.0

    if decisiveness is None or decisiveness >= decisive_k:
        return SampleEscalationDecision(
            "NO_ACTION",
            None,
            "evidence is decisive enough for the existing verdict ladder",
        )

    return SampleEscalationDecision(
        "ESCALATE",
        next_depth,
        (
            f"weak-but-promising evidence: median_delta_ms={median_delta_ms:.3f}, "
            f"CI_low={bootstrap_ci_low_ms:.3f}, delta_min_ms={delta_min_ms:.3f}, "
            f"decisiveness={decisiveness:.3f}<{decisive_k:.3f}; escalate to m{next_depth}"
        ),
    )


def case_interval_ms(case: str) -> int | None:
    case_base = str(case).split("_s", 1)[0]
    for part in case_base.split("_"):
        if part.startswith("i"):
            try:
                return int(part[1:])
            except ValueError:
                return None
    return None


def _float_or_none(value: object) -> float | None:
    try:
        if value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: object) -> int:
    try:
        if value == "":
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _case_status_failed(row: dict[str, object]) -> bool:
    return str(row.get("status") or "") not in {"", "PASS"}


def threshold_consistency(
    case_deltas: list[dict[str, object]],
    *,
    build_status: str = "pass",
    correctness_status: str = "pass",
    timing_status: str = "pass",
    has_control: bool = True,
    lost_failure_count: int = 0,
    derive_lost_failures_from_cases: bool = True,
    min_normal_improvement_ms: float = DEFAULT_MIN_NORMAL_P95_IMPROVEMENT_MS,
    max_normal_regression_ms: float = DEFAULT_MAX_NORMAL_P95_REGRESSION_MS,
    max_stress_regression_ms: float = DEFAULT_MAX_STRESS_P95_REGRESSION_MS,
) -> ThresholdConsistencyResult:
    """Mirror the batch runner summary writer's auto-decision logic."""

    normal_cases: list[ThresholdCaseDelta] = []
    stress_cases: list[ThresholdCaseDelta] = []
    derived_lost_failures = int(lost_failure_count)

    for row in case_deltas:
        if not isinstance(row, dict):
            continue
        case = str(row.get("case") or "")
        delta = _float_or_none(row.get("p95_delta_ms"))
        control_lost = _int_or_zero(row.get("control_lost"))
        candidate_lost = _int_or_zero(row.get("candidate_lost"))
        control_unknown = _int_or_zero(row.get("control_unknown_pushes"))
        candidate_unknown = _int_or_zero(row.get("candidate_unknown_pushes"))
        if derive_lost_failures_from_cases and (
            control_lost or candidate_lost or control_unknown or candidate_unknown or _case_status_failed(row)
        ):
            derived_lost_failures += 1

        interval_ms = case_interval_ms(case)
        if interval_ms is None:
            continue
        if interval_ms >= 20:
            normal_cases.append(ThresholdCaseDelta(case=case, p95_delta_ms=delta, lane="normal"))
        elif interval_ms <= 5:
            stress_cases.append(ThresholdCaseDelta(case=case, p95_delta_ms=delta, lane="stress"))

    normal_improved = bool(normal_cases) and all(
        delta.p95_delta_ms is not None and delta.p95_delta_ms <= -min_normal_improvement_ms
        for delta in normal_cases
    )
    normal_regression_ok = all(
        delta.p95_delta_ms is not None and delta.p95_delta_ms <= max_normal_regression_ms
        for delta in normal_cases
    )
    stress_regression_ok = all(
        delta.p95_delta_ms is not None and delta.p95_delta_ms <= max_stress_regression_ms
        for delta in stress_cases
    )
    accepted = (
        build_status == "pass"
        and correctness_status == "pass"
        and timing_status == "pass"
        and derived_lost_failures == 0
        and has_control
        and normal_improved
        and stress_regression_ok
    )
    rejected = derived_lost_failures > 0 or not normal_regression_ok or not stress_regression_ok
    decision = "promotion_candidate" if accepted else "rejected" if rejected else "screening_only"
    return ThresholdConsistencyResult(
        decision=decision,
        accepted=accepted,
        lost_failure_count=derived_lost_failures,
        has_control=has_control,
        normal_frequency_p95_improved=normal_improved,
        normal_frequency_p95_regression_ok=normal_regression_ok,
        stress_p95_regression_ok=stress_regression_ok,
        normal_cases=tuple(normal_cases),
        stress_cases=tuple(stress_cases),
    )


class ThresholdAdapter:
    """Translate case-delta summaries into the threshold-consistency judge."""

    @staticmethod
    def threshold_result(
        summary: dict[str, object],
        *,
        min_normal_improvement_ms: float = DEFAULT_MIN_NORMAL_P95_IMPROVEMENT_MS,
        max_normal_regression_ms: float = DEFAULT_MAX_NORMAL_P95_REGRESSION_MS,
        max_stress_regression_ms: float = DEFAULT_MAX_STRESS_P95_REGRESSION_MS,
    ) -> ThresholdConsistencyResult:
        raw_deltas = summary.get("case_deltas") or summary.get("case_deltas") or []
        case_deltas = raw_deltas if isinstance(raw_deltas, list) else []
        has_shell_lost_count = "lost_failure_count" in summary
        return threshold_consistency(
            case_deltas,
            build_status=str(summary.get("build_status") or "pass"),
            correctness_status=str(summary.get("correctness_status") or "pass"),
            timing_status=str(summary.get("timing_status") or "pass"),
            has_control=bool(summary.get("has_control", True)),
            lost_failure_count=_int_or_zero(summary.get("lost_failure_count")),
            derive_lost_failures_from_cases=not has_shell_lost_count,
            min_normal_improvement_ms=min_normal_improvement_ms,
            max_normal_regression_ms=max_normal_regression_ms,
            max_stress_regression_ms=max_stress_regression_ms,
        )


class TimingAdapter:
    """Translate paired timing evidence into the domain-blind scorecard."""

    @staticmethod
    def scorecard(
        *,
        deltas_ms: list[float],
        required_pairs: int,
        control_median_ms: float,
        median_delta_ms: float | None,
        bootstrap_ci_low_ms: float | None,
        bootstrap_ci_high_ms: float | None,
        permutation_p_value_arg: float | None,
        paired_stdev_ms: float | None,
        paired_range_ms: float | None,
        noise_flag: str,
        confidence_tier_result: ConfidenceTierResult,
        build_pass: bool,
        compare_pass: bool,
        change_class: str,
        scenario_id: str = "paired_timing",
    ) -> Scorecard:
        if not build_pass:
            correctness_pass = False
            correctness_reason = "build failed before paired timing evidence could be trusted."
        elif not compare_pass:
            correctness_pass = False
            correctness_reason = "compare failed; the paired timing evidence is invalid."
        else:
            correctness_pass = True
            correctness_reason = ""

        bypass_verdict = ""
        bypass_reason = ""
        if correctness_pass and change_class == "class_a":
            bypass_verdict = "accepted_class_a"
            bypass_reason = "Class A algorithmic change: correctness pass is sufficient; perf recorded but not gated."

        return Scorecard(
            scenario_id=scenario_id,
            correctness_pass=correctness_pass,
            correctness_reason=correctness_reason,
            performance_bypass_verdict=bypass_verdict,
            performance_bypass_reason=bypass_reason,
            primary=ScorecardPrimary(
                name="paired_median_delta_ms",
                samples_ms=deltas_ms,
                required_sample_count=required_pairs,
                control_median_ms=control_median_ms,
                median_delta_ms=median_delta_ms,
                bootstrap_ci_low_ms=bootstrap_ci_low_ms,
                bootstrap_ci_high_ms=bootstrap_ci_high_ms,
                permutation_p_value=permutation_p_value_arg,
                paired_stdev_ms=paired_stdev_ms,
                paired_range_ms=paired_range_ms,
                noise_flag=noise_flag,
                confidence_tier=confidence_tier_result,
            ),
        )


def judge_scorecard(
    scorecard: Scorecard,
    *,
    replicated: bool = False,
    decisive_k: float = DEFAULT_DECISIVE_K,
) -> ScorecardVerdict:
    """Domain-blind scorecard judge for the primary performance metric."""

    primary = scorecard.primary
    median_delta_ms = primary.median_delta_ms
    bootstrap_low_ms = primary.bootstrap_ci_low_ms
    bootstrap_high_ms = primary.bootstrap_ci_high_ms
    p_value = primary.permutation_p_value
    pair_count = len(primary.samples_ms)
    control_median_ms = primary.control_median_ms
    paired_range_ms = primary.paired_range_ms
    paired_stdev_ms = primary.paired_stdev_ms
    noise_flag = primary.noise_flag
    ct = primary.confidence_tier

    if not scorecard.correctness_pass:
        return ScorecardVerdict("rejected", scorecard.correctness_reason)
    if scorecard.performance_bypass_verdict:
        return ScorecardVerdict(scorecard.performance_bypass_verdict, scorecard.performance_bypass_reason)
    for regression in scorecard.regressions:
        if not regression.ok:
            reason = regression.reason or f"regression {regression.name} breached configured limit"
            return ScorecardVerdict("rejected", reason)

    clear_non_improvement = (
        pair_count >= primary.required_sample_count
        and median_delta_ms is not None
        and median_delta_ms <= 0.0
        and bootstrap_high_ms is not None
        and bootstrap_high_ms <= 0.0
    )

    if clear_non_improvement:
        return ScorecardVerdict(
            "rejected",
            (
                f"paired evidence shows non-improvement; median delta={median_delta_ms:.3f}ms against "
                f"control median {control_median_ms:.3f}ms with bootstrap CI "
                f"[{_format_optional_ms(bootstrap_low_ms)}, {_format_optional_ms(bootstrap_high_ms)}]ms "
                f"and permutation p={p_value:.6f}."
            ),
        )
    if pair_count < primary.required_sample_count:
        return ScorecardVerdict(
            "neutral",
            f"screening only; collected {pair_count} paired samples, need at least {primary.required_sample_count}.",
        )
    if median_delta_ms is None or median_delta_ms <= 0.0:
        return ScorecardVerdict(
            "rejected",
            "paired median delta is not positive; candidate is not faster under the interleaved A/B samples.",
        )
    if ct.tier == "decisive":
        decisiveness_str = f"{ct.decisiveness:.3f}" if ct.decisiveness is not None else "\u221e"
        return ScorecardVerdict(
            "accepted",
            (
                f"paired median delta={median_delta_ms:.3f}ms against control median {control_median_ms:.3f}ms "
                f"with bootstrap CI [{_format_optional_ms(bootstrap_low_ms)}, {_format_optional_ms(bootstrap_high_ms)}]ms "
                f"(CI-native decisive: margin={_format_optional_ms(ct.margin)}ms, "
                f"decisiveness={decisiveness_str}, "
                f"sign_consistency={ct.sign_consistency:.2f}); "
                f"permutation p={_format_optional_ms(p_value) if p_value is not None else 'n/a'} (reported, not gating)."
            ),
        )
    if ct.tier == "marginal":
        if replicated:
            return ScorecardVerdict(
                "accepted_noisy_replicated",
                (
                    f"paired median delta={median_delta_ms:.3f}ms against control median {control_median_ms:.3f}ms "
                    f"is statistically conclusive (bootstrap CI [{_format_optional_ms(bootstrap_low_ms)}, "
                    f"{_format_optional_ms(bootstrap_high_ms)}]ms, permutation p={p_value:.6f}) "
                    f"with replicated evidence across multiple locked independent runs; "
                    f"measurement environment was noisy (range={_format_optional_ms(paired_range_ms)}ms, "
                    f"stdev={_format_optional_ms(paired_stdev_ms)}ms) but replication supports shared-host promotion; "
                    f"artifact marked non-bare-metal."
                ),
            )
        marginal_dec = f"{ct.decisiveness:.3f}" if ct.decisiveness is not None else "0.000"
        marginal_sign = f"{ct.sign_consistency:.2f}" if ct.sign_consistency is not None else "0.00"
        return ScorecardVerdict(
            "accepted_noisy_single",
            (
                f"paired median delta={median_delta_ms:.3f}ms against control median {control_median_ms:.3f}ms "
                f"is statistically conclusive (bootstrap CI [{_format_optional_ms(bootstrap_low_ms)}, "
                f"{_format_optional_ms(bootstrap_high_ms)}]ms, permutation p={p_value:.6f}) "
                f"but confidence tier is marginal (decisiveness={marginal_dec} "
                f"< {decisive_k}, sign_consistency={marginal_sign}); "
                f"accepted as evidence only \u2014 NOT applied; queued for validation replication."
            ),
        )

    if noise_flag == "NOISY":
        return ScorecardVerdict(
            "NOISY_PENDING",
            (
                f"paired jitter is noisy against control median {control_median_ms:.3f}ms "
                f"and the statistical evidence is not yet conclusive "
                f"(confidence tier: weak, margin={_format_optional_ms(ct.margin)}ms); "
                f"range={_format_optional_ms(paired_range_ms)}ms, stdev={_format_optional_ms(paired_stdev_ms)}ms."
            ),
        )
    return ScorecardVerdict(
        "neutral",
        (
            f"paired median delta={median_delta_ms:.3f}ms is positive but not yet credible enough for acceptance "
            f"(confidence tier: weak, margin={_format_optional_ms(ct.margin)}ms); "
            f"bootstrap CI [{_format_optional_ms(bootstrap_low_ms)}, {_format_optional_ms(bootstrap_high_ms)}]ms, "
            f"p={p_value:.6f}."
        ),
    )


def format_samples_ms(values_ms: list[float]) -> str:
    return _csv([float(value) for value in values_ms])


def _format_optional_ms(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def summarize_paired_timing(
    control_samples_ms: list[float],
    candidate_samples_ms: list[float],
    *,
    build_pass: bool = True,
    compare_pass: bool = True,
    change_class: str = "class_b",
    replicated: bool = False,
    required_pairs: int = DEFAULT_PROMOTION_SAMPLE_FLOOR,
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    permutation_resamples: int = DEFAULT_PERMUTATION_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    sample_floor_for_bundle_audit: int = DEFAULT_BUNDLE_AUDIT_SAMPLE_FLOOR,
    noise_range_threshold_ms: float = DEFAULT_NOISE_RANGE_THRESHOLD_MS,
    noise_stdev_threshold_ms: float = DEFAULT_NOISE_STDEV_THRESHOLD_MS,
    noise_cv_threshold: float = DEFAULT_NOISE_CV_THRESHOLD,
    noise_range_ratio: float = DEFAULT_NOISE_RANGE_RATIO,
    noise_stdev_ratio: float = DEFAULT_NOISE_STDEV_RATIO,
    verdict_context: str = "",
    # B redesign parameters (2026-06-02).  delta_min_ms defaults to None,
    # meaning "compute as 0.5 % of control median" — mark as "→ contract field
    # when A (unified scorecard) lands".
    delta_min_ms: float | None = None,
    decisive_k: float = DEFAULT_DECISIVE_K,
    sign_min: float = DEFAULT_SIGN_MIN,
) -> PairedTimingEvidence:
    control = [float(value) for value in control_samples_ms]
    candidate = [float(value) for value in candidate_samples_ms]
    paired = paired_samples(control, candidate)
    deltas_ms = [row["delta_ms"] for row in paired]
    pair_count = len(paired)
    median_delta_ms = float(statistics.median(deltas_ms)) if deltas_ms else None
    paired_mean_ms = float(statistics.mean(deltas_ms)) if deltas_ms else None
    paired_stdev_ms = float(statistics.stdev(deltas_ms)) if len(deltas_ms) > 1 else None
    paired_range_ms = float(max(deltas_ms) - min(deltas_ms)) if len(deltas_ms) > 1 else (0.0 if deltas_ms else None)
    control_median_ms = float(statistics.median(control)) if control else 0.0
    control_median_seconds = control_median_ms / 1000.0 if control else None
    if control_median_ms > 0.0:
        noise_flag = noise_flag_from_paired(
            deltas_ms,
            control_median_ms,
            range_ratio=noise_range_ratio,
            stdev_ratio=noise_stdev_ratio,
            cv_threshold=noise_cv_threshold,
        )
    else:
        noise_flag = noise_flag_from_deltas(
            deltas_ms,
            range_threshold_ms=noise_range_threshold_ms,
            stdev_threshold_ms=noise_stdev_threshold_ms,
            cv_threshold=noise_cv_threshold,
        )
    bootstrap_low_ms, bootstrap_high_ms = bootstrap_interval(
        deltas_ms,
        confidence=confidence,
        resamples=bootstrap_resamples,
        seed_parts=(control, candidate, verdict_context, required_pairs, sample_floor_for_bundle_audit),
    )
    p_value = permutation_p_value(
        deltas_ms,
        observed_statistic=median_delta_ms or 0.0,
        resamples=permutation_resamples,
        seed_parts=(control, candidate, verdict_context, required_pairs, sample_floor_for_bundle_audit),
    )
    # SNR-native confidence tier (B redesign, Option A / CI-native, 2026-06-02).
    # delta_min_ms defaults to 0.5 % of control_median when not supplied.
    # This will become a human-frozen contract field when the unified scorecard (A) lands.
    _delta_min = (
        delta_min_ms
        if delta_min_ms is not None
        else (control_median_ms * 0.005 if control_median_ms > 0.0 else 0.0)
    )
    ct = confidence_tier(
        bootstrap_low_ms,
        bootstrap_high_ms,
        median_delta_ms,
        deltas_ms,
        p_value,
        delta_min_ms=_delta_min,
        decisive_k=decisive_k,
        sign_min=sign_min,
    )

    scorecard = TimingAdapter.scorecard(
        deltas_ms=deltas_ms,
        required_pairs=required_pairs,
        control_median_ms=control_median_ms,
        median_delta_ms=median_delta_ms,
        bootstrap_ci_low_ms=bootstrap_low_ms,
        bootstrap_ci_high_ms=bootstrap_high_ms,
        permutation_p_value_arg=p_value,
        paired_stdev_ms=paired_stdev_ms,
        paired_range_ms=paired_range_ms,
        noise_flag=noise_flag,
        confidence_tier_result=ct,
        build_pass=build_pass,
        compare_pass=compare_pass,
        change_class=change_class,
    )
    scorecard_verdict = judge_scorecard(scorecard, replicated=replicated, decisive_k=decisive_k)
    verdict = scorecard_verdict.verdict
    reason = scorecard_verdict.reason

    # Detectability floor (2.14). Computed from the same paired stdev / depth the
    # gate already has; additive context, does not influence the verdict above.
    mde_ms = min_detectable_effect_ms(paired_stdev_ms, pair_count, delta_min_ms=_delta_min)
    power_at_observed_effect = power_at_effect_ms(
        median_delta_ms, paired_stdev_ms, pair_count, delta_min_ms=_delta_min
    )

    return PairedTimingEvidence(
        control_samples_ms=control,
        candidate_samples_ms=candidate,
        paired_deltas_ms=deltas_ms,
        verdict=verdict,
        reason=reason,
        noise_flag=noise_flag,
        required_pairs=required_pairs,
        paired_sample_count=pair_count,
        control_sample_count=len(control),
        candidate_sample_count=len(candidate),
        control_median_ms=control_median_ms if control else None,
        control_median_seconds=control_median_seconds,
        median_delta_ms=median_delta_ms,
        bootstrap_ci_low_ms=bootstrap_low_ms,
        bootstrap_ci_high_ms=bootstrap_high_ms,
        permutation_p_value=p_value,
        paired_stdev_ms=paired_stdev_ms,
        paired_range_ms=paired_range_ms,
        paired_mean_ms=paired_mean_ms,
        confidence_tier_name=ct.tier,
        confidence_margin_ms=ct.margin,
        confidence_ci_width_ms=ct.ci_width,
        confidence_decisiveness=ct.decisiveness,
        confidence_sign_consistency=ct.sign_consistency,
        delta_min_ms_used=_delta_min,
        decisive_k_used=decisive_k,
        sign_min_used=sign_min,
        escalation_steps_used=DEFAULT_SAMPLE_ESCALATION_STEPS,
        control_stdev_ms=paired_stdev_ms if control else None,
        control_range_ms=paired_range_ms if control else None,
        mde_ms=mde_ms,
        power_at_observed_effect=power_at_observed_effect,
    )


def naive_k1_counterfactual(paired_deltas_ms: list[float]) -> tuple[float, bool]:
    """Return (first_delta_ms, would_accept) for the naive single-sample rule.

    Naive-k1: sample the first paired delta; accept iff delta > 0.
    Stored as an explicit evidence field so paper ablations do not need
    to recompute from the raw vector.
    """
    if not paired_deltas_ms:
        return (0.0, False)
    first = float(paired_deltas_ms[0])
    return (first, first > 0)


def _weather_bucket(stdev_ms: float, range_ms: float) -> str:
    """Classify a run into a coarse weather bucket from paired-delta noise stats."""
    if stdev_ms <= WEATHER_BUCKET_STDEV_THRESHOLD_MS and range_ms <= WEATHER_BUCKET_RANGE_THRESHOLD_MS:
        return "calm"
    return "noisy"


def independence_verified(audit_a: dict, audit_b: dict) -> bool:
    """Return True iff two timing audit dicts represent independent runs.

    Requires ALL of:
    - Both dicts have 'recorded_at', 'paired_stdev_ms', 'paired_range_ms'.
    - Time gap between records >= INDEPENDENCE_MIN_GAP_SECONDS (30 min).
    - The two records fall in *different* weather buckets (same-condition
      back-to-back runs do not count as independent replication).

    Missing or unparseable metadata → False (conservative).
    """
    try:
        ts_a = datetime.datetime.fromisoformat(audit_a["recorded_at"].replace("Z", "+00:00"))
        ts_b = datetime.datetime.fromisoformat(audit_b["recorded_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return False
    if abs((ts_b - ts_a).total_seconds()) < INDEPENDENCE_MIN_GAP_SECONDS:
        return False
    try:
        bucket_a = _weather_bucket(float(audit_a["paired_stdev_ms"]), float(audit_a["paired_range_ms"]))
        bucket_b = _weather_bucket(float(audit_b["paired_stdev_ms"]), float(audit_b["paired_range_ms"]))
    except (KeyError, ValueError):
        return False
    return bucket_a != bucket_b


def replication_verified_from_audits(
    asserted: bool,
    *,
    prior_recorded_at: str = "",
    prior_stdev_ms: str = "",
    prior_range_ms: str = "",
    current_recorded_at: str = "",
    current_stdev_ms: str = "",
    current_range_ms: str = "",
) -> bool:
    """Verify a caller's replication assertion against prior/current audits."""

    if not asserted:
        return False
    prior = {
        "recorded_at": str(prior_recorded_at or ""),
        "paired_stdev_ms": str(prior_stdev_ms or ""),
        "paired_range_ms": str(prior_range_ms or ""),
    }
    current = {
        "recorded_at": str(current_recorded_at or ""),
        "paired_stdev_ms": str(current_stdev_ms or ""),
        "paired_range_ms": str(current_range_ms or ""),
    }
    return independence_verified(prior, current)


def evidence_fields(evidence: PairedTimingEvidence, *, change_class: str = "class_b", replicated: bool = False, host_id: str = "", env_class: str = "") -> dict[str, str]:
    _naive_first_ms, _naive_accept = naive_k1_counterfactual(evidence.paired_deltas_ms)
    return {
        "change_class": change_class,
        "replicated": "true" if replicated else "false",
        "timing_verdict": evidence.verdict,
        "timing_verdict_reason": evidence.reason,
        "timing_verdict_method": evidence.verdict_method,
        "control_sample_count": str(evidence.control_sample_count),
        "candidate_sample_count": str(evidence.candidate_sample_count),
        "paired_sample_count": str(evidence.paired_sample_count),
        "control_median_ms": _format_optional_ms(evidence.control_median_ms),
        "control_median_seconds": _format_optional_ms(evidence.control_median_seconds),
        "control_samples_ms": format_samples_ms(evidence.control_samples_ms),
        "candidate_samples_ms": format_samples_ms(evidence.candidate_samples_ms),
        "paired_deltas_ms": format_samples_ms(evidence.paired_deltas_ms),
        "paired_deltas_seconds": format_samples_ms([value / 1000.0 for value in evidence.paired_deltas_ms]),
        "median_delta_ms": _format_optional_ms(evidence.median_delta_ms),
        "median_delta_seconds": _format_optional_ms(None if evidence.median_delta_ms is None else evidence.median_delta_ms / 1000.0),
        "bootstrap_ci_low_ms": _format_optional_ms(evidence.bootstrap_ci_low_ms),
        "bootstrap_ci_high_ms": _format_optional_ms(evidence.bootstrap_ci_high_ms),
        "bootstrap_ci_low_seconds": _format_optional_ms(
            None if evidence.bootstrap_ci_low_ms is None else evidence.bootstrap_ci_low_ms / 1000.0
        ),
        "bootstrap_ci_high_seconds": _format_optional_ms(
            None if evidence.bootstrap_ci_high_ms is None else evidence.bootstrap_ci_high_ms / 1000.0
        ),
        "permutation_p_value": "" if evidence.permutation_p_value is None else f"{evidence.permutation_p_value:.6f}",
        "paired_stdev_ms": _format_optional_ms(evidence.paired_stdev_ms),
        "paired_range_ms": _format_optional_ms(evidence.paired_range_ms),
        "paired_mean_ms": _format_optional_ms(evidence.paired_mean_ms),
        "noise_flag": evidence.noise_flag,
        # Confidence tier fields (B redesign, 2026-06-02).
        "confidence_tier": evidence.confidence_tier_name or "",
        "confidence_margin_ms": _format_optional_ms(evidence.confidence_margin_ms),
        "confidence_ci_width_ms": _format_optional_ms(evidence.confidence_ci_width_ms),
        "confidence_decisiveness": (
            "" if evidence.confidence_decisiveness is None
            else f"{evidence.confidence_decisiveness:.4f}"
        ),
        "confidence_sign_consistency": (
            "" if evidence.confidence_sign_consistency is None
            else f"{evidence.confidence_sign_consistency:.4f}"
        ),
        # G2 — decision constants (paper §A.4 auditability).
        "delta_min_ms_used": _format_optional_ms(evidence.delta_min_ms_used),
        "decisive_k": "" if evidence.decisive_k_used is None else f"{evidence.decisive_k_used:.3f}",
        "sign_min": "" if evidence.sign_min_used is None else f"{evidence.sign_min_used:.3f}",
        "escalation_steps": ",".join(str(s) for s in evidence.escalation_steps_used),
        # G3 — naive-k1 counterfactual.
        "naive_k1_first_delta_ms": f"{_naive_first_ms:.3f}",
        "naive_k1_would_accept": "true" if _naive_accept else "false",
        # G4 — environment fingerprint.
        "host_id": host_id or _safe_hostname(),
        "env_class": env_class or "",
        "control_stdev_ms": _format_optional_ms(evidence.control_stdev_ms),
        "control_range_ms": _format_optional_ms(evidence.control_range_ms),
        # Detectability floor (2.14) — additive, verdict-neutral.
        "mde_ms": _format_optional_ms(evidence.mde_ms),
        "power_at_observed_effect": (
            "" if evidence.power_at_observed_effect is None
            else f"{evidence.power_at_observed_effect:.4f}"
        ),
    }


def _safe_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return ""


def validate_class_a(
    *,
    hypothesis: str = "",
    change_notes: str = "",
    touched_files: list[str] | None = None,
    candidate_id: str = "",
) -> tuple[bool, str]:
    """Hard whitelist for Class A validation.

    Returns (is_valid, reason).  The caller should force class_b when this
    function returns ``False`` regardless of what was originally specified.

    At least one *allowed* pattern must be present, and none of the
    *forbidden* patterns may appear.
    """
    combined = (
        hypothesis
        + " "
        + change_notes
        + " "
        + " ".join(touched_files or [])
        + " "
        + candidate_id
    ).lower()

    # -- allowed patterns (at least one must match) --
    allowed = [
        "dead store",
        "unused assignment",
        "removes dead",
        "unused parameter",
        "unused temporary",
        "replace copy with existing",
        "replace with existing",
        "already computed",
        "already done elsewhere",
        "redundant computation",
        "eliminates unused",
        "removes unused",
        "pure removal",
    ]

    # -- forbidden patterns (NONE must match) --
    forbidden = [
        "new cache",
        "thread_local",
        "static local",
        "new branch",
        "new conditional",
        "adds branch",
        "new state variable",
        "new state",
        "container type",
        "replace type",
        "swap container",
        "aggregation reorder",
        "path reorder",
        "multi-user",
        "shared buffer payload reuse",
    ]

    has_allowed = any(pattern in combined for pattern in allowed)
    matched_forbidden = [pattern for pattern in forbidden if pattern in combined]

    if matched_forbidden:
        return False, (
            "Class A rejected: forbidden pattern(s) found: "
            + ", ".join(matched_forbidden)
        )
    if not has_allowed:
        return False, (
            "Class A rejected: no allowed pattern matched; "
            + "change is not a pure dead-store / unused-value removal"
        )
    return True, "valid Class A: allowed pattern matched, no forbidden patterns found"
