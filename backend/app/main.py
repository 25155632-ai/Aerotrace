"""Aerotrace backend application factory.

Local prototype only: the server binds to loopback (see config.py),
there is no authentication, and therefore this app must not be exposed
to a network. A public deployment needs real authentication and a
security review first (see docs/SECURITY.md).

Run from backend/:
    .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from .api.routes import router
from .alerts import AlertConnectionManager, AlertDispatcher
from .config import APP_VERSION, Settings
from .models import HealthResponse
from .store import init_db


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db(settings.database_path)
        dispatcher = AlertDispatcher(settings.database_path, settings, app.state.alert_manager)
        app.state.alert_dispatcher = dispatcher
        await dispatcher.start()
        try:
            yield
        finally:
            await dispatcher.stop()

    app = FastAPI(title="Aerotrace", version=APP_VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.alert_manager = AlertConnectionManager()
    app.state.alert_dispatcher = None
    # Test seam only: production passes None and uses a real connection.
    app.state.opensky_transport = None

    # Local frontend origin only; never a wildcard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
         allow_methods=["GET", "POST", "PUT"],
        # Only the same-origin guard header (see below). Anything else
        # a browser would send still needs an explicit allowlist entry.
        allow_headers=["X-Requested-With"],
    )

    # Same-origin guard for state-changing requests. This is NOT
    # authentication: there are no accounts, and this header proves
    # nothing about who the caller is. It exists because the backend
    # binds loopback with no credentials, so a plain cross-origin
    # form POST (a "simple request", which CORS does not preflight)
    # could otherwise trigger ingestion or a detection run from any
    # website the operator happens to visit while the backend runs.
    # A custom header cannot be set by a plain form, and a fetch that
    # sets it triggers a CORS preflight that only the allowlisted
    # frontend origin passes. API clients (curl, tests) set the header
    # explicitly; see docs/SECURITY_AUDIT.md.
    @app.middleware("http")
    async def require_same_origin_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
            # Body-size bound: this app's POSTs carry no body or a tiny
            # bounding-box JSON. Starlette otherwise reads an unbounded
            # body before validation. Chunked bodies without a
            # Content-Length header are not capped by this check and
            # are noted as a residual in docs/SECURITY_AUDIT.md.
            content_length = request.headers.get("content-length")
            try:
                body_too_large = (
                    content_length is not None and int(content_length) > 1_000_000
                )
            except ValueError:
                body_too_large = True
            if body_too_large:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large for this API."},
                )
            if not request.headers.get("x-requested-with"):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "Missing X-Requested-With header: state-changing API calls must come from the Aerotrace frontend or set this header explicitly."
                    },
                )
        return await call_next(request)

    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def frontend_entrypoint() -> RedirectResponse:
        """Send browser visits to the local dashboard instead of a raw 404.

        The backend remains an API service on port 8000, while the Vite/TanStack
        frontend runs on the configured frontend origin. Keeping this redirect
        here makes the common local entrypoint (http://127.0.0.1:8000) useful
        without duplicating or mounting the frontend build inside FastAPI.
        """

        return RedirectResponse(url=settings.frontend_origin, status_code=307)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        # Service health only. Per-source availability lives at
        # GET /api/sources, where the live source is honestly reported
        # as not_verified until a live fetch succeeds.
        return HealthResponse(status="ok", version=APP_VERSION, database="ready")

    return app


app = create_app()
