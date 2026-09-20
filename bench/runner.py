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
  qa.jsonl             scheduled online QA records (when qa is configured)
"""
import json
import os
import pickle
import time
from pathlib import Path

from .backends import apply_memory_backend, make_consolidation_proposer


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

    with open("configs/memory_config.json") as handle:  # cwd == StreamMeCo/
        memory_config = json.load(handle)
    graph = VideoGraph(**memory_config)

    runtime = _attach_consolidation(config, graph, output_dir) if config["path"] == 2 else None
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
                base64_video, base64_frames, base64_audio = process_video_clip(clip["path"])
                if not base64_frames:
                    construction.write(json.dumps(
                        {"clip_id": clip["clip_id"], "skipped": "no_frames"}) + "\n")
                    continue
                sample["segment_end_s"] = clip["end_s"]
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
                    _dump_graph(graph, graphs_dir / f"clip_{clip['clip_id']:06d}.pkl")
                elif clip["end_s"] >= next_boundary:
                    timings = runtime.consolidate_until(clip["end_s"])
                    with open(output_dir / "consolidation.jsonl", "a") as log:
                        log.write(json.dumps(timings, default=str) + "\n")
                    next_boundary = (int(clip["end_s"] // period_s) + 1) * period_s
                if qa is not None:
                    # Path 2: QA runs after the consolidation barrier, against a
                    # read snapshot, so answers see the consolidated graph.
                    qa.fire_due(_qa_graph(graph, runtime), clip["end_s"])
        finally:
            if runtime is not None:
                runtime.close()
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

    moss = config.get("moss", False)
    kwargs = {}
    if not moss:
        kwargs["moss"] = False
    elif "endpoint" in moss:
        os.environ["MOSS_ENDPOINT"] = moss["endpoint"]
        os.environ["MOSS_MEDIA_ROOT"] = moss["media_root"]
        os.environ["MOSS_REVISION"] = moss.get("revision", "server-unspecified")
    else:
        kwargs["moss_config"] = moss  # {"checkpoint", "revision", "repository"}
    return attach_online(
        graph, evidence, work,
        proposer=make_consolidation_proposer(config["consolidation_backend"]),
        period_s=config["period_s"], **kwargs)


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
