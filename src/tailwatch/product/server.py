"""ASGI entry point. Default preview is synthetic and needs no database."""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine, create_engine

from tailwatch.product.api import create_app
from tailwatch.product.messages import signer_from_env

if TYPE_CHECKING:
    from tailwatch.product.live import LiveFeeds

ASSETS = Path(__file__).resolve().parents[1] / "static"
url = os.environ.get("TAILWATCH_DATABASE_URL")


def _live_feeds(engine: Engine) -> "LiveFeeds | None":
    """Research feeds run only with an explicit, rights-reviewed source manifest."""
    manifest = os.environ.get("TAILWATCH_LIVE_MANIFEST")
    if not manifest:
        return None
    from tailwatch.product.live import LiveFeeds
    from tailwatch.sources.objects import LocalObjectStore
    from tailwatch.sources.policy import load_manifest

    return LiveFeeds(
        engine,
        load_manifest(Path(manifest)),
        LocalObjectStore(Path(os.environ.get("TAILWATCH_LIVE_OBJECTS", ".data/live-objects"))),
        os.environ.get("TAILWATCH_SEC_USER_AGENT") or None,
    )


if url:
    engine = create_engine(url, pool_pre_ping=True)
    app = create_app(
        engine,
        production_enabled=os.environ.get("TAILWATCH_ENABLE_PRODUCTION") == "1",
        signer=signer_from_env(),
        reference_source=os.environ.get("TAILWATCH_REFERENCE_SOURCE") or None,
        live=_live_feeds(engine),
        # Only for servers bound to 127.0.0.1: lets the live feeds render without a key.
        live_local_view=os.environ.get("TAILWATCH_LIVE_LOCAL_VIEW") == "1",
    )
else:
    app = FastAPI(title="Tailwatch synthetic preview")
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
