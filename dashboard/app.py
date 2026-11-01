#!/usr/bin/env python3
"""pi5 dashboard: status, network, files, phone sync, downloads and addons."""
import argparse
import getpass
import json
import mimetypes
from urllib.parse import urlsplit

from aiohttp import web

import addons
import auth
import downloads
import files
import network
import phones
import status
import tech
from common import AUTH_FILE, PORT, STATIC_DIR, ApiError

PUBLIC_PATHS = {"/api/login", "/api/session"}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


mimetypes.add_type("application/manifest+json", ".webmanifest")


def _private(path):
    return path.startswith(("/api/", "/files/")) and path not in PUBLIC_PATHS


@web.middleware
async def guard(request, handler):
    try:
        origin = request.headers.get("Origin")
        if request.method not in ("GET", "HEAD") and origin:
            if urlsplit(origin).netloc != request.host:
                raise ApiError(403, "Cross-site request refused")
        if _private(request.path) and not auth.is_valid(request):
            raise ApiError(401, "Please sign in")
        response = await handler(request)
    except ApiError as error:
        response = web.json_response({"error": error.message}, status=error.status)
    except json.JSONDecodeError:
        response = web.json_response({"error": "Invalid request"}, status=400)
    response.headers.update(SECURITY_HEADERS)
    if not _private(request.path):
        # Revalidate, so phones pick up dashboard updates on the next load.
        response.headers["Cache-Control"] = "no-cache"
    return response


async def index(request):
    return web.FileResponse(STATIC_DIR / "index.html")


def create_app():
    app = web.Application(middlewares=[guard])
    auth.setup(app)
    status.setup(app)
    network.setup(app)
    files.setup(app)
    downloads.setup(app)
    phones.setup(app)
    addons.setup(app)
    tech.setup(app)
    app.router.add_get("/", index)
    app.router.add_static("/static", STATIC_DIR)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set-password", action="store_true", help="prompt for a new password")
    parser.add_argument("--init-password", metavar="PASSWORD", help="set the password if none exists")
    args = parser.parse_args()
    if args.set_password:
        auth.set_password(getpass.getpass("New dashboard password: "))
        print("Saved. Restart the dashboard to apply it.")
    elif args.init_password:
        if not AUTH_FILE.exists():
            auth.set_password(args.init_password)
    else:
        # All interfaces, so the same server answers on WiFi and on the hotspot.
        web.run_app(create_app(), host=None, port=PORT, access_log=None)


if __name__ == "__main__":
    main()
