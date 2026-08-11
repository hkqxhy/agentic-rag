from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

import httpx


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider returns an unusable response."""


@dataclass(slots=True)
class EmbeddingClient:
    base_url: str
    api_key: str
    model: str
    dimensions: int = 1024
    timeout_seconds: float = 15.0
    max_attempts: int = 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        clean_texts = [text.strip() for text in texts]
        if not clean_texts or any(not text for text in clean_texts):
            raise EmbeddingError("embedding input must contain non-empty text")
        if len(clean_texts) > 10:
            raise EmbeddingError("text-embedding-v4 accepts at most 10 texts per request")
        if not self.base_url or not self.api_key or self.api_key == "EMPTY":
            raise EmbeddingError("embedding provider is not configured")

        endpoint = f"{self.base_url.rstrip('/')}/embeddings"
        payload = {
            "model": self.model,
            "input": clean_texts,
            "dimensions": self.dimensions,
        }
        last_error: Exception | None = None
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = await client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    return self._parse_response(response.json(), len(clean_texts))
                except (httpx.HTTPError, ValueError, EmbeddingError) as exc:
                    last_error = exc
                    if attempt < self.max_attempts:
                        await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
        raise EmbeddingError(f"embedding request failed after retries: {last_error}")

    def _parse_response(self, payload: dict[str, Any], expected: int) -> list[list[float]]:
        raw_items = payload.get("data")
        if not isinstance(raw_items, list) or len(raw_items) != expected:
            raise EmbeddingError("embedding response count does not match input count")

        ordered = sorted(raw_items, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for item in ordered:
            raw_vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(raw_vector, list) or len(raw_vector) != self.dimensions:
                raise EmbeddingError(
                    f"embedding dimension mismatch: expected {self.dimensions}"
                )
            vector = [float(value) for value in raw_vector]
            if any(not math.isfinite(value) for value in vector):
                raise EmbeddingError("embedding contains non-finite values")
            vectors.append(vector)
        return vectors
