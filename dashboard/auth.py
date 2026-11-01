"""Single shared password login, kept in a stateless signed cookie."""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time

from aiohttp import web

from common import AUTH_FILE, ApiError

COOKIE = "pi5_session"
SESSION_SECONDS = 30 * 24 * 3600

# Failed logins wait behind this lock, limiting guesses to about one per second.
_login_lock = asyncio.Lock()


def _scrypt(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def set_password(password):
    """Store a new password. A new secret also signs out every session."""
    salt = os.urandom(16)
    doc = {
        "salt": salt.hex(),
        "hash": _scrypt(password, salt).hex(),
        "secret": secrets.token_hex(32),
    }
    tmp = AUTH_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, AUTH_FILE)


def _load():
    return json.loads(AUTH_FILE.read_text())


def _sign(expires, secret):
    return hmac.new(secret.encode(), str(expires).encode(), hashlib.sha256).hexdigest()


def is_valid(request):
    token = request.cookies.get(COOKIE, "")
    expires, _, signature = token.partition(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(signature, _sign(expires, request.app["auth"]["secret"]))


async def login(request):
    body = await request.json()
    password = str(body.get("password", ""))
    doc = request.app["auth"]
    async with _login_lock:
        digest = await asyncio.get_running_loop().run_in_executor(
            None, _scrypt, password, bytes.fromhex(doc["salt"])
        )
        if not hmac.compare_digest(digest.hex(), doc["hash"]):
            await asyncio.sleep(1)
            raise ApiError(403, "Wrong password")
    expires = int(time.time()) + SESSION_SECONDS
    response = web.json_response({"ok": True})
    response.set_cookie(
        COOKIE,
        f"{expires}.{_sign(expires, doc['secret'])}",
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="Strict",
    )
    return response


async def logout(request):
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE)
    return response


async def session(request):
    return web.json_response({"authenticated": is_valid(request)})


def setup(app):
    app["auth"] = _load()
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/session", session)
