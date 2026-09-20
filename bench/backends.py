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
    {"type": "text"}, {"type": "image", "image": data-uri|url|path},
    {"type": "video", "video": data-uri|url|path}.
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
                parts.append({"type": "video_url",
                              "video_url": {"url": _media_url(part["video"], "video/mp4")}})
            else:
                raise ValueError(f"unsupported message part type: {kind!r}")
        converted.append({**message, "content": parts})
    return converted


def _media_url(value, default_mime):
    if not isinstance(value, str):
        raise ValueError("media content must be a data URI, URL, or local path string")
    if value.startswith(("data:", "http://", "https://")):
        return value
    data = Path(value).read_bytes()
    return f"data:{default_mime};base64," + base64.b64encode(data).decode()


def chat_completion(backend, messages, *, timeout=None, attempts=5, backoff=2.0):
    """One OpenAI-compatible /chat/completions call -> (text, total_tokens)."""
    url = backend["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    key_env = backend.get("api_key_env")
    if key_env and os.environ.get(key_env):
        headers["Authorization"] = "Bearer " + os.environ[key_env]
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

    def get_response(messages):
        return chat_completion(backend, to_openai_messages(messages))

    chat_qwen.get_response = get_response
    memory_processing_qwen.get_response = get_response
    return get_response


def make_consolidation_proposer(backend):
    """proposer(packet, work) over any OpenAI-compatible endpoint."""
    if backend["type"] != "openai_compatible":
        raise ValueError("consolidation backend must be openai_compatible")

    def proposer(packet, work):
        from consolidation.llm_consolidator import propose
        return propose(packet, work, backend["model"],
                       endpoint=backend["base_url"],
                       key_env=backend.get("api_key_env") or "CONSOLIDATION_API_KEY")

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
    api_key = os.environ.get(backend.get("api_key_env") or "") or "bench-unused-key"
    chat_api.config[name] = {"base_url": backend["base_url"], "api_key": api_key}
    chat_api.client[name] = openai.OpenAI(
        api_key=api_key, base_url=backend["base_url"])
    return name
