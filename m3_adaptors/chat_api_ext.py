"""Extensions that the consolidation adaptor adds to the pristine
``mmagent.utils.chat_api`` module at runtime.

Ported from the EDITED ``mmagent/utils/chat_api.py`` and re-based onto the
pristine module's plumbing: the pristine ``config`` dict and
``_get_client_or_raise`` client lookup are reached through ``sys.modules`` at
call time, and batched HTTP/embedding work goes through the injected
``cloud_http`` module. ``apply()`` installs these as attributes on the imported
pristine module (``chat_api.get_embeddings_batch = ...`` etc.).
"""
import base64
import logging
import math
import os
import sys
from time import sleep

import cloud_http

logger = logging.getLogger(__name__)


def _chat_api():
    return sys.modules['mmagent.utils.chat_api']


def get_embeddings_batch(model, texts, timeout=120, metrics=None):
    chat_api = _chat_api()
    return cloud_http.embed_batch(chat_api._get_client_or_raise(model), chat_api.config[model].get('model',model),
        texts, timeout=timeout, attempts=int(os.environ.get('EGOLIFE_EMBEDDING_MAX_ATTEMPTS','2')),metrics=metrics)


def _api_key(model):
    model_config = _chat_api().config[model]
    api_key = os.environ.get(model_config.get("api_key_env", "")) or model_config.get("api_key")
    if not api_key:
        raise ValueError(
            f"Missing API key for '{model}'. Set {model_config.get('api_key_env')} "
            "or configure api_key in configs/api_config.json."
        )
    return api_key


def _audio_content_type(audio_format):
    return {
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
    }.get(audio_format, f"audio/{audio_format}")


def _timestamp(seconds, round_up=False):
    seconds = max(0, float(seconds or 0))
    total_seconds = math.ceil(seconds) if round_up else math.floor(seconds)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _segments_from_words(words):
    segments = []
    current = None
    for word in words or []:
        text = word.get("punctuated_word") or word.get("word") or word.get("text") or ""
        text = text.strip()
        if not text:
            continue
        speaker = word.get("speaker")
        start = float(word.get("start", 0))
        end = float(word.get("end", start))
        if current is None or (speaker is not None and speaker != current["speaker"]):
            if current is not None:
                segments.append(current)
            current = {"speaker": speaker, "start": start, "end": end, "words": [text]}
        else:
            current["end"] = end
            current["words"].append(text)
    if current is not None:
        segments.append(current)
    return [
        {
            "start_time": _timestamp(segment["start"]),
            "end_time": _timestamp(segment["end"], round_up=True),
            "asr": " ".join(segment["words"]),
            "speaker": segment["speaker"],
        }
        for segment in segments
    ]


def _normalize_transcription(response):
    utterances = response.get("results", {}).get("utterances", [])
    if utterances:
        return [
            {
                "start_time": _timestamp(item.get("start")),
                "end_time": _timestamp(item.get("end"), round_up=True),
                "asr": (item.get("transcript") or item.get("text") or "").strip(),
                "speaker": item.get("speaker"),
            }
            for item in utterances
            if (item.get("transcript") or item.get("text") or "").strip()
        ]

    segments = response.get("segments", [])
    if segments:
        return [
            {
                "start_time": _timestamp(item.get("start")),
                "end_time": _timestamp(item.get("end"), round_up=True),
                "asr": (item.get("text") or item.get("transcript") or "").strip(),
                "speaker": item.get("speaker"),
            }
            for item in segments
            if (item.get("text") or item.get("transcript") or "").strip()
        ]

    words = response.get("words", [])
    if not words:
        channels = response.get("results", {}).get("channels", [])
        if channels:
            alternatives = channels[0].get("alternatives", [])
            if alternatives:
                words = alternatives[0].get("words", [])
    word_segments = _segments_from_words(words)
    if word_segments:
        return word_segments

    text = (response.get("text") or "").strip()
    if text:
        duration = response.get("duration") or response.get("usage", {}).get("seconds") or 0
        return [{"start_time": "00:00", "end_time": _timestamp(duration, round_up=True), "asr": text}]
    return []


