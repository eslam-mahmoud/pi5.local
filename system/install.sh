#!/usr/bin/env bash
# Installs or updates the dashboard on the Pi. Safe to run repeatedly.
# Run as root from the unpacked project: sudo bash system/install.sh
set -euo pipefail

SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
ADMIN_USER="${SUDO_USER:-$(id -un)}"
PASSWORD_NOTE="$(getent passwd "$ADMIN_USER" | cut -d: -f6)/pi5-dashboard-password.txt"

apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
  python3-aiohttp syncthing ffmpeg nodejs \
  python3-brotli python3-certifi python3-mutagen python3-pycryptodome python3-requests python3-websockets

id pi5 >/dev/null 2>&1 || useradd --system --home-dir /var/lib/pi5 --create-home --shell /usr/sbin/nologin pi5
usermod -aG pi5 "$ADMIN_USER"

# Setgid so files added over SSH by the admin stay editable by the dashboard.
install -d -o pi5 -g pi5 -m 2775 /srv/pi5 /srv/pi5/presets /srv/pi5/phones /srv/pi5/downloads
install -d -o pi5 -g pi5 -m 0750 /var/lib/pi5 /var/lib/pi5/syncthing /var/lib/pi5/bin

# The official release, because Debian's yt-dlp is too old for YouTube. It lives where the
# dashboard can update it, since sites change often.
if [[ ! -x /var/lib/pi5/bin/yt-dlp ]]; then
  work="$(mktemp -d)"
  base=https://github.com/yt-dlp/yt-dlp/releases/latest/download
  curl -fsSL -o "$work/yt-dlp" "$base/yt-dlp"
  curl -fsSL -o "$work/SHA2-256SUMS" "$base/SHA2-256SUMS"
  (cd "$work" && grep -E ' yt-dlp$' SHA2-256SUMS | sha256sum -c -)
  install -o pi5 -g pi5 -m 0755 "$work/yt-dlp" /var/lib/pi5/bin/yt-dlp
  rm -rf "$work"
fi

rm -rf /opt/pi5-dashboard
install -d -m 0755 /opt/pi5-dashboard
cp -R "$SOURCE/dashboard/." /opt/pi5-dashboard/
find /opt/pi5-dashboard -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root /opt/pi5-dashboard

# Root-owned, because the pi5-addon helper runs these recipes as root.
rm -rf /opt/pi5-addons
install -d -o root -g root -m 0755 /opt/pi5-addons /var/lib/pi5-addons /srv/pi5/addons
cp -R "$SOURCE/addons/." /opt/pi5-addons/
chown -R root:root /opt/pi5-addons
install -m 0755 "$SOURCE/system/pi5-addon" /usr/local/sbin/

if [[ ! -e /var/lib/pi5/auth.json ]]; then
  password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  sudo -u pi5 PI5_DATA_DIR=/var/lib/pi5 python3 /opt/pi5-dashboard/app.py --init-password "$password"
  install -o "$ADMIN_USER" -g "$ADMIN_USER" -m 0600 /dev/null "$PASSWORD_NOTE"
  printf '%s\n' "$password" > "$PASSWORD_NOTE"
fi

# Lets phones on the hotspot open pi5.local even without mDNS support.
install -d /etc/NetworkManager/dnsmasq-shared.d
printf 'address=/pi5.local/10.42.0.1\n' > /etc/NetworkManager/dnsmasq-shared.d/pi5-dashboard.conf

install -m 0755 "$SOURCE/system/wifi-fallback" /usr/local/sbin/
install -m 0644 "$SOURCE"/system/pi5-*.service "$SOURCE"/system/wifi-fallback.service "$SOURCE"/system/wifi-fallback.timer /etc/systemd/system/
install -m 0644 "$SOURCE/system/50-pi5-dashboard.rules" /etc/polkit-1/rules.d/
systemctl daemon-reload
systemctl enable pi5-syncthing.service pi5-dashboard.service
systemctl enable --now wifi-fallback.timer
systemctl restart pi5-syncthing.service
systemctl restart pi5-dashboard.service
