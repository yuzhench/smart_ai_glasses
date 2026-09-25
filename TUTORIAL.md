# bench tutorial — configuring and running the dual-path online benchmark

`bench` streams a video session clip-by-clip through the StreamMeCo/M3 memory
pipeline and optionally consolidates the memory graph online. **One run-config
JSON selects everything**: the dataset, the path, and every model involved.

- **Path 1** — pristine online memorization: clips → `process_segment` → one
  evolving VideoGraph.
- **Path 2** — the *same* loop plus online consolidation of the graph every
  `period_s` seconds, with automatic before/after graph replay captures.

Models are never hardcoded. You point the harness at any
OpenAI-compatible endpoint (cloud API, proxy, or self-hosted vLLM) or a local
checkpoint, per role. Section 4 is the model-configuration cookbook; section 5
shows how to bootstrap a dataset from nothing.

---

## 1. Roles that need a model

| Role | When used | Requirements |
| --- | --- | --- |
| Memory-construction VLM (`memory_backend`) | every clip, both paths | accepts chronological JPEG `image_url` parts with timestamps; returns text |
| Consolidation LLM (`consolidation_backend`) | path 2, every `period_s` | text-only chat; the reply body must parse as one JSON patch object |
| QA answerer (`qa.backend`) | when questions fire | text-only chat |
| QA judge (`qa.judge_backend`) | when a question has a ground-truth answer | text-only chat |
| Embeddings (`text-embedding-3-large` alias) | memory writes **and** retrieval, both paths | OpenAI-compatible `/embeddings`; **keep the alias name**, change only `base_url`/`model` |
| ASR + diarization (`asr_provider`) | every clip with audio, both paths | MAI-Transcribe-2 through `provider: openrouter` is selected; Deepgram remains an available alternative — see §4.6 |
| Speaker embedding | every clip with speech | local CAM++ checkpoint (no API) |
| Face embedding | every clip with faces | local InsightFace buffalo_l pack (no API) |

## 2. The three config layers

### 2.1 `bench/configs/backends.json` — model registry for the harness

Each entry is a named backend that run configs reference. Two types:

```json
{
  "my-local-vlm": { "type": "local" },

  "my-chat-model": {
    "type": "openai_compatible",
    "base_url": "https://api.example.com/v1",
    "model": "exact-upstream-model-id",
    "api_key_env": "MY_PROVIDER_KEY",
    "api_key": "sk-...",
    "temperature": 0.0,
    "timeout": 600,
    "qpm": 60
  }
}
```

- `type: "local"` — the in-process VLM loader; loads the checkpoint named by
  `StreamMeCo/configs/processing_config.json` → `ckpt` on first use. GPU only.
- `type: "openai_compatible"` — anything speaking `/chat/completions`.
  - `model` is the exact model id sent in the request body (on a proxy, the
    proxy-side name).
  - **Credential resolution**: the env var named by `api_key_env` wins; the
    inline `api_key` is the fallback. Prefer env vars on shared machines.
  - `temperature` / `timeout` are optional per-backend overrides.

To add a model: add one entry here, then reference its key in a run config.
Nothing else changes.

### 2.2 `StreamMeCo/configs/api_config.json` — services used by the pipeline itself

Read at import time by pristine `mmagent/utils/chat_api.py` and the
`m3_adaptors` extensions. Three kinds of entries:

```json
{
  "text-embedding-3-large": {
    "provider": "openrouter", "capability": "embeddings",
    "base_url": "https://openrouter.ai/api/v1",
    "api_key_env": "OPENROUTER_API_KEY", "api_key": "sk-or-...",
    "model": "openai/text-embedding-3-large", "qpm": 60
  },
  "some-chat-alias": {
    "provider": "302ai", "capability": "chat",
    "base_url": "https://api.302.ai/v1", "api_key": "sk-...", "qpm": 60
  },
  "openrouter-mai-transcribe-2": {
    "provider": "openrouter", "capability": "transcription",
    "base_url": "https://openrouter.ai/api/v1",
    "api_key_env": "OPENROUTER_API_KEY", "api_key": "sk-or-...",
    "model": "microsoft/mai-transcribe-2",
    "upstream_provider": "azure", "retries": 2, "qpm": 60
  }
}
```

