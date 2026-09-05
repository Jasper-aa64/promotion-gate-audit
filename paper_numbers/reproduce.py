#!/usr/bin/env python3
"""Reproduce every number behind CLAIMS.md A1-A11, manuscript anchors,
and the two counterfactuals.

Pure stdlib, no network, deterministic output (no wall-clock, no absolute paths).

Inputs (public artifacts only; never reads evidence/ or raw candidate_result.json):
  publication_v2/candidate_evidence.jsonl       typed public evidence (31 candidates)
  publication_v2/independent_recompute.json    official recomputer output
  publication_v2/frozen_judge_v1.py            extracted judge decision logic
  publication_v2/study_binding.json            frozen study/workload metadata
  publication_v2/judge_equivalence.json        stage replay equivalence summary
  publication_v2/judge_differential_cases.json synthetic branch-coverage summary

Usage:  python reproduce.py            (from anywhere; paths resolve via __file__)
Exit code: 0 = all claims reproduced, 1 = at least one FAIL.
"""

import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.normpath(os.path.join(
    HERE, "..", "v2_fmt_public_formal_experiment", "publication_v2"))

FAILURES = []


def check(label, computed, expected, criterion, round_to=5):
    ok = True
    if isinstance(expected, (int, float)):
        ok = abs(computed - expected) <= 1e-9 if round_to is None else abs(
            round(computed, round_to) - round(expected, round_to)) <= 0
    else:
        ok = computed == expected
    status = "PASS" if ok else "FAIL"
    if not ok:
        FAILURES.append(label)
    print(f"[{status}] {label}: computed={computed!r} expected={expected!r}")
    print(f"        criterion: {criterion}")
    return ok


def load_rows():
    path = os.path.join(PUB, "candidate_evidence.jsonl")
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    assert len(rows) == 31, f"expected 31 rows, got {len(rows)}"
    return rows


def load_recompute():
    return load_public_json("independent_recompute.json")


def load_public_json(filename):
    with open(os.path.join(PUB, filename), encoding="utf-8") as f:
        return json.load(f)


def load_judge_source():
    with open(os.path.join(PUB, "frozen_judge_v1.py"), encoding="utf-8") as f:
        return f.read()


def num(v):
    return float(v) if isinstance(v, str) else v


def first_pair_delta(row):
    return min(row["pairs"], key=lambda p: p["pair_index"])["delta_ms"]


def binomial_tail_ge(n, k, p):
    """Exact P(X >= k) for X ~ Binomial(n, p), computed term by term."""
    return sum(math.comb(n, j) * p ** j * (1.0 - p) ** (n - j)
               for j in range(k, n + 1))


