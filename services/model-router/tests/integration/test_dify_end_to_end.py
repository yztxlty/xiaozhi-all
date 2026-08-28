import asyncio
import json

import httpx
import pytest

from model_router.application import create_dify_router
from model_router.contracts import LLMCompleted, LLMTextDelta


@pytest.mark.asyncio
async def test_application_streams_stateless_dify_workflow(
    settings,
    llm_request,
) -> None:
    captured: list[httpx.Request] = []
    body = (
        b'data: {"event":"workflow_started","task_id":"task_1",'
        b'"workflow_run_id":"run_1","data":{}}\n\n'
        b'data: {"event":"text_chunk","task_id":"task_1",'
        b'"workflow_run_id":"run_1","data":{"text":"\xe6\x88\x91\xe5\x9c\xa8"}}\n\n'
        b'data: {"event":"workflow_finished","task_id":"task_1",'
        b'"workflow_run_id":"run_1","data":{"status":"succeeded",'
        b'"outputs":{"emotion":"warm"},"total_tokens":3}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        router = create_dify_router(settings, http)
        events = [item async for item in router.stream(llm_request, asyncio.Event())]

    deltas = [item for item in events if isinstance(item, LLMTextDelta)]
    assert [item.text for item in deltas] == ["我在"]
    assert isinstance(events[-1], LLMCompleted)
    assert events[-1].reply_text == "我在"

    request_body = json.loads(captured[0].content)
    assert request_body["inputs"]["role_profile_json"]
    assert request_body["inputs"]["short_history_json"]
    assert request_body["inputs"]["long_memories_json"]
    assert "conversation_id" not in request_body
