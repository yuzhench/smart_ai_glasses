"""Offline checks for the benchmark's shared one-shot QA path."""
import json
import sys
from types import ModuleType

import pytest

from bench import qa
from m3_adaptors.one_shot_retrieval import answer_with_retrieval


@pytest.mark.parametrize("path", [1, 2])
def test_one_search_one_answer_without_controller(monkeypatch, path):
    calls = []
    mmagent = ModuleType("mmagent")
    mmagent.__path__ = []
    retrieve = ModuleType("mmagent.retrieve")

    if path == 1:
        def search(graph, query, current_clips, *, topk):
            calls.append(("search", graph, query, topk))
            return {"CLIP_7": ["Jake opens the door"]}, [7], {7: 0.9}
    else:
        def search(graph, query, current_clips, *, topk, metrics=None):
            calls.append(("search", graph, query, topk))
            metrics.update({"returned_node_ids": [31], "graph_version": "v2"})
            return {"CLIP_7": ["Jake opens the door"]}, [7], {7: 0.9}

    def answer(model, messages):
        calls.append(("answer", model, messages))
        return "B. He opens the door", 1

    retrieve.search = search
    retrieve.generate_messages = lambda inputs: inputs
    retrieve.get_response_with_retry = answer
    retrieve.generate_action = lambda *args, **kwargs: pytest.fail("controller called")
    mmagent.retrieve = retrieve
    monkeypatch.setitem(sys.modules, "mmagent", mmagent)
    monkeypatch.setitem(sys.modules, "mmagent.retrieve", retrieve)

    graph = object()
    prediction, trace = answer_with_retrieval(
        graph, "Which action?\nA. Sits\nB. Opens door", topk=10, model="qa")

    assert [call[0] for call in calls] == ["search", "answer"]
    assert calls[0][1] is graph
    assert calls[0][3] == 10
    assert "Jake opens the door" in calls[1][2][0]["content"]
    assert prediction == "B. He opens the door"
    assert trace["retrieved_clip_ids"] == [7]
    assert trace["retrieved_memories"]["CLIP_7"] == ["Jake opens the door"]
    assert trace["clip_scores"] == {"7": 0.9}
    assert ("search_metrics" in trace) == (path == 2)


def test_online_qa_persists_retrieval_and_skips_judge_without_answer(
        monkeypatch, tmp_path):
    monkeypatch.setattr(qa, "register_chat_alias", lambda backend: "qa-model")
    from m3_adaptors import one_shot_retrieval
    calls = []

    def answer(graph, question, *, topk, model):
        calls.append((graph, question, topk, model))
        return "A", {"retrieved_clip_ids": [3], "retrieved_memories": {"CLIP_3": ["fact"]}}

    monkeypatch.setattr(one_shot_retrieval, "answer_with_retrieval", answer)
    graph = object()
    log = tmp_path / "qa.jsonl"
    runner = qa.OnlineQA({
        "questions": [{"id": "q", "question": "What?", "ask_at_s": 3}],
        "topk": 5,
        "backend": {"name": "qa"},
        "judge_backend": {"name": "qa"},
    }, log)
    runner.fire_due(graph, 3)
    runner.finish(graph, 3)

    assert calls == [(graph, "What?", 5, "qa-model")]
    (record,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert record["prediction"] == "A"
    assert record["retrieval"]["retrieved_clip_ids"] == [3]
    assert record["verdict"] is None
    assert "late" not in record
