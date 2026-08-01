from enum import StrEnum

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeploymentMode(StrEnum):
    self_hosted = "self_hosted"
    staging = "staging"
    prod = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWSREAD_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Where this instance runs. The mode is consulted ONLY inside this file:
    # it picks defaults for the feature flags below and arms the prod boot
    # checks. Everything else in the app reads the individual flags.
    deployment: DeploymentMode = DeploymentMode.self_hosted

    # Deployment-derived feature flags. None = derive from deployment mode; an
    # explicit NEWSREAD_* env var always wins. After validation both are bools.
    # self_hosted is single-user: registration closes once the owner exists.
    allow_signup: bool | None = None  # NEWSREAD_ALLOW_SIGNUP
    # Slack/Teams sharing. Off for self_hosted (a single-user instance showing
    # workspace-integration UI is noise); still requires credentials when on.
    messaging_enabled: bool | None = None  # NEWSREAD_MESSAGING_ENABLED
    # Browser history follows the deployment mode: on for prod/staging (the
    # operator has read the privacy doc and runs the instance deliberately),
    # off for self_hosted until explicitly enabled. Explicit env var wins.
    browser_history_enabled: bool | None = None  # NEWSREAD_BROWSER_HISTORY_ENABLED
    # Whether the first account registered on an empty instance becomes the
    # owner. On for self_hosted (the person installing is the operator); off
    # for hosted modes, where public signup must never mint an owner — hosted
    # and existing installations promote via `scripts/set_role.py` instead.
    first_account_owner: bool | None = None  # NEWSREAD_FIRST_ACCOUNT_OWNER
    # Bring-your-own LLM keys. On for self_hosted (run AI on whatever you
    # like); off for hosted modes, where everyone runs on the operator's key
    # so usage stays meterable (llm_usage) and margins stay modelable.
    # Turning it off gates *saving* keys — existing stored keys keep working
    # until removed, so flipping the flag never silently breaks users.
    byo_llm_keys_enabled: bool | None = None  # NEWSREAD_BYO_LLM_KEYS_ENABLED
    # Packaged Chrome extension served from Settings → Browser history. Empty
    # means the in-repo default (extension/newsread-history-extension.zip,
    # produced by `npm run build` there); the download link hides when the
    # file is absent. Docker mounts ./extension and points this inside it.
    extension_package: str = ""  # NEWSREAD_EXTENSION_PACKAGE
    # V2 history content storage remains a separate rollout capability while
    # Phase 1 lays its schema and storage foundation. Enabling it requires a
    # complete object-store and encryption configuration below.
    browser_history_content_enabled: bool = False
    # Phase 6 cutover. When enabled the server advertises capability revision
    # 3 and pre-v2 inline bodies are accepted as metadata only.
    browser_history_finalize_enabled: bool = False
    # Temporary compatibility window for zip-distributed pre-v2 extensions.
    # Disable after the announced window to accept their visits as metadata only.
    browser_history_legacy_inline_enabled: bool = True

    # Private S3-compatible storage for encrypted browser-history objects.
    # Keeping this contract provider-neutral lets local Compose use SeaweedFS while
    # hosted deployments use any maintained S3-compatible service.
    object_store_endpoint: str = ""
    object_store_access_key: str = ""
    object_store_secret_key: str = ""
    object_store_bucket: str = "newsread-history"
    # Public media (AI-generated article illustrations) lives in its own
    # bucket on the same endpoint: unencrypted, not user-scoped, and outside
    # the history GC sweep. Created on demand at startup.
    object_store_media_bucket: str = "newsread-media"
    object_store_region: str = "us-east-1"
    object_store_secure: bool = False
    history_object_max_bytes: int = 1024 * 1024
    history_object_compressed_max_bytes: int = 512 * 1024
    history_image_max_bytes: int = 200 * 1024
    history_user_storage_max_bytes: int = 512 * 1024 * 1024
    history_embedding_daily_limit: int = 1_000
    history_object_gc_grace_hours: int = 24
    history_object_gc_scan_limit: int = 10_000
    history_object_delete_batch: int = 200
    history_embedding_backlog_alert_hours: int = 6
    history_deletion_backlog_alert_hours: int = 1
    history_storage_alert_ratio: float = 0.9

    # AES-256 key-encryption key wrapping per-user history data keys. The
    # current version is used for new/rewrapped rows; previous versions are a
    # JSON object {"version": "base64-key"} kept only during rotation.
    history_encryption_master_key: str = ""
    history_encryption_wrapping_key_version: int = 1
    history_encryption_previous_master_keys: str = ""

    database_url: str = "postgresql+asyncpg://newsread:newsread@localhost:5433/newsread"
    redis_url: str = "redis://localhost:6380/0"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expires_days: int = 30
    # bcrypt work factor for password hashing. Tests lower it (hashing at cost
    # 12 dominates suite runtime); production must keep >= 12.
    bcrypt_rounds: int = 12
    cors_origins: str = "http://localhost:3000"
    feed_refresh_minutes: int = 15
    # SSRF guard: reject feed URLs that resolve to private/loopback networks.
    # Self-hosted deployments subscribing to feeds on their own LAN can set
    # NEWSREAD_BLOCK_PRIVATE_FEED_URLS=false.
    block_private_feed_urls: bool = True

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
    # Whether openai_model accepts image input. When set, image-only pages
    # (comics, infographics) that yield no prose are summarized from a
    # rendered screenshot instead of failing with "couldn't fetch full text".
    openai_model_vision: bool = Field(
        default=False,
        validation_alias=AliasChoices("NEWSREAD_OPENAI_MODEL_VISION", "OPENAI_MODEL_VISION"),
    )
    # Embedding model for semantic search over articles; served by the same
    # endpoint as openai_model. Unset -> search falls back to keyword matching.
    openai_embedding_model: str = Field(
        default="",
        validation_alias=AliasChoices("NEWSREAD_OPENAI_EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL"),
    )

    # Server-wide default for generating images for articles that have none
    # (users with their own image model override it). Any OpenAI-compatible
    # endpoint; OpenRouter image models are served via chat completions with
    # the modalities extension, which image_gen.py handles by base URL.
    # The GENERTAION alias tolerates the typo this key shipped with in .env.
    image_generation_base_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "NEWSREAD_IMAGE_GENERATION_BASE_URL", "IMAGE_GENERATION_BASE_URL"
        ),
    )
    image_generation_model: str = Field(
        default="",
        validation_alias=AliasChoices(
            "NEWSREAD_IMAGE_GENERATION_MODEL",
            "IMAGE_GENERATION_MODEL",
            "IMAGE_GENERTAION_MODEL",
        ),
    )
    image_generation_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "NEWSREAD_IMAGE_GENERATION_API_KEY", "IMAGE_GENERATION_API_KEY"
        ),
    )
    # JSON object merged verbatim into every generation request — model-specific
    # knobs like {"aspect_ratio": "16:9"}. Invalid JSON is ignored with a warning.
    image_generation_extra_params: str = Field(
        default="",
        validation_alias=AliasChoices(
            "NEWSREAD_IMAGE_GENERATION_EXTRA_PARAMS", "IMAGE_GENERATION_EXTRA_PARAMS"
        ),
    )

    # Server-wide model for translating AI summaries into the reader's
    # language. Deliberately its own endpoint: translation is a cheap,
    # high-volume, globally cached job that suits a free model, while
    # summarization wants the good one. Unset model -> translation runs on the
    # main openai_* model, so a self-hoster gets the feature with no extra
    # config. The base URL defaults to OpenRouter because that is where the
    # free models the feature is designed around live.
    translation_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("NEWSREAD_TRANSLATION_BASE_URL", "TRANSLATION_BASE_URL"),
    )
    translation_model: str = Field(
        default="",
        validation_alias=AliasChoices("NEWSREAD_TRANSLATION_MODEL", "TRANSLATION_MODEL"),
    )
    # Falls back to openai_api_key when the endpoint is the same one.
    translation_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("NEWSREAD_TRANSLATION_API_KEY", "TRANSLATION_API_KEY"),
    )

    # YouTube channel feeds. The key is optional: handles and channel URLs
    # resolve by reading the channel page's <link rel="alternate"> feed tag.
    # With a key, handles resolve through channels.list and a plain display
    # name can be searched (search.list), which the page scrape cannot do.
    youtube_api_key: str = Field(
        default="", validation_alias=AliasChoices("NEWSREAD_YOUTUBE_API_KEY", "YOUTUBE_API_KEY")
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

    @model_validator(mode="after")
    def _resolve_deployment(self) -> "Settings":
        """Fill mode-derived flag defaults and refuse insecure prod boots."""
        is_self_hosted = self.deployment is DeploymentMode.self_hosted
        if self.allow_signup is None:
            self.allow_signup = not is_self_hosted
        if self.messaging_enabled is None:
            self.messaging_enabled = not is_self_hosted
        if self.browser_history_enabled is None:
            self.browser_history_enabled = not is_self_hosted
        if self.first_account_owner is None:
            self.first_account_owner = is_self_hosted
        if self.byo_llm_keys_enabled is None:
            self.byo_llm_keys_enabled = is_self_hosted
        if self.browser_history_content_enabled:
            required = {
                "NEWSREAD_OBJECT_STORE_ENDPOINT": self.object_store_endpoint,
                "NEWSREAD_OBJECT_STORE_ACCESS_KEY": self.object_store_access_key,
                "NEWSREAD_OBJECT_STORE_SECRET_KEY": self.object_store_secret_key,
                "NEWSREAD_OBJECT_STORE_BUCKET": self.object_store_bucket,
                "NEWSREAD_HISTORY_ENCRYPTION_MASTER_KEY": self.history_encryption_master_key,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "NEWSREAD_BROWSER_HISTORY_CONTENT_ENABLED requires " + ", ".join(missing)
                )
            from .history_crypto import HistoryCryptoError, MasterKeyring

            try:
                MasterKeyring.from_config(
                    current_key=self.history_encryption_master_key,
                    current_version=self.history_encryption_wrapping_key_version,
                    previous_keys_json=self.history_encryption_previous_master_keys,
                )
            except HistoryCryptoError as exc:
                raise ValueError(f"invalid history encryption configuration: {exc}") from exc
        if self.browser_history_finalize_enabled and not self.browser_history_content_enabled:
            raise ValueError(
                "NEWSREAD_BROWSER_HISTORY_FINALIZE_ENABLED requires "
                "NEWSREAD_BROWSER_HISTORY_CONTENT_ENABLED"
            )
        if (
            self.history_object_max_bytes < 1
            or self.history_object_compressed_max_bytes < 1
            or self.history_image_max_bytes < 1
            or self.history_user_storage_max_bytes < 1
        ):
            raise ValueError("history object, image, and storage byte limits must be positive")
        if (
            self.history_embedding_daily_limit < 1
            or self.history_object_gc_grace_hours < 0
            or self.history_object_gc_scan_limit < 1
            or self.history_object_delete_batch < 1
            or self.history_embedding_backlog_alert_hours < 1
            or self.history_deletion_backlog_alert_hours < 1
            or not 0 < self.history_storage_alert_ratio <= 1
        ):
            raise ValueError("history quota and operations limits are invalid")
        if not is_self_hosted and self.jwt_secret == "dev-secret-change-me":
            raise ValueError(
                f"NEWSREAD_DEPLOYMENT={self.deployment.value} requires a real "
                "NEWSREAD_JWT_SECRET (the dev default signs forgeable tokens)"
            )
        return self


settings = Settings()
