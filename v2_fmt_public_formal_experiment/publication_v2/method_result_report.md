# fmt Public Formal Experiment: Result Report

## Executive result

The frozen public study completed all 31 preregistered candidates on the
authorized Linux host: 30 strict nulls and one real buffer-reuse candidate.
All candidates passed the frozen semantic correctness contract.

- Automatic false promotions among strict nulls: **0/30**.
- Exact one-sided 95% Clopper--Pearson upper bound: **9.503%**.
- Naive-k1 false promotions: **16/30**.
- Real candidate: **signal detected, but not automatically promoted**.

The real candidate finished as `accepted_noisy_single`: median paired gain
`35.861 ms` (about `6.51%` of the `550.473 ms` control median), bootstrap 95%
CI `[6.368, 58.506] ms`, permutation `p=0.030985`, sign consistency `0.875`.
Under the existing policy this is evidence-only and requires a separate
independent replication before promotion.

## Frozen protocol and custody

| Item | Frozen value |
|---|---|
| Target | fmt 12.1.0 |
| Source commit | `407c905e45ad75fc29bf0f9bb7c5c2fd3475976f` |
| Workload | 1,024,000 formatting operations |
| Strict-null denominator | 30 |
| Real candidate | `fmt_buffer_reuse` |
| Judge | `paired_bootstrap_permutation_v1` |
| Ladder | m4 screening; m8 first formal decision; m12 only by frozen escalation |
| Final manifest SHA-256 | `3c6e8bb3c13edc73368fedf481a5c0af228e9cfdc0e06323c659b3992b8e3c1f` |
| Authorization SHA-256 | `801f6d854d398769ed47cdacf475a52dc43e2044ce37b72b8fbb947d8ce6ab52` |
| Formal ledger SHA-256 | `f1599ac0af4fced42f0cee08a42d89f96a01211a49a0a062fa3a4979d4326afc` |

The first weather preflight was `NOISY` and stopped before any candidate
timing. The second preflight was `QUIET`; only that attempt entered the formal
run. The shared validation lock covered compilation, correctness, paired
timing, ledger finalization, and release. Candidates ran sequentially.

The repository evidence copy replaces five private-host metadata values and
two full lock-command strings with neutral placeholders. The original selected
evidence archive SHA is retained in `evidence/sanitization_manifest.json`;
candidate results, pair values, verdicts, and the formal ledger were not
modified.

## Preflight environment and cross-session migration

This section is descriptive environment evidence; it is not a confirmatory
experiment and no cross-session comparison is used to compute any gain.

Two weather preflights ran before any candidate timing. The first (`NOISY`)
was stopped by its own control-jitter thresholds; the second (`QUIET`) was the
only attempt that entered the formal run. The frozen jitter summaries
(`evidence/output/formal/preflight/attempt_01/host_jitter_summary.json` and
`attempt_02`) record, for the five control samples of each attempt, the
median, coefficient of variation (CV) and relative range:

| Attempt | Decision | Control median | CV | Relative range |
|---|---|---|---:|---:|
| 1 | `NOISY` | `651.534 ms` | `0.139901` | `0.330277` |
| 2 | `QUIET` | `547.875 ms` | `0.011564` | `0.028193` |

These three summary statistics are recomputed by the formal recomputer from
the frozen control samples and carried in `execution_custody.json` and
`independent_recompute.json`; the table above is not hand-copied.

The control-only calibration of the same wrapper measured a control median of
`532.686 ms` (frozen in the final execution manifest), while the formal run's
run-internal paired control median was `550.473 ms` (the real candidate's m8
judge over eight control samples). The session-to-session shift is `+3.339%`
(about 3.3%). This descriptive shift is one reason the formal design pairs
every candidate against a control measured inside the same run and the same
quiet window. All gain percentages in this report, including the real
candidate's `6.51%`, are computed against the run-internal paired control
median of `550.473 ms`, never against the calibration median or any other
cross-session anchor.

The pairing design and the exclusive use of the `QUIET` window bound the
applicability of this run to that window on that host. The `NOISY` preflight
was rejected before any candidate timing, so no cross-window comparison of
candidate timings exists and none is claimed.

## Null calibration

All 30 null patches had been frozen and machine-checked as strict nulls before
timing. Their executable preprocessed token streams were equivalent to the
control. In the formal run:

| Terminal outcome | Count |
|---|---:|
| Automatic promotion | 0 |
| `NOISY_PENDING` | 18 |
| `rejected` | 12 |
| Total strict nulls | 30 |

