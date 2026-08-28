from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    provider_id: str
    streaming: bool
    cancellation: bool

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("Provider 标识不能为空")


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider_id: str
    healthy: bool
    detail: str = ""
