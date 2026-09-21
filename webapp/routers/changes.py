import ipaddress
from urllib.parse import quote

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse

from webapp.clients.netbox import (
    netbox_get,
    netbox_patch,
)
from webapp.config import (
    NETBOX_URL,
    PROFILE_CHOICE_SET_ID,
    PROFILE_TEMPLATE_MAP,
    ROUTING_CHOICE_SET_ID,
)
from webapp.services.changes import (
    get_wan_address_state,
)
from webapp.services.inventory import (
    find_managed_device,
    get_choice_values,
    get_devices,
    get_sites,
)
from webapp.ui import templates


router = APIRouter()
@router.get("/changes")
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


@router.post("/changes/update")
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


@router.post("/changes/update-wan")
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


@router.post("/changes/update-metadata")
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


