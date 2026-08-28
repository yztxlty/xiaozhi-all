from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from model_router.contracts import (
    LLMCancelled,
    LLMCompleted,
    LLMFailed,
    LLMProvider,
    LLMRequest,
    LLMStarted,
    LLMStreamEvent,
    LLMTextDelta,
)

from .client import DifyWorkflowClient
from .config import DifyWorkflowSettings
from .errors import DifyClientError
from .event_parser import DifyEvent, DifyProtocolError
from .input_mapper import map_dify_request
from .metrics import (
    DIFY_REQUESTS,
    DIFY_STOP_REQUESTS,
    DIFY_TOTAL_SECONDS,
    DIFY_TTFT_SECONDS,
)


class DifyWorkflowProvider(LLMProvider):
    def __init__(
        self,
        settings: DifyWorkflowSettings,
        client: DifyWorkflowClient,
    ) -> None:
        self.settings = settings
        self.client = client

    def _base(self, request: LLMRequest) -> dict[str, str]:
        return {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "generation_id": request.generation_id,
            "provider": self.settings.provider_id,
        }

    async def _stop(self, task_id: str | None, user: str) -> None:
        if not task_id:
            return
        result = "failed"
        try:
            await asyncio.wait_for(
                self.client.stop(task_id, user),
                timeout=self.settings.stop_timeout_ms / 1000,
            )
            result = "succeeded"
        except Exception:
            pass
        finally:
            DIFY_STOP_REQUESTS.labels(
                provider=self.settings.provider_id,
                result=result,
            ).inc()

    @staticmethod
    async def _close(iterator: AsyncIterator[DifyEvent]) -> None:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            with suppress(Exception):
                await close()

    @staticmethod
    def _usage(data: dict[str, Any]) -> dict[str, int | float]:
        usage: dict[str, int | float] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[key] = value
        return usage

    async def stream(
        self,
        request: LLMRequest,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[LLMStreamEvent]:
        started_at = time.perf_counter()
        result = "incomplete"
        if cancel_event.is_set():
            result = "cancelled"
            yield LLMCancelled(**self._base(request))
            DIFY_REQUESTS.labels(
                provider=self.settings.provider_id,
                result=result,
            ).inc()
            DIFY_TOTAL_SECONDS.labels(
                provider=self.settings.provider_id,
                status=result,
            ).observe(time.perf_counter() - started_at)
            return

        sequence = 0
        parts: list[str] = []
        task_id: str | None = None
        emitted_started = False
        iterator = self.client.stream(map_dify_request(request)).__aiter__()

        try:
            while True:
                next_event = asyncio.create_task(anext(iterator))
                cancellation = asyncio.create_task(cancel_event.wait())
                done, _ = await asyncio.wait(
                    {next_event, cancellation},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if cancellation in done and cancellation.result():
                    next_event.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await next_event
                    await self._close(iterator)
                    await self._stop(task_id, request.user_id)
                    result = "cancelled"
                    yield LLMCancelled(**self._base(request))
                    return

                cancellation.cancel()
                with suppress(asyncio.CancelledError):
                    await cancellation

                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    result = "failed"
                    yield LLMFailed(
                        **self._base(request),
                        code="DIFY_STREAM_DISCONNECTED",
                        retryable=not parts,
                        delta_emitted=bool(parts),
                    )
                    return

                task_id = event.task_id or task_id

                if event.event == "workflow_started":
                    if not emitted_started:
                        emitted_started = True
                        yield LLMStarted(**self._base(request))
                    continue

                if event.event == "text_chunk":
                    text = event.data.get("text")
                    if isinstance(text, str) and text:
                        if sequence == 0:
                            DIFY_TTFT_SECONDS.labels(
                                provider=self.settings.provider_id
                            ).observe(time.perf_counter() - started_at)
                        sequence += 1
                        parts.append(text)
                        yield LLMTextDelta.from_request(
                            request,
                            self.settings.provider_id,
                            sequence,
                            text,
                        )
                    continue

                if event.event == "workflow_finished":
                    if event.data.get("status") == "succeeded":
                        result = "completed"
                        outputs = event.data.get("outputs")
                        yield LLMCompleted(
                            **self._base(request),
                            reply_text="".join(parts),
                            usage=self._usage(event.data),
                            metadata=outputs if isinstance(outputs, dict) else {},
                        )
                    else:
                        result = "failed"
                        yield LLMFailed(
                            **self._base(request),
                            code="DIFY_WORKFLOW_FAILED",
                            retryable=False,
                            delta_emitted=bool(parts),
                        )
                    return

                if event.event == "node_finished" and event.data.get("status") == "failed":
                    result = "failed"
                    yield LLMFailed(
                        **self._base(request),
                        code="DIFY_WORKFLOW_FAILED",
                        retryable=False,
                        delta_emitted=bool(parts),
                    )
                    return

                if event.event in {"workflow_paused", "human_input_required"}:
                    result = "failed"
                    yield LLMFailed(
                        **self._base(request),
                        code="DIFY_UNSUPPORTED_PAUSE",
                        retryable=False,
                        delta_emitted=bool(parts),
                    )
                    return

                if event.event == "error":
                    result = "failed"
                    yield LLMFailed(
                        **self._base(request),
                        code="DIFY_WORKFLOW_FAILED",
                        retryable=not parts and event.status in {429, 502, 503, 504},
                        delta_emitted=bool(parts),
                    )
                    return
        except DifyClientError as exc:
            result = "failed"
            yield LLMFailed(
                **self._base(request),
                code=exc.code,
                retryable=exc.retryable and not parts,
                delta_emitted=bool(parts),
            )
        except DifyProtocolError:
            result = "failed"
            yield LLMFailed(
                **self._base(request),
                code="DIFY_PROTOCOL_ERROR",
                retryable=False,
                delta_emitted=bool(parts),
            )
        except asyncio.CancelledError:
            result = "cancelled"
            await self._close(iterator)
            await self._stop(task_id, request.user_id)
            raise
        finally:
            await self._close(iterator)
            DIFY_REQUESTS.labels(
                provider=self.settings.provider_id,
                result=result,
            ).inc()
            DIFY_TOTAL_SECONDS.labels(
                provider=self.settings.provider_id,
                status=result,
            ).observe(time.perf_counter() - started_at)
