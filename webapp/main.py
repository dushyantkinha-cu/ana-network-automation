#!/usr/bin/env python3

import ipaddress
import json
import os
import re
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

from webapp.config import (
    GOLDEN_DIR,
    GRAFANA_DASHBOARDS,
    GRAFANA_PORT,
    INVENTORY_SCRIPT,
    MANAGEMENT_NETWORK,
    NETBOX_URL,
    NETWORK_VENDORS,
    PROFILE_CHOICE_SET_ID,
    PROFILE_ROLE_MAP,
    PROFILE_TEMPLATE_MAP,
    REPO_ROOT,
    ROUTING_CHOICE_SET_ID,
    SITE_STATUS_CHOICES,
    STATIC_DIR,
    TEMPLATE_DIR,
    VALIDATION_DIR,
    VALIDATION_SCRIPT,
)
from webapp.clients.netbox import (
    netbox_delete,
    netbox_get,
    netbox_patch,
    netbox_post,
)
from webapp.services.inventory import (
    find_managed_device,
    get_choice_values,
    get_device_types,
    get_devices,
    get_network_roles,
    get_platforms,
    get_sites,
    get_staged_devices,
    load_inventory,
)
from webapp.services.automation import (
    golden_snapshots,
    latest_validation_report,
    run_validation,
    validation_step_status,
)
from webapp.services.changes import (
    get_wan_address_state,
    make_slug,
)

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


@app.get("/changes/new-site")
def new_site_page(
    request: Request,
    status: str | None = None,
    message: str | None = None,
):
    return templates.TemplateResponse(
        request=request,
        name="new_site.html",
        context={
            "page_title": "Add Site",
            "site_status_choices": SITE_STATUS_CHOICES,
            "action_status": status,
            "action_message": message,
            "netbox_url": NETBOX_URL,
        },
    )


