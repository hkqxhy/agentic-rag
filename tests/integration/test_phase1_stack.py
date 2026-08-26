from __future__ import annotations

import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from agentic_rag.api.app import create_app
from agentic_rag.database import Database
from agentic_rag.knowledge.dense import DenseRetrievalService, serialize_vector
from agentic_rag.knowledge.embedding import EmbeddingClient
from agentic_rag.settings import Settings
from agentic_rag.worker import process_run

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTIC_RAG_RUN_INTEGRATION") != "1",
    reason="set AGENTIC_RAG_RUN_INTEGRATION=1 with PostgreSQL and Redis available",
)


class FakeEmbeddingClient(EmbeddingClient):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        assert texts
        return [[1.0, *([0.0] * 1023)] for _ in texts]


@pytest.mark.asyncio
async def test_idle_queue_wait_outlives_redis_py_default_timeout() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        run_queue_key=f"agentic_rag:test:idle:{uuid4()}",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert await app.state.broker.dequeue(timeout_seconds=6) is None


@pytest.mark.asyncio
async def test_pgvector_dense_retrieval_round_trip() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        dense_retrieval_mode="hybrid",
        embedding_base_url="https://example.test/v1",
        embedding_api_key="test-key",
        dense_min_similarity=0.4,
    )
    database = Database(settings.database_url)
    suffix = uuid4().hex
    document_id = f"dense-doc-{suffix}"
    chunk_id = f"dense-chunk-{suffix}"
    vector = serialize_vector([1.0, *([0.0] * 1023)])
    try:
        async with database.session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_documents (
                        id, source_uri, title, status, authority_level, checksum, metadata
                    ) VALUES (:id, :source, 'Dense test', 'active', 'official',
                              :checksum, '{"authority_level":"official","status":"active"}'::jsonb)
                    """
                ),
                {"id": document_id, "source": document_id, "checksum": f"sha256:{'0' * 64}"},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_chunks (
                        id, document_id, chunk_index, content, title, source,
                        metadata, content_hash, status
                    ) VALUES (
                        :id, :document_id, 0, '校园卡挂失补办流程', '校园卡',
                        :source, '{"authority_level":"official","status":"active"}'::jsonb,
                            :content_hash, 'active'
                    )
                    """
                ),
                {
                    "id": chunk_id,
                    "document_id": document_id,
                    "source": document_id,
                    "content_hash": "1" * 64,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO knowledge_embeddings (
                        chunk_id, embedding_model, embedding_version,
                        dimensions, content_hash, embedding
                    ) VALUES (
                        :chunk_id, :model, :version, 1024, :content_hash,
                        CAST(:embedding AS vector)
                    )
                    """
                ),
                {
                    "chunk_id": chunk_id,
                    "model": settings.embedding_model,
                    "version": settings.embedding_version,
                    "content_hash": "1" * 64,
                    "embedding": vector,
                },
            )
            await session.commit()

        service = DenseRetrievalService(
            settings,
            embedding_client=FakeEmbeddingClient(
                base_url="https://example.test/v1",
                api_key="test-key",
                model="test-model",
            ),
        )
        result = await service.search("饭卡丢了", database)

        assert result.diagnostics["status"] == "ok"
        assert result.hits[0].chunk.id == chunk_id
        assert result.hits[0].score == pytest.approx(1.0)
    finally:
        async with database.session() as session:
            await session.execute(
                text(
                    "DELETE FROM knowledge_documents WHERE id = :id"
                ),
                {"id": document_id},
            )
            await session.commit()
        await database.close()


@pytest.mark.asyncio
async def test_authenticated_owned_idempotent_stream_round_trip() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        auth_rate_limit=100,
        question_rate_limit=100,
        ip_question_rate_limit=200,
    )
    app = create_app(settings)
    suffix = uuid4().hex[:12]

    async with app.router.lifespan_context(app):
        await app.state.broker.redis.delete(settings.run_queue_key)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as owner:
            register = await owner.post(
                "/api/v1/auth/register",
                json={
                    "email": f"owner-{suffix}@example.com",
                    "username": f"owner_{suffix}",
                    "password": "correct-horse-battery-staple",
                },
            )
            assert register.status_code == 201
            assert settings.session_cookie_name in owner.cookies

            created = await owner.post(
                "/api/v1/conversations",
                json={"title": "集成测试"},
            )
            assert created.status_code == 201
            conversation_id = created.json()["id"]

            message_payload = {"content": "校园卡丢失后怎么补办？"}
            idempotency_headers = {"Idempotency-Key": f"integration-{suffix}"}
            accepted = await owner.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json=message_payload,
                headers=idempotency_headers,
            )
            replayed = await owner.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json=message_payload,
                headers=idempotency_headers,
            )
            assert accepted.status_code == replayed.status_code == 202
            assert accepted.json()["run_id"] == replayed.json()["run_id"]
            run_id = accepted.json()["run_id"]

            conflict = await owner.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "另一个问题"},
                headers=idempotency_headers,
            )
            assert conflict.status_code == 409

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as stranger:
                await stranger.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"stranger-{suffix}@example.com",
                        "username": f"stranger_{suffix}",
                        "password": "correct-horse-battery-staple",
                    },
                )
                hidden = await stranger.get(f"/api/v1/conversations/{conversation_id}")
                hidden_stream = await stranger.get(f"/api/v1/runs/{run_id}/events")
                assert hidden.status_code == 404
                assert hidden_stream.status_code == 404

            queued_run = await app.state.broker.dequeue(timeout_seconds=1)
            assert str(queued_run) == run_id
            await process_run(queued_run, app.state.database, app.state.broker)
            events = [event async for event in app.state.broker.events(queued_run)]
            assert events[0].event == "run.status"
            assert any(event.event == "agent.step" for event in events)
            assert any(event.event == "message.delta" for event in events)
            assert events[-1].event == "message.completed"
            completed_message = events[-1].data["message"]
            assert completed_message["message_metadata"]["agent"]["framework"] == "langgraph"

            stored = await owner.get(f"/api/v1/conversations/{conversation_id}")
            assert stored.status_code == 200
            assert stored.json()["active_run"] is None
            assert [item["role"] for item in stored.json()["messages"]] == [
                "user",
                "assistant",
            ]
            assistant_message = stored.json()["messages"][-1]
            assert assistant_message["message_metadata"]["agent"]["framework"] == "langgraph"

            logged_out = await owner.post("/api/v1/auth/logout")
            assert logged_out.status_code == 204
            assert (await owner.get("/api/v1/auth/me")).status_code == 401
