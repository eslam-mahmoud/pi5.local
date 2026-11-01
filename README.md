<p align="center">
  <img src="dashboard/static/icon-180.png" width="96" height="96" alt="">
</p>

<h1 align="center">pi5.local</h1>

<p align="center">
  A phone-style web dashboard that turns a Raspberry Pi 5 into your own small home server.
</p>

<p align="center">
  <a href="https://github.com/eslam-mahmoud/pi5.local/actions/workflows/ci.yml"><img src="https://github.com/eslam-mahmoud/pi5.local/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/Raspberry%20Pi-5-c51a4a" alt="Raspberry Pi 5">
  <img src="https://img.shields.io/badge/frontend-no%20dependencies-2dd4bf" alt="No frontend dependencies">
</p>

<p align="center">
  <img src="docs/screenshots/overview.png" alt="The Home, Network, Downloads and Phones screens">
</p>

Open `http://pi5.local` from any device on the same network to see the Pi's health, switch WiFi,
store files, back up phones and download video or audio. It keeps working with no internet,
because the Pi falls back to its own hotspot.

The backend is one small Python service. The frontend is plain HTML, CSS and JavaScript modules
in `dashboard/static`, with no libraries and no build step. It can be added to a phone's home
screen, and wide screens get a side rail instead of the bottom tabs.

## What it does

