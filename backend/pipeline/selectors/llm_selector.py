"""LLM Segment Selector for automatic clip extraction.

Traces to: FR-5 (automatic segment selection via LLM),
           AC-4 (candidate segments with start/end),
           EC-4 (handling malformed/non-JSON LLM response),
           EC-5 (handling invalid API key / quota limits vs network error),
           NFR-3 (masking API key, never hardcoding or logging in plain text).
"""

import json
import re
from typing import Any

import requests

from backend.core.errors import LLMAuthError, LLMError, LLMQuotaError, LLMResponseError
from backend.pipeline.selectors.base import SegmentSelector


class LLMSelector(SegmentSelector):
    """Selects candidate clip segments from video transcripts using an LLM provider."""

    PROVIDER_CONFIGS = {
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4o-mini",
        },
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        },
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "default_model": "llama-3.3-70b-versatile",
        },
        "nvidia": {
            "base_url": "https://integrate.api.nvidia.com/v1",
            "default_model": "meta/llama-3.1-70b-instruct",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "default_model": "deepseek-chat",
        },
    }

    def __init__(
        self,
        api_key: str,
        provider: str = "openai",
        model: str | None = None,
        base_url: str | None = None,
    ):
        if not api_key or not isinstance(api_key, str) or not api_key.strip():
            raise LLMAuthError("LLM API key is required and cannot be empty.")

        self.api_key = api_key.strip()
        self.provider = provider.lower()

        cfg = self.PROVIDER_CONFIGS.get(self.provider, {})
        self.base_url = base_url or cfg.get("base_url", "https://api.openai.com/v1")
        self.model = model or cfg.get("default_model", "gpt-4o-mini")

    def __repr__(self) -> str:
        # NFR-3: Never expose API key in plain text representation or logs
        return f"LLMSelector(provider='{self.provider}', model='{self.model}', api_key='***')"

    def _strip_markdown_fences(self, content: str) -> str:
        """Strip markdown code block fences if present (e.g. ```json ... ```)."""
        if not isinstance(content, str):
            raise LLMResponseError(
                "LLM mengembalikan konten kosong (null) — coba model lain atau ulangi."
            )
        content = content.strip()
        fenced_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)
        match = fenced_pattern.match(content)
        if match:
            return match.group(1).strip()
        return content

    def _build_prompt(self, transcript: list[dict], video_duration: float) -> str:
        """Construct user prompt incorporating transcript text and constraints."""
        transcript_text = "\n".join(
            f"[{seg.get('start', 0.0):.2f} - {seg.get('end', 0.0):.2f}] {seg.get('text', '')}"
            for seg in transcript
        )
        return (
            f"Video duration: {video_duration:.2f} seconds.\n\n"
            f"Transcript:\n{transcript_text}\n\n"
            "Task: Identify the most interesting, coherent, or viral clip segments from this video.\n"
            "Constraints:\n"
            "- Each segment must have start and end timestamps in seconds (start < end, end <= video duration).\n"
            "- Return ONLY a JSON array of segment objects.\n"
            "- Each object must have: 'start' (float), 'end' (float), 'title' (string), 'reason' (string).\n"
            "- Optional virality fields (Fase 3 Iterasi 3.2, omit if unsure): "
            "'viralityScore' (int 1-100), 'hook' (short hook-type label), "
            "'caption' (1-2 sentence ready-to-post caption), 'hashtags' (array of tag strings).\n"
        )

    def _call_api(self, prompt: str) -> str:
        """Perform HTTP request to the LLM completion endpoint."""
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/clipper-turbo"
            headers["X-Title"] = "YouTube Clip Generator"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional video editor who selects engaging clip segments from video transcripts. Always respond strictly in valid JSON format.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
        except requests.exceptions.RequestException as exc:
            # Distinguish regular network / connection failure from auth/quota error (EC-5)
            raise LLMError(f"LLM network error: {exc}") from exc

        # Handle specific HTTP error status codes (EC-5)
        if resp.status_code in (401, 403):
            raise LLMAuthError(f"Invalid or unauthorized LLM API key (HTTP {resp.status_code}).")
        elif resp.status_code == 429:
            raise LLMQuotaError(f"LLM API quota exceeded or rate limit reached (HTTP {resp.status_code}).")
        elif not resp.ok:
            raise LLMError(f"LLM API request failed with HTTP {resp.status_code}.")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMResponseError(f"Malformed response payload from LLM API: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError(
                "LLM mengembalikan konten kosong (null) — coba model lain atau ulangi."
            )
        return content

    def test_connection(self, timeout: float = 10.0) -> dict[str, Any]:
        """Verify provider credentials with a lightweight GET {base}/models call.

        Returns {"ok": bool, "message": str}. Never includes the API key.
        """
        url = f"{self.base_url.rstrip('/')}/models"
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=timeout,
            )
        except requests.exceptions.Timeout:
            return {"ok": False, "message": "Koneksi timeout — periksa jaringan atau endpoint."}
        except requests.exceptions.RequestException as exc:
            return {"ok": False, "message": f"Koneksi gagal: {exc}"}

        if resp.status_code == 200:
            return {"ok": True, "message": f"Tersambung ke {self.provider} ({self.model})."}
        if resp.status_code in (401, 403):
            return {"ok": False, "message": "API key salah atau tidak punya akses (401/403)."}
        if resp.status_code == 404:
            return {"ok": False, "message": "Model atau endpoint tidak ditemukan (404) — periksa nama model dan URL."}
        if resp.status_code == 429:
            return {"ok": False, "message": "Kuota habis atau rate limit (429)."}
        return {"ok": False, "message": f"Provider menolak dengan HTTP {resp.status_code}."}

    def select(self, transcript: list[dict], video_duration: float) -> list[dict]:
        """Select clip segments using LLM analysis of the transcript.

        Args:
            transcript: List of transcript segments.
            video_duration: Total duration of the video in seconds.

        Returns:
            List of candidate segments with 'start', 'end', 'source': 'llm', 'title', and 'reason'.

        Raises:
            LLMAuthError: If the API key is invalid or unauthorized (EC-5).
            LLMQuotaError: If quota is exhausted or rate limit hit (EC-5).
            LLMResponseError: If response is malformed, not valid JSON, or missing required structure (EC-4).
            LLMError: On other LLM or network failures.
        """
        if not transcript:
            raise LLMResponseError("Transcript is empty; cannot select segments.")

        prompt = self._build_prompt(transcript, video_duration)
        raw_content = self._call_api(prompt)

        cleaned_json = self._strip_markdown_fences(raw_content)

        try:
            parsed = json.loads(cleaned_json)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"LLM response is not valid JSON: {exc}") from exc

        # Accept either a direct list or a dict containing a 'segments' or 'clips' list
        if isinstance(parsed, dict):
            if "segments" in parsed and isinstance(parsed["segments"], list):
                raw_segments = parsed["segments"]
            elif "clips" in parsed and isinstance(parsed["clips"], list):
                raw_segments = parsed["clips"]
            else:
                raise LLMResponseError("LLM response JSON object missing 'segments' or 'clips' list.")
        elif isinstance(parsed, list):
            raw_segments = parsed
        else:
            raise LLMResponseError("LLM response must be a JSON array or object with segments list.")

        if not raw_segments:
            raise LLMResponseError("LLM returned an empty segment list.")

        validated: list[dict[str, Any]] = []
        for idx, seg in enumerate(raw_segments):
            if not isinstance(seg, dict) or "start" not in seg or "end" not in seg:
                raise LLMResponseError(f"Segment at index {idx} missing 'start' or 'end' keys.")

            start = seg["start"]
            end = seg["end"]

            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                raise LLMResponseError(f"Segment at index {idx} has non-numeric timestamps: start={start}, end={end}")

            start_f = float(start)
            end_f = float(end)

            if start_f < 0:
                raise LLMResponseError(f"Segment at index {idx} has negative start timestamp: {start_f}")
            if start_f >= end_f:
                raise LLMResponseError(f"Segment at index {idx} has start >= end: start={start_f}, end={end_f}")
            if end_f > video_duration:
                raise LLMResponseError(
                    f"Segment at index {idx} has end timestamp ({end_f}) exceeding video duration ({video_duration})"
                )

            validated.append({
                "start": start_f,
                "end": end_f,
                "source": "llm",
                "title": seg.get("title", f"Clip {idx + 1}"),
                "reason": seg.get("reason", "Selected by LLM"),
                # Fase 3 Iterasi 3.2: optional virality fields with safe fallbacks
                # (older LLM responses without them stay valid — no new EC-4).
                "virality_score": self._parse_virality_score(seg.get("viralityScore")),
                "hook": str(seg.get("hook", "") or ""),
                "caption": str(seg.get("caption", "") or ""),
                "hashtags": self._parse_hashtags(seg.get("hashtags")),
            })

        return validated

    @staticmethod
    def _parse_virality_score(value: Any) -> int | None:
        """Clamp LLM virality score to 1-100; None when missing/invalid."""
        if isinstance(value, bool):
            return None
        try:
            score = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return max(1, min(100, score))

    @staticmethod
    def _parse_hashtags(value: Any) -> list[str]:
        """Normalize LLM hashtags to a clean list of '#tag' strings."""
        if not isinstance(value, list):
            return []
        tags = []
        for tag in value:
            if not isinstance(tag, str):
                continue
            clean = tag.strip().lstrip("#").strip()
            if clean:
                tags.append(f"#{clean}")
        return tags
