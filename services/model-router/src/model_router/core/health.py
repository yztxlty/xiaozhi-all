from enum import StrEnum

from pydantic import BaseModel


class ProviderHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ProviderHealth(BaseModel):
    provider_id: str
    status: ProviderHealthStatus
    detail: str | None = None

    @property
    def usable(self) -> bool:
        return self.status is not ProviderHealthStatus.UNHEALTHY
