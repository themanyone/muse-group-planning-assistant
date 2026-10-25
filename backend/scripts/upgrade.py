"""In-place upgrade (roadmap B-156).

    uv run python scripts/upgrade.py --zip releases/gpa-backend-vX.Y-DATE.zip \
        [--root /opt/gpa] [--env-file /etc/gpa/gpa.env]

The order is the roadmap's, and it is not negotiable:
**backup → migrate → suite smoke → serve**. Mechanically
the new tree is staged and built FIRST (pure addition —
nothing about the live deployment changes), because the
backup itself may need the new tree's tooling:

1. **Stage + build.** The new ZIP is unpacked beside the
   installation and given a FRESH virtualenv from its own
   lockfile (a copied venv is a broken venv — B-146).
2. **Backup.** A bundle of the live deployment (database +
   storage + manifest) is taken — by the current tree's own
   backup script when it has one, else by the staged tree's
   (an installation older than the backup tooling, like
   v0.4, has none; the bundle format is the same). The
   bundle's hashes are re-verified here. Nothing that
   changes the deployment happens before this exists: an
   upgrade that cannot prove its way back does not proceed.
   (The bundle covers SQLite deployments; a PostgreSQL
   deployment's backup is its platform's dump discipline,
   taken before running this script — DEPLOYMENT.md.)
3. **Migrate.** The staged tree's Alembic migrations run
   against the LIVE database. If this fails, nothing has
   been swapped; the way back is step 2's bundle.
4. **Smoke.** The staged server boots against the real
   (now migrated) deployment data on a scratch port and
   must answer /health with status ok before it may serve.
5. **Serve (the swap).** The old tree moves aside to
   ``<root>.previous-<stamp>``; the staged tree takes the
   root's path, carrying the deployment's data/, backups/
   and .env with it, and its venv is rebuilt in place — a
   venv is path-bound (its scripts name their interpreter
   by absolute path), so the serving venv must be born at
   its final address; the staged one was scaffolding for
   migrate + smoke. The script then prints the restart
   commands — restarting the service is the operator's
   deliberate act, not a script's side effect.

**Rollback is "restore the backup," stated plainly:** swap
the two directories back and place the bundle with
scripts/restore.py (the exact commands are printed at the
end of every run and live in DEPLOYMENT.md).

The tool ships with the backend from B-156 on. Upgrading
an installation older than that (the drill: v0.4 → v0.5)
runs this script from a checkout of the new code — the
--root flag exists for exactly that.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class UpgradeError(Exception):
    """A plain-words stop: the upgrade does not proceed."""


def _run(command: list[str], *, cwd: Path, env: dict | None = None) -> None:
    """Run one step, loudly. Any failure stops the upgrade."""
    print(f"  $ {' '.join(command)}  (in {cwd})")
    result = subprocess.run(command, cwd=cwd, env=env)
    if result.returncode != 0:
        raise UpgradeError(
            f"step failed with exit {result.returncode}: {command[0]} "
            "— the installation has NOT been swapped; see the "
            "rollback note if migrations already ran."
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_bundle(bundle: Path) -> None:
    """Re-check every hash in a backup bundle's manifest.

    The upgrade's rollback is only real if the bundle is —
    so the bundle proves itself here, the same check
    scripts/restore.py runs before placing anything.
    """
    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file():
        raise UpgradeError(f"backup bundle has no manifest: {bundle}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # The B-146 manifest shape: one database entry plus one
    # entry per storage file (paths relative to storage/).
    entries = []
    database = manifest.get("database") or {}
    if database.get("file"):
        entries.append((bundle / database["file"], database.get("sha256"),
                        database["file"]))
    for entry in manifest.get("storage_files", []):
        entries.append((bundle / "storage" / entry["path"],
                        entry.get("sha256"), entry["path"]))
    if not entries:
        raise UpgradeError("backup bundle's manifest names no files at all")
    for path, expected, label in entries:
        if not path.is_file():
            raise UpgradeError(f"backup bundle is missing {label}")
        if _sha256(path) != expected:
            raise UpgradeError(
                f"backup bundle file {label} fails its hash — "
                "the way back is not proven; refusing to upgrade."
            )


def read_env_file(path: Path | None) -> dict[str, str]:
    """Parse a GPA env file (KEY=VALUE lines) for subprocesses.

    Deliberately a parser, never a shell source: values are
    data, not code. Only GPA_* keys are carried over.
    """
    values: dict[str, str] = {}
    if path is None:
        return values
    if not path.is_file():
        raise UpgradeError(f"env file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().startswith("GPA_"):
            values[key.strip()] = value.strip()
    return values


def stage_zip(zip_path: Path, stage_dir: Path) -> Path:
    """Unpack a release ZIP; return the staged tree's root.

    The §8 anatomy puts the runnable tree under gpa-backend/
    inside the ZIP; the manifest must agree, or this is not
    a GPA release and the upgrade refuses it.
    """
    if not zip_path.is_file():
        raise UpgradeError(f"release ZIP not found: {zip_path}")
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if "release-manifest.json" not in names:
            raise UpgradeError(
                f"{zip_path.name} has no release-manifest.json — "
                "not a GPA release ZIP (see the roadmap, §8)."
            )
        manifest = json.loads(archive.read("release-manifest.json"))
        archive.extractall(stage_dir)
    staged = stage_dir / "gpa-backend"
    if not (staged / "pyproject.toml").is_file():
        raise UpgradeError("the ZIP's gpa-backend/ tree is missing pyproject.toml")
    print(f"  staged {manifest.get('version', '?')} "
          f"(git {manifest.get('git_hash', '?')[:9]}) at {staged}")
    return staged


def smoke(staged: Path, env: dict[str, str], *, port: int) -> None:
    """Boot the staged tree against the deployment and ask it
    the one question that matters: /health, status ok."""
    process_env = dict(os.environ) | env
    process = subprocess.Popen(
        [
            str(staged / ".venv" / "bin" / "uvicorn"),
            "gpa.main:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=staged,
        env=process_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if process.poll() is not None:
                raise UpgradeError(
                    "the staged server exited during smoke — "
                    "read its log by booting it by hand."
                )
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=3
                ) as response:
                    body = json.loads(response.read())
                if body.get("status") == "ok":
                    print(f"  smoke ok: /health on :{port} → status ok, "
                          f"database {body.get('database')}")
                    return
                raise UpgradeError(f"smoke /health answered {body!r}")
            except (OSError, ValueError):
                time.sleep(1)
        raise UpgradeError("the staged server never answered /health in 60s")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def upgrade(
    *,
    root: Path,
    zip_path: Path,
    env_file: Path | None,
    database_url: str,
    storage_dir: Path,
    smoke_port: int,
    log=print,
) -> dict:
    """Run the five steps. Returns a small report dict."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    env = read_env_file(env_file)
    env.setdefault("GPA_DATABASE_URL", database_url)
    env.setdefault("GPA_STORAGE_DIR", str(storage_dir))

    if not (root / "pyproject.toml").is_file():
        raise UpgradeError(f"{root} does not look like a GPA installation.")

    # 1 — Stage + build the new tree beside the installation.
    # Pure addition: nothing about the live deployment has
    # changed yet, and nothing that changes it happens until
    # the backup below exists and verifies.
    log("1/5 stage + build the new tree…")
    stage_dir = root.parent / f"{root.name}.staging-{stamp}"
    staged = stage_zip(zip_path, stage_dir)
    # --no-editable is load-bearing: an editable install is a
    # .pth file naming the STAGING path, and step 5 renames
    # that path away — the swapped-in tree's venv would point
    # at a directory that no longer exists and the service
    # would not boot. The upgrade drill (v0.4 → v0.5) caught
    # exactly this. A deployment venv must own its packages.
    _run(["uv", "sync", "--frozen", "--no-editable"], cwd=staged)

    # 2 — Backup, and proof. The runner is the CURRENT tree's
    # own backup script when it has one, run with its own
    # interpreter (its code imports against its own
    # environment). An installation older than the backup
    # tooling (v0.4 has no scripts/backup.py — the drill's
    # case) has none: the STAGED tree's script takes the
    # bundle instead. Same bundle format, same manifest —
    # restore.py reads any of them; what matters is that the
    # way back exists before anything mutates.
    log("2/5 backup (before anything mutates)…")
    bundle_parent = root / "backups"
    bundle_parent.mkdir(parents=True, exist_ok=True)
    bundle = bundle_parent / f"gpa-backup-{stamp}"
    if (root / "scripts" / "backup.py").is_file():
        runner_root, runner_python = root, root / ".venv" / "bin" / "python"
    else:
        runner_root, runner_python = staged, staged / ".venv" / "bin" / "python"
        log("  the current tree predates backup tooling; "
            "the new tree's backup script takes the bundle.")
    _run(
        [str(runner_python), "scripts/backup.py", "--out", str(bundle)],
        cwd=runner_root,
        env=dict(os.environ) | env,
    )
    verify_bundle(bundle)
    log(f"  bundle verified: {bundle}")

    # 3 — Migrate the LIVE database with the staged tree.
    log("3/5 migrate the live database (staged tree's Alembic)…")
    _run(
        [str(staged / ".venv" / "bin" / "python"), "-m", "alembic", "upgrade", "head"],
        cwd=staged,
        env=dict(os.environ) | env,
    )

    # 4 — Smoke the staged tree against the real data.
    log("4/5 smoke (staged server, real data, scratch port)…")
    smoke(staged, env, port=smoke_port)

    # 5 — The swap: data and configuration carry over; the old
    # tree stays whole, one rename away, as .previous-<stamp>.
    log("5/5 swap…")
    for carried in ("data", "backups"):
        source = root / carried
        if source.exists():
            shutil.copytree(source, staged / carried, dirs_exist_ok=True)
    if (root / ".env").exists() and not (staged / ".env").exists():
        shutil.copy2(root / ".env", staged / ".env")
    previous = root.parent / f"{root.name}.previous-{stamp}"
    root.rename(previous)
    try:
        staged.rename(root)
    except OSError:
        previous.rename(root)  # put the live tree back, then say so
        raise UpgradeError("the swap failed midway; the old tree was put back.")
    shutil.rmtree(stage_dir, ignore_errors=True)

    # The staged venv was born at the staging address — and a
    # venv is path-bound: its console scripts' shebangs name
    # the interpreter by absolute path, so after the rename
    # they point at a directory that no longer exists and
    # nothing in .venv/bin would run (the drill's final boot
    # caught this too). The serving venv is therefore rebuilt
    # in place, born at its final address; the staged venv
    # was always scaffolding for migrate + smoke.
    shutil.rmtree(root / ".venv", ignore_errors=True)
    _run(["uv", "sync", "--frozen", "--no-editable"], cwd=root)

    log("")
    log(f"Upgraded: {root} now runs the {zip_path.name} tree.")
    log(f"The previous tree is kept whole at {previous}.")
    log("Restart to serve it:")
    log("  systemctl restart gpa gpa-worker        (systemd installs)")
    log(f"  ./run.sh                                (hand-run trees, in {root})")
    log("")
    log("ROLLBACK, plainly: restore the backup —")
    log(f"  mv {root} {root.name}.failed-{stamp} && mv {previous} {root}")
    log(f"  uv run python scripts/restore.py {bundle} <scratch dir>, then point "
        "GPA_DATABASE_URL / GPA_STORAGE_DIR at the restored files (DEPLOYMENT.md).")
    return {"bundle": str(bundle), "previous": str(previous), "root": str(root)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upgrade a GPA installation in place (B-156)."
    )
    parser.add_argument("--zip", type=Path, required=True, help="The new release ZIP")
    parser.add_argument(
        "--root", type=Path, default=ROOT,
        help="The installation to upgrade (default: this script's own tree)",
    )
    parser.add_argument(
        "--env-file", type=Path, default=None,
        help="The deployment's GPA env file (B-153 layout: /etc/gpa/gpa.env)",
    )
    parser.add_argument(
        "--database-url", default=None,
        help="Override the database URL (default: env file, else <root>/data/gpa.db)",
    )
    parser.add_argument(
        "--smoke-port", type=int, default=18777,
        help="Scratch port for the smoke boot (default 18777)",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    database_url = (
        args.database_url
        or read_env_file(args.env_file).get("GPA_DATABASE_URL")
        or f"sqlite:///{root}/data/gpa.db"
    )
    storage_dir = Path(
        read_env_file(args.env_file).get("GPA_STORAGE_DIR", root / "data" / "storage")
    )
    try:
        upgrade(
            root=root,
            zip_path=args.zip,
            env_file=args.env_file,
            database_url=database_url,
            storage_dir=storage_dir,
            smoke_port=args.smoke_port,
        )
    except UpgradeError as error:
        print(f"Upgrade stopped: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
