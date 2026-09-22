from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .tracker import TrackObservation


ALIASES = {
    "person": ("person", "people", "man", "woman", "boy", "girl"),
    "bicycle": ("bicycle", "bicycles", "bike", "bikes"),
    "car": ("car", "cars", "vehicle", "vehicles"),
    "motorcycle": ("motorcycle", "motorcycles", "motorbike"),
    "bus": ("bus", "buses"),
    "backpack": ("backpack", "backpacks"),
    "handbag": ("handbag", "handbags", "purse", "purses"),
    "suitcase": ("suitcase", "suitcases", "luggage"),
    "bottle": ("bottle", "bottles", "water bottle", "water bottles"),
    "cup": ("cup", "cups", "mug", "mugs"),
    "bowl": ("bowl", "bowls"),
    "chair": ("chair", "chairs", "seat", "seats"),
    "dining table": ("table", "tables", "desk", "desks", "counter"),
    "tv": ("tv", "television", "screen", "screens"),
    "laptop": ("laptop", "laptops", "computer", "computers"),
    "cell phone": ("phone", "phones", "cellphone", "mobile"),
    "book": ("book", "books", "notebook", "notebooks"),
    "cat": ("cat", "cats"),
    "dog": ("dog", "dogs"),
}


@dataclass(frozen=True)
class ObjectEvidence:
    targets: tuple[str, ...]
    tracks: tuple[TrackObservation, ...]
    prompt_context: str

    @property
    def has_matches(self) -> bool:
        return bool(self.tracks)


class ObjectEvidencePlanner:
    """Select tracked objects that are explicitly relevant to the question."""

    def plan(
        self,
        question: str,
        tracks: tuple[TrackObservation, ...],
    ) -> ObjectEvidence:
        text = " " + re.sub(r"[^a-z0-9]+", " ", str(question).lower()) + " "
        targets = tuple(
            label
            for label, aliases in ALIASES.items()
            if any(f" {alias} " in text for alias in aliases)
        )
        if not targets:
            return ObjectEvidence((), (), "")

        matches = tuple(track for track in tracks if track.label in targets)
        if not matches:
            target_text = ", ".join(targets)
            context = (
                "The continuous detector currently has no stable track for "
                f"the requested object class ({target_text}). This is not proof "
                "that the object is absent; rely on the image."
            )
            return ObjectEvidence(targets, (), context)

        counts = Counter(track.label for track in matches)
        count_text = ", ".join(
            f"{count} {label}" for label, count in sorted(counts.items())
        )
        details = "; ".join(
            f"{track.label} #{track.track_id} at {track.position}, "
            f"confidence {track.confidence:.2f}, tracked {track.visible_s:.1f}s"
            for track in matches[:8]
        )
        context = (
            "Auxiliary continuous-detector evidence (verify against the image): "
            f"{count_text}. {details}."
        )
        return ObjectEvidence(targets, matches, context)
