"""Video and audio downloads with yt-dlp, one at a time.

Everything is sorted into folders under downloads/:

    Videos/<channel>/<title> [<id>].mp4
    Videos/<channel>/<playlist>/001 - <title> [<id>].mp4
    Audio/<channel>/<title> [<id>].mp3

A folder name given with a download replaces <channel>. The ID in each filename keeps
two videos with the same title apart.

Options come from fixed lists, and the link is passed after "--", so nothing from the
browser can become a yt-dlp option. Downloads run as a low-priority process in their
own temporary folder, which is removed afterwards.
"""
import asyncio
import json
import os
import re
import secrets
import shutil
import signal
import time
from urllib.parse import urlsplit

from aiohttp import web

import files
from common import DATA_DIR, STORAGE_DIR, ApiError, run

YT_DLP = DATA_DIR / "bin" / "yt-dlp"
CACHE_DIR = DATA_DIR / "yt-dlp-cache"
DOWNLOADS_DIR = STORAGE_DIR / "downloads"
TEMP_DIR = DOWNLOADS_DIR / ".tmp"

KEEP_JOBS = 30
RESERVED_BYTES = 1024 * 1024 * 1024  # stop before the SD card fills up
SPACE_CHECK_SECONDS = 5
MAX_URL = 2048
CLIP = re.compile(r"^\d{1,2}(:\d{2}){0,2}-\d{1,2}(:\d{2}){0,2}$")

QUALITIES = {"best": None, "1080": 1080, "720": 720, "480": 480}
AUDIO_FORMATS = {"mp3", "m4a"}
RATE_LIMITS = {"", "200K", "500K", "1M", "5M"}
FRAGMENTS = {1, 2, 4, 8}
MAX_RETRIES = 50

PROGRESS = "%(progress.{status,downloaded_bytes,total_bytes,total_bytes_estimate,speed,eta})j"
CHANNEL = "%(uploader,channel,extractor_key|Unknown).60B"
PLAYLIST = "%(playlist_title,playlist_id|Playlist).80B"


def clean_options(body):
    """Check every option against what the dashboard offers."""
    audio = body.get("kind") == "audio"
    quality = str(body.get("quality", ""))
    if quality not in (AUDIO_FORMATS if audio else QUALITIES):
        raise ApiError(400, "Choose a quality from the list")
    rate = str(body.get("rate", ""))
    if rate not in RATE_LIMITS:
        raise ApiError(400, "Choose a speed limit from the list")
    try:
        fragments = int(body.get("fragments", 1))
        retries = int(body.get("retries", 10))
    except (TypeError, ValueError):
        raise ApiError(400, "Invalid number")
    if fragments not in FRAGMENTS or not 0 <= retries <= MAX_RETRIES:
        raise ApiError(400, "Invalid download option")
    clip = str(body.get("clip", "")).strip()
    if clip and not CLIP.match(clip):
        raise ApiError(400, "Write the clip as start-end, like 1:00-2:30")
    folder = str(body.get("folder", "")).strip()
    return {
        "audio": audio,
        "quality": quality,
        "rate": rate,
        "fragments": fragments,
        "retries": retries,
        "clip": clip,
        "folder": files.clean_name(folder) if folder else "",
        "playlist": bool(body.get("playlist")),
        "subtitles": bool(body.get("subtitles")) and not audio,
    }


def output_template(options):
    kind = "Audio" if options["audio"] else "Videos"
    # % starts a template field, so a typed folder name needs it doubled.
    folder = options["folder"].replace("%", "%%") or CHANNEL
    if options["playlist"]:
        return f"{kind}/{folder}/{PLAYLIST}/%(playlist_index)03d - %(title).120B [%(id)s].%(ext)s"
    return f"{kind}/{folder}/%(title).150B [%(id)s].%(ext)s"


