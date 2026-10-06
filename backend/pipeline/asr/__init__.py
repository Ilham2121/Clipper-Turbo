"""Pluggable ASR Provider Factory."""

import logging
from typing import Any

from backend.models.db import db
from backend.pipeline.asr.base import ASRProvider
from backend.pipeline.asr.deepgram import DeepgramASRProvider
from backend.pipeline.asr.groq import GroqASRProvider
from backend.pipeline.asr.local import LocalWhisperProvider
from backend.pipeline.asr.openai import OpenAIASRProvider

logger = logging.getLogger(__name__)


def get_asr_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    device: str | None = None,
    vad_filter: bool = True,
) -> ASRProvider:
    """Resolve and instantiate the requested ASR provider with fallback to local.

    Priority:
        1. Explicit provider_name argument.
        2. Stored settings in db ("asr_settings").
        3. Default to "local" faster-whisper.
    """
    settings = db.get_setting("asr_settings") or {}
    resolved_provider = (
        (provider_name or "").strip()
        or settings.get("provider")
        or "local"
    ).lower()

    resolved_model = model or settings.get("model")
    resolved_device = device or settings.get("device")
    resolved_vad = vad_filter if vad_filter is not None else settings.get("vad_filter", True)

    if resolved_provider == "groq":
        key = api_key or db.get_api_key("groq")
        return GroqASRProvider(api_key=key, model=resolved_model)

    if resolved_provider == "openai":
        key = api_key or db.get_api_key("openai")
        return OpenAIASRProvider(api_key=key, model=resolved_model)

    if resolved_provider == "deepgram":
        key = api_key or db.get_api_key("deepgram")
        return DeepgramASRProvider(api_key=key, model=resolved_model)

    # Default: local faster-whisper
    return LocalWhisperProvider(
        model_size=resolved_model,
        device=None if resolved_device == "auto" else resolved_device,
        vad_filter=resolved_vad,
    )
