from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import RequestResponseEndpoint

from agentic_rag import __version__
from agentic_rag.broker import RunBroker
from agentic_rag.database import Database
from agentic_rag.observability import configure_logging, configure_tracing
from agentic_rag.settings import Settings, get_settings

from .routes import conversations, health, runs


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = app_settings
        application.state.database = Database(app_settings.database_url)
        application.state.broker = RunBroker.from_settings(app_settings)
        try:
            yield
        finally:
            await application.state.broker.close()
            await application.state.database.close()

    application = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if app_settings.environment != "production" else None,
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.2f}"
        return response

    application.include_router(health.router)
    application.include_router(conversations.router, prefix="/api/v1")
    application.include_router(runs.router, prefix="/api/v1")
    configure_tracing(application, app_settings)
    return application


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "agentic_rag.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
