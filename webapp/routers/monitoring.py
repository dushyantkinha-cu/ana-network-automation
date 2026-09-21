from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from webapp.config import (
    GRAFANA_DASHBOARDS,
    GRAFANA_PORT,
    NETBOX_URL,
)
from webapp.ui import templates


router = APIRouter()


@router.get("/monitoring")
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