- **Keys must be inline here.** Pristine `chat_api` builds its clients from
  the `api_key` field only — `api_key_env` is *not* read at client
  construction (it is honored by the adaptor's raw-HTTP ASR path).
- The **embeddings alias name `text-embedding-3-large` is load-bearing**:
  pristine retrieval looks it up by that exact name. Point it at any
  compatible embeddings endpoint by editing `base_url` + `model` + `api_key`.
- The ASR entry is selected by `processing_config.json` →
  `"asr_provider": "openrouter-mai-transcribe-2"`; `provider` must be `deepgram` or
  `openrouter`, and `capability` (if present) must be `transcription`.
- Chat aliases here are only needed for pristine code paths that resolve a
  model name through `chat_api.config` directly. The harness's own calls —
  memory VLM, consolidation, QA answer/judge — go through `backends.json`.

`StreamMeCo/configs/processing_config.json` also holds
`speaker_embedding_checkpoint` (CAM++; default
`models/camplus/campplus_cn_en_common.pt`, override with env
`CAMPLUS_CHECKPOINT`) and `ckpt` (local VLM; only read by `type: "local"`
backends).

### 2.3 `bench/configs/runs/<name>.json` — one file = one run

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
  "qa": {
    "questions": "bench/configs/datasets/egolife_jake_qa_20min.json",
    "backend": "gemini-3.8-flash",
    "judge_backend": "gpt-5.6-sol",
    "topk": 10
  },
  "output_dir": "bench/results/my_run"
}
```

- `dataset` — a name in `configs/datasets/` or a path to a manifest (§2.4).
- `path` — `1` (no consolidation; omit `consolidation_backend`) or `2`
  (requires `consolidation_backend`, must be `openai_compatible`).
- `period_s` — consolidation cadence in stream seconds (default 1200).
- `moss` — required for Path 2: `{"endpoint", "media_root", "revision"}` or
  `{"checkpoint", "revision", "repository"}`. A failed or missing MOSS window
  stops consolidation before Sol is called. The saved `prompt_packet.json`
  contains the full local speaker timeline and utterance alignments.
- `qa` — optional. `questions` is a JSON path or inline list; `judge_backend`
  defaults to `backend`.
- `output_dir` — relative paths resolve against the project root.

CLI overrides without editing files (repeatable, values are JSON-parsed):

```bash
python -m bench bench/configs/runs/jake_path2_gemini.json \
    --set period_s=600 --set consolidation_backend=my-chat-model
