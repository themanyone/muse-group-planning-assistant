"""Link ingest tests (roadmap B-029) — the SSRF attack suite.

The guards are the feature: a member pastes a link, and the
server must fetch the PAGE without ever letting the link aim it
at itself, the LAN, or a cloud metadata service. Every test runs
against a fake internet (monkeypatched transport + resolver) —
the suite never touches the real network.
"""

import pytest

from conftest import bearer, make_group, register
from gpa.services import linkfetch

PUBLIC = ["93.184.216.34"]  # example.org's public address shape.
PAGE = b"<html><body><h1>Harvest Supper</h1><p>Saturday at the hall.</p></body></html>"


class FakeNet:
    """A tiny fake internet: hostnames resolve where the test
    says, and URLs answer what the test programmed."""

    def __init__(self, dns: dict, pages: dict):
        self.dns = dns
        self.pages = pages
        self.fetched: list[str] = []

    def resolver(self, host: str):
        return self.dns[host]

    def transport(self, url: str, timeout: int):
        self.fetched.append(url)
        return self.pages[url]


@pytest.fixture()
def fake_net(monkeypatch):
    """Install a FakeNet factory over linkfetch's real seams."""

    def install(dns, pages):
        net = FakeNet(dns, pages)
        monkeypatch.setattr(linkfetch, "_resolve", net.resolver)
        monkeypatch.setattr(linkfetch, "_urllib_transport", net.transport)
        return net

    return install


def _post_link(client, owner, group, url):
    return client.post(
        f"/api/v1/groups/{group['id']}/sources/link",
        json={"url": url},
        headers=bearer(owner),
    )


def test_link_archives_page_text_and_records_final_url(client, fake_net):
    fake_net(
        {"example.org": PUBLIC},
        {"https://example.org/supper": (200, {"Content-Type": "text/html"}, PAGE)},
    )
    owner = register(client)
    group = make_group(client, owner)
    response = _post_link(client, owner, group, "https://example.org/supper")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "link"
    assert body["url"] == "https://example.org/supper"
    whole = "\n".join(c["text"] for c in body["chunks"])
    assert "Harvest Supper" in whole and "Saturday at the hall" in whole


def test_cloud_metadata_address_is_blocked_before_any_fetch(client, fake_net):
    net = fake_net({"metadata.example": ["169.254.169.254"]}, {})
    owner = register(client)
    group = make_group(client, owner)
    response = _post_link(client, owner, group, "http://metadata.example/latest/meta-data/")
    assert response.status_code == 422
    assert response.json()["code"] == "fetch_blocked"
    assert net.fetched == []  # Refused before a single byte moved.


@pytest.mark.parametrize(
    "address", ["10.0.0.5", "192.168.1.1", "127.0.0.1", "172.16.0.9", "::1"]
)
def test_private_and_loopback_addresses_are_blocked(client, fake_net, address):
    fake_net({"sneaky.example": [address]}, {})
    owner = register(client)
    group = make_group(client, owner)
    response = _post_link(client, owner, group, "http://sneaky.example/admin")
    assert response.status_code == 422
    assert response.json()["code"] == "fetch_blocked"


def test_metadata_hostname_is_blocked_by_name(client, fake_net):
    fake_net({"metadata.google.internal": PUBLIC}, {})
    owner = register(client)
    group = make_group(client, owner)
    response = _post_link(client, owner, group, "http://metadata.google.internal/")
    assert response.status_code == 422
    assert response.json()["code"] == "fetch_blocked"


def test_redirect_into_the_lan_is_blocked_at_the_hop(client, fake_net):
    net = fake_net(
        {"public.example": PUBLIC, "lan.example": ["10.0.0.5"]},
        {
            "https://public.example/start": (
                302, {"Location": "http://lan.example/secret"}, b"",
            )
        },
    )
    owner = register(client)
    group = make_group(client, owner)
    response = _post_link(client, owner, group, "https://public.example/start")
    assert response.status_code == 422
    assert response.json()["code"] == "fetch_blocked"
    # The public hop was fetched; the LAN hop never was.
    assert net.fetched == ["https://public.example/start"]


def test_public_redirect_chain_archives_the_final_page(client, fake_net):
    fake_net(
        {"example.org": PUBLIC},
        {
            "https://example.org/old": (301, {"Location": "/supper"}, b""),
            "https://example.org/supper": (200, {"Content-Type": "text/html"}, PAGE),
        },
    )
    owner = register(client)
    group = make_group(client, owner)
    response = _post_link(client, owner, group, "https://example.org/old")
    assert response.status_code == 201, response.text
    assert response.json()["url"] == "https://example.org/supper"


def test_unreadable_page_archives_nothing(client, fake_net):
    fake_net(
        {"example.org": PUBLIC},
        {"https://example.org/gone": (404, {"Content-Type": "text/html"}, b"nope")},
    )
    owner = register(client)
    group = make_group(client, owner)
    response = _post_link(client, owner, group, "https://example.org/gone")
    assert response.status_code == 502
    assert response.json()["code"] == "fetch_failed"
    listed = client.get(f"/api/v1/groups/{group['id']}/sources", headers=bearer(owner))
    assert listed.json() == []


def test_non_http_schemes_are_blocked(client, fake_net):
    fake_net({}, {})
    owner = register(client)
    group = make_group(client, owner)
    for url in ("file:///etc/passwd", "ftp://example.org/x", "gopher://example.org/"):
        response = _post_link(client, owner, group, url)
        assert response.status_code == 422, url
        assert response.json()["code"] == "fetch_blocked"
