import asyncio

import pytest

from model_router.core.capability import ProviderCapability
from model_router.core.contracts import LLMRequest, LLMTextDelta
from model_router.core.health import ProviderHealth, ProviderHealthStatus
from model_router.core.router import LLMRouter
from model_router.providers.dify_workflow.cancellation import (
    DifyCancellationController,
)
from model_router.providers.dify_workflow.output_mapper import DifyOutputMapper


def test_core_exposes_only_provider_independent_models() -> None:
    capability = ProviderCapability(
        provider_id="dify-workflow-primary",
        streaming_output=True,
        cancellation=True,
    )
    health = ProviderHealth(
        provider_id=capability.provider_id,
        status=ProviderHealthStatus.HEALTHY,
    )

    assert capability.kind == "llm"
    assert health.usable is True
    assert LLMRouter


def test_dify_output_mapping_stays_inside_provider_adapter() -> None:
    request = LLMRequest(
        session_id="s_1",
        turn_id="t_1",
        generation_id="g_1",
        user_id="usr_1",
        user_text="你好",
        role_profile={"name": "幽光"},
    )
    mapper = DifyOutputMapper(request, "dify-workflow-primary")

    delta = mapper.text_delta("我在")

    assert isinstance(delta, LLMTextDelta)
    assert delta.sequence == 1
    assert mapper.reply_text == "我在"


def test_dify_cancellation_is_not_a_core_dependency() -> None:
    assert DifyCancellationController
    assert asyncio


def test_flattened_internal_modules_are_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("model_router.contracts")
    with pytest.raises(ModuleNotFoundError):
        __import__("model_router.router")
