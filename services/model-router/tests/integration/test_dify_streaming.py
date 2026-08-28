import asyncio
import json

import httpx
import pytest

from model_router.providers.dify_workflow.client import DifyWorkflowClient


class GatedSSEStream(httpx.AsyncByteStream):
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release

    async def __aiter__(self):
        yield (
            b'event: ping\n\n'
            b'data: {"event":"workflow_started","task_id":"task_1",'
            b'"workflow_run_id":"run_1","data":{}}\n\n'
        )
        await self.release.wait()
        yield (
            b'data: {"event":"text_chunk","task_id":"task_1",'
            b'"workflow_run_id":"run_1","data":{"text":"\xe4\xbd\xa0"}}\n\n'
            b'data: {"event":"workflow_finished","task_id":"task_1",'
            b'"workflow_run_id":"run_1","data":{"status":"succeeded"}}\n\n'
        )


class BrokenAfterTextStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield (
            b'data: {"event":"workflow_started","task_id":"task_broken",'
            b'"workflow_run_id":"run_broken","data":{}}\n\n'
            b'data: {"event":"text_chunk","task_id":"task_broken",'
            b'"workflow_run_id":"run_broken","data":{"text":"\xe5\x8d\x8a\xe5\x8f\xa5"}}\n\n'
        )
        raise httpx.ReadError("connection dropped")


@pytest.mark.asyncio
async def test_stream_yields_before_response_finishes(settings) -> None:
    release = asyncio.Event()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=GatedSSEStream(release),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DifyWorkflowClient(settings, http)
        events = client.stream(
            {"inputs": {"user_text": "你好"}, "response_mode": "streaming", "user": "usr_1"}
        )

        first = await asyncio.wait_for(anext(events), timeout=0.2)
        assert first.event == "workflow_started"
        assert not release.is_set()

        release.set()
        assert (await anext(events)).event == "text_chunk"
        await events.aclose()

    request = captured[0]
    assert request.url.path == "/v1/workflows/run"
    assert request.headers["authorization"].startswith("Bearer ")
    assert json.loads(request.content)["response_mode"] == "streaming"


@pytest.mark.asyncio
async def test_stream_rejects_non_sse_response(settings) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = DifyWorkflowClient(settings, http)
        with pytest.raises(Exception, match="事件流"):
            await anext(client.stream({"inputs": {}, "response_mode": "streaming", "user": "usr_1"}))


@pytest.mark.asyncio
async def test_stream_retries_rate_limit_once_before_text(settings) -> None:
    attempts = 0
    body = (
        b'data: {"event":"workflow_started","task_id":"task_2",'
        b'"workflow_run_id":"run_2","data":{}}\n\n'
        b'data: {"event":"text_chunk","task_id":"task_2",'
        b'"workflow_run_id":"run_2","data":{"text":"\xe6\x88\x90\xe5\x8a\x9f"}}\n\n'
        b'data: {"event":"workflow_finished","task_id":"task_2",'
        b'"workflow_run_id":"run_2","data":{"status":"succeeded"}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"code": "rate_limit"})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DifyWorkflowClient(settings, http)
        events = [
            item
            async for item in client.stream(
                {"inputs": {}, "response_mode": "streaming", "user": "usr_1"}
            )
        ]

    assert attempts == 2
    assert [item.event for item in events] == [
        "workflow_started",
        "text_chunk",
        "workflow_finished",
    ]


@pytest.mark.asyncio
async def test_stream_never_retries_after_text(settings) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=BrokenAfterTextStream(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DifyWorkflowClient(settings, http)
        events = client.stream(
            {"inputs": {}, "response_mode": "streaming", "user": "usr_1"}
        )
        assert (await anext(events)).event == "workflow_started"
        assert (await anext(events)).event == "text_chunk"
        with pytest.raises(Exception, match="连接中断"):
            await anext(events)

    assert attempts == 1
