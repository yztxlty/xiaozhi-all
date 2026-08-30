import asyncio

from model_router.contracts import LLMCancelled, LLMCompleted, LLMStarted, LLMTextDelta
from model_router.providers.dify_chatflow.event_parser import DifyChatflowEvent
from model_router.providers.dify_chatflow.provider import DifyChatflowProvider


def event(name: str, *, answer: str | None = None, metadata: dict | None = None):
    return DifyChatflowEvent(
        event=name,
        task_id="task_1",
        message_id="message_1",
        conversation_id="ignored",
        workflow_run_id=None,
        answer=answer,
        data={},
        metadata=metadata or {},
    )


class FakeClient:
    async def stream(self, payload):
        yield event("message", answer="你")
        yield event("agent_message", answer="好")
        yield event("message_end", metadata={"usage": {"total_tokens": 2}})

    async def stop(self, task_id, user):
        raise AssertionError("正常完成不应停止任务")


async def test_provider_maps_chatflow_to_one_ordered_llm_stream(chatflow_settings, llm_request):
    provider = DifyChatflowProvider(chatflow_settings, FakeClient())

    events = [event async for event in provider.stream(llm_request, asyncio.Event())]

    assert isinstance(events[0], LLMStarted)
    assert [event.text for event in events if isinstance(event, LLMTextDelta)] == ["你", "好"]
    assert isinstance(events[-1], LLMCompleted)
    assert events[-1].reply_text == "你好"
    assert events[-1].usage == {"total_tokens": 2}


async def test_provider_does_not_aclose_stream_while_anext_is_cancelled(chatflow_settings, llm_request):
    release = asyncio.Event()

    class InFlightStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await release.wait()
            raise StopAsyncIteration

        async def aclose(self):
            raise AssertionError("must not close an in-flight async generator")

    class Client:
        def stream(self, _payload):
            return InFlightStream()

        async def stop(self, _task_id, _user):
            return None

    provider = DifyChatflowProvider(chatflow_settings, Client())
    cancel = asyncio.Event()
    events = []

    async def consume():
        async for item in provider.stream(llm_request, cancel):
            events.append(item)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    cancel.set()
    await task

    assert any(isinstance(item, LLMCancelled) for item in events)
