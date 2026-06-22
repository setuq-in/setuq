from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SPLUNK_HOST: str = "localhost"
    SPLUNK_PORT: int = 8089
    SPLUNK_USERNAME: str = "admin"
    SPLUNK_PASSWORD: str = ""
    SPLUNK_VERIFY_SSL: bool = True
    SPLUNK_JOB_TIMEOUT_SECONDS: int = 120

    LLM_PROVIDER: str = "openai"
    LLM_MODEL: str = "gpt-4o"
    LLM_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_TIMEOUT_SECONDS: float = 300.0
    OLLAMA_JSON_MODE: bool = True

    SESSION_MAX_TURNS: int = 10
    GUARDRAIL_MAX_TIME_RANGE_DAYS: int = 365

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    API_KEY: str = ""  # Empty = auth disabled (dev mode)
    UI_ORIGINS: str = "*"  # Comma-separated allowed origins

    OBSERVABILITY_ENABLED: bool = False
    LANGFUSE_HOST: str = "http://localhost:3000"
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    PII_REDACT: bool = True
    MAX_TOKENS_PER_RUN: int = 50_000
    MAX_COST_USD_PER_RUN: float = 0.50

    AUDIT_LOG_PATH: str = "audit.log"

    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_IP: str = "60/minute"
    RATE_LIMIT_PER_SESSION: str = "10/minute"

    IDEMPOTENCY_CACHE_ENABLED: bool = True
    IDEMPOTENCY_TTL_SECONDS: int = 300

    FALLBACK_ENABLED: bool = False
    FALLBACK_PROVIDERS: str = ""  # comma-sep: "anthropic,ollama"

    REDIS_URL: str = ""  # e.g. redis://localhost:6379/0

    SCHEMA_DISCOVERY_ENABLED: bool = False
    SCHEMA_CACHE_PATH: str = "engine/data/schema_cache.db"
    SCHEMA_REFRESH_HOURS: float = 24.0
    SCHEMA_MAX_INDEXES: int = 20

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
