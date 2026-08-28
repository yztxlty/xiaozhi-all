from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DifyWorkflowSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIFY_WORKFLOW_",
        extra="ignore",
    )

    base_url: HttpUrl
    api_key: SecretStr
    provider_id: str = "dify-workflow-primary"
    connect_timeout_ms: int = Field(default=1000, ge=100, le=5000)
    read_timeout_ms: int = Field(default=15000, ge=1000, le=60000)
    total_timeout_ms: int = Field(default=20000, ge=1000, le=60000)
    stop_timeout_ms: int = Field(default=500, ge=100, le=2000)
    max_retries_before_delta: int = Field(default=1, ge=0, le=1)
