from __future__ import annotations

import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_rag.api.app import create_app
from agentic_rag.settings import Settings
from agentic_rag.worker import process_run

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTIC_RAG_RUN_INTEGRATION") != "1",
    reason="set AGENTIC_RAG_RUN_INTEGRATION=1 with PostgreSQL and Redis available",
)


@pytest.mark.asyncio
async def test_authenticated_owned_idempotent_stream_round_trip() -> None:
    settings = Settings(
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
            assert any(event.event == "message.delta" for event in events)
            assert events[-1].event == "message.completed"

            stored = await owner.get(f"/api/v1/conversations/{conversation_id}")
            assert stored.status_code == 200
            assert stored.json()["active_run"] is None
            assert [item["role"] for item in stored.json()["messages"]] == [
                "user",
                "assistant",
            ]

            logged_out = await owner.post("/api/v1/auth/logout")
            assert logged_out.status_code == 204
            assert (await owner.get("/api/v1/auth/me")).status_code == 401
