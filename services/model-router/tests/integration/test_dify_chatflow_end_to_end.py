import asyncio
import json

import httpx

from model_router.application import create_dify_chatflow_router
from model_router.contracts import LLMCompleted, LLMTextDelta


async def test_application_streams_stateless_chatflow(chatflow_settings, llm_request):
    captured: list[httpx.Request] = []
    body = (
        b'data: {"event":"message","task_id":"task_1",'
        b'"message_id":"message_1","answer":"\xe6\x88\x91\xe5\x9c\xa8"}\n\n'
        b'data: {"event":"message_end","task_id":"task_1",'
        b'"message_id":"message_1","metadata":{"usage":{"total_tokens":3}}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        router = create_dify_chatflow_router(chatflow_settings, http)
        events = [event async for event in router.stream(llm_request, asyncio.Event())]

    assert [event.text for event in events if isinstance(event, LLMTextDelta)] == ["我在"]
    assert isinstance(events[-1], LLMCompleted)
    request = json.loads(captured[0].content)
    assert captured[0].url.path == "/v1/chat-messages"
    assert request["conversation_id"] == ""
    assert request["inputs"]["memory"]
