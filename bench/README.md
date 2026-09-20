# bench — benchmark harness

One runner, two paths, pluggable VLM/LLM backends — all selected by a single
run-config JSON. Nothing under `StreamMeCo/`, `consolidation/`, or
`m3_adaptors/` is modified.

## Run

```bash
cd StreamMeCo-consolidation
python -m bench bench/configs/runs/jake_path1_qwen.json      # path 1: pristine online
python -m bench bench/configs/runs/jake_path2_gemini.json    # path 2: + online consolidation
python -m bench bench/configs/runs/jake_path2_gemini.json --validate   # config check only
```

Quick overrides without editing files:

```bash
python -m bench bench/configs/runs/jake_path2_gemini.json \
    --set memory_backend=qwen-vllm --set consolidation_backend=gpt-consol
```

## The three config layers (`bench/configs/`)

**`backends.json`** — every model the harness can use, named once:

```json
{
  "qwen-local":   {"type": "local"},
  "qwen-vllm":    {"type": "openai_compatible", "base_url": "http://gpu-box:8000/v1",
                   "model": "<model-id-as-served>", "api_key_env": "VLLM_API_KEY"},
  "gemini-cloud": {"type": "openai_compatible", "base_url": "https://.../v1",
                   "model": "gemini-2.5-pro", "api_key_env": "GEMINI_API_KEY"}
}
```

- `"local"`: no patch — the pristine in-process VLM loader
  (`mmagent/utils/chat_qwen.py`) loads whatever checkpoint
  `StreamMeCo/configs/processing_config.json` → `ckpt` names (GPU box).
- `"openai_compatible"`: anything speaking `/chat/completions` — a cloud API,
  a proxy, or a local checkpoint served with `vllm serve`.
  API keys come from the env var named by `api_key_env` (never stored here).

**`datasets/<name>.json`** — what to stream:

```json
{"session": "egolife_jake_day1",
 "clips_glob": "../benchmark/egolife_m3_jake_day1/clips/*.mp4",
 "clip_seconds": 10}
```

or an explicit list: `{"clips": [{"path": "...", "start_s": 0, "end_s": 10}, ...]}`.
Clip ids come from numeric filenames when possible, else list order.

**`runs/<name>.json`** — one file = one benchmark run:

```json
{
  "dataset": "egolife_jake",
  "path": 2,
  "memory_backend": "gemini-cloud",
  "consolidation_backend": "gemini-cloud",
  "period_s": 1200,
  "moss": false,
  "qa": {"questions": "bench/configs/datasets/egolife_jake_qa.json",
         "backend": "gemini-cloud", "topk": 10},
  "output_dir": "bench/results/jake_path2_gemini"
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
- `moss` — `false` (explicit no-MOSS run), `{"endpoint", "media_root",
  "revision"?}` (MOSS diarization server), or `{"checkpoint", "revision",
  "repository"}` (local MOSS).

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
- Cloud-VLM caveat: the endpoint must accept video as a base64 data URI in a
  `video_url` part. If a provider rejects it, serve a checkpoint with vLLM
  instead.

## Tests

```bash
.venv/bin/python -m pytest bench/tests -q   # fully offline
```
