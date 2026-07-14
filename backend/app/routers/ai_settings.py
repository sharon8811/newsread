"""Per-user LLM configuration — "bring your own key".

A user either rides the server-wide default (config.py) or saves their own
provider/key/model here; interactive AI calls then run on their key and get
logged to llm_usage. Keys are write-only: stored Fernet-encrypted (crypto.py)
and surfaced back only as key_hint.
"""

import logging

from fastapi import APIRouter, HTTPException
from openai import AsyncOpenAI

from .. import crypto, image_gen, llm
from ..deps import CurrentUser, DbSession
from ..models import User, UserAISettings
from ..schemas import (
    AIImageSettingsOut,
    AISettingsIn,
    AISettingsOut,
    AITestIn,
    AITestOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/settings", tags=["ai"])

_TEST_TIMEOUT = 15


def _hint(key: str) -> str:
    # Too-short keys get no hint rather than leaking half the secret.
    return key[-4:] if len(key) >= 8 else ""


def _require_crypto() -> None:
    if not crypto.is_configured():
        raise HTTPException(
            status_code=503,
            detail="The server cannot store API keys: NEWSREAD_TOKEN_ENCRYPTION_KEY is not set.",
        )


def _out(row: UserAISettings | None, user: User, generations_this_month: int = 0) -> AISettingsOut:
    has_image_block = row is not None and bool(row.image_provider and row.image_model)
    prompt_fields = dict(
        image_generation_available=has_image_block or image_gen.is_configured(),
        image_prompt=user.image_prompt,
        default_image_prompt=image_gen.DEFAULT_IMAGE_PROMPT,
        image_gen_monthly_limit=user.image_gen_monthly_limit,
        image_generations_this_month=generations_this_month,
    )
    if row is None:
        return AISettingsOut(
            configured=False, system_available=llm.is_configured(), **prompt_fields
        )
    image = None
    if has_image_block:
        image = AIImageSettingsOut(
            provider=row.image_provider,
            model=row.image_model,
            base_url=row.image_base_url or "",
            key_hint=row.image_key_hint or "",
            extra_params=row.image_extra_params or "",
        )
    return AISettingsOut(
        configured=True,
        system_available=llm.is_configured(),
        provider=row.provider,
        model=row.model,
        base_url=row.base_url or None,
        key_hint=row.key_hint,
        supports_vision=row.supports_vision,
        image=image,
        **prompt_fields,
    )


@router.get("", response_model=AISettingsOut)
async def get_ai_settings(
    user: CurrentUser,
    session: DbSession,
):
    return _out(
        await session.get(UserAISettings, user.id),
        user,
        await image_gen.generations_this_month(session, user.id),
    )


@router.put("", response_model=AISettingsOut)
async def put_ai_settings(
    body: AISettingsIn,
    user: CurrentUser,
    session: DbSession,
):
    _require_crypto()
    row = await session.get(UserAISettings, user.id)

    # Like the API key, an omitted base URL keeps the stored one (same provider).
    base_url = body.base_url
    if not base_url and row is not None and row.provider == body.provider:
        base_url = row.base_url
    if body.provider == "custom" and not base_url:
        raise HTTPException(status_code=422, detail="A base URL is required for a custom provider")

    if body.api_key:
        api_key_enc = crypto.encrypt_token(body.api_key)
        key_hint = _hint(body.api_key)
    elif row is not None and row.provider == body.provider:
        # A stored key is only reused for the provider it was entered for.
        api_key_enc = row.api_key_enc
        key_hint = row.key_hint
    else:
        raise HTTPException(status_code=422, detail="An API key is required")

    image_provider = image_model = image_base_url = None
    image_api_key_enc = image_key_hint = image_extra_params = None
    if body.image is not None:
        image_extra_params = body.image.extra_params or None
        same_stored_image_provider = row is not None and row.image_provider == body.image.provider
        image_base_url = body.image.base_url or (
            row.image_base_url if same_stored_image_provider else None
        )
        if body.image.provider == "custom" and not image_base_url:
            raise HTTPException(
                status_code=422, detail="A base URL is required for a custom image provider"
            )
        image_provider = body.image.provider
        image_model = body.image.model
        if body.image.api_key:
            image_api_key_enc = crypto.encrypt_token(body.image.api_key)
            image_key_hint = _hint(body.image.api_key)
        elif same_stored_image_provider and row.image_api_key_enc:
            # A stored key is only ever reused for the provider it was
            # entered for — switching providers demands a fresh key.
            image_api_key_enc = row.image_api_key_enc
            image_key_hint = row.image_key_hint
        elif body.image.provider != body.provider:
            # Same provider falls back to the main key at call time; a
            # different provider needs its own.
            raise HTTPException(
                status_code=422, detail="An API key is required for the image model"
            )

    if row is None:
        row = UserAISettings(user_id=user.id)
        session.add(row)
    row.provider = body.provider
    row.model = body.model
    row.base_url = base_url if body.provider == "custom" else ""
    row.supports_vision = body.supports_vision
    row.api_key_enc = api_key_enc
    row.key_hint = key_hint
    row.image_provider = image_provider
    row.image_model = image_model
    row.image_base_url = image_base_url
    row.image_api_key_enc = image_api_key_enc
    row.image_key_hint = image_key_hint
    row.image_extra_params = image_extra_params
    await session.commit()
    return _out(row, user, await image_gen.generations_this_month(session, user.id))


@router.delete("", status_code=204)
async def delete_ai_settings(
    user: CurrentUser,
    session: DbSession,
):
    """Back to the server-wide default. Idempotent."""
    row = await session.get(UserAISettings, user.id)
    if row is not None:
        await session.delete(row)
        await session.commit()


@router.post("/test", response_model=AITestOut)
async def test_ai_settings(
    body: AITestIn,
    user: CurrentUser,
    session: DbSession,
):
    """Run one tiny completion so the user learns their key works before
    saving — never persists anything.

    The stored key is only ever combined with the stored provider/base_url:
    honoring a caller-supplied endpoint with the stored key would let anyone
    holding a session token exfiltrate the otherwise write-only key.
    """
    if body.api_key:
        provider, model, base_url = body.provider, body.model, body.base_url or ""
        if not provider or not model:
            raise HTTPException(
                status_code=422, detail="Provide provider and model along with the API key"
            )
        if provider == "custom" and not base_url:
            raise HTTPException(
                status_code=422, detail="A base URL is required for a custom provider"
            )
        api_key = body.api_key
    else:
        row = await session.get(UserAISettings, user.id)
        if row is None:
            raise HTTPException(
                status_code=422, detail="Provide an API key, or save settings first"
            )
        if (body.provider and body.provider != row.provider) or (
            body.base_url is not None and body.base_url != row.base_url
        ):
            raise HTTPException(
                status_code=422, detail="Provide the API key to test different settings"
            )
        provider, base_url = row.provider, row.base_url
        model = body.model or row.model
        # crypto.TokenCryptoError propagates to the app-level 503 handler.
        api_key = crypto.decrypt_token(row.api_key_enc)

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=llm.resolve_base_url(provider, base_url),
        timeout=_TEST_TIMEOUT,
        max_retries=0,
    )
    try:
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=5,
        )
    except Exception as exc:
        logger.info("AI settings test failed for user %s: %s", user.id, exc)
        return AITestOut(ok=False, detail=str(exc)[:300], model=model)
    finally:
        await client.close()
    return AITestOut(ok=True, model=model)
