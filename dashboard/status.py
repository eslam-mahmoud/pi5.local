"""Live stats for the home page, and the power and service actions."""
import asyncio
import glob
import os
import shutil
import time
from pathlib import Path

from aiohttp import web

import addons
import files
import network
from common import STORAGE_DIR, ApiError, run

SIZE_SECONDS = 60  # walking every phone backup is too slow to do on each refresh
SERVICES = {"pi5-syncthing.service": "Phone sync", "wifi-fallback.timer": "WiFi fallback"}
ACTIONS = {
    "reboot": ("systemctl", "--no-ask-password", "reboot"),
    "poweroff": ("systemctl", "--no-ask-password", "poweroff"),
    "restart-sync": ("systemctl", "--no-ask-password", "restart", "pi5-syncthing.service"),
}

_previous = {}  # last CPU and network counters, to turn them into rates
_sizes = {"time": 0, "values": {}}


def _read_cpu():
    with open("/proc/stat") as f:
        values = [int(v) for v in f.readline().split()[1:9]]
    return sum(values), values[3] + values[4]


def _read_traffic():
    received = sent = 0
    with open("/proc/net/dev") as f:
        for line in f.readlines()[2:]:
            name, _, data = line.partition(":")
            if name.strip() != "lo":
                fields = data.split()
                received += int(fields[0])
                sent += int(fields[8])
    return received, sent


def cpu():
    load = os.getloadavg()[0]
    cores = os.cpu_count()
    try:
        total, idle = _read_cpu()
    except OSError:
        return {"percent": None, "load": load, "cores": cores}
    last_total, last_idle = _previous.get("cpu", (total, idle))
    _previous["cpu"] = (total, idle)
    spent = total - last_total
    percent = round((1 - (idle - last_idle) / spent) * 100) if spent else None
    return {"percent": percent, "load": load, "cores": cores}


def traffic():
    try:
        now, (received, sent) = time.monotonic(), _read_traffic()
    except OSError:
        return {"rx": None, "tx": None}
    last_time, last_received, last_sent = _previous.get("net", (now, received, sent))
    _previous["net"] = (now, received, sent)
    seconds = now - last_time
    if not seconds:
        return {"rx": None, "tx": None}
    return {
        "rx": round((received - last_received) / seconds),
        "tx": round((sent - last_sent) / seconds),
    }


def memory():
    try:
        with open("/proc/meminfo") as f:
            info = {k: int(v.split()[0]) * 1024 for k, v in (line.split(":") for line in f)}
    except OSError:
        return None
    return {"total": info["MemTotal"], "used": info["MemTotal"] - info["MemAvailable"]}


def uptime():
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except OSError:
        return None


def temperature():
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000)
    except (OSError, ValueError):
        return None


def under_voltage():
    """True when the power supply is too weak right now, None if unknown."""
    for path in glob.glob("/sys/class/hwmon/hwmon*/name"):
        directory = Path(path).parent
        if Path(path).read_text().strip() == "rpi_volt":
            return (directory / "in0_lcrit_alarm").read_text().strip() == "1"
    return None


def _tree_size(path):
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


async def sizes():
    if not _sizes["values"] or time.monotonic() - _sizes["time"] > SIZE_SECONDS:
        loop = asyncio.get_running_loop()
        _sizes["values"] = {
            root: await loop.run_in_executor(None, _tree_size, STORAGE_DIR / root)
            for root in files.ROOTS
        }
        _sizes["time"] = time.monotonic()
    return _sizes["values"]


async def services():
    result = []
    for unit, label in {**SERVICES, **addons.installed()}.items():
        _, state, _ = await run("systemctl", "is-active", unit)
        result.append({"label": label, "active": state == "active"})
    return result


async def status(request):
    disk = shutil.disk_usage(STORAGE_DIR)
    return web.json_response(
        {
            "network": await network.current(),
            "temperature": temperature(),
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
            "cpu": cpu(),
            "memory": memory(),
            "uptime": uptime(),
            "underVoltage": under_voltage(),
            "traffic": traffic(),
            "sizes": await sizes(),
            "services": await services(),
            "uploads": list(files.ACTIVE.values()),
            "downloads": request.app["downloads"].running(),
        }
    )


async def action(request):
    body = await request.json()
    command = ACTIONS.get(body.get("action"))
    if not command:
        raise ApiError(400, "Unknown action")
    reason = files.busy_reason()
    if reason and not body.get("force") and body["action"] != "restart-sync":
        raise ApiError(409, f"{reason} and this would stop it")
    code, _, error = await run(*command, timeout=30)
    if code:
        raise ApiError(500, error or "The Pi refused that action")
    return web.json_response({"ok": True})


def setup(app):
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/system/action", action)
