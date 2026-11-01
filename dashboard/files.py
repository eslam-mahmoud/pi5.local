"""File browser for the storage roots.

presets and downloads are writable from the dashboard. phones is read-only here:
only Syncthing writes to it.
"""
import asyncio
import os
import secrets
import shutil
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from common import STORAGE_DIR, ApiError

ROOTS = {"presets": True, "downloads": True, "phones": False}
CHUNK_BYTES = 1024 * 1024
# Keeps the OS, logs and Syncthing's database working when storage is nearly full.
RESERVED_BYTES = 512 * 1024 * 1024

ACTIVE = {}  # running uploads, shown on the home page and checked before a reboot
DOWNLOADING = set()  # ids of running yt-dlp jobs


def busy_reason():
    """What a reboot or network change would interrupt, if anything."""
    if ACTIVE:
        return "An upload is running"
    if DOWNLOADING:
        return "A download is running"
    return ""


def is_hidden(name):
    # Dotfiles also cover .stfolder, .stversions and .upload-* temp files.
    return name.startswith((".", "~syncthing~"))


def clean_name(name):
    name = (name or "").strip()
    if (
        not name
        or is_hidden(name)
        or "/" in name
        or "\\" in name
        or "\0" in name
        or len(name.encode()) > 255
    ):
        raise ApiError(400, "Invalid name")
    return name


def root_dir(root):
    if root not in ROOTS:
        raise ApiError(404, "Unknown storage area")
    return (STORAGE_DIR / root).resolve()


def writable_root(root):
    base = root_dir(root)
    if not ROOTS[root]:
        raise ApiError(403, "This area is read-only")
    return base


def resolve(base, relative):
    """Turn a browser path into a real path, refusing escapes and hidden entries."""
    parts = [p for p in (relative or "").split("/") if p]
    if any(is_hidden(p) for p in parts):
        raise ApiError(404, "Not found")
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise ApiError(404, "Not found")
    return target


def _list(path):
    entries = []
    with os.scandir(path) as it:
        for entry in it:
            if is_hidden(entry.name):
                continue
            try:
                info = entry.stat()
                is_dir = entry.is_dir()
            except OSError:
                continue
            entries.append(
                {
                    "name": entry.name,
                    "dir": is_dir,
                    "size": 0 if is_dir else info.st_size,
                    "mtime": int(info.st_mtime),
                }
            )
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return entries


async def list_dir(request):
    root = request.match_info["root"]
    path = resolve(root_dir(root), request.query.get("path"))
    if not path.is_dir():
        raise ApiError(404, "Not found")
    entries = await asyncio.get_running_loop().run_in_executor(None, _list, path)
    return web.json_response({"entries": entries, "writable": ROOTS[root]})


async def download(request):
    root = request.match_info["root"]
    path = resolve(root_dir(root), request.match_info["path"])
    if not path.is_file():
        raise ApiError(404, "Not found")
    # Always an attachment, so uploaded HTML can never run inside the dashboard.
    return web.FileResponse(
        path,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"},
    )


def _publish(tmp, directory, name):
    """Give an upload its final name, keeping both files if the name is taken."""
    stem, suffix = os.path.splitext(name)
    candidate, n = name, 0
    while True:
        try:
            os.link(tmp, directory / candidate)  # fails rather than replaces
            return candidate
        except FileExistsError:
            n += 1
            candidate = f"{stem} ({n}){suffix}"


async def upload(request):
    # Streams to disk beside the target: /tmp is RAM-backed and too small.
    base = writable_root(request.match_info["root"])
    directory = resolve(base, request.query.get("path"))
    if not directory.is_dir():
        raise ApiError(404, "Folder not found")
    name = clean_name(request.query.get("name"))
    length = request.content_length
    if length is None:
        raise ApiError(411, "Upload size is required")
    if length > shutil.disk_usage(directory).free - RESERVED_BYTES:
        raise ApiError(507, "Not enough free space on the Pi")

    loop = asyncio.get_running_loop()
    token = secrets.token_hex(8)
    tmp = directory / f".upload-{token}"
    written = 0
    progress = ACTIVE[token] = {"name": name, "received": 0, "total": length}
    try:
        with open(tmp, "xb") as f:
            async for chunk in request.content.iter_chunked(CHUNK_BYTES):
                written += len(chunk)
                await loop.run_in_executor(None, f.write, chunk)
                progress["received"] = written
        if written != length:
            raise ApiError(400, "Upload was cut short")
        final = await loop.run_in_executor(None, _publish, tmp, directory, name)
    except ConnectionResetError:
        raise ApiError(400, "Upload was interrupted")
    finally:
        ACTIVE.pop(token, None)
        tmp.unlink(missing_ok=True)
    return web.json_response({"name": final, "size": written})


async def make_dir(request):
    base = writable_root(request.match_info["root"])
    body = await request.json()
    parent = resolve(base, body.get("path"))
    target = parent / clean_name(body.get("name"))
    try:
        target.mkdir()
    except FileExistsError:
        raise ApiError(409, "That name is already in use")
    except FileNotFoundError:
        raise ApiError(404, "Folder not found")
    return web.json_response({"name": target.name}, status=201)


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


async def delete(request):
    base = writable_root(request.match_info["root"])
    path = resolve(base, request.query.get("path"))
    if path == base:
        raise ApiError(400, "The storage area itself can't be deleted")
    if not path.exists():
        raise ApiError(404, "Not found")
    await asyncio.get_running_loop().run_in_executor(None, _remove, path)
    return web.json_response({"ok": True})


def setup(app):
    for root in ROOTS:
        Path(STORAGE_DIR / root).mkdir(parents=True, exist_ok=True)
    # Nothing is uploading yet, so any temp file was cut off by a restart or power cut.
    for leftover in (STORAGE_DIR / "presets").rglob(".upload-*"):
        leftover.unlink(missing_ok=True)
    app.router.add_get("/api/files/{root}", list_dir)
    app.router.add_put("/api/files/{root}", upload)
    app.router.add_delete("/api/files/{root}", delete)
    app.router.add_post("/api/files/{root}/folders", make_dir)
    app.router.add_get("/files/{root}/{path:.*}", download)
