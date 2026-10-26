"""Application configuration (roadmap B-002).

What this file is for: every knob the running app has lives here, in
one typed place, read from the environment with the ``GPA_`` prefix.
Nothing else in the codebase reads ``os.environ`` directly — if a
setting matters, it is declared here, documented in ``.env.example``,
and validated at startup so a missing or unsafe value fails fast
with a plain-words message instead of a surprise at 2 a.m.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The development-only fallback secret. It exists so a fresh clone
# runs instantly in dev; production startup refuses it (see below).

# Secrets production must never run with (B-145): the dev
# fallback below plus the obvious placeholders a tired deploy
# reaches for. Refused by NAME at startup, in plain words —
# a public deployment must never sign tokens with a string
# the whole internet can read in this file or guess in one try.
KNOWN_DEFAULT_SECRETS = frozenset(
    {
        "dev-only-change-me",
        "changeme",
        "change-me",
        "secret",
        "password",
        "dev-secret",
        "test-secret",
        "test-secret-for-tests-only",
    }
)

# Production's minimum: a real secret is a long random value.
PROD_SECRET_MIN_LENGTH = 32
DEV_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    """Typed settings for the whole application.

    Field names map to environment variables by the GPA_ prefix:
    ``database_url`` is read from ``GPA_DATABASE_URL``, and so on.
    """

    model_config = SettingsConfigDict(env_prefix="GPA_", env_file=".env", extra="ignore")

    # Which world are we in: dev, test, or prod. Prod is stricter.
    env: str = "dev"

    # Database location. SQLite file by default; the URL is the one
    # swap-point for PostgreSQL later (roadmap D-06).
    database_url: str = "sqlite:///./data/gpa.db"

    # Signing secret for access tokens + one-time token hashing.
    secret_key: str = DEV_SECRET

    # Token lifetimes (roadmap B-012/B-013).
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    magic_link_minutes: int = 30

    # Local file storage (B-007) and the dev mail outbox (B-012).
    storage_dir: Path = Path("./data/storage")
    outbox_dir: Path = Path("./data/outbox")

    # Auth rate limiting (B-021): attempts per minute per IP.
    auth_rate_per_minute: int = 30

    # File ingest (B-025): the largest single upload, in bytes.
    # Refused files are rejected before anything is stored.
    max_upload_bytes: int = 10_485_760

    # Background jobs (B-008): attempts before dead-letter, and the
    # base delay that exponential backoff grows from.
    job_max_attempts: int = 5
    job_backoff_base_seconds: int = 5

    # Job queue backend (B-157, decision D-09): "database" is the
    # chassis itself — the job table + claim-by-update worker this
    # project was built on, safe across any number of worker
    # processes. "arq" submits through the external-queue adapter
    # (services/jobqueue.py) to a Redis the deployment runs, for
    # deployments that outgrow one box; it needs the `queue`
    # install extra and ``redis_url`` below.
    job_queue: str = "database"

    # Redis address for the arq backend (B-157), e.g.
    # "redis://127.0.0.1:6379/0". Environment-only like every
    # address-with-possible-credentials here; empty on the
    # database backend, where it is never read.
    redis_url: str = ""

    # Internal comms key (B-163, §6A.4): base64 of the 32-byte
    # AES-256-GCM key that seals internal API payloads whenever
    # a call crosses a process boundary. Empty derives a key
    # from the app secret with a domain label (development
    # posture); production sets a real one. Environment-only.
    internal_key_b64: str = ""

    # Service identities (B-164): a directory of <name>.seed /
    # <name>.pub files (with <name>.pub.prev as rotation
    # overlap). Empty = identities derived from the app secret
    # (development custody, full machinery).
    internal_identities_dir: str = ""

    # Field-encryption master key (B-167): base64 of 32 bytes.
    # Wraps every group's data key; never stored in the
    # database. Empty derives a master from the app secret
    # (development posture); production sets a real one.
    master_key_b64: str = ""

    # Encrypted file storage (B-168): when true, stored
    # objects are sealed per-file (keys wrapped under the
    # master key) by the EncryptedStorage driver.
    encrypt_storage: bool = False

    # Master-key rotation (B-169): the OLD master key, set
    # by the operator only for the rotation job's run, then
    # unset. Never a resting configuration.
    master_key_old_b64: str = ""

    # Replay window for signed internal messages (B-164).
    internal_replay_window_seconds: int = 300

    # Link ingest (B-029): how long a page fetch may take, and the
    # largest page body the archive will keep. The SSRF guards
    # themselves live in services/linkfetch.py and are not knobs —
    # safety limits are not settings a deployment can loosen.
    link_fetch_timeout_seconds: int = 10
    link_fetch_max_bytes: int = 2_097_152

    # Dev/demo convenience: create tables at startup. Production
    # runs Alembic migrations and sets this to false.
    auto_create_tables: bool = True

    # AI adapter (Phase 3, B-035 → B-037, decision D-18). Which
    # driver sits behind the MetaModelClient protocol: "mock"
    # (schema-valid canned output, the default — the whole suite
    # and demo run offline with no key), "fixture" (recorded
    # responses keyed by prompt hash), or "live" (the Meta Model
    # API over httpx). Swapping drivers is a config change, never
    # a code change.
    ai_driver: str = "mock"

    # Fixture driver: directory of recorded responses. Empty means
    # the package's own ai/fixtures directory.
    ai_fixtures_dir: str = ""

    # Live driver: base URL + API key. The key is a SECRET and is
    # read from the environment only (GPA_AI_API_KEY) — it never
    # appears in the repo, a ZIP, or a log line.
    ai_api_base: str = ""
    ai_api_key: str = ""

    # Live driver: the model id each tier names, the per-call
    # timeout, and how many attempts a call gets (retries happen on
    # 429/5xx only — B-037).
    ai_model_spark: str = "muse-spark"
    ai_model_wonder: str = "muse-wonder"
    ai_live_timeout_seconds: float = 30.0
    ai_live_max_attempts: int = 3

    # Live driver cost accounting (B-037): price per 1k tokens, in
    # micro-units of currency (1/1,000,000), so the usage ledger
    # stays integer-exact. Zero means "price not configured" — the
    # ledger then records tokens truthfully and cost as 0.
    ai_price_spark_micros_per_1k: int = 0
    ai_price_wonder_micros_per_1k: int = 0

    # Local driver (Phase 10, B-119): an OpenAI-compatible
    # endpoint on hardware the operator controls. A group whose
    # ``prune_driver`` setting says "local" prunes through this
    # endpoint while extraction stays on the process driver.
    # The base looks like http://localhost:11434/v1; one model
    # id serves every tier; the key is optional (many local
    # servers want none) and environment-only when present.
    ai_local_base: str = ""
    ai_local_model: str = ""
    ai_local_api_key: str = ""
    ai_local_timeout_seconds: float = 60.0

    # Mail transport (Phase 8, B-095). The notification outbox
    # sends through ONE driver boundary (services/mailer.py):
    # "file" is the dev driver — messages land as JSON files in
    # ``outbox_dir``, exactly where an operator (or a test) can
    # read them; the real provider driver lands in Phase 12
    # (B-133) behind the same protocol. Tests register capture /
    # failing transports through the mailer registry instead.
    mail_transport: str = "file"

    # SMTP provider driver (Phase 12, B-133): GPA_MAIL_TRANSPORT
    # ="smtp". The provider is any SMTP-speaking service; the
    # password is a SECRET, environment-only like every secret
    # here. ``mail_from_address`` is the envelope/header From
    # the group's mail wears — deliverability (SPF/DKIM/DMARC
    # for its domain) is a feature, documented in DEPLOYMENT.md.
    mail_smtp_host: str = ""
    mail_smtp_port: int = 587
    mail_smtp_username: str = ""
    mail_smtp_password: str = ""
    mail_smtp_use_tls: bool = True
    mail_from_address: str = ""

    # The provider webhook's shared secret (B-133): when set,
    # /api/v1/webhooks/mail/{provider} demands the header
    # ``x-gpa-mail-signature`` = HMAC-SHA256 hex of the raw body
    # under this secret, and refuses unsigned word. Empty means
    # the dev posture — the receiver records unsigned events —
    # and DEPLOYMENT.md says so in plain words.
    mail_webhook_secret: str = ""

    # The public origin invite links are built on (B-098):
    # "{public_base_url}/invite/<token>". Empty means links are
    # path-only ("/invite/<token>") — right for local dev, and
    # the frontend can absolutise them itself.
    public_base_url: str = ""

    # Integration sealing (Phase 11, B-127): the envelope key
    # for secrets stored in integration configs and outbound
    # webhook secrets (services/sealing.py). Empty means "use
    # the app secret" — one secret to run a small deployment,
    # a separate one when the operator wants the envelope to
    # survive an app-secret rotation. Environment-only, like
    # every secret here; it is never stored anywhere.
    integration_key: str = ""

    # Board segmentation (Phase 5, B-064, decision D-19): SAM is
    # used ONLY to cut a board photo into poster regions. Its base
    # URL names a SAM-shaped HTTP endpoint; empty (the default)
    # means "no SAM configured" and the decided fallback runs —
    # the vision model is asked for the poster boxes instead, and
    # the same region pipeline proceeds. The key, like every
    # secret here, comes from the environment only.
    sam_api_base: str = ""
    sam_api_key: str = ""

    @field_validator("secret_key")
    @classmethod
    def _secret_must_be_real_in_prod(cls, value: str, info) -> str:
        """Fail startup, in plain words, if prod runs with a
        known-default or short secret (B-145).

        A config error should stop the world loudly at boot —
        never let a public deployment sign tokens with a
        published or guessable string.
        """
        env = (info.data or {}).get("env", "dev")
        if env == "prod" and value in KNOWN_DEFAULT_SECRETS:
            raise ValueError(
                "GPA_SECRET_KEY is set to a known default value. "
                "Set GPA_SECRET_KEY to a long random value before "
                "running with GPA_ENV=prod."
            )
        if env == "prod" and len(value) < PROD_SECRET_MIN_LENGTH:
            raise ValueError(
                "GPA_SECRET_KEY is too short for production "
                f"(under {PROD_SECRET_MIN_LENGTH} characters). "
                "Set GPA_SECRET_KEY to a long random value before "
                "running with GPA_ENV=prod."
            )
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, built (and validated) once."""
    return Settings()
