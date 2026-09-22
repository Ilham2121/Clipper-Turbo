"""Active speaker tracking for smart vertical crop.

Traces to: Fase 4 Iterasi 4.2 (Active Speaker Tracking / Smart Crop).
Samples frames across a clip segment, detects the largest face per frame
with MediaPipe Face Detection, and returns the median face x-center so the
9:16 crop follows the speaker instead of the frame center.
"""

import statistics

DEFAULT_SAMPLES = 5


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _largest_face_center(results) -> float | None:
    """Normalized x-center of the largest detected face, or None."""
    detections = getattr(results, "detections", None)
    if not detections:
        return None
    biggest = max(
        detections,
        key=lambda d: (
            d.location_data.relative_bounding_box.width
            * d.location_data.relative_bounding_box.height
        ),
    )
    box = biggest.location_data.relative_bounding_box
    return _clamp01(box.xmin + box.width / 2)


def _open_detector():
    """Return a MediaPipe face detector, or None when unavailable."""
    try:
        import mediapipe as mp
    except ImportError:
        return None
    return mp.solutions.face_detection.FaceDetection(
        model_selection=0, min_detection_confidence=0.5
    )


def detect_face_track(
    video_path: str,
    start: float,
    end: float,
    window: float = 2.0,
    max_windows: int = 30,
) -> list[tuple[float, float]]:
    """Detect a time-ordered face-x track over a clip (dynamic crop pan).

    Returns [(t_relative_seconds, x_normalized_0_1), ...] sorted by time,
    or [] when detection is unavailable / no face found anywhere.
    """
    if end <= start or window <= 0:
        return []

    try:
        import cv2
    except ImportError:
        return []

    detector = _open_detector()
    if detector is None:
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        cap.release()
        return []

    duration = end - start
    count = max(1, min(max_windows, int(duration / window) or 1))
    step = duration / count
    samples: list[tuple[float, float]] = []

    try:
        with detector:
            for i in range(count):
                t_abs = start + step * (i + 0.5)
                cap.set(cv2.CAP_PROP_POS_MSEC, t_abs * 1000)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                center = _largest_face_center(detector.process(rgb))
                if center is not None:
                    samples.append((round(t_abs - start, 3), center))
    except Exception:
        return []
    finally:
        cap.release()

    return _smooth_track(samples)


def _smooth_track(samples: list[tuple[float, float]], radius: int = 1) -> list[tuple[float, float]]:
    """Moving-average smoothing so crop pan does not jitter."""
    if len(samples) <= 2:
        return samples
    smoothed = []
    for i, (t, x) in enumerate(samples):
        lo = max(0, i - radius)
        hi = min(len(samples), i + radius + 1)
        window_values = [s[1] for s in samples[lo:hi]]
        smoothed.append((t, _clamp01(sum(window_values) / len(window_values))))
    return smoothed


def detect_face_center(
    video_path: str,
    start: float,
    end: float,
    samples: int = DEFAULT_SAMPLES,
) -> float | None:
    """Detect dominant speaker x-center (normalized 0-1) across a segment.

    Returns None when no face is found or detection is unavailable —
    callers must fall back to center crop.
    """
    if end <= start or samples < 1:
        return None

    try:
        import cv2
    except ImportError:
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        cap.release()
        return None

    detector = _open_detector()
    if detector is None:
        cap.release()
        return None

    centers: list[float] = []
    try:
        with detector:
            duration = end - start
            for i in range(samples):
                t = start + duration * (i + 0.5) / samples
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                center = _largest_face_center(detector.process(rgb))
                if center is not None:
                    centers.append(center)
    except Exception:
        return None
    finally:
        cap.release()

    if not centers:
        return None
    return _clamp01(statistics.median(centers))


def crop_x_expression(face_x: float) -> str:
    """Build FFmpeg crop x-expression centering the 9:16 window on face_x.

    face_x is normalized 0-1 across frame width; output is clamped to the
    valid crop range [0, iw-ow] via min/max (no shell involved).
    """
    f = _clamp01(face_x)
    return f"min(max({f}*iw-(ih*9/16)/2,0),iw-ih*9/16)"


def crop_track_expression(track: list[tuple[float, float]]) -> str | None:
    """Build a time-varying FFmpeg crop x-expression from a face track.

    `track` is [(t_seconds, x_normalized), ...] as returned by
    detect_face_track. Produces a piecewise-constant ladder using FFmpeg's
    `t` (current frame time), so the crop pans to follow the speaker.

    Returns None when the track is too small to be worth panning.
    """
    if not track or len(track) < 2:
        return None

    centers = [(t, _clamp01(x)) for t, x in sorted(track, key=lambda s: s[0])]
    # Reuse the static expression form for each step value.
    steps = [(t, crop_x_expression(x)) for t, x in centers]

    expr = steps[-1][1]
    for t, seg_expr in reversed(steps[:-1]):
        expr = f"if(lt(t,{t:.3f}),{seg_expr},{expr})"
    return expr
