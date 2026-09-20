"""Online QA scheduling.

Questions carry ``ask_at_s`` (media time). The runner fires each question
after the first committed clip whose ``end_s`` reaches it — and after any
consolidation barrier at that point, so Path-2 answers see the consolidated
graph. Answering reuses pristine ``retrieve.answer_with_retrieval`` /
``verify_qa``; the backend is injected as a chat_api alias, so pristine
logic runs unchanged on any configured model.

Questions format (JSON list or inline in the run config):

    [{"id": "q1", "question": "...", "answer": "...", "ask_at_s": 1230.0}]

``answer`` is optional; without it the verdict step is skipped.
"""
import json
import time

from .backends import register_chat_alias
from .config import _abs


def load_questions(reference):
    items = reference if isinstance(reference, list) else _read(_abs(reference))
    questions = []
    for index, item in enumerate(items):
        if not item.get("question"):
            raise ValueError(f"question #{index} lacks 'question'")
        if item.get("ask_at_s") is None:
            raise ValueError(f"question #{index} lacks 'ask_at_s'")
        questions.append({
            "id": str(item.get("id", f"q{index}")),
            "question": item["question"],
            "answer": item.get("answer"),
            "ask_at_s": float(item["ask_at_s"]),
        })
    questions.sort(key=lambda q: q["ask_at_s"])
    return questions


def _read(path):
    with open(path) as handle:
        return json.load(handle)


class OnlineQA:
    """Fires scheduled questions against the live (or consolidated) graph."""

    def __init__(self, qa_config, log_path):
        self.questions = load_questions(qa_config["questions"])
        self.topk = qa_config["topk"]
        self.alias = register_chat_alias(qa_config["backend"])
        self.judge_alias = (self.alias if qa_config["judge_backend"] is qa_config["backend"]
                            else register_chat_alias(qa_config["judge_backend"]))
        self.log = open(log_path, "a")
        self.pending = list(self.questions)

    def fire_due(self, graph, now_s):
        """Answer every question scheduled at or before ``now_s``."""
        self._fire(graph, threshold_s=now_s, answered_at_s=now_s, late=False)

    def finish(self, graph, now_s):
        """Answer questions scheduled beyond the stream end, marked late."""
        self._fire(graph, threshold_s=float("inf"), answered_at_s=now_s, late=True)
        self.log.close()

    def _fire(self, graph, threshold_s, answered_at_s, late):
        from mmagent.retrieve import answer_with_retrieval, verify_qa
        due = [q for q in self.pending if q["ask_at_s"] <= threshold_s]
        self.pending = [q for q in self.pending if q["ask_at_s"] > threshold_s]
        for question in due:
            started = time.perf_counter()
            prediction, _trace = answer_with_retrieval(
                graph, question["question"], topk=self.topk, model=self.alias)
            verdict = None
            if question["answer"] is not None:
                verdict = verify_qa(question["question"], question["answer"],
                                    prediction, model=self.judge_alias)
            record = {
                "id": question["id"],
                "ask_at_s": question["ask_at_s"],
                "answered_at_s": answered_at_s,
                "question": question["question"],
                "ground_truth": question["answer"],
                "prediction": prediction,
                "verdict": verdict,
                "wall_ms": (time.perf_counter() - started) * 1000,
            }
            if late:
                record["late"] = True
            self.log.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.log.flush()
