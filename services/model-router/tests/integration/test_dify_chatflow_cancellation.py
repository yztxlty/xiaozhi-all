import json

import httpx
import pytest

from model_router.providers.dify_chatflow.client import DifyChatflowClient


@pytest.mark.asyncio
async def test_stop_calls_official_chat_message_endpoint(chatflow_settings) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"result": "success"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DifyChatflowClient(chatflow_settings, http)
        await client.stop("task_1", "usr_1")

    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/chat-messages/task_1/stop"
    assert json.loads(request.content) == {"user": "usr_1"}
    assert "test-key-not-a-real-secret" not in repr(request)
