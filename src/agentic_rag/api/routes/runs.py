from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from agentic_rag.auth import AuditRepository, request_fingerprint, require_user
from agentic_rag.broker import StreamEvent
from agentic_rag.models import UserModel
from agentic_rag.repository import RunNotFoundError, RunRepository
from agentic_rag.schemas import RunStatus

router = APIRouter(prefix="/runs", tags=["runs"])


def format_sse(event: StreamEvent) -> str:
    lines: list[str] = []
    if event.event_id:
        lines.append(f"id: {event.event_id}")
    lines.append(f"event: {event.event}")
    lines.append(f"data: {json.dumps(event.data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    user: Annotated[UserModel, Depends(require_user)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    async with request.app.state.database.session() as session:
        try:
            await RunRepository(session).get_for_owner(run_id, user.id)
        except RunNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
            ) from exc

    async def event_source() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"
        async for event in request.app.state.broker.events(run_id, last_event_id or "0-0"):
            if await request.is_disconnected():
                return
            yield format_sse(event)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: UUID,
    request: Request,
    user: Annotated[UserModel, Depends(require_user)],
) -> dict[str, str]:
    async with request.app.state.database.session() as session:
        repository = RunRepository(session)
        try:
            run = await repository.get_for_owner(run_id, user.id)
        except RunNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
            ) from exc
        await repository.cancel(run)
        await AuditRepository(session).record(
            "run.cancel",
            "success",
            actor_user_id=user.id,
            target_type="run",
            target_id=str(run_id),
            request_id=request.state.request_id,
            fingerprint=request_fingerprint(request),
        )
        await session.commit()
    await request.app.state.broker.cancel(run_id)
    await request.app.state.broker.publish(
        run_id,
        "run.cancelled",
        {"status": RunStatus.CANCELLED.value},
    )
    return {"status": RunStatus.CANCELLED.value}
