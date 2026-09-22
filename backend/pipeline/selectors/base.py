from abc import ABC, abstractmethod


class SegmentSelector(ABC):
    """Abstract base class for segment selection strategies."""

    @abstractmethod
    def select(self, transcript: list[dict], video_duration: float) -> list[dict]:
        """Select segments from transcript within video duration.

        Args:
            transcript: List of transcript segment dicts.
            video_duration: Total video duration in seconds.

        Returns:
            List of dicts with at least 'start', 'end', and 'source'.
        """
        pass