def build_command(job):
    options = job["options"]
    command = [
        "nice", "-n", "10", str(YT_DLP),
        "--ignore-config", "--no-plugin-dirs",
        "--js-runtimes", "node",
        "--cache-dir", str(CACHE_DIR),
        "--paths", f"home:{DOWNLOADS_DIR}",
        "--paths", f"temp:{TEMP_DIR / job['id']}",
        "--output", output_template(options),
        "--no-overwrites",
        "--embed-metadata",
        "--newline", "--no-color", "--progress",
        "--progress-template", f"download:PI5P {PROGRESS}",
        "--progress-template", "postprocess:PI5S %(progress.postprocessor)s",
        "--print", "before_dl:PI5T %(title)s",
        "--print", "after_move:PI5F %(filepath)s",
        "--concurrent-fragments", str(options["fragments"]),
        "--retries", str(options["retries"]),
        "--yes-playlist" if options["playlist"] else "--no-playlist",
    ]
    if options["audio"]:
        command += ["--format", "ba/b", "--extract-audio", "--audio-format", options["quality"], "--audio-quality", "0"]
    else:
        command += ["--format", "bv*+ba/b", "--merge-output-format", "mp4"]
        height = QUALITIES[options["quality"]]
        if height:
            # H.264 plays on every phone, so it wins at a chosen size.
            command += ["--format-sort", f"res:{height},vcodec:h264,acodec:m4a"]
    if options["rate"]:
        command += ["--limit-rate", options["rate"]]
    if options["clip"]:
        command += ["--download-sections", f"*{options['clip']}"]
    if options["subtitles"]:
        command += ["--write-subs", "--sub-langs", "en,ar", "--embed-subs"]
    return command + ["--", job["url"]]


def _relative(path):
    return path.removeprefix(str(DOWNLOADS_DIR)).lstrip("/")


def _remove_empty_folders():
    """A download that didn't finish can leave its channel folder behind, empty."""
    for root in ("Videos", "Audio"):
        for directory, _, _ in os.walk(DOWNLOADS_DIR / root, topdown=False):
            try:
                os.rmdir(directory)
            except OSError:
                pass  # it holds files


