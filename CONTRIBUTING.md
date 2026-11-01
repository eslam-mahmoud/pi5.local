# Contributing

Bug reports and pull requests are welcome. For a large change, open an issue first so we can
agree on the approach.

To add another app, you usually want an addon. See the [addon guide](docs/addons.md).

## Run it locally

The web app runs on any computer. Stats and network features need a real Pi, but the screens,
login, files and downloads form all work.

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install aiohttp
```

```bash
mkdir -p dev/data dev/storage
```

```bash
export PI5_DATA_DIR="$PWD/dev/data" PI5_STORAGE_DIR="$PWD/dev/storage" PI5_PORT=8080
```

```bash
.venv/bin/python dashboard/app.py --set-password
```

```bash
.venv/bin/python dashboard/app.py
```

Then open `http://localhost:8080`. The `dev/` folder stays out of git.

## Project rules

- **No frontend dependencies and no build step.** The app has to load on the hotspot with no
  internet.
- **Keep the backend small.** `aiohttp` is the only Python dependency, installed from Debian.
- **Use text nodes for page content.** Names of files, networks and videos come from outside,
  so never build HTML from strings.
- **Check every option against a fixed list** before it reaches a system command, and pass
  commands as argument lists, never through a shell.
- **Keep the `pi5` user's rights narrow.** A new privilege needs a reason in the pull request.
- **Ask first in the UI** before anything that interrupts a running upload or download.

## Before you open a pull request

These are the checks CI runs:

```bash
python3 -m compileall -q dashboard
```

```bash
python3 system/check-addons.py
```

```bash
shellcheck --severity=warning deploy.sh system/install.sh system/wifi-fallback system/pi5-addon addons/*/*.sh
```

If you changed network code, test it on a real Pi. SSH usually runs over the same WiFi, so run
anything that touches `wlan0` as a detached `systemd-run` job and read its log afterwards.

Describe what you tested in the pull request, including what you couldn't test.