Thirty candidates terminated at m8. `fmt-011` alone followed the frozen gray
zone rule to m12 and still remained `NOISY_PENDING`. No null was removed from
the denominator, and no failed or inconvenient observation was discarded.

The naive-k1 counterfactual accepts a candidate whenever the first paired
delta is positive. It would have accepted 16 of the 30 strict nulls; across
all 31 candidates it accepted 16/31, because the real candidate's first paired
delta was negative and naive-k1 missed it. This is a preregistered,
deliberately weak counterfactual, not a competitive statistical baseline; it
shows why a single favorable timing observation cannot authorize promotion.

## Post-hoc exploratory sensitivity

To provide a conventional statistical comparison without new timing, a
bootstrap-only rule was applied after the experiment to the same frozen final
observations. This analysis was not preregistered and is exploratory. The rule
accepts when the bootstrap 95% CI lower bound is above zero; unlike the full
gate, it does not require the frozen minimum-practical-effect margin,
permutation evidence, or sign consistency.

| Rule | Status | Strict-null false promotions | Real-candidate result |
|---|---|---:|---|
| Naive-k1 | preregistered counterfactual | 16/30 | missed; first pair was negative |
| Bootstrap-only | post-hoc exploratory | 2/30 | accepted |
| Full frozen gate | preregistered formal rule | 0/30 | signal retained; no automatic promotion |

The two bootstrap-only false promotions were `fmt-011` and `fmt-016`. Their
final CI lower bounds were `2.818 ms` and `2.573 ms`, while their permutation
p-values were `0.096952` and `0.112944`; the permutation component therefore
blocked both from becoming statistically conclusive under the full rule. This
same-data comparison illustrates a risk/sensitivity tradeoff. It is not an
independent experiment or a population-level power estimate.

## Sampling cost and correctness coverage

No candidate terminated at m4 because m4 was frozen as screening-only and had
no terminal decision authority. m4 triggered no failure or block in this run;
it acted only as preregistered screening and pair accumulation. Thirty
candidates terminated at m8 and one strict null (`fmt-011`) escalated to m12,
for 252 paired observations in total.
There was no preregistered fixed-depth cost comparator, so this run does not
estimate net adaptive-sampling cost savings. Descriptively it used four more
pairs than always-m8 and 120 fewer than always-m12; those counterfactuals answer
different decision designs and are not a demonstrated efficiency gain.

All 31 candidates passed correctness. The run therefore verifies that the
correctness contract was executed before timing, but correctness-gate rejection
effectiveness was not evaluated. That question requires a separately frozen set
of deliberately incorrect candidates and must not be backfilled into this
strict-null denominator.

## Real-candidate interpretation

The real candidate reduced repeated output-buffer work. It passed correctness
and produced statistically positive evidence at m8, despite the first paired
observation being unfavorable (`-9.387 ms`). Therefore:

- `real_signal_detected_count = 1`;
- `real_automatic_promotion_count = 0`;
- naive-k1 would have missed this candidate on its first observation.

This result demonstrates both directions of the decision problem on one public
target: avoid promoting strict nulls while retaining a real signal. It does not
claim that the real patch is production-ready or universally beneficial.

## Independent recomputation

`independent_recompute.json` was rebuilt from all pair-level observations. The
recomputer does not import the formal runner. It reconstructs every frozen
verdict context, reruns every m4/m8/m12 judge, checks semantic digests and byte
counts, verifies the append-only ledger hash, and recomputes the aggregate.
The post-hoc numbers in this report are generated by this formal recomputer
from the frozen observations, never hand-copied.

Status: `INDEPENDENT_RECOMPUTE_EXACT_MATCH`.

Tampering with a pair causes the recomputation to fail closed.

## Public package v2 (derived representation)

`publication_v2/` is a derived public representation rebuilt from the frozen
custody. The frozen v1 custody directory (`publication/`) is preserved
byte-identical with its original CRLF line endings; the v2 package is written
with LF line endings, and every binding and inventory hash in v2 is computed
over the actual committed bytes. In the frozen v1 custody, the
`candidate_evidence_sha256` recorded in `study_binding.json` was computed over
LF in-memory text and therefore does not equal the raw-bytes hash of the
committed CRLF file; the v2 binding records both hashes and the exact treatment
of that discrepancy in its `line_ending_treatment` block.

