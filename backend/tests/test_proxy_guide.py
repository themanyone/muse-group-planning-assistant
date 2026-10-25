"""Reverse-proxy guide contracts (roadmap B-154).

The live test deploy is rehearsed with a real Caddy
binary (deploy/PROXY.md's record); these tests pin the
examples to the application's own facts, so the guide
cannot drift from the code it fronts: the upload limit is
B-141's, the feed path is the router's, HSTS is on, and
nothing caches the API.
"""

from pathlib import Path

from gpa.config import Settings

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def _read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_both_examples_carry_hsts_and_the_b141_upload_limit():
    limit = Settings(_env_file=None).max_upload_bytes
    caddy = _read("Caddyfile.example")
    nginx = _read("nginx.conf.example")
    for example in (caddy, nginx):
        assert "Strict-Transport-Security" in example
        assert "max-age=31536000" in example
        assert "proxy_cache" not in example  # API answers are personal
    assert f"max_size {limit}" in caddy
    assert f"client_max_body_size {limit // (1024 * 1024)}m" in nginx


def test_secret_bearing_paths_are_called_out():
    caddy = _read("Caddyfile.example")
    nginx = _read("nginx.conf.example")
    guide = _read("PROXY.md")
    # The feed token never reaches a log in either example.
    assert "calendar/feed/REDACTED" in caddy
    assert "access_log off" in nginx
    # The guide names every called-out path class.
    for needle in ("calendar/feed/", "/invites/{token}", "/email-in/{token}",
                   "/webhooks/mail/{provider}", "/groups/{id}/stream"):
        assert needle in guide, f"PROXY.md forgot {needle}"
    # And the one rule that breaks webhooks if forgotten.
    assert "RAW body" in guide


def test_sse_is_unbuffered_in_both_examples():
    assert "flush_interval -1" in _read("Caddyfile.example")
    assert "proxy_buffering off" in _read("nginx.conf.example")
