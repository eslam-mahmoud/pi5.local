# Addon master prompts

Prompts to paste into a coding assistant. Fill in anything in `<angle brackets>` first.

## Package a new project as an addon

Open the assistant in the [pi5.local](https://github.com/eslam-mahmoud/pi5.local) repository,
because that's where the recipe goes.

```text
Package <project name> as a pi5.local addon.

Project repository: <URL>
Addon ID: <lowercase-id>
Port: <free port>

Read docs/addons.md first and follow it exactly. addons/rtw is the working example. Then read
the project's README and start scripts to learn what it installs, what it runs and where it
keeps data.

Add the project to addons/registry.json as planned, and create addons/<id>/ with addon.json,
install.sh and addon.service. Don't change the project's own repository, and don't change the
dashboard, the pi5-addon helper or the polkit rule. If the project can't run without a change on
its side, stop and tell me what it needs. Change its status to ready only after the tests below
pass, and run python3 system/check-addons.py before you finish.

The target is a Raspberry Pi 5 with 1 GB of RAM running Debian 13 (trixie) arm64, under
systemd, shared with the dashboard and Syncthing. Check these before you finish:

- Which packages are in Debian trixie for arm64, and whether any of them starts a service on
  install. Port 80 belongs to the dashboard, so mask any service that would claim it.
- Which ports the project uses, against the table in docs/addons.md. Add the new ones there.
- That install.sh is safe to run again, because it's also the update, and keeps all user data.
- That the project runs as the addon's own user, with data in ADDON_STORAGE and everything
  else in ADDON_HOME.
- What happens after a power cut. Clear stale process IDs and lock files on start.
- A MemoryMax that fits on this Pi, and the hardening lines from the guide.
- That the app works offline after installing, since the Pi may be on its hotspot.
- If it has a sign-in, generate the first password in install.sh, never commit one, and use
  the credentials field so the dashboard can show it.

Then deploy and test on the real Pi, through the dashboard's API rather than by hand:
install, open it from another device, sign in, add real content, stop, start, restart, update,
remove, install again, and reboot. Tell me exactly what passed, what you couldn't test, and
what size to put in the download field. Don't commit or push until I ask.
```

## Optional: improve RTW for the Pi

RTW already runs on the Pi without changes, so nothing here is required. Open the assistant in
the [RTW repository](https://github.com/eslam-mahmoud/reinvent-the-wheel) only if you want
these. After they land, simplify `addons/rtw` in the pi5.local repository to use them.

```text
RTW runs on a Raspberry Pi 5 as a pi5.local addon, through a recipe in the other repository:
https://github.com/eslam-mahmoud/pi5.local/tree/main/addons/rtw

That recipe works around three gaps in RTW. Close them, keeping macOS and Termux working
exactly as they do now:

1. Linux support in `rtw setup`. It only handles macOS and Termux, so the recipe installs
   packages and calls server/configure.sh itself. Add a Debian path that uses apt, and keep
   setup free of prompts when there's no terminal.

2. A foreground mode, such as `rtw run`, for service managers. `rtw start` backgrounds nginx,
   Kiwix and the admin service and exits, so systemd can't see a crash or restart anything. On
   the Pi a dead nginx stays down until someone presses Restart. The new mode should stay
   running, stop everything if one service dies, and exit non-zero so systemd can restart it.
   Keep `rtw start` as it is.

3. Setting the admin password without a terminal. server/passwd.py needs a terminal, so the
   recipe writes the htpasswd entry itself. Accept the password on stdin, for example
   `rtw passwd --stdin`, and never as a command-line argument.

Also treat these paths as a public interface, or tell me when they change, because the recipe
depends on them: server/configure.sh and its ROOT, CONTENT and MIME_TYPES variables,
scripts/fetch-vendor.sh, and state/conf/htpasswd.

Update the README and docs, test on macOS and on Linux, and describe anything you couldn't
test. Don't commit or push until I ask.
```
