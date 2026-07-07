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
    # Embedding model for semantic search over articles; served by the same
    # endpoint as openai_model. Unset -> search falls back to keyword matching.
    openai_embedding_model: str = Field(
        default="",
        validation_alias=AliasChoices(
            "NEWSREAD_OPENAI_EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL"
        ),
    )

    # Web tools for the Q&A agent. Without either, the agent still works,
    # just without web search/extract. SearXNG (self-hosted metasearch) wins
    # when both are configured — it's the local-deployment option.
    tavily_api_key: str = Field(
        default="", validation_alias=AliasChoices("NEWSREAD_TAVILY_API_KEY", "TAVILY_API_KEY")
    )
    searxng_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("NEWSREAD_SEARXNG_BASE_URL", "SEARXNG_BASE_URL"),
    )

    # Messaging integrations (share to Slack / Microsoft Teams as the user).
    # Unset -> the platform shows as "not configured" in settings.
    slack_client_id: str = ""  # NEWSREAD_SLACK_CLIENT_ID
    slack_client_secret: str = ""  # NEWSREAD_SLACK_CLIENT_SECRET
    # Verifies inbound requests from Slack (Events API / interactivity).
    # Unused until an inbound feature ships (e.g. syncing channel replies).
    slack_signing_secret: str = ""  # NEWSREAD_SLACK_SIGNING_SECRET
    teams_client_id: str = ""  # NEWSREAD_TEAMS_CLIENT_ID
    teams_client_secret: str = ""  # NEWSREAD_TEAMS_CLIENT_SECRET
    # Entra authority: "organizations" (any work/school tenant) or a tenant id.
    teams_tenant: str = "organizations"  # NEWSREAD_TEAMS_TENANT
    # Public base URL the OAuth providers redirect back to. Slack requires
    # HTTPS, so in dev this is a tunnel (ngrok/cloudflared) to the backend.
    oauth_redirect_base: str = "http://localhost:8000"  # NEWSREAD_OAUTH_REDIRECT_BASE
    # Where to send the browser after the OAuth callback completes.
    frontend_base_url: str = "http://localhost:3000"  # NEWSREAD_FRONTEND_BASE_URL
    # Fernet key encrypting per-user platform tokens at rest. Generate with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""  # NEWSREAD_TOKEN_ENCRYPTION_KEY


settings = Settings()
