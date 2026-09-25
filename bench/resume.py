"""Resume a path-2 run at a preserved pre-consolidation snapshot.

When a path-2 run crashes at the consolidation boundary, construction is
already complete and the exact pre-consolidation graph is preserved (see
``bench/results/*_pending_snapshot/``). This module re-attaches the
consolidation runtime to that graph and replays only the consolidation
barrier — consolidation, graph-replay capture, and the final close — without
re-running construction.

    python -m bench bench/configs/runs/jake_path2_gemini.json \
        --resume-from bench/results/jake_path2_pending_snapshot

The snapshot directory must contain ``snapshot.pkl`` (the VideoGraph) and
``snapshot.json`` (``graph_version``, ``cutoff_clip_id``,
``cutoff_timestamp``). Outputs land in the run config's ``output_dir`` —
i.e. the crashed run is completed in place (evidence export reads its
``consolidation/audits/``).
"""
import json
import pickle
from pathlib import Path

from .backends import apply_memory_backend
from .graphreplay import GraphReplayer
from .runner import _attach_consolidation, _dump_graph, _open_qa, _qa_graph


def resume_consolidation(config, snapshot_dir):
    snapshot_dir = Path(snapshot_dir)
    meta = json.loads((snapshot_dir / "snapshot.json").read_text())
    cutoff = float(meta["cutoff_timestamp"])
    output_dir = Path(config["output_dir"])
    if config["path"] != 2:
        raise ValueError("--resume-from only applies to path-2 runs")

    import m3_adaptors
    m3_adaptors.apply()  # reader side: consolidation never constructs
    apply_memory_backend(config["memory_backend"])

    with open(snapshot_dir / "snapshot.pkl", "rb") as handle:
        graph = pickle.load(handle)
    # The pickle was written inside segment(), before the runtime's exit
    # bookkeeping; restore the exact post-segment state.
    graph._consolidation_runtime = None
    graph.last_completed_clip_id = int(meta["cutoff_clip_id"])
    graph.last_completed_timestamp = cutoff
    graph.current_graph_version = int(meta["graph_version"])
    graph.segment_times = {
        event["clip_id"]: (event["start_s"], event["end_s"])
        for event in config["dataset"]["plan"]
        if not event["gap"]
    }

    runtime = _attach_consolidation(config, graph, output_dir)
    replayer = GraphReplayer(output_dir)
    qa = _open_qa(config, output_dir)
    try:
        pending = replayer.before(runtime.read_graph(), cutoff)
        timings = runtime.consolidate_until(cutoff)
        replayer.after(pending, runtime.read_graph())
        with open(output_dir / "consolidation.jsonl", "a") as log:
            log.write(json.dumps(timings, default=str) + "\n")
        if qa is not None:
            qa.fire_due(_qa_graph(graph, runtime), cutoff)
    finally:
        # Same tail-flush capture discipline as bench.runner.
        pending = None
        if getattr(runtime, "last_time", 0) > getattr(
                graph, "last_consolidated_timestamp", -1):
            pending = replayer.before(runtime.read_graph(), cutoff)
            pending["_pre_ts"] = getattr(graph, "last_consolidated_timestamp", None)
        runtime.close()
        if pending is not None:
            if getattr(graph, "last_consolidated_timestamp", None) != pending.get("_pre_ts"):
                replayer.after(pending, runtime.read_graph())
            else:
                replayer.discard(pending)
    if qa is not None:
        qa.finish(_qa_graph(graph, runtime), cutoff)
    _dump_graph(graph, output_dir / "graph_final.pkl")
    return graph
