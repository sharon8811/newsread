from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWSREAD_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://newsread:newsread@localhost:5433/newsread"
    redis_url: str = "redis://localhost:6380/0"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expires_days: int = 30
    cors_origins: str = "http://localhost:3000"
    feed_refresh_minutes: int = 15

    # Any OpenAI-compatible endpoint (OpenAI, vLLM, LiteLLM, Ollama).
    # Read from NEWSREAD_OPENAI_* or the standard OPENAI_* names.
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("NEWSREAD_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    openai_base_url: str = Field(
        default="", validation_alias=AliasChoices("NEWSREAD_OPENAI_BASE_URL", "OPENAI_BASE_URL")
    )
    openai_model: str = Field(
        default="", validation_alias=AliasChoices("NEWSREAD_OPENAI_MODEL", "OPENAI_MODEL")
    )


settings = Settings()
