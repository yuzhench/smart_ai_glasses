"""Online benchmark runner: Path 1 (pristine) or Path 2 (+ online consolidation).

Both paths share the same loop; the run config alone decides. Path 2
additionally attaches the consolidation runtime, whose patched
``process_segment`` enters ``runtime.segment(clip_id, end_s)`` around each
clip; the runner releases consolidation at each period boundary via
``consolidate_until`` and closes the runtime at the end.

Outputs under ``output_dir``:
  construction.jsonl   per-clip wall time (both paths)
  graphs/              per-clip graph pickles (path 1; path 2 writes them
                       into consolidation/audits via the adaptor instead)
  graph_final.pkl      final live graph
  consolidation.jsonl  per-period consolidation timings (path 2)
  consolidation/       evidence, audits, snapshot job directories (path 2)
  graphs/              before/after_<n>_consolidation.{md,pkl} + README index —
                       automatic replay capture around each consolidation (path 2)
  qa.jsonl             scheduled online QA records (when qa is configured)
"""
import json
import os
import pickle
import time
from pathlib import Path

from .backends import apply_memory_backend, make_consolidation_proposer
from .graphreplay import GraphReplayer


def run(config):
    output_dir = Path(config["output_dir"])
    intermediate = output_dir / "intermediate"
    graphs_dir = output_dir / "graphs"
    for directory in (intermediate, graphs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if config["path"] == 2:
        import m3_adaptors
        m3_adaptors.apply_writer()
    apply_memory_backend(config["memory_backend"])

    from m3_agent.memorization_memory_graphs import process_segment
    from mmagent.utils.video_processing import process_video_clip
    from mmagent.videograph import VideoGraph

    with open("configs/processing_config.json") as handle:
        source_fps = float(json.load(handle).get("fps", 5))
    with open("configs/memory_config.json") as handle:  # cwd == StreamMeCo/
        memory_config = json.load(handle)
    graph = VideoGraph(**memory_config)

    runtime = _attach_consolidation(config, graph, output_dir) if config["path"] == 2 else None
    replayer = GraphReplayer(output_dir) if runtime is not None else None
    qa = _open_qa(config, output_dir)

    sample = {"intermediate_outputs": str(intermediate)}
    if config["path"] == 2:
        sample["clip_audit_dir"] = str(output_dir / "consolidation" / "audits")

    period_s = config["period_s"]
    next_boundary = period_s
    last_end_s = None
    with open(output_dir / "construction.jsonl", "a") as construction:
        try:
            for clip in config["dataset"]["clips"]:
                started = time.perf_counter()
                base64_video, base64_frames, base64_audio = process_video_clip(
                    clip["path"], fps=source_fps)
                if not base64_frames:
                    construction.write(json.dumps(
                        {"clip_id": clip["clip_id"], "skipped": "no_frames"}) + "\n")
                    continue
                sample["segment_end_s"] = clip["end_s"]
                # Consolidation evidence (m3_adaptors/evidence.py) reads
                # graph.segment_times[clip_id] -> (start_s, end_s). Populate
                # BEFORE process_segment: the runtime snapshots the graph at
                # segment exit, so the clip's own entry must already exist.
                times = getattr(graph, "segment_times", None)
                if times is None:
                    times = graph.segment_times = {}
                times[clip["clip_id"]] = (clip["start_s"], clip["end_s"])
                process_segment(graph, base64_video, base64_frames, base64_audio,
                                clip["clip_id"], sample, clip["path"])
                last_end_s = clip["end_s"]
                construction.write(json.dumps({
                    "clip_id": clip["clip_id"], "path": clip["path"],
                    "start_s": clip["start_s"], "end_s": clip["end_s"],
                    "wall_ms": (time.perf_counter() - started) * 1000,
                }) + "\n")
                construction.flush()
                if runtime is None:
                    # Pristine process_segment never refreshes equivalences
                    # (streaming_process_video does it once at the end); QA
                    # retrieval needs reverse_character_mappings per clip.
                    graph.refresh_equivalences()
                    _dump_graph(graph, graphs_dir / f"clip_{clip['clip_id']:06d}.pkl")
                elif clip["end_s"] >= next_boundary:
                    pending = replayer.before(runtime.read_graph(), clip["end_s"])
                    timings = runtime.consolidate_until(clip["end_s"])
                    replayer.after(pending, runtime.read_graph())
                    with open(output_dir / "consolidation.jsonl", "a") as log:
                        log.write(json.dumps(timings, default=str) + "\n")
                    next_boundary = (int(clip["end_s"] // period_s) + 1) * period_s
                if qa is not None:
                    # Path 2: QA runs after the consolidation barrier, against a
                    # read snapshot, so answers see the consolidated graph.
                    qa.fire_due(_qa_graph(graph, runtime), clip["end_s"])
        finally:
            if runtime is not None:
                # close() flushes a final tail window; capture the pair only
                # when a tail consolidation is actually pending.
                pending = None
                if getattr(runtime, "last_time", 0) > getattr(
                        graph, "last_consolidated_timestamp", -1):
                    pending = replayer.before(runtime.read_graph(), last_end_s or 0)
                    pending["_pre_ts"] = getattr(graph, "last_consolidated_timestamp", None)
                runtime.close()
                if pending is not None:
                    if getattr(graph, "last_consolidated_timestamp", None) != pending.get("_pre_ts"):
                        replayer.after(pending, runtime.read_graph())
                    else:
                        replayer.discard(pending)
    if qa is not None:
        qa.finish(_qa_graph(graph, runtime), last_end_s)
    _dump_graph(graph, output_dir / "graph_final.pkl")
    return graph


def _qa_graph(graph, runtime):
    return runtime.read_graph() if runtime is not None else graph


def _attach_consolidation(config, graph, output_dir):
    from consolidation.port import attach_online
    from mmagent.consolidation_evidence import export_consolidation_evidence

    work = output_dir / "consolidation"
    work.mkdir(parents=True, exist_ok=True)
    dataset = config["dataset"]

    def evidence(snapshot):
        return export_consolidation_evidence(snapshot, work, dataset["session"],
                                             dataset["plan"])

    moss = config.get("moss")
    if not isinstance(moss, dict) or not moss:
        raise ValueError("path 2 requires MOSS before construction or consolidation")
    if "endpoint" in moss:
        from consolidation.moss_runner import WindowMoss
        if not Path(moss["media_root"]).is_dir():
            raise FileNotFoundError("MOSS media root: " + moss["media_root"])
        moss_runner = WindowMoss(moss["endpoint"], moss["media_root"],
                                 revision=moss["revision"])
    else:
        from consolidation.moss_local import LocalWindowMoss
        checkpoint = Path(moss["checkpoint"])
        repository = Path(moss["repository"])
        if not (checkpoint / "config.json").is_file():
            raise FileNotFoundError("MOSS checkpoint config: " + str(checkpoint / "config.json"))
        if not repository.is_dir():
            raise FileNotFoundError("MOSS repository: " + str(repository))
        moss_runner = LocalWindowMoss(checkpoint, moss["revision"], repository)
    return attach_online(
        graph, evidence, work,
        proposer=make_consolidation_proposer(config["consolidation_backend"]),
        moss=moss_runner, period_s=config["period_s"])


def _open_qa(config, output_dir):
    if not config.get("qa"):
        return None
    from .qa import OnlineQA
    return OnlineQA(config["qa"], output_dir / "qa.jsonl")


def _dump_graph(graph, path):
    temporary = str(path) + ".tmp"
    with open(temporary, "wb") as handle:
        pickle.dump(graph, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, path)
