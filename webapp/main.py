#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


REPO_ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = Path(__file__).resolve().parent
AUTOMATION_DIR = REPO_ROOT / "automation"

INVENTORY_SCRIPT = AUTOMATION_DIR / "netbox_inventory.py"
VALIDATION_SCRIPT = AUTOMATION_DIR / "run_validation.py"

VALIDATION_DIR = REPO_ROOT / "validation-reports"
GOLDEN_DIR = REPO_ROOT / "golden-configs"

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
    version="0.2.0",
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

def run_validation():
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATION_SCRIPT),
            "--details",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=os.environ,
    )

    return {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }

def latest_validation_report():
    reports = sorted(
        VALIDATION_DIR.glob("validation-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not reports:
        return None

    path = reports[0]

    try:
        with path.open() as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    return {
        "path": path,
        "name": path.name,
        "data": report,
    }


def validation_step_status(report):
    if not report:
        return {}

    steps = report.get("steps", {})

    statuses = {}

    render = steps.get("render")

    if isinstance(render, list):
        statuses["render"] = all(
            item.get("returncode") == 0
            for item in render
        )
    else:
        statuses["render"] = False

    for step_name in (
        "collect",
        "intent_validation",
        "drift",
    ):
        step = steps.get(step_name)

        if not isinstance(step, dict):
            statuses[step_name] = False
            continue

        if step.get("skipped"):
            statuses[step_name] = None
        else:
            statuses[step_name] = (
                step.get("returncode") == 0
            )

    return statuses


def golden_snapshots():
    if not GOLDEN_DIR.exists():
        return []

    snapshots = []

    for path in GOLDEN_DIR.iterdir():
        if not path.is_dir():
            continue

        manifest_path = path / "manifest.json"

        if not manifest_path.is_file():
            continue

        try:
            with manifest_path.open() as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        snapshots.append(
            {
                "snapshot_id": path.name,
                "path": path,
                "manifest": manifest,
            }
        )

    return sorted(
        snapshots,
        key=lambda item: item["snapshot_id"],
        reverse=True,
    )


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

@app.get("/automation")
def automation_page(
    request: Request,
    status: str | None = None,
    message: str | None = None,
):
    try:
        devices = get_devices()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    validation = latest_validation_report()

    report = (
        validation["data"]
        if validation
        else None
    )

    snapshots = golden_snapshots()

    latest_golden = (
        snapshots[0]
        if snapshots
        else None
    )

    return templates.TemplateResponse(
        request=request,
        name="automation.html",
        context={
            "page_title": "Automation",
            "devices": devices,
            "validation": validation,
            "report": report,
            "step_status": validation_step_status(report),
            "golden_snapshots": snapshots,
            "latest_golden": latest_golden,
            "netbox_url": NETBOX_URL,
            "action_status": status,
            "action_message": message,
        },
    )

@app.post("/automation/validate")
def run_validation_action():
    result = run_validation()

    if result["returncode"] == 0:
        status = "success"
        message = (
            "Validation completed successfully. "
            "All managed devices passed."
        )
    else:
        status = "error"

        message = (
            "Validation failed. Review the latest "
            "validation report and server logs."
        )

    return RedirectResponse(
        url=(
            "/automation"
            f"?status={quote(status)}"
            f"&message={quote(message)}"
        ),
        status_code=303,
    )
