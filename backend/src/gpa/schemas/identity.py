"""Identity schemas: accounts, sign-in, tokens, API keys (Phase 1)."""

# NOTE on email validation: pydantic's EmailStr would need the extra
# email-validator package. To keep the dependency list exactly the
# roadmap's, email shape is checked by a small in-house validator in
# services/identity.py instead — schemas only bound lengths here.
from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """Create an account with email + password (B-011)."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)
    timezone: str = Field(default="UTC", max_length=64)
    locale: str = Field(default="en", max_length=16)


class LoginRequest(BaseModel):
    """Sign in with email + password (B-013)."""

    email: str
    password: str


class MagicLinkRequest(BaseModel):
    """Ask for a sign-in link by email (B-012)."""

    email: str


class TokenConsumeRequest(BaseModel):
    """Present a one-time token (magic link / reset / email change)."""

    token: str
    # Only used by the password-reset flow: the new password to set.
    new_password: str | None = Field(default=None, min_length=8, max_length=200)


class RefreshRequest(BaseModel):
    """Exchange a refresh token for a fresh session (B-013)."""

    refresh_token: str


class SessionResponse(BaseModel):
    """What a successful sign-in returns: short-lived access token,
    rotating refresh token, and who it belongs to."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str


class UserResponse(BaseModel):
    """A person's public profile. Never carries any credential."""

    id: str
    email: str
    name: str
    timezone: str
    locale: str


class ProfileUpdateRequest(BaseModel):
    """Change the editable profile fields (B-011)."""

    name: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=16)


class PasswordChangeRequest(BaseModel):
    """Set a new password, proving the current one (B-020)."""

    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class EmailChangeRequest(BaseModel):
    """Ask to move the account to a new email (B-020). The change
    completes only when the token sent to the NEW address is used."""

    new_email: str = Field(min_length=3, max_length=320)


class LoginResponse(BaseModel):
    """What password sign-in returns (B-174). Without MFA this is
    the ordinary session; with MFA on, the tokens stay empty and
    the answer carries ``mfa_required`` plus the single-use
    challenge the authenticator code must answer."""

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    user_id: str
    mfa_required: bool = False
    mfa_challenge: str | None = None


class MfaChallengeRequest(BaseModel):
    """Answer a sign-in MFA challenge with an authenticator code."""

    challenge_token: str
    code: str = Field(min_length=6, max_length=8)


class MfaCodeRequest(BaseModel):
    """A bare authenticator code (confirm enrolment / disable)."""

    code: str = Field(min_length=6, max_length=8)


class MfaSetupResponse(BaseModel):
    """Enrolment's one showing of the secret, plus the QR URI."""

    secret: str
    otpauth_uri: str
    enabled: bool = False


class MfaStatusResponse(BaseModel):
    """The account's MFA posture (B-174)."""

    enabled: bool
    setup_pending: bool
    offer_required: bool


class SessionItemResponse(BaseModel):
    """One signed-in device (a refresh-token family, B-174)."""

    family_id: str
    created_at: str
    last_seen_at: str
    expires_at: str
    user_agent: str = ""
    ip_address: str = ""
    status: str
    current: bool = False


class ApiKeyCreateRequest(BaseModel):
    """Mint an API key with chosen scopes (B-014)."""

    label: str = Field(default="", max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["read"])


class ApiKeyResponse(BaseModel):
    """One API key as listed. ``key`` is present ONLY in the create
    response — the one moment the full key is ever shown."""

    id: str
    label: str
    prefix: str
    scopes: list[str]
    last_used_at: str | None = None
    revoked: bool = False
    key: str | None = None
