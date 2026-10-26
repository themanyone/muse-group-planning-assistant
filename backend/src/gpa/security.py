"""Security primitives (roadmap Phase 1, B-011 → B-014).

What this file is for: the small, sharp tools every identity feature
is built from — Argon2id password hashing, one-time token hashing,
HS256 access tokens (JWT), and API-key generation. Written directly
on the standard library plus argon2-cffi, so every behaviour here is
ours to read and test (roadmap D-46).

Rules that never bend:
- Passwords are hashed with Argon2id and never stored or logged raw.
- One-time tokens and API keys are stored ONLY as SHA-256 hashes —
  a leaked database yields no usable credential.
- Access tokens are short-lived (15 minutes by default, B-013).
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from gpa.errors import AppError

# Argon2id is the memory-hard password hash the roadmap names
# (B-011). One shared hasher; parameters are the library defaults.
_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password with Argon2id. The raw password is never kept."""
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Check a password against its Argon2id hash, safely."""
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # A malformed stored hash is a failed login, not a crash.
        return False


def hash_secret(value: str) -> str:
    """SHA-256 hex of a token/key — the only form ever stored."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_token(nbytes: int = 32) -> str:
    """A fresh random one-time token (magic links, resets, refresh)."""
    return secrets.token_urlsafe(nbytes)


# --- Access tokens: a minimal, honest JWT (HS256) ------------------
# Implemented by hand so the format carries no surprises: header,
# payload, HMAC-SHA256 signature, base64url throughout (RFC 7519).


def _b64url_encode(raw: bytes) -> str:
    """base64url without padding, as JWT requires."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    """Inverse of _b64url_encode (restores stripped padding)."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_access_token(secret: str, user_id: str, minutes: int = 15, family_id: str = "") -> str:
    """Mint a signed access token for one user.

    Claims: ``sub`` (the user id), ``type`` ("access" — refresh
    tokens are NOT JWTs here; they are opaque rows, see services),
    ``exp``/``iat``, and a unique ``jti``. When the session's
    refresh family is known it rides as ``fam`` (B-174) so the
    session list can mark which signed-in device is THIS one;
    it is a label, never a credential — the refresh token itself
    stays the family's only key.
    """
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + minutes * 60,
        "jti": uuid.uuid4().hex,
    }
    if family_id:
        payload["fam"] = family_id
    signing_input = (
        f"{_b64url_encode(json.dumps(header).encode())}"
        f".{_b64url_encode(json.dumps(payload).encode())}"
    )
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def verify_access_token(secret: str, token: str) -> dict:
    """Verify a token's signature, type, and expiry; return claims.

    Fails closed with the registry's 401 codes — expired is told
    apart from invalid because the friendly fix differs ("request a
    new link" vs "that link is wrong").
    """
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected = hmac.new(
            secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
            raise AppError("token_invalid")
        payload = json.loads(_b64url_decode(payload_b64))
    except AppError:
        raise
    except Exception:
        raise AppError("token_invalid")
    if payload.get("type") != "access":
        raise AppError("token_invalid")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise AppError("token_expired")
    return payload


# --- API keys -------------------------------------------------------

# Every key starts with this prefix so a leaked string in a log or a
# screenshot is recognisable as one of ours (and greppable).
API_KEY_PREFIX = "gpa_"


def new_api_key() -> tuple[str, str, str]:
    """Create an API key; return (full_key, display_prefix, hash).

    Only the hash is stored. The display prefix (first characters)
    lets a user tell their keys apart in a list without exposing
    anything usable.
    """
    full = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return full, full[:12], hash_secret(full)
