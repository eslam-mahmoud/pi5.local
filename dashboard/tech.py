"""Raw system data for the technical section. Everything here is read-only."""
import asyncio
import json
import os
import pwd
import re
import shutil
import socket
import time
from pathlib import Path

from aiohttp import web

from common import ApiError, run

CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE_BYTES = os.sysconf("SC_PAGE_SIZE")
SECTOR_BYTES = 512
TOP_PROCESSES = 15
SERVICE_SECONDS = 10
LOG_LINES = 200

THROTTLE_FLAGS = {
    0: "Under-voltage",
    1: "Frequency capped",
    2: "Throttled",
    3: "Soft temperature limit",
}
LOGS = {
    "pi5-dashboard": ("-u", "pi5-dashboard"),
    "pi5-syncthing": ("-u", "pi5-syncthing"),
    "NetworkManager": ("-u", "NetworkManager"),
    "wifi-fallback": ("-u", "wifi-fallback"),
    "addons": ("-u", "pi5-addon-*"),
    "ssh": ("-u", "ssh"),
    "kernel": ("-k",),
}

_last = {}  # previous counters, to turn totals into rates
_services = {"time": None, "value": {}}


def _text(path, default=""):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeDecodeError):
        return default


def _number(path):
    try:
        return int(_text(path))
    except ValueError:
        return None


def _rates(key, counters):
    """Turn {name: (total, total)} into {name: (per second, per second)}."""
    now = time.monotonic()
    then, previous = _last.get(key, (now, {}))
    _last[key] = (now, counters)
    seconds = now - then
    return {
        name: tuple(
            max(0, round((value - before) / seconds)) if seconds else 0
            for value, before in zip(values, previous.get(name, values))
        )
        for name, values in counters.items()
    }


def _hwmon(name):
    for path in Path("/sys/class/hwmon").glob("hwmon*/name"):
        if _text(path) == name:
            return path.parent
    return None


def system():
    release = dict(
        line.split("=", 1) for line in _text("/etc/os-release").splitlines() if "=" in line
    )
    try:
        uptime = int(float(_text("/proc/uptime").split()[0]))
    except (IndexError, ValueError):
        uptime = None
    return {
        "hostname": socket.gethostname(),
        "model": _text("/proc/device-tree/model").rstrip("\0"),
        "os": release.get("PRETTY_NAME", "").strip('"'),
        "kernel": os.uname().release,
        "arch": os.uname().machine,
        "uptime": uptime,
    }


def _cpu_times():
    times = {}
    for line in _text("/proc/stat").splitlines():
        if not line.startswith("cpu"):
            break
        fields = line.split()
        values = [int(v) for v in fields[1:9]]
        times[fields[0]] = (sum(values), values[3] + values[4])
    return times


def cpu():
    now = _cpu_times()
    previous = _last.get("cpu", {})
    _last["cpu"] = now

    def percent(name):
        total, idle = now[name]
        last_total, last_idle = previous.get(name, (0, 0))
        spent = total - last_total
        return round((1 - (idle - last_idle) / spent) * 100) if spent else 0

    cores = [name for name in now if name != "cpu"]
    return {
        "percent": percent("cpu") if "cpu" in now else None,
        "cores": [
            {
                "percent": percent(name),
                "mhz": (_number(f"/sys/devices/system/cpu/{name}/cpufreq/scaling_cur_freq") or 0) // 1000,
            }
            for name in cores
        ],
        "governor": _text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "load": list(os.getloadavg()),
    }


def memory():
    info = {}
    for line in _text("/proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        info[key] = int(value.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    return {
        "total": total,
        "used": total - info.get("MemAvailable", 0),
        "buffers": info.get("Buffers", 0),
        "cached": info.get("Cached", 0),
        "swapTotal": info.get("SwapTotal", 0),
        "swapUsed": info.get("SwapTotal", 0) - info.get("SwapFree", 0),
    }


def disks():
    seen = set()
    mounts = []
    for line in _text("/proc/mounts").splitlines():
        device, mount, fstype = line.split()[:3]
        if not device.startswith("/dev/") or device in seen or fstype == "squashfs":
            continue
        seen.add(device)
        try:
            usage = shutil.disk_usage(mount.replace("\\040", " "))
        except OSError:
            continue
        mounts.append(
            {
                "mount": mount,
                "device": device,
                "fstype": fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
            }
        )
    return mounts


def disk_io():
    counters = {}
    for line in _text("/proc/diskstats").splitlines():
        fields = line.split()
        name = fields[2]
        if re.fullmatch(r"mmcblk\d+|sd[a-z]+|nvme\d+n\d+", name):
            counters[name] = (int(fields[5]) * SECTOR_BYTES, int(fields[9]) * SECTOR_BYTES)
    return [
        {"device": name, "read": read, "write": write}
        for name, (read, write) in _rates("disk", counters).items()
    ]


