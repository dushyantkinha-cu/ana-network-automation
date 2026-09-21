from collections import Counter

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse

from webapp.config import NETBOX_URL
from webapp.services.inventory import (
    get_devices,
    load_inventory,
)
from webapp.ui import templates


router = APIRouter()


@router.get("/health")
def health():
    return {
        "status": "ok",
        "application": "ANA Network Automation",
    }


@router.get("/api/inventory")
def api_inventory():
    try:
        inventory = load_inventory()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    return JSONResponse(content=inventory)


@router.get("/")
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


@router.get("/inventory")
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
