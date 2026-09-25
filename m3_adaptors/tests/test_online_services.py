import m3_adaptors
m3_adaptors.apply()

import base64
import json
from types import SimpleNamespace

from mmagent import asr_cache
from mmagent.asr_cache import PreparedASRCache
from mmagent.consolidation_evidence import export_consolidation_evidence


def test_mai_request_preserves_speakers_and_timestamps(monkeypatch):
    from m3_adaptors import chat_api_ext

    class Response:
        is_error = False

        def json(self):
            return {"segments": [{"start": 4.1, "end": 8.2,
                                   "text": "好，然后一个秒表。", "speaker": 0}]}

    calls = []
    monkeypatch.setattr(chat_api_ext, "_chat_api", lambda: SimpleNamespace(config={
        "mai": {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
                "model": "microsoft/mai-transcribe-2", "api_key": "test-key"}}))
    monkeypatch.setattr(chat_api_ext.cloud_http, "post",
                        lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    segments = chat_api_ext.transcribe_audio("mai", b"wav-bytes")
    url, request = calls[0]
    assert url.endswith("/audio/transcriptions")
    assert request["json"]["model"] == "microsoft/mai-transcribe-2"
    assert request["json"]["provider"]["options"]["azure"]["diarization"]["enabled"]
    assert segments == [{"start_time": "00:04", "end_time": "00:09",
                         "asr": "好，然后一个秒表。", "speaker": 0}]


def test_prepared_asr_cache_calls_provider_once(tmp_path, monkeypatch):
    calls = []

    def run(provider, request):
        calls.append(provider)
        return (
            {
                provider: [
                    {
                        "start_time": "00:00",
                        "end_time": "00:02",
                        "asr": "hello",
                        "asr_provider": provider,
                        "asr_sources": [provider],
                    }
                ]
            },
            [],
            {provider: 12.0},
            12.0,
        )

    monkeypatch.setattr(asr_cache, "run_selected_asr", run)
    cache = PreparedASRCache(
        tmp_path / "asr",
        "test-asr",
        {"provider": "deepgram", "model": "test", "api_key": "excluded"},
        display_root=tmp_path,
    )
    audio = base64.b64encode(b"audio")
    first, first_context = cache.get(audio)
    second, second_context = cache.get(audio)
    assert len(calls) == 1
    assert first_context["cache_hit"] is False
    assert second_context["cache_hit"] is True
    assert second[0] == first[0]
    assert second[2] == {"test-asr": None}
    assert "excluded" not in next((tmp_path / "asr").glob("*.json")).name


def test_evidence_export_uses_recorded_provider_and_mapper(tmp_path):
    directory = tmp_path / "method"
    audit_dir = directory / "audits"
    audit_dir.mkdir(parents=True)
    audit = {
        "voice_observations": [
            {
                "voice_node_id": 4,
                "source_row_index": 0,
                "start_time": "00:00",
                "end_time": "00:02",
                "asr": "hello",
                "asr_provider": "custom-asr",
                "assignment_scores": {
                    "method": "TST",
                    "method_id": "ecapa",
                    "candidates": [],
                    "threshold": 0.6,
                    "created_new_identity": True,
                },
            }
        ]
    }
    (audit_dir / "clip_1_audit.json").write_text(json.dumps(audit))
    graph = SimpleNamespace(
        nodes={}, edges={}, segment_times={1: (0.0, 2.0)}, asr_provider="custom-asr"
    )
    snapshot = SimpleNamespace(
        graph=graph, cutoff_clip_id=1, cutoff_timestamp=2.0, graph_version="v1"
    )
    plan = [
        {
            "clip_id": 1,
            "start_s": 0.0,
            "end_s": 2.0,
            "gap": None,
            "source": {
                "path": "/recording.wav",
                "start_s": 0.0,
                "source_offset_s": 0.0,
            },
        }
    ]
    exported = export_consolidation_evidence(snapshot, directory, "session", plan)
    observation = exported["replay"]["observations"][0]
    assert observation["transcripts"] == {"custom-asr": "hello"}
    assert "clip_1_voices.tst.json" in observation["audio_ref"]
    assignment = json.loads(exported["assignment_jsonl"].read_text().strip())
    assert assignment["method"] == "TST"
    assert assignment["decision"] == "new_voice"


def test_evidence_export_marks_missing_source_tail_as_gap(tmp_path):
    directory = tmp_path / "method"
    audit_dir = directory / "audits"
    audit_dir.mkdir(parents=True)
    (audit_dir / "clip_1_audit.json").write_text(json.dumps({"voice_observations": []}))
    graph = SimpleNamespace(nodes={}, edges={}, segment_times={1: (0.0, 2.0)})
    snapshot = SimpleNamespace(graph=graph, cutoff_clip_id=1,
                               cutoff_timestamp=2.0, graph_version="v1")
    plan = [dict(clip_id=1, start_s=0.0, end_s=2.0, source_end_s=1.8,
                 gap=None, source=dict(path="/recording.wav", start_s=0.0,
                                       source_offset_s=0.0))]
    replay = export_consolidation_evidence(snapshot, directory, "session", plan)["replay"]
    assert [(s["absolute_start_seconds"], s["absolute_end_seconds"], s["gap"])
            for s in replay["segments"]] == [(0.0, 1.8, None), (1.8, 2.0, "source_gap")]
    assert replay["source_gaps"] == [dict(clip_id=1, reason="source_gap",
                                          start_s=1.8, end_s=2.0)]
