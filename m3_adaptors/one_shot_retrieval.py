"""One-shot online QA shared by the two benchmark paths.

The benchmark supplies its current graph: Path 1's live graph or Path 2's
read snapshot after the consolidation barrier.  Search is resolved from
``mmagent.retrieve`` at call time so each path retains its own retrieval
implementation.  No action planner or iterative retrieval is involved.
"""
import inspect
import json
import time


def answer_with_retrieval(video_graph, question, *, topk=5, model):
    from mmagent import retrieve

    query = question.strip()
    if not query:
        raise ValueError("QA question must not be empty")

    metrics = {}
    search_kwargs = {"topk": topk}
    if "metrics" in inspect.signature(retrieve.search).parameters:
        search_kwargs["metrics"] = metrics

    search_started = time.perf_counter()
    memories, clip_ids, clip_scores = retrieve.search(
        video_graph, query, [], **search_kwargs)
    search_ms = (time.perf_counter() - search_started) * 1000

    prompt = (
        "Answer the question using the retrieved video memories. "
        "If it has choices, start your answer with the selected choice letter. "
        "Give one concise final answer; do not request another search.\n\n"
        f"Question:\n{question}\n\n"
        f"Retrieved video memories:\n{json.dumps(memories, ensure_ascii=False)}"
    )
    messages = retrieve.generate_messages([{"type": "text", "content": prompt}])
    answer_started = time.perf_counter()
    answer = retrieve.get_response_with_retry(model, messages)[0]
    answer_ms = (time.perf_counter() - answer_started) * 1000
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("QA answer model returned no text")

    trace = {
        "query": query,
        "retrieved_clip_ids": list(clip_ids),
        "retrieved_memories": memories,
        "clip_scores": {str(key): float(value)
                        for key, value in clip_scores.items()},
        "search_ms": search_ms,
        "answer_ms": answer_ms,
    }
    if metrics:
        trace["search_metrics"] = metrics
    return answer.strip(), trace
