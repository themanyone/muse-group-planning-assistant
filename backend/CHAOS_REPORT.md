# Chaos report — crash-correction harness (B-175)

Generated 2026-10-25 by `scripts/chaos_report.py`.
Invariant judged after every fault, on ground healed by
startup recovery (B-172): no committed user entry is lost,
no half-written entity is visible, and every interrupted
job is resumed or visibly failed — never silently gone.

## SIGKILL subprocess legs (process + power-loss profile)

6/6 legs hold the invariant.

- **ingest_after_commit**: PASS — invariant holds
- **ingest_mid_transaction**: PASS — invariant holds
- **extraction_mid_job**: PASS — invariant holds
- **approval_mid_transaction**: PASS — invariant holds
- **upload_mid_session**: PASS — invariant holds
- **digest_mid_job**: PASS — invariant holds

## In-process pass (100 runs, seed 175)

100/100 runs hold the invariant.

| Fault | Runs | Passed |
|---|---|---|
| commit | 18 | 18 |
| rollback | 17 | 17 |
| job | 17 | 17 |
| diskfull | 19 | 19 |
| readonly | 29 | 29 |
