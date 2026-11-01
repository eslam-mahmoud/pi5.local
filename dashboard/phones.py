"""Phone sync on top of Syncthing.

Syncthing does the transfer. This module adds the rules that keep phones apart:

- a phone must be approved in the dashboard before anything syncs
- each phone gets its own directory under phones/, never shared or reused
- folders a phone offers are accepted automatically into that directory
- a folder ID already used by another phone is refused, because Syncthing would
  merge the two phones into one folder
- Pi folders are receive-only, and anything a phone changes or deletes is kept
  in that folder's .stversions
"""
import asyncio
import json
import re
import xml.etree.ElementTree as ET

import aiohttp
from aiohttp import web

from common import PHONES_FILE, STORAGE_DIR, SYNCTHING_HOME, SYNCTHING_URL, ApiError

POLL_SECONDS = 10
PHONES_DIR = STORAGE_DIR / "phones"
DEVICE_ID = re.compile(r"^[A-Z2-7]{7}(-[A-Z2-7]{7}){7}$")

# LAN only: phones sync on the home WiFi or the hotspot, nothing goes through relays.
OPTIONS = {
    "globalAnnounceEnabled": False,
    "relaysEnabled": False,
    "natEnabled": False,
    "localAnnounceEnabled": True,
    "urAccepted": -1,
    "crashReportingEnabled": False,
    "autoUpgradeIntervalH": 0,
    "startBrowser": False,
    "minHomeDiskFree": {"value": 5, "unit": "%"},
}


class SyncthingDown(Exception):
    pass


class Syncthing:
    """Minimal client for the REST API on localhost."""

    def __init__(self):
        self.session = None
        self.api_key = None

    def _read_api_key(self):
        try:
            tree = ET.parse(SYNCTHING_HOME / "config.xml")
        except (OSError, ET.ParseError) as error:
            raise SyncthingDown(str(error))
        return tree.findtext("gui/apikey")

    async def request(self, method, path, raw=False, **kwargs):
        self.api_key = self.api_key or self._read_api_key()
        headers = {"X-API-Key": self.api_key or ""}
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with self.session.request(
                method, SYNCTHING_URL + path, headers=headers, timeout=timeout, **kwargs
            ) as response:
                if response.status == 403:
                    self.api_key = None  # regenerated config
                if response.status >= 400:
                    detail = (await response.text()).strip()
                    raise ApiError(502, f"Syncthing refused the change: {detail}")
                if raw:
                    return await response.read()
                if response.content_type == "application/json":
                    return await response.json()
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise SyncthingDown(str(error))

    async def get(self, path, **kwargs):
        return await self.request("GET", path, **kwargs)


def slug(text, fallback):
    cleaned = re.sub(r"[^\w.-]+", "-", text or "", flags=re.UNICODE).strip("-._")
    return cleaned[:60] or fallback


def unique_dir(parent, name):
    """A directory name nothing has ever used, so old data is never mixed in."""
    candidate, n = name, 1
    while (parent / candidate).exists():
        n += 1
        candidate = f"{name}-{n}"
    return candidate


