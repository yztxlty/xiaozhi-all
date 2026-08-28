from typing import Literal

from pydantic import BaseModel


class ProviderCapability(BaseModel):
    provider_id: str
    kind: Literal["llm"] = "llm"
    streaming_output: bool
    cancel_supported: bool
    conversation_state: Literal["stateless", "stateful"]
