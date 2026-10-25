"""Native service packaging contracts (roadmap B-153).

The installer itself is rehearsed by running it (deploy/
DEPLOYMENT.md's rehearsal record); these tests pin the
packaging's shape so a later edit cannot quietly weaken
it: the dedicated user, the locked-down env file, the
backup timer, and — the D-24 law — no Docker anywhere.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def _read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_units_run_the_app_and_worker_as_the_service_user():
    api = _read("gpa.service.in")
    worker = _read("gpa-worker.service.in")
    for unit in (api, worker):
        assert "User=@USER@" in unit
        assert "EnvironmentFile=@CONFIG@/gpa.env" in unit
        assert "NoNewPrivileges=true" in unit
        assert "ProtectSystem=strict" in unit
    assert "@PREFIX@/.venv/bin/uvicorn" in api
    assert "gpa.main:create_app --factory" in api
    assert "--host 127.0.0.1" in api  # proxy-only posture
    assert "@PREFIX@/.venv/bin/gpa-worker" in worker
    # The units log to the journal — never to world-readable files.
    assert "StandardOutput=journal" in api


def test_backup_timer_is_daily_persistent_and_uses_default_bundles():
    timer = _read("gpa-backup.timer")
    assert "OnCalendar=" in timer
    assert "Persistent=true" in timer  # a slept-through night still backs up
    service = _read("gpa-backup.service.in")
    assert "scripts/backup.py" in service
    # No --out on the ExecStart line: backup.py names
    # timestamped bundles itself, and refuses to overwrite
    # an existing one. (The unit's header comment explains
    # the absence — the check is on the command, not prose.)
    exec_line = next(
        line for line in service.splitlines() if line.startswith("ExecStart=")
    )
    assert "--out" not in exec_line


def test_installer_is_plain_bash_and_honours_the_laws():
    script = _read("install.sh")
    assert script.startswith("#!/usr/bin/env bash")
    assert subprocess.run(
        ["bash", "-n", str(DEPLOY / "install.sh")]
    ).returncode == 0
    # The env file is born locked down, and an existing one
    # is never overwritten (rotation is deliberate, D-31).
    assert "chmod 0640" in script
    assert (
        "never overwritten" in script
        or "left alone" in script
        or "left untouched" in script
    )
    # Enable is not start: the operator starts it on purpose.
    # (The printed next-steps DO tell the operator the start
    # command — the check is that no executable line of the
    # script itself starts or restarts the service.)
    assert "systemctl enable" in script
    import re

    assert not re.search(r"^\s*systemctl\s+(start|restart)\b", script, re.M)
    # uv builds the venv from the lockfile, frozen.
    assert "uv sync --frozen" in script


def test_no_docker_anywhere_in_the_deployment_story():
    # The law is about USE: the deploy prose names the ban
    # itself ("no Docker, D-24"), which is documentation,
    # not usage. What must never appear is a Docker command,
    # a compose file, or a Dockerfile instruction.
    import re

    usage = re.compile(
        r"docker\s+(run|build|compose|pull|exec)\b|docker-compose|^FROM\s+\S+",
        re.M,
    )
    for path in DEPLOY.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not usage.search(text), f"Docker usage crept into {path.name}"