def transcribe_audio(model, audio_data, audio_format="wav", timeout=180):
    """Return timestamped diarized segments from a configured ASR provider."""
    config = _chat_api().config
    model_config = config[model]
    provider = model_config.get("provider")
    base_url = model_config["base_url"].rstrip("/")

    if provider == "deepgram":
        params = {
            "model": model_config.get("model", "nova-3"),
            "smart_format": "true",
            "utterances": "true",
            "language": model_config.get("language", "multi"),
            "diarize_model": model_config.get("diarize_model", "latest"),
        }
        response = cloud_http.post(
            f"{base_url}/v1/listen",
            params=params,
            headers={
                "Authorization": f"Token {_api_key(model)}",
                "Content-Type": _audio_content_type(audio_format),
            },
            content=audio_data,
            timeout=timeout,
        )
    elif provider == "openrouter":
        payload = {
            "model": model_config.get("model", "microsoft/mai-transcribe-2"),
            "input_audio": {"data": base64.b64encode(audio_data).decode("ascii"), "format": audio_format},
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
            "provider": {
                "only": [model_config.get("upstream_provider", "azure")],
                "options": {"azure": {"diarization": {"enabled": True}}},
            },
        }
        response = cloud_http.post(
            f"{base_url}/audio/transcriptions",
            headers={
                "Authorization": f"Bearer {_api_key(model)}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/Celina-love-sweet/StreamMeCo",
                "X-Title": "StreamMeCo",
            },
            json=payload,
            timeout=timeout,
        )
    else:
        raise ValueError(f"Unsupported transcription provider for '{model}': {provider}")

    if response.is_error:
        from mmagent.utils.asr_resilience import ASRHTTPError
        raise ASRHTTPError(provider, response.status_code, response.text, response.headers.get("Retry-After"))
    return _normalize_transcription(response.json())


def transcribe_audio_with_retry(model, audio_data, audio_format="wav", timeout=180, context=None):
    from mmagent.utils.asr_resilience import transcribe_resilient
    config = _chat_api().config
    root = os.environ.get("EGOLIFE_RESULTS")
    return transcribe_resilient(
        model, audio_data, audio_format, config[model],
        lambda: transcribe_audio(model, audio_data, audio_format=audio_format, timeout=timeout),
        retries=int(os.environ.get("EGOLIFE_ASR_MAX_ATTEMPTS", "2")) if root else config[model].get("retries", 2), root=root, context=context,
    )


def get_embedding(model, text, timeout=15):
    """Single-text embedding; sends the configured upstream model id, not the
    config key (ported from the edited chat_api)."""
    chat_api = _chat_api()
    model_client = chat_api._get_client_or_raise(model)
    request_model = chat_api.config[model].get("model", model)
    response = model_client.embeddings.create(
        input=text, model=request_model, timeout=timeout
    )
    return response.data[0].embedding, response.usage.total_tokens


def get_embedding_with_retry(model, text, timeout=15, attempts=5, backoff=2.0, max_sleep=20.0):
    """General retry: exponential backoff (``backoff ** i`` seconds, capped at
    ``max_sleep``), raising RuntimeError after ``attempts`` tries."""
    last_exception = None
    for i in range(attempts):
        try:
            return get_embedding(model, text, timeout)
        except Exception as exc:
            last_exception = exc
            logger.warning("Embedding attempt %d/%d failed: %s", i + 1, attempts, exc)
            if i + 1 < attempts:
                sleep(min(backoff ** (i + 1), max_sleep))
    raise RuntimeError(
        f"Failed to get embedding after {attempts} retries for model '{model}'. "
        f"Last error: {type(last_exception).__name__}: {last_exception}"
    ) from last_exception


def apply(module=None):
    """Install the extension functions on the pristine chat_api module."""
    module = module if module is not None else _chat_api()
    module.get_embedding = get_embedding
    module.get_embedding_with_retry = get_embedding_with_retry
    module.get_embeddings_batch = get_embeddings_batch
    module.transcribe_audio = transcribe_audio
    module.transcribe_audio_with_retry = transcribe_audio_with_retry
    module._normalize_transcription = _normalize_transcription
