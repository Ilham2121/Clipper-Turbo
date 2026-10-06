"""Base interfaces and protocols for pluggable ASR providers."""

import os
import shutil
import subprocess
from typing import Any, Protocol, runtime_checkable

from backend.core.errors import TranscriptionError


@runtime_checkable
class ASRProvider(Protocol):
    """Protocol for Speech-to-Text providers."""

    name: str

    def transcribe(
        self,
        media_path: str,
        *,
        language: str | None = None,
        prompt: str | None = None,
        keyterms: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Transcribe audio or video into segments with word timestamps.

        Returns:
            list of {
                "start": float,
                "end": float,
                "text": str,
                "words": [
                    {
                        "start": float,
                        "end": float,
                        "word": str,
                        "raw": str,
                        "prob": float,
                    }
                ]
            }
        """
        ...


def extract_audio_for_asr(media_path: str, target_dir: str | None = None) -> str:
    """Extract a lightweight 16kHz mono MP3/WAV from video for fast upload and processing.

    If media_path is already a small audio file (.mp3, .wav), returns it as is.
    Otherwise runs ffmpeg -vn -ac 1 -ar 16000 to drastically reduce file size (typically 10x-20x).
    """
    if not os.path.isfile(media_path):
        raise TranscriptionError(f"Media file not found for ASR: {media_path}")

    ext = os.path.splitext(media_path)[1].lower()
    if ext in (".mp3", ".wav", ".m4a", ".aac") and os.path.getsize(media_path) < 25 * 1024 * 1024:
        return media_path

    out_dir = target_dir or os.path.dirname(media_path)
    base_name = os.path.splitext(os.path.basename(media_path))[0]
    out_audio = os.path.join(out_dir, f"{base_name}_asr_16k.mp3")

    if os.path.isfile(out_audio) and os.path.getsize(out_audio) > 1024:
        return out_audio

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        media_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "48k",
        out_audio,
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            return media_path  # fallback to original if ffmpeg extraction fails
        return out_audio
    except Exception:
        return media_path
