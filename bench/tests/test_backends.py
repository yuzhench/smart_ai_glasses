"""Offline tests for the bench harness: no GPU, torch, or real endpoints."""
import base64
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


def test_converter_text_image_video(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "describe"},
        {"type": "image", "image": "data:image;base64,AAAA"},
        {"type": "video", "video": str(clip)},
    ]}]
    (converted,) = backends.to_openai_messages(messages)
    text, image, video = converted["content"]
    assert text == {"type": "text", "text": "describe"}
    assert image == {"type": "image_url",
                     "image_url": {"url": "data:image;base64,AAAA"}}
    expected = "data:video/mp4;base64," + base64.b64encode(b"video-bytes").decode()
    assert video == {"type": "video_url", "video_url": {"url": expected}}


def test_converter_passthrough_urls_and_strings():
    messages = [
        {"role": "system", "content": "plain"},
        {"role": "user", "content": [
            {"type": "video", "video": "https://example.com/clip.mp4"}]},
    ]
    system, user = backends.to_openai_messages(messages)
    assert system == {"role": "system", "content": "plain"}
    assert user["content"][0]["video_url"]["url"] == "https://example.com/clip.mp4"


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


def _stub_mmagent(monkeypatch):
    chat_qwen = ModuleType("mmagent.utils.chat_qwen")
    chat_qwen.get_response = lambda messages: ("pristine", 0)
    mpq = ModuleType("mmagent.memory_processing_qwen")
    mpq.get_response = chat_qwen.get_response
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
    base = {"dataset": str(manifest), "memory_backend": "qwen-vllm"}

    path2 = tmp_path / "path2.json"
    path2.write_text(json.dumps({**base, "path": 2}))
    with pytest.raises(ValueError, match="consolidation_backend"):
        config.load_run(path2)

    path2.write_text(json.dumps(
        {**base, "path": 2, "consolidation_backend": "gpt-consol"}))
    resolved = config.load_run(path2)
    assert resolved["path"] == 2
    assert resolved["period_s"] == 1200
    assert resolved["dataset"]["clips"][0]["end_s"] == 10.0
    assert resolved["dataset"]["plan"][0]["gap"] is None

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
        {"dataset": str(manifest), "path": 1, "memory_backend": "qwen-vllm"}))
    resolved = config.load_run(run, overrides={"memory_backend": "gemini-cloud"})
    assert resolved["memory_backend"]["name"] == "gemini-cloud"


def test_qa_questions_validation_and_order():
    with pytest.raises(ValueError, match="ask_at_s"):
        qa.load_questions([{"question": "x"}])
    questions = qa.load_questions([
        {"question": "later", "ask_at_s": 20},
        {"question": "sooner", "ask_at_s": 5, "answer": "a"},
    ])
    assert [q["question"] for q in questions] == ["sooner", "later"]
    assert questions[0]["id"] == "q1"  # original list position, stable ids
