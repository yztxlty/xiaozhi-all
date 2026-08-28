import httpx

from .providers.dify_workflow.client import DifyWorkflowClient
from .providers.dify_workflow.config import DifyWorkflowSettings
from .providers.dify_workflow.provider import DifyWorkflowProvider
from .router import LLMRouter


def create_dify_router(
    settings: DifyWorkflowSettings,
    http: httpx.AsyncClient,
) -> LLMRouter:
    client = DifyWorkflowClient(settings, http)
    provider = DifyWorkflowProvider(settings, client)
    return LLMRouter([provider])
