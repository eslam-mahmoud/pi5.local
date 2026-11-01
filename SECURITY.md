# Security policy

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately through
[GitHub security advisories](https://github.com/eslam-mahmoud/pi5.local/security/advisories/new)
with the steps to reproduce it and what an attacker could do.

Only the latest version on `main` is supported.

## What the dashboard is designed for

pi5.local is meant for a trusted local network or its own hotspot.

- **Traffic is plain HTTP**, so anyone able to read the network can read it. Don't expose the
  dashboard to the internet or forward its port from a router.
- **There is one shared password.** Everyone who signs in has the same rights.
- **The service runs as the unprivileged `pi5` user** under systemd hardening. Its system rights
  are limited by `system/50-pi5-dashboard.rules`.
- **Downloads run yt-dlp on links you paste**, so only use links you trust.
- **Addon recipes run as root when installed.** Only recipes shipped in `addons/` can be
  installed, never a link from the browser, so read a recipe before you add it. Each addon then
  runs as its own unprivileged user.

## Keep these out of the repository

The Pi's dashboard password, hotspot password and `deploy.env` are not part of the project.
`.gitignore` covers the usual files, but check what you stage before you commit.
