from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenAICompatibleSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_COMPATIBLE_", extra="ignore")

    base_url: HttpUrl = HttpUrl("https://api.deepseek.com")
    api_key: SecretStr
    model: str = "deepseek-v4-flash"
    provider_id: str = "deepseek-openai-compatible"
    thinking: Literal["enabled", "disabled"] = "disabled"
    max_tokens: int = Field(default=256, ge=32, le=4096)
    connect_timeout_ms: int = Field(default=3000, ge=100, le=10000)
    read_timeout_ms: int = Field(default=30000, ge=1000, le=120000)
