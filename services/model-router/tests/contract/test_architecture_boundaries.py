import inspect

from model_router.core.capability import ProviderCapability
from model_router.core.health import ProviderHealth, ProviderHealthStatus
from model_router.core.router import LLMRouter


def test_core_exposes_only_provider_independent_models() -> None:
    capability = ProviderCapability(
        provider_id="dify-chatflow-primary",
        streaming_output=True,
        cancel_supported=True,
        conversation_state="stateless",
    )
    health = ProviderHealth(
        provider_id=capability.provider_id,
        status=ProviderHealthStatus.HEALTHY,
    )

    assert capability.kind == "llm"
    assert health.usable is True
    assert LLMRouter


def test_core_has_no_provider_dependency() -> None:
    assert "model_router.providers" not in inspect.getsource(LLMRouter)
