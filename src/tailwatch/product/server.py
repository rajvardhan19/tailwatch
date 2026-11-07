"""ASGI entry point. Default preview is synthetic and needs no database."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine

from tailwatch.product.api import create_app

ASSETS = Path(__file__).resolve().parents[1] / "static"
url = os.environ.get("TAILWATCH_DATABASE_URL")
app = (
    create_app(
        create_engine(url, pool_pre_ping=True),
        production_enabled=os.environ.get("TAILWATCH_ENABLE_PRODUCTION") == "1",
    )
    if url
    else FastAPI(title="Tailwatch synthetic preview")
)
app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(
        ASSETS / "index.html",
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
