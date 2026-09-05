# Public fmt formal experiment evidence (derived package v2)

This directory is a **derived public representation** rebuilt from the frozen
study custody. It is not the original frozen artifact: the frozen v1 custody
directory (`publication/`) remains byte-identical and is preserved verbatim
(CRLF line endings). This v2 package is written with LF line endings and every
binding and inventory hash is computed over the actual committed bytes.

- `candidate_evidence.jsonl`: typed schema `fmt_public_candidate_evidence_v2`,
  31 rows (30 strict nulls plus the real `fmt_buffer_reuse`). Each row carries
  the pair-level observations, the typed projection of the frozen terminal
  ledger, and typed m4/m8/m12 stage judges. Native JSON types: numbers for
  elapsed times, CI bounds, p-values, medians and sample counts; booleans for
  `naive_k1_would_accept`/`correctness_pass`; strings for `verdict`,
  `candidate_id`, `oracle_label` and the ISO-8601 `recorded_at`.
- `frozen_judge_v1.py`: pure-standard-library extraction of the frozen paired
  timing judge. It is a decision-logic extraction, not the original execution
  script.
- `recompute_public.py`: self-contained replay (Python standard library plus
  this directory only). It replays every cumulative m4/m8/m12 judge, the typed
  ledger projection, the fixed-seed differential matrix and all aggregates.
- `judge_differential_cases.json`: 64 fixed-seed (`20260902`) synthetic cases
  covering positive/negative effects x none/low/medium/high noise x m4/m8/m12
  depths, with the original judge's expected output embedded per case.
- `study_binding.json`: provenance and custody hashes (frozen ledger SHA, v1
  custody JSONL SHA, v2 typed JSONL SHA, authorized runner SHA, original timing
  judge SHA, public judge SHA, recomputer SHA), the frozen calibration control
  median, the type-conversion rules and the documented CRLF/LF line-ending
  treatment.
- `execution_custody.json`: sanitized weather attempts with their recomputed
  jitter statistics, and derived-only post-processing recovery hashes.
- `independent_recompute.json`: aggregate rebuilt from pair-level evidence,
  including the preregistered counterfactual, the post-hoc exploratory
  numbers and the preflight/cross-session descriptive statistics.
- `judge_equivalence.json`: output of `recompute_public.py`: exact 31-candidate
  / 63-stage replay plus the 64-case random differential status.
- `publication_scan.json`: scanner contract, clean result, synthetic
  positive-control probes and the file inventory.
- `artifact_inventory.json`: byte length and SHA-256 for every other file.
- `method_result_report.md`: interpretation, limitations and recovery record.
- `external_consumer_acceptance.md`: machine-run external-consumer acceptance
  record (clean temporary directory, empty PYTHONPATH, audit-hook read
  sandbox, typed-JSON boolean proof, tamper fail-closed, inventory and
  sensitive scan).

Equivalence scope: the public judge exactly reproduces all 31 frozen candidates
and all 63 cumulative stage decisions in this package and matches the original
judge on the 64-case fixed-seed differential matrix. This is observed-input
equivalence; it is not proof that the extracted module and the operational
runner are identical over the full input domain. The operational runner and the
original judge are identified by hash in `study_binding.json`.

Positive deltas mean the candidate was faster. `accepted_noisy_single` is a
signal-only verdict and does not authorize automatic promotion. No candidate
was re-timed and none was removed from the frozen 30-null denominator.

## Result boundaries

- m4 is screening-only and has no terminal decision authority. In this run
  m4 triggered no failure or block; it acted only as preregistered screening
  and pair accumulation.
- Total sampling cost: 252 paired observations (four more pairs than
  always-m8, 120 fewer than always-m12). There is no preregistered fixed-depth
  cost comparator, so this run does not estimate net adaptive-sampling cost
  savings.
- All 31 candidates passed correctness; the run verifies the correctness
  contract was executed before timing, but correctness-gate rejection
  effectiveness for deliberately incorrect patches was not evaluated.
- No analytical Type-I error upper bound is claimed. `0/30` is public
  implementation calibration; the exact one-sided 95% Clopper-Pearson upper
  bound remains `9.503%`.
- Formal gain percentages use the run-internal paired control only. The
  cross-session calibration median (`532.686 ms`)
  and the formal run control median
  (`550.473 ms`, about
  `3.3%` higher)
  are descriptive environment evidence recorded in `execution_custody.json`
  and `independent_recompute.json`.
