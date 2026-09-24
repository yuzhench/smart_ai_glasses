from __future__ import annotations

import base64
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class LargeResult:
    answer: str
    provider: str
    model: str
    latency_s: float
    web_used: bool
    sources: list[str]


class LargeBackend:
    """
    Common interface for LARGE + web.

    The routing pipeline should call this interface instead of
    depending directly on one cloud provider.
    """

    def answer_with_web(
        self,
        question: str,
        image_path: Optional[Path] = None,
    ) -> LargeResult:
        raise NotImplementedError


class PlaceholderLargeBackend(LargeBackend):
    """
    Used when the real group LARGE backend has not been connected yet.
    """

    def answer_with_web(
        self,
        question: str,
        image_path: Optional[Path] = None,
    ) -> LargeResult:

        raise RuntimeError(
            "LARGE web backend is not configured yet. "
            "Set LARGE_PROVIDER=fake for wiring tests, "
            "or connect the future group LARGE backend."
        )


class FakeLargeBackend(LargeBackend):
    """
    No network call.

    This only verifies that:
        KNOWLEDGE
          -> LARGE backend
          -> answer
          -> executor / TTS

    wiring works before the real model arrives.
    """

    def answer_with_web(
        self,
        question: str,
        image_path: Optional[Path] = None,
    ) -> LargeResult:

        start = time.perf_counter()

        answer = (
            "[FAKE WEB LARGE] "
            f"Received knowledge question: {question}"
        )

        latency = time.perf_counter() - start

        return LargeResult(
            answer=answer,
            provider="fake",
            model="fake-large-web",
            latency_s=latency,
            web_used=False,
            sources=[],
        )


class OpenAIWebBackend(LargeBackend):
    """
    Temporary compatibility backend.

    This preserves the OpenAI web-search path that already existed
    in FinalExecutor. It is optional and is NOT the final group model.
    """

    def __init__(self):
        from openai import OpenAI

        self.client = OpenAI()

        self.model = os.getenv(
            "OPENAI_LARGE_MODEL",
            os.getenv(
                "LARGE_VLM_MODEL",
                "gpt-5.6-luna",
            ),
        )

    @staticmethod
    def _image_to_data_url(
        path: Path,
    ) -> str:

        mime, _ = mimetypes.guess_type(
            str(path)
        )

        if mime is None:
            mime = "image/jpeg"

        data = base64.b64encode(
            Path(path).read_bytes()
        ).decode("utf-8")

        return (
            f"data:{mime};base64,{data}"
        )

    def answer_with_web(
        self,
        question: str,
        image_path: Optional[Path] = None,
    ) -> LargeResult:

        content = [
            {
                "type": "input_text",
                "text": (
                    "You are answering a question from "
                    "wearable AI glasses. "
                    "Use web search when needed. "
                    "Give a short answer suitable for "
                    "speaking aloud.\n\n"
                    f"Question: {question}"
                ),
            }
        ]

        if image_path is not None:
            content.append(
                {
                    "type": "input_image",
                    "image_url":
                        self._image_to_data_url(
                            image_path
                        ),
                    "detail": "auto",
                }
            )

        start = time.perf_counter()

        response = self.client.responses.create(
            model=self.model,
            tools=[
                {
                    "type": "web_search",
                }
            ],
            input=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
            max_output_tokens=180,
        )

        latency = time.perf_counter() - start

        answer = (
            response.output_text
            or ""
        ).strip()

        return LargeResult(
            answer=answer,
            provider="openai",
            model=self.model,
            latency_s=latency,
            web_used=True,
            sources=[],
        )


def create_large_backend() -> LargeBackend:

    provider = os.getenv(
        "LARGE_PROVIDER",
        "placeholder",
    ).strip().lower()

    if provider == "placeholder":
        return PlaceholderLargeBackend()

    if provider == "fake":
        return FakeLargeBackend()

    if provider == "openai":
        return OpenAIWebBackend()

    raise ValueError(
        f"Unknown LARGE_PROVIDER: {provider}"
    )