def main():
    print("=" * 70)
    print("fmt v0.1 preprint number reproduction (pure stdlib, deterministic)")
    print("inputs: publication_v2/{candidate_evidence.jsonl, "
          "independent_recompute.json, frozen_judge_v1.py, "
          "study_binding.json, judge_equivalence.json, "
          "judge_differential_cases.json}")
    print("=" * 70)

    rows = load_rows()
    ir = load_recompute()
    judge = load_judge_source()
    binding = load_public_json("study_binding.json")
    equivalence = load_public_json("judge_equivalence.json")
    differential = load_public_json("judge_differential_cases.json")
    nulls = [r for r in rows if r["oracle_label"] == "FALSE"]
    reals = [r for r in rows if r["oracle_label"] != "FALSE"]
    assert len(nulls) == 30 and len(reals) == 1

    # ---------------------------------------------------------------- P0
    print("\n--- P0 counterfactual definitions (verified from typed JSONL) ---")

    # P0a naive-k1: first pair delta_ms > 0 => accept
    naive_null = sum(first_pair_delta(r) > 0 for r in nulls)
    naive_real = sum(first_pair_delta(r) > 0 for r in reals)
    field_consistent = all(
        (first_pair_delta(r) > 0) == bool(r["naive_k1_would_accept"])
        for r in rows)
    check("P0a naive-k1 null accepts", naive_null, 16,
          "count of null candidates whose first pair delta_ms > 0; "
          "recomputed from pairs[0].delta_ms")
    check("P0a naive-k1 real accepts", naive_real, 0,
          "real first-pair delta is negative, so naive-k1 misses it")
    check("P0a naive-k1 all-candidate accepts", naive_null + naive_real, 16,
          "16/31 across all 31 candidates")
    check("P0a naive-k1 field consistency", field_consistent, True,
          "recomputed first-pair rule equals recorded naive_k1_would_accept "
          "for all 31 rows")
    check("P0a cross-check official recompute", num(ir["null_naive_false_promotions"]), 16,
          "independent_recompute.json null_naive_false_promotions")
    check("P0a cross-check all accepts", num(ir["all_candidate_naive_accepts"]), 16,
          "independent_recompute.json all_candidate_naive_accepts")
    check("P0a cross-check real naive", bool(ir["real_naive_accept"]), False,
          "independent_recompute.json real_naive_accept == False")

    # P0b bootstrap-only: final bootstrap 95% CI lower bound > 0 => accept
    boot_ids = sorted(r["candidate_id"] for r in nulls
                      if num(r["final_judge"]["bootstrap_ci_low_ms"]) > 0)
    boot_real = sum(num(r["final_judge"]["bootstrap_ci_low_ms"]) > 0
                    for r in reals)
    check("P0b bootstrap-only null accepts", len(boot_ids), 2,
          "null candidates with final_judge.bootstrap_ci_low_ms > 0")
    check("P0b bootstrap-only IDs", boot_ids, ["fmt-011", "fmt-016"],
          "the two false positives must be exactly fmt-011 and fmt-016")
    check("P0b bootstrap-only real accepts", boot_real, 1,
          "real candidate CI low bound is positive")
    check("P0b cross-check official count",
          num(ir["posthoc_bootstrap_only_false_promotions"]), 2,
          "independent_recompute.json posthoc_bootstrap_only_false_promotions")
    check("P0b cross-check official ids",
          sorted(ir["posthoc_bootstrap_only_false_promotion_ids"]),
          ["fmt-011", "fmt-016"],
          "independent_recompute.json ids")
    check("P0b cross-check official real accepts",
          num(ir["posthoc_bootstrap_only_real_accepts"]), 1,
          "independent_recompute.json posthoc_bootstrap_only_real_accepts")

    # P0c anchor: null verdict distribution
    from collections import Counter
    vd = Counter(r["verdict"] for r in nulls)
    check("P0c anchor null verdict distribution",
          dict(vd), {"rejected": 12, "NOISY_PENDING": 18},
          "zero accepted among the 30 nulls")

    # ---------------------------------------------------------------- A1
    print("\n--- A1 gate result and 95% one-sided upper bound ---")
    promoted = sum(r["verdict"] in
                   {"accepted", "accepted_noisy_single",
                    "accepted_noisy_replicated"} for r in nulls)
    check("A1 null gate promotions", promoted, 0,
          "count of promoted verdicts among 30 strict nulls")
    check("A1 cross-check official", num(ir["null_gate_false_promotions"]), 0,
          "independent_recompute.json null_gate_false_promotions")
    bound = 1.0 - 0.05 ** (1.0 / 30)
    check("A1 95% one-sided upper bound", bound, 0.09503,
          "1 - 0.05^(1/30); model: candidate-level results interpretable as "
          "independent Bernoulli draws with a common promotion probability "
          "(unconditional fact is only 0/30)")

    # ---------------------------------------------------------------- A2/A3
    print("\n--- A2 binary identity / A3 what is certified ---")
    null_cand = {r["candidate_binary_sha256"] for r in nulls}
    null_ctrl = {r["control_binary_sha256"] for r in nulls}
    same_pair = all(r["candidate_binary_sha256"] == r["control_binary_sha256"]
                    for r in nulls)
    check("A2 nulls: one single identical hash", len(null_cand), 1,
          "30/30 nulls share the same binary hash; expected prefix "
          "6e50bccc4189c8aa")
    check("A2 null hash prefix",
          next(iter(null_cand)).startswith("6e50bccc4189c8aa"), True,
          "the shared hash is 6e50bccc4189c8aa...")
    check("A2 nulls candidate==control", same_pair, True,
          "each null's candidate and control binaries are byte-identical")
    check("A2 real hash prefix",
          reals[0]["candidate_binary_sha256"].startswith("52d798dec09f688f"),
          True,
          "the single real candidate differs: 52d798dec09f688f...")
    check("A2 cross-check official usable nulls", num(ir["usable_null_count"]), 30,
          "independent_recompute.json usable_null_count")
    check("A3 inference", (len(null_cand) == 1 and same_pair
                           and reals[0]["candidate_binary_sha256"]
                           != next(iter(null_cand))), True,
          "A2 => the gate result certifies end-to-end pipeline behavior, not "
          "judge performance on semantically-equivalent-but-different binaries")

    # ---------------------------------------------------------------- A4
    print("\n--- A4 real candidate ---")
    real = reals[0]
    md = num(real["median_delta_ms"])
    cm = num(ir["formal_real_run_control_median_ms"])
    check("A4 real median delta (ms)", md, 35.861,
          "median_delta_ms of fmt_buffer_reuse")
    check("A4 formal run control median (ms)", cm, 550.473,
          "independent_recompute.json formal_real_run_control_median_ms")
    check("A4 relative effect", round(100.0 * md / cm, 3), 6.515,
          "median delta / control median")
    check("A4 verdict", real["verdict"], "accepted_noisy_single",
          "real candidate verdict")
    check("A4 tier", real["final_judge"]["confidence_tier"], "marginal",
          "final_judge.confidence_tier")
    check("A4 no automatic promotion", num(ir["real_automatic_promotion_count"]), 0,
          "independent_recompute.json real_automatic_promotion_count")

    # ---------------------------------------------------------------- A5
    print("\n--- A5 total measurement cost ---")
    null_pairs = sum(r["paired_sample_count"] for r in nulls)
    real_pairs = sum(r["paired_sample_count"] for r in reals)
    check("A5 null pairs", null_pairs, 244, "sum of paired_sample_count over 30 nulls")
    check("A5 real pairs", real_pairs, 8, "paired_sample_count of fmt_buffer_reuse")
    check("A5 total pairs", null_pairs + real_pairs, 252, "244 + 8")

    # ---------------------------------------------------------- manuscript anchors
    # These values appear in the manuscript but are provenance or descriptive
    # anchors rather than separate CLAIMS.md rows. Keep them executable so the
    # manuscript cannot silently drift from the public package.
    print("\n--- Manuscript supplementary anchors ---")
    final_judge = real["final_judge"]
    check("M1 real bootstrap CI low (ms)", num(final_judge["bootstrap_ci_low_ms"]),
          6.368, "real candidate final_judge.bootstrap_ci_low_ms")
    check("M2 real bootstrap CI high (ms)", num(final_judge["bootstrap_ci_high_ms"]),
          58.506, "real candidate final_judge.bootstrap_ci_high_ms")
    check("M3 real permutation p-value", num(final_judge["permutation_p_value"]),
          0.030985, "real candidate final_judge.permutation_p_value")
    check("M4 real sign consistency", num(final_judge["confidence_sign_consistency"]),
          0.875, "real candidate final_judge.confidence_sign_consistency")
    check("M5 real first paired delta (ms)", num(final_judge["naive_k1_first_delta_ms"]),
          -9.387, "real candidate first paired delta, rounded to 3 ms",
          round_to=3)

    details = {
        item["candidate_id"]: item
        for item in ir["bootstrap_only_false_promotion_details"]
    }
    check("M6 fmt-011 bootstrap-only CI low (ms)",
          num(details["fmt-011"]["bootstrap_ci_low_ms"]), 2.818,
          "independent_recompute.json bootstrap-only detail")
    check("M7 fmt-011 permutation p-value",
          num(details["fmt-011"]["permutation_p_value"]), 0.096952,
          "independent_recompute.json bootstrap-only detail")
    check("M8 fmt-016 bootstrap-only CI low (ms)",
          num(details["fmt-016"]["bootstrap_ci_low_ms"]), 2.573,
          "independent_recompute.json bootstrap-only detail")
    check("M9 fmt-016 permutation p-value",
          num(details["fmt-016"]["permutation_p_value"]), 0.112944,
          "independent_recompute.json bootstrap-only detail")

    preflights = {
        item["decision"]: item for item in ir["preflight_jitter_comparison"]
    }
    noisy = preflights["NOISY"]
    quiet = preflights["QUIET"]
    check("M10 NOISY preflight CV", num(noisy["control_cov"]), 0.1399,
          "independent_recompute.json preflight_jitter_comparison",
          round_to=4)
    check("M11 NOISY preflight relative range", num(noisy["control_range_ratio"]),
          0.3303, "independent_recompute.json preflight_jitter_comparison",
          round_to=4)
    check("M12 QUIET preflight CV", num(quiet["control_cov"]), 0.0116,
          "independent_recompute.json preflight_jitter_comparison",
          round_to=4)
    check("M13 QUIET preflight relative range", num(quiet["control_range_ratio"]),
          0.0282, "independent_recompute.json preflight_jitter_comparison",
          round_to=4)
    check("M14 calibration control median (ms)",
          num(ir["calibration_control_median_ms"]), 532.686,
          "independent_recompute.json calibration_control_median_ms",
          round_to=3)
    drift = 100.0 * (num(ir["formal_real_run_control_median_ms"]) -
                      num(ir["calibration_control_median_ms"])) / \
            num(ir["calibration_control_median_ms"])
    check("M15 calibration-to-formal control shift (%)", drift, 3.339,
          "derived from formal and calibration control medians", round_to=3)
    check("M16 workload operation count", num(binding["operation_count"]),
          1024000, "study_binding.json operation_count")
    check("M17 source commit", binding["source_commit"],
          "407c905e45ad75fc29bf0f9bb7c5c2fd3475976f",
          "study_binding.json source_commit")
    check("M18 terminal candidate rows", len(rows), 31,
          "candidate_evidence.jsonl row count")
    check("M19 m8 terminal candidates",
          num(equivalence["m8_terminal_candidate_count"]), 30,
          "judge_equivalence.json m8_terminal_candidate_count")
    check("M20 m12 terminal candidates",
          num(equivalence["m12_terminal_candidate_count"]), 1,
          "judge_equivalence.json m12_terminal_candidate_count")
    check("M21 cumulative stage judgments",
          num(equivalence["stage_judge_match_count"]), 63,
          "judge_equivalence.json stage_judge_match_count")
    check("M22 differential cases", num(differential["case_count"]), 64,
          "judge_differential_cases.json case_count")
    check("M23 always-m8 descriptive pair count", len(rows) * 8, 248,
          "31 candidates x 8 pairs")
    check("M24 current pairs minus always-m8", null_pairs + real_pairs - len(rows) * 8,
          4, "252 - 248")
    check("M25 always-m12 descriptive pair count", len(rows) * 12, 372,
          "31 candidates x 12 pairs")
    check("M26 always-m12 minus current pairs", len(rows) * 12 - null_pairs - real_pairs,
          120, "372 - 252")

    # ---------------------------------------------------------------- A6
    print("\n--- A6 judge source audit ---")
    consts = {}
    for name in ("SIGN_MIN", "DECISIVE_K", "SAMPLE_ESCALATION_STEPS",
                 "BOOTSTRAP_RESAMPLES", "PERMUTATION_RESAMPLES"):
        m = re.search(rf"^{name}\s*=\s*(.+)$", judge, re.M)
        consts[name] = m.group(1).strip() if m else "NOT FOUND"
    check("A6 SIGN_MIN", consts["SIGN_MIN"], "0.9", "frozen_judge_v1.py constant")
    check("A6 DECISIVE_K", consts["DECISIVE_K"], "1.0", "frozen_judge_v1.py constant")
    check("A6 SAMPLE_ESCALATION_STEPS", consts["SAMPLE_ESCALATION_STEPS"],
          "(4, 8, 12)", "frozen_judge_v1.py constant")
    check("A6 BOOTSTRAP_RESAMPLES", consts["BOOTSTRAP_RESAMPLES"], "2000",
          "frozen_judge_v1.py constant")
    check("A6 PERMUTATION_RESAMPLES", consts["PERMUTATION_RESAMPLES"], "2000",
          "frozen_judge_v1.py constant")
    conjunction = ("margin > 0.0" in judge
                   and "decisiveness >= DECISIVE_K" in judge
                   and "sign_consistency >= SIGN_MIN" in judge)
    check("A6 decisive tier conjoins CI + sign conditions", conjunction, True,
          "source contains margin>0 AND decisiveness>=K AND "
          "sign_consistency>=SIGN_MIN in the decisive branch")
    check("A6 nominal settings stated", (conjunction
                                         and consts["BOOTSTRAP_RESAMPLES"] == "2000"
                                         and consts["PERMUTATION_RESAMPLES"] == "2000"),
          True,
          "CI condition has nominal 95% setting and permutation nominal 0.05 "
          "setting; what is NOT calibrated is the joint authorization risk of "
          "the conjunction plus the sequential ladder plus replication")

    # ---------------------------------------------------------------- A7
    print("\n--- A7 certification sample requirement ---")
    check("A7 0.9^28", 0.9 ** 28, 0.05233, "0.9^28 > 0.05")
    check("A7 0.9^29", 0.9 ** 29, 0.04710, "0.9^29 <= 0.05")
    check("A7 ln(0.05)/ln(0.9)", math.log(0.05) / math.log(0.9), 28.4332,
          "analytic crossing point", round_to=4)
    check("A7 minimum n", math.ceil(math.log(0.05) / math.log(0.9)), 29,
          "smallest n such that an all-positive run certifies q>0.9 at 95%")
    check("A7 frozen ladder", consts["SAMPLE_ESCALATION_STEPS"], "(4, 8, 12)",
          "frozen escalation budget, below the n=29 requirement")

    # ---------------------------------------------------------------- A8
    print("\n--- A8 all-positive lower bounds ---")
    for n, exp in ((8, 0.688), (12, 0.779), (29, 0.902)):
        check(f"A8 0.05^(1/{n})", 0.05 ** (1.0 / n), exp,
              f"95% one-sided lower bound on q given n={n} all positive",
              round_to=3)

    # ---------------------------------------------------------------- A9
    print("\n--- A9 stage-level CI crossing approximation ---")
    z = 1.96
    z_dec = 3.0 * z
    check("A9 decisive z-score", z_dec, 5.88,
          "decisive geometry: margin >= ci_width => (delta-zs)-delta_min >= "
          "2zs => delta-delta_min >= 3*z*s ~ 5.88 SE")
    check("A9 marginal z-score", z, 1.96,
          "marginal geometry: ci_low > delta_min => delta-delta_min > 1.96 SE")
    check("A9 sample ratio", (z_dec / z) ** 2, 9.0,
          "(5.88/1.96)^2; a stage-level CI crossing approximation for "
          "single-stage conditions only - it excludes permutation, "
          "sign_consistency, the sequential ladder and replication, so it is "
          "NOT a promotion-path cost ratio")

    # ---------------------------------------------------------------- A10
    print("\n--- A10 arm_order / pair_index parity collinearity ---")
    def parity_bijection_exceptions(pairs):
        odd_pat = set()
        even_pat = set()
        exc = 0
        for p in pairs:
            pat = tuple(p["arm_order"])
            (odd_pat if p["pair_index"] % 2 else even_pat).add(pat)
        # bijection requires each parity maps to exactly one pattern
        modal_odd = next(iter(odd_pat)) if len(odd_pat) == 1 else None
        modal_even = next(iter(even_pat)) if len(even_pat) == 1 else None
        for p in pairs:
            pat = tuple(p["arm_order"])
            if p["pair_index"] % 2:
                exc += pat != modal_odd
            else:
                exc += pat != modal_even
        return exc, len(odd_pat), len(even_pat), odd_pat, even_pat

    npairs = [p for r in nulls for p in r["pairs"]]
    rpairs = [p for r in reals for p in r["pairs"]]
    exc_n, no, ne, odd_pat, even_pat = parity_bijection_exceptions(npairs)
    exc_r, _, _, _, _ = parity_bijection_exceptions(rpairs)
    check("A10 null pair exceptions", exc_n, 0,
          "0/244 null pairs violate the parity<->arm_order bijection")
    check("A10 null parity patterns",
          (len(odd_pat) == 1 and len(even_pat) == 1
           and odd_pat != even_pat), True,
          f"odd pairs all {odd_pat}, even pairs all {even_pat} "
          "=> arm order is a deterministic function of parity, so order "
          "effects are unidentifiable")
    check("A10 real pair exceptions", exc_r, 0,
          "0/8 for the real candidate (reported for completeness)")

    # ---------------------------------------------------------------- A11
    print("\n--- A11 exact one-sided binomial test sizes (sign test, q0=0.5) ---")
    print("     n : threshold c_n | actual size")
    sizes = {}
    for n in range(8, 61):
        c_n = next(k for k in range(n + 1)
                   if binomial_tail_ge(n, k, 0.5) <= 0.05)
        sizes[n] = binomial_tail_ge(n, c_n, 0.5)
        print(f"     {n:3d} : {c_n:2d} | {sizes[n]:.5f}")
    vals = list(sizes.values())
    check("A11 size range", (round(min(vals), 4), round(max(vals), 4)),
          (0.0107, 0.0494),
          "calibrated one-sided exact binomial test (q0=0.5), actual size "
          "across n=8..60")
    check("A11 size mean", round(sum(vals) / len(vals), 4), 0.0347,
          "mean actual size over n=8..60; sizes fluctuate below the 0.05 "
          "nominal level")

    # ---------------------------------------------------------------- done
    print("\n" + "=" * 70)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("RESULT: all claims reproduced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
