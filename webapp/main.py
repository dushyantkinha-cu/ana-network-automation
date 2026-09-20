#!/usr/bin/env python3

import ipaddress
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

import requests

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

ROUTING_CHOICE_SET_ID = 1
PROFILE_CHOICE_SET_ID = 2
PROFILE_TEMPLATE_MAP = {
    "Cisco IOS-XE": {
        "edge": "templates/cisco/edge.j2",
    },
    "Arista EOS": {
        "distribution": "templates/arista/distribution.j2",
        "access": "templates/arista/access.j2",
        "core": "templates/arista/core.j2",
    },
    "Nokia SR Linux": {
        "core": "templates/nokia/core.j2",
    },
}

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


def get_sites():
    data = netbox_get(
        "/api/dcim/sites/?limit=0"
    )

    return [
        {
            "id": site["id"],
            "name": site["name"],
        }
        for site in data.get("results", [])
    ]


def netbox_headers():
    token = os.environ.get("NETBOX_TOKEN")

    if not token:
        raise RuntimeError(
            "NETBOX_TOKEN is not available to the web application."
        )

    return {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def netbox_get(path):
    try:
        response = requests.get(
            f"{NETBOX_URL}{path}",
            headers=netbox_headers(),
            timeout=10,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        raise RuntimeError(
            f"NetBox GET failed: {exc}"
        ) from exc


def netbox_patch(path, payload):
    try:
        response = requests.patch(
            f"{NETBOX_URL}{path}",
            headers=netbox_headers(),
            json=payload,
            timeout=10,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        raise RuntimeError(
            f"NetBox PATCH failed: {exc}"
        ) from exc


def get_choice_values(choice_set_id):
    data = netbox_get(
        f"/api/extras/custom-field-choice-sets/"
        f"{choice_set_id}/"
    )

    choices = []

    for source in (
        data.get("base_choices") or [],
        data.get("extra_choices") or [],
    ):
        for value, label in source:
            choices.append(
                {
                    "value": value,
                    "label": label,
                }
            )

    return choices


def find_managed_device(hostname):
    devices = get_devices()

    return next(
        (
            device
            for device in devices
            if device.get("hostname") == hostname
        ),
        None,
    )


def get_wan_address_state(device):
    device_id = device.get("device_id")

    if not device_id:
        raise RuntimeError(
            "Managed device has no NetBox device ID."
        )

    context = device.get("config_context") or {}
    nat = context.get("nat") or {}

    outside_interface = nat.get(
        "outside_interface"
    )

    if not outside_interface:
        return None

    interfaces = netbox_get(
        "/api/dcim/interfaces/"
        f"?device_id={device_id}"
        f"&name={outside_interface}"
    ).get("results", [])

    if len(interfaces) != 1:
        raise RuntimeError(
            f"Unable to uniquely resolve "
            f"{device['hostname']} "
            f"WAN interface {outside_interface}."
        )

    interface = interfaces[0]

    ip_objects = netbox_get(
        "/api/ipam/ip-addresses/"
        f"?interface_id={interface['id']}"
        "&limit=0"
    ).get("results", [])

    ipv4 = None
    ipv6 = None

    for ip_object in ip_objects:
        address = ip_object.get("address")

        if not address:
            continue

        parsed = ipaddress.ip_interface(
            address
        )

        record = {
            "id": ip_object["id"],
            "address": address,
            "prefixlen": parsed.network.prefixlen,
        }

        if parsed.version == 4:
            if ipv4 is not None:
                raise RuntimeError(
                    "WAN interface has multiple IPv4 "
                    "addresses; automatic editing is "
                    "not safe."
                )

            ipv4 = record

        else:
            if ipv6 is not None:
                raise RuntimeError(
                    "WAN interface has multiple IPv6 "
                    "addresses; automatic editing is "
                    "not safe."
                )

            ipv6 = record

    return {
        "interface_id": interface["id"],
        "interface_name": outside_interface,
        "ipv4": ipv4,
        "ipv6": ipv6,
    }


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


@app.get("/changes")
def changes_page(
    request: Request,
    device: str | None = None,
    status: str | None = None,
    message: str | None = None,
):
    try:
        devices = get_devices()

        routing_choices = get_choice_values(
            ROUTING_CHOICE_SET_ID
        )

        profile_choices = get_choice_values(
            PROFILE_CHOICE_SET_ID
        )

        sites = get_sites()

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    selected_device = None
    raw_device = None
    compatible_profiles = []
    template_path = None
    wan_state = None

    if device:
        selected_device = next(
            (
                item
                for item in devices
                if item.get("hostname") == device
            ),
            None,
        )

        if selected_device is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Device is not in managed "
                    "automation scope."
                ),
            )

        device_id = selected_device.get("device_id")

        if not device_id:
            raise HTTPException(
                status_code=500,
                detail="Managed device has no NetBox device ID.",
            )

        try:
            raw_device = netbox_get(
                f"/api/dcim/devices/{device_id}/"
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail=str(exc),
            ) from exc

        platform = selected_device.get("platform")

        supported_profiles = PROFILE_TEMPLATE_MAP.get(
            platform,
            {},
        )

        compatible_profiles = [
            choice
            for choice in profile_choices
            if choice["value"] in supported_profiles
        ]

        template_path = supported_profiles.get(
            selected_device.get("config_profile")
        )

        wan_state = get_wan_address_state(
            selected_device
        )

    return templates.TemplateResponse(
        request=request,
        name="changes.html",
        context={
            "page_title": "Changes",
            "devices": devices,
            "selected_device": selected_device,
            "raw_device": raw_device,
            "routing_choices": routing_choices,
            "compatible_profiles": compatible_profiles,
            "template_path": template_path,
            "wan_state": wan_state,
            "sites": sites,
            "action_status": status,
            "action_message": message,
            "netbox_url": NETBOX_URL,
        },
    )


@app.post("/changes/update")
async def update_device_intent(request: Request):
    form = await request.form()

    hostname = str(
        form.get("hostname", "")
    ).strip()

    config_profile = str(
        form.get("config_profile", "")
    ).strip()

    routing_protocols = [
        str(value)
        for value in form.getlist(
            "routing_protocols"
        )
    ]

    try:
        device = find_managed_device(hostname)

        if (
            device is None
            or device.get("automation_managed") is not True
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Device is outside managed "
                    "automation scope."
                ),
            )

        allowed_routing = {
            choice["value"]
            for choice in get_choice_values(
                ROUTING_CHOICE_SET_ID
            )
        }

        invalid_routing = (
            set(routing_protocols)
            - allowed_routing
        )

        if invalid_routing:
            raise HTTPException(
                status_code=400,
                detail="Invalid routing protocol selection.",
            )

        platform = device.get("platform")

        supported_profiles = PROFILE_TEMPLATE_MAP.get(
            platform,
            {},
        )

        if config_profile not in supported_profiles:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected configuration profile "
                    "is not supported by this platform."
                ),
            )

        device_id = device.get("device_id")

        if not device_id:
            raise HTTPException(
                status_code=500,
                detail="Managed device has no NetBox device ID.",
            )

        payload = {
            "custom_fields": {
                "automation_managed": True,
                "routing_protocols": routing_protocols,
                "config_profile": config_profile,
            }
        }

        netbox_patch(
            f"/api/dcim/devices/{device_id}/",
            payload,
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    message = (
        f"NetBox intent for {hostname} "
        "was updated successfully."
    )

    return RedirectResponse(
        url=(
            "/changes"
            f"?device={quote(hostname)}"
            "&status=success"
            f"&message={quote(message)}"
        ),
        status_code=303,
    )


@app.post("/changes/update-wan")
async def update_wan_addresses(request: Request):
    form = await request.form()

    hostname = str(
        form.get("hostname", "")
    ).strip()

    submitted_ipv4 = str(
        form.get("wan_ipv4", "")
    ).strip()

    submitted_ipv6 = str(
        form.get("wan_ipv6", "")
    ).strip()

    try:
        device = find_managed_device(hostname)

        if (
            device is None
            or device.get(
                "automation_managed"
            ) is not True
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Device is outside managed "
                    "automation scope."
                ),
            )

        wan_state = get_wan_address_state(
            device
        )

        if wan_state is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This device has no managed "
                    "WAN interface."
                ),
            )

        if not wan_state["ipv4"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    "WAN interface has no IPv4 "
                    "address object."
                ),
            )

        if not wan_state["ipv6"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    "WAN interface has no IPv6 "
                    "address object."
                ),
            )

        try:
            ipv4 = ipaddress.ip_interface(
                submitted_ipv4
            )

            ipv6 = ipaddress.ip_interface(
                submitted_ipv6
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "WAN addresses must use valid "
                    "CIDR notation."
                ),
            ) from exc

        if ipv4.version != 4:
            raise HTTPException(
                status_code=400,
                detail=(
                    "WAN IPv4 field must contain "
                    "an IPv4 address."
                ),
            )

        if ipv6.version != 6:
            raise HTTPException(
                status_code=400,
                detail=(
                    "WAN IPv6 field must contain "
                    "an IPv6 address."
                ),
            )

        if (
            ipv4.network.prefixlen
            != wan_state["ipv4"]["prefixlen"]
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "WAN IPv4 prefix length cannot "
                    "be changed from this form."
                ),
            )

        if (
            ipv6.network.prefixlen
            != wan_state["ipv6"]["prefixlen"]
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "WAN IPv6 prefix length cannot "
                    "be changed from this form."
                ),
            )

        normalized_ipv4 = str(ipv4)
        normalized_ipv6 = str(ipv6)

        netbox_patch(
            (
                "/api/ipam/ip-addresses/"
                f"{wan_state['ipv4']['id']}/"
            ),
            {
                "address": normalized_ipv4,
            },
        )

        netbox_patch(
            (
                "/api/ipam/ip-addresses/"
                f"{wan_state['ipv6']['id']}/"
            ),
            {
                "address": normalized_ipv6,
            },
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    message = (
        f"WAN addresses for {hostname} "
        "were updated successfully in NetBox."
    )

    return RedirectResponse(
        url=(
            "/changes"
            f"?device={quote(hostname)}"
            "&status=success"
            f"&message={quote(message)}"
        ),
        status_code=303,
    )


@app.post("/changes/update-metadata")
async def update_device_metadata(request: Request):
    form = await request.form()

    hostname = str(
        form.get("hostname", "")
    ).strip()

    site_value = str(
        form.get("site_id", "")
    ).strip()

    try:
        device = find_managed_device(hostname)

        if (
            device is None
            or device.get(
                "automation_managed"
            ) is not True
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Device is outside managed "
                    "automation scope."
                ),
            )

        try:
            site_id = int(site_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid site selection.",
            ) from exc

        sites = get_sites()

        valid_site_ids = {
            site["id"]
            for site in sites
        }

        if site_id not in valid_site_ids:
            raise HTTPException(
                status_code=400,
                detail="Selected site does not exist.",
            )

        device_id = device.get("device_id")

        if not device_id:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Managed device has no "
                    "NetBox device ID."
                ),
            )

        netbox_patch(
            f"/api/dcim/devices/{device_id}/",
            {
                "site": site_id,
            },
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    message = (
        f"Device metadata for {hostname} "
        "was updated successfully in NetBox."
    )

    return RedirectResponse(
        url=(
            "/changes"
            f"?device={quote(hostname)}"
            "&status=success"
            f"&message={quote(message)}"
        ),
        status_code=303,
    )
