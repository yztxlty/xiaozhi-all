from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

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

from .client import DifyChatflowClient
from .config import DifyChatflowSettings
from .errors import DifyChatflowClientError
from .event_parser import DifyChatflowEvent, DifyChatflowProtocolError
from .input_mapper import map_chatflow_request


class DifyChatflowProvider(LLMProvider):
    def __init__(self, settings: DifyChatflowSettings, client: DifyChatflowClient) -> None:
        self.settings = settings
        self.client = client

    def _base(self, request: LLMRequest) -> dict[str, str]:
        return {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "generation_id": request.generation_id,
            "provider": self.settings.provider_id,
        }

    @staticmethod
    def _usage(metadata: dict[str, Any]) -> dict[str, int | float]:
        source = metadata.get("usage", {})
        if not isinstance(source, dict):
            return {}
        return {
            key: value
            for key, value in source.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
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
        iterator = self.client.stream(map_chatflow_request(request)).__aiter__()
        task_id: str | None = None
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
                    if task_id:
                        with suppress(Exception):
                            await self.client.stop(task_id, request.user_id)
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
                        code="DIFY_STREAM_DISCONNECTED",
                        retryable=not parts,
                        delta_emitted=bool(parts),
                    )
                    return

                task_id = event.task_id or task_id
                if event.event in {"message", "agent_message"} and event.answer:
                    sequence += 1
                    parts.append(event.answer)
                    yield LLMTextDelta.from_request(
                        request,
                        self.settings.provider_id,
                        sequence,
                        event.answer,
                    )
                elif event.event == "message_end":
                    yield LLMCompleted(
                        **self._base(request),
                        reply_text="".join(parts),
                        usage=self._usage(event.metadata),
                        metadata={},
                    )
                    return
                elif event.event == "error" or (
                    event.event == "workflow_finished" and event.data.get("status") == "failed"
                ):
                    yield LLMFailed(
                        **self._base(request),
                        code=event.code or "DIFY_CHATFLOW_FAILED",
                        retryable=not parts and event.status in {429, 502, 503, 504},
                        delta_emitted=bool(parts),
                    )
                    return
        except DifyChatflowClientError as exc:
            yield LLMFailed(
                **self._base(request),
                code=exc.code,
                retryable=exc.retryable and not parts,
                delta_emitted=bool(parts),
            )
        except DifyChatflowProtocolError:
            yield LLMFailed(
                **self._base(request),
                code="DIFY_PROTOCOL_ERROR",
                retryable=False,
                delta_emitted=bool(parts),
            )
        finally:
            close = getattr(iterator, "aclose", None)
            if close:
                with suppress(Exception):
                    await close()
