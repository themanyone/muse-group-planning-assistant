"""The one guarded road out to the web (roadmap B-029).

What this file is for: link ingest fetches a page a member pasted.
Fetching arbitrary URLs from a server is a classic way to make
the server attack itself (SSRF) — ask it for the cloud metadata
address and it hands over credentials; ask it for the LAN and it
maps the house. So EVERY fetch in this codebase goes through this
helper, and the helper is built fail-closed:

- Only http/https, only hosts that resolve, and EVERY resolved
  address must be a public one — private, loopback, link-local
  (which includes the cloud metadata address 169.254.169.254),
  reserved, and multicast addresses are refused before any byte
  moves.
- Known metadata hostnames are refused by name as well.
- Redirects are followed by hand (at most 5), and each hop is
  re-validated from scratch: a public page that redirects INTO
  the LAN is refused at the hop, not fetched.
- Timeouts and a body-size cap bound every fetch.

The two seams — ``transport`` (move the bytes) and ``resolver``
(name → addresses) — are injectable, so the test suite exercises
the guards against a fake internet and never touches the real
one. The defaults are the standard library's, deliberately
(D-46): no new dependency learns our members' reading habits.
"""

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from gpa.errors import AppError

# Hostnames that are metadata services by name, whatever they
# resolve to. Belt and braces behind the address checks.
_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata.google"}

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


def _resolve(host: str) -> list[str]:
    """Default resolver: the OS's own address lookup."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse urllib's automatic redirects — fetch() walks hops
    itself so each one can be re-validated before it is fetched."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urllib_transport(url: str, timeout: int) -> tuple[int, dict, bytes]:
    """Default transport: one GET, no redirect following.

    HTTP error statuses (including 3xx) come back as ordinary
    results — deciding what a status MEANS is fetch()'s job, and
    it cannot decide what it never sees.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(
        url, headers={"User-Agent": "GroupPlanningAssistant/1.0 (link archive)"}
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        # An HTTP status was reached; report it like a response.
        return err.code, dict(err.headers or {}), err.read() if err.fp else b""
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise AppError(
            "fetch_failed", detail=f"That page could not be fetched: {err}"
        ) from None


def assert_public_url(url: str, resolver=_resolve) -> str:
    """Validate one URL for fetching, or raise fetch_blocked.

    Returns the URL unchanged on success. Every resolved address
    must be public: a host that resolves to a mix of public and
    private addresses is refused whole — fetching "the public one"
    would still teach an attacker what the private one answered.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AppError(
            "fetch_blocked", detail="Only http and https links can be fetched."
        )
    host = parsed.hostname.lower()
    if host in _BLOCKED_HOSTNAMES:
        raise AppError(
            "fetch_blocked", detail="That host is a metadata address and is never fetched."
        )
    try:
        addresses = resolver(host)
    except Exception:
        raise AppError(
            "fetch_failed", detail="That host could not be looked up."
        ) from None
    if not addresses:
        raise AppError("fetch_failed", detail="That host could not be looked up.")
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            raise AppError(
                "fetch_blocked", detail="That host resolved to an unreadable address."
            ) from None
        if not address.is_global:
            raise AppError(
                "fetch_blocked",
                detail="That link points at a private or local address, which is never fetched.",
            )
    return url


def fetch(
    url: str,
    *,
    timeout: int = 10,
    max_bytes: int = 2_097_152,
    transport=None,
    resolver=None,
) -> tuple[str, bytes, str]:
    """Fetch a page the guarded way: (final URL, body, content type).

    Each redirect hop is validated before it is fetched. Only
    200 responses with a readable content type come back; every
    refusal is a problem+json error the API can return as-is.
    """
    transport = transport or _urllib_transport
    resolver = resolver or _resolve
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        assert_public_url(current, resolver)
        status, headers, body = transport(current, timeout)
        lowered = {str(k).lower(): str(v) for k, v in headers.items()}
        if status in _REDIRECT_STATUSES:
            location = lowered.get("location")
            if not location:
                raise AppError("fetch_failed", detail="That page redirected nowhere.")
            current = urljoin(current, location)
            continue
        if status != 200:
            raise AppError(
                "fetch_failed", detail=f"That page answered with status {status}."
            )
        content_type = lowered.get("content-type", "").lower()
        if content_type and not (
            "text/html" in content_type or "text/plain" in content_type
        ):
            raise AppError(
                "fetch_failed", detail="That link is not a readable page of text."
            )
        if len(body) > max_bytes:
            raise AppError(
                "fetch_failed", detail="That page is larger than the archive keeps."
            )
        return current, body, content_type or "text/plain"
    raise AppError("fetch_failed", detail="That page redirected too many times.")
