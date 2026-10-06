"""Deepgram cloud ASR provider (Nova-2 / Nova-3) with word timestamps."""

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Any

from backend.core.errors import TranscriptionError
from backend.pipeline.asr.base import extract_audio_for_asr
from backend.pipeline.text_normalize import normalize_transcript_segments

logger = logging.getLogger(__name__)


class DeepgramASRProvider:
    """Deepgram cloud ASR provider with low latency and precise word timestamps."""

    name = "deepgram"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = (api_key or os.getenv("DEEPGRAM_API_KEY") or "").strip()
        self.model = model or "nova-2"

    def transcribe(
        self,
        media_path: str,
        *,
        language: str | None = None,
        prompt: str | None = None,
        keyterms: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.api_key:
            raise TranscriptionError("Deepgram API key is not configured.")

        audio_path = extract_audio_for_asr(media_path)
        if not os.path.isfile(audio_path):
            raise TranscriptionError(f"Audio file not found: {audio_path}")

        query_params = [
            f"model={self.model}",
            "punctuate=true",
            "utterances=true",
        ]
        if language:
            query_params.append(f"language={language}")
        if keyterms:
            for term in keyterms[:20]:
                query_params.append(f"keywords={urllib.parse.quote(term)}")

        url = "https://api.deepgram.com/v1/listen?" + "&".join(query_params)

        with open(audio_path, "rb") as f:
            audio_data = f.read()

        req = urllib.request.Request(
            url,
            data=audio_data,
            headers={
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "audio/mpeg",
                "User-Agent": "ClipperTurbo/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise TranscriptionError(f"Deepgram ASR API error ({exc.code}): {err_body}") from exc
        except Exception as exc:
            raise TranscriptionError(f"Deepgram ASR request failed: {exc}") from exc

        results = resp_data.get("results", {})
        channels = results.get("channels", [])
        if not channels:
            raise TranscriptionError("Deepgram produced empty channels.")

        alternatives = channels[0].get("alternatives", [])
        if not alternatives:
            raise TranscriptionError("Deepgram produced empty alternatives.")

        alt = alternatives[0]
        words = alt.get("words", [])
        paragraphs = alt.get("paragraphs", {}).get("paragraphs", [])

        segments: list[dict[str, Any]] = []
        if paragraphs:
            for p in paragraphs:
                for sent in p.get("sentences", []):
                    s_text = sent.get("text", "").strip()
                    s_start = float(sent.get("start", 0.0))
                    s_end = float(sent.get("end", 0.0))
                    sent_words = [
                        {
                            "start": float(w.get("start", 0.0)),
                            "end": float(w.get("end", 0.0)),
                            "word": str(w.get("punctuated_word") or w.get("word", "")).strip(),
                            "raw": str(w.get("word", "")),
                            "prob": float(w.get("confidence", 1.0)),
                        }
                        for w in words
                        if s_start <= float(w.get("start", 0.0)) <= s_end
                    ]
                    segments.append({
                        "start": s_start,
                        "end": s_end,
                        "text": s_text,
                        "words": sent_words,
                    })
        elif words:
            curr_seg: dict[str, Any] = {"start": float(words[0]["start"]), "end": float(words[0]["end"]), "words": []}
            for w in words:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                p_word = str(w.get("punctuated_word") or w.get("word", "")).strip()
                curr_seg["words"].append({
                    "start": w_start,
                    "end": w_end,
                    "word": p_word,
                    "raw": str(w.get("word", "")),
                    "prob": float(w.get("confidence", 1.0)),
                })
                curr_seg["end"] = w_end
                if len(curr_seg["words"]) >= 8 or p_word.endswith((".", "!", "?")):
                    curr_seg["text"] = " ".join(item["word"] for item in curr_seg["words"])
                    segments.append(curr_seg)
                    curr_seg = {"start": w_end, "end": w_end, "words": []}
            if curr_seg.get("words"):
                curr_seg["text"] = " ".join(item["word"] for item in curr_seg["words"])
                segments.append(curr_seg)

        return normalize_transcript_segments(segments)
