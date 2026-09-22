# Clipper Turbo

Local web app that turns a YouTube video into short vertical clips. Give it a URL,
pick segments manually or let an LLM pick them, and it renders 9:16 / 16:9 MP4
files with burned-in subtitles.

Everything runs on your machine. The only external service is the LLM you point
it at, and that is optional (manual mode never calls one).

## What it does

Pipeline runs as five sequential stages, each updating job status as it goes:

| # | Stage | Tool |
|---|-------|------|
| 1 | Download video + audio + YouTube captions | `yt-dlp` |
| 2 | Transcribe (word-level timestamps) | `faster-whisper` (local, free) |
| 3 | Pick segments | `LLMSelector` (any OpenAI-compatible API) or `ManualSelector` |
| 4 | Render clips | `ffmpeg` |
| 5 | Serve download links | FastAPI + SQLite |

Details worth knowing:

- **Subtitles are phrase blocks**, not word-by-word karaoke. Words are grouped
  2–4 at a time with inline highlight, and events never overlap — the earlier
  per-word approach produced stacked, unreadable text.
- **ASR text is the subtitle source**, not YouTube captions. Auto-captions
  frequently mishear words. YouTube captions are only a fallback when ASR fails.
- **Smart crop** tracks the largest face across the clip and pans the 9:16
  window to follow it, instead of a fixed center crop.
- **Audio is normalized** to −16 LUFS (EBU R128) so clips sound consistent on
  phones.
- **Hardware encoding** is auto-detected (`h264_nvenc` → `h264_qsv` → `libx264`).
  Detection includes a real 0.5s test render, because an encoder can be listed
  while the installed GPU driver is too old to run it.
- **Progress is streamed** over SSE, with a polling fallback.
- **Storage is managed**: raw files are deleted after render, and a background
  sweep clears expired jobs. The Settings panel shows what is reclaimable.

## Requirements

- Python 3.11+
- Node.js 18+
- `ffmpeg` on `PATH`
- ~2 GB RAM free for `faster-whisper` (`small` model ≈ 500 MB)

## Setup

**Backend** (from the project root):

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

**Frontend**:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to port 8000.

For LLM mode, either paste an API key in the form or save it once in
Settings → API Key (stored encrypted in the local database, reused after that).

## Tests

```bash
python -m pytest test_youtube_clip_generator.py
```

The suite maps to the acceptance criteria and edge cases in `prd-youtube-clip-generator.md`.
`conftest.py` redirects the database to a temp file so running tests never touches
`storage/clipper.db`.

## Configuration

All optional; defaults are sane.

| Variable | Default | Purpose |
|---|---|---|
| `CLIPPER_MAX_CONCURRENT_JOBS` | `2` | Pipeline jobs processed at once |
| `CLIPPER_RENDER_WORKERS` | `2` | Clips rendered in parallel |
| `CLIPPER_ASR_MODEL` | `small` | Whisper size (`tiny`/`base`/`small`/`medium`/`large-v3-turbo`) |
| `CLIPPER_ASR_COMPUTE_TYPE` | `int8` | ctranslate2 compute type |
| `CLIPPER_VIDEO_CODEC` | auto | Force `h264_nvenc` / `h264_qsv` / `libx264` |
| `CLIPPER_JOB_TTL_HOURS` | `24` | Age at which job dirs are purged |
| `CLIPPER_CLEANUP_INTERVAL_MINUTES` | `60` | Sweep interval; `0` disables |
| `CLIPPER_DB_PATH` | `storage/clipper.db` | SQLite location |
| `CLIPPER_SECRET_KEY` | dev default | Key for encrypting stored API keys |

If ASR cannot load the configured model (usually out of memory), it falls back
to a smaller one instead of failing the job.

## Layout

```
backend/
  api/routes/     jobs, llm_keys, maintenance
  core/           limiter, cleanup, config, errors
  models/db.py    SQLAlchemy store (jobs, clips, llm_api_keys, app_settings)
  pipeline/       ingest, transcribe, karaoke, speaker, render, pipeline
  selectors/      llm_selector, manual_selector
frontend/src/
  components/     JobForm, JobProgress, ClipResults, SettingsDrawer, ...
  api/client.ts
```

## Known limitations

- Single-process, single-user. SQLite and the in-process job queue are fine for
  self-hosting but are not what you want for multi-tenant use.
- Center crop is used when no face is detected; smart crop tracks the single
  largest face, so two-person podcasts are not split-screened.
- Stored API keys use XOR encryption with a key from `CLIPPER_SECRET_KEY`. Set
  a real secret (and treat it as sensitive) if this runs anywhere but your own
  machine.
- `os.startfile()` reveal-folder only works on a local desktop host.

## Docs

- `prd-youtube-clip-generator.md` — requirements and acceptance criteria
- `plan.md` — architecture and implementation order
- `optimize.md` — the phased roadmap that produced the current feature set
