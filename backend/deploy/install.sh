#!/usr/bin/env bash
# Group Planning Assistant — native installer (roadmap B-153).
#
# No Docker, ever (decision D-24): this script installs the
# backend as plain files, a uv-managed virtualenv, a locked-down
# environment file, and systemd units — the same on a Raspberry
# Pi-class edge box and on a small cloud VM.
#
#   sudo ./deploy/install.sh
#
# run from the root of an unpacked release tree (or a checkout).
# It is safe to re-run: the code tree is refreshed, an existing
# environment file is NEVER overwritten (your secrets stay
# yours), and existing data is left exactly where it lies.
#
# Layout it builds (all overridable, see below):
#   @PREFIX@   /opt/gpa            the code tree + .venv + data/
#   @CONFIG@   /etc/gpa/gpa.env    the environment file (0640)
#   units      /etc/systemd/system/gpa{,-worker,-backup}.{service,timer}
#
# Rehearsal / CI knobs (environment variables):
#   GPA_PREFIX          install root            (default /opt/gpa)
#   GPA_CONFIG_DIR      env file directory      (default /etc/gpa)
#   GPA_SYSTEMD_DIR     unit directory          (default /etc/systemd/system)
#   GPA_SERVICE_USER    service account         (default gpa)
#   GPA_SKIP_USER=1     do not create the account (it must exist)
#   GPA_SKIP_SYSTEMD=1  do not systemctl daemon-reload / enable
#   GPA_INSTALL_LOGROTATE=1  also install deploy/gpa.logrotate
#
# The acceptance rehearsal for this installer is recorded in
# DEPLOYMENT.md ("Native install"): a full run into a throwaway
# prefix with a real venv, a generated env file proven 0640,
# rendered units validated with systemd-analyze, and the
# installed tree answering /health.
set -euo pipefail

SOURCE_TREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${GPA_PREFIX:-/opt/gpa}"
CONFIG_DIR="${GPA_CONFIG_DIR:-/etc/gpa}"
SYSTEMD_DIR="${GPA_SYSTEMD_DIR:-/etc/systemd/system}"
SERVICE_USER="${GPA_SERVICE_USER:-gpa}"
ENV_FILE="$CONFIG_DIR/gpa.env"

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

# --- Preconditions -------------------------------------------------
# Python 3.12+ and uv are the whole toolchain (the project pins
# its dependencies in uv.lock; the venv is built from the lock,
# never from a network improvisation).
command -v python3 >/dev/null 2>&1 || die "python3 not found — install Python 3.12+ first."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "Python 3.12+ is required."
command -v uv >/dev/null 2>&1 || die "uv not found — install it (https://docs.astral.sh/uv/) and re-run."
[ -f "$SOURCE_TREE/pyproject.toml" ] || die "$SOURCE_TREE does not look like the GPA backend tree."
[ "$(id -u)" = "0" ] || die "run as root (sudo) — the installer creates an account and writes system paths."

# --- The service account -------------------------------------------
if [ "${GPA_SKIP_USER:-0}" != "1" ]; then
    if ! id "$SERVICE_USER" >/dev/null 2>&1; then
        # A dedicated, login-less account: the service owns its
        # data and nothing else on the box.
        useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"
        say "created service account: $SERVICE_USER"
    fi
fi
id "$SERVICE_USER" >/dev/null 2>&1 || die "service account $SERVICE_USER does not exist."

# --- The code tree ---------------------------------------------------
mkdir -p "$PREFIX" "$PREFIX/data" "$PREFIX/backups" "$CONFIG_DIR" "$SYSTEMD_DIR"
# Refresh the code, never the runtime state: .venv is rebuilt
# below; data/, backups/ and any local .env are the deployment's
# own and are not part of the shipped tree.
( cd "$SOURCE_TREE" && tar --exclude='./.venv' --exclude='./data' \
    --exclude='./backups' --exclude='./.git' --exclude='./.env' \
    --exclude='./__pycache__' --exclude='*/__pycache__' -cf - . ) \
    | ( cd "$PREFIX" && tar -xf - )
say "code tree installed at $PREFIX"

# The virtualenv, from the lockfile, with the project's own tool.
( cd "$PREFIX" && uv sync --frozen --no-dev --no-editable )
say "virtualenv built from uv.lock"

# --- The environment file -------------------------------------------
if [ -e "$ENV_FILE" ]; then
    say "environment file exists — left untouched: $ENV_FILE"
else
    # A real secret, generated here, never printed, never in git.
    SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    umask 027
    cat > "$ENV_FILE" <<EOF
# Group Planning Assistant — production environment (B-153).
# Written by deploy/install.sh. Secrets live ONLY here.
GPA_ENV=prod
GPA_DATABASE_URL=sqlite:///$PREFIX/data/gpa.db
GPA_SECRET_KEY=$SECRET
GPA_STORAGE_DIR=$PREFIX/data/storage
GPA_OUTBOX_DIR=$PREFIX/data/outbox
# The public origin the reverse proxy serves (B-154); invite
# links are built on it. Set before going live.
GPA_PUBLIC_BASE_URL=
EOF
    # root:service-user, group-readable, world-blind.
    chown "root:$SERVICE_USER" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
    say "environment file written (0640): $ENV_FILE"
fi

# Data belongs to the service; code belongs to root.
chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX/data" "$PREFIX/backups"

# --- The units --------------------------------------------------------
render_unit() {
    # Render one template with this install's real paths.
    local template="$1" output="$2"
    sed -e "s|@PREFIX@|$PREFIX|g" \
        -e "s|@USER@|$SERVICE_USER|g" \
        -e "s|@CONFIG@|$CONFIG_DIR|g" \
        "$template" > "$output"
    say "unit installed: $output"
}
render_unit "$PREFIX/deploy/gpa.service.in"        "$SYSTEMD_DIR/gpa.service"
render_unit "$PREFIX/deploy/gpa-worker.service.in" "$SYSTEMD_DIR/gpa-worker.service"
render_unit "$PREFIX/deploy/gpa-backup.service.in" "$SYSTEMD_DIR/gpa-backup.service"
cp "$PREFIX/deploy/gpa-backup.timer" "$SYSTEMD_DIR/gpa-backup.timer"
say "unit installed: $SYSTEMD_DIR/gpa-backup.timer"

if [ "${GPA_INSTALL_LOGROTATE:-0}" = "1" ]; then
    cp "$PREFIX/deploy/gpa.logrotate" /etc/logrotate.d/gpa
    say "logrotate installed: /etc/logrotate.d/gpa"
fi

# --- systemd ----------------------------------------------------------
if [ "${GPA_SKIP_SYSTEMD:-0}" != "1" ]; then
    systemctl daemon-reload
    # Enabled, not started: the operator reads the env file,
    # sets GPA_PUBLIC_BASE_URL, and starts it deliberately.
    systemctl enable gpa.service gpa-worker.service gpa-backup.timer
    say "units enabled (not started)"
fi

cat <<EOF

Installed. Next, in order:
  1. Read $ENV_FILE — set GPA_PUBLIC_BASE_URL and any mail /
     integration settings (DEPLOYMENT.md walks every one).
  2. systemctl start gpa gpa-worker
  3. curl http://127.0.0.1:8000/health   →  {"status": "ok", ...}
  4. Put the reverse proxy in front (deploy/PROXY.md) before
     the world can reach it.
EOF
