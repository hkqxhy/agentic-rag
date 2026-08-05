from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import AgentRunModel, ConversationModel, MessageModel
from .schemas import MessageRole, RunStatus


class ConversationNotFoundError(LookupError):
    pass


class RunNotFoundError(LookupError):
    pass


class IdempotencyConflictError(ValueError):
    pass


class ConversationRepository:
    def __init__(self, session: AsyncSession, owner_id: UUID) -> None:
        self.session = session
        self.owner_id = owner_id

    async def create(self, title: str) -> ConversationModel:
        conversation = ConversationModel(
            owner_id=self.owner_id,
            title=title.strip() or "新对话",
        )
        self.session.add(conversation)
        await self.session.flush()
        await self.session.refresh(conversation)
        return conversation

    async def list_active(self, limit: int = 100) -> list[ConversationModel]:
        result = await self.session.scalars(
            select(ConversationModel)
            .where(
                ConversationModel.owner_id == self.owner_id,
                ConversationModel.deleted_at.is_(None),
            )
            .order_by(ConversationModel.updated_at.desc())
            .limit(limit)
        )
        return list(result)

    async def get(self, conversation_id: UUID) -> ConversationModel:
        conversation = await self.session.scalar(
            select(ConversationModel)
            .options(selectinload(ConversationModel.messages))
            .where(
                ConversationModel.id == conversation_id,
                ConversationModel.owner_id == self.owner_id,
                ConversationModel.deleted_at.is_(None),
            )
        )
        if conversation is None:
            raise ConversationNotFoundError(str(conversation_id))
        return conversation

    async def rename(self, conversation_id: UUID, title: str) -> ConversationModel:
        conversation = await self.get(conversation_id)
        conversation.title = title.strip()
        conversation.updated_at = datetime.now(UTC)
        await self.session.flush()
        return conversation

    async def soft_delete(self, conversation_id: UUID) -> None:
        conversation = await self.get(conversation_id)
        conversation.deleted_at = datetime.now(UTC)
        conversation.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def add_user_message_and_run(
        self,
        conversation_id: UUID,
        content: str,
        idempotency_key: str | None = None,
    ) -> tuple[MessageModel, AgentRunModel, bool]:
        conversation = await self.get(conversation_id)
        normalized_content = content.strip()
        if idempotency_key:
            existing = await self.session.scalar(
                select(AgentRunModel).where(
                    AgentRunModel.conversation_id == conversation.id,
                    AgentRunModel.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                input_message = await self.session.get(MessageModel, existing.input_message_id)
                if input_message is None or input_message.content != normalized_content:
                    raise IdempotencyConflictError(idempotency_key)
                return input_message, existing, False
        message = MessageModel(
            conversation_id=conversation.id,
            role=MessageRole.USER.value,
            content=normalized_content,
        )
        self.session.add(message)
        await self.session.flush()
        run = AgentRunModel(
            conversation_id=conversation.id,
            input_message_id=message.id,
            idempotency_key=idempotency_key,
            status=RunStatus.QUEUED.value,
        )
        self.session.add(run)
        conversation.updated_at = datetime.now(UTC)
        if conversation.title == "新对话":
            conversation.title = _derive_title(content)
        await self.session.flush()
        await self.session.refresh(message)
        await self.session.refresh(run)
        return message, run, True

    async def get_idempotent_run(
        self,
        conversation_id: UUID,
        idempotency_key: str,
        content: str,
    ) -> tuple[MessageModel, AgentRunModel]:
        await self.get(conversation_id)
        run = await self.session.scalar(
            select(AgentRunModel).where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.idempotency_key == idempotency_key,
            )
        )
        if run is None:
            raise RunNotFoundError(idempotency_key)
        input_message = await self.session.get(MessageModel, run.input_message_id)
        if input_message is None or input_message.content != content.strip():
            raise IdempotencyConflictError(idempotency_key)
        return input_message, run

    async def get_active_run(self, conversation_id: UUID) -> AgentRunModel | None:
        await self.get(conversation_id)
        return await self.session.scalar(
            select(AgentRunModel)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]),
            )
            .order_by(AgentRunModel.created_at.desc())
            .limit(1)
        )


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, run_id: UUID) -> AgentRunModel:
        run = await self.session.get(AgentRunModel, run_id)
        if run is None:
            raise RunNotFoundError(str(run_id))
        return run

    async def get_for_owner(self, run_id: UUID, owner_id: UUID) -> AgentRunModel:
        run = await self.session.scalar(
            select(AgentRunModel)
            .join(ConversationModel, ConversationModel.id == AgentRunModel.conversation_id)
            .where(
                AgentRunModel.id == run_id,
                ConversationModel.owner_id == owner_id,
                ConversationModel.deleted_at.is_(None),
            )
        )
        if run is None:
            raise RunNotFoundError(str(run_id))
        return run

    async def get_input_message(self, run: AgentRunModel) -> MessageModel:
        message = await self.session.get(MessageModel, run.input_message_id)
        if message is None:
            raise RuntimeError(f"Input message {run.input_message_id} is missing")
        return message

    async def mark_running(self, run: AgentRunModel) -> None:
        run.status = RunStatus.RUNNING.value
        run.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def claim_queued(self, run_id: UUID) -> bool:
        result = await self.session.execute(
            update(AgentRunModel)
            .where(
                AgentRunModel.id == run_id,
                AgentRunModel.status == RunStatus.QUEUED.value,
            )
            .values(status=RunStatus.RUNNING.value, updated_at=datetime.now(UTC))
            .returning(AgentRunModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def complete(self, run: AgentRunModel, content: str) -> MessageModel:
        message = MessageModel(
            conversation_id=run.conversation_id,
            role=MessageRole.ASSISTANT.value,
            content=content,
        )
        self.session.add(message)
        await self.session.flush()
        run.output_message_id = message.id
        run.status = RunStatus.COMPLETED.value
        run.updated_at = datetime.now(UTC)
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def fail(self, run: AgentRunModel, code: str, message: str) -> None:
        run.status = RunStatus.FAILED.value
        run.error_code = code[:80]
        run.error_message = message[:2_000]
        run.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def fail_queued(self, run_id: UUID, code: str, message: str) -> None:
        run = await self.get(run_id)
        if run.status == RunStatus.QUEUED.value:
            await self.fail(run, code, message)

    async def cancel(self, run: AgentRunModel) -> None:
        if run.status in {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }:
            return
        run.status = RunStatus.CANCELLED.value
        run.updated_at = datetime.now(UTC)
        await self.session.flush()


def _derive_title(content: str) -> str:
    normalized = " ".join(content.split())
    return normalized[:36] + ("..." if len(normalized) > 36 else "")