```

### 2.4 `bench/configs/datasets/<name>.json` — what to stream

Explicit clip list (recommended; supports gaps and provenance):

```json
{
  "session": "egolife_jake_day1_20min",
  "source": "free-text provenance note",
  "clips": [
    {"clip_id": 71010, "path": "/abs/path/DAY1_A1_JAKE_19433000.mp4",
     "start_s": 0.0, "end_s": 30.02}
  ]
}
```

Or a glob for uniform gap-free clips: `{"clips_glob": ".../*.mp4",
"clip_seconds": 30}` (start/end inferred from sort order).

`start_s`/`end_s` are stream-relative seconds; questions' `ask_at_s` fire when
a clip's `end_s` passes them (in path 2, after any consolidation barrier at
that point). Questions scheduled past the stream end are answered at the end
and flagged `"late": true` in `qa.jsonl`.

Question file format:

```json
[{"id": "q1", "question": "...", "answer": "B (Alice)", "ask_at_s": 244.0}]
```

`answer` is optional; omit it to skip judging.

## 3. Minimal worked example (current configuration)

The shipped configs run EgoLife DAY1 Jake from 11:09:42.08 through 11:30:00
wall clock (41 clips, including the partial first clip)
with these choices — treat them as *examples*, not defaults you must keep:

| Role | Example backend | Endpoint |
| --- | --- | --- |
| `memory_backend` (paths 1+2) | `gemini-3.8-flash` | 302.ai proxy |
| `consolidation_backend` (path 2) | `gpt-5.6-sol` | 302.ai proxy |
| QA answerer / judge | `gemini-3.8-flash` / `gpt-5.6-sol` | 302.ai proxy |
| Embeddings | `openai/text-embedding-3-large` | OpenRouter |
| ASR | `microsoft/mai-transcribe-2` | OpenRouter |

```bash
python -m bench bench/configs/runs/jake_path1_gemini.json --validate   # always validate first
python -m bench bench/configs/runs/jake_path1_gemini.json              # path 1
python -m bench bench/configs/runs/jake_path2_gemini.json              # path 2 (+ 1 consolidation at t=1200s)
```

Dataset/QA files shipped: `egolife_jake_{20min,1h,full}.json` and
`egolife_jake_qa{,_20min}.json` (13 of the 40 media-covered questions fall in
the 20-minute window). Clip paths point at the GPU box
(`/opt/streammeco/data/jake/media/`); regenerate manifests for your own paths
with §5.3.

## 4. Changing models — recipes

All recipes follow the same pattern: **(1)** add/edit one entry in
`backends.json`, **(2)** select it in a run config or with `--set`, **(3)**
`--validate`, **(4)** run. Sections 4.6–4.7 cover the two services that live
in `api_config.json` instead.

### 4.1 Memory-construction VLM (`memory_backend`)

Used for every clip in both paths. Use the *same* memory backend in your
path-1 and path-2 configs for a clean A/B comparison of consolidation.

- **A cloud/proxy chat model** (any OpenAI-compatible multimodal endpoint):

  ```json
  "my-vlm": {"type": "openai_compatible",
             "base_url": "https://api.example.com/v1",
             "model": "vendor-model-id",
             "api_key_env": "MY_KEY"}
  ```

  The harness samples decoded clip frames at `frame_fps: 2` by default,
  labels them with clip-relative timestamps, and sends each JPEG as an
  `image_url` data URI. It never sends the MP4 as `video_url`. Confirm that
  your endpoint processes multiple images by checking usage or a known-frame
  probe; accepting the request alone does not prove that it saw the images.
  Set `frame_fps` in this backend entry to change the memory VLM sample rate.
- **A checkpoint you serve yourself** (best fidelity to the upstream model):

  ```bash
  vllm serve Qwen/Qwen2.5-Omni-7B --port 8000    # on the GPU box
  ```

  Register its `/v1` endpoint as an `openai_compatible` backend and use the
  same timestamped JPEG `image_url` input. Check the served model's image
  support and multi-image limits before the online run. `type: "local"` is the
  separate in-process Qwen path and retains native video input. See the
  [vLLM multimodal server guide](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
  for the served endpoint's image behavior.

  ```json
  "qwen-vllm": {"type": "openai_compatible",
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "Qwen/Qwen2.5-Omni-7B",
                "api_key_env": "VLLM_API_KEY"}
  ```
- **The pristine in-process loader** (no server):

  ```json
  "qwen-local": {"type": "local"}
  ```

  loads `processing_config.json` → `ckpt` with `device_map="auto"` on first
  generation call. Needs the checkpoint on disk and `flash-attn` installed.

Select it: `--set memory_backend=my-vlm`.

### 4.2 Consolidation LLM (`consolidation_backend`, path 2 only)

Called once per `period_s` boundary, off the live write path. The prompt asks
for a JSON patch; **the reply must parse as a single JSON object** and finish
with `finish_reason: stop` — prefer strong instruction-following models and
leave sampling deterministic-ish (the harness sends no temperature unless the
backend entry sets one). Any `openai_compatible` entry works:

```json
"my-consolidator": {"type": "openai_compatible",
                    "base_url": "https://api.example.com/v1",
                    "model": "vendor-model-id",
                    "api_key_env": "MY_KEY"}
