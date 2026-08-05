from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest

BASE_URL = os.getenv("AGENTIC_RAG_E2E_BASE_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="set AGENTIC_RAG_E2E_BASE_URL to run staging acceptance tests",
)


def _assert_status(response: httpx.Response, expected: int) -> None:
    assert response.status_code == expected, (
        f"{response.request.method} {response.request.url} returned "
        f"{response.status_code}: {response.text[:500]}"
    )


def _stream_events(response: httpx.Response) -> Iterator[dict[str, object]]:
    event_type = "message"
    event_id: str | None = None
    data_lines: list[str] = []
    for line in response.iter_lines():
        if not line:
            if data_lines:
                yield {
                    "event": event_type,
                    "id": event_id,
                    "data": json.loads("\n".join(data_lines)),
                }
            event_type = "message"
            event_id = None
            data_lines = []
            continue
        if line.startswith(":") or line.startswith("retry:"):
            continue
        field, _, value = line.partition(":")
        value = value.lstrip()
        if field == "event":
            event_type = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value)


def _wait_for_assistant(
    client: httpx.Client,
    conversation_id: str,
    timeout_seconds: float = 10,
) -> httpx.Response:
    deadline = time.monotonic() + timeout_seconds
    latest: httpx.Response | None = None
    while time.monotonic() < deadline:
        latest = client.get(f"/api/v1/conversations/{conversation_id}")
        _assert_status(latest, 200)
        if any(message["role"] == "assistant" for message in latest.json()["messages"]):
            return latest
        time.sleep(0.1)
    assert latest is not None
    pytest.fail(f"assistant response was not persisted within {timeout_seconds}s: {latest.text}")


def test_staging_health_and_security_headers() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        live = client.get("/health/live", headers={"X-Request-ID": "x" * 200})
        _assert_status(live, 200)
        assert live.json()["status"] == "ok"
        assert 1 <= len(live.headers["X-Request-ID"]) <= 64
        assert live.headers["X-Frame-Options"] == "DENY"
        assert live.headers["X-Content-Type-Options"] == "nosniff"

        ready = client.get("/health/ready")
        _assert_status(ready, 200)
        payload = ready.json()
        assert payload["status"] == "ok"
        assert payload["dependencies"] == {"postgres": "ok", "redis": "ok"}


