"""List a group's public events with the GPA Python SDK.

    python examples/public_events.py http://127.0.0.1:8000 riverbend-community-trust

No key needed: public pages are public. Twelve lines of
program; the SDK is standard-library only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))
from gpa_sdk import GpaClient  # noqa: E402

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
slug = sys.argv[2] if len(sys.argv) > 2 else "riverbend-community-trust"
for event in GpaClient(base).public_events(slug):
    print(event["starts_at"], event["title"])