```

Select it: `--set consolidation_backend=my-consolidator`. Using the same
model as `memory_backend` is legal; using a stronger reasoning model for
consolidation is the intended pattern.

### 4.3 QA answerer and judge (`qa.backend`, `qa.judge_backend`)

Both must be `openai_compatible`. The answerer does multi-step retrieval over
the graph; the judge compares the prediction with ground truth. They default
to the memory backend when omitted; override either:

```json
"qa": {"questions": "...", "backend": "my-vlm",
       "judge_backend": "my-consolidator", "topk": 10}
```

### 4.4 Smoke-test an endpoint before a full run

```python
from bench.backends import chat_completion
backend = {"type": "openai_compatible", "base_url": "https://api.example.com/v1",
           "model": "vendor-model-id", "api_key": "sk-..."}
print(chat_completion(backend, [{"role": "user", "content": "Reply with: ok"}]))
```

For memory VLMs, additionally send one clip through
`bench.configs` + a 1-clip dataset manifest — `--validate` checks config
wiring, not endpoint behavior.

### 4.5 Rate limits and robustness

`qpm` in config entries is documentation-only; actual client behavior: the
harness retries 429/5xx with exponential backoff (5 attempts, ≤30 s sleep) and
fails the run on repeated `finish_reason: "length"` or non-stop finishes.
Keep provider-side rate limits in mind when picking `period_s` and clip
cadence.

### 4.6 ASR provider (`api_config.json` + `processing_config.json`)

Exactly one provider is active, named by `asr_provider`:

- **Deepgram**: `{"provider": "deepgram", "base_url": "https://api.deepgram.com",
  "model": "nova-3", "diarize_model": "latest", "api_key": "...", ...}`
- **OpenRouter transcription**: `{"provider": "openrouter",
  "base_url": "https://openrouter.ai/api/v1",
  "model": "microsoft/mai-transcribe-2", "upstream_provider": "azure",
  "api_key_env": "OPENROUTER_API_KEY", ...}`

Add a new entry under its own alias and set `"asr_provider": "<alias>"` in
`processing_config.json`.

**Both paths use `asr_provider`.** Path 2 routes through the m3 writer chain;
path 1's `voice_processing.diarize_audio` also supports OpenRouter MAI and
Deepgram. [MAI-Transcribe-2](https://openrouter.ai/microsoft/mai-transcribe-2)
uses automatic multilingual transcription with segment timing and speaker
labels. When `asr_provider` is **not set**, path 1 falls back to its original
VLM-based audio segmentation: the clip is sent to the model named by
`processing_config.json` → `"diarization_model"` (default
`"gemini-3.8-flash"`), which must be an alias in `api_config.json`; the alias
name is sent verbatim as the upstream model id, so it must equal a model id
your endpoint actually serves.

### 4.7 Embeddings (`api_config.json`)

Keep the alias key `text-embedding-3-large` (pristine retrieval looks it up
by name); change where it points:

```json
"text-embedding-3-large": {
  "provider": "<your provider>", "capability": "embeddings",
  "base_url": "https://your-endpoint/v1",
  "model": "upstream/embedding-model-id",
  "api_key": "sk-..."
}
```

The embedding dimension becomes whatever the upstream model produces — the
graph stores vectors opaquely, but never mix two embedding models within one
run/graph.

### 4.8 Which file do I edit? — cheat sheet

| I want to change… | Edit… | Then select via… |
| --- | --- | --- |
| memory VLM | `backends.json` | run config `memory_backend` |
| consolidation LLM | `backends.json` | run config `consolidation_backend` |
| QA answer / judge | `backends.json` | run config `qa.backend` / `qa.judge_backend` |
| embeddings endpoint | `api_config.json` (keep alias name) | — (automatic) |
| ASR provider (both paths) | `api_config.json` | `processing_config.json` `asr_provider` |
| diarization VLM (path 1 fallback, only when `asr_provider` unset) | `api_config.json` (alias = upstream model id) | `processing_config.json` `diarization_model` |
| speaker/face models | filesystem checkpoints | `CAMPLUS_CHECKPOINT` / `speaker_embedding_checkpoint` |
| local VLM checkpoint | `processing_config.json` `ckpt` | `memory_backend` with `type: "local"` |

## 5. Bootstrapping the Jake dataset from scratch

Starting point: nothing downloaded. Goal: the 182-clip media tree plus the
question files the manifests in §3 reference.

### 5.1 Videos — HuggingFace `lmms-lab/EgoLife`

The dataset repo ([viewer](https://huggingface.co/datasets/lmms-lab/EgoLife/viewer))
stores the egocentric recordings as **30-second MP4 clips** laid out by
subject/day, each filename carrying the DAY1 wall-clock start time
(`HHMMSSff`):

```
A1_JAKE/DAY1/DAY1_A1_JAKE_19433000.mp4      ← starts 19:43:30.00
A1_JAKE/DAY1/DAY1_A1_JAKE_19440000.mp4      ← starts 19:44:00.00
...
```

Download with the CLI (whole DAY1 of subject JAKE, ~a full day), or narrow
`--include` to a window:

```bash
pip install -U "huggingface_hub[cli]"
hf download lmms-lab/EgoLife --repo-type dataset \
    --include "A1_JAKE/DAY1/*" \
    --local-dir /data/egolife
