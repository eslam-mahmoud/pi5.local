"""Optional apps that run beside the dashboard, each as its own systemd service.

ADDONS_DIR holds registry.json, the list of known addons, and one recipe folder for each
that's ready: addon.json, install.sh and addon.service. It's root-owned and ships with the
dashboard, so nothing else can be installed from here, and links are never accepted from
the browser.

The dashboard has no root rights. It starts the pi5-addon-install@ and pi5-addon-remove@
units, which run the pi5-addon helper, and reads how they went. See docs/addons.md.
"""
import asyncio
import json
import shutil
from pathlib import Path

from aiohttp import web

import network
from common import ADDONS_DIR, ADDONS_HOME, STORAGE_DIR, ApiError, run

UNIT_DIR = Path("/etc/systemd/system")
ACTIONS = {"start", "stop", "restart"}
CHECK_SECONDS = 1
ERROR_CHARS = 300
MB = 1024 * 1024


def registry():
    try:
        return json.loads((ADDONS_DIR / "registry.json").read_text())["addons"]
    except (OSError, ValueError, KeyError):
        return []


def manifests():
    """Addons that can be installed: listed as ready, with a recipe."""
    found = {}
    for entry in registry():
        if entry.get("status") != "ready":
            continue
        try:
            addon = json.loads((ADDONS_DIR / entry["id"] / "addon.json").read_text())
        except (OSError, ValueError):
            continue
        if addon.get("id") == entry["id"]:
            found[addon["id"]] = addon
    return found


def _service(addon_id):
    return f"pi5-addon-{addon_id}.service"


def _job(kind, addon_id):
    return f"pi5-addon-{kind}@{addon_id}.service"


def installed():
    """Installed addons' units and names, for the home page's service check."""
    return {
        _service(addon_id): addon["name"]
        for addon_id, addon in manifests().items()
        if (UNIT_DIR / _service(addon_id)).exists()
    }


async def _show(unit):
    _, out, _ = await run("systemctl", "show", unit, "--property=LoadState,ActiveState")
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


async def _answers(port):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), CHECK_SECONDS)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


async def _last_lines(unit):
    _, out, _ = await run("journalctl", "-u", unit, "-n", "4", "--no-pager", "-o", "cat")
    return out[-ERROR_CHARS:]


async def _state(addon):
    addon_id = addon["id"]
    install = await _show(_job("install", addon_id))
    remove = await _show(_job("remove", addon_id))
    if install.get("ActiveState") == "activating":
        return "installing", ""
    if remove.get("ActiveState") == "activating":
        return "removing", ""

    failed_job = next((k for k, s in (("install", install), ("remove", remove)) if s.get("ActiveState") == "failed"), None)
    message = await _last_lines(_job(failed_job, addon_id)) if failed_job else ""
    service = await _show(_service(addon_id))
    if service.get("LoadState") != "loaded":
        return ("failed" if failed_job else "available"), message
    active = service.get("ActiveState")
    if active == "active":
        return ("running" if await _answers(addon["port"]) else "problem"), message
    if active == "failed":
        return "failed", message or await _last_lines(_service(addon_id))
    return "stopped", message


def _planned(entry):
    return {
        "id": entry["id"],
        "name": entry["name"],
        "summary": entry["summary"],
        "icon": "addons",
        "homepage": entry.get("repository", ""),
        "credentials": False,
        "installed": False,
        "state": "planned",
        "message": "",
    }


async def overview(request):
    ready = manifests()
    addons = []
    for entry in registry():
        addon = ready.get(entry["id"])
        if not addon:
            addons.append(_planned(entry))
            continue
        state, message = await _state(addon)
        addons.append(
            {
                "id": addon["id"],
                "name": addon["name"],
                "summary": addon["summary"],
                "icon": addon.get("icon", "addons"),
                "homepage": addon.get("homepage", ""),
                "download": addon.get("download", ""),
                "port": addon["port"],
                "path": addon.get("path", "/"),
                "credentials": "credentials" in addon,
                "installed": (UNIT_DIR / _service(addon["id"])).exists(),
                "state": state,
                "message": message,
            }
        )
    return web.json_response({"addons": addons})


def _addon(request):
    addon = manifests().get(request.match_info["id"])
    if not addon:
        raise ApiError(404, "That addon isn't available")
    return addon


async def _start_job(kind, addon_id):
    for other in manifests():
        for job in ("install", "remove"):
            if (await _show(_job(job, other))).get("ActiveState") == "activating":
                raise ApiError(409, "Wait for the addon that's being set up to finish")
    code, _, error = await run("systemctl", "--no-ask-password", "--no-block", "start", _job(kind, addon_id))
    if code:
        raise ApiError(502, error or "Could not start that")
    return web.json_response({"ok": True}, status=202)


async def install(request):
    """Install an addon, or update one that's already installed."""
    addon = _addon(request)
    if (await network.current())["mode"] in ("hotspot", "offline"):
        raise ApiError(409, "This needs internet, so connect the Pi to WiFi first")
    if shutil.disk_usage(STORAGE_DIR).free < addon.get("needsMb", 0) * MB:
        raise ApiError(409, "Not enough free space on the Pi")
    return await _start_job("install", addon["id"])


async def remove(request):
    return await _start_job("remove", _addon(request)["id"])


async def action(request):
    addon = _addon(request)
    body = await request.json()
    verb = body.get("action")
    if verb not in ACTIONS:
        raise ApiError(400, "Unknown action")
    code, _, error = await run("systemctl", "--no-ask-password", verb, _service(addon["id"]), timeout=60)
    if code:
        raise ApiError(502, error or "That didn't work")
    return web.json_response({"ok": True})


async def credentials(request):
    addon = _addon(request)
    details = addon.get("credentials")
    if not details:
        raise ApiError(404, "This addon has no sign-in")
    try:
        password = (ADDONS_HOME / addon["id"] / details["file"]).read_text().strip()
    except OSError:
        raise ApiError(404, "No saved password. It may have been changed by hand")
    response = web.json_response({"user": details["user"], "password": password})
    response.headers["Cache-Control"] = "no-store"
    return response


def setup(app):
    app.router.add_get("/api/addons", overview)
    app.router.add_post("/api/addons/{id}/install", install)
    app.router.add_post("/api/addons/{id}/remove", remove)
    app.router.add_post("/api/addons/{id}/action", action)
    app.router.add_get("/api/addons/{id}/credentials", credentials)
