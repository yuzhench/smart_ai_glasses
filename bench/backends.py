"""Pluggable generation/consolidation backends.

Two backend types only:

* ``local`` — no patch; the pristine in-process VLM loader reads its
  checkpoint from ``StreamMeCo/configs/processing_config.json`` (``ckpt``).
* ``openai_compatible`` — any endpoint speaking ``/chat/completions``:
  cloud APIs, proxies, or a GPU checkpoint served with ``vllm serve``.

Stdlib only; no new dependencies.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

RETRY_STATUS = {429, 500, 502, 503, 504}


def to_openai_messages(messages):
    """Convert chat_qwen-style messages to OpenAI multimodal chat format.

    Input parts (see StreamMeCo/mmagent/utils/chat_qwen.py:generate_messages):
    {"type": "text"}, {"type": "image", "image": data-uri|url|path}.
    Video must be supplied as timestamped image frames by the memory backend.
    """
    converted = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            converted.append(message)
            continue
        parts = []
        for part in content:
            kind = part.get("type")
            if kind == "text":
                parts.append({"type": "text", "text": part["text"]})
            elif kind == "image":
                parts.append({"type": "image_url",
                              "image_url": {"url": _media_url(part["image"], "image/jpeg")}})
            elif kind == "video":
                raise ValueError("video parts are unsupported by this chat route; send timestamped image frames")
            else:
                raise ValueError(f"unsupported message part type: {kind!r}")
        converted.append({**message, "content": parts})
    return converted


def _frame_context(frames, source_fps, frame_fps):
    """Sample already-decoded JPEGs in clip order for image-capable chat APIs."""
    if not frames:
        raise ValueError("memory generation requires decoded video frames")
    source_fps = float(source_fps)
    frame_fps = float(frame_fps)
    if source_fps <= 0 or frame_fps <= 0 or frame_fps > source_fps:
        raise ValueError("frame_fps must be positive and no greater than source fps")
    indices = []
    sample = 0
    while True:
        index = round(sample * source_fps / frame_fps)
        if index >= len(frames):
            break
        if not indices or index != indices[-1]:
            indices.append(index)
        sample += 1
    context = [{"type": "text", "content": (
        "Chronological video frames follow. Times are relative to this clip. "
        "Use the Voice features below for speech; do not infer speech from images."
    )}]
    for index in indices:
        seconds = index / source_fps
        minutes, remainder = divmod(seconds, 60)
        context.extend((
            {"type": "text", "content": f"Video frame at {int(minutes):02d}:{remainder:04.1f}:"},
            {"type": "images/jpeg", "content": [frames[index]]},
        ))
    return context


def _media_url(value, default_mime):
    if not isinstance(value, str):
        raise ValueError("media content must be a data URI, URL, or local path string")
    if value.startswith(("data:", "http://", "https://")):
        return value
    data = Path(value).read_bytes()
    return f"data:{default_mime};base64," + base64.b64encode(data).decode()


def _api_key(backend):
    """Env var named by api_key_env wins; inline "api_key" is the fallback."""
    key_env = backend.get("api_key_env") or ""
    return os.environ.get(key_env) or backend.get("api_key") or ""


def chat_completion(backend, messages, *, timeout=None, attempts=5, backoff=2.0):
    """One OpenAI-compatible /chat/completions call -> (text, total_tokens)."""
    url = backend["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    api_key = _api_key(backend)
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    payload = {"model": backend["model"], "messages": messages}
    if backend.get("temperature") is not None:
        payload["temperature"] = backend["temperature"]
    body = json.dumps(payload).encode()
    timeout = float(timeout or backend.get("timeout", 600))
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
            choice = result["choices"][0]
            if choice.get("finish_reason") not in (None, "stop"):
                raise ValueError("incomplete response: " + str(choice.get("finish_reason")))
            usage = result.get("usage") or {}
            return choice["message"]["content"], usage.get("total_tokens")
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_STATUS or attempt == attempts:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts:
                raise
        time.sleep(min(backoff ** attempt, 30))
    raise RuntimeError("unreachable")


def apply_memory_backend(backend):
    """Route the memory-generation VLM to the configured backend.

    ``local`` leaves the pristine in-process Thinker untouched. Otherwise
    rebind ``get_response`` on both modules that hold the name:
    ``memory_processing_qwen`` imported it directly from ``chat_qwen``
    (``from .utils.chat_qwen import get_response``), so patching only
    ``chat_qwen`` would leave the old reference behind. Both modules'
    ``generate_messages`` output shares one format, so a single converter
    serves every backend. Call before the first clip; the pristine lazy
    checkpoint load then never fires.
    """
    if backend["type"] == "local":
        return None
    import mmagent.memory_processing_qwen as memory_processing_qwen
    import mmagent.utils.chat_qwen as chat_qwen

    original_context = getattr(memory_processing_qwen,
                               "_bench_native_generate_video_context", None)
    if original_context is None:
        original_context = memory_processing_qwen.generate_video_context
        memory_processing_qwen._bench_native_generate_video_context = original_context

    def generate_video_context(base64_frames, faces_list, voices_list,
                               video_path=None, faces_input="face_only"):
        context = original_context(base64_frames, faces_list, voices_list,
                                   video_path, faces_input)
        if not context or context[0].get("type") != "video_base64/mp4":
            raise ValueError("unexpected memory video context format")
        source_fps = memory_processing_qwen.processing_config.get("fps", 5)
        frame_fps = backend.get("frame_fps", 2)
        return _frame_context(base64_frames, source_fps, frame_fps) + context[1:]

    def get_response(messages):
        return chat_completion(backend, to_openai_messages(messages))

    memory_processing_qwen.generate_video_context = generate_video_context
    chat_qwen.get_response = get_response
    memory_processing_qwen.get_response = get_response
    return get_response


def make_consolidation_proposer(backend):
    """proposer(packet, work) over any OpenAI-compatible endpoint."""
    if backend["type"] != "openai_compatible":
        raise ValueError("consolidation backend must be openai_compatible")

    def proposer(packet, work):
        from consolidation.llm_consolidator import propose
        key_env = backend.get("api_key_env") or "CONSOLIDATION_API_KEY"
        if backend.get("api_key") and not os.environ.get(key_env):
            os.environ[key_env] = backend["api_key"]
        return propose(packet, work, backend["model"],
                       endpoint=backend["base_url"],
                       key_env=key_env)

    return proposer


def register_chat_alias(backend):
    """Register a bench backend as a ``chat_api`` alias.

    Pristine ``chat_api.get_response`` sends the alias name as the upstream
    model id, so the alias is registered under ``backend['model']`` itself.
    This lets pristine ``retrieve.answer_with_retrieval`` / ``verify_qa``
    run on any configured backend without touching StreamMeCo/.
    """
    if backend["type"] != "openai_compatible":
        raise ValueError("chat alias backend must be openai_compatible")
    import openai
    from mmagent.utils import chat_api

    name = backend["model"]
    api_key = _api_key(backend) or "bench-unused-key"
    chat_api.config[name] = {"base_url": backend["base_url"], "api_key": api_key}
    chat_api.client[name] = openai.OpenAI(
        api_key=api_key, base_url=backend["base_url"])
    return name
