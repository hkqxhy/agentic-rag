from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_prefix="AGENTIC_RAG_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Agentic RAG"
    environment: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://agentic_rag:agentic_rag@localhost:5432/agentic_rag"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    run_queue_key: str = "agentic_rag:runs:queue"
    run_stream_prefix: str = "agentic_rag:runs:events"
    run_event_ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    session_cookie_name: str = "agentic_rag_session"
    session_ttl_seconds: int = Field(default=604_800, ge=3_600, le=2_592_000)
    session_cookie_secure: bool = False
    audit_hash_key: str = "development-only-change-me"
    trust_proxy_headers: bool = False
    question_rate_limit: int = Field(default=10, ge=1, le=300)
    question_rate_window_seconds: int = Field(default=60, ge=1, le=3_600)
    ip_question_rate_limit: int = Field(default=120, ge=1, le=1_000)
    auth_rate_limit: int = Field(default=60, ge=1, le=500)
    auth_identity_rate_limit: int = Field(default=10, ge=1, le=100)
    auth_rate_window_seconds: int = Field(default=300, ge=10, le=3_600)
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4318/v1/traces"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def validate_production_security(self) -> Settings:
        if self.environment == "production":
            if not self.session_cookie_secure:
                raise ValueError("secure session cookies are required in production")
            if self.audit_hash_key == "development-only-change-me":
                raise ValueError("AGENTIC_RAG_AUDIT_HASH_KEY must be changed in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
