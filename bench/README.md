# bench — benchmark harness

One runner, two paths, pluggable VLM/LLM backends — all selected by a single
run-config JSON. Nothing under `StreamMeCo/`, `consolidation/`, or
`m3_adaptors/` is modified to switch models or paths. (One standing
modification exists: `StreamMeCo/mmagent/voice_processing.py` uses the lazy
CAM++-only speaker chain; ERes2NetV2 was removed.)

See the [tutorial](../doc/TUTORIAL.md) for the configuration walkthrough and
the [Jake DAY1 runbook](../doc/benchmark.md) for the full comparison setup.

## Run

```bash
cd StreamMeCo-consolidation
python -m bench bench/configs/runs/jake_path1_gemini.json      # path 1: pristine online
python -m bench bench/configs/runs/jake_path2_gemini.json    # path 2: + online consolidation
python -m bench bench/configs/runs/jake_path2_gemini.json --validate   # config check only
# path 2 only: resume consolidation from a preserved pre-consolidation snapshot
# (snapshot.pkl + snapshot.json), skipping construction entirely:
python -m bench bench/configs/runs/jake_path2_gemini.json \
    --resume-from bench/results/jake_path2_pending_snapshot
```

Quick overrides without editing files:

```bash
python -m bench bench/configs/runs/jake_path2_gemini.json \
    --set memory_backend=gemini-3.8-flash --set consolidation_backend=gpt-5.6-sol
```

## The three config layers (`bench/configs/`)

**`backends.json`** — every model the harness can use, named once:

```json
{
  "qwen-local":   {"type": "local"},
  "gemini-3.8-flash": {"type": "openai_compatible", "base_url": "https://api.302.ai/v1",
                   "model": "gemini-3.8-flash", "api_key_env": "API_302_KEY"},
  "gpt-5.6-sol": {"type": "openai_compatible", "base_url": "https://api.302.ai/v1",
                   "model": "gpt-5.6-sol", "api_key_env": "API_302_KEY"}
}
```

- `"local"`: no patch — the pristine in-process VLM loader
  (`mmagent/utils/chat_qwen.py`) loads whatever checkpoint
  `StreamMeCo/configs/processing_config.json` → `ckpt` names (GPU box).
- `"openai_compatible"`: anything speaking `/chat/completions` — a cloud API,
  a proxy, or a local checkpoint served with `vllm serve`.
  API keys come from the env var named by `api_key_env`, falling back to an
  inline `"api_key"` field if the env var is unset.

**`datasets/<name>.json`** — what to stream:

```json
{"session": "egolife_jake_day1_20min",
 "clips": [{"clip_id": 71010,
            "path": "/opt/streammeco/data/jake/media/DAY1_A1_JAKE_19433000.mp4",
            "start_s": 0.0, "end_s": 30.02}, ...]}
```

or a glob form: `{"clips_glob": "...", "clip_seconds": 10}` (uniform,
gap-free clips only). With an explicit list, clip ids come from `clip_id`;
with a glob, from numeric filenames when possible, else list order.

**`runs/<name>.json`** — one file = one benchmark run:

```json
{
  "dataset": "egolife_jake_20min",
  "path": 2,
  "memory_backend": "gemini-3.8-flash",
  "consolidation_backend": "gpt-5.6-sol",
  "period_s": 1200,
  "moss": {"checkpoint": "/opt/streammeco/models/MOSS-Transcribe-Diarize",
           "revision": "704aa4a9c304e8520be88901e0d1960158ef5b15",
           "repository": "/opt/streammeco/repos/MOSS-Transcribe-Diarize"},
  "qa": {"questions": "bench/configs/datasets/egolife_jake_qa_20min.json",
         "backend": "gemini-3.8-flash", "judge_backend": "gpt-5.6-sol", "topk": 10},
  "output_dir": "bench/results/jake_path2_gemini_sol_20min"
}
```

- `path: 1` — pristine online M3+StreamMeCo: clips → `process_segment` → one
  evolving VideoGraph, per-clip pickles under `graphs/`. No consolidation.
- `path: 2` — the *same* loop plus online consolidation: evidence adaptors →
  `ConsolidationRuntime`, committed into the live graph automatically every
  `period_s` (default 1200 s = 20 min), job artifacts under
  `consolidation/`. Requires `consolidation_backend`.
- `memory_backend` — the generation VLM. Use the same backend in a path-1 and
  a path-2 config for a clean comparison.
- `moss` — required for Path 2: `{"endpoint", "media_root", "revision"}`
  (MOSS diarization server), or `{"checkpoint", "revision", "repository"}`
  (local MOSS). The window run, segment timeline, and per-utterance alignments
  enter Sol's input. Missing or invalid MOSS evidence stops consolidation.

## Online QA

Questions are scheduled by media time and fire as the stream reaches them —
after the consolidation barrier when one lands on the same clip, so path-2
answers see the consolidated graph:

```json
[{"id": "q1", "question": "...", "answer": "...", "ask_at_s": 1200.0}]
```

`answer` is optional (skips judging). Answering reuses pristine
`retrieve.answer_with_retrieval` / `verify_qa`; `qa.backend` is injected as a
`chat_api` alias, so any `openai_compatible` backend works without touching
upstream. `qa.judge_backend` overrides the judge separately. Results land in
`qa.jsonl`. Retrieval embeddings use the `text-embedding-3-large` alias from
`StreamMeCo/configs/api_config.json` as upstream intends.

## Outputs (`output_dir/`)

| File | Path 1 | Path 2 |
|---|---|---|
| `construction.jsonl` (per-clip wall time) | x | x |
| `graphs/clip_*.pkl` per-clip snapshots | x | (in `consolidation/audits/`) |
| `graph_final.pkl` | x | x |
| `consolidation.jsonl` per-period timings | | x |
| `consolidation/` evidence, audits, snapshot jobs | | x |
| `graphs/{before,after}_<n>_consolidation.{md,pkl}` + `graphs/README.md` index — automatic pre/post-consolidation graph replay | | x |
| `qa.jsonl` scheduled QA records | if `qa` set | if `qa` set |

## GPU-run notes

- Launch from the project root; the harness `chdir`s into `StreamMeCo/`
  because pristine modules read `configs/*.json` relative to the CWD.
- The writer environment needs the full StreamMeCo deps (torch, insightface,
  moviepy) plus the CAM++ speaker checkpoint and the face models —
  consolidation or not, voices/faces are always processed.
- `memory_backend: qwen-local` additionally needs the Thinker checkpoint from
  `processing_config.json`; any `openai_compatible` memory backend does not
  (the pristine lazy load never fires).
- Any `openai_compatible` memory VLM, including a locally deployed vLLM
  checkpoint, receives ordered JPEG `image_url` parts with clip-relative
  timestamps. `frame_fps` in the backend config defaults to 2; the extracted
  source frames are 5 fps. Verify that the endpoint actually processes the
  image parts (for example, via image-token usage and a known-frame probe).

## Tests

```bash
.venv/bin/python -m pytest bench/tests -q   # fully offline
```
