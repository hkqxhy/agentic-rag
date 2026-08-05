from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status

from agentic_rag.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", service=request.app.state.settings.app_name)


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    dependencies: dict[str, str] = {}
    checks = {
        "postgres": request.app.state.database.ping(),
        "redis": request.app.state.broker.ping(),
    }
    results = await asyncio.gather(*checks.values(), return_exceptions=True)
    for name, result in zip(checks, results, strict=True):
        dependencies[name] = "ok" if not isinstance(result, BaseException) else "unavailable"
    if any(value != "ok" for value in dependencies.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "dependencies": dependencies},
        )
    return HealthResponse(
        status="ok",
        service=request.app.state.settings.app_name,
        dependencies=dependencies,
    )
