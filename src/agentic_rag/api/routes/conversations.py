from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

from agentic_rag.repository import ConversationNotFoundError, ConversationRepository
from agentic_rag.schemas import (
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
) -> ConversationSummary:
    async with request.app.state.database.session() as session:
        repository = ConversationRepository(session)
        conversation = await repository.create(payload.title)
        await session.commit()
        return ConversationSummary.model_validate(conversation)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(request: Request) -> list[ConversationSummary]:
    async with request.app.state.database.session() as session:
        conversations = await ConversationRepository(session).list_active()
        return [ConversationSummary.model_validate(item) for item in conversations]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: UUID, request: Request) -> ConversationDetail:
    async with request.app.state.database.session() as session:
        try:
            conversation = await ConversationRepository(session).get(conversation_id)
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from exc
        return ConversationDetail.model_validate(conversation)


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    request: Request,
) -> ConversationSummary:
    async with request.app.state.database.session() as session:
        try:
            conversation = await ConversationRepository(session).rename(
                conversation_id, payload.title
            )
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from exc
        await session.commit()
        return ConversationSummary.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: UUID, request: Request) -> Response:
    async with request.app.state.database.session() as session:
        try:
            await ConversationRepository(session).soft_delete(conversation_id)
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from exc
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
) -> RunAccepted:
    async with request.app.state.database.session() as session:
        repository = ConversationRepository(session)
        try:
            message, run = await repository.add_user_message_and_run(
                conversation_id,
                payload.content,
            )
        except ConversationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            ) from exc
        await session.commit()
    try:
        await request.app.state.broker.enqueue(run.id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Run queue is unavailable",
        ) from exc
    return RunAccepted(
        run_id=run.id,
        conversation_id=conversation_id,
        input_message=MessageView.model_validate(message),
        status=RunStatus.QUEUED,
    )
