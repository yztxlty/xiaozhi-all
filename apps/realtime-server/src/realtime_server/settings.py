from __future__ import annotations

import os
from dataclasses import dataclass

from model_router.providers.dify_chatflow.config import DifyChatflowSettings


@dataclass(frozen=True, slots=True)
class RealtimeSettings:
    dify: DifyChatflowSettings

    @classmethod
    def from_values(
        cls,
        *,
        dify_base_url: str,
        dify_api_key: str,
        allow_insecure_http: bool = False,
    ) -> RealtimeSettings:
        return cls(
            DifyChatflowSettings(
                base_url=dify_base_url,
                api_key=dify_api_key,
                allow_insecure_http=allow_insecure_http,
            )
        )

    @classmethod
    def from_env(cls) -> RealtimeSettings:
        return cls.from_values(
            dify_base_url=os.environ["DIFY_CHATFLOW_BASE_URL"],
            dify_api_key=os.environ["DIFY_CHATFLOW_API_KEY"],
            allow_insecure_http=os.getenv("DIFY_CHATFLOW_ALLOW_INSECURE_HTTP") == "1",
        )
