"""Run/dataset/backend configuration loading and validation.

A run config is one JSON file selecting everything:

    {
      "dataset": "egolife_jake",           // configs/datasets/<name>.json or a path
      "path": 2,                           // 1 = pristine online, 2 = + online consolidation
      "memory_backend": "gemini-cloud",    // generation VLM, key into backends.json
      "consolidation_backend": "gemini-cloud",  // path 2 only, key into backends.json
      "period_s": 1200,                    // consolidation period (default 20 min)
      "moss": false,                       // false | {"endpoint","media_root"} | local ckpt dict
      "qa": {"questions": "...", "backend": "gemini-cloud", "topk": 10},
      "output_dir": "results/my_run"
    }

All relative paths resolve against the project root (StreamMeCo-consolidation/).
"""
import glob
import json
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCH_DIR.parent
DEFAULT_BACKENDS = BENCH_DIR / "configs" / "backends.json"


def _read(path):
    with open(path) as handle:
        return json.load(handle)


def _abs(reference):
    path = Path(reference)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_backends(path=None):
    return _read(path or DEFAULT_BACKENDS)


def resolve_backend(backends, name):
    if name not in backends:
        raise ValueError(f"unknown backend {name!r}; defined: {sorted(backends)}")
    backend = dict(backends[name])
    backend["name"] = name
    kind = backend.get("type")
    if kind == "local":
        return backend
    if kind == "openai_compatible":
        for key in ("base_url", "model"):
            if not backend.get(key):
                raise ValueError(f"backend {name!r} requires {key!r}")
        return backend
    raise ValueError(f"backend {name!r} has unknown type {kind!r}")


def _clip_id(clip_path, fallback):
    try:
        return int(Path(clip_path).stem)
    except ValueError:
        return fallback


def load_dataset(reference):
    """Load a dataset manifest -> {session, source, clips, plan}.

    Manifest: either {"clips": [{"path", "start_s", "end_s", "clip_id"?}, ...]}
    or {"clips_glob": "...", "clip_seconds": 10}. Optional "session", "source".
    """
    path = Path(reference)
    if path.suffix != ".json":
        path = BENCH_DIR / "configs" / "datasets" / f"{reference}.json"
    else:
        path = _abs(reference)
    if not path.exists():
        raise ValueError(f"dataset manifest not found: {path}")
    manifest = _read(path)
    session = manifest.get("session") or path.stem
    source = manifest.get("source")
    clips = []
    if "clips" in manifest:
        for index, entry in enumerate(manifest["clips"]):
            clips.append({
                "clip_id": int(entry.get("clip_id", index)),
                "path": str(_abs(entry["path"])),
                "start_s": float(entry["start_s"]),
                "end_s": float(entry["end_s"]),
            })
    else:
        if not manifest.get("clips_glob"):
            raise ValueError(f"{path}: supply 'clips' or 'clips_glob'")
        clip_seconds = float(manifest.get("clip_seconds", 0))
        if clip_seconds <= 0:
            raise ValueError(f"{path}: 'clips_glob' requires positive 'clip_seconds'")
        pattern = str(_abs(manifest["clips_glob"]))
        for index, clip in enumerate(sorted(glob.glob(pattern))):
            clips.append({
                "clip_id": _clip_id(clip, index),
                "path": clip,
                "start_s": index * clip_seconds,
                "end_s": (index + 1) * clip_seconds,
            })
    clips.sort(key=lambda c: c["clip_id"])
    plan = [
        {"clip_id": c["clip_id"], "start_s": c["start_s"], "end_s": c["end_s"],
         "gap": None, "source": source}
        for c in clips
    ]
    return {"session": session, "source": source, "clips": clips, "plan": plan}


def load_run(run_path, backends_path=None, overrides=None):
    raw = _read(run_path)
    raw = {**raw, **(overrides or {})}
    path = int(raw.get("path", 0))
    if path not in (1, 2):
        raise ValueError("run config 'path' must be 1 (pristine) or 2 (consolidation)")
    backends = load_backends(backends_path)
    memory = resolve_backend(backends, raw.get("memory_backend", "qwen-local"))
    consolidation = None
    if path == 2:
        name = raw.get("consolidation_backend")
        if not name:
            raise ValueError("path 2 requires 'consolidation_backend'")
        consolidation = resolve_backend(backends, name)
        if consolidation["type"] != "openai_compatible":
            raise ValueError("consolidation backend must be openai_compatible")
    dataset = load_dataset(raw["dataset"])
    period_s = float(raw.get("period_s", 1200))
    if period_s <= 0:
        raise ValueError("'period_s' must be positive")
    qa = _load_qa(raw.get("qa"), backends, memory, raw.get("memory_backend"))
    return {
        "path": path,
        "memory_backend": memory,
        "consolidation_backend": consolidation,
        "dataset": dataset,
        "period_s": period_s,
        "moss": raw.get("moss", False),
        "qa": qa,
        "output_dir": str(_abs(raw.get("output_dir") or f"bench/results/{Path(run_path).stem}")),
    }


def _load_qa(qa_raw, backends, memory, memory_name):
    if not qa_raw:
        return None
    backend_name = qa_raw.get("backend")
    if not backend_name and memory["type"] == "openai_compatible":
        backend_name = memory_name
    if not backend_name:
        raise ValueError("qa requires a 'backend' when memory_backend is local")
    backend = resolve_backend(backends, backend_name)
    if backend["type"] != "openai_compatible":
        raise ValueError("qa backend must be openai_compatible")
    judge = (resolve_backend(backends, qa_raw["judge_backend"])
             if qa_raw.get("judge_backend") else backend)
    if not qa_raw.get("questions"):
        raise ValueError("qa requires 'questions' (list or JSON path)")
    return {
        "backend": backend,
        "judge_backend": judge,
        "questions": qa_raw["questions"],
        "topk": int(qa_raw.get("topk", 10)),
    }


def summarize(config):
    """Compact validation summary for --validate."""
    dataset = config["dataset"]
    clips = dataset["clips"]
    return {
        "path": config["path"],
        "memory_backend": _backend_summary(config["memory_backend"]),
        "consolidation_backend": (_backend_summary(config["consolidation_backend"])
                                  if config["consolidation_backend"] else None),
        "period_s": config["period_s"],
        "moss": config["moss"],
        "qa": ({
            "backend": _backend_summary(config["qa"]["backend"]),
            "judge_backend": _backend_summary(config["qa"]["judge_backend"]),
            "topk": config["qa"]["topk"],
            "questions": (config["qa"]["questions"]
                          if isinstance(config["qa"]["questions"], str)
                          else f"inline ({len(config['qa']['questions'])})"),
        } if config["qa"] else None),
        "dataset": {
            "session": dataset["session"],
            "clips": len(clips),
            "first_clip": clips[0] if clips else None,
            "last_clip": clips[-1] if clips else None,
        },
        "output_dir": config["output_dir"],
    }


def _backend_summary(backend):
    return {"name": backend["name"], "type": backend["type"],
            "model": backend.get("model"), "base_url": backend.get("base_url")}
