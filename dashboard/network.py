"""WiFi status and switching, through NetworkManager.

The Pi has one radio, so changing network drops whoever is connected over WiFi,
possibly including the browser that asked. Each switch therefore runs in the
background after the reply is sent, and goes back to the previous network if the
new one can't be joined.

The radio can't scan while it's a hotspot, so looking for networks from the hotspot
turns it off for a moment. The list is kept to show once the hotspot is back.

The fallback timer would undo a manual move to the hotspot by rejoining a known
network, so it's stopped for as long as the hotspot was chosen on purpose. A
reboot starts it again.
"""
import asyncio
import json
import re

from aiohttp import web

import files
from common import ApiError, run

WLAN = "wlan0"
HOTSPOT = "Hotspot"
FALLBACK_TIMER = "wifi-fallback.timer"
CONNECT_SECONDS = 45
SCAN_SECONDS = 30
SCAN_TRIES = 4
REPLY_SECONDS = 1.5  # time for the reply to reach the browser before the network drops


def _fields(line):
    """Split one line of nmcli's terse output, which escapes colons."""
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in re.split(r"(?<!\\):", line)]


async def _nmcli(*args, timeout=15):
    return await run("nmcli", *args, timeout=timeout)


async def _lines(*args):
    _, out, _ = await _nmcli("-t", *args)
    return [_fields(line) for line in out.splitlines()]


async def _ssid(connection):
    _, out, _ = await _nmcli("-g", "802-11-wireless.ssid", "connection", "show", connection)
    return out


async def _active_wifi():
    for fields in await _lines("-f", "NAME,TYPE", "connection", "show", "--active"):
        if len(fields) == 2 and "wireless" in fields[1]:
            return fields[0]
    return None


async def _timer(verb):
    return await run("systemctl", "--no-ask-password", verb, FALLBACK_TIMER)


async def _nearby(rescan):
    """Networks in range, strongest of each name, with nmcli's exit code and error."""
    code, out, error = await _nmcli(
        "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", WLAN,
        "--rescan", rescan, timeout=SCAN_SECONDS,
    )
    best = {}
    for fields in map(_fields, out.splitlines()):
        if len(fields) == 3 and fields[0]:
            ssid, signal, security = fields
            if ssid not in best or int(signal) > best[ssid]["signal"]:
                best[ssid] = {"ssid": ssid, "signal": int(signal), "open": not security}
    return code, best, error


async def _nearby_without_hotspot():
    await _nmcli("connection", "down", HOTSPOT)
    for _ in range(SCAN_TRIES):  # the radio takes a moment to allow scanning again
        await asyncio.sleep(2)
        code, best, _ = await _nearby("yes")
        if code == 0 and best:
            return best
    return {}


async def _hotspot_clients():
    _, out, _ = await run("ip", "-j", "neigh", "show", "dev", WLAN)
    neighbours = json.loads(out or "[]")
    return sum(1 for n in neighbours if n.get("lladdr") and "FAILED" not in n.get("state", []))


def _signal_dbm():
    try:
        with open("/proc/net/wireless") as f:
            for line in f:
                if line.strip().startswith(f"{WLAN}:"):
                    return int(float(line.split()[3]))
    except (OSError, ValueError, IndexError):
        pass
    return None


async def current():
    """The active network, for the header and the home page."""
    _, addresses, _ = await run("ip", "-j", "-4", "addr", "show", "scope", "global")
    ips = [
        info["local"]
        for link in json.loads(addresses or "[]")
        for info in link.get("addr_info", [])
    ]
    for fields in await _lines("-f", "NAME,TYPE", "connection", "show", "--active"):
        if len(fields) != 2 or fields[1] == "loopback":
            continue
        name, kind = fields
        if name == HOTSPOT:
            return {
                "mode": "hotspot",
                "name": await _ssid(name),
                "ips": ips,
                "clients": await _hotspot_clients(),
            }
        if "wireless" in kind:
            return {"mode": "wifi", "name": await _ssid(name), "ips": ips, "signal": _signal_dbm()}
        return {"mode": "ethernet", "name": name, "ips": ips}
    return {"mode": "offline", "name": "", "ips": ips}


