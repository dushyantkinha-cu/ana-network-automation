import ipaddress
from urllib.parse import quote

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse

from webapp.clients.netbox import (
    netbox_delete,
    netbox_get,
    netbox_patch,
    netbox_post,
)
from webapp.config import (
    MANAGEMENT_NETWORK,
    NETBOX_URL,
    PROFILE_CHOICE_SET_ID,
    PROFILE_ROLE_MAP,
    PROFILE_TEMPLATE_MAP,
    ROUTING_CHOICE_SET_ID,
)
from webapp.services.inventory import (
    get_choice_values,
    get_device_types,
    get_network_roles,
    get_platforms,
    get_sites,
    get_staged_devices,
)
from webapp.ui import templates


router = APIRouter()
@router.get("/changes/new")
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


@router.post("/changes/new")
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
