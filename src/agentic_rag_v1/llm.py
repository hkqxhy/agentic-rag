from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class OpenAICompatibleLLM:
    """Minimal OpenAI-compatible chat client using only the standard library."""

    base_url: str = ""
    api_key: str = "EMPTY"
    model: str = "qwen-plus"
    timeout: float = 30.0
    temperature: float = 0.2
    last_error: str = field(default="", init=False, repr=False)
    last_filter: str = field(default="", init=False, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url.strip())

    def chat(self, messages: list[dict[str, str]], max_tokens: int = 900) -> str | None:
        if not self.enabled:
            return None
        self.last_error = ""
        self.last_filter = ""
        url = self._chat_url()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.last_error = f"http_{exc.code}"
            return None
        except (OSError, urllib.error.URLError):
            self.last_error = "network_error"
            return None
        except json.JSONDecodeError:
            self.last_error = "invalid_response"
            return None
        content = _extract_content(data)
        if content is None:
            self.last_error = "empty_response"
        return content

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 900,
    ) -> Iterator[str]:
        if not self.enabled:
            return
        self.last_error = ""
        self.last_filter = ""
        url = self._chat_url()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        yielded = False
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload_text = line.removeprefix("data:").strip()
                    if payload_text == "[DONE]":
                        break
                    try:
                        data = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue
                    delta = _extract_delta(data)
                    if delta:
                        yielded = True
                        yield delta
        except urllib.error.HTTPError as exc:
            self.last_error = f"http_{exc.code}"
            return
        except (OSError, urllib.error.URLError):
            self.last_error = "network_error"
            return
        if not yielded:
            self.last_error = "empty_response"

    def _chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"


def _extract_content(data: dict[str, Any]) -> str | None:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


def _extract_delta(data: dict[str, Any]) -> str:
    try:
        delta = data["choices"][0].get("delta") or {}
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
    content = delta.get("content")
    if isinstance(content, str):
        return content
    return ""
