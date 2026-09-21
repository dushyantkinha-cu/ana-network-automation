from urllib.parse import quote

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse

from webapp.clients.netbox import (
    netbox_get,
    netbox_post,
)
from webapp.config import (
    NETBOX_URL,
    SITE_STATUS_CHOICES,
)
from webapp.services.changes import make_slug
from webapp.ui import templates


router = APIRouter()
@router.get("/changes/new-site")
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


@router.post("/changes/new-site")
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


