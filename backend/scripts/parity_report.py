"""Print the B-201 API parity report for the test report.

Usage: ``python scripts/parity_report.py`` from the repo root.
Builds the application on a throwaway in-memory database (the
register is checked against the LIVE route table, never a
stored snapshot), prints the report lines, and exits 1 when
parity fails -- so a release train can gate on it exactly as
it gates on the suite. No user data is read or written.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gpa.config import Settings  # noqa: E402
from gpa.main import create_app  # noqa: E402
from gpa.services.parity import format_parity_report, parity_report  # noqa: E402


def main() -> int:
    """Build the app, check parity, print the report."""
    app = create_app(
        Settings(env="dev", secret_key="parity-report-only" * 4,
                 database_url="sqlite://")
    )
    report = parity_report(app)
    print(format_parity_report(report))
    return 0 if report["parity_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
