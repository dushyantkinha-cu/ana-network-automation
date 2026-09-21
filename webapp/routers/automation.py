from urllib.parse import quote

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse

from webapp.config import NETBOX_URL
from webapp.services.automation import (
    golden_snapshots,
    latest_validation_report,
    run_validation,
    validation_step_status,
)
from webapp.services.inventory import get_devices
from webapp.ui import templates


router = APIRouter()


@router.get("/automation")
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


@router.post("/automation/validate")
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
