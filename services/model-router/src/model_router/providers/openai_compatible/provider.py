from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from model_router.core.contracts import (
    LLMCancelled,
    LLMCompleted,
    LLMFailed,
    LLMProvider,
    LLMRequest,
    LLMStarted,
    LLMStreamEvent,
    LLMTextDelta,
)

from .client import OpenAICompatibleClient, OpenAICompatibleClientError
from .config import OpenAICompatibleSettings
from .input_mapper import map_chat_completion_request


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, settings: OpenAICompatibleSettings, client: OpenAICompatibleClient) -> None:
        self.settings = settings
        self.client = client

    def _base(self, request: LLMRequest) -> dict[str, str]:
        return {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "generation_id": request.generation_id,
            "provider": self.settings.provider_id,
        }

    async def stream(
        self,
        request: LLMRequest,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[LLMStreamEvent]:
        if cancel_event.is_set():
            yield LLMCancelled(**self._base(request))
            return

        yield LLMStarted(**self._base(request))
        iterator = self.client.stream(map_chat_completion_request(request, self.settings)).__aiter__()
        sequence = 0
        parts: list[str] = []
        try:
            while True:
                next_event = asyncio.create_task(anext(iterator))
                cancelled = asyncio.create_task(cancel_event.wait())
                done, _ = await asyncio.wait({next_event, cancelled}, return_when=asyncio.FIRST_COMPLETED)
                if cancelled in done and cancelled.result():
                    next_event.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await next_event
                    yield LLMCancelled(**self._base(request))
                    return
                cancelled.cancel()
                with suppress(asyncio.CancelledError):
                    await cancelled

                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    yield LLMFailed(
                        **self._base(request),
                        code="OPENAI_COMPATIBLE_STREAM_DISCONNECTED",
                        retryable=not parts,
                        delta_emitted=bool(parts),
                    )
                    return

                if event.text:
                    sequence += 1
                    parts.append(event.text)
                    yield LLMTextDelta.from_request(
                        request,
                        self.settings.provider_id,
                        sequence,
                        event.text,
                    )
                if event.done:
                    yield LLMCompleted(
                        **self._base(request),
                        reply_text="".join(parts),
                        finish_reason=event.finish_reason or "stop",
                        usage=event.usage,
                    )
                    return
        except OpenAICompatibleClientError as exc:
            yield LLMFailed(
                **self._base(request),
                code=exc.code,
                retryable=exc.retryable and not parts,
                delta_emitted=bool(parts),
            )
        finally:
            close = getattr(iterator, "aclose", None)
            if close:
                with suppress(Exception):
                    await close()