def interfaces(addresses):
    ips = {
        link["ifname"]: [f"{a['local']}/{a['prefixlen']}" for a in link.get("addr_info", [])]
        for link in addresses
    }
    totals = {}
    details = {}
    for path in Path("/sys/class/net").glob("*"):
        name = path.name
        if name == "lo":
            continue
        rx = _number(path / "statistics/rx_bytes") or 0
        tx = _number(path / "statistics/tx_bytes") or 0
        totals[name] = (rx, tx)
        details[name] = {
            "name": name,
            "state": _text(path / "operstate"),
            "mac": _text(path / "address"),
            "ips": ips.get(name, []),
            "rxTotal": rx,
            "txTotal": tx,
        }
    for name, (rx, tx) in _rates("net", totals).items():
        details[name].update(rx=rx, tx=tx)
    return sorted(details.values(), key=lambda d: d["name"])


def fan():
    directory = _hwmon("pwmfan")
    if not directory:
        return None
    level = _number(directory / "pwm1")
    return {
        "rpm": _number(directory / "fan1_input"),
        "percent": round(level / 255 * 100) if level is not None else None,
    }


def temperatures():
    readings = {}
    cpu_zone = _number("/sys/class/thermal/thermal_zone0/temp")
    if cpu_zone is not None:
        readings["CPU"] = round(cpu_zone / 1000, 1)
    rp1 = _hwmon("rp1_adc")
    value = _number(rp1 / "temp1_input") if rp1 else None
    if value is not None:
        readings["RP1 chip"] = round(value / 1000, 1)
    return readings


async def _vc(*args):
    _, out, _ = await run("vcgencmd", *args, timeout=5)
    return out


async def videocore():
    arm, core, v3d, volts, ram_volts, throttled, pmic, supply, firmware = await asyncio.gather(
        _vc("measure_clock", "arm"),
        _vc("measure_clock", "core"),
        _vc("measure_clock", "v3d"),
        _vc("measure_volts", "core"),
        _vc("measure_volts", "sdram_c"),
        _vc("get_throttled"),
        _vc("measure_temp", "pmic"),
        _vc("pmic_read_adc", "EXT5V_V"),
        _vc("version"),
    )

    def after_equals(text, pattern):
        match = re.search(pattern, text)
        return float(match.group(1)) if match else None

    def mhz(text):
        hertz = after_equals(text, r"=(\d+)")
        return round(hertz / 1_000_000) if hertz else None

    match = re.search(r"=0x([0-9a-fA-F]+)", throttled)
    bits = int(match.group(1), 16) if match else None
    return {
        "armMhz": mhz(arm),
        "coreMhz": mhz(core),
        "v3dMhz": mhz(v3d),
        "coreVolts": after_equals(volts, r"=([\d.]+)V"),
        "ramVolts": after_equals(ram_volts, r"=([\d.]+)V"),
        "supplyVolts": after_equals(supply, r"=([\d.]+)V"),
        "pmicTemp": after_equals(pmic, r"=([\d.]+)"),
        "firmware": firmware.splitlines()[0] if firmware else "",
        "throttled": None
        if bits is None
        else {
            "raw": f"0x{bits:x}",
            "now": [label for bit, label in THROTTLE_FLAGS.items() if bits >> bit & 1],
            "sinceBoot": [label for bit, label in THROTTLE_FLAGS.items() if bits >> (bit + 16) & 1],
        },
    }


