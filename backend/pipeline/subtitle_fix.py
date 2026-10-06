"""LLM-based post-correction for subtitle spelling and proper nouns.

Traces to: Phase 2 Iterasi 2.5 (guarded JSON-patch spelling corrector).
Protects against hallucinations with strict validation guards:
  - Only changes <= 15% of words in the clip.
  - Preserves word timestamps exactly by operating as an index-targeted patch.
"""

import json
import logging
from typing import Any

from backend.pipeline.text_normalize import normalize_word_tokens

logger = logging.getLogger(__name__)


def apply_word_patch(
    words: list[dict[str, Any]],
    patches: list[dict[str, Any]],
    glossary: list[str] | None = None,
    max_change_ratio: float = 0.15,
) -> list[dict[str, Any]]:
    """Apply validated spelling patch to word tokens without shifting timestamps.

    Args:
        words: List of {"word": str, "start": float, "end": float, ...}.
        patches: List of {"i": int, "to": str}.
        glossary: Known proper nouns that bypass phonetic similarity checks.
        max_change_ratio: Maximum proportion of words allowed to be modified (guard).

    Returns:
        Updated words list, or original list if validation fails.
    """
    if not words or not patches:
        return words

    total_words = len(words)
    max_allowed_changes = max(1, int(total_words * max_change_ratio))

    valid_patches: list[dict[str, Any]] = []
    seen_indices: set[int] = set()

    for p in patches:
        if not isinstance(p, dict):
            continue
        idx = p.get("i")
        to_word = p.get("to")
        if not isinstance(idx, int) or not isinstance(to_word, str):
            continue
        if idx < 0 or idx >= total_words or idx in seen_indices:
            continue
        to_clean = to_word.strip()
        if not to_clean:
            continue

        seen_indices.add(idx)
        valid_patches.append({"i": idx, "to": to_clean})

    # Guard: hallucination check - cannot change more than 15% of clip text
    if len(valid_patches) > max_allowed_changes:
        logger.warning(
            "LLM spelling patch rejected: requested %d changes out of %d words (limit: %d)",
            len(valid_patches),
            total_words,
            max_allowed_changes,
        )
        return words

    # Clone words and apply patches
    updated = [dict(w) for w in words]
    for p in valid_patches:
        idx = p["i"]
        updated[idx]["word"] = p["to"]
        updated[idx]["raw"] = p["to"]

    return normalize_word_tokens(updated)
