from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import OpenAICompatibleSettings


class OpenAICompatibleClientError(RuntimeError):
    def __init__(self, code: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(slots=True)
class OpenAIStreamEvent:
    text: str = ""
    done: bool = False
    finish_reason: str | None = None
    usage: dict[str, int | float] = field(default_factory=dict)


class OpenAICompatibleClient:
    def __init__(self, settings: OpenAICompatibleSettings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key.get_secret_value()}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

    @property
    def url(self) -> str:
        return f"{str(self.settings.base_url).rstrip('/')}/chat/completions"

    @staticmethod
    def _error_from_status(status: int) -> OpenAICompatibleClientError:
        if status in {401, 403}:
            return OpenAICompatibleClientError("OPENAI_COMPATIBLE_AUTH_FAILED")
        if status == 429:
            return OpenAICompatibleClientError("OPENAI_COMPATIBLE_RATE_LIMITED", retryable=True)
        if status >= 500:
            return OpenAICompatibleClientError("OPENAI_COMPATIBLE_UPSTREAM_FAILED", retryable=True)
        return OpenAICompatibleClientError("OPENAI_COMPATIBLE_BAD_REQUEST")

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[OpenAIStreamEvent]:
        timeout = httpx.Timeout(
            connect=self.settings.connect_timeout_ms / 1000,
            read=self.settings.read_timeout_ms / 1000,
            write=3.0,
            pool=1.0,
        )
        finish_reason: str | None = None
        usage: dict[str, int | float] = {}
        try:
            async with self.http.stream(
                "POST",
                self.url,
                headers=self.headers,
                json=payload,
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    raise self._error_from_status(response.status_code)
                if "text/event-stream" not in response.headers.get("content-type", "").lower():
                    raise OpenAICompatibleClientError("OPENAI_COMPATIBLE_PROTOCOL_ERROR")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        yield OpenAIStreamEvent(done=True, finish_reason=finish_reason or "stop", usage=usage)
                        return
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise OpenAICompatibleClientError("OPENAI_COMPATIBLE_PROTOCOL_ERROR") from exc
                    choices = data.get("choices") or []
                    if choices:
                        choice = choices[0]
                        finish_reason = choice.get("finish_reason") or finish_reason
                        text = (choice.get("delta") or {}).get("content")
                        if isinstance(text, str) and text:
                            yield OpenAIStreamEvent(text=text)
                    source_usage = data.get("usage")
                    if isinstance(source_usage, dict):
                        usage = {
                            key: value
                            for key, value in source_usage.items()
                            if isinstance(value, (int, float)) and not isinstance(value, bool)
                        }
            raise OpenAICompatibleClientError("OPENAI_COMPATIBLE_STREAM_DISCONNECTED", retryable=True)
        except OpenAICompatibleClientError:
            raise
        except httpx.ConnectTimeout as exc:
            raise OpenAICompatibleClientError("OPENAI_COMPATIBLE_CONNECT_TIMEOUT", retryable=True) from exc
        except httpx.ConnectError as exc:
            raise OpenAICompatibleClientError("OPENAI_COMPATIBLE_CONNECT_FAILED", retryable=True) from exc
        except httpx.ReadTimeout as exc:
            raise OpenAICompatibleClientError("OPENAI_COMPATIBLE_READ_TIMEOUT", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise OpenAICompatibleClientError("OPENAI_COMPATIBLE_STREAM_DISCONNECTED", retryable=True) from exc
