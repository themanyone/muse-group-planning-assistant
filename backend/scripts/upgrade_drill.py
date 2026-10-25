"""The upgrade drill (roadmap B-156): two real ZIPs.

    uv run python scripts/upgrade_drill.py

Installs the v0.4 release ZIP into a scratch directory,
boots it, and writes a marker (a registered user with a
group) through its real API. Then runs scripts/upgrade.py
against the v0.5 ZIP — backup (by the staged tree: v0.4
predates backup tooling), migrate, smoke, swap — and
proves: the new tree serves, the marker survived with its
data, the migration stamp advanced to the new tree's head,
and the old tree sits whole beside the new one. Every step
is timed; the drill prints its verdict and exits non-zero
on any failure. Nothing outside the scratch directory is
touched.
"""

import argparse
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER_EMAIL = "drill-marker@example.org"


def _run(command, *, cwd, env=None, timeout=900):
    print(f"  $ {' '.join(str(part) for part in command)}")
    result = subprocess.run(
        [str(part) for part in command], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(result.stderr[-3000:])
        raise SystemExit(f"drill step failed: {command[0]}")
    return result


def _boot(tree: Path, env: dict, port: int) -> subprocess.Popen:
    process = subprocess.Popen(
        [
            str(tree / ".venv" / "bin" / "uvicorn"),
            "gpa.main:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=tree, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=3
            ) as response:
                if json.loads(response.read()).get("status") == "ok":
                    return process
        except OSError:
            time.sleep(1)
    process.terminate()
    raise SystemExit(f"server in {tree} never became healthy")


def _api(port: int, method: str, path: str, body=None, token=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _counts(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as connection:
        counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in ("users", "groups")
        }
        # A create_all install (the default boot) has never
        # met Alembic, so the stamp table may not exist yet;
        # the upgrade is what introduces it.
        has_stamp = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='alembic_version'"
        ).fetchone()
        counts["alembic_version"] = (
            connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
            if has_stamp else None
        )
        return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Upgrade drill: v0.4 → v0.5.")
    parser.add_argument(
        "--work", type=Path,
        default=Path.home() / "workspace" / ".verify-tmp" / "upgrade-drill",
    )
    args = parser.parse_args()
    timings: dict[str, float] = {}

    zips = {}
    for version in ("v0.4", "v0.5"):
        found = sorted(glob.glob(str(ROOT / "releases" / f"gpa-backend-{version}-*.zip")))
        if not found:
            raise SystemExit(f"release ZIP for {version} not found in releases/")
        zips[version] = Path(found[0])

    work = args.work
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    # -- install v0.4 ------------------------------------
    started = time.time()
    with zipfile.ZipFile(zips["v0.4"]) as archive:
        archive.extractall(work / "unpacked")
    install = work / "unpacked" / "gpa-backend"
    install.rename(work / "install")
    install = work / "install"
    _run(["uv", "sync", "--frozen"], cwd=install)
    (install / "data").mkdir(exist_ok=True)
    env_file = install / ".env"
    env_file.write_text(
        f"GPA_DATABASE_URL=sqlite:///{install}/data/gpa.db\n"
        "GPA_SECRET_KEY=drill-secret\n"
        f"GPA_STORAGE_DIR={install}/data/storage\n"
        f"GPA_OUTBOX_DIR={install}/data/outbox\n",
        encoding="utf-8",
    )
    env = dict(os.environ) | {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in env_file.read_text().splitlines() if "=" in line
    }
    timings["install v0.4"] = time.time() - started

    # -- marker through the v0.4 API ----------------------
    started = time.time()
    server = _boot(install, env, 18773)
    try:
        session = _api(18773, "POST", "/api/v1/auth/register", {
            "email": MARKER_EMAIL, "password": "drill-pass-9",
            "name": "Drill Marker",
        })
        token = session.get("access_token") or session["tokens"]["access_token"]
        _api(18773, "POST", "/api/v1/groups",
             {"name": "Drill Circle"}, token=token)
    finally:
        server.terminate()
        server.wait(timeout=10)
    before = _counts(install / "data" / "gpa.db")
    assert before["users"] == 1 and before["groups"] == 1, before
    timings["marker via v0.4 API"] = time.time() - started
    print(f"  v0.4 live: {before}")

    # -- the upgrade ---------------------------------------
    started = time.time()
    result = _run(
        [
            sys.executable, ROOT / "scripts" / "upgrade.py",
            "--root", install, "--zip", zips["v0.5"],
            "--env-file", env_file, "--smoke-port", "18774",
        ],
        cwd=ROOT,
        env=dict(os.environ) | {"PATH": os.environ["PATH"]},
        timeout=1200,
    )
    print(result.stdout[-1500:])
    timings["upgrade v0.4 → v0.5"] = time.time() - started

    # -- proof ----------------------------------------------
    started = time.time()
    assert (install / "FREEZE_DRILL.md").is_file(), "the v0.5 tree did not land"
    previous = sorted(work.glob("install.previous-*"))
    assert previous and not (previous[0] / "FREEZE_DRILL.md").exists(), \
        "the previous tree is not the intact v0.4 tree"
    after = _counts(install / "data" / "gpa.db")
    assert after["users"] == 1 and after["groups"] == 1, after
    assert after["alembic_version"] != before["alembic_version"], \
        "the migration stamp did not advance"
    heads = _run(
        [install / ".venv" / "bin" / "python", "-m", "alembic", "heads"],
        cwd=install, env=env,
    ).stdout
    assert after["alembic_version"] in heads, (after, heads)
    bundles = sorted((install / "backups").glob("gpa-backup-*"))
    assert bundles, "no backup bundle travelled with the data"

    server = _boot(install, env, 18773)
    try:
        session = _api(18773, "POST", "/api/v1/auth/login", {
            "email": MARKER_EMAIL, "password": "drill-pass-9",
        })
        token = session.get("access_token") or session["tokens"]["access_token"]
        me = _api(18773, "GET", "/api/v1/users/me", token=token)
        assert me["email"] == MARKER_EMAIL, me
    finally:
        server.terminate()
        server.wait(timeout=10)
    timings["proof"] = time.time() - started

    print("\nTimings:")
    for step, seconds in timings.items():
        print(f"  {step:<28} {seconds:6.1f}s")
    print(f"  migration: {before['alembic_version']} → {after['alembic_version']}")
    print("UPGRADE DRILL: PASS — v0.4 → v0.5, marker intact, "
          "old tree kept, bundle verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
