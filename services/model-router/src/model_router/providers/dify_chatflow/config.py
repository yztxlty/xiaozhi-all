from __future__ import annotations

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DifyChatflowSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIFY_CHATFLOW_",
        extra="ignore",
    )

    base_url: HttpUrl
    api_key: SecretStr
    provider_id: str = "dify-chatflow-primary"
    allow_insecure_http: bool = False
    connect_timeout_ms: int = Field(default=1000, ge=100, le=5000)
    read_timeout_ms: int = Field(default=15000, ge=1000, le=60000)
    total_timeout_ms: int = Field(default=20000, ge=1000, le=60000)
    stop_timeout_ms: int = Field(default=500, ge=100, le=2000)
    max_retries_before_delta: int = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def require_https_unless_explicitly_allowed(self) -> DifyChatflowSettings:
        if self.base_url.scheme != "https" and not self.allow_insecure_http:
            raise ValueError("Dify 地址必须使用 HTTPS；开发联调需显式允许 HTTP")
        return self
