from __future__ import annotations

import cv2

from .tracker import TrackObservation


def draw_track_overlay(
    frame_bgr,
    tracks: tuple[TrackObservation, ...],
    labels: set[str] | None = None,
    tracker_ready: bool = True,
    tracker_latency_s: float | None = None,
):
    """Draw current boxes and detector confidence on an OpenCV frame."""

    height, width = frame_bgr.shape[:2]
    selected = [
        track
        for track in tracks
        if labels is None or track.label in labels
    ]

    for track in selected:
        x1, y1, x2, y2 = track.bbox_xyxy_norm
        left = max(0, min(width - 1, round(x1 * width)))
        top = max(0, min(height - 1, round(y1 * height)))
        right = max(0, min(width - 1, round(x2 * width)))
        bottom = max(0, min(height - 1, round(y2 * height)))

        color = (60, 220, 60)
        cv2.rectangle(
            frame_bgr,
            (left, top),
            (right, bottom),
            color,
            2,
        )

        label = (
            f"{track.label} #{track.track_id} "
            f"det score={track.confidence:.2f}"
        )
        (text_width, text_height), baseline = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            1,
        )
        text_top = max(0, top - text_height - baseline - 6)
        cv2.rectangle(
            frame_bgr,
            (left, text_top),
            (min(width - 1, left + text_width + 8), top),
            color,
            -1,
        )
        cv2.putText(
            frame_bgr,
            label,
            (left + 4, max(text_height + 1, top - baseline - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

    if not tracker_ready:
        status = "object tracker: loading"
    elif labels == {"cell phone"}:
        best = max((track.confidence for track in selected), default=None)
        score = "--" if best is None else f"{best:.2f}"
        status = f"phone tracks={len(selected)}  best det score={score}"
    else:
        status = f"object tracks={len(selected)}"

    if tracker_latency_s is not None:
        status += f"  tracker={tracker_latency_s * 1000:.0f}ms"

    (status_width, status_height), status_baseline = cv2.getTextSize(
        status,
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        1,
    )
    cv2.rectangle(
        frame_bgr,
        (8, 8),
        (min(width - 1, status_width + 20), status_height + status_baseline + 18),
        (20, 20, 20),
        -1,
    )
    cv2.putText(
        frame_bgr,
        status,
        (14, status_height + 13),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (80, 240, 80),
        1,
        cv2.LINE_AA,
    )

    return frame_bgr
