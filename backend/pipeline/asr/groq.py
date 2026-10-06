"""Groq cloud ASR provider (whisper-large-v3-turbo) with word timestamps."""

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

DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"


class GroqASRProvider:
    """Groq Cloud Whisper provider providing fast inference with word timestamps."""

    name = "groq"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = (api_key or os.getenv("GROQ_API_KEY") or "").strip()
        self.model = model or DEFAULT_GROQ_MODEL

    def transcribe(
        self,
        media_path: str,
        *,
        language: str | None = None,
        prompt: str | None = None,
        keyterms: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.api_key:
            raise TranscriptionError("Groq API key is not configured.")

        # Extract lightweight audio if video
        audio_path = extract_audio_for_asr(media_path)
        if not os.path.isfile(audio_path):
            raise TranscriptionError(f"Audio file not found: {audio_path}")

        # Construct multipart/form-data upload using urllib
        boundary = "----WebKitFormBoundaryGroqASR" + os.urandom(8).hex()
        body_parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            part = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
            body_parts.append(part)

        add_field("model", self.model)
        add_field("response_format", "verbose_json")
        # timestamp_granularities[]=word
        part = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="timestamp_granularities[]"\r\n\r\n'
            f"word\r\n"
        ).encode("utf-8")
        body_parts.append(part)

        if language:
            add_field("language", language)

        combined_prompt = prompt or ""
        if keyterms:
            terms_str = ", ".join(keyterms)
            combined_prompt = f"{combined_prompt}. Kata kunci: {terms_str}".strip(". ")
        if combined_prompt:
            add_field("prompt", combined_prompt)

        # Add file
        with open(audio_path, "rb") as f:
            file_data = f.read()

        filename = os.path.basename(audio_path)
        file_header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: audio/mpeg\r\n\r\n"
        ).encode("utf-8")
        body_parts.append(file_header)
        body_parts.append(file_data)
        body_parts.append(b"\r\n")

        body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(body_parts)

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "ClipperTurbo/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise TranscriptionError(f"Groq ASR API error ({exc.code}): {err_body}") from exc
        except Exception as exc:
            raise TranscriptionError(f"Groq ASR request failed: {exc}") from exc

        return self._format_response(resp_data)

    @staticmethod
    def _format_response(resp_data: dict[str, Any]) -> list[dict[str, Any]]:
        raw_segments = resp_data.get("segments") or []
        raw_words = resp_data.get("words") or []

        # If verbose_json has segments
        segments: list[dict[str, Any]] = []
        if raw_segments:
            for s in raw_segments:
                seg_entry: dict[str, Any] = {
                    "start": float(s.get("start", 0.0)),
                    "end": float(s.get("end", 0.0)),
                    "text": str(s.get("text", "")).strip(),
                }
                seg_words = []
                for w in raw_words:
                    w_start = float(w.get("start", 0.0))
                    w_end = float(w.get("end", 0.0))
                    if seg_entry["start"] <= w_start <= seg_entry["end"] or (
                        w_start >= seg_entry["start"] and len(seg_words) == 0
                    ):
                        word_str = str(w.get("word", "")).strip()
                        if word_str:
                            seg_words.append({
                                "start": w_start,
                                "end": w_end,
                                "word": word_str,
                                "raw": str(w.get("word", "")),
                                "prob": 1.0,
                            })
                if seg_words:
                    seg_entry["words"] = seg_words
                segments.append(seg_entry)
        elif raw_words:
            # Fallback if no segments returned, chunk words into 6-word segments
            current_seg: dict[str, Any] = {"start": raw_words[0]["start"], "end": raw_words[0]["end"], "words": []}
            for w in raw_words:
                word_str = str(w.get("word", "")).strip()
                if not word_str:
                    continue
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                current_seg["words"].append({
                    "start": w_start,
                    "end": w_end,
                    "word": word_str,
                    "raw": str(w.get("word", "")),
                    "prob": 1.0,
                })
                current_seg["end"] = w_end
                if len(current_seg["words"]) >= 8:
                    current_seg["text"] = " ".join(item["word"] for item in current_seg["words"])
                    segments.append(current_seg)
                    current_seg = {"start": w_end, "end": w_end, "words": []}
            if current_seg.get("words"):
                current_seg["text"] = " ".join(item["word"] for item in current_seg["words"])
                segments.append(current_seg)
        else:
            text = str(resp_data.get("text", "")).strip()
            if text:
                segments.append({"start": 0.0, "end": 10.0, "text": text})

        if not segments:
            raise TranscriptionError("Groq ASR produced empty transcript.")

        return normalize_transcript_segments(segments)
