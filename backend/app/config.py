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

    # Optional API tokens for link enrichers (raise rate limits, never required).
    github_token: str = ""  # NEWSREAD_GITHUB_TOKEN: 60/hr -> 5000/hr
    hf_token: str = ""  # NEWSREAD_HF_TOKEN: 500 -> 1000 req/5min

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

    # Tavily web search/extract for the Q&A agent. Without it the agent still
    # works, just without web tools.
    tavily_api_key: str = Field(
        default="", validation_alias=AliasChoices("NEWSREAD_TAVILY_API_KEY", "TAVILY_API_KEY")
    )


settings = Settings()
