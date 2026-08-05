from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis

from .settings import Settings


@dataclass(slots=True, frozen=True)
class StreamEvent:
    event: str
    data: dict[str, Any]
    event_id: str | None = None


@dataclass(slots=True, frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RunBroker:
    _RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.settings = settings

    @classmethod
    def from_settings(cls, settings: Settings) -> RunBroker:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        return cls(client, settings)

    def stream_key(self, run_id: UUID) -> str:
        return f"{self.settings.run_stream_prefix}:{run_id}"

    def cancel_key(self, run_id: UUID) -> str:
        return f"{self.settings.run_stream_prefix}:{run_id}:cancelled"

    async def ping(self) -> None:
        await self.redis.ping()

    async def close(self) -> None:
        await self.redis.aclose()

    async def enqueue(self, run_id: UUID) -> None:
        await self.redis.rpush(self.settings.run_queue_key, str(run_id))

    async def consume_rate_limit(
        self,
        scope: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitResult:
        key = f"agentic_rag:rate:{scope}"
        result = cast(
            list[int],
            await self.redis.eval(self._RATE_LIMIT_SCRIPT, 1, key, window_seconds),
        )
        current, ttl = int(result[0]), max(int(result[1]), 1)
        return RateLimitResult(
            allowed=current <= limit,
            remaining=max(limit - current, 0),
            retry_after_seconds=ttl,
        )

    async def cancel(self, run_id: UUID) -> None:
        await self.redis.setex(
            self.cancel_key(run_id),
            self.settings.run_event_ttl_seconds,
            "1",
        )

    async def is_cancelled(self, run_id: UUID) -> bool:
        return bool(await self.redis.exists(self.cancel_key(run_id)))

    async def dequeue(self, timeout_seconds: int = 5) -> UUID | None:
        item = cast(
            tuple[str, str] | None,
            await self.redis.blpop(self.settings.run_queue_key, timeout=timeout_seconds),
        )
        if item is None:
            return None
        _, raw_run_id = item
        return UUID(raw_run_id)

    async def publish(self, run_id: UUID, event: str, data: dict[str, Any]) -> str:
        key = self.stream_key(run_id)
        event_id = await self.redis.xadd(
            key,
            {"event": event, "data": json.dumps(data, ensure_ascii=False)},
            maxlen=1_000,
            approximate=True,
        )
        await self.redis.expire(key, self.settings.run_event_ttl_seconds)
        return str(event_id)

    async def events(
        self,
        run_id: UUID,
        last_event_id: str = "0-0",
    ) -> AsyncIterator[StreamEvent]:
        key = self.stream_key(run_id)
        cursor = last_event_id or "0-0"
        terminal_events = {"message.completed", "run.failed", "run.cancelled"}
        while True:
            response = cast(
                list[tuple[str, list[tuple[str, dict[str, str]]]]],
                await self.redis.xread({key: cursor}, count=100, block=15_000),
            )
            if not response:
                yield StreamEvent(event="heartbeat", data={})
                continue
            for _, entries in response:
                for event_id, fields in entries:
                    cursor = str(event_id)
                    event_name = str(fields.get("event", "message"))
                    raw_data = fields.get("data", "{}")
                    try:
                        data = json.loads(raw_data)
                    except json.JSONDecodeError:
                        data = {"raw": raw_data}
                    yield StreamEvent(event=event_name, data=data, event_id=cursor)
                    if event_name in terminal_events:
                        return
