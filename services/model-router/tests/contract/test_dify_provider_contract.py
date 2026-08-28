import asyncio
from collections.abc import AsyncIterator

import pytest

from model_router.contracts import (
    LLMCancelled,
    LLMCompleted,
    LLMFailed,
    LLMStarted,
    LLMTextDelta,
)
from model_router.providers.dify_workflow.errors import DifyClientError
from model_router.providers.dify_workflow.event_parser import DifyEvent
from model_router.providers.dify_workflow.provider import DifyWorkflowProvider


def event(name: str, data: dict | None = None) -> DifyEvent:
    return DifyEvent(
        event=name,
        task_id="task_1",
        workflow_run_id="run_1",
        data=data or {},
    )


class FakeDifyClient:
    def __init__(
        self,
        events: list[DifyEvent] | None = None,
        *,
        error: Exception | None = None,
        wait_after_started: bool = False,
    ) -> None:
        self.events = events or []
        self.error = error
        self.wait_after_started = wait_after_started
        self.release = asyncio.Event()
        self.stop_calls: list[tuple[str, str]] = []

    async def stream(self, payload: dict[str, object]) -> AsyncIterator[DifyEvent]:
        for index, item in enumerate(self.events):
            yield item
            if index == 0 and self.wait_after_started:
                await self.release.wait()
        if self.error:
            raise self.error

    async def stop(self, task_id: str, user: str) -> None:
        self.stop_calls.append((task_id, user))


@pytest.mark.asyncio
async def test_text_chunks_are_numbered_and_completed_once(settings, llm_request) -> None:
    client = FakeDifyClient(
        [
            event("workflow_started"),
            event("text_chunk", {"text": "你"}),
            event("text_chunk", {"text": "好"}),
            event(
                "workflow_finished",
                {"status": "succeeded", "outputs": {"emotion": "warm"}, "total_tokens": 2},
            ),
        ]
    )
    provider = DifyWorkflowProvider(settings, client)

    events = [item async for item in provider.stream(llm_request, asyncio.Event())]

    deltas = [item for item in events if isinstance(item, LLMTextDelta)]
    terminals = [item for item in events if isinstance(item, (LLMCompleted, LLMFailed, LLMCancelled))]
    assert isinstance(events[0], LLMStarted)
    assert [item.sequence for item in deltas] == [1, 2]
    assert "".join(item.text for item in deltas) == "你好"
    assert len(terminals) == 1
    assert isinstance(terminals[0], LLMCompleted)
    assert terminals[0].reply_text == "你好"
    assert terminals[0].metadata == {"emotion": "warm"}


@pytest.mark.asyncio
async def test_cancel_stops_blocked_stream_and_drops_late_text(settings, llm_request) -> None:
    client = FakeDifyClient(
        [event("workflow_started"), event("text_chunk", {"text": "迟到文本"})],
        wait_after_started=True,
    )
    provider = DifyWorkflowProvider(settings, client)
    cancel_event = asyncio.Event()
    stream = provider.stream(llm_request, cancel_event)

    assert isinstance(await anext(stream), LLMStarted)
    cancel_event.set()
    terminal = await asyncio.wait_for(anext(stream), timeout=0.2)

    assert isinstance(terminal, LLMCancelled)
    assert client.stop_calls == [("task_1", "usr_1")]
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dify_event", "expected_code"),
    [
        (event("workflow_finished", {"status": "failed"}), "DIFY_WORKFLOW_FAILED"),
        (event("workflow_paused", {"status": "paused"}), "DIFY_UNSUPPORTED_PAUSE"),
        (event("human_input_required"), "DIFY_UNSUPPORTED_PAUSE"),
        (DifyEvent("error", None, None, {}, 503, "upstream_error", "failed"), "DIFY_WORKFLOW_FAILED"),
    ],
)
async def test_failure_events_have_one_terminal(
    settings,
    llm_request,
    dify_event: DifyEvent,
    expected_code: str,
) -> None:
    client = FakeDifyClient([event("workflow_started"), dify_event])
    provider = DifyWorkflowProvider(settings, client)

    events = [item async for item in provider.stream(llm_request, asyncio.Event())]
    terminals = [item for item in events if isinstance(item, (LLMCompleted, LLMFailed, LLMCancelled))]

    assert len(terminals) == 1
    assert isinstance(terminals[0], LLMFailed)
    assert terminals[0].code == expected_code


@pytest.mark.asyncio
async def test_transport_error_is_retryable_only_before_text(settings, llm_request) -> None:
    error = DifyClientError("DIFY_CONNECT_FAILED", "failed", retryable=True)
    before = DifyWorkflowProvider(settings, FakeDifyClient(error=error))
    after = DifyWorkflowProvider(
        settings,
        FakeDifyClient([event("workflow_started"), event("text_chunk", {"text": "半句"})], error=error),
    )

    before_events = [item async for item in before.stream(llm_request, asyncio.Event())]
    after_events = [item async for item in after.stream(llm_request, asyncio.Event())]

    assert isinstance(before_events[-1], LLMFailed)
    assert before_events[-1].retryable is True
    assert before_events[-1].delta_emitted is False
    assert isinstance(after_events[-1], LLMFailed)
    assert after_events[-1].retryable is False
    assert after_events[-1].delta_emitted is True


@pytest.mark.asyncio
async def test_stop_timeout_does_not_change_local_cancelled_state(settings, llm_request) -> None:
    class SlowStopClient(FakeDifyClient):
        async def stop(self, task_id: str, user: str) -> None:
            await asyncio.Event().wait()

    fast_stop_settings = settings.model_copy(update={"stop_timeout_ms": 100})
    client = SlowStopClient([event("workflow_started")], wait_after_started=True)
    provider = DifyWorkflowProvider(fast_stop_settings, client)
    cancel_event = asyncio.Event()
    stream = provider.stream(llm_request, cancel_event)

    assert isinstance(await anext(stream), LLMStarted)
    cancel_event.set()
    terminal = await asyncio.wait_for(anext(stream), timeout=0.2)

    assert isinstance(terminal, LLMCancelled)
