from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass


DEFAULT_LABELS = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "bus",
    "backpack",
    "handbag",
    "suitcase",
    "bottle",
    "cup",
    "bowl",
    "chair",
    "dining table",
    "tv",
    "laptop",
    "cell phone",
    "book",
    "cat",
    "dog",
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class ObjectTrackerConfig:
    enabled: bool = True
    model: str = "yolo11n.pt"
    tracker: str = "botsort.yaml"
    device: str = "auto"
    fps: float = 5.0
    confidence: float = 0.25
    iou: float = 0.50
    image_size: int = 640
    max_staleness_s: float = 1.5
    labels: tuple[str, ...] = DEFAULT_LABELS

    @classmethod
    def from_env(cls) -> "ObjectTrackerConfig":
        labels = tuple(
            item.strip().lower()
            for item in os.getenv(
                "OBJECT_TRACKING_LABELS",
                ",".join(DEFAULT_LABELS),
            ).split(",")
            if item.strip()
        )
        return cls(
            enabled=_env_bool("OBJECT_TRACKING_ENABLED", True),
            model=os.getenv("OBJECT_TRACKING_MODEL", "yolo11n.pt"),
            tracker=os.getenv("OBJECT_TRACKING_TRACKER", "botsort.yaml"),
            device=os.getenv("OBJECT_TRACKING_DEVICE", "auto"),
            fps=float(os.getenv("OBJECT_TRACKING_FPS", "5")),
            confidence=float(os.getenv("OBJECT_TRACKING_CONFIDENCE", "0.25")),
            iou=float(os.getenv("OBJECT_TRACKING_IOU", "0.50")),
            image_size=int(os.getenv("OBJECT_TRACKING_IMAGE_SIZE", "640")),
            max_staleness_s=float(
                os.getenv("OBJECT_TRACKING_MAX_STALENESS_S", "1.5")
            ),
            labels=labels,
        )


@dataclass(frozen=True)
class TrackObservation:
    track_id: int
    label: str
    confidence: float
    bbox_xyxy_norm: tuple[float, float, float, float]
    position: str
    first_seen: float
    last_seen: float
    seen_frames: int

    @property
    def visible_s(self) -> float:
        return max(0.0, self.last_seen - self.first_seen)


