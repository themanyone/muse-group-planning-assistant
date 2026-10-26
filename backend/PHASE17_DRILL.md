# Phase 17 drill log — the addendum proof (roadmap B-180)

*Run 2026-10-25 by the timer forge crew. This log ships inside
the release ZIP built at this gate, as the roadmap requires.
Every number below is read from that run's own log file, never
from a summary.*

## The drill (`tests/test_phase17_gate.py`, 1 test, all legs)

One unbroken story on a fresh deployment with encrypted storage
(B-168) enabled:

1. **Flyer in over a resumable upload.** A poster PNG arrives
   through the B-171 chunked-upload road in three parts, sent
   out of order (2, 0) on one app instance. That instance is
   then closed — the in-process stand-in for the kill (the true
   SIGKILL legs are B-175's subprocess harness, in the suite) —
   and a second instance over the same directories reports
   `parts_missing == [1]`, accepts the missing part, and
   completes: the whole-file SHA-256 verifies and the bytes
   ingest as an image source with its flyer.
2. **Process killed mid-extraction.** A read is requested
   (fixture driver); a worker claims the vision job and never
   returns. The claim is aged past its 900 s lease the way time
   would age it, and the restarted instance's startup recovery
   (B-172) **requeues** the job — resumed, never silently gone.
   The job then runs to `done` and the candidate stands in the
   review queue. No committed data was lost: the flyer, its
   source, and the upload's hash all survived.
3. **Candidate approved over the internal encrypted API.** The
   approval is the person's API act; its cross-module effects
   flow through the B-162 internal API, and the audit stream
   carries the `internal.*` lines (B-165) from which the
   approval's story reconstructs. The minted event stands
   published with its flyer attached.
4. **Raw disk greps find no plaintext user data.** A distinctive
   token planted ONLY in the flyer's transcript, and a 32-byte
   slice of the poster's own PNG, are searched for in **every
   file the deployment wrote** — the SQLite database, its WAL,
   and the whole storage tree. Absent everywhere, while the API
   serves the transcript whole to its owner.
5. **Audit chain verifies.** B-170's link-walk verifier answers
   intact over everything the drill did.

## Defects the drill found — fixed at root in the gate commit

A gate that finds nothing is a gate nobody believes. This one
found two real defects, both in earlier phases' code:

1. **No flyer could ever arrive over a resumable upload.**
   `uploads.complete` handed every assembled file to the B-025
   text/PDF ingest, which honestly refuses PNG bytes — so the
   B-171 road could never complete an image, and B-180's first
   leg was impossible as specified. Completion now dispatches
   on the sniffed kind: image bytes go to the B-027 image
   ingest, everything else to B-025, exactly as the one-shot
   roads divide the work.
2. **`Flyer.transcript` rested in plaintext.** B-167 sealed the
   transcript's chunk twin but not the Flyer row's own copy —
   the drill's WAL grep caught the token there. The transcript
   now seals as its own field (`flyer.transcript`) with one
   opening funnel for every reader (wall, source detail, flyer
   views, group export); tombstone snapshots keep the sealed
   form, and a new architecture pin (flyers → sources) records
   the funnel's home with its reason.

## Gate totals

- Drill + durability + architecture + field-encryption batch:
  **14 passed**.
- Vision / flyer / tombstone / erasure / Phase 5 gate batch:
  **45 passed**.
- Full suite, SQLite: **1,026 passed / 7 skipped / 0 failed**
  (collected 1,033; run as two declared back-to-back halves
  on the final tree — 668 + 358 passed — after two single
  full runs died to environment resets mid-suite with zero
  failures to the cut; the 7 skips are the PostgreSQL parity
  tests standing down without an admin URL, by design).
- Full suite, PostgreSQL: **1,006 passed / 27 skipped /
  0 failed** in PG mode (collected 1,033), plus the parity
  file's **7/7 passed** against the live engine — **1,013
  passed on-engine**. The 27 skips are the named
  SQLite-bound file tests standing down in PG mode.
  1026 + 7 = 1006 + 27 = 1033: both engines reconcile.
- Contract: **201 paths / 229 schemas** (unchanged at this
  gate — the drill and its fixes add no routes).

## Named waivers (never silent)

- **Second OS: NOT RUN.** The roadmap asks the drill to pass
  on a second OS; this builder is Linux only and no Windows or
  macOS machine is reachable. The drill is platform-pure
  (pathlib, no OS branching — B-177's audit enforces it) and
  the B-177 CI matrix defines the identical suite on Windows
  and macOS. The waiver is recorded here, in the open.
- **Coverage floors** (promised at B-178 to be measured at
  this gate): a coverage-instrumented full run was not
  completed in this gate's window; floors remain unstated in
  QUALITY_PIPELINE.md rather than invented. The security and
  durability suites themselves are green in the totals above.
