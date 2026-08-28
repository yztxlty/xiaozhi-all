import asyncio
from collections.abc import AsyncIterator

import pytest

from model_router.contracts import (
    LLMFailed,
    LLMProvider,
    LLMRequest,
    LLMStreamEvent,
    LLMTextDelta,
)
from model_router.router import LLMRouter


class FixedProvider(LLMProvider):
    def __init__(self, provider_id: str, mode: str) -> None:
        self.provider_id = provider_id
        self.mode = mode
        self.requests: list[LLMRequest] = []

    async def stream(
        self,
        request: LLMRequest,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[LLMStreamEvent]:
        self.requests.append(request)
        base = {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "generation_id": request.generation_id,
            "provider": self.provider_id,
        }
        if self.mode == "fail_before_text":
            yield LLMFailed(
                **base,
                code="UPSTREAM_FAILED",
                retryable=True,
                delta_emitted=False,
            )
            return
        if self.mode == "partial_then_fail":
            yield LLMTextDelta(**base, sequence=1, text="半句")
            yield LLMFailed(
                **base,
                code="STREAM_DISCONNECTED",
                retryable=False,
                delta_emitted=True,
            )
            return
        yield LLMTextDelta(**base, sequence=1, text="备用回复")


@pytest.mark.asyncio
async def test_router_falls_back_before_first_text_with_new_generation(llm_request) -> None:
    primary = FixedProvider("primary", "fail_before_text")
    backup = FixedProvider("backup", "success")
    router = LLMRouter([primary, backup])

    events = [item async for item in router.stream(llm_request, asyncio.Event())]

    backup_events = [item for item in events if item.provider == "backup"]
    assert backup_events
    assert backup_events[0].generation_id != llm_request.generation_id
    assert backup.requests[0].generation_id == backup_events[0].generation_id


@pytest.mark.asyncio
async def test_router_never_splices_backup_after_text(llm_request) -> None:
    primary = FixedProvider("primary", "partial_then_fail")
    backup = FixedProvider("backup", "success")
    router = LLMRouter([primary, backup])

    events = [item async for item in router.stream(llm_request, asyncio.Event())]

    assert not backup.requests
    assert not any(item.provider == "backup" for item in events)


def test_router_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="至少需要一个"):
        LLMRouter([])