class Downloads:
    def __init__(self):
        self.jobs = {}
        self.queue = asyncio.Queue()
        self.process = None
        self.current = None
        self.version = ""
        self.task = None

    def add(self, url, options):
        job = {
            "id": secrets.token_hex(6),
            "url": url,
            "title": url,
            "options": options,
            "state": "queued",
            "stage": "",
            "percent": 0,
            "speed": None,
            "eta": None,
            "size": None,
            "files": [],
            "message": "",
        }
        self.jobs[job["id"]] = job
        self._prune()
        self.queue.put_nowait(job)
        return job

    def _prune(self):
        finished = [j for j in self.jobs.values() if j["state"] not in ("queued", "running")]
        for job in finished[: max(0, len(self.jobs) - KEEP_JOBS)]:
            del self.jobs[job["id"]]

    def running(self):
        job = self.current
        return [{"name": job["title"], "percent": job["percent"]}] if job else []

    def cancel(self, job, reason=""):
        if job["state"] == "queued":
            job["state"] = "cancelled"
        elif job["state"] == "running" and self.process:
            job["stopping"] = reason or "Cancelled"
            os.killpg(self.process.pid, signal.SIGTERM)

    def _line(self, job, line):
        tag, _, value = line.partition(" ")
        if tag == "PI5T":
            job["title"] = value
            job["stage"] = "Downloading"
        elif tag == "PI5F":
            job["files"].append(_relative(value))
        elif tag == "PI5S":
            job["stage"] = {"Merger": "Merging", "ExtractAudio": "Converting"}.get(value, "Finishing")
        elif tag == "PI5P":
            try:
                progress = json.loads(value)
            except ValueError:
                return
            done = progress.get("downloaded_bytes") or 0
            total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
            job.update(
                speed=progress.get("speed"),
                eta=progress.get("eta"),
                size=total,
                percent=round(done / total * 100, 1) if total else job["percent"],
            )
        elif line.startswith("ERROR:"):
            job["message"] = line.removeprefix("ERROR:").strip()[:300]

    def _low_on_space(self):
        return shutil.disk_usage(DOWNLOADS_DIR).free < RESERVED_BYTES

    async def _download(self, job):
        if self._low_on_space():
            job.update(state="failed", message="Not enough free space on the Pi")
            return
        job["state"] = "running"
        self.current = job
        files.DOWNLOADING.add(job["id"])
        job_temp = TEMP_DIR / job["id"]
        job_temp.mkdir(parents=True, exist_ok=True)
        checked = time.monotonic()
        try:
            self.process = await asyncio.create_subprocess_exec(
                *build_command(job),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            async for raw in self.process.stdout:
                self._line(job, raw.decode(errors="replace").rstrip())
                if time.monotonic() - checked > SPACE_CHECK_SECONDS:
                    checked = time.monotonic()
                    if self._low_on_space():
                        self.cancel(job, "Stopped because the Pi is nearly full")
            code = await self.process.wait()
        finally:
            self.process = None
            self.current = None
            files.DOWNLOADING.discard(job["id"])
            shutil.rmtree(job_temp, ignore_errors=True)

        job.update(speed=None, eta=None, stage="")
        stopped = job.pop("stopping", None)
        if stopped:
            job.update(state="cancelled", message=stopped)
            _remove_empty_folders()
        elif code == 0 or job["files"]:
            job.update(state="done", percent=100)
        else:
            job.update(state="failed", message=job["message"] or f"yt-dlp stopped with code {code}")
            _remove_empty_folders()

    async def worker(self):
        while True:
            job = await self.queue.get()
            if job["state"] != "queued":
                continue
            try:
                await self._download(job)
            except OSError as error:
                job.update(state="failed", message=str(error))

    async def read_version(self):
        code, out, _ = await run(str(YT_DLP), "--version")
        self.version = out if code == 0 else ""


async def overview(request):
    downloads = request.app["downloads"]
    if not downloads.version:
        await downloads.read_version()
    return web.json_response(
        {
            "version": downloads.version,
            "jobs": [
                {k: v for k, v in job.items() if k != "options"}
                for job in reversed(list(downloads.jobs.values()))
            ],
        }
    )


async def add(request):
    body = await request.json()
    url = str(body.get("url", "")).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or len(url) > MAX_URL:
        raise ApiError(400, "Paste a full link starting with http or https")
    job = request.app["downloads"].add(url, clean_options(body))
    return web.json_response({"id": job["id"]}, status=202)


async def remove(request):
    """Cancel a waiting or running download, or clear a finished one from the list."""
    downloads = request.app["downloads"]
    job = downloads.jobs.get(request.match_info["id"])
    if not job:
        raise ApiError(404, "That download is no longer listed")
    if job["state"] in ("queued", "running"):
        downloads.cancel(job)
    else:
        del downloads.jobs[job["id"]]
    return web.json_response({"ok": True})


async def update(request):
    downloads = request.app["downloads"]
    if downloads.current:
        raise ApiError(409, "Wait for the current download to finish")
    code, out, error = await run(str(YT_DLP), "--update", timeout=180)
    if code:
        raise ApiError(502, error or "Update failed")
    await downloads.read_version()
    return web.json_response({"version": downloads.version, "message": out.splitlines()[-1] if out else ""})


async def _background(app):
    shutil.rmtree(TEMP_DIR, ignore_errors=True)  # left behind by a restart or power cut
    downloads = app["downloads"]
    downloads.task = asyncio.create_task(downloads.worker())
    yield
    downloads.task.cancel()


def setup(app):
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    app["downloads"] = Downloads()
    app.cleanup_ctx.append(_background)
    app.router.add_get("/api/downloads", overview)
    app.router.add_post("/api/downloads", add)
    app.router.add_post("/api/downloads/update", update)
    app.router.add_delete("/api/downloads/{id}", remove)
