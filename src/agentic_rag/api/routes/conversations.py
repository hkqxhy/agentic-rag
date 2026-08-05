from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError

from agentic_rag.auth import AuditRepository, request_fingerprint, require_user
from agentic_rag.models import UserModel
from agentic_rag.repository import (
    ConversationNotFoundError,
    ConversationRepository,
    IdempotencyConflictError,
    RunNotFoundError,
    RunRepository,
)
from agentic_rag.schemas import (
    ActiveRunView,
    ConversationCreate,
    ConversationDetail,
    ConversationSummary,
    ConversationUpdate,
    MessageCreate,
    MessageView,
    RunAccepted,
    RunStatus,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    request: Request,
    user: Annotated[UserModel, Depends(require_user)],
) -> ConversationSummary:
    async with request.app.state.database.session() as session:
        repository = ConversationRepository(session, user.id)
        conversation = await repository.create(payload.title)
        await session.commit()
        return ConversationSummary.model_validate(conversation)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    request: Request,
    user: Annotated[UserModel, Depends(require_user)],
) -> list[ConversationSummary]:
    async with request.app.state.database.session() as session:
        conversations = await ConversationRepository(session, user.id).list_active()
        return [ConversationSummary.model_validate(item) for item in conversations]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    request: Request,
    user: Annotated[UserModel, Depends(require_user)],
) -> ConversationDetail:
    async with request.app.state.database.session() as session:
        try:
            repository = ConversationRepository(session, user.id)
            conversation = await repository.get(conversation_id)
            active_run = await repository.get_active_run(conversation_id)
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from exc
        summary = ConversationSummary.model_validate(conversation)
        return ConversationDetail(
            **summary.model_dump(),
            messages=[MessageView.model_validate(item) for item in conversation.messages],
            active_run=(
                ActiveRunView(run_id=active_run.id, status=RunStatus(active_run.status))
                if active_run
                else None
            ),
        )


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    request: Request,
    user: Annotated[UserModel, Depends(require_user)],
) -> ConversationSummary:
    async with request.app.state.database.session() as session:
        try:
            conversation = await ConversationRepository(session, user.id).rename(
                conversation_id, payload.title
            )
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from exc
        await AuditRepository(session).record(
            "conversation.rename",
            "success",
            actor_user_id=user.id,
            target_type="conversation",
            target_id=str(conversation_id),
            request_id=request.state.request_id,
            fingerprint=request_fingerprint(request),
        )
        await session.commit()
        return ConversationSummary.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    request: Request,
    user: Annotated[UserModel, Depends(require_user)],
) -> Response:
    async with request.app.state.database.session() as session:
        try:
            await ConversationRepository(session, user.id).soft_delete(conversation_id)
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from exc
        await AuditRepository(session).record(
            "conversation.delete",
            "success",
            actor_user_id=user.id,
            target_type="conversation",
            target_id=str(conversation_id),
            request_id=request.state.request_id,
            fingerprint=request_fingerprint(request),
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{conversation_id}/messages",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_message(
    conversation_id: UUID,
    payload: MessageCreate,
    request: Request,
    user: Annotated[UserModel, Depends(require_user)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", max_length=64),
    ] = None,
) -> RunAccepted:
    settings = request.app.state.settings
    fingerprint = request_fingerprint(request)
    for scope, limit in (
        (f"question:user:{user.id}", settings.question_rate_limit),
        (f"question:client:{fingerprint}", settings.ip_question_rate_limit),
    ):
        rate = await request.app.state.broker.consume_rate_limit(
            scope,
            limit,
            settings.question_rate_window_seconds,
        )
        if not rate.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Question rate limit exceeded",
                headers={"Retry-After": str(rate.retry_after_seconds)},
            )
    async with request.app.state.database.session() as session:
        repository = ConversationRepository(session, user.id)
        try:
            message, run, created = await repository.add_user_message_and_run(
                conversation_id,
                payload.content,
                idempotency_key,
            )
            await session.commit()
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from exc
        except IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used for another message",
            ) from exc
        except IntegrityError:
            await session.rollback()
            if not idempotency_key:
                raise
            try:
                message, run = await repository.get_idempotent_run(
                    conversation_id,
                    idempotency_key,
                    payload.content,
                )
            except (IdempotencyConflictError, RunNotFoundError) as lookup_error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key could not be replayed",
                ) from lookup_error
            created = False
    if created and run.status == RunStatus.QUEUED.value:
        try:
            await request.app.state.broker.enqueue(run.id)
        except Exception as exc:
            async with request.app.state.database.session() as session:
                await RunRepository(session).fail_queued(
                    run.id,
                    "QUEUE_UNAVAILABLE",
                    "The run could not be queued",
                )
                await session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Run queue is unavailable",
            ) from exc
    return RunAccepted(
        run_id=run.id,
        conversation_id=conversation_id,
        input_message=MessageView.model_validate(message),
        status=RunStatus(run.status),
    )
