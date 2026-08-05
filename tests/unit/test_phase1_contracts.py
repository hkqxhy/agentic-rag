from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from agentic_rag.api.app import create_app
from agentic_rag.api.routes.runs import format_sse
from agentic_rag.broker import RunBroker, StreamEvent
from agentic_rag.repository import _derive_title
from agentic_rag.schemas import MessageCreate
from agentic_rag.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from agentic_rag.settings import Settings
from agentic_rag.worker import build_phase1_reply, chunk_text


def test_settings_parse_cors_origins() -> None:
    settings = Settings(
        _env_file=None,
        cors_origins="http://localhost:3000, https://demo.example.com",
    )

    assert settings.allowed_origins == [
        "http://localhost:3000",
        "https://demo.example.com",
    ]


def test_production_requires_secure_cookie_and_audit_key() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")

    settings = Settings(
        _env_file=None,
        environment="production",
        session_cookie_secure=True,
        audit_hash_key="a-production-secret-that-is-not-committed",
    )
    assert settings.session_cookie_secure is True


@pytest.mark.asyncio
async def test_passwords_are_argon2_hashed_and_sessions_are_opaque() -> None:
    password_hash = await hash_password("correct-horse-battery-staple")
    token = generate_session_token()

    assert password_hash.startswith("$argon2")
    assert await verify_password("correct-horse-battery-staple", password_hash)
    assert not await verify_password("wrong-password", password_hash)
    assert len(token) >= 40
    assert hash_session_token(token) != token


@pytest.mark.asyncio
async def test_rate_limit_result_is_derived_from_atomic_script() -> None:
    class FakeRedis:
        async def eval(self, script: str, number_of_keys: int, key: str, window: int):
            assert "INCR" in script and "EXPIRE" in script
            assert number_of_keys == 1
            assert key == "agentic_rag:rate:question:user:test"
            assert window == 60
            return [4, 51]

    settings = Settings(_env_file=None)
    broker = RunBroker(FakeRedis(), settings)  # type: ignore[arg-type]
    result = await broker.consume_rate_limit("question:user:test", 3, 60)

    assert result.allowed is False
    assert result.remaining == 0
    assert result.retry_after_seconds == 51


def test_message_rejects_blank_content() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(content="")


def test_conversation_title_is_normalized_and_bounded() -> None:
    title = _derive_title("  新生   报到  " + "材料" * 30)

    assert "  " not in title
    assert title.endswith("...")
    assert len(title) == 39


def test_sse_event_contains_cursor_event_and_utf8_json() -> None:
    rendered = format_sse(
        StreamEvent(
            event="message.delta",
            event_id="171-0",
            data={"text": "你好"},
        )
    )

    assert rendered == 'id: 171-0\nevent: message.delta\ndata: {"text": "你好"}\n\n'


def test_phase1_stub_is_explicit_and_streamable() -> None:
    reply = build_phase1_reply("校园卡怎么补办？")
    chunks = chunk_text(reply, size=7)

    assert "不代表真实校务答案" in reply
    assert "".join(chunks) == reply
    assert all(1 <= len(chunk) <= 7 for chunk in chunks)


@pytest.mark.asyncio
async def test_live_health_does_not_require_dependencies() -> None:
    app = create_app(Settings(_env_file=None, app_name="Agentic RAG test"))

    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/health/live",
            headers={"X-Request-ID": str(uuid4())},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Agentic RAG test",
        "dependencies": {},
    }
    assert response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_untrusted_request_id_is_bounded_before_audit_use() -> None:
    app = create_app(Settings(_env_file=None, app_name="Agentic RAG test"))

    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/health/live",
            headers={"X-Request-ID": "x" * 200},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "x" * 200
    assert len(response.headers["X-Request-ID"]) <= 64