def test_staging_authenticated_conversation_lifecycle() -> None:
    suffix = uuid4().hex[:12]
    password = "phase1-acceptance-password-2026"
    owner_username = f"accept_owner_{suffix}"
    stranger_username = f"accept_other_{suffix}"
    metrics: dict[str, float] = {}

    with (
        httpx.Client(base_url=BASE_URL, timeout=15) as owner,
        httpx.Client(base_url=BASE_URL, timeout=15) as stranger,
    ):
        registered = owner.post(
            "/api/v1/auth/register",
            json={
                "email": f"{owner_username}@example.com",
                "username": owner_username,
                "password": password,
            },
        )
        _assert_status(registered, 201)
        session_cookie = registered.headers["Set-Cookie"]
        assert "HttpOnly" in session_cookie
        assert "SameSite=lax" in session_cookie
        assert owner.get("/api/v1/auth/me").status_code == 200

        duplicate = owner.post(
            "/api/v1/auth/register",
            json={
                "email": f"{owner_username}@example.com",
                "username": owner_username,
                "password": password,
            },
        )
        _assert_status(duplicate, 409)

        created = owner.post("/api/v1/conversations", json={"title": "Phase 1 acceptance"})
        _assert_status(created, 201)
        conversation_id = created.json()["id"]
        listed = owner.get("/api/v1/conversations")
        _assert_status(listed, 200)
        assert conversation_id in {item["id"] for item in listed.json()}

        renamed = owner.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={"title": "Phase 1 renamed"},
        )
        _assert_status(renamed, 200)
        assert renamed.json()["title"] == "Phase 1 renamed"

        question = "新生报到需要准备哪些材料？"
        idempotency_key = f"acceptance-{suffix}"
        started_at = time.perf_counter()
        accepted = owner.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": question},
            headers={"Idempotency-Key": idempotency_key},
        )
        _assert_status(accepted, 202)
        metrics["message_accept_ms"] = (time.perf_counter() - started_at) * 1000
        run_id = accepted.json()["run_id"]

        replayed = owner.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": question},
            headers={"Idempotency-Key": idempotency_key},
        )
        _assert_status(replayed, 202)
        assert replayed.json()["run_id"] == run_id
        conflict = owner.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "另一条消息"},
            headers={"Idempotency-Key": idempotency_key},
        )
        _assert_status(conflict, 409)

        other_registered = stranger.post(
            "/api/v1/auth/register",
            json={
                "email": f"{stranger_username}@example.com",
                "username": stranger_username,
                "password": password,
            },
        )
        _assert_status(other_registered, 201)
        assert stranger.get(f"/api/v1/conversations/{conversation_id}").status_code == 404
        assert (
            stranger.patch(
                f"/api/v1/conversations/{conversation_id}",
                json={"title": "must not rename"},
            ).status_code
            == 404
        )
        assert stranger.delete(f"/api/v1/conversations/{conversation_id}").status_code == 404
        assert stranger.get(f"/api/v1/runs/{run_id}/events").status_code == 404

        persisted = _wait_for_assistant(owner, conversation_id)
        metrics["persisted_complete_ms"] = (time.perf_counter() - started_at) * 1000

        stream_started_at = time.perf_counter()
        with owner.stream(
            "GET",
            f"/api/v1/runs/{run_id}/events",
            headers={"Accept-Encoding": "identity"},
            timeout=15,
        ) as stream:
            _assert_status(stream, 200)
            assert stream.headers["Content-Type"].startswith("text/event-stream")
            assert stream.headers["X-Accel-Buffering"] == "no"
            events = []
            first_event_at: float | None = None
            for event in _stream_events(stream):
                if first_event_at is None:
                    first_event_at = time.perf_counter()
                events.append(event)
                if event["event"] in {"message.completed", "run.failed", "run.cancelled"}:
                    break
        assert first_event_at is not None
        metrics["sse_first_event_ms"] = (first_event_at - stream_started_at) * 1000
        metrics["e2e_complete_ms"] = (time.perf_counter() - started_at) * 1000
        event_types = [event["event"] for event in events]
        assert "message.delta" in event_types
        assert event_types[-1] == "message.completed"
        assert all(event["id"] for event in events)

        assert persisted.json()["active_run"] is None
        assert [message["role"] for message in persisted.json()["messages"]] == [
            "user",
            "assistant",
        ]

        persistence = owner.post(
            "/api/v1/conversations",
            json={"title": "persists after login"},
        )
        _assert_status(persistence, 201)
        persistence_id = persistence.json()["id"]
        _assert_status(owner.post("/api/v1/auth/logout"), 204)
        assert owner.get("/api/v1/auth/me").status_code == 401
        logged_in = owner.post(
            "/api/v1/auth/login",
            json={"identifier": owner_username, "password": password},
        )
        _assert_status(logged_in, 200)
        _assert_status(owner.get(f"/api/v1/conversations/{persistence_id}"), 200)

        cancelled_conversation = owner.post(
            "/api/v1/conversations",
            json={"title": "cancel acceptance"},
        )
        _assert_status(cancelled_conversation, 201)
        cancelled_conversation_id = cancelled_conversation.json()["id"]
        cancellable = owner.post(
            f"/api/v1/conversations/{cancelled_conversation_id}/messages",
            json={"content": "请停止这次生成"},
            headers={"Idempotency-Key": f"cancel-{suffix}"},
        )
        _assert_status(cancellable, 202)
        cancelled_run_id = cancellable.json()["run_id"]
        cancelled = owner.post(f"/api/v1/runs/{cancelled_run_id}/cancel")
        _assert_status(cancelled, 200)
        assert cancelled.json() == {"status": "cancelled"}
        with owner.stream("GET", f"/api/v1/runs/{cancelled_run_id}/events", timeout=10) as stream:
            _assert_status(stream, 200)
            cancel_events = []
            for event in _stream_events(stream):
                cancel_events.append(event)
                if event["event"] in {"run.cancelled", "message.completed"}:
                    break
        assert cancel_events[-1]["event"] == "run.cancelled"

        for item_id in (conversation_id, persistence_id, cancelled_conversation_id):
            _assert_status(owner.delete(f"/api/v1/conversations/{item_id}"), 204)
            assert owner.get(f"/api/v1/conversations/{item_id}").status_code == 404

    print("E2E_METRICS=" + json.dumps(metrics, sort_keys=True))
