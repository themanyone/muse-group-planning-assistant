#!/usr/bin/env python3
"""The restore drill (roadmap B-146) — backup is a promise;
this script is the promise KEPT, on a timer.

    uv run python scripts/restore_drill.py [--work DIR] [--log FILE]

The drill, end to end, timing every step:

1. PLANT   — boot the app (main tree) on a throwaway data
             directory and create marker data: an owner, a
             group, a pasted source carrying a unique marker,
             an uploaded file.
2. BACKUP  — scripts/backup.py takes a bundle of the planted
             instance.
3. COPY    — copy the repository source to a CLEAN directory
             (no .venv, no __pycache__ — a copied environment
             is a broken environment; the drill builds fresh).
4. RESTORE — scripts/restore.py verifies the bundle and
             places the data beside the copied tree.
5. BUILD   — a FRESH ``uv sync`` in the copied tree.
6. TEST    — the full test suite in the copied tree.
7. VERIFY  — boot the app from the COPIED tree against the
             RESTORED data and read the marker back through
             the real API (login, group, source chunks).

Every step's seconds land in the drill log; any failure exits
non-zero with the step named. Run it after any change to
backup/restore, and before calling a release done.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "DRILL-MARKER-7f3a9c"

PLANT_PY = '''
import sys
from pathlib import Path

root, data_dir = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / "src"))

from fastapi.testclient import TestClient

from gpa.config import Settings
from gpa.main import create_app

settings = Settings(
    env="test",
    database_url=f"sqlite:///{data_dir}/data.db",
    storage_dir=data_dir / "storage",
    secret_key="drill-secret-for-tests-only",
    auto_create_tables=True,
    auth_rate_per_minute=1000,
)
client = TestClient(create_app(settings))
owner = client.post(
    "/api/v1/auth/register",
    json={"email": "drill@example.org", "password": "correct-horse-9",
          "name": "Drill Owner"},
)
assert owner.status_code == 201, owner.text
headers = {"Authorization": f"Bearer {owner.json()['access_token']}"}
group = client.post(
    "/api/v1/groups", json={"name": "Restore Drill Hearth"}, headers=headers
)
assert group.status_code == 201, group.text
gid = group.json()["id"]
pasted = client.post(
    f"/api/v1/groups/{gid}/sources/paste",
    json={"title": "Drill chat",
          "text": "[21/10/2026, 23:02:00] Ana: MARKER_TEXT lantern parade, Friday 7pm"},
    headers=headers,
)
assert pasted.status_code == 201, pasted.text
uploaded = client.post(
    f"/api/v1/groups/{gid}/sources/upload",
    params={"filename": "drill.txt"},
    content=b"MARKER_TEXT file words",
    headers={**headers, "Content-Type": "application/octet-stream"},
)
assert uploaded.status_code == 201, uploaded.text
print(gid)
'''.replace("MARKER_TEXT", MARKER)

VERIFY_PY = '''
import sys
from pathlib import Path

root, data_dir = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / "src"))

from fastapi.testclient import TestClient

from gpa.config import Settings
from gpa.main import create_app

settings = Settings(
    env="test",
    database_url=f"sqlite:///{data_dir}/data.db",
    storage_dir=data_dir / "storage",
    secret_key="drill-secret-for-tests-only",
    auto_create_tables=True,
    auth_rate_per_minute=1000,
)
client = TestClient(create_app(settings))
login = client.post(
    "/api/v1/auth/login",
    json={"email": "drill@example.org", "password": "correct-horse-9"},
)
assert login.status_code == 200, login.text
headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
# There is deliberately no "list all groups" route; a member
# finds their groups through /users/me/data (B-144's surface)
# — the drill reads back through the same door a person uses.
me = client.get("/api/v1/users/me/data", headers=headers)
assert me.status_code == 200, me.text
(group,) = [
    g for g in me.json()["my_groups"]
    if g["group_name"] == "Restore Drill Hearth"
]
sources = client.get(
    f"/api/v1/groups/{group['group_id']}/sources", headers=headers
)
assert sources.status_code == 200, sources.text
assert len(sources.json()) == 2, sources.text
detail = client.get(
    f"/api/v1/groups/{group['group_id']}/sources/{sources.json()[0]['id']}",
    headers=headers,
)
assert "MARKER_TEXT" in detail.text, detail.text
print("marker data verified in the restored instance")
'''.replace("MARKER_TEXT", MARKER)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the backup/restore drill.")
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--skip-tests", action="store_true",
                        help="Skip step 6 (the copied tree's full suite)")
    args = parser.parse_args()

    import tempfile

    work = args.work or Path(tempfile.mkdtemp(prefix="gpa-drill-"))
    work.mkdir(parents=True, exist_ok=True)
    log_path = args.log or (work / "restore-drill.log")
    lines: list[str] = []

    def note(line: str) -> None:
        print(line, flush=True)
        lines.append(line)

    def run(step: str, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        started = time.monotonic()
        result = subprocess.run(
            command, capture_output=True, text=True, **kwargs
        )
        seconds = time.monotonic() - started
        if result.returncode != 0:
            note(f"STEP {step}: FAILED after {seconds:.1f}s")
            note(result.stdout[-3000:])
            note(result.stderr[-3000:])
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            raise SystemExit(1)
        note(f"STEP {step}: ok in {seconds:.1f}s")
        return result

    env = dict(os.environ)
    # Scrub the caller's own environment pointers: when the
    # drill itself runs under `uv run`, VIRTUAL_ENV names the
    # MAIN tree's venv, and a child `uv run` in the restored
    # tree would trip over the mismatch (found on the first
    # drill run — the restored suite imported no fastapi).
    # uv re-derives the right environment per directory.
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    # …and the main tree's venv bin comes off PATH for the
    # same reason: a child must never resolve `pytest` or
    # `python` from the tree being drilled AGAINST (the second
    # drill run's suite quietly ran the wrong interpreter).
    path_parts = [
        part for part in env.get("PATH", "").split(":")
        if part and not part.startswith(str(ROOT))
    ]
    env["PATH"] = ":".join(
        [f"{Path.home() / '.local' / 'bin'}", *path_parts]
    )
    uv = shutil.which("uv", path=env["PATH"]) or "uv"

    planted = work / "planted"
    planted.mkdir(exist_ok=True)
    (work / "plant.py").write_text(PLANT_PY, encoding="utf-8")
    (work / "verify.py").write_text(VERIFY_PY, encoding="utf-8")
    run("plant", [uv, "run", "python", str(work / "plant.py"), str(ROOT), str(planted)],
        cwd=ROOT, env=env)

    bundle = work / "bundle"
    backup_env = dict(
        env,
        GPA_DATABASE_URL=f"sqlite:///{planted}/data.db",
        GPA_STORAGE_DIR=str(planted / "storage"),
        GPA_SECRET_KEY="drill-secret-for-tests-only",
        GPA_ENV="test",
    )
    run("backup", [uv, "run", "python", "scripts/backup.py", "--out", str(bundle)],
        cwd=ROOT, env=backup_env)

    restored_app = work / "restored-app"
    started = time.monotonic()
    shutil.copytree(
        ROOT, restored_app,
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".git", "backups", "drill-runs",
            "*.pyc", ".forge-lock", "data",
        ),
    )
    note(f"STEP copy: ok in {time.monotonic() - started:.1f}s")

    restored_data = work / "restored-data"
    run("restore", [sys.executable, "scripts/restore.py", str(bundle), str(restored_data)],
        cwd=restored_app, env=env)

    # The project's documented install (memory map): the dev
    # extra carries pytest; a bare sync builds a runtime-only
    # environment and the suite step finds no runner.
    run("build (fresh uv sync)", [uv, "sync", "--extra", "dev"],
        cwd=restored_app, env=env)

    if not args.skip_tests:
        test_env = dict(
            env,
            PYTEST_ADDOPTS=f"--basetemp={work}/drill-basetemp",
        )
        result = run("test (full suite, restored tree)",
                     [uv, "run", "--extra", "dev", "pytest", "-q"],
                     cwd=restored_app, env=test_env)
        tail = (result.stdout or "").strip().splitlines()
        if tail:
            note(f"  suite tail: {tail[-1]}")

    result = run("verify (marker read-back)",
                 [uv, "run", "python", str(work / "verify.py"),
                  str(restored_app), str(restored_data)],
                 cwd=restored_app, env=env)
    note(f"  {result.stdout.strip()}")

    note(f"DRILL PASSED — workdir {work}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"drill log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
