#!/usr/bin/env python3
"""Welch t-test helper for the optimization evaluator.

The evaluator only needs a binary decision helper with enough numeric
precision for noisy wall-clock samples. This implementation has no third-party
dependency, so it can run on the remote CentOS environment once Python is
available.
"""

from __future__ import annotations

import argparse
import json
import math
from statistics import mean


def parse_samples(raw: str) -> list[float]:
    values = [float(part.strip()) for part in raw.split(",") if part.strip()]
    if len(values) < 2:
        raise ValueError("at least two samples are required")
    return values


def sample_variance(values: list[float]) -> float:
    avg = mean(values)
    return sum((value - avg) ** 2 for value in values) / (len(values) - 1)


def betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for incomplete beta, adapted from Numerical Recipes."""
    max_iterations = 200
    eps = 3.0e-12
    fpmin = 1.0e-300

    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d

    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            return h

    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    bt = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )

    if x < (a + 1.0) / (a + b + 2.0):
        return bt * betacf(a, b, x) / a
    return 1.0 - bt * betacf(b, a, 1.0 - x) / b


def student_t_cdf(t_value: float, df: float) -> float:
    if df <= 0.0:
        raise ValueError("degrees of freedom must be positive")

    x = df / (df + t_value * t_value)
    ibeta = regularized_incomplete_beta(df / 2.0, 0.5, x)
    if t_value >= 0:
        return 1.0 - 0.5 * ibeta
    return 0.5 * ibeta


def welch(candidate: list[float], baseline: list[float]) -> dict[str, float | int]:
    candidate_mean = mean(candidate)
    baseline_mean = mean(baseline)
    candidate_var = sample_variance(candidate)
    baseline_var = sample_variance(baseline)
    nx = len(candidate)
    ny = len(baseline)

    candidate_term = candidate_var / nx
    baseline_term = baseline_var / ny
    denominator = math.sqrt(candidate_term + baseline_term)
    if denominator == 0.0:
        t_statistic = 0.0 if candidate_mean == baseline_mean else math.inf
        degrees = math.inf
        p_two_tailed = 1.0 if candidate_mean == baseline_mean else 0.0
    else:
        t_statistic = (candidate_mean - baseline_mean) / denominator
        degrees = (candidate_term + baseline_term) ** 2 / (
            (candidate_term**2 / (nx - 1)) + (baseline_term**2 / (ny - 1))
        )
        cdf = student_t_cdf(t_statistic, degrees)
        p_two_tailed = 2.0 * min(cdf, 1.0 - cdf)

    return {
        "candidate_n": nx,
        "baseline_n": ny,
        "candidate_mean": round(candidate_mean, 6),
        "baseline_mean": round(baseline_mean, 6),
        "candidate_variance": round(candidate_var, 6),
        "baseline_variance": round(baseline_var, 6),
        "t_statistic": round(t_statistic, 6),
        "degrees_of_freedom": round(degrees, 6),
        "p_two_tailed": round(max(0.0, min(1.0, p_two_tailed)), 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()

    result = welch(parse_samples(args.candidate), parse_samples(args.baseline))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
