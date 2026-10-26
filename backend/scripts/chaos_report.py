"""Chaos report runner (roadmap B-175).

Runs the crash-correction harness end to end — the six
SIGKILL subprocess legs (one per roadmap flow) and the seeded
100-run in-process pass — and writes ``CHAOS_REPORT.md`` at
the repo root: the invariant report the roadmap asks to ride
in the release's test report. The same checks run as tests in
``tests/test_chaos.py``; this script is the operator's road
to the same verdicts, with the numbers written down.

    uv run python scripts/chaos_report.py [--runs 100]

Native only, like everything in this repo: the "process
profile" is a real subprocess killed with SIGKILL, the
"power-loss profile" is the same kill landing inside an open
transaction (SQLite's WAL + synchronous=FULL is the ground
that makes a power cut and a kill -9 the same event for
committed rows), and disk-full / read-only faults are injected
at the storage / engine boundary. No Docker, no VM.
"""

import argparse
import signal
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gpa.config import Settings  # noqa: E402
from gpa.db import Database  # noqa: E402
from gpa.services import chaos, recovery  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = REPO_ROOT / "tests" / "chaos_worker.py"
SCENARIOS = (
    "ingest_after_commit",
    "ingest_mid_transaction",
    "extraction_mid_job",
    "approval_mid_transaction",
    "upload_mid_session",
    "digest_mid_job",
)


def _run_leg(base: Path, scenario: str) -> tuple[bool, str]:
    """One subprocess leg: kill, recover, judge."""
    work = base / scenario
    work.mkdir(parents=True)
    db_file = work / "chaos.db"
    storage = work / "storage"
    marker = work / "marker.txt"
    result = subprocess.run(
        [sys.executable, str(WORKER), str(db_file), str(storage),
         str(marker), scenario],
        capture_output=True, text=True, timeout=120,
    )
    if not marker.exists():
        return False, f"worker never reached its kill point: {result.stderr[-400:]}"
    if result.returncode != -signal.SIGKILL:
        return False, f"worker exit was {result.returncode}, not SIGKILL"
    settings = Settings(
        env="test", database_url=f"sqlite:///{db_file}",
        secret_key="chaos-report-not-a-real-secret",
        storage_dir=storage, outbox_dir=work / "outbox",
    )
    db = Database(f"sqlite:///{db_file}")
    session = db.session()
    try:
        recovery.assess_and_repair(session, settings)
        verdict = chaos.invariant_report(session)
        return verdict["ok"], "; ".join(verdict["problems"]) or "invariant holds"
    finally:
        session.close()
        db.engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="gpa-chaos-") as tmp:
        base = Path(tmp)
        legs = {name: _run_leg(base / "legs", name) for name in SCENARIOS}
        report = chaos.run_inprocess_chaos(base / "loop", runs=args.runs, seed=175)

    legs_passed = sum(1 for ok, _ in legs.values() if ok)
    lines = [
        "# Chaos report — crash-correction harness (B-175)",
        "",
        f"Generated {date.today().isoformat()} by `scripts/chaos_report.py`.",
        "Invariant judged after every fault, on ground healed by",
        "startup recovery (B-172): no committed user entry is lost,",
        "no half-written entity is visible, and every interrupted",
        "job is resumed or visibly failed — never silently gone.",
        "",
        "## SIGKILL subprocess legs (process + power-loss profile)",
        "",
        f"{legs_passed}/{len(legs)} legs hold the invariant.",
        "",
    ]
    for name, (ok, note) in legs.items():
        lines.append(f"- **{name}**: {'PASS' if ok else 'FAIL'} — {note}")
    lines += [
        "",
        f"## In-process pass ({args.runs} runs, seed {report['seed']})",
        "",
        f"{report['passed']}/{report['runs']} runs hold the invariant.",
        "",
        "| Fault | Runs | Passed |",
        "|---|---|---|",
    ]
    for kind, counts in report["by_fault"].items():
        lines.append(f"| {kind} | {counts['runs']} | {counts['passed']} |")
    if report["failures"]:
        lines += ["", "## Failures", ""]
        for failure in report["failures"]:
            lines.append(f"- run {failure['run']} ({failure['fault']}): {failure['problems']}")
    lines.append("")
    out = REPO_ROOT / "CHAOS_REPORT.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"legs: {legs_passed}/{len(legs)} passed")
    print(f"in-process: {report['passed']}/{report['runs']} passed")
    print(f"report written to {out}")
    return 0 if legs_passed == len(legs) and report["passed"] == report["runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
