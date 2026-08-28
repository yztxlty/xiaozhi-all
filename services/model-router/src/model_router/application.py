import httpx

from .providers.dify_chatflow.client import DifyChatflowClient
from .providers.dify_chatflow.config import DifyChatflowSettings
from .providers.dify_chatflow.provider import DifyChatflowProvider
from .router import LLMRouter


def create_dify_chatflow_router(
    settings: DifyChatflowSettings,
    http: httpx.AsyncClient,
) -> LLMRouter:
    return LLMRouter([DifyChatflowProvider(settings, DifyChatflowClient(settings, http))])
