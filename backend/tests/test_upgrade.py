"""Upgrade tool contracts (roadmap B-156).

The full v0.4 → v0.5 drill is scripts/upgrade_drill.py
(run by operators and recorded in TEST_REPORT); these
tests pin the tool's refusal paths and its bundle
verification — the parts that must never silently pass.
"""

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "gpa_upgrade", ROOT / "scripts" / "upgrade.py"
)
upgrade_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(upgrade_tool)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_env_file_parser_takes_only_gpa_keys(tmp_path):
    env_file = tmp_path / "gpa.env"
    env_file.write_text(
        "# a comment\n"
        "GPA_DATABASE_URL=sqlite:////srv/gpa.db\n"
        "GPA_SECRET_KEY=abc=def\n"  # values may contain =
        "UNRELATED=nope\n"
        "\n"
        "GPA_STORAGE_DIR=/srv/storage\n",
        encoding="utf-8",
    )
    values = upgrade_tool.read_env_file(env_file)
    assert values == {
        "GPA_DATABASE_URL": "sqlite:////srv/gpa.db",
        "GPA_SECRET_KEY": "abc=def",
        "GPA_STORAGE_DIR": "/srv/storage",
    }
    with pytest.raises(upgrade_tool.UpgradeError):
        upgrade_tool.read_env_file(tmp_path / "missing.env")


def test_stage_zip_refuses_anything_but_a_release(tmp_path):
    bogus = tmp_path / "bogus.zip"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("readme.txt", "not a release")
    with pytest.raises(upgrade_tool.UpgradeError, match="release-manifest"):
        upgrade_tool.stage_zip(bogus, tmp_path / "stage")
    with pytest.raises(upgrade_tool.UpgradeError, match="not found"):
        upgrade_tool.stage_zip(tmp_path / "absent.zip", tmp_path / "stage")


def test_stage_zip_accepts_the_release_shape(tmp_path):
    release = tmp_path / "release.zip"
    with zipfile.ZipFile(release, "w") as archive:
        archive.writestr(
            "release-manifest.json",
            json.dumps({"version": "v9.9", "git_hash": "x" * 40}),
        )
        archive.writestr("gpa-backend/pyproject.toml", "[project]\n")
    staged = upgrade_tool.stage_zip(release, tmp_path / "stage")
    assert staged == tmp_path / "stage" / "gpa-backend"
    assert (staged / "pyproject.toml").is_file()


def test_verify_bundle_checks_the_real_manifest_shape(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "storage" / "sub").mkdir(parents=True)
    db_bytes = b"the database"
    file_bytes = b"a stored file"
    (bundle / "data.db").write_bytes(db_bytes)
    (bundle / "storage" / "sub" / "f.bin").write_bytes(file_bytes)
    (bundle / "manifest.json").write_text(
        json.dumps({
            "database": {"file": "data.db", "sha256": _sha(db_bytes), "bytes": 12},
            "storage_files": [
                {"path": "sub/f.bin", "sha256": _sha(file_bytes), "bytes": 13}
            ],
        }),
        encoding="utf-8",
    )
    upgrade_tool.verify_bundle(bundle)  # passes as written

    (bundle / "storage" / "sub" / "f.bin").write_bytes(b"tampered")
    with pytest.raises(upgrade_tool.UpgradeError, match="fails its hash"):
        upgrade_tool.verify_bundle(bundle)


def test_staged_venv_is_built_non_editable():
    # An editable install is a .pth naming the staging path;
    # the swap renames that path away and the upgraded tree
    # would not boot. The v0.4 → v0.5 drill caught this.
    source = (ROOT / "scripts" / "upgrade.py").read_text(encoding="utf-8")
    assert '"--no-editable"' in source


def test_serving_venv_is_rebuilt_at_its_final_address():
    # A venv is path-bound (console-script shebangs name the
    # interpreter absolutely). After the swap the tool must
    # rebuild the venv in place — the drill's final boot is
    # the real proof; this pins that the step exists.
    source = (ROOT / "scripts" / "upgrade.py").read_text(encoding="utf-8")
    assert "born at its final address" in source


def test_upgrade_refuses_a_non_installation(tmp_path):
    with pytest.raises(upgrade_tool.UpgradeError, match="does not look like"):
        upgrade_tool.upgrade(
            root=tmp_path,
            zip_path=tmp_path / "whatever.zip",
            env_file=None,
            database_url="sqlite:///x.db",
            storage_dir=tmp_path / "storage",
            smoke_port=18777,
            log=lambda *args: None,
        )
