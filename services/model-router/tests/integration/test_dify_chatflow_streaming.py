import asyncio
import json

import httpx
import pytest

from model_router.providers.dify_chatflow.client import DifyChatflowClient


class GatedSSEStream(httpx.AsyncByteStream):
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release

    async def __aiter__(self):
        yield (
            b'data: {"event":"workflow_started","task_id":"task_1",'
            b'"workflow_run_id":"run_1","data":{}}\n\n'
        )
        await self.release.wait()
        yield (
            b'data: {"event":"message","task_id":"task_1",'
            b'"message_id":"message_1","answer":"\xe4\xbd\xa0"}\n\n'
            b'data: {"event":"message_end","task_id":"task_1",'
            b'"message_id":"message_1","metadata":{}}\n\n'
        )


class BrokenAfterMessageStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield (
            b'data: {"event":"message","task_id":"task_broken",'
            b'"message_id":"message_broken","answer":"\xe5\x8d\x8a\xe5\x8f\xa5"}\n\n'
        )
        raise httpx.ReadError("connection dropped")


@pytest.mark.asyncio
async def test_stream_yields_before_response_finishes(chatflow_settings) -> None:
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
        client = DifyChatflowClient(chatflow_settings, http)
        events = client.stream(
            {"query": "你好", "inputs": {}, "response_mode": "streaming", "user": "usr_1"}
        )
        first = await asyncio.wait_for(anext(events), timeout=0.2)
        assert first.event == "workflow_started"
        assert not release.is_set()

        release.set()
        assert (await anext(events)).answer == "你"
        await events.aclose()

    request = captured[0]
    assert request.url.path == "/v1/chat-messages"
    assert request.headers["authorization"].startswith("Bearer ")
    assert json.loads(request.content)["response_mode"] == "streaming"


@pytest.mark.asyncio
async def test_stream_rejects_non_sse_response(chatflow_settings) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = DifyChatflowClient(chatflow_settings, http)
        with pytest.raises(Exception, match="事件流"):
            await anext(
                client.stream(
                    {"query": "你好", "inputs": {}, "response_mode": "streaming", "user": "usr_1"}
                )
            )


@pytest.mark.asyncio
async def test_stream_retries_once_before_message_delta(chatflow_settings) -> None:
    attempts = 0
    body = (
        b'data: {"event":"message","task_id":"task_2",'
        b'"message_id":"message_2","answer":"\xe6\x88\x90\xe5\x8a\x9f"}\n\n'
        b'data: {"event":"message_end","task_id":"task_2",'
        b'"message_id":"message_2","metadata":{}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"code": "rate_limit"})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DifyChatflowClient(chatflow_settings, http)
        events = [
            event
            async for event in client.stream(
                {"query": "你好", "inputs": {}, "response_mode": "streaming", "user": "usr_1"}
            )
        ]

    assert attempts == 2
    assert [event.event for event in events] == ["message", "message_end"]


@pytest.mark.asyncio
async def test_stream_never_retries_after_message_delta(chatflow_settings) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=BrokenAfterMessageStream(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DifyChatflowClient(chatflow_settings, http)
        events = client.stream(
            {"query": "你好", "inputs": {}, "response_mode": "streaming", "user": "usr_1"}
        )
        assert (await anext(events)).answer == "半句"
        with pytest.raises(Exception, match="连接中断"):
            await anext(events)

    assert attempts == 1