class PhoneSync:
    def __init__(self):
        self.st = Syncthing()
        self.phones = {}  # deviceID -> {"name", "dir"}
        self.issues = []
        self.configured = False
        self._lock = asyncio.Lock()  # one writer of Syncthing's config at a time
        self._load()

    def _load(self):
        if PHONES_FILE.exists():
            self.phones = json.loads(PHONES_FILE.read_text())

    def _save(self):
        tmp = PHONES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.phones, indent=2, ensure_ascii=False))
        tmp.replace(PHONES_FILE)

    async def configure(self):
        status = await self.st.get("/rest/system/status")
        await self.st.request("PATCH", "/rest/config/options", json=OPTIONS)
        await self.st.request(
            "PATCH", f"/rest/config/devices/{status['myID']}", json={"name": "pi5"}
        )
        pending = await self.st.get("/rest/config/restart-required")
        if pending.get("requiresRestart"):
            await self.st.request("POST", "/rest/system/restart")
            return
        self.configured = True

    async def add_folder(self, device_id, folder_id, label):
        phone_dir = PHONES_DIR / self.phones[device_id]["dir"]
        name = unique_dir(phone_dir, slug(label, slug(folder_id, "folder")))
        await self.st.request(
            "POST",
            "/rest/config/folders",
            json={
                "id": folder_id,
                "label": label or folder_id,
                "path": str(phone_dir / name),
                "type": "receiveonly",
                "devices": [{"deviceID": device_id}],
                "versioning": {"type": "trashcan", "params": {"cleanoutDays": "0"}},
                "minDiskFree": {"value": 5, "unit": "%"},
                "fsWatcherEnabled": True,
                "rescanIntervalS": 3600,
                "ignorePerms": True,
            },
        )

    def _owner(self, folder):
        for device in folder["devices"]:
            if device["deviceID"] in self.phones:
                return self.phones[device["deviceID"]]["name"]
        return "the Pi"

    async def accept_folders(self):
        async with self._lock:
            pending = await self.st.get("/rest/cluster/pending/folders")
            folders = {f["id"]: f for f in await self.st.get("/rest/config/folders")}
            issues = []
            for folder_id, info in pending.items():
                for device_id, offer in info["offeredBy"].items():
                    if device_id not in self.phones:
                        continue
                    if folder_id in folders:
                        issues.append(
                            {
                                "phone": self.phones[device_id]["name"],
                                "folder": offer.get("label") or folder_id,
                                "folderID": folder_id,
                                "owner": self._owner(folders[folder_id]),
                            }
                        )
                        continue
                    await self.add_folder(device_id, folder_id, offer.get("label"))
                    folders[folder_id] = await self.st.get(f"/rest/config/folders/{folder_id}")
            self.issues = issues

    async def run(self):
        while True:
            try:
                if not self.configured:
                    await self.configure()
                await self.accept_folders()
            except SyncthingDown:
                self.configured = False
            except ApiError as error:
                print(f"phone sync: {error.message}", flush=True)
            await asyncio.sleep(POLL_SECONDS)

    async def _folder_status(self, folder):
        status = await self.st.get(f"/rest/db/status?folder={folder['id']}")
        total = status.get("globalBytes", 0)
        done = status.get("inSyncBytes", 0)
        return {
            "id": folder["id"],
            "label": folder.get("label") or folder["id"],
            "path": str(_relative(folder["path"])),
            "state": status.get("state", "unknown"),
            "error": status.get("error", ""),
            "bytes": status.get("localBytes", 0),
            "files": status.get("localFiles", 0),
            "percent": 100 if total == 0 else round(done * 100 / total),
        }

    async def overview(self):
        try:
            status = await self.st.get("/rest/system/status")
            version = await self.st.get("/rest/system/version")
            connections = (await self.st.get("/rest/system/connections"))["connections"]
            stats = await self.st.get("/rest/stats/device")
            folders = await self.st.get("/rest/config/folders")
            pending = await self.st.get("/rest/cluster/pending/devices")
            devices = await self.st.get("/rest/config/devices")
        except SyncthingDown:
            return {"online": False}

        phones = []
        for device_id, phone in self.phones.items():
            shared = [
                f for f in folders if any(d["deviceID"] == device_id for d in f["devices"])
            ]
            connection = connections.get(device_id, {})
            phones.append(
                {
                    "id": device_id,
                    "name": phone["name"],
                    "dir": phone["dir"],
                    "connected": connection.get("connected", False),
                    "lastSeen": stats.get(device_id, {}).get("lastSeen"),
                    "folders": [await self._folder_status(f) for f in shared],
                }
            )
        return {
            "online": True,
            "deviceID": status["myID"],
            "version": version.get("version", ""),
            "paused": any(d["paused"] for d in devices if d["deviceID"] in self.phones),
            "phones": phones,
            "pending": [
                {
                    "id": device_id,
                    "name": info.get("name", ""),
                    "address": info.get("address", ""),
                    "time": info.get("time"),
                }
                for device_id, info in pending.items()
            ],
            "issues": self.issues,
        }

    async def approve(self, device_id, name):
        async with self._lock:
            pending = await self.st.get("/rest/cluster/pending/devices")
            if device_id not in pending:
                raise ApiError(404, "That phone is no longer waiting for approval")
            directory = unique_dir(PHONES_DIR, slug(name, "phone"))
            (PHONES_DIR / directory).mkdir(parents=True)
            await self.st.request(
                "POST",
                "/rest/config/devices",
                json={"deviceID": device_id, "name": name, "addresses": ["dynamic"]},
            )
            self.phones[device_id] = {"name": name, "dir": directory}
            self._save()
        return self.phones[device_id]

    async def remove(self, device_id):
        """Stop syncing a phone. Its files stay on the Pi."""
        async with self._lock:
            folders = await self.st.get("/rest/config/folders")
            for folder in folders:
                if any(d["deviceID"] == device_id for d in folder["devices"]):
                    await self.st.request("DELETE", f"/rest/config/folders/{folder['id']}")
            await self.st.request("DELETE", f"/rest/config/devices/{device_id}")
            self.phones.pop(device_id, None)
            self._save()


def _relative(path):
    return path.removeprefix(str(PHONES_DIR)).lstrip("/")


def _device_id(value):
    if not DEVICE_ID.match(value or ""):
        raise ApiError(400, "Invalid device ID")
    return value


async def _guarded(call):
    try:
        return await call
    except SyncthingDown:
        raise ApiError(503, "The sync service isn't running")


async def overview(request):
    return web.json_response(await request.app["phones"].overview())


async def qr(request):
    sync = request.app["phones"]
    status = await _guarded(sync.st.get("/rest/system/status"))
    image = await _guarded(sync.st.get("/qr/", raw=True, params={"text": status["myID"]}))
    return web.Response(body=image, content_type="image/png")


async def approve(request):
    body = await request.json()
    device_id = _device_id(body.get("id"))
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 60:
        raise ApiError(400, "Give the phone a name of up to 60 characters")
    phone = await _guarded(request.app["phones"].approve(device_id, name))
    return web.json_response(phone, status=201)


async def ignore(request):
    device_id = _device_id(request.match_info["id"])
    sync = request.app["phones"]
    await _guarded(
        sync.st.request("DELETE", "/rest/cluster/pending/devices", params={"device": device_id})
    )
    return web.json_response({"ok": True})


async def remove(request):
    device_id = _device_id(request.match_info["id"])
    await _guarded(request.app["phones"].remove(device_id))
    return web.json_response({"ok": True})


async def pause(request):
    body = await request.json()
    path = "/rest/system/pause" if body.get("paused") else "/rest/system/resume"
    await _guarded(request.app["phones"].st.request("POST", path))
    return web.json_response({"ok": True})


async def _background(app):
    sync = app["phones"]
    sync.st.session = aiohttp.ClientSession()
    task = asyncio.create_task(sync.run())
    yield
    task.cancel()
    await sync.st.session.close()


def setup(app):
    app["phones"] = PhoneSync()
    app.cleanup_ctx.append(_background)
    app.router.add_get("/api/phones", overview)
    app.router.add_get("/api/phones/qr", qr)
    app.router.add_post("/api/phones", approve)
    app.router.add_post("/api/phones/pause", pause)
    app.router.add_delete("/api/phones/pending/{id}", ignore)
    app.router.add_delete("/api/phones/{id}", remove)
