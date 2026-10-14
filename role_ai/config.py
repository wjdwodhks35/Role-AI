from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ROLE_AI_", env_file=".env", extra="ignore"
    )
    mode: Literal["local", "public"] = "local"
    database_url: str = "sqlite:///./data/role-ai.db"
    allowed_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]
    cookie_secure: bool = False
    session_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    registration_enabled: bool = False
    bootstrap_email: str = ""
    bootstrap_password: SecretStr = SecretStr("")
    default_provider: Literal["ollama", "vllm", "openai", "mock"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    provider_timeout_seconds: float = Field(default=60, gt=0, le=300)
    demo_enabled: bool = False
    vllm_url: str = "http://127.0.0.1:8001/v1"
    vllm_model: str = ""
    openai_enabled: bool = False
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9._:-]*$")
    openai_input_usd_per_million: Decimal = Field(default=Decimal("0"), ge=0, le=1000000, allow_inf_nan=False)
    openai_output_usd_per_million: Decimal = Field(default=Decimal("0"), ge=0, le=1000000, allow_inf_nan=False)
    openai_daily_budget_usd: Decimal = Field(default=Decimal("0"), ge=0, le=1000, allow_inf_nan=False)
    openai_request_budget_usd: Decimal = Field(default=Decimal("0"), ge=0, le=100, allow_inf_nan=False)
    openai_max_output_tokens: int = Field(default=512, ge=32, le=4096)
    openclaw_enabled: bool = False
    openclaw_config_path: str = ""
    openclaw_url: str = "http://127.0.0.1:18789"
    openclaw_token: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_safety(self):
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("Phase 1 supports SQLite; add a migration before changing databases")
        if self.mode == "public" and (not self.cookie_secure or self.demo_enabled):
            raise ValueError("Public mode requires secure cookies and forbids mock mode")
        if self.default_provider == "mock" and not self.demo_enabled:
            raise ValueError("Mock provider requires explicit demo_enabled")
        if bool(self.bootstrap_email) != bool(self.bootstrap_password.get_secret_value()):
            raise ValueError("Set both bootstrap email and password, or neither")
        if self.bootstrap_email and (
            "@" not in self.bootstrap_email
            or len(self.bootstrap_password.get_secret_value()) < 12
        ):
            raise ValueError("Bootstrap credentials require an email and 12-character password")
        return self
