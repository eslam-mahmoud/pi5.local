#!/usr/bin/env bash
# Installs or updates RTW from its own repository, unchanged. RTW's `rtw setup` supports
# macOS and Termux, so this does the Linux equivalent and then calls RTW's own scripts.
set -euo pipefail

REPO=https://github.com/eslam-mahmoud/reinvent-the-wheel.git
APP="$ADDON_HOME/app"
PASSWORD_FILE="$ADDON_HOME/admin-password.txt"

as_addon() { runuser -u "$ADDON_USER" -- env HOME="$ADDON_HOME" "$@"; }

# Debian's nginx starts on port 80, which belongs to the dashboard. RTW runs its own
# nginx on 8080, so the system one must never start.
systemctl mask nginx.service
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
  nginx kiwix-tools git curl openssl python3

if [[ -d "$APP/.git" ]]; then
  as_addon git -C "$APP" pull --ff-only
else
  as_addon git clone --depth 1 "$REPO" "$APP"
fi

if [[ ! -d "$APP/web/vendor/maplibre" ]]; then
  as_addon bash "$APP/scripts/fetch-vendor.sh"
fi

as_addon env ROOT="$APP" CONTENT="$ADDON_STORAGE" MIME_TYPES=/etc/nginx/mime.types \
  bash "$APP/server/configure.sh"

# RTW's password command needs a terminal, so the first password is written here in the
# same format. The dashboard shows it, and `rtw passwd` can still change it later.
if [[ ! -s "$PASSWORD_FILE" ]]; then
  HTPASSWD="$APP/state/conf/htpasswd" PASSWORD_FILE="$PASSWORD_FILE" python3 - <<'PY'
import base64, hashlib, os, secrets

password = secrets.token_urlsafe(12)
salt = os.urandom(8)
digest = hashlib.sha1(password.encode() + salt).digest()
with open(os.environ["HTPASSWD"], "w") as f:
    f.write(f"admin:{{SSHA}}{base64.b64encode(digest + salt).decode()}\n")
with open(os.environ["PASSWORD_FILE"], "w") as f:
    f.write(password + "\n")
PY
  chown "$ADDON_USER:$ADDON_USER" "$APP/state/conf/htpasswd"
  chmod 0600 "$APP/state/conf/htpasswd"
  chown root:pi5 "$PASSWORD_FILE"
  chmod 0640 "$PASSWORD_FILE"
fi