# clips land in /data/egolife/A1_JAKE/DAY1/
```

For the shipped 20-minute benchmark window you only need the 40 clips
`DAY1_A1_JAKE_19433000.mp4` … `DAY1_A1_JAKE_20030000.mp4` (the viewer's file
list or `huggingface_hub.list_repo_files(...)` tells you exact names; the
stream has two natural gaps later in the day, so don't assume continuity past
20:04).

### 5.2 Questions — HuggingFace `kfkas/egolifeqa-imu-benchmark`

The EgoLifeQA online benchmark slice lives in
`data/v2/benchmark.csv` (500 rows; 102 for subject JAKE) in
`kfkas/egolifeqa-imu-benchmark`:

```bash
hf download kfkas/egolifeqa-imu-benchmark --repo-type dataset \
    --include "data/v2/benchmark.csv" --local-dir /data/egolifeqa
```

Relevant columns: `question_id` (`egolifeqa-a1-jake-N`), `question`,
`choices` (JSON list), `answer_index`, `query_time_s` (DAY1 wall-clock
seconds since midnight), `source_ids` (the clip ids the evidence lives in).

### 5.3 Generate the bench manifest + QA file

This script turns the downloads into `bench/configs/datasets/` files. Clip
timing comes from the filename timestamp; durations from `ffprobe`.
`ask_at_s`/`start_s` are seconds since the first clip (`t0`), so questions
align with the stream even across recording gaps.

```python
import csv, json, re, subprocess
from pathlib import Path

CLIPS_DIR = Path("/data/egolife/A1_JAKE/DAY1")          # from §5.1
BENCH_CSV = "/data/egolifeqa/data/v2/benchmark.csv"     # from §5.2
OUT = Path("bench/configs/datasets")
WINDOW_S = 1200                                          # first 20 minutes

def wall_s(name):  # DAY1_A1_JAKE_19433000.mp4 -> seconds since midnight
    ts = re.search(r"_(\d{8})\.mp4", name).group(1)
    return int(ts[:2]) * 3600 + int(ts[2:4]) * 60 + int(ts[4:6]) + int(ts[6:]) / 100

clips = sorted((wall_s(p.name), p) for p in CLIPS_DIR.glob("*.mp4"))
t0 = clips[0][0]
manifest = {"session": "egolife_jake_day1_20min",
            "source": "EgoLife DAY1_A1_JAKE via lmms-lab/EgoLife",
            "wall_clock_t0_s": t0, "clips": []}
