"""The developer pack (roadmap B-160).

Four promises, each tested as stated:

- the Python SDK regenerates from the frozen contract
  byte-identically (the B-149 determinism discipline), is
  standard-library only, and loads clean;
- the TypeScript types are promoted into a package that
  `npm pack` accepts, its index.d.ts byte-identical to the
  project's generated types;
- key self-service is a real round trip: mint, use, list
  (prefix only), revoke, refused;
- THE acceptance: an outside script — examples/
  public_events.py, under twenty lines — lists a group's
  public events against a REAL booted server (uvicorn
  subprocess, seeded over HTTP), no test-client shortcuts.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import bearer, register

ROOT = Path(__file__).resolve().parent.parent
SDK_PY = ROOT / "sdk" / "python" / "gpa_sdk.py"
TS_DIR = ROOT / "sdk" / "typescript"
EXAMPLE = ROOT / "examples" / "public_events.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_sdk", ROOT / "scripts" / "generate_sdk.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sdk_regenerates_byte_identically():
    import hashlib

    module = _load_generator()
    spec_bytes = (ROOT / "OPENAPI.json").read_bytes()
    spec = json.loads(spec_bytes)
    spec_hash = hashlib.sha256(spec_bytes).hexdigest()[:16]
    assert SDK_PY.read_text(encoding="utf-8") == module.render_sdk(
        spec, spec_hash
    ), "sdk/python/gpa_sdk.py is stale — run scripts/generate_sdk.py"
    assert (TS_DIR / "index.d.ts").read_bytes() == (
        ROOT / "frontend_types.ts"
    ).read_bytes(), "the TS package's types drifted from frontend_types.ts"


def test_sdk_is_stdlib_only_and_exposes_the_public_sugar():
    import ast

    tree = ast.parse(SDK_PY.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), imported

    spec = importlib.util.spec_from_file_location("gpa_sdk", SDK_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module.GpaClient("http://127.0.0.1:9")
    assert callable(client.public_events) and callable(client.public_page)
    # One method per operation, roughly: the contract's 242
    # operations all made it into the client.
    methods = [n for n in dir(client) if not n.startswith("_")]
    assert len(methods) >= 242


def test_typescript_package_packs():
    package = json.loads((TS_DIR / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "gpa-types"
    assert package["types"] == "index.d.ts"
    assert package["license"] == "GPL-2.0-only"
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm is not installed in this environment")
    # npm pack against a COPY: packing must not litter the tree.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "pkg"
        shutil.copytree(TS_DIR, staged)
        env = {**os.environ, "npm_config_cache": str(Path(tmp) / "cache")}
        run = subprocess.run(
            [npm, "pack", str(staged), "--silent"],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=120,
        )
        assert run.returncode == 0, run.stderr
        tarball = next(Path(tmp).glob("*.tgz"))
        with tarfile.open(tarball) as tar:
            names = set(tar.getnames())
        assert "package/index.d.ts" in names
        assert "package/package.json" in names


def test_key_self_service_round_trip(client):
    """Mint → use → list (prefix only) → revoke → refused."""
    owner = register(client)
    created = client.post(
        "/api/v1/users/me/api-keys",
        json={"label": "pack test", "scopes": ["read"]},
        headers=bearer(owner),
    )
    assert created.status_code == 201, created.text
    full_key = created.json()["key"]
    assert full_key and full_key.startswith("gpa_")

    # Keys travel in X-API-Key — NOT as a Bearer token
    # (a Bearer attempt is refused token_invalid, by design).
    used = client.get(
        "/api/v1/users/me", headers={"X-API-Key": full_key}
    )
    assert used.status_code == 200, used.text

    listed = client.get("/api/v1/users/me/api-keys", headers=bearer(owner))
    assert listed.status_code == 200
    assert all(full_key not in str(row) for row in listed.json())

    key_id = created.json()["id"]
    revoked = client.delete(
        f"/api/v1/users/me/api-keys/{key_id}", headers=bearer(owner)
    )
    assert revoked.status_code in (200, 204), revoked.text
    refused = client.get(
        "/api/v1/users/me", headers={"X-API-Key": full_key}
    )
    assert refused.status_code == 401


def _http(method, url, token=None, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read() or b"null")


def test_example_lists_public_events_against_a_booted_server(tmp_path):
    """THE acceptance, run for real: uvicorn subprocess on a
    scratch database, seeded over HTTP, then the example
    script as a separate process must print the event."""
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 20, f"the example is {len(lines)} lines"

    port = 18777
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "GPA_ENV": "test",
        "GPA_SECRET_KEY": "pack-test-secret",
        "GPA_DATABASE_URL": f"sqlite:///{tmp_path}/pack.db",
        "GPA_STORAGE_DIR": str(tmp_path / "storage"),
        "GPA_OUTBOX_DIR": str(tmp_path / "outbox"),
    }
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "gpa.main:create_app",
            "--factory", "--host", "127.0.0.1", "--port", str(port),
        ],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(80):
            try:
                status, _ = _http("GET", f"{base}/health")
                if status == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise AssertionError("the booted server never answered /health")

        _, owner = _http(
            "POST", f"{base}/api/v1/auth/register",
            payload={
                "email": "mara@example.org",
                "password": "correct-horse-battery",
                "display_name": "Mara Ellison",
            },
        )
        token = owner["access_token"]
        _, group = _http(
            "POST", f"{base}/api/v1/groups", token=token,
            payload={
                "name": "Riverbend Community Trust",
                "visibility": "public-listed",
            },
        )
        status, _event = _http(
            "POST", f"{base}/api/v1/groups/{group['id']}/events", token=token,
            payload={
                "title": "Lantern Parade",
                "starts_at": "2026-12-05T17:00:00+00:00",
                "ends_at": "2026-12-05T19:00:00+00:00",
            },
        )
        assert status == 201

        run = subprocess.run(
            [sys.executable, str(EXAMPLE), base, group["slug"]],
            capture_output=True, text=True, timeout=60,
        )
        assert run.returncode == 0, run.stderr
        assert "Lantern Parade" in run.stdout
        assert "2026-12-05" in run.stdout
    finally:
        server.terminate()
        server.wait(timeout=10)