def _username(uid, names={}):
    if uid not in names:
        try:
            names[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:
            names[uid] = str(uid)
    return names[uid]


def _read_processes():
    # Names only: full command lines can contain passwords.
    found = {}
    for path in Path("/proc").glob("[0-9]*"):
        try:
            raw = (path / "stat").read_text()
            owner = path.stat().st_uid
        except OSError:
            continue
        name = raw[raw.index("(") + 1 : raw.rindex(")")]
        fields = raw[raw.rindex(")") + 2 :].split()
        found[int(path.name)] = {
            "name": name,
            "state": fields[0],
            "ticks": int(fields[11]) + int(fields[12]),
            "threads": int(fields[17]),
            "rss": int(fields[21]) * PAGE_BYTES,
            "user": _username(owner),
        }
    return found


def processes():
    now = time.monotonic()
    found = _read_processes()
    then, previous = _last.get("proc", (now, {}))
    _last["proc"] = (now, found)
    seconds = now - then
    rows = []
    for pid, process in found.items():
        last = previous.get(pid)
        spent = process["ticks"] - last["ticks"] if last else 0
        rows.append(
            {
                "pid": pid,
                "name": process["name"],
                "user": process["user"],
                "state": process["state"],
                "threads": process["threads"],
                "rss": process["rss"],
                "cpu": round(spent / CLK_TCK / seconds * 100, 1) if seconds else 0,
            }
        )
    rows.sort(key=lambda r: (-r["cpu"], -r["rss"]))
    return {"total": len(rows), "top": rows[:TOP_PROCESSES]}


async def services():
    now = time.monotonic()
    if _services["time"] is None or now - _services["time"] > SERVICE_SECONDS:
        _, out, _ = await run(
            "systemctl", "list-units", "--type=service", "--all", "--no-pager", "--output=json"
        )
        groups = {"failed": [], "running": [], "inactive": []}
        for unit in json.loads(out or "[]"):
            if unit["active"] == "failed":
                group = "failed"
            elif unit["active"] == "active":
                group = "running"
            else:
                group = "inactive"
            groups[group].append(
                {
                    "unit": unit["unit"].removesuffix(".service"),
                    "state": unit["sub"],
                    "description": unit["description"],
                }
            )
        _services.update(time=now, value=groups)
    return _services["value"]


def usb():
    devices = []
    for path in sorted(Path("/sys/bus/usb/devices").glob("*")):
        if not (path / "idVendor").exists():
            continue
        vendor = _text(path / "idVendor")
        devices.append(
            {
                "port": path.name,
                "id": f"{vendor}:{_text(path / 'idProduct')}",
                "maker": _text(path / "manufacturer"),
                "product": _text(path / "product"),
                "mbps": _number(path / "speed"),
                "rootHub": vendor == "1d6b",
            }
        )
    return devices


async def docker():
    code, out, error = await run("docker", "ps", "--format", "{{json .}}", timeout=5)
    if code == 127:
        return {"state": "missing", "containers": []}
    if code:
        return {"state": "denied", "detail": error, "containers": []}
    containers = [json.loads(line) for line in out.splitlines()]
    return {
        "state": "ok",
        "containers": [
            {"name": c.get("Names"), "image": c.get("Image"), "status": c.get("Status")}
            for c in containers
        ],
    }


async def overview(request):
    loop = asyncio.get_running_loop()
    (_, addresses, _), vc, service_groups, docker_state, process_list = await asyncio.gather(
        run("ip", "-j", "addr"),
        videocore(),
        services(),
        docker(),
        loop.run_in_executor(None, processes),
    )
    return web.json_response(
        {
            "system": system(),
            "cpu": cpu(),
            "memory": memory(),
            "temperatures": temperatures(),
            "videocore": vc,
            "fan": fan(),
            "disks": disks(),
            "diskIo": disk_io(),
            "network": interfaces(json.loads(addresses or "[]")),
            "processes": process_list,
            "services": service_groups,
            "usb": usb(),
            "docker": docker_state,
            "logs": list(LOGS),
        }
    )


PIN_LINE = re.compile(
    r"^\s*(?P<pin>\d+):\s*(?P<mode>\S+)\s+(?P<pull>\S+)?\s*\|\s*(?P<level>\S+)\s*//\s*"
    r"(?P<name>\S+)\s*=\s*(?P<function>\S+)"
)
POWER_LINE = re.compile(r"^\s*(?P<pin>\d+):\s*(?P<rail>\S+)\s*$")


def parse_pins(text):
    pins = []
    for line in text.splitlines():
        match = PIN_LINE.match(line)
        if match:
            pin = match.groupdict()
            pins.append({**pin, "pin": int(pin["pin"]), "kind": "gpio"})
            continue
        match = POWER_LINE.match(line)
        if match:
            pins.append({"pin": int(match["pin"]), "kind": match["rail"]})
    return pins


async def gpio(request):
    code, out, error = await run("pinctrl", "-p")
    if code:
        raise ApiError(502, error or "pinctrl failed")
    return web.json_response({"pins": parse_pins(out)})


async def logs(request):
    source = LOGS.get(request.query.get("source"))
    if not source:
        raise ApiError(404, "Unknown log")
    _, out, error = await run(
        "journalctl", *source, "-n", str(LOG_LINES), "--no-pager", "--no-hostname", "-o", "short-iso"
    )
    return web.json_response({"lines": out.splitlines() or [error or "No entries"]})


def setup(app):
    app.router.add_get("/api/tech", overview)
    app.router.add_get("/api/tech/gpio", gpio)
    app.router.add_get("/api/tech/logs", logs)
