from __future__ import annotations

import os

import pytest

from agentic_rag.broker import RunBroker
from agentic_rag.database import Database
from agentic_rag.repository import ConversationRepository
from agentic_rag.settings import Settings
from agentic_rag.worker import process_run

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTIC_RAG_RUN_INTEGRATION") != "1",
    reason="set AGENTIC_RAG_RUN_INTEGRATION=1 with PostgreSQL and Redis available",
)


@pytest.mark.asyncio
async def test_persisted_conversation_queue_and_stream_round_trip() -> None:
    settings = Settings(_env_file=None)
    database = Database(settings.database_url)
    broker = RunBroker.from_settings(settings)

    try:
        async with database.session() as session:
            repository = ConversationRepository(session)
            conversation = await repository.create("集成测试")
            input_message, run = await repository.add_user_message_and_run(
                conversation.id,
                "校园卡丢失后怎么补办？",
            )
            await session.commit()

        await broker.enqueue(run.id)
        dequeued = await broker.dequeue(timeout_seconds=1)
        assert dequeued == run.id

        await process_run(run.id, database, broker)
        events = [event async for event in broker.events(run.id)]

        assert events[0].event == "run.status"
        assert any(event.event == "message.delta" for event in events)
        assert events[-1].event == "message.completed"
        assert events[-1].data["message"]["conversation_id"] == str(conversation.id)
        assert input_message.content in "校园卡丢失后怎么补办？"

        async with database.session() as session:
            stored = await ConversationRepository(session).get(conversation.id)
            assert [message.role for message in stored.messages] == ["user", "assistant"]
    finally:
        await broker.close()
        await database.close()
