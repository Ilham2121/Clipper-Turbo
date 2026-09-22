class DownloadError(Exception):
    """Raised when video download fails.

    Covers: EC-1 (invalid/deleted/private URL), EC-7 (network failure after retries).
    """

    pass


class TranscriptionError(Exception):
    """Raised when transcription fails.

    Covers: EC-9 (ASR model load failure, audio corrupt, timeout).
    """

    pass


class SegmentValidationError(Exception):
    """Raised when manual segment timestamps are invalid.

    Covers: EC-6 (start > end, out of video duration bounds).
    """

    pass


class LLMError(Exception):
    """Base exception for errors during LLM segment selection.

    Covers general LLM failure, connection timeouts, and network issues.
    """

    pass


class LLMAuthError(LLMError):
    """Raised when LLM API key is invalid or unauthorized (HTTP 401/403).

    Covers: EC-5.
    """

    pass


class LLMQuotaError(LLMError):
    """Raised when LLM quota is exhausted or rate limited (HTTP 429).

    Covers: EC-5.
    """

    pass


class LLMResponseError(LLMError):
    """Raised when LLM response cannot be parsed as JSON or violates expected schema.

    Covers: EC-4.
    """

    pass


class RenderError(Exception):
    """Raised when video rendering, trimming, or subtitle burning fails.

    Covers: EC-8 (disk full during render), FFmpeg process failures.
    """

    pass
