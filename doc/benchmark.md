# Jake DAY1 online benchmark runbook

This runbook is for a coding agent starting with a clean checkout, a fresh GPU machine, no EgoLife data, and no API credentials. The benchmark compares **Path 1** (native M3 online memory) with **Path 2** (the same writer plus online consolidation). Run them as separate Python processes from empty graphs. Both use the same media, question schedule, construction model, embeddings, ASR, face and voice models, and QA model. Each process writes its own graph and retrieval answers.

The final handoff is unambiguous:

| Path | Graph | Online retrieval result |
| --- | --- | --- |
| Path 1 | `<path1_output>/graph_final.pkl` | `<path1_output>/qa.jsonl` |
| Path 2 | `<path2_output>/graph_final.pkl` | `<path2_output>/qa.jsonl` |

Also deliver a paired per-question comparison and summary of answer accuracy
and QA wall time. The intended QA policy is **one retrieval over the current
graph followed by one answer-model call per question**. **Never** load Path
1's graph into Path 2, resume one path from the other, or combine their output
directories. Sharing downloaded media and checkpoint files is fine.

## What the reference run establishes

The v9 directory `bench/results/jake_path2_gemini_mai_moss_day1_first25min_consolidation_v9_20260924/` is a useful Path 2 reference, not a finished benchmark. GitHub carries only its memory-construction text, first consolidation job (`snapshot_41`), and graph replay pair; the rest of the local run remains ignored and is **not** a dependency for a new machine. It used Gemini 3.8 Flash for construction, MAI-Transcribe-2 for clip speech, CAM++ and Buffalo-L for identity, OpenRouter text embeddings, local MOSS, and GPT-6 Sol for consolidation. It constructed **51 clips**, completed the first consolidation at **1217.96 s**, then failed at the final MOSS tail with `ValueError: overlapping source segments`. Its exit status is `1`; it has no final consolidated graph or QA results. The new agent may choose different construction and consolidation backends.

The older `egolife_jake_full.json` manifest starts at 19:43:30 and covers only 182 clips. `egolife_jake_qa.json` contains only 40 questions. Neither describes the entire DAY1 comparison.

## 1. Choose and record the experiment inputs

The following revisions are a **reproducible reference**, not hardcoded model choices:

| Source | Reference revision | Observed contents |
| --- | --- | --- |
| [EgoLife media](https://huggingface.co/datasets/lmms-lab/EgoLife) | `143fb319be7aa5ae210c936bf4f0f3a86092afb0` | 828 `A1_JAKE/DAY1/*.mp4` clips, 11:09:42.08 through 22:05:30 |
| [EgoLifeQA-IMU](https://huggingface.co/datasets/kfkas/egolifeqa-imu-benchmark) | `992544af32686f2a977aa55a122dc3944264cbea` | 101 questions with source clips solely from Jake DAY1 |
| [MOSS-Transcribe-Diarize](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize) | `704aa4a9c304e8520be88901e0d1960158ef5b15` | Path 2 audio evidence checkpoint |

Record the actual revision IDs used. If a revision changes, regenerate the manifest and QA set and report their actual counts; do not silently reuse the reference counts. The two paths must use **identical** generated files and question order.

Decide the two dynamic backends before running:

- **Construction VLM:** an image-capable OpenAI-compatible endpoint (cloud or separately served), or the repo's `local` loader and a compatible local checkpoint. Keep the selected model and frame sampling the same across paths.
- **Consolidation LLM:** an OpenAI-compatible text model selected for Path 2. It must reliably return a complete JSON patch.
- **QA answerer:** preferably the construction model, held constant across paths. QA needs an OpenAI-compatible backend; if construction uses the in-process `local` loader, expose that model through a compatible chat endpoint or select a separate QA backend for **both** paths.
- **Scoring:** compare the answer model's multiple-choice letter with the held-out answer key after both runs. Do not make a judge-model call in the measured online QA path.

Keep the existing MAI, OpenRouter embedding, CAM++, and Buffalo-L roles unless
equivalent working services or checkpoints are deliberately configured. **Text
embeddings are mandatory for both paths, and MOSS transcription/diarization is
mandatory for every Path 2 consolidation window, including the final tail.**
Supply a working local MOSS checkpoint plus helpers or a versioned MOSS
endpoint; an unavailable MOSS service blocks Path 2. Do not bypass its window
or substitute MAI clip ASR for its evidence. Do not mix different text
embedding spaces within one graph.

## 2. Provision the GPU and all dependencies

Use a disposable runtime copy of the checkout outside Git. This allows the pristine embedding client to receive an inline key in its runtime config without modifying a tracked file. Paths below are examples; choose equivalent paths if the machine differs.

```bash
export SOURCE_REPO="$(pwd -P)"          # run from the clean checkout root
export RUN_ROOT=/opt/streammeco/run/day1
export DATA_ROOT=/opt/streammeco/data
export MODEL_ROOT=/opt/streammeco/models
export REPO_ROOT=/opt/streammeco/repos
sudo apt-get update
sudo apt-get install -y build-essential ffmpeg git git-lfs rsync \
  libgl1 libglib2.0-0 libsndfile1 python3.10 python3.10-dev python3.10-venv
sudo mkdir -p "$RUN_ROOT" "$DATA_ROOT" "$MODEL_ROOT" "$REPO_ROOT" /opt/streammeco/secrets
sudo chown -R "$(id -u):$(id -g)" /opt/streammeco
rsync -a --exclude=.git --exclude=.venv --exclude=__pycache__ \
  --exclude=bench/results/ "$SOURCE_REPO/" "$RUN_ROOT/"
python3.10 -m venv /opt/streammeco/.venv
source /opt/streammeco/.venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

Install the **validated CUDA/Python matrix in [GPU_DEPLOYMENT.md](GPU_DEPLOYMENT.md) §2**. Its baseline is Torch 2.6.0 CUDA 12.4 wheels, matching torchvision/torchaudio, Transformers, InsightFace, ONNX Runtime GPU, OpenAI/httpx, PyAV/MoviePy/OpenCV, NumPy/SciPy/scikit-learn, audio libraries, and `consolidation/requirements-native.txt`. A compact reference command is:

```bash
python -m pip install torch==2.6.0+cu124 torchvision==0.21.0 \
  torchaudio==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124
python -m pip install \
  accelerate==1.15.0 albumentations==2.0.8 av==17.1.0 easydict==1.13 \
  hdbscan==0.8.44 httpx==0.28.1 insightface==0.7.3 matplotlib==3.10.9 \
  moviepy==2.2.1 numpy==1.26.4 onnx==1.17.0 onnxruntime-gpu==1.21.1 \
  openai==1.109.1 opencv-python-headless==4.11.0.86 pillow==11.3.0 \
  pydub==0.25.1 qwen-vl-utils==0.0.14 requests==2.32.3 \
  scikit-image==0.25.2 scikit-learn==1.6.1 scipy==1.14.1 \
  soundfile==0.13.1 transformers==5.17.0 tqdm==4.67.1
python -m pip install -r "$RUN_ROOT/consolidation/requirements-native.txt"
python -m pip check
```

This is the deployment guide's reference matrix, not a demand to use these
versions on incompatible hardware. A successful import and smoke run are the
final dependency gates. Avoid blindly installing `StreamMeCo/requirements.txt`
afterward because its broad/older pins can replace the validated stack. If the
GPU or chosen local VLM needs a different stack, resolve and record it
**before** either benchmark run.

| Dependency | Why it is needed | How it is selected |
| --- | --- | --- |
| `ffmpeg`/`ffprobe`, PyAV, MoviePy, OpenCV, Pillow | Decode clip video, audio, and frames; measure durations | OS and Python environment |
| Torch/CUDA, Transformers | Writer models and local MOSS | Python environment; optional local construction VLM |
| InsightFace, ONNX Runtime GPU, Buffalo-L ONNX pack | Face detection and recognition | InsightFace model cache, normally `~/.insightface/models/buffalo_l` |
| 3D-Speaker code and CAM++ `.pt` | 192-D speaker embeddings | `CAMPLUS_CHECKPOINT` or `speaker_embedding_checkpoint` |
| OpenRouter MAI-Transcribe-2 | Timestamped clip speech | `processing_config.json` → `asr_provider` → `api_config.json` |
| OpenRouter `text-embedding-3-large` | 3072-D memory and query embeddings | Load-bearing `text-embedding-3-large` alias in `api_config.json` |
| **MOSS-Transcribe-Diarize (required for Path 2)** | Audio evidence for every consolidation window and final tail | Run config `moss` object; local checkpoint and helpers, or a working versioned MOSS server |
| Construction, QA, consolidation chat endpoints | Multimodal construction, one-shot retrieval answering, JSON consolidation | `bench/configs/backends.json` or `--backends` |

For the v9-like local identity and MOSS chain, use the 3D-Speaker commit from `GPU_DEPLOYMENT.md` (`065629c313eaf1a01c65c640c46d77e61e9607b4`), the official ModelScope CAM++ model `iic/speech_campplus_sv_zh_en_16k-common_advanced` revision `v1.0.0`, and the MOSS checkpoint revision above. Install the [MOSS helper repository](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize) into the writer venv; pin and record its Git commit. For example:

```bash
git clone https://github.com/modelscope/3D-Speaker.git "$REPO_ROOT/3D-Speaker"
git -C "$REPO_ROOT/3D-Speaker" checkout 065629c313eaf1a01c65c640c46d77e61e9607b4
git clone https://github.com/OpenMOSS/MOSS-Transcribe-Diarize.git "$REPO_ROOT/MOSS-Transcribe-Diarize"
git -C "$REPO_ROOT/MOSS-Transcribe-Diarize" rev-parse HEAD   # record and pin this commit
python -m pip install -e "$REPO_ROOT/MOSS-Transcribe-Diarize"
```

Fetch CAM++ with ModelScope's `snapshot_download`, then set `CAMPLUS_CHECKPOINT` to its `campplus_cn_en_common.pt`. Verify SHA-256 `92f29b94e6948786a26778c9e302525d185bb08c8b9f5252ed98776902840199`. Trigger `FaceAnalysis(name="buffalo_l")` once before the run and verify `det_10g.onnx`, `w600k_r50.onnx`, `1k3d68.onnx`, `2d106det.onnx`, and `genderage.onnx` in its cache. The local MOSS config needs both checkpoint and helper repository paths. An external MOSS server is also supported by the run-config interface; it must actually run the MOSS model and return valid diarized window segments.

Download tools can live in a separate setup venv so they cannot disturb the
benchmark matrix. Set the revision variables to the selected IDs in §1. The
essential download sequence is:

```bash
python3.10 -m venv /opt/streammeco/setup-venv
source /opt/streammeco/setup-venv/bin/activate
python -m pip install --upgrade pip 'huggingface_hub[cli]' modelscope
hf download lmms-lab/EgoLife --repo-type dataset --revision "$EGO_REV" \
  --include 'A1_JAKE/DAY1/*.mp4' --local-dir "$DATA_ROOT/egolife"
hf download kfkas/egolifeqa-imu-benchmark --repo-type dataset \
  --revision "$QA_REV" --include 'data/v2/benchmark.csv' \
  --local-dir "$DATA_ROOT/egolifeqa"
hf download OpenMOSS-Team/MOSS-Transcribe-Diarize \
  --revision "$MOSS_REV" --local-dir "$MODEL_ROOT/MOSS-Transcribe-Diarize"
python - <<'PY'
import os
from modelscope.hub.snapshot_download import snapshot_download
snapshot_download('iic/speech_campplus_sv_zh_en_16k-common_advanced',
                  revision='v1.0.0',
                  local_dir=os.path.join(os.environ['MODEL_ROOT'], 'camplus'))
PY
export CAMPLUS_CHECKPOINT="$(find "$MODEL_ROOT/camplus" -name campplus_cn_en_common.pt -print -quit)"
test -f "$CAMPLUS_CHECKPOINT"
printf '%s  %s\n' 92f29b94e6948786a26778c9e302525d185bb08c8b9f5252ed98776902840199 \
  "$CAMPLUS_CHECKPOINT" | sha256sum -c -
source /opt/streammeco/.venv/bin/activate
python - <<'PY'
from pathlib import Path
from insightface.app import FaceAnalysis
FaceAnalysis(name='buffalo_l')
root = Path.home() / '.insightface/models/buffalo_l'
for name in ('det_10g.onnx', 'w600k_r50.onnx', '1k3d68.onnx',
             '2d106det.onnx', 'genderage.onnx'):
    assert (root / name).is_file(), root / name
print('Buffalo-L ready')
PY
```

The table in §1 gives verified reference values for `EGO_REV`, `QA_REV`, and
`MOSS_REV`. The reference videos total roughly 13.25 GB; allow extra disk for
two independent outputs and intermediates. `GPU_DEPLOYMENT.md` mentions
`gpu_setup/download_models.py`, but that script is absent from this checkout;
do not invoke it. A remote/served construction VLM needs no checkpoint in the
writer process. The in-process `local` backend additionally needs a compatible
checkpoint at `processing_config.json` → `ckpt` and `flash-attn`.

## 3. Build and validate full-day media and QA files

Reuse the dataset and QA JSON interfaces in [TUTORIAL.md](TUTORIAL.md) §2.4 and §5.3. Write the generated files into the disposable runtime, then point **both** run configs to those same absolute paths. The agent may write a short generator or adapt the tutorial's example; the output contract matters more than a particular script.

1. Enumerate every downloaded `A1_JAKE/DAY1/DAY1_A1_JAKE_HHMMSSff.mp4`, sort by timestamp, and use the first timestamp (11:09:42.08 in the reference revision) as stream `t0`. Keep explicit `clip_id`, absolute `path`, `start_s`, and `end_s` for each clip. Use `ffprobe` for media duration. Preserve large wall-clock gaps.
2. Prevent MOSS source overlap: for each clip, cap committed `end_s` at the **next clip's start** if the MP4 duration runs beyond it. Never stretch a clip over a long recording gap. Preserve the first reference clip's 17.92-second committed slot with `source_end_s: 17.63`, documenting its 0.29-second missing-media tail. For other short media ends, leave the gap unobserved.
3. Read `data/v2/benchmark.csv`. Keep rows only when **all** `source_ids` belong to Jake DAY1 and are present among downloaded clip stems. Parse `query_time_raw=DAY1@HHMMSSff` into `ask_at_s = query wall time - t0`; the reference CSV's `query_time_s` is empty for these rows. Build each multiple-choice question from `question` and `choices`, following the tutorial's question JSON shape. Write a **runtime QA file without `answer` fields** and a separate answer-key file mapping each question ID to the letter from `answer_index`. The current `OnlineQA` calls `verify_qa` only when an `answer` field is present, so the runtime file avoids a judge call without changing the config interface.
4. Validate every MP4 exists and decodes, clip IDs are unique, `start_s < end_s`, no committed clip overlaps the next, source ends lie within their committed slots, all QA source IDs exist, question IDs are unique, and every `ask_at_s` lies within the stream. With the reference revisions, expect **828 clips and 101 questions**. A changed count requires a documented source revision or filtering explanation before proceeding.

The v9 tail failed because its final ten adjacent clip pairs overlapped by 0.02–0.04 seconds. Do not reuse that manifest unchanged.

**Long-gap gate:** the full DAY1 source has a gap of about **10,351 seconds** between `DAY1_A1_JAKE_14183000.mp4` and `DAY1_A1_JAKE_17110116.mp4`. The current runner consolidates when the next actual clip crosses a period boundary, and the local MOSS window builder can include leading gap silence. [MOSS documents single-pass audio up to 90 minutes](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize). Before the full Path 2 run, inspect the maximum prospective MOSS window. If it exceeds the chosen runner's proven limit, adapt and test gap handling: omit unobserved leading silence from MOSS inference while keeping session-relative timestamps, no invented speech, and the full committed graph timeline. A 1-clip smoke test alone does not exercise this gap. Do not claim a completed full-day comparison until the gap is handled and Path 2's final tail succeeds.

## 4. Configure the existing interfaces and credentials

The repo has three configuration layers; use them instead of hardcoding model calls:

| Interface | Set here | Rules |
| --- | --- | --- |
| `bench/configs/backends.json` or `python -m bench --backends <private-registry.json>` | Named construction, consolidation, and QA answer backends | Entries use `type: "openai_compatible"` with `base_url`, exact `model`, and `api_key_env`, or `type: "local"` for in-process construction. Optional `frame_fps`, `timeout`, `temperature`. |
| `StreamMeCo/configs/api_config.json` | MAI and text-embedding service endpoints | Keep the alias name `text-embedding-3-large`; use the same embedding model for writes and reads. |
| `StreamMeCo/configs/processing_config.json` | `asr_provider`, CAM++ checkpoint fallback, optional local VLM `ckpt` | Keep `asr_provider: "openrouter-mai-transcribe-2"` for the reference service chain. |
| Dataset manifest and QA JSON | Clip schedule and `ask_at_s` questions | Both paths point to the same files. |
| Two run-config JSON files | `dataset`, `path`, `memory_backend`, `qa`, `output_dir`; Path 2 adds `consolidation_backend`, `period_s`, `moss` | Set distinct output directories. `qa.backend` must be OpenAI-compatible; omit `qa.judge_backend` for this benchmark and omit `answer` from runtime questions. |
| CLI | `--validate`, `--backends`, repeatable `--set KEY=VALUE` | `--set` overrides top-level keys; edit JSON for nested `qa` or `moss`. `--resume-from` is not a fresh paired run. |

The default reference service chain needs `OPENROUTER_API_KEY` for MAI and embeddings, plus credentials for the chosen construction, consolidation, and QA answer endpoints. Keep secrets outside Git, for example in a mode-600 `/opt/streammeco/secrets/runtime.env` sourced in each shell. The tracked configs currently contain inline key fields; **do not assume any inherited key works**. In particular, pristine `mmagent.utils.chat_api` constructs its embedding client from `api_config.json`'s **inline** `api_key` at import time. In the disposable runtime copy, clear inherited inline keys and inject the operator's embedding key into that alias **before** starting either process. The MAI adaptor honors its `api_key_env`; set its runtime alias/key coherently too. Never print keys in preflight output or commit the edited runtime config.

If the new agent has no provider credentials, it must obtain the required keys
or a working equivalent endpoint from the operator before service preflight.
The checked-out JSON is not a credential source. Re-run `pip check` after
installing the MOSS helpers or any optional local-model packages.

For a Path 2 local MOSS runner, `moss` is `{"checkpoint": "...", "revision": "...", "repository": "..."}`. For a served runner, it is `{"endpoint": "...", "media_root": "...", "revision": "..."}`. The two shapes are exclusive, and **one must be configured and pass inference preflight**. The runner rejects missing MOSS before construction; the consolidation worker rejects a missing window result before reasoning. The Path 1 config omits consolidation and MOSS. Keep `period_s: 1200` for the reference comparison unless the experiment deliberately changes cadence; record any change.

The run configs can name any valid models. A clear naming pattern is `memory-vlm` and `consolidation-llm`. Point Path 1 and Path 2 at the same `memory-vlm` and `qa.backend`; Path 2 alone names `consolidation-llm`. `StreamMeCo/configs/memory_config.json` sets native graph parameters and must be the same for both processes.

For an endpoint-based run, the private backend registry can start with this
shape. Replace URLs, model IDs, and environment-variable names; no key value
belongs in the JSON:

```json
{
  "memory-vlm": {
    "type": "openai_compatible", "base_url": "<chat /v1 URL>",
    "model": "<image-capable model ID>", "api_key_env": "MEMORY_API_KEY",
    "frame_fps": 2
  },
  "consolidation-llm": {
    "type": "openai_compatible", "base_url": "<chat /v1 URL>",
    "model": "<text model ID>", "api_key_env": "CONSOLIDATION_API_KEY"
  }
}
```

Use two run JSON files with the **same** `dataset`, `memory_backend`, and `qa`
values. Path 1 sets `"path": 1`; Path 2 sets `"path": 2` and additionally
provides `consolidation_backend`, `period_s`, and one `moss` object. The
following common fragment shows the QA interface; the actual paths should be
absolute on the GPU host:

```json
{
  "dataset": "/absolute/path/to/day1_manifest.json",
  "memory_backend": "memory-vlm",
  "qa": {
    "questions": "/absolute/path/to/day1_questions.json",
    "backend": "memory-vlm", "topk": 10
  }
}
```

**One-shot QA interface:** `bench/qa.py` calls
`m3_adaptors.one_shot_retrieval.answer_with_retrieval` for both paths. It
invokes the active path's `mmagent.retrieve.search` once with the configured
`topk`, then asks the configured QA model once to answer from those facts.
Path 2 still reads its consolidation snapshot after the barrier. The
`generate_action` controller is outside this benchmark QA path. Each
`qa.jsonl` record includes the answer and a `retrieval` trace with clip IDs,
returned facts, scores, and timings; Path 2 also includes available node IDs
and graph version in `search_metrics`. Check one search and one answer call
per question on both paths in the smoke run. Omit `answer` from runtime
questions to avoid a judge call; score against the separate answer key.

## 5. Preflight, smoke, then launch

Run `python -m bench <path1-config> --backends <registry> --validate` and the equivalent Path 2 command. Confirm the resolved dataset count, paths, backends, period, MOSS mode, and distinct outputs. `--validate` checks config wiring, **not** endpoint behavior.

Before paying for a full day, verify:

- CUDA tensor operation, `CUDAExecutionProvider`, CAM++ checksum/import, Buffalo-L ONNX files, the MOSS checkpoint/repository or server revision, and `pip check`.
- OpenRouter embeddings return 3072 values; MAI transcribes a short recorded WAV; the selected consolidation backend returns a complete chat response.
- Run MOSS on a short real audio window through the **selected local or served runner**. Check its model/revision, source-audio hash, session-relative timestamps, speaker IDs, parseable segments, and complete output. A reachable endpoint or loadable checkpoint alone is insufficient. Fail the Path 2 preflight if this call fails, truncates, or produces no usable evidence for observed speech.
- The construction backend processes **multiple ordered images**. A text-only “OK” response is insufficient; use two distinguishable JPEGs or a known-frame probe and inspect what the model saw.
- Both paths complete a separate, disposable small smoke run from an empty graph. Include one scheduled smoke QA to exercise exactly one retrieval and one answer call; Path 2 must also complete a MOSS/consolidation tail. Keep smoke outputs away from the full-run directories.
- The manifest's non-overlap validator and the **long-gap MOSS gate** in §3 pass. If any gate fails, fix the cause and rerun preflight. Record any code adjustment and test it before launching full DAY1.

Launch the full configs as **two fresh processes**, preferably sequentially to simplify GPU/resource and provenance accounting. This is a command pattern, not a requirement to use these filenames:

```bash
set -Eeuo pipefail
python -u -m bench "$PATH1_CONFIG" --backends "$BACKEND_REGISTRY" \
  2>&1 | tee "$PATH1_LOG"
python -u -m bench "$PATH2_CONFIG" --backends "$BACKEND_REGISTRY" \
  2>&1 | tee "$PATH2_LOG"
```

Source the secret environment, activate the writer venv, set `PYTHONPATH` to the runtime root, `StreamMeCo`, 3D-Speaker, and MOSS helper repository, and run from the runtime root. A `tmux` session is suitable over SSH. Require a new, absent `output_dir` for each attempt: `construction.jsonl` and `qa.jsonl` append, so rerunning into a partial directory corrupts the comparison. Archive failed outputs separately. `--resume-from` repairs Path 2 consolidation from a saved snapshot but does not recreate a paired online QA stream; do not use such a recovery as the official full comparison.

Monitor clip count and QA count as the stream advances. Path 2 also writes `consolidation.jsonl`, a `consolidation/` evidence tree, and `graphs/README.md` with before/after consolidation graph Markdown and pickle pairs. Check that each Path 2 consolidation job contains its MOSS window result and that observed speech has valid diarized segments; stop if MOSS or native reindexing fails. Path 1 writes per-clip graph pickles in `graphs/`. Neither process should read the other path's result directory.

## 6. Result contract and scoring

### Review the graphs with `bench/graphreplay.py`

The Path 2 runner already calls `GraphReplayer.before()` immediately before
each consolidation barrier and `GraphReplayer.after()` after the committed,
reindexed graph is available. It also captures a completed final-tail pair;
an unsuccessful or absent tail has no after-state. There is no separate
`graphreplay.py` command to launch during construction.

1. Open `<path2_output>/graphs/README.md` and its linked
   `before_<ordinal>_consolidation.md` and
   `after_<ordinal>_consolidation.md` files. Check that every successful
   scheduled boundary, and the completed final tail when one occurs, has
   **both** Markdown and pickle states at the same cutoff. The scheduled
   barriers appear in `consolidation.jsonl`; the runner captures the final
   tail at shutdown, so count that pair separately. A missing after-state is
   a failed or incomplete Path 2 result, not a completed consolidation.
2. Compare node and edge counts, character mappings, trusted aliases, and
   canonical names in each pair. Match changed identities to that window's
   `identity_changes.json`, `execution.json`, and `reindex_report` in
   `consolidation.jsonl` where available. For the final tail, inspect its
   consolidation job artifacts and the before/after pickle pair. Check that
   Path 1's graph has no Path 2 identity revision or consolidation metadata.
   Path 1 may group `<voice_*>` and `<face_*>` under `character_*`, but only
   Path 2's accepted consolidation can assign supported human names and
   re-embed the affected retrieval text.
3. Use `bench.graphreplay.graph_view` and `render_graph` in a small **read-only**
   analysis script to render each path's own `graph_final.pkl` to a separate
   final Markdown file. Load pickles only in the benchmark writer environment,
   with the same `mmagent` import setup used by that path; apply the reader
   adaptor before loading a Path 2 pickle. Keep the two final views beside the
   result report and retain the original pickles as the authoritative graphs.

`graphreplay.py` Markdown deliberately shows stored raw node `contents` and
identity mappings; it omits embeddings and does **not** prove that retrieval
text was reindexed. For that check, inspect the Path 2 pickle's text-node
`retrieval_contents` and `embedding_input_fingerprint`, and confirm the
matching reindex report lists changed text nodes and its embedding backend.
For example, v9's completed first boundary changed 186 text nodes and mapped
some `<voice_*>` references to Jake or Xiushuo; v9 still failed later and is
not a benchmark result.

Produce a small report next to, not inside, either run's graph directory:

- **Path 1:** exact `graph_final.pkl` path and final graph Markdown; `qa.jsonl` path; clip count; question count; valid option-letter count; exact-match accuracy against the separate answer key; mean or median `wall_ms`.
- **Path 2:** the same fields, plus consolidation count, final-tail status, and links to each before/after graph pair and reindex report.
- **Paired comparison:** one CSV row per question with `id`, `ask_at_s`, ground-truth option, both raw predictions, both parsed option letters, both correctness flags, and both `wall_ms` values. Compare question IDs, wording, and order before computing a difference. Flag ambiguous or missing option letters rather than guessing.

These are **online** retrieval scores: each question sees its own path's graph
at the first committed clip ending at or after `ask_at_s` (Path 2 waits for a
consolidation barrier at that point). `graph_final.pkl` is the end-state
artifact, not the graph that answered every earlier question. If a later
analysis evaluates all questions against each final graph, label that a
separate offline replay and keep its answers out of the online comparison.

With the reference datasets, acceptance requires **828 constructed clips and 101 online answers per path**, no skipped clip, no late question, one retrieval and one answer call per question, zero judge calls, both processes exiting `0`, both `graph_final.pkl` files present, and Path 2's MOSS/consolidation final tail complete. Every Path 2 consolidation window must have valid MOSS evidence and a completed native reindex, and every completed consolidation must have its graph replay pair. Score each answer against its held-out multiple-choice key. `qa.jsonl.wall_ms` includes retrieval and answer generation; label it total QA wall time, not pure retrieval latency. Inspect each `retrieval` trace before claiming per-question retrieved evidence.

For a different pinned dataset revision, replace 828/101 with the documented, validated counts. “About 100 retrievals” means **101 eligible DAY1 questions** in the reference CSV. Keep the raw `qa.jsonl` files and graph pickles as the authoritative artifacts; any summary must be reproducible from them. Record the code commit, effective non-secret config, package versions, GPU, data/model revisions and hashes, UTC start/end time, and failures or deviations.

Do not report a paired benchmark from v9, a partial run, shared graph state, a failed Path 2 tail, or mismatched QA schedules.
