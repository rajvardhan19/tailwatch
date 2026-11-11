"""ASGI entry point. Default preview is synthetic and needs no database."""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine

from tailwatch.product.api import create_app
from tailwatch.product.messages import signer_from_env

ASSETS = Path(__file__).resolve().parents[1] / "static"
url = os.environ.get("TAILWATCH_DATABASE_URL")
app = (
    create_app(
        create_engine(url, pool_pre_ping=True),
        production_enabled=os.environ.get("TAILWATCH_ENABLE_PRODUCTION") == "1",
        signer=signer_from_env(),
        reference_source=os.environ.get("TAILWATCH_REFERENCE_SOURCE") or None,
    )
    if url
    else FastAPI(title="Tailwatch synthetic preview")
)
app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")


@app.middleware("http")
async def revalidate_assets(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        # Revalidate (cheap via ETag) so a dashboard fix is never served stale.
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(ASSETS / "index.html", headers=HEADERS)


@app.get("/confirm", include_in_schema=False)
def confirm() -> FileResponse:
    # Static page: the signed token stays in the fragment and is posted only on click.
    return FileResponse(ASSETS / "confirm.html", headers=HEADERS)
