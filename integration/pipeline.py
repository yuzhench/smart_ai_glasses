import queue
import threading
import time

from integration.final_executor import (
    FinalExecutor,
)
from routing.high_level_router import (
    HighLevelRouter,
)
from routing.perception import (
    ObjectEvidence,
    ObjectEvidencePlanner,
    ObjectTracker,
    ObjectTrackerConfig,
)
from routing.short_term_memory import (
    RollingVisualBuffer,
)
from when.types import Route, TriggerType


class RoutingPipeline:
    def __init__(
        self,
        buffer_seconds=15.0,
        buffer_fps=2.0,
        object_tracker_config=None,
    ):
        self.router = (
            HighLevelRouter()
        )

        self.visual_buffer = (
            RollingVisualBuffer(
                window_seconds=
                    buffer_seconds,
            )
        )

        self.object_tracker = ObjectTracker(
            object_tracker_config
            or ObjectTrackerConfig.from_env()
        )
        self.evidence_planner = ObjectEvidencePlanner()

        self.executor = (
            FinalExecutor(
                visual_buffer=
                    self.visual_buffer,
                speak=True,
            )
        )

        self.buffer_interval = (
            1.0
            / float(buffer_fps)
        )

        self._last_buffer_time = (
            0.0
        )

        self._buffer_lock = (
            threading.Lock()
        )

        self.q = queue.Queue(
            maxsize=4
        )

        self.worker = (
            threading.Thread(
                target=self._worker,
                daemon=True,
            )
        )

        self.worker.start()

    def observe_frame(
        self,
        frame_rgb,
    ):
        now = time.time()

        # Tracking has its own 1-slot queue and FPS limit, so this call never
        # waits for object detection and runs before the 2 FPS memory throttle.
        self.object_tracker.submit(
            frame_rgb,
            timestamp=now,
        )

        with self._buffer_lock:

            if (
                now
                - self._last_buffer_time
                < self.buffer_interval
            ):
                return

            self._last_buffer_time = (
                now
            )

        try:
            self.visual_buffer.add_rgb(
                frame_rgb,
                timestamp=now,
            )

        except Exception as exc:
            print(
                "[15S BUFFER] skipped:",
                repr(exc),
            )

    def submit(
        self,
        event,
        frame_rgb,
    ):
        if self.object_tracker.available:
            evidence = self.evidence_planner.plan(
                event.query.text,
                self.object_tracker.snapshot(),
            )
        else:
            evidence = ObjectEvidence((), (), "")

        try:
            self.q.put_nowait(
                (
                    event,
                    None
                    if frame_rgb is None
                    else frame_rgb.copy(),
                    evidence,
                )
            )

        except queue.Full:
            print(
                "\n⚠ Routing queue full; "
                "dropping trigger."
            )

    def close(self):
        self.object_tracker.close()

    def _print_decision(
        self,
        event,
        decision,
    ):
        print()
        print("=" * 68)
        print("WHEN -> FINAL 4-WAY ROUTER")
        print("=" * 68)
        print(
            "Trigger :",
            event.trigger_type.value,
        )
        print(
            "Question:",
            event.query.text,
        )
        print(
            "Route   :",
            decision.route,
        )
        print(
            "Confidence:",
            f"{decision.confidence:.3f}",
        )

        if decision.probabilities:
            ordered = sorted(
                decision.probabilities.items(),
                key=lambda x: x[1],
                reverse=True,
            )

            print(
                "Probabilities:"
            )

            for label, value in ordered:
                print(
                    f"  {label:14s} "
                    f"{value:.3f}"
                )

        print("=" * 68)

    def _worker(self):
        while True:

            event, frame_rgb, evidence = (
                self.q.get()
            )

            try:

                if (
                    event.route
                    is not Route.TRIGGER
                ):
                    continue

                if event.trigger_type in (
                    TriggerType.STANDING,
                    TriggerType.ALERT,
                ):
                    self.executor.execute_proactive(
                        event,
                        frame_rgb,
                        object_evidence=evidence,
                    )
                    continue

                decision = (
                    self.router.route(
                        event.query.text
                    )
                )

                self._print_decision(
                    event,
                    decision,
                )

                self.executor.execute(
                    decision,
                    event,
                    frame_rgb,
                    object_evidence=evidence,
                )

            except Exception as exc:

                print()
                print(
                    "✗ Final routing error:",
                    repr(exc),
                )
                print()

            finally:
                self.q.task_done()
