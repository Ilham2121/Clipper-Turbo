"""Ingest stage — download video + audio from YouTube via yt-dlp.

Traces to: FR-1 (accept YouTube URL), FR-2 (download video+audio),
           EC-1 (invalid URL handling), EC-7 (retry on network failure).
"""

import glob
import ipaddress
import os
import re
import time
from urllib.parse import urlparse

import yt_dlp

from backend.core.errors import DownloadError


ALLOWED_HOSTNAMES = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

YOUTUBE_URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"[A-Za-z0-9_-]{11}"
)


def validate_youtube_url(url: str) -> bool:
    """Validate that a URL is a legitimate YouTube URL and prevent SSRF attacks.

    Guards against:
    - Non-HTTP/HTTPS schemes (e.g. file://, gopher://, ftp://)
    - IP address targets (e.g. 169.254.169.254, 127.0.0.1, internal subnets)
    - Host spoofing / subdomain attacks (e.g. youtube.com.attacker.com)
    - Embedded userinfo (e.g. user:pass@youtube.com)
    - Localhost or loopback access
    """
    if not url or not isinstance(url, str):
        return False

    url_str = url.strip()
    if not url_str:
        return False

    if not url_str.startswith(("http://", "https://")):
        if url_str.startswith(("youtube.com/", "www.youtube.com/", "youtu.be/", "m.youtube.com/")):
            parsed = urlparse("https://" + url_str)
        else:
            return False
    else:
        parsed = urlparse(url_str)

    if parsed.scheme not in ("http", "https"):
        return False

    if parsed.username or parsed.password:
        return False

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return False

    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False

    try:
        ipaddress.ip_address(hostname)
        return False
    except ValueError:
        pass

    is_allowed_domain = (
        hostname in ALLOWED_HOSTNAMES
        or hostname.endswith(".youtube.com")
    )
    if not is_allowed_domain:
        return False

    path = parsed.path
    if hostname == "youtu.be":
        video_id = path.lstrip("/")
        if not video_id:
            return False
    elif "youtube.com" in hostname:
        if path.startswith("/shorts/") or path.startswith("/embed/") or path.startswith("/v/"):
            pass
        elif path == "/watch" or path.startswith("/watch"):
            pass
        elif "v=" in (parsed.query or ""):
            pass
        else:
            if not parsed.query and path in ("", "/"):
                return False

    return True


def _is_valid_youtube_url(url: str) -> bool:
    return validate_youtube_url(url)


def probe_video_metadata(url: str) -> dict | None:
    """Fetch video title + duration without downloading (metadata-only probe).

    Returns {"title", "duration"} or None on any failure. Optional by design:
    callers must not fail the job when the probe is unavailable.
    """
    if not validate_youtube_url(url):
        return None
    try:
        with yt_dlp.YoutubeDL({
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 30,
            "allowed_extractors": ["youtube", "youtube:shorts"],
        }) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return None
        return {
            "title": info.get("title", ""),
            "duration": float(info.get("duration") or 0.0),
        }
    except Exception:
        return None


def estimate_processing_seconds(duration: float, hardware_accelerated: bool = False) -> dict:
    """Heuristic processing-time estimate from video duration.

    CPU (libx264): ~1.0x duration (download 25% + ASR 45% + render 30%).
    Hardware (nvenc/qsv): ~0.35x duration. Explicitly a rough estimate.
    """
    factor = 0.35 if hardware_accelerated else 1.0
    total = max(30.0, duration * factor)
    return {
        "total": round(total),
        "download": round(total * 0.25),
        "transcribe": round(total * 0.45),
        "render": round(total * 0.30),
    }


def _find_subtitle_file(output_dir: str, video_id: str) -> str | None:
    """Find downloaded .vtt subtitle for a video id (None when absent).

    Prefers manual English/Indonesian subs, then auto-generated, then any.
    """
    candidates = sorted(glob.glob(os.path.join(output_dir, f"{video_id}.*.vtt")))
    if not candidates:
        return None

    def rank(path: str) -> tuple[int, str]:
        name = os.path.basename(path).lower()
        if ".en.vtt" in name or ".id.vtt" in name:
            return (0, name)
        if "live_chat" in name:
            return (3, name)
        return (1, name)

    return sorted(candidates, key=rank)[0]


def download_video(
    url: str,
    output_dir: str,
    max_retries: int = 1,
) -> dict:
    """Download video+audio from a YouTube URL and merge into mp4.

    Args:
        url: YouTube video URL.
        output_dir: Directory to save the downloaded file.
        max_retries: How many times to retry on transient (network) errors.

    Returns:
        dict with keys:
            - video_path (str): absolute path to the downloaded mp4.
            - title (str): video title.
            - duration (float): video duration in seconds.
            - subtitle_path (str | None): downloaded .vtt path, or None (ASR fallback).

    Raises:
        DownloadError: On invalid URL (EC-1) or persistent failure after retries (EC-7).
    """
    if not validate_youtube_url(url):
        raise DownloadError(f"Invalid YouTube URL: {url}")

    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": 1024 * 1024 * 1024,
        "allowed_extractors": ["youtube", "youtube:shorts"],
    }

    # FR-3: subtitle-only pass (video already on disk). Best-effort on purpose:
    # a subtitle failure (e.g. HTTP 429) must NEVER fail the video download —
    # transcribe() falls back to ASR (AC-3).
    sub_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en.*", "en", "id.*", "id"],
        "subtitlesformat": "vtt/best",
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "allowed_extractors": ["youtube", "youtube:shorts"],
        "socket_timeout": 30,
    }

    last_error = None
    attempts = max_retries + 1
    info: dict | None = None

    for attempt in range(1, attempts + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break

        except yt_dlp.utils.DownloadError as exc:
            error_msg = str(exc).lower()
            is_transient = any(
                kw in error_msg
                for kw in ("urlopen error", "timed out", "connection reset", "http error 429", "http error 503")
            )

            if not is_transient:
                raise DownloadError(str(exc)) from exc

            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** attempt)

    if info is None:
        raise DownloadError(
            f"Download failed after {max_retries + 1} attempts: {last_error}"
        )

    try:
        with yt_dlp.YoutubeDL(sub_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception:
        pass  # subtitle unavailable -> ASR fallback downstream

    video_path = os.path.join(output_dir, f"{info['id']}.mp4")
    return {
        "video_path": video_path,
        "title": info.get("title", ""),
        "duration": info.get("duration", 0.0),
        "subtitle_path": _find_subtitle_file(output_dir, info["id"]),
    }
