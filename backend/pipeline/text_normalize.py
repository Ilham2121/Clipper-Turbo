"""Text and token normalization for subtitle spacing and vocabulary hygiene.

Traces to: improvement_plan.md — Phase 2 (Iteration 2.1).
Solves:
- Detached punctuation ("Halo , apa kabar ?") -> ("Halo, apa kabar?")
- Reduplication hyphens ("anak - anak") -> ("anak-anak")
- Formatted numbers ("10 . 000", "3 , 5") -> ("10.000", "3,5")
- English contractions ("do n't", "it 's") -> ("don't", "it's")
- Parentheses and quotes spacing ("( dalam )") -> ("(dalam)")
- Sub-word token gluing based on Whisper leading-space metadata.
"""

import re
import unicodedata
from typing import Any

# Punctuation that should NOT have space before it
_NO_SPACE_BEFORE = re.compile(r"\s+([,.\?!:;%\)\]\}»”’'])")

# Punctuation that should NOT have space after it
_NO_SPACE_AFTER = re.compile(r"([\(\[\{«“‘])\s+")

# Hyphenated reduplication words or compounds (e.g. anak - anak -> anak-anak)
_REDUPLICATION_PATTERN = re.compile(r"\b([A-Za-z0-9]+)\s*-\s*([A-Za-z0-9]+)\b")

# Thousands separator or decimals with spaces (e.g. 10 . 000 -> 10.000 or 3 , 14 -> 3,14)
_NUMBER_SPACING_DOT = re.compile(r"(\d+)\s*\.\s*(\d+)")
_NUMBER_SPACING_COMMA = re.compile(r"(\d+)\s*,\s*(\d+)")

# English contractions split by tokenizer
_CONTRACTIONS_PATTERN = re.compile(
    r"\b([A-Za-z]+)\s+('(?:s|m|d|ll|re|ve|t)|n't)\b", re.IGNORECASE
)

# Currency notation (e.g. Rp . 50 -> Rp50, Rp 50 -> Rp50)
_CURRENCY_PATTERN = re.compile(r"\b(Rp|IDR|USD)\s*\.?\s*(\d+)", re.IGNORECASE)


def clean_text_spacing(text: str) -> str:
    """Normalize text spacing, punctuation boundaries, and Unicode NFC."""
    if not text:
        return ""

    # 1. Unicode NFC normalization
    normalized = unicodedata.normalize("NFC", text)

    # 2. Collapse internal whitespace
    cleaned = " ".join(normalized.split())

    # 3. Currency formatting (Rp 10.000 -> keep standard)
    cleaned = _CURRENCY_PATTERN.sub(r"\1 \2", cleaned)

    # 4. Indonesian reduplication / compounds: "anak - anak" -> "anak-anak"
    cleaned = _REDUPLICATION_PATTERN.sub(r"\1-\2", cleaned)

    # 5. Number separators: "10 . 000" -> "10.000", "3 , 5" -> "3,5"
    cleaned = _NUMBER_SPACING_DOT.sub(r"\1.\2", cleaned)
    cleaned = _NUMBER_SPACING_COMMA.sub(r"\1,\2", cleaned)

    # 6. Contractions: "do n't" -> "don't", "it 's" -> "it's"
    cleaned = _CONTRACTIONS_PATTERN.sub(r"\1\2", cleaned)

    # 7. No space before punctuation: "Halo , apa kabar ?" -> "Halo, apa kabar?"
    cleaned = _NO_SPACE_BEFORE.sub(r"\1", cleaned)

    # 8. No space after open brackets/quotes: "( halo )" -> "(halo)"
    cleaned = _NO_SPACE_AFTER.sub(r"\1", cleaned)

    # 9. Clean up quote spacing: " kata " -> "kata"
    cleaned = re.sub(r'"\s+([^"]+?)\s+"', r'"\1"', cleaned)

    # 10. Final trim and single space guarantee
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_word_tokens(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge fragmented tokens and detached punctuation into natural display words.

    Whisper emits sub-words where tokens without a leading space are continuations.
    This function glues tokens together while preserving accurate start and end timestamps.
    """
    if not words:
        return []

    merged: list[dict[str, Any]] = []

    for w in words:
        has_raw = "raw" in w and bool(w.get("raw"))
        raw_text = str(w.get("raw", ""))
        word_text = str(w.get("word") or "").strip()
        if not word_text:
            continue

        start = float(w.get("start", 0.0))
        end = float(w.get("end", start + 0.1))
        prob = float(w.get("prob", 1.0))

        is_punctuation = bool(re.match(r"^[,.\?!:;%\)\]\}»”’']+$", word_text))
        is_hyphen = word_text == "-"
        is_contraction = bool(re.match(r"^('(?:s|m|d|ll|re|ve|t)|n't)$", word_text, re.IGNORECASE))

        # Check if this token should be glued to the previous token
        should_glue = False
        if merged:
            prev_word = merged[-1]["word"]
            if prev_word.endswith("-"):
                # previous token was a hyphen, glue: "anak-" + "anak" -> "anak-anak"
                should_glue = True
            elif is_punctuation or is_contraction or is_hyphen:
                should_glue = True
            elif has_raw and not raw_text.startswith((" ", "\t")) and not prev_word.endswith((" ", "-", "(", "[", "{", "“", '"')):
                should_glue = True

        if should_glue and merged:
            prev = merged[-1]
            prev["word"] = clean_text_spacing(f"{prev['word']}{word_text}")
            prev["end"] = max(prev["end"], end)
            if "prob" in prev:
                prev["prob"] = min(prev["prob"], prob)
        else:
            merged.append({
                "word": word_text,
                "start": start,
                "end": max(start + 0.05, end),
                "prob": prob,
            })

    # Final pass to clean display spacing on each merged word
    for m in merged:
        m["word"] = clean_text_spacing(m["word"])

    return merged


def normalize_transcript_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean all transcript segments and word timestamps for subtitle generation."""
    normalized: list[dict[str, Any]] = []

    for seg in segments:
        entry = dict(seg)
        words = seg.get("words")
        if words:
            cleaned_words = normalize_word_tokens(words)
            entry["words"] = cleaned_words
            entry["text"] = clean_text_spacing(" ".join(w["word"] for w in cleaned_words))
        else:
            entry["text"] = clean_text_spacing(str(seg.get("text", "")))

        normalized.append(entry)

    return normalized