class ObjectTracker:
    """Run Ultralytics + BoT-SORT without blocking the camera loop.

    The input queue has one slot. If inference falls behind, the older waiting
    frame is replaced with the newest frame. Routing and WHEN therefore keep
    running at camera speed even when object tracking is slower.
    """

    def __init__(self, config: ObjectTrackerConfig | None = None):
        self.config = config or ObjectTrackerConfig.from_env()
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model = None
        self._class_ids: list[int] | None = None
        self._tracks: dict[tuple[str, int], TrackObservation] = {}
        self._last_submit = 0.0
        self._last_latency_s: float | None = None
        self._available = False

        if self.config.enabled:
            self._thread = threading.Thread(
                target=self._run,
                name="object-tracker",
                daemon=True,
            )
            self._thread.start()
        else:
            print("[OBJECT TRACKER] disabled")

    @property
    def available(self) -> bool:
        return self._available

    @property
    def last_latency_s(self) -> float | None:
        return self._last_latency_s

    def submit(self, frame_rgb, timestamp: float | None = None) -> None:
        if not self.config.enabled or self._stop.is_set() or frame_rgb is None:
            return

        now_mono = time.monotonic()
        interval = 1.0 / max(self.config.fps, 0.1)
        if now_mono - self._last_submit < interval:
            return
        self._last_submit = now_mono

        item = (
            time.time() if timestamp is None else float(timestamp),
            frame_rgb.copy(),
        )

        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                pass

    def snapshot(self, now: float | None = None) -> tuple[TrackObservation, ...]:
        if now is None:
            now = time.time()
        with self._lock:
            active = [
                track
                for track in self._tracks.values()
                if now - track.last_seen <= self.config.max_staleness_s
            ]
        return tuple(sorted(active, key=lambda item: (item.label, item.track_id)))

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _load(self) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "ultralytics is not installed; run "
                "pip install -r when/requirements.txt"
            ) from exc

        self._model = YOLO(self.config.model)
        names = self._model.names
        if isinstance(names, list):
            names = dict(enumerate(names))
        allowed = set(self.config.labels)
        self._class_ids = [
            int(class_id)
            for class_id, label in names.items()
            if str(label).lower() in allowed
        ]
        self._available = True
        print(
            "[OBJECT TRACKER] ready: "
            f"{self.config.model} + {self.config.tracker}, "
            f"{self.config.fps:g} FPS, {len(self._class_ids)} classes"
        )

    def _run(self) -> None:
        try:
            self._load()
        except Exception as exc:
            print(f"[OBJECT TRACKER] unavailable: {type(exc).__name__}: {exc}")
            return

        while not self._stop.is_set():
            try:
                timestamp, frame_rgb = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            started = time.perf_counter()
            try:
                self._track_frame(frame_rgb, timestamp)
                self._last_latency_s = time.perf_counter() - started
            except Exception as exc:
                print(f"[OBJECT TRACKER] frame skipped: {type(exc).__name__}: {exc}")
            finally:
                self._queue.task_done()

    def _track_frame(self, frame_rgb, timestamp: float) -> None:
        # Ultralytics expects an OpenCV-style BGR ndarray.
        frame_bgr = frame_rgb[..., ::-1].copy()
        kwargs = {
            "source": frame_bgr,
            "persist": True,
            "tracker": self.config.tracker,
            "conf": self.config.confidence,
            "iou": self.config.iou,
            "imgsz": self.config.image_size,
            "verbose": False,
        }
        if self._class_ids:
            kwargs["classes"] = self._class_ids
        if self.config.device.lower() != "auto":
            kwargs["device"] = self.config.device

        result = self._model.track(**kwargs)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            self._expire(timestamp)
            return

        height, width = frame_rgb.shape[:2]
        xyxy = boxes.xyxy.cpu().tolist()
        class_ids = boxes.cls.cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        raw_ids = None if boxes.id is None else boxes.id.cpu().tolist()
        names = result.names

        updates: dict[tuple[str, int], TrackObservation] = {}
        for index, (coords, class_id, confidence) in enumerate(
            zip(xyxy, class_ids, confidences)
        ):
            label = str(names[int(class_id)]).lower()
            track_id = int(raw_ids[index]) if raw_ids is not None else -(index + 1)
            x1, y1, x2, y2 = coords
            normalized = (
                max(0.0, min(1.0, x1 / width)),
                max(0.0, min(1.0, y1 / height)),
                max(0.0, min(1.0, x2 / width)),
                max(0.0, min(1.0, y2 / height)),
            )
            key = (label, track_id)
            with self._lock:
                previous = self._tracks.get(key)
            first_seen = previous.first_seen if previous else timestamp
            seen_frames = previous.seen_frames + 1 if previous else 1
            updates[key] = TrackObservation(
                track_id=track_id,
                label=label,
                confidence=float(confidence),
                bbox_xyxy_norm=normalized,
                position=self._position(normalized),
                first_seen=first_seen,
                last_seen=timestamp,
                seen_frames=seen_frames,
            )

        with self._lock:
            self._tracks.update(updates)
        self._expire(timestamp)

    def _expire(self, now: float) -> None:
        keep_for = max(5.0, self.config.max_staleness_s * 3.0)
        with self._lock:
            self._tracks = {
                key: value
                for key, value in self._tracks.items()
                if now - value.last_seen <= keep_for
            }

    @staticmethod
    def _position(bbox: tuple[float, float, float, float]) -> str:
        x1, y1, x2, y2 = bbox
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0
        horizontal = "left" if center_x < 1 / 3 else "right" if center_x > 2 / 3 else "center"
        vertical = "top" if center_y < 1 / 3 else "bottom" if center_y > 2 / 3 else "middle"
        return f"{horizontal}-{vertical}"
