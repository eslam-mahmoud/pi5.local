#!/usr/bin/env python3
"""Checks that addons/registry.json and the recipe folders agree. CI runs this."""
import json
import re
import sys
from pathlib import Path

ADDONS = Path(__file__).resolve().parent.parent / "addons"
ID = re.compile(r"^[a-z][a-z0-9-]{0,20}$")
REQUIRED = ("id", "name", "summary", "repository", "status")
RECIPE_FILES = ("addon.json", "install.sh", "addon.service")
SHARED = ("name", "summary")


def check():
    problems = []
    entries = json.loads((ADDONS / "registry.json").read_text())["addons"]
    ids = [entry.get("id") for entry in entries]
    ports = {}
    for addon_id in {i for i in ids if ids.count(i) > 1}:
        problems.append(f"{addon_id}: listed twice")

    for entry in entries:
        addon_id = entry.get("id")
        for field in REQUIRED:
            if not entry.get(field):
                problems.append(f"{addon_id}: missing {field}")
        if not ID.match(addon_id or ""):
            problems.append(f"{addon_id}: invalid ID")
        if entry.get("status") == "planned":
            continue
        if entry.get("status") != "ready":
            problems.append(f"{addon_id}: status must be ready or planned")
            continue
        missing = [name for name in RECIPE_FILES if not (ADDONS / addon_id / name).is_file()]
        if missing:
            problems.append(f"{addon_id}: recipe is missing {', '.join(missing)}")
            continue
        manifest = json.loads((ADDONS / addon_id / "addon.json").read_text())
        if manifest.get("id") != addon_id:
            problems.append(f"{addon_id}: addon.json has a different id")
        for field in SHARED:
            if manifest.get(field) != entry.get(field):
                problems.append(f"{addon_id}: {field} differs between registry and addon.json")
        port = manifest.get("port")
        if not isinstance(port, int):
            problems.append(f"{addon_id}: port must be a number")
        elif port in ports:
            problems.append(f"{addon_id}: port {port} is already used by {ports[port]}")
        ports[port] = addon_id

    for folder in ADDONS.iterdir():
        if folder.is_dir() and folder.name not in ids:
            problems.append(f"{folder.name}: recipe isn't in registry.json")
    return problems


problems = check()
for problem in problems:
    print(problem, file=sys.stderr)
sys.exit(1 if problems else 0)
