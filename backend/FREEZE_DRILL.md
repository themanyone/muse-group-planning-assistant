# Freeze drill log — ZIP-Final (roadmap B-151)

*The drill: build ZIP-Final, unzip it on a clean profile, build
with a fresh `uv sync` (never a copied venv), run the demo cold,
record the timings. This log ships inside the ZIP it describes
(at `gpa-backend/FREEZE_DRILL.md`), as B-151's acceptance
requires. It took four runs. Two of the failures were real
defects in what would have shipped; both were fixed at the root
and are recorded here, not tidied away.*

## The candidate that was drilled

- File: `releases/gpa-backend-v0.5-2026-10-25.zip`
- sha256: `609d0484b1b7251131f02564b7d4d71f290be78216f519d69a24c61f7718a2e0`
- Size: 1,193,367 bytes · 382 entries · built by
  `scripts/make_release_zip.py` from git
  `d951f81b9de7ae947f4713188bfa69a0ab62f07d` (its
  `release-manifest.json` names the same hash; slice range
  B-001 → B-151)
- Drill environment: a fresh directory on this machine, the
  ZIP unzipped as-is, `uv sync --extra dev` building a new
  virtualenv, the demo seeded with `./demo.sh --seed-only`,
  then the server booted (on port 8123, the drill's choice to
  avoid clashing with anything on the demo's default 8000) and
  the seeded story read back through the API.

**About the shipped ZIP vs the drilled candidate:** the shipped
ZIP-Final was rebuilt by the same script at the Phase 14 gate-
papers commit, so this log could be inside it. The rebuild
differs from the drilled candidate only in documentation —
the gate papers (TEST_REPORT / CHANGELOG / FRONTEND_HANDOFF /
PROJECT_MEMORY_MAP) and this log itself. No code, test, or
contract file differs; `SHA256SUMS.txt` and the two builds'
manifests let any reader verify that claim file by file.

## The four runs

### Run 1 — FAILED at the first step: the scripts were not executable

Candidate `81da87f8…` (tree `69908aa`). Unzip fine, sync fine —
then `./demo.sh: Permission denied`. The release script wrote
every entry with the archive-default mode 0644, so `demo.sh`,
`run.sh`, and `test.sh` landed non-executable: a cold reader
following the instructions hit a wall at command one. (The
server, started directly under uvicorn, booted and answered
`/health` — the product was fine; its packaging was not.)

**Defect, in the release script, shared by every train ZIP
before this one** — no earlier drill had unzipped a ZIP and
tried to run the demo from it. Fixed at the root (`53ad1c5`):
`_zip_info` carries each source file's Unix mode and mtime
into the archive, in both build shapes, and
`tests/test_release_zip.py` pins the mapping.

A plain admission belongs in this run's margin: the guard
test's first version was itself defective (it passed a 1970
timestamp, which the ZIP format refuses), and its commit
landed while the test was red because the committing chain
read pytest's exit code through a `tail` pipeline and masked
the failure. The test was fixed in `05ec22b`, and the gate
suite was re-run from the corrected tree rather than trusted
from the contaminated start. TEST_REPORT's Phase 14 addendum
carries the same account.

### Run 2 — FAILED at the seed: the database had no home

Candidate `501acbd7…` (tree `05ec22b`). Permissions verified
in-archive (0770 on all three entry scripts); the seed now ran
— and died: `sqlite3.OperationalError: unable to open database
file`. A cold tree has no `data/` directory (the ZIP never
carries runtime data), SQLite refuses a file whose parent is
missing, and the `Database` chassis never created the parent.
The server had been silently rescued all along by storage and
outbox creation making `data/` as a side effect; the seed
script, meeting the database first, had no such luck.

**Defect, in B-003's chassis** (`src/gpa/db.py`). Fixed at the
root (`d951f81`): `Database._ensure_sqlite_parent_dir` creates
the file's parent directory when the engine is built — the one
place that opens the database guarantees its home exists —
with a regression test in `tests/test_foundation.py` on a path
whose parent does not exist.

### Run 3 — the drill's own error, not the artifact's

Candidate `609d0484…` (tree `d951f81`). Seed green (7 users,
3 groups, 2 sources, 8 events, 1 announcement), boot +
`/health` green, login as Mara green — then the drill's
read-back step failed: it called `GET /api/v1/groups`, a route
that does not exist. There is deliberately no list-my-groups
route; a group is reached by its id, and a caller's own
footprint — including their groups — is `GET
/api/v1/users/me/data` (B-144). The drill script was corrected
to read back through that surface. No artifact change.

### Run 4 — GREEN, end to end (the same candidate)

| Step | Time | Result |
|---|---|---|
| Unzip | 0 s | 382 entries |
| `uv sync --extra dev` (fresh venv) | 2 s | exit 0 |
| `./demo.sh --seed-only` | 20 s | exit 0 — 7 users, 3 groups, 2 sources, 8 events, 1 announcement |
| Server boot + `GET /health` | 4 s | `{"status": "ok", "database": "ok"}` |
| Login (`mara@example.org`) | — | token issued |
| Read-back (`GET /users/me/data`) | — | Mara's groups: **Riverbend Community Trust** |

Total wall clock for the cold run: **27 seconds**
(2026-10-25 14:17:26 → 14:17:53 UTC).

## What the drill proved, and what it did not

Proved: the shipped artifact installs, seeds, serves, and
answers on a clean profile with no hand-holding — the exact
journey `submission/RUN.md` promises a cold reader. Not
proved here, and not claimed: the full test suite inside the
drilled tree (that is the B-146 restore drill's pattern, run
at the Phase 13 gate; this gate's suite ran in the main tree —
see TEST_REPORT's Phase 14 addendum), and any live third-party
behaviour (the standing boundaries in
`submission/WAIVER_AND_SCOPE.md` apply unchanged).
