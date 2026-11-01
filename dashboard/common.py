"""Paths and helpers shared by the dashboard modules."""
import asyncio
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("PI5_DATA_DIR", "/var/lib/pi5"))
STORAGE_DIR = Path(os.environ.get("PI5_STORAGE_DIR", "/srv/pi5"))
SYNCTHING_HOME = Path(os.environ.get("PI5_SYNCTHING_HOME", DATA_DIR / "syncthing"))
SYNCTHING_URL = os.environ.get("PI5_SYNCTHING_URL", "http://127.0.0.1:8384")
PORT = int(os.environ.get("PI5_PORT", "80"))

AUTH_FILE = DATA_DIR / "auth.json"
PHONES_FILE = DATA_DIR / "phones.json"
STATIC_DIR = Path(__file__).parent / "static"


class ApiError(Exception):
    """An error reported to the browser as {"error": message}."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


async def run(*command, timeout=15):
    """Run a command and return (exit code, stdout, stderr). 127 means it's missing."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", f"{command[0]} not found"
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return 124, "", "Timed out"
    return process.returncode, stdout.decode().strip(), stderr.decode().strip()
