#!/usr/bin/env bash
# Copies this project to the Pi and runs the installer.
set -euo pipefail

cd "$(dirname "$0")"
[[ -f deploy.env ]] && source deploy.env  # PI=user@host, kept out of git
PI="${PI:-pi@pi5.local}"

rsync -a --delete --exclude __pycache__ --exclude .DS_Store dashboard system addons "$PI:pi5-dashboard-src/"
ssh "$PI" 'sudo bash ~/pi5-dashboard-src/system/install.sh'
ssh "$PI" 'systemctl is-active pi5-dashboard pi5-syncthing'
