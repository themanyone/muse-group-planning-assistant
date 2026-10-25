# Run it — from the ZIP, cold

*Everything here runs natively: Python via `uv`, the server
under uvicorn. No Docker, anywhere, ever (the project's D-24).
No network and no API key are needed for the demo: the default
AI driver is the offline mock, and the demo's recorded paths
run on the fixture driver.*

## From the release ZIP

```sh
unzip gpa-backend-v0.5-<date>.zip
cd gpa-backend
uv sync --extra dev        # install the exact locked environment
./demo.sh                  # seed the demo story, then serve
```

The server answers on `http://127.0.0.1:8000`; `GET /health`
is the first knock. `./demo.sh --seed-only` seeds without
serving (the freeze drill uses exactly this).

Demo logins — every demo person shares the password
`demo-password-123`:

| Who | Email | Role in the story |
|---|---|---|
| Mara | `mara@example.org` | Owner, Riverbend Community Trust (the nonprofit) |
| Ana | `ana@example.org` | Owner, Northside Hiking Club |
| Demo | `demo@example.org` | The B-010 demo group's user |

Seeding is idempotent: run it twice and the second run
creates nothing (a test pins this).

## Just the server, or just the tests

```sh
./run.sh            # serve (no seed)
./run.sh --demo     # seed the original demo group, then serve
./test.sh           # the full pytest suite + one-page report
uv run pytest -q    # the same suite, direct
```

## Configuration

Copy `.env.example` to `.env` and fill in what you need; every
setting carries the `GPA_` prefix and is documented inline in
that file. The dev defaults run the whole product offline. Two
refusals are deliberate: in `GPA_ENV=prod` the app will not
start with the development secret or a short one, and it says
why in plain words. Real secrets live in the environment only
— no `.env` has ever been committed to this repository, and no
release ZIP contains one (the freeze drill greps for exactly
that).

## The demo in four flows

These are the submission video's script. Each flow is first an
integration test (roadmap B-148: *if it cannot be tested, it
is not in the video*); the test names are the receipts.

- **Flow A — the flyer.** A photographed poster is read by the
  vision pipeline; the draft lands in the review queue with
  region provenance; a person approves; the event is published
  with the flyer attached and appears on the agenda.
  (`tests/test_phase5_gate.py`, fixture driver.)
- **Flow B — the argument.** The hiking club's 40-message trip
  argument is pasted in and extracted into 1 event, 3 tasks,
  2 decisions, and 2 questions; the reviewer corrects the place
  and confirms the volunteered kayak owner — by naming the
  member, because the AI never assigns an owner itself — and
  approval mints them all; the conversation continues in the
  minted event's thread. (`tests/test_phase7_gate.py`.)
- **Flow C — the newsletter.** The Trust's 12-section
  newsletter becomes exactly 12 drafts; re-extraction
  supersedes rather than doubles; a reviewer edits a date,
  approves, and the event lands on the agenda.
  (`tests/test_demo_flows.py`.)
- **Flow D — Saturday night.** The tonight view for the demo
  groups: the pinned event first, this evening's events in
  order, tomorrow's correctly excluded.
  (`tests/test_demo_flows.py`.)

Against the live server from `./demo.sh`, the same beats are
clickable by hand: sign in as Mara or Ana, open the review
queue, and the seeded story's drafts are waiting.

## Going further

- `DEPLOYMENT.md` (repo root) — the mail provider setup with
  SPF/DKIM/DMARC notes, and the **pre-submission checklist**:
  the checks that require real accounts and the project
  owner's word, recorded there as checklist items rather than
  run here.
- `PROJECT_MEMORY_MAP.md` (repo root) — the living map: every
  slice, every decision, every recorded deviation.
- `THREAT_MODEL.md` (repo root) — the security model, with
  each abuse case mapped to the test that carries it.
