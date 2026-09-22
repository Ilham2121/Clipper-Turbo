# Backend image: FastAPI + ffmpeg + local Whisper.
#
# Debian-based (not alpine) on purpose: faster-whisper's ctranslate2 wheels and
# MediaPipe's native deps are glibc-only, and building them from source in a
# musl container is painful for contributors.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/app/.cache/huggingface

# ffmpeg is a hard runtime dependency. libgl1/libglib2.0 are required by
# opencv (pulled in via mediapipe) — without them `import cv2` fails.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so code edits don't invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend/ ./backend/
COPY conftest.py test_youtube_clip_generator.py ./

# Non-root user; owns /app so storage/ and the Whisper cache are writable.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/storage \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