- **Home:** live stats, switch or join WiFi, start the hotspot, restart or shut down the Pi.
- **Files:** upload, download and organize preset files.
- **Downloads:** video and audio from [yt-dlp](https://github.com/yt-dlp/yt-dlp), sorted into folders.
- **Phones:** one-way backup with [Syncthing](https://syncthing.net), each phone in its own folder.
- **Tech:** read-only system detail for technical users.
- **RTW:** a reserved tab. RTW is a separate project with its own repository.

## Screenshots

| Home | Network | Files |
| --- | --- | --- |
| <img src="docs/screenshots/home.png" width="240" alt="Home screen with network, stats and actions"> | <img src="docs/screenshots/network.png" width="240" alt="Network sheet with saved and nearby networks"> | <img src="docs/screenshots/files.png" width="240" alt="Preset files list"> |

| Downloads | Phones | Tech |
| --- | --- | --- |
| <img src="docs/screenshots/downloads.png" width="240" alt="Download form and progress"> | <img src="docs/screenshots/phones.png" width="240" alt="Synced phones and their folders"> | <img src="docs/screenshots/tech.png" width="240" alt="Technical overview of the Pi"> |

<details>
<summary>Desktop and light theme</summary>

<img src="docs/screenshots/desktop.png" alt="Home screen on a wide display with a side rail">

<img src="docs/screenshots/home-light.png" width="240" alt="Home screen in the light theme">

</details>

Screenshots use demo data. The theme follows your device's light or dark setting.

## Requirements

- Raspberry Pi 5 with 64-bit Raspberry Pi OS Lite (tested on Debian 13 trixie, 1 GB RAM)
- NetworkManager managing `wlan0`
- SSH key access from your computer to a user with passwordless `sudo`
- Internet on the Pi while installing

## Install

Create the hotspot once on the Pi. The fallback looks for a profile named `Hotspot`:

```bash
sudo nmcli connection add type wifi ifname wlan0 con-name Hotspot autoconnect no ssid pi5-ap mode ap ipv4.method shared 802-11-wireless.band bg wifi-sec.key-mgmt wpa-psk wifi-sec.psk 'choose-a-password'
```

Then, from your computer, say which user to connect as and deploy:

```bash
echo 'PI=pi@pi5.local' > deploy.env
```

```bash
./deploy.sh
```

This copies the project to the Pi and runs `system/install.sh`, which is safe to repeat. It keeps
the password, the paired phones and all stored files. `deploy.env` stays out of git.

To update code while the Pi has no internet, join its hotspot first. The installer's `apt` step
needs internet, so copy `dashboard/` to `/opt/pi5-dashboard` and restart `pi5-dashboard` instead.

## Addresses

| Network | Address |
| --- | --- |
| WiFi | `http://pi5.local`, or the address shown on the Home page |
| `pi5-ap` hotspot | `http://pi5.local` or `http://10.42.0.1` |

The dashboard listens on all interfaces, so it needs no change when the network switches.

If a Mac browser shows `ERR_ADDRESS_UNREACHABLE` or "Unable to connect", allow it under
System Settings → Privacy & Security → Local Network, then relaunch the browser.

## Project layout

| Path | Contents |
| --- | --- |
| `dashboard/` | Python service, one module per area |
| `dashboard/static/` | Web app |
| `system/` | Installer, systemd units, polkit rule and the WiFi fallback |
| `deploy.sh` | Copies the project to the Pi and installs it |

## Layout on the Pi

| Path | Contents |
| --- | --- |
| `/opt/pi5-dashboard` | App code, replaced on each deploy |
| `/srv/pi5/presets` | Preset files. Upload in the dashboard, or copy here over SSH |
| `/srv/pi5/downloads` | Videos and audio from the Downloads screen |
| `/srv/pi5/phones/<phone>/<folder>` | Synced phone files |
| `/srv/pi5/phones/<phone>/<folder>/.stversions` | Older copies of files a phone edited or deleted |
| `/var/lib/pi5` | Password hash, phone list and Syncthing config |

Services: `pi5-dashboard` on port 80 and `pi5-syncthing` on port 22000. Both run as the
unprivileged `pi5` user. The Syncthing admin page is only reachable from the Pi itself.

## Network

`wifi-fallback.timer` checks the connection every 2 minutes and starts the hotspot when `wlan0`
has no network.

- Choosing the hotspot pauses `wifi-fallback.timer`, or it would rejoin WiFi by itself. It
  starts again when you connect to WiFi or reboot.
- If a network can't be joined, the Pi goes back to the previous one, and a new network
  isn't saved.
- The radio can't scan while it's a hotspot. Scanning from the hotspot turns it off for
  about 20 seconds, so connected devices drop and rejoin, then the list appears under Network.
- Anything that would stop a running upload asks first.
- Shut down turns the Pi off. Press its power button or replug the power to start it.

## Downloads

Files → Downloads fetches one item at a time and sorts everything into folders:

```text
Videos/<channel>/<title> [<id>].mp4
Videos/<channel>/<playlist>/001 - <title> [<id>].mp4
Audio/<channel>/<title> [<id>].mp3
```

A folder name typed with a download replaces `<channel>`. The ID in each filename keeps two
videos with the same title apart.

- yt-dlp is the official release in `/var/lib/pi5/bin`, because Debian's version is too old
  for YouTube. Use **Update** on the Downloads screen if a site stops working.
- It uses `ffmpeg` for merging and converting, and Node.js for YouTube.
- A download stops if the Pi has less than 1 GB free, and new ones are refused until there's room.
- A running download survives a page reload, but not a dashboard or Pi restart.
- Only download content you have the right to save.

## Tech tab

- **Overview:** system, per-core CPU, clocks, voltages, throttle flags, temperatures, fan,
  memory, disks and I/O, network interfaces.
- **Processes:** top 15 by CPU. It shows names only, because command lines can hold secrets.
- **Services:** every systemd service, grouped and searchable.
- **GPIO:** the 40-pin header from `pinctrl -p`. Pins can't be changed from the dashboard.
- **Devices:** USB devices and Docker containers.
- **Logs:** recent journal entries since the last boot.

The dashboard joins the `video`, `gpio` and `systemd-journal` groups to read these.

## Phone sync rules

- A phone syncs nothing until it is approved in the dashboard.
- Each phone gets its own directory, even if two phones share a name.
- A folder ID already used by another phone is refused, because Syncthing would merge them.
- The Pi is receive-only and never changes anything on a phone.
- Sync works on the home WiFi or the hotspot only. Relays and internet discovery are off.
- `.stversions` is never cleaned automatically, so it keeps growing. Syncthing stops writing
  when the card has less than 5% free.

## Password and security

The first install generates a password and saves it in the deploying user's home on the Pi:

```bash
ssh pi@pi5.local cat pi5-dashboard-password.txt
```

To change it:

```bash
ssh -t pi@pi5.local 'sudo -u pi5 python3 /opt/pi5-dashboard/app.py --set-password && sudo systemctl restart pi5-dashboard && rm -f ~/pi5-dashboard-password.txt'
```

The dashboard is meant for a trusted local network. It uses plain HTTP, so don't expose it to
the internet. What the `pi5` user may do is limited by `system/50-pi5-dashboard.rules`.

## Adding a section

Add `dashboard/<name>.py` with a `setup(app)` function like the other modules and call it from
`create_app()` in `app.py`. Add `static/js/<name>.js` exporting `show()`, which returns the
screen's nodes, and register it in `static/js/main.js`.

## Project status

pi5.local is new and has been developed and tested on one Raspberry Pi 5 with 1 GB of RAM. See
the [changelog](CHANGELOG.md) for what's included.

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) to run it
locally, and [SECURITY.md](SECURITY.md) to report a security problem.

## License

[MIT](LICENSE)