class Switcher:
    def __init__(self):
        self.job = None  # {"target", "state", "message"}, plus "scanning" for a look around
        self.task = None
        self.found = {}  # the last networks seen, by name

    @property
    def busy(self):
        return bool(self.job) and self.job["state"] == "switching"

    def start(self, label, activate, undo=None):
        if self.busy:
            raise ApiError(409, "The Pi is already switching network")
        self.job = {"target": label, "state": "switching", "message": ""}
        self.task = asyncio.create_task(self._switch(label, activate, undo))

    async def _switch(self, label, activate, undo):
        await asyncio.sleep(REPLY_SECONDS)
        previous = await _active_wifi()
        to_hotspot = label == HOTSPOT
        if to_hotspot:
            await _timer("stop")
        code, _, error = await activate()
        if code == 0:
            if not to_hotspot:
                await _timer("start")
            self.job.update(state="done", message="")
            return
        if undo:
            await undo()
        if previous and await _active_wifi() != previous:
            await _nmcli(
                "--wait", str(CONNECT_SECONDS), "connection", "up", previous,
                timeout=CONNECT_SECONDS + 5,
            )
        await _timer("start")
        self.job.update(state="failed", message=error or "Could not connect")

    def look_around(self):
        if self.busy:
            raise ApiError(409, "The Pi is already switching network")
        self.job = {"target": HOTSPOT, "state": "switching", "message": "", "scanning": True}
        self.task = asyncio.create_task(self._look_around())

    async def _look_around(self):
        await asyncio.sleep(REPLY_SECONDS)
        found = await _nearby_without_hotspot()
        if found:
            self.found = found
        code, _, _ = await _nmcli(
            "--wait", str(CONNECT_SECONDS), "connection", "up", HOTSPOT, timeout=CONNECT_SECONDS + 5
        )
        if code:
            await _timer("start")  # it starts the hotspot when the Pi has no network
        message = f"Found {len(found)} networks. Open Network to see them." if found else "No networks found"
        self.job.update(state="done", message=message)


def _guard_transfers(body):
    reason = files.busy_reason()
    if reason and not body.get("force"):
        raise ApiError(409, f"{reason} and switching network would stop it")


async def overview(request):
    switcher = request.app["network"]
    now = await current()
    active = await _active_wifi()
    saved = []
    hotspot_ssid = ""
    for fields in await _lines("-f", "NAME,TYPE", "connection", "show"):
        if len(fields) != 2 or "wireless" not in fields[1]:
            continue
        name = fields[0]
        if name == HOTSPOT:
            hotspot_ssid = await _ssid(name)
        else:
            saved.append({"name": name, "ssid": await _ssid(name), "active": name == active})

    if active != HOTSPOT and not switcher.busy:
        code, found, _ = await _nearby("auto")
        if code == 0:
            switcher.found = found
    best = switcher.found
    known = {s["ssid"] for s in saved}
    for network in saved:
        network["signal"] = best.get(network["ssid"], {}).get("signal")

    return web.json_response(
        {
            "current": now,
            "saved": saved,
            "hotspot": {"ssid": hotspot_ssid, "active": active == HOTSPOT},
            "available": sorted(
                (n for n in best.values() if n["ssid"] not in known),
                key=lambda n: -n["signal"],
            ),
            "switch": switcher.job,
        }
    )


async def scan(request):
    switcher = request.app["network"]
    if switcher.busy:
        raise ApiError(409, "The Pi is already switching network")
    if await _active_wifi() == HOTSPOT:
        _guard_transfers(await request.json())
        switcher.look_around()
        return web.json_response({"ok": True}, status=202)
    code, found, error = await _nearby("yes")
    if code:
        raise ApiError(502, error or "Scan failed")
    switcher.found = found
    return web.json_response({"ok": True})


async def connect(request):
    """Move to a saved network, or to the hotspot."""
    body = await request.json()
    name = str(body.get("name", ""))
    _guard_transfers(body)
    names = [f[0] for f in await _lines("-f", "NAME,TYPE", "connection", "show") if "wireless" in f[-1]]
    if name not in names:
        raise ApiError(404, "That network isn't saved on the Pi")
    if name == await _active_wifi():
        raise ApiError(409, "The Pi is already on that network")

    async def activate():
        return await _nmcli(
            "--wait", str(CONNECT_SECONDS), "connection", "up", name, timeout=CONNECT_SECONDS + 5
        )

    request.app["network"].start(name, activate)
    return web.json_response({"ok": True}, status=202)


async def add(request):
    """Join a new network. It's only kept if joining works."""
    body = await request.json()
    ssid = str(body.get("ssid", ""))
    password = str(body.get("password", ""))
    _guard_transfers(body)
    if not 1 <= len(ssid.encode()) <= 32:
        raise ApiError(400, "Choose a network")
    if password and not 8 <= len(password) <= 63:
        raise ApiError(400, "WiFi passwords are 8 to 63 characters")
    existing = [f[0] for f in await _lines("-f", "NAME", "connection", "show")]
    if ssid in existing:
        raise ApiError(409, "That network is already saved")
    from_hotspot = await _active_wifi() == HOTSPOT

    async def activate():
        if from_hotspot:
            await _nearby_without_hotspot()  # NetworkManager only joins networks it has seen
        args = ["--wait", str(CONNECT_SECONDS), "device", "wifi", "connect", ssid, "ifname", WLAN]
        if password:
            args += ["password", password]
        return await _nmcli(*args, timeout=CONNECT_SECONDS + 5)

    async def undo():
        await _nmcli("connection", "delete", ssid)

    request.app["network"].start(ssid, activate, undo)
    return web.json_response({"ok": True}, status=202)


def setup(app):
    app["network"] = Switcher()
    app.router.add_get("/api/network", overview)
    app.router.add_post("/api/network/scan", scan)
    app.router.add_post("/api/network/connect", connect)
    app.router.add_post("/api/network/add", add)
