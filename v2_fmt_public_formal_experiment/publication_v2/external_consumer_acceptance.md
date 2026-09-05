# External consumer acceptance record

This record documents the machine-run external-consumer acceptance
of this derived public package. The acceptance flow is re-executed
on every publication bundle build against a fresh copy of the
package placed in a clean directory under the system temporary
directory; every command below runs inside that copy. The record
contains no wall-clock timestamp and no absolute host paths, so the
derived package remains deterministic.

## Setup

1. The complete package content is copied to
   `<acceptance-temp-dir>/package`.
2. Every subprocess runs with an empty `PYTHONPATH` and a working
   directory of `<acceptance-temp-dir>/scratch` (an empty directory,
   not the repository).
3. The self-contained recomputer runs under a Python audit hook that
   records every file open. The recorded paths are classified
   against three allowed roots: `<acceptance-temp-dir>`,
   `<acceptance-temp-dir>/package`, and the Python standard
   library. Any other read fails the acceptance.

## Clean recompute

The audit-hook driver runs the same `recompute_package` entrypoint;
the equivalent direct command is:

    python <acceptance-temp-dir>/package/recompute_public.py --package <acceptance-temp-dir>/package

Exit code: `0`, status:
`PUBLIC_RECOMPUTE_EXACT_MATCH`.

| Check | Accepted value | Observed value |
|---|---|---|
| Strict-null false promotions (full gate) | 0/30 | `0/30` |
| Strict-null false promotions (naive-k1) | 16/30 | `16/30` |
| Strict-null false promotions (bootstrap-only) | 2/30 | `2/30` |
| Real candidate typed verdict | accepted_noisy_single | `accepted_noisy_single` |
| Total paired observations | 252 | `252` |
| Correctness passes | 31 | `31` |
| Artifact inventory files | 11 | `11` |

## Typed JSON boolean proof

Every typed row was checked field-by-field:

- boolean fields `correctness_pass, naive_k1_would_accept, null_far_denominator_eligible` are native
  Python `bool` values in all 31 rows, never strings;
- `naive_k1_would_accept` is `True` in 16
  rows and `False` in 15 rows.

Contrast with the v1 string representation:

- `bool("false")` is `True` in
  Python: the v1 string `"false"` is a non-empty string and is
  therefore truthy;
- `bool(False) is False` evaluates to
  `True`: the v2 native `false` is
  falsy, never truthy.

So a consumer testing `if row["naive_k1_would_accept"]:` would
misread every v1 `"false"` string as accepted, while the v2 native
booleans are read correctly.

## Read sandbox

- total file opens recorded by the audit hook:
  `35`;
- opens inside the allowed roots: `35`;
- opens outside the publication package (other than the Python
  standard library): `0`;
- opens inside the repository: `0`;
- opens inside Python site-packages (excluded from the allowed
  standard-library root): `0`.

All recorded file opens were inside this package, the acceptance
scratch directory or the Python standard library; the recomputer
never read the repository or any other external file.

## Tamper fail-closed

One digit of the numeric field `control_ms` of the first pair of
`fmt-001` was altered in a scratch copy (same byte length, different
bytes). The self-contained recompute then exited with code
`2` and the terminal error:

    PublicRecomputeError: inventory hash mismatch: candidate_evidence.jsonl

A fresh clean copy re-run after the tamper test exited with code
`0` and status `PUBLIC_RECOMPUTE_EXACT_MATCH`,
showing the package itself was never modified by the tamper test.

## Inventory and sensitive scan

- artifact inventory hash/size re-check on the acceptance copy:
  `11/11`
  files matched;
- publication sensitive scan findings:
  `0`.

## Determinism

The acceptance is re-run on every bundle build; all recorded values
are machine outputs (exit codes, recomputed aggregates, hashes), not
manual copies. No wall-clock timestamp or absolute host path is
recorded.
