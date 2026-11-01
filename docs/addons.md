# Addons

An addon is an app that runs beside the dashboard. It stays its own project, in its own
repository, and keeps its own web pages, port and users. The dashboard installs it, starts and
stops it, shows whether it's healthy and links to it.

Use an addon for anything that already runs as a server. To add a screen inside the dashboard
itself, see [Adding a section](../README.md#adding-a-section) instead.

## How it works

```text
Addons screen ──▶ dashboard (pi5 user, no root rights)
                     │ starts pi5-addon-install@<id> or pi5-addon-remove@<id>
                     ▼
                  pi5-addon helper (root)
                     │ runs the recipe from /opt/pi5-addons/<id>
                     ▼
                  pi5-addon-<id>.service (the addon's own user)
                     ▲
Other devices ───────┘ http://pi5.local:<port>
```

An addon adds two things to this repository: a line in the registry and a recipe, a small folder
that says how to fetch and run the project.

```text
addons/
  registry.json     every known addon
  <id>/
    addon.json      what the dashboard shows
    install.sh      installs or updates the app, as root
    addon.service   how systemd runs it
    uninstall.sh    optional extra cleanup, as root
```

## Registry

[`addons/registry.json`](../addons/registry.json) lists every known addon, and it's what the
Addons tab shows. It ships with the dashboard, so it only changes with a deploy. It's never
downloaded, because a recipe runs as root.

```json
{
  "id": "notes",
  "name": "Notes",
  "summary": "One sentence shown on the Addons screen.",
  "repository": "https://github.com/you/notes",
  "status": "planned"
}
```

| Status | Meaning |
| --- | --- |
| `planned` | Known, with no recipe yet. The tab shows it with a link to the project, and no Install button |
| `ready` | Has a tested recipe in `addons/<id>/`, and can be installed |

Only addons listed as `ready` can be installed from the dashboard. For these, `name` and
`summary` must match `addon.json`. To check everything agrees, including that recipes and ports
don't clash:

```bash
python3 system/check-addons.py
```

## Add an addon

1. Pick an ID: a lowercase letter, then up to 20 lowercase letters, digits or hyphens.
2. Add it to the registry as `planned`. You can stop here until the recipe is ready.
3. Pick a port that nothing else uses. See [Ports in use](#ports-in-use).
4. Create `addons/<id>/` with the files below. [`addons/rtw`](../addons/rtw) is a full example.
5. Test it on a real Pi, then change its status to `ready`.
6. Deploy with `./deploy.sh`. The addon appears on the Addons screen, with an Install button.

### addon.json

```json
{
  "id": "notes",
  "name": "Notes",
  "summary": "One sentence shown on the Addons screen.",
  "icon": "addons",
  "port": 8090,
  "path": "/",
  "homepage": "https://github.com/you/notes",
  "download": "About 40 MB",
  "needsMb": 200,
  "credentials": { "user": "admin", "file": "admin-password.txt" }
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | Yes | Must match the folder name |
| `name` | Yes | Shown on the card |
| `summary` | Yes | One sentence shown on the card |
| `port` | Yes | Where the app listens. The dashboard checks it and links to it |
| `path` | No | Page to open, `/` by default |
| `icon` | No | An icon from the sprite in `dashboard/static/index.html`, without `i-` |
| `homepage` | No | The project's page |
| `download` | No | Shown before installing |
| `needsMb` | No | Free space needed to start installing |
| `credentials` | No | Adds **Show admin password**. `file` is relative to `ADDON_HOME` |

### install.sh

The helper creates the addon's user and folders, then runs this script as root. It runs again
for every update, so it must be safe to repeat and must keep the addon's data.

| Variable | Value |
| --- | --- |
| `ADDON_ID` | The addon's ID |
| `ADDON_USER` | `pi5-addon-<id>`, the system user that runs it |
| `ADDON_HOME` | `/var/lib/pi5-addons/<id>`, for code, config and state. Deleted on removal |
| `ADDON_STORAGE` | `/srv/pi5/addons/<id>`, for the user's data. Kept on removal |

```bash
#!/usr/bin/env bash
set -euo pipefail

APP="$ADDON_HOME/app"
as_addon() { runuser -u "$ADDON_USER" -- env HOME="$ADDON_HOME" "$@"; }

if [[ -d "$APP/.git" ]]; then
  as_addon git -C "$APP" pull --ff-only
else
  as_addon git clone --depth 1 https://github.com/you/notes.git "$APP"
fi
```

Run everything you can as the addon's user. Keep root for system steps such as `apt-get`.

A saved password must be readable by the dashboard, so write it as `root:pi5` with mode `0640`.
Never commit one.

### addon.service

The helper replaces `@USER@`, `@HOME@` and `@STORAGE@`, then installs this as
`pi5-addon-<id>.service`.

```ini
[Unit]
Description=Notes
After=network.target

[Service]
User=@USER@
Group=@USER@
ExecStart=@HOME@/app/run --port 8090
Restart=on-failure
MemoryMax=300M

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=@HOME@ @STORAGE@

[Install]
WantedBy=multi-user.target
```

Keep the hardening lines. Set `MemoryMax`, because this Pi may have only 1 GB of RAM to share
with the dashboard and phone sync.

## Rules for addons

- **Listen on your own port.** Port 80 belongs to the dashboard. If a package starts its own
  service on install, mask it first, as `addons/rtw/install.sh` does with nginx.
- **Keep data in `ADDON_STORAGE`,** so removing or reinstalling doesn't lose it.
- **Bind to all interfaces** for pages people open, and to `127.0.0.1` for internal services.
- **Assume the power can be cut.** Clear stale process IDs and lock files on start.
- **Handle your own sign-in.** The dashboard's password doesn't reach the addon, and an addon's
  pages open at its own port, without a dashboard session.
- **Work offline after installing.** Bundle fonts, scripts and maps, because the Pi may be on
  its hotspot with no internet.

## Ports in use

| Port | Used by |
| --- | --- |
| 22 | SSH |
| 80 | Dashboard |
| 8080, 8443 | RTW |
| 8081, 8082 | RTW, internal |
| 8384 | Syncthing admin, internal |
| 22000 | Syncthing sync |

Add your addon's ports to this table.

## Use addons on the Pi

The Addons screen handles install, update, start, stop, restart and remove. The same steps work
over SSH:

```bash
sudo pi5-addon install rtw
```

```bash
sudo pi5-addon remove rtw
```

```bash
journalctl -u 'pi5-addon-*' -n 50
```

- Installing and updating need internet, so the dashboard refuses them on the hotspot. Addons
  that are already installed keep running there, at `http://10.42.0.1:<port>`.
- Only one addon can be set up at a time.
- Removing deletes `ADDON_HOME` and keeps `ADDON_STORAGE`. To free that space too, delete
  `/srv/pi5/addons/<id>` over SSH.
- Packages installed with `apt` stay after removal, because something else may use them.
- The Home screen lists a stopped addon under Services, and Tech → Logs has an `addons` source.

## Security

Addons can only come from `/opt/pi5-addons`, which is root-owned and replaced on each deploy. The
dashboard never installs from a link typed into it, and it only passes known addon IDs to
`pi5-addon`. Installing a recipe runs its `install.sh` as root, so only add recipes you've read.

The dashboard's polkit rule lets the `pi5` user manage units named `pi5-addon-*`.

## Package a project with an AI assistant

[addon-master-prompt.md](addon-master-prompt.md) is a prompt to hand to a coding assistant to
prepare any project as an addon.
