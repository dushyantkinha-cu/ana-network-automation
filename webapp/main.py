#!/usr/bin/env python3

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from webapp.config import STATIC_DIR
from webapp.routers.automation import (
    router as automation_router,
)
from webapp.routers.changes import (
    router as changes_router,
)
from webapp.routers.core import (
    router as core_router,
)
from webapp.routers.monitoring import (
    router as monitoring_router,
)
from webapp.routers.onboarding import (
    router as onboarding_router,
)
from webapp.routers.sites import (
    router as sites_router,
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

app.include_router(core_router)
app.include_router(monitoring_router)
app.include_router(automation_router)
app.include_router(changes_router)
app.include_router(sites_router)
app.include_router(onboarding_router)