@app.post("/changes/new-site")
async def create_site(request: Request):
    form = await request.form()

    name = str(
        form.get("name", "")
    ).strip()

    status = str(
        form.get("status", "")
    ).strip()

    description = str(
        form.get("description", "")
    ).strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Site name is required.",
        )

    if len(name) > 100:
        raise HTTPException(
            status_code=400,
            detail="Site name is too long.",
        )

    if status not in SITE_STATUS_CHOICES:
        raise HTTPException(
            status_code=400,
            detail="Invalid site status.",
        )

    slug = make_slug(name)

    if not slug:
        raise HTTPException(
            status_code=400,
            detail=(
                "Site name could not be converted "
                "to a valid NetBox slug."
            ),
        )

    try:
        existing_name = netbox_get(
            "/api/dcim/sites/"
            f"?name={quote(name)}"
        ).get("results", [])

        if existing_name:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A NetBox site with this name "
                    "already exists."
                ),
            )

        existing_slug = netbox_get(
            "/api/dcim/sites/"
            f"?slug={quote(slug)}"
        ).get("results", [])

        if existing_slug:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A NetBox site with this slug "
                    "already exists."
                ),
            )

        site = netbox_post(
            "/api/dcim/sites/",
            {
                "name": name,
                "slug": slug,
                "status": status,
                "description": description,
            },
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    message = (
        f"Site '{site['name']}' was created "
        f"in NetBox with slug '{site['slug']}'."
    )

    return RedirectResponse(
        url=(
            "/changes/new-site"
            "?status=success"
            f"&message={quote(message)}"
        ),
        status_code=303,
    )


@app.get("/changes/new")
def new_device_page(
    request: Request,
    status: str | None = None,
    message: str | None = None,
):
    try:
        sites = get_sites()
        device_types = get_device_types()
        platforms = get_platforms()
        roles = get_network_roles()

        routing_choices = get_choice_values(
            ROUTING_CHOICE_SET_ID
        )

        profile_choices = get_choice_values(
            PROFILE_CHOICE_SET_ID
        )

        staged_devices = get_staged_devices()

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    vendors = {}

    for item in device_types + platforms:
        manufacturer = (
            item.get("manufacturer") or {}
        )

        if manufacturer.get("id"):
            vendors[manufacturer["id"]] = {
                "id": manufacturer["id"],
                "name": manufacturer["name"],
            }

    return templates.TemplateResponse(
        request=request,
        name="new_device.html",
        context={
            "page_title": "Add Device",
            "sites": sites,
            "vendors": sorted(
                vendors.values(),
                key=lambda item: item["name"],
            ),
            "device_types": device_types,
            "platforms": platforms,
            "roles": roles,
            "routing_choices": routing_choices,
            "profile_choices": profile_choices,
            "staged_devices": staged_devices,
            "profile_template_map": PROFILE_TEMPLATE_MAP,
            "action_status": status,
            "action_message": message,
            "netbox_url": NETBOX_URL,
        },
    )


@app.post("/changes/new")
async def create_staged_device(request: Request):
    form = await request.form()

    hostname = str(
        form.get("hostname", "")
    ).strip()

    site_value = str(
        form.get("site_id", "")
    ).strip()

    vendor_value = str(
        form.get("vendor_id", "")
    ).strip()

    device_type_value = str(
        form.get("device_type_id", "")
    ).strip()

    platform_value = str(
        form.get("platform_id", "")
    ).strip()

    role_value = str(
        form.get("role_id", "")
    ).strip()

    management_ip = str(
        form.get("management_ip", "")
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
        if (
            not hostname
            or len(hostname) > 64
            or not all(
                char.isalnum()
                or char in ".-_"
                for char in hostname
            )
            or not hostname[0].isalnum()
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid device hostname.",
            )

        try:
            site_id = int(site_value)
            vendor_id = int(vendor_value)
            device_type_id = int(
                device_type_value
            )
            platform_id = int(
                platform_value
            )
            role_id = int(role_value)

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid NetBox object "
                    "selection."
                ),
            ) from exc

        sites = get_sites()
        device_types = get_device_types()
        platforms = get_platforms()
        roles = get_network_roles()

        valid_sites = {
            item["id"]: item
            for item in sites
        }

        valid_device_types = {
            item["id"]: item
            for item in device_types
        }

        valid_platforms = {
            item["id"]: item
            for item in platforms
        }

        valid_roles = {
            item["id"]: item
            for item in roles
        }

        if site_id not in valid_sites:
            raise HTTPException(
                status_code=400,
                detail="Invalid site selection.",
            )

        device_type = valid_device_types.get(
            device_type_id
        )

        platform = valid_platforms.get(
            platform_id
        )

        role = valid_roles.get(role_id)

        if (
            device_type is None
            or platform is None
            or role is None
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid device type, "
                    "platform, or role."
                ),
            )

        dt_manufacturer = (
            device_type.get("manufacturer")
            or {}
        )

        platform_manufacturer = (
            platform.get("manufacturer")
            or {}
        )

        if (
            dt_manufacturer.get("id")
            != vendor_id
            or platform_manufacturer.get("id")
            != vendor_id
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Vendor, device type, and "
                    "platform are not compatible."
                ),
            )

        supported_profiles = (
            PROFILE_TEMPLATE_MAP.get(
                platform["name"],
                {},
            )
        )

        if config_profile not in supported_profiles:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Configuration profile is "
                    "not supported by the "
                    "selected platform."
                ),
            )

        required_role = PROFILE_ROLE_MAP.get(
            config_profile
        )

        if role["name"] != required_role:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Profile '{config_profile}' "
                    f"requires role "
                    f"'{required_role}'."
                ),
            )

        allowed_routing = {
            item["value"]
            for item in get_choice_values(
                ROUTING_CHOICE_SET_ID
            )
        }

        if (
            set(routing_protocols)
            - allowed_routing
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid routing protocol "
                    "selection."
                ),
            )

        try:
            mgmt = ipaddress.ip_interface(
                management_ip
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Management IP must use "
                    "valid CIDR notation."
                ),
            ) from exc

        if mgmt.version != 4:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Management address must "
                    "be IPv4."
                ),
            )

        if mgmt.network.prefixlen != 24:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Management prefix length "
                    "must be /24."
                ),
            )

        if (
            mgmt.ip
            not in MANAGEMENT_NETWORK
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Management IP must be inside "
                    "172.20.20.0/24."
                ),
            )

        existing_devices = netbox_get(
            "/api/dcim/devices/"
            f"?name={hostname}"
        ).get("results", [])

        if existing_devices:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A NetBox device with this "
                    "hostname already exists."
                ),
            )

        normalized_mgmt = str(mgmt)

        existing_ips = netbox_get(
            "/api/ipam/ip-addresses/"
            f"?address={normalized_mgmt}"
        ).get("results", [])

        if existing_ips:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Management IP already exists "
                    "in NetBox."
                ),
            )

        created_device_id = None
        created_interface_id = None
        created_ip_id = None

        try:
            device = netbox_post(
                "/api/dcim/devices/",
                {
                    "name": hostname,
                    "device_type": device_type_id,
                    "role": role_id,
                    "site": site_id,
                    "status": "staged",
                    "platform": platform_id,
                    "custom_fields": {
                        "automation_managed": False,
                        "routing_protocols": (
                            routing_protocols
                        ),
                        "config_profile": (
                            config_profile
                        ),
                    },
                },
            )

            created_device_id = device["id"]

            interface = netbox_post(
                "/api/dcim/interfaces/",
                {
                    "device": created_device_id,
                    "name": "automation-mgmt",
                    "type": "virtual",
                    "enabled": True,
                    "description": (
                        "Logical NMAS-reachable "
                        "management endpoint; not "
                        "necessarily the device-native "
                        "interface."
                    ),
                },
            )

            created_interface_id = interface["id"]

            ip_object = netbox_post(
                "/api/ipam/ip-addresses/",
                {
                    "address": normalized_mgmt,
                    "status": "active",
                    "assigned_object_type": (
                        "dcim.interface"
                    ),
                    "assigned_object_id": (
                        created_interface_id
                    ),
                    "description": (
                        "ANA automation and "
                        "management endpoint for "
                        f"{hostname}"
                    ),
                },
            )

            created_ip_id = ip_object["id"]

            netbox_patch(
                (
                    "/api/dcim/devices/"
                    f"{created_device_id}/"
                ),
                {
                    "primary_ip4": created_ip_id,
                },
            )

        except RuntimeError as creation_error:
            cleanup_errors = []

            if created_ip_id is not None:
                try:
                    netbox_delete(
                        "/api/ipam/ip-addresses/"
                        f"{created_ip_id}/"
                    )
                except RuntimeError as exc:
                    cleanup_errors.append(str(exc))

            if created_interface_id is not None:
                try:
                    netbox_delete(
                        "/api/dcim/interfaces/"
                        f"{created_interface_id}/"
                    )
                except RuntimeError as exc:
                    cleanup_errors.append(str(exc))

            if created_device_id is not None:
                try:
                    netbox_delete(
                        "/api/dcim/devices/"
                        f"{created_device_id}/"
                    )
                except RuntimeError as exc:
                    cleanup_errors.append(str(exc))

            if cleanup_errors:
                raise RuntimeError(
                    f"{creation_error} "
                    "Rollback was incomplete: "
                    + "; ".join(cleanup_errors)
                ) from creation_error

            raise RuntimeError(
                f"{creation_error} "
                "Partial onboarding was rolled back."
            ) from creation_error

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    template_path = (
        PROFILE_TEMPLATE_MAP[
            platform["name"]
        ][config_profile]
    )

    message = (
        f"{hostname} was created in NetBox "
        "as a staged device. "
        f"Template: {template_path}. "
        "Automation remains disabled."
    )

    return RedirectResponse(
        url=(
            "/changes/new"
            "?status=success"
            f"&message={quote(message)}"
        ),
        status_code=303,
    )


@app.get("/monitoring")
def monitoring_page(
    request: Request,
    view: str = "overview",
):
    dashboard = GRAFANA_DASHBOARDS.get(
        view
    )

    if dashboard is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown monitoring dashboard.",
        )

    browser_host = request.url.hostname

    grafana_base_url = (
        f"{request.url.scheme}://"
        f"{browser_host}:{GRAFANA_PORT}"
    )

    dashboard_url = (
        f"{grafana_base_url}"
        f"/d/{dashboard['uid']}"
        "?orgId=1"
        "&kiosk"
        "&refresh=5s"
    )

    return templates.TemplateResponse(
        request=request,
        name="monitoring.html",
        context={
            "page_title": "Monitoring",
            "dashboards": GRAFANA_DASHBOARDS,
            "selected_view": view,
            "selected_dashboard": dashboard,
            "dashboard_url": dashboard_url,
            "grafana_base_url": grafana_base_url,
            "netbox_url": NETBOX_URL,
        },
    )
