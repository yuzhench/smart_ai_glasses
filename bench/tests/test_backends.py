"""Offline tests for the bench harness: no GPU, torch, or real endpoints."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import ModuleType

import pytest

from bench import backends, config, qa


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        self.server.captured.append(
            (self.path, body, self.headers.get("Authorization")))
        payload = json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 7},
            "model": body["model"],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.captured = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/v1", httpd
    finally:
        httpd.shutdown()


def _backend(server, **extra):
    base_url, _ = server
    return {"type": "openai_compatible", "name": "fake",
            "base_url": base_url, "model": "fake-model", **extra}


def test_converter_text_image():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "describe"},
        {"type": "image", "image": "data:image;base64,AAAA"},
    ]}]
    (converted,) = backends.to_openai_messages(messages)
    text, image = converted["content"]
    assert text == {"type": "text", "text": "describe"}
    assert image == {"type": "image_url",
                     "image_url": {"url": "data:image;base64,AAAA"}}


def test_converter_rejects_video_part():
    with pytest.raises(ValueError, match="timestamped image frames"):
        backends.to_openai_messages([{"role": "user", "content": [
            {"type": "video", "video": "data:video/mp4;base64,AAAA"}]}])


def test_memory_frames_are_timestamped_and_image_urls():
    frames = ["AAAA"] * 150
    inputs = backends._frame_context(frames, source_fps=5, frame_fps=2)
    assert len(inputs) == 1 + 2 * 60
    assert inputs[1]["content"] == "Video frame at 00:00.0:"
    assert inputs[-2]["content"] == "Video frame at 00:29.6:"
    # The converter receives the same text/image shape as generate_messages.
    messages = [{"role": "user", "content": [
        {"type": "text", "text": item["content"]} if item["type"] == "text"
        else {"type": "image", "image": "data:image/jpeg;base64," + item["content"][0]}
        for item in inputs]}]
    converted = backends.to_openai_messages(messages)
    assert sum(part["type"] == "image_url" for part in converted[0]["content"]) == 60
    assert not any(part["type"] == "video_url" for part in converted[0]["content"])


def test_converter_rejects_unknown_part():
    with pytest.raises(ValueError, match="unsupported"):
        backends.to_openai_messages(
            [{"role": "user", "content": [{"type": "audio", "audio": "x"}]}])


def test_registry_roundtrip():
    registry = config.load_backends()
    for name in registry:
        resolved = config.resolve_backend(registry, name)
        assert resolved["name"] == name
    with pytest.raises(ValueError, match="unknown backend"):
        config.resolve_backend(registry, "does-not-exist")


def test_chat_completion_request(server, monkeypatch):
    monkeypatch.setenv("BENCH_TEST_KEY", "secret")
    backend = _backend(server, api_key_env="BENCH_TEST_KEY")
    text, tokens = backends.chat_completion(
        backend, [{"role": "user", "content": "hi"}])
    assert (text, tokens) == ("ok", 7)
    path, body, authorization = server[1].captured[0]
    assert path == "/v1/chat/completions"
    assert body["model"] == "fake-model"
    assert authorization == "Bearer secret"


def test_chat_completion_inline_api_key_fallback(server, monkeypatch):
    monkeypatch.delenv("BENCH_TEST_KEY", raising=False)
    backend = _backend(server, api_key_env="BENCH_TEST_KEY", api_key="inline-secret")
    backends.chat_completion(backend, [{"role": "user", "content": "hi"}])
    assert server[1].captured[0][2] == "Bearer inline-secret"
    monkeypatch.setenv("BENCH_TEST_KEY", "env-wins")
    backends.chat_completion(backend, [{"role": "user", "content": "hi"}])
    assert server[1].captured[1][2] == "Bearer env-wins"


def _stub_mmagent(monkeypatch):
    chat_qwen = ModuleType("mmagent.utils.chat_qwen")
    chat_qwen.get_response = lambda messages: ("pristine", 0)
    mpq = ModuleType("mmagent.memory_processing_qwen")
    mpq.get_response = chat_qwen.get_response
    mpq.processing_config = {"fps": 5}
    mpq.generate_video_context = lambda frames, faces, voices, video, face_input: [
        {"type": "video_base64/mp4", "content": video},
        {"type": "text", "content": "Voice features:"},
    ]
    for name in ("mmagent", "mmagent.utils"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setitem(sys.modules, "mmagent.utils.chat_qwen", chat_qwen)
    monkeypatch.setitem(sys.modules, "mmagent.memory_processing_qwen", mpq)
    return chat_qwen, mpq


def test_apply_memory_backend_rebinds_both_modules(server, monkeypatch):
    chat_qwen, mpq = _stub_mmagent(monkeypatch)
    get_response = backends.apply_memory_backend(_backend(server))
    assert chat_qwen.get_response is get_response
    assert mpq.get_response is get_response
    assert mpq.get_response([]) == ("ok", 7)
    assert server[1].captured[0][0] == "/v1/chat/completions"
    context = mpq.generate_video_context(["AAAA"] * 5, {}, {}, "encoded-mp4")
    assert context[0]["type"] == "text"
    assert context[-1] == {"type": "text", "content": "Voice features:"}
    assert sum(item["type"] == "images/jpeg" for item in context) == 2
    backends.apply_memory_backend(_backend(server, frame_fps=1))
    context = mpq.generate_video_context(["AAAA"] * 5, {}, {}, "encoded-mp4")
    assert sum(item["type"] == "images/jpeg" for item in context) == 1


def test_apply_memory_backend_local_is_noop(monkeypatch):
    chat_qwen, mpq = _stub_mmagent(monkeypatch)
    pristine = chat_qwen.get_response
    assert backends.apply_memory_backend({"type": "local", "name": "q"}) is None
    assert chat_qwen.get_response is pristine
    assert mpq.get_response is pristine


def test_consolidation_proposer_uses_configured_endpoint(server, monkeypatch, tmp_path):
    calls = []
    import consolidation.llm_consolidator as llm_consolidator
    monkeypatch.setattr(llm_consolidator, "propose",
                        lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    proposer = backends.make_consolidation_proposer(
        _backend(server, api_key_env="BENCH_TEST_KEY"))
    proposer({"packet": True}, tmp_path)
    (args, kwargs) = calls[0]
    assert args[2] == "fake-model"
    assert kwargs["endpoint"] == server[0]
    assert kwargs["key_env"] == "BENCH_TEST_KEY"


def test_consolidation_proposer_rejects_local():
    with pytest.raises(ValueError, match="openai_compatible"):
        backends.make_consolidation_proposer({"type": "local", "name": "q"})


def test_register_chat_alias(server, monkeypatch):
    client_calls = []

    class FakeOpenAI:
        def __init__(self, api_key=None, base_url=None):
            client_calls.append((api_key, base_url))

    openai_stub = ModuleType("openai")
    openai_stub.OpenAI = FakeOpenAI
    chat_api = ModuleType("mmagent.utils.chat_api")
    chat_api.config = {}
    chat_api.client = {}
    monkeypatch.setitem(sys.modules, "openai", openai_stub)
    for name in ("mmagent", "mmagent.utils"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setitem(sys.modules, "mmagent.utils.chat_api", chat_api)
    monkeypatch.setenv("BENCH_TEST_KEY", "secret")

    alias = backends.register_chat_alias(
        _backend(server, api_key_env="BENCH_TEST_KEY"))
    assert alias == "fake-model"
    assert chat_api.config["fake-model"]["base_url"] == server[0]
    assert client_calls == [("secret", server[0])]


def test_run_config_path_validation(tmp_path):
    manifest = tmp_path / "dataset.json"
    manifest.write_text(json.dumps({"clips": [
        {"path": "/tmp/a.mp4", "start_s": 0.0, "end_s": 10.0},
    ]}))
    base = {"dataset": str(manifest), "memory_backend": "gemini-3.8-flash"}

    path2 = tmp_path / "path2.json"
    path2.write_text(json.dumps({**base, "path": 2}))
    with pytest.raises(ValueError, match="consolidation_backend"):
        config.load_run(path2)

    path2.write_text(json.dumps(
        {**base, "path": 2, "consolidation_backend": "gpt-5.6-sol"}))
    with pytest.raises(ValueError, match="requires a MOSS"):
        config.load_run(path2)
    moss = {"checkpoint": "/models/moss", "repository": "/repos/moss", "revision": "pinned"}
    path2.write_text(json.dumps(
        {**base, "path": 2, "consolidation_backend": "gpt-5.6-sol", "moss": moss}))
    resolved = config.load_run(path2)
    assert resolved["path"] == 2
    assert resolved["period_s"] == 1200
    assert resolved["dataset"]["clips"][0]["end_s"] == 10.0
    assert resolved["dataset"]["plan"][0]["gap"] is None
    assert resolved["moss"] == moss

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**base, "path": 3}))
    with pytest.raises(ValueError, match="'path'"):
        config.load_run(bad)

    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps({**base, "path": 1, "memory_backend": "nope"}))
    with pytest.raises(ValueError, match="unknown backend"):
        config.load_run(unknown)


def test_run_config_overrides(tmp_path):
    manifest = tmp_path / "dataset.json"
    manifest.write_text(json.dumps({"clips": [
        {"path": "/tmp/a.mp4", "start_s": 0.0, "end_s": 10.0},
    ]}))
    run = tmp_path / "run.json"
    run.write_text(json.dumps(
        {"dataset": str(manifest), "path": 1, "memory_backend": "gemini-3.8-flash"}))
    resolved = config.load_run(run, overrides={"memory_backend": "gpt-5.6-sol"})
    assert resolved["memory_backend"]["name"] == "gpt-5.6-sol"


def test_qa_questions_validation_and_order():
    with pytest.raises(ValueError, match="ask_at_s"):
        qa.load_questions([{"question": "x"}])
    questions = qa.load_questions([
        {"question": "later", "ask_at_s": 20},
        {"question": "sooner", "ask_at_s": 5, "answer": "a"},
    ])
    assert [q["question"] for q in questions] == ["sooner", "later"]
    assert questions[0]["id"] == "q1"  # original list position, stable ids