for w, p in clips:
    if w - t0 >= WINDOW_S:
        break
    dur = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(p)]))
    manifest["clips"].append({"clip_id": int(w), "path": str(p.resolve()),
                              "start_s": round(w - t0, 2),
                              "end_s": round(w + dur - t0, 2)})

questions = []
for row in csv.DictReader(open(BENCH_CSV)):
    if not row["question_id"].startswith("egolifeqa-a1-jake-"):
        continue
    ask = float(row["query_time_s"]) - t0
    if not 0 <= ask <= WINDOW_S:
        continue
    choices = json.loads(row["choices"])
    letters = "ABCD"[: len(choices)]
    text = (row["question"].rstrip() + "\n"
            + "\n".join(f"{l}. {c}" for l, c in zip(letters, choices))
            + "\nAnswer with the option letter and its text.")
    answer = f"{letters[int(row['answer_index'])]} ({choices[int(row['answer_index'])]})"
    questions.append({"id": row["question_id"], "question": text,
                      "answer": answer, "ask_at_s": round(ask, 2)})

(OUT / "my_jake.json").write_text(json.dumps(manifest, indent=1) + "\n")
(OUT / "my_jake_qa.json").write_text(
    json.dumps(sorted(questions, key=lambda q: q["ask_at_s"]), indent=1,
               ensure_ascii=False) + "\n")
print(f"{len(manifest['clips'])} clips, {len(questions)} questions")
```

Then point a run config at them (`"dataset": "my_jake"`,
`"qa.questions": "bench/configs/datasets/my_jake_qa.json"`) and `--validate`.
Only questions whose evidence clips you actually downloaded will be answerable
— check each question's `source_ids` against your clip set if you trim the
download.

## 6. Running on the GPU box

```bash
ssh streammeco-gpu
source /opt/streammeco/secrets/runtime.env    # optional env overrides
cd /opt/streammeco/run/StreamMeCo-consolidation
export PYTHONPATH=/opt/streammeco/repos/3D-Speaker   # provides speakerlab (CAM++)

/opt/streammeco/.venv/bin/python -m bench bench/configs/runs/jake_path2_gemini.json
```

- Use the writer venv `/opt/streammeco/.venv` (Python 3.10: torch, moviepy,
  insightface, onnxruntime-gpu). `mandol-venv` is consolidation-only.
- Identity checkpoints are local files, wired via symlinks:
  `StreamMeCo/models/camplus/campplus_cn_en_common.pt` and
  `~/.insightface/models/buffalo_l` (InsightFace's default root). Speaker
  embeddings are CAM++-only; no ERes2NetV2 checkpoint exists or is needed.
- A cloud-VLM run does not need the local VLM checkpoint (`ckpt`).
- Env vars override inline keys when set: `API_302_KEY`, `OPENROUTER_API_KEY`,
  `DEEPGRAM_API_KEY` (but see §2.2 — pristine `chat_api` clients are built
  from the inline `api_key` in `api_config.json`).

## 7. Outputs (`output_dir/`) and tests

| File | Path 1 | Path 2 |
| --- | --- | --- |
| `construction.jsonl` per-clip wall time | x | x |
| `graphs/clip_*.pkl` per-clip snapshots | x | (in `consolidation/audits/`) |
| `graph_final.pkl` final live graph | x | x |
| `consolidation.jsonl` per-period timings | | x |
| `consolidation/` evidence, audits, jobs | | x |
| `graphs/{before,after}_<n>_consolidation.{md,pkl}` + `graphs/README.md` | | x |
| `qa.jsonl` answers + verdicts | x | x |

On path 2, `bench/graphreplay.py` captures the live VideoGraph right **before**
and **after** every consolidation boundary (and a final tail flush, if one
happens): a human-reviewable Markdown rendering plus the raw pickle for
programmatic replay. `graphs/README.md` indexes each pair with node/edge and
character-mapping counts so the effect of each consolidation is visible at a
glance.

```bash
python -m pytest bench/tests -q   # offline; no GPU or endpoints needed
```