The v2 candidate evidence uses the typed schema
`fmt_public_candidate_evidence_v2`: a lossless projection of the frozen
terminal ledger from string fields to native JSON types. Elapsed times, CI
bounds, p-values, medians and sample counts are native JSON numbers;
`naive_k1_would_accept` and correctness flags are native JSON booleans;
`verdict`, `candidate_id` and `oracle_label` stay strings, and `recorded_at`
stays an ISO-8601 string for microsecond-precision losslessness. The
conversion rules and the frozen ledger SHA, v1 custody JSONL SHA and v2 typed
JSONL SHA are recorded in `publication_v2/study_binding.json`. Per-candidate
semantic consistency between the frozen ledger and the typed projection is
machine-checked before any artifact is written.

The v2 package also contains a pure-standard-library extraction of the frozen
judge and a self-contained replay entrypoint. The public judge is a
decision-logic extraction, not the original execution script. It exactly
reproduces all 31 terminal candidates and all 63 cumulative stage decisions in
the public evidence, and it matches the original timing judge on a 64-case
fixed-seed (`20260902`) random differential matrix covering positive and
negative effects, none/low/medium/high noise, and m4/m8/m12 depths. This is
observed-input equivalence; it does not equal full input-domain code
equivalence, and the extraction is not claimed to be identical to the
operational runner over every possible input.

Custody and provenance hashes:

| Item | SHA-256 |
|---|---|
| Authorized execution runner | `43902a6eccf9921dc61043e6d6bec33d5d526a4f4a85fdbeb0028a354c55e289` |
| Original timing judge (`scripts/timing_analysis.py`) | `cc0c888779cf771cc9c31b8e7021b084d83061256ae7bf67ed4f3dbf334f4944` |
| Public frozen judge (`frozen_judge_v1.py`) | recorded in `publication_v2/study_binding.json` |
| Self-contained recomputer (`recompute_public.py`) | recorded in `publication_v2/study_binding.json` |

The v2 package is a derived representation of the frozen evidence; it is not
an original measurement artifact and it authorizes no further experiment.

## Post-processing incident

After all 31 terminal rows had been appended and the validation lock had been
released, the authorized command-line script raised `NameError` while creating
derived summary files. Its `__main__` entrypoint appeared before a later helper
definition. No candidate timing, correctness result, or ledger row was changed.

The same authorized runner bytes were imported after module definition to
recreate only `summary.json`, `run_state.json`, and `heartbeat.json`. The
recovery record binds the unchanged ledger SHA and explicitly states
`measurement_rows_modified=false`. A regression test now requires the CLI
entrypoint to be the final top-level statement. This correction is prospective;
the historical authorization remains bound to the actually executed runner.

## Scope and limitations

This is a candidate-level calibration on one public C++ target and one fixed
workload. `0/30` means no false promotion was observed; it does not mean the
true false-promotion probability is zero. The exact upper bound remains 9.503%.
This report does not claim an analytical Type-I error bound for the nested
m4/m8/m12 rule. Optional-look risk under explicit distribution and dependence
assumptions is a separate research question; the 30-null result is public
implementation calibration, not the source of a 5% guarantee.

The experiment does not establish batch-familywise error control, cross-project
generalization, or a universal performance-optimization benchmark. The real
candidate is a single functional control and ended as evidence-only. Broader
claims require independent targets and a separately authorized replication.

## Verification

- Affected regression set (runner, recomputer, preregistration capability,
  public preregistration design, control-only calibration): `103 passed`; with
  the publication scanner tests: `115 passed, 4 subtests passed`.
- Runner, independent recomputer, public frozen judge and self-contained public
  recomputer: Python syntax checks passed.
- Direct runner CLI help path executed successfully.
- The self-contained public recomputer replays the derived v2 package with an
  empty `PYTHONPATH` (standard library plus the package directory only) and
  reproduces the exact-match status, the 31/31 typed-ledger consistency and the
  64-case differential matrix.
- The external-consumer acceptance flow passed on a fresh copy of the package
  in a clean system temporary directory: empty `PYTHONPATH`, audit-hook read
  sandbox with zero reads outside the package and the standard library, typed
  JSON boolean proof (native `bool`, never the truthy string `"false"`), tamper
  fail-closed with a non-zero exit, and a clean inventory/sensitive scan. The
  machine-run record is `publication_v2/external_consumer_acceptance.md`.
- The publication scan returned zero findings and its synthetic private-host
  positive control triggered the expected rule. Raw logs, agent logs and
  private-host custody artifacts are excluded from the publication directories
  and Git; the original evidence tree is not committed.
- The frozen v1 custody directory remains byte-identical; only the separate
  derived `publication_v2/` representation is added.
- Full repository pytest discovery remains blocked by four unrelated historical
  paired-tail test modules whose optional `numpy` dependency is absent from this
  Windows environment. No dependency was installed or old study modified for
  this experiment.
