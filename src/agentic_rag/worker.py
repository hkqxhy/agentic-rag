from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from .broker import RunBroker
from .database import Database
from .repository import RunNotFoundError, RunRepository
from .schemas import MessageView
from .settings import Settings, get_settings

LOGGER = logging.getLogger(__name__)


def build_phase1_reply(question: str) -> str:
    topic = " ".join(question.split())[:80]
    return (
        f"我已收到你的问题：{topic}。\n\n"
        "当前 Phase 1 已接通持久会话、任务队列和流式事件链路。"
        "正式的 Agent 决策、知识检索与引用生成将在后续阶段接入；"
        "在此之前，本响应只用于验证工程链路，不代表真实校务答案。"
    )


def chunk_text(text: str, size: int = 14) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


async def process_run(run_id: UUID, database: Database, broker: RunBroker) -> None:
    try:
        async with database.session() as session:
            repository = RunRepository(session)
            run = await repository.get(run_id)
            input_message = await repository.get_input_message(run)
            await repository.mark_running(run)
            await session.commit()

        await broker.publish(run_id, "run.status", {"stage": "processing"})
        reply = build_phase1_reply(input_message.content)
        for delta in chunk_text(reply):
            if await broker.is_cancelled(run_id):
                return
            await broker.publish(run_id, "message.delta", {"text": delta})
            await asyncio.sleep(0.025)

        async with database.session() as session:
            repository = RunRepository(session)
            run = await repository.get(run_id)
            message = await repository.complete(run, reply)
            await session.commit()
            message_payload = MessageView.model_validate(message).model_dump(mode="json")
        await broker.publish(run_id, "message.completed", {"message": message_payload})
    except RunNotFoundError:
        LOGGER.warning("Ignoring missing run %s", run_id)
    except Exception as exc:
        LOGGER.exception("Run %s failed", run_id)
        async with database.session() as session:
            repository = RunRepository(session)
            try:
                run = await repository.get(run_id)
                await repository.fail(run, "WORKER_ERROR", str(exc))
                await session.commit()
            except RunNotFoundError:
                pass
        await broker.publish(
            run_id,
            "run.failed",
            {"code": "WORKER_ERROR", "message": "The run could not be completed."},
        )


async def worker_loop(settings: Settings | None = None) -> None:
    app_settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, app_settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database = Database(app_settings.database_url)
    broker = RunBroker.from_settings(app_settings)
    LOGGER.info("Worker is ready")
    try:
        while True:
            run_id = await broker.dequeue(timeout_seconds=5)
            if run_id is None:
                continue
            await process_run(run_id, database, broker)
    finally:
        await broker.close()
        await database.close()


def run() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    run()
