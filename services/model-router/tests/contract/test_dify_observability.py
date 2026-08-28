import asyncio

import pytest
from prometheus_client import REGISTRY

from model_router.providers.dify_workflow.event_parser import DifyEvent
from model_router.providers.dify_workflow.metrics import safe_trace_attributes
from model_router.providers.dify_workflow.provider import DifyWorkflowProvider


def test_trace_attributes_exclude_secret_prompt_and_memory() -> None:
    attrs = safe_trace_attributes(
        provider="dify-workflow-primary",
        session_id="s_1",
        turn_id="t_1",
        generation_id="g_1",
        task_id="task_1",
    )

    rendered = repr(attrs).lower()
    assert attrs["llm.provider"] == "dify-workflow-primary"
    assert attrs["dify.task_id"] == "task_1"
    assert "authorization" not in rendered
    assert "api_key" not in rendered
    assert "user_text" not in rendered
    assert "long_memories" not in rendered


@pytest.mark.asyncio
async def test_provider_records_completed_request_without_content_labels(
    settings,
    llm_request,
) -> None:
    class SuccessfulClient:
        async def stream(self, payload):
            yield DifyEvent("workflow_started", "task_1", "run_1", {})
            yield DifyEvent("text_chunk", "task_1", "run_1", {"text": "私密正文"})
            yield DifyEvent(
                "workflow_finished",
                "task_1",
                "run_1",
                {"status": "succeeded"},
            )

        async def stop(self, task_id, user):
            return None

    labels = {"provider": "dify-workflow-primary", "result": "completed"}
    before = REGISTRY.get_sample_value("xiaozhi_dify_requests_total", labels) or 0
    provider = DifyWorkflowProvider(settings, SuccessfulClient())

    _ = [item async for item in provider.stream(llm_request, asyncio.Event())]

    after = REGISTRY.get_sample_value("xiaozhi_dify_requests_total", labels) or 0
    assert after == before + 1
    metric_text = "\n".join(
        family.name + repr(family.samples) for family in REGISTRY.collect()
        if family.name.startswith("xiaozhi_dify")
    )
    assert "私密正文" not in metric_text
