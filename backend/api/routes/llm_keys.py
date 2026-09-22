"""Stored LLM API key management endpoints.

Keys are stored server-side encrypted at rest (NFR-3), one row per provider.
The raw key is never returned — list responses carry only a masked preview.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.config import decrypt_at_rest, encrypt_at_rest, mask_api_key
from backend.models.db import db
from backend.pipeline.selectors.llm_selector import LLMSelector

router = APIRouter(prefix="/api/llm-keys", tags=["llm-keys"])

KNOWN_PROVIDERS = set(LLMSelector.PROVIDER_CONFIGS) | {"custom"}


def _check_provider(provider: str) -> str:
    name = (provider or "").strip().lower()
    if name not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"Unknown LLM provider: '{provider}'")
    return name


class LlmKeySaveRequest(BaseModel):
    apiKey: str
    model: str | None = None
    baseUrl: str | None = None


class LlmKeyTestRequest(BaseModel):
    apiKey: str | None = None
    model: str | None = None
    baseUrl: str | None = None


class LlmKeyStatus(BaseModel):
    provider: str
    hasKey: bool
    maskedKey: str | None = None
    model: str | None = None
    baseUrl: str | None = None
    updatedAt: datetime | None = None
    lastVerifiedAt: datetime | None = None
    lastVerifiedOk: bool | None = None


class LlmTestResult(BaseModel):
    ok: bool
    message: str


@router.get("", response_model=list[LlmKeyStatus])
def list_llm_keys() -> list[LlmKeyStatus]:
    stored = {row.provider: row for row in db.list_llm_keys()}
    result = []
    for provider in sorted(KNOWN_PROVIDERS):
        row = stored.get(provider)
        if row is None:
            result.append(LlmKeyStatus(provider=provider, hasKey=False))
            continue
        try:
            masked = mask_api_key(decrypt_at_rest(row.encrypted_key))
        except Exception:
            masked = "***"
        result.append(
            LlmKeyStatus(
                provider=provider,
                hasKey=True,
                maskedKey=masked,
                model=row.model,
                baseUrl=row.base_url,
                updatedAt=row.updated_at,
                lastVerifiedAt=row.last_verified_at,
                lastVerifiedOk=row.last_verified_ok,
            )
        )
    return result


def _status_for_provider(provider: str) -> LlmKeyStatus:
    row = db.get_llm_key(provider)
    if row is None:
        return LlmKeyStatus(provider=provider, hasKey=False)
    try:
        masked = mask_api_key(decrypt_at_rest(row.encrypted_key))
    except Exception:
        masked = "***"
    return LlmKeyStatus(
        provider=provider,
        hasKey=True,
        maskedKey=masked,
        model=row.model,
        baseUrl=row.base_url,
        updatedAt=row.updated_at,
        lastVerifiedAt=row.last_verified_at,
        lastVerifiedOk=row.last_verified_ok,
    )


@router.put("/{provider}", response_model=LlmKeyStatus)
def save_llm_key(provider: str, body: LlmKeySaveRequest) -> LlmKeyStatus:
    name = _check_provider(provider)
    raw = (body.apiKey or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="apiKey is required.")
    cfg = LLMSelector.PROVIDER_CONFIGS.get(name, {})
    db.save_llm_key(
        provider=name,
        encrypted_key=encrypt_at_rest(raw),
        model=(body.model or None) or cfg.get("default_model"),
        base_url=(body.baseUrl or None) or cfg.get("base_url"),
    )
    return _status_for_provider(name)


@router.delete("/{provider}")
def delete_llm_key(provider: str) -> dict:
    name = _check_provider(provider)
    if not db.delete_llm_key(name):
        raise HTTPException(status_code=404, detail=f"No stored key for provider '{name}'.")
    return {"deleted": True, "provider": name}


@router.post("/{provider}/test", response_model=LlmTestResult)
def test_llm_key(provider: str, body: LlmKeyTestRequest | None = None) -> LlmTestResult:
    name = _check_provider(provider)
    cfg = LLMSelector.PROVIDER_CONFIGS.get(name, {})

    raw = ((body.apiKey if body else None) or "").strip()
    model = ((body.model if body else None) or "").strip() or None
    base_url = ((body.baseUrl if body else None) or "").strip() or None
    testing_stored = not raw

    if testing_stored:
        row = db.get_llm_key(name)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No stored key for provider '{name}'.")
        try:
            raw = decrypt_at_rest(row.encrypted_key)
        except Exception:
            return LlmTestResult(ok=False, message="Key tersimpan rusak — simpan ulang.")
        model = model or row.model
        base_url = base_url or row.base_url

    try:
        selector = LLMSelector(
            api_key=raw,
            provider=name,
            model=model or cfg.get("default_model"),
            base_url=base_url or cfg.get("base_url"),
        )
    except Exception as exc:
        return LlmTestResult(ok=False, message=str(exc))

    result = selector.test_connection()
    if testing_stored:
        db.mark_llm_key_verified(name, bool(result["ok"]))
    return LlmTestResult(ok=bool(result["ok"]), message=str(result["message"]))
