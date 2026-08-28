import json

import httpx
import pytest

from model_router.providers.dify_workflow.client import DifyWorkflowClient


@pytest.mark.asyncio
async def test_stop_calls_official_workflow_task_endpoint(settings) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"result": "success"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DifyWorkflowClient(settings, http)
        await client.stop("task_1", "usr_1")

    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/workflows/tasks/task_1/stop"
    assert json.loads(request.content) == {"user": "usr_1"}
    assert "test-key-not-a-real-secret" not in repr(request)
