#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


REPO_ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = Path(__file__).resolve().parent
AUTOMATION_DIR = REPO_ROOT / "automation"

INVENTORY_SCRIPT = AUTOMATION_DIR / "netbox_inventory.py"

STATIC_DIR = WEBAPP_DIR / "static"
TEMPLATE_DIR = WEBAPP_DIR / "templates"

NETBOX_URL = os.environ.get(
    "NETBOX_URL",
    "http://netbox.local",
).rstrip("/")


app = FastAPI(
    title="ANA Network Automation",
    description=(
        "Network source-of-truth, validation, "
        "and automation portal."
    ),
    version="0.1.0",
)

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)

templates = Jinja2Templates(
    directory=TEMPLATE_DIR,
)


def load_inventory():
    result = subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=os.environ,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or "Unable to retrieve NetBox inventory."
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Inventory script returned invalid JSON: {exc}"
        ) from exc


def get_devices():
    inventory = load_inventory()
    return inventory.get("devices", [])


@app.get("/health")
def health():
    return {
        "status": "ok",
        "application": "ANA Network Automation",
    }


@app.get("/api/inventory")
def api_inventory():
    try:
        inventory = load_inventory()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    return JSONResponse(content=inventory)


@app.get("/")
def dashboard(request: Request):
    try:
        devices = get_devices()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    platform_counts = Counter(
        device.get("platform") or "Unknown"
        for device in devices
    )

    profile_counts = Counter(
        device.get("config_profile") or "Unassigned"
        for device in devices
    )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "page_title": "Dashboard",
            "devices": devices,
            "device_count": len(devices),
            "platform_counts": dict(platform_counts),
            "profile_counts": dict(profile_counts),
            "netbox_url": NETBOX_URL,
        },
    )


@app.get("/inventory")
def inventory_page(request: Request):
    try:
        devices = get_devices()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    return templates.TemplateResponse(
        request=request,
        name="inventory.html",
        context={
            "page_title": "Managed Inventory",
            "devices": devices,
            "netbox_url": NETBOX_URL,
        },
    )
