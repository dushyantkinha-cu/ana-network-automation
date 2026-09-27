from urllib.parse import quote

from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse

from webapp.clients.jenkins import (
    get_deployment_job,
    trigger_deployment,
)
from webapp.clients.netbox import netbox_get
from webapp.config import (
    NETBOX_URL,
    PROFILE_CHOICE_SET_ID,
    PROFILE_TEMPLATE_MAP,
    ROUTING_CHOICE_SET_ID,
)
from webapp.services.automation import (
    golden_snapshots,
    latest_validation_report,
    run_validation,
    validation_step_status,
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


@router.get("/automation")
def automation_page(
    request: Request,
    device: str | None = None,
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

    selected_device = None
    raw_device = None
    routing_choices = []
    compatible_profiles = []
    template_path = None
    wan_state = None
    sites = []
    deployment_job = None
    deployment_error = None

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

            routing_choices = get_choice_values(
                ROUTING_CHOICE_SET_ID
            )

            profile_choices = get_choice_values(
                PROFILE_CHOICE_SET_ID
            )

            sites = get_sites()

            wan_state = get_wan_address_state(
                selected_device
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

        try:
            deployment_job = get_deployment_job()
        except RuntimeError as exc:
            deployment_error = str(exc)

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
            "selected_device": selected_device,
            "raw_device": raw_device,
            "routing_choices": routing_choices,
            "compatible_profiles": compatible_profiles,
            "template_path": template_path,
            "wan_state": wan_state,
            "sites": sites,
            "deployment_job": deployment_job,
            "deployment_error": deployment_error,
            "validation": validation,
            "report": report,
            "step_status": validation_step_status(
                report
            ),
            "golden_snapshots": snapshots,
            "latest_golden": latest_golden,
            "netbox_url": NETBOX_URL,
            "action_status": status,
            "action_message": message,
        },
    )


@router.post("/automation/deploy")
def deployment_action(
    device: str = Form(...),
    action: str = Form(...),
):
    if action != "dry-run":
        raise HTTPException(
            status_code=400,
            detail=(
                "Only dry-run deployment is currently "
                "enabled from the portal."
            ),
        )

    try:
        managed_device = find_managed_device(device)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    if (
        not managed_device
        or managed_device.get("automation_managed")
        is not True
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Device is not in managed "
                "automation scope."
            ),
        )

    try:
        trigger_deployment(
            device=device,
            action="dry-run",
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    message = (
        f"Dry run queued successfully in Jenkins "
        f"for {device}."
    )

    return RedirectResponse(
        url=(
            "/automation"
            f"?device={quote(device)}"
            "&status=success"
            f"&message={quote(message)}"
        ),
        status_code=303,
    )


@router.post("/automation/validate")
def run_validation_action(
    device: str | None = None,
):
    result = run_validation(
        hostname=device,
    )

    if result["returncode"] == 0:
        status = "success"

        if device:
            message = (
                f"Validation completed successfully "
                f"for {device}."
            )
        else:
            message = (
                "Validation completed successfully. "
                "All managed devices passed."
            )

    else:
        status = "error"

        if device:
            message = (
                f"Validation failed for {device}. "
                "Review the latest validation report "
                "and server logs."
            )
        else:
            message = (
                "Validation failed. Review the latest "
                "validation report and server logs."
            )

    query = []

    if device:
        query.append(
            f"device={quote(device)}"
        )

    query.extend(
        [
            f"status={quote(status)}",
            f"message={quote(message)}",
        ]
    )

    return RedirectResponse(
        url="/automation?" + "&".join(query),
        status_code=303,
    )
