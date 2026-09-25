# GPU Deployment Cookbook — StreamMeCo + Consolidation (dual-path)

Deployment dependencies for `StreamMeCo-consolidation/` (Path 1: pristine
StreamMeCo/M3 online memorization; Path 2: same run + online consolidation
via `m3_adaptors`). Selectively distilled from the parent repo's
the parent project's architecture notes: voice embeddings are CAM++; the active ASR is MAI-Transcribe-2.

---

## 1. Model-level dependencies

### 1.1 Local GPU models (checkpoints on disk)

| Stage | Model | On-disk location (relative to `StreamMeCo/`) | Runtime | Notes |
| --- | --- | --- | --- | --- |
| Memory-generation VLM | **Any VLM checkpoint you choose** — see §1.1.1 for the selection interface | Wherever you place it; the loader path comes from `configs/processing_config.json` key `ckpt` (only used by the `local` backend) | PyTorch + Transformers in-process, or an external OpenAI-compatible server (e.g. vLLM) | No checkpoint is required on disk if the run config selects a remote/`openai_compatible` backend |
| Speaker embedding (voice identity) | **SpeakerLab CAM++** (`speech_campplus_sv_zh_en_16k-common_advanced`, revision `v1.0.0`) | `models/camplus/campplus_cn_en_common.pt` (SHA-256 `92f29b94e6948786a26778c9e302525d185bb08c8b9f5252ed98776902840199`) — override via `CAMPLUS_CHECKPOINT` env or `processing_config["speaker_embedding_checkpoint"]` | torch, lazy-loaded on first voice segment | **192-D** voice embeddings. Requires the **3D-Speaker** repo (provides `speakerlab.models.campplus.DTDNN.CAMPPlus`). CAM++ is the **only** speaker-embedding chain — the pristine eager ERes2NetV2 load was removed from `mmagent/voice_processing.py`, so no ERes2NetV2 checkpoint is needed. |
| Face detection + recognition | **InsightFace Buffalo-L** pack | `models/insightface/models/buffalo_l/` (`det_10g.onnx`, `w600k_r50.onnx`, `1k3d68.onnx`, `2d106det.onnx`, `genderage.onnx`) | ONNX Runtime GPU (`CUDAExecutionProvider`) | `FaceAnalysis(name="buffalo_l")`, RetinaFace detection + ArcFace recognition; embeddings stored on `img` nodes |

#### 1.1.1 VLM/LLM backend interface (how a model is selected)

No VLM/LLM is hardcoded by the harness. `bench/configs/backends.json` is the
single registry; each entry is one of two types:

```jsonc
{
  "my-local-vlm":  { "type": "local" },
  "my-served-vlm": { "type": "openai_compatible",
                     "base_url": "http://gpu-box:8000/v1",
                     "model": "<model-id-as-served>",
                     "api_key_env": "VLLM_API_KEY",
                     "api_key": "<optional inline fallback key>" }
}
```

The env var named by `api_key_env` wins; the inline `api_key` is the
fallback. The shipped registry (`bench/configs/backends.json`) routes
`gemini-3.8-flash` (memory construction, both paths) and `gpt-5.6-sol`
(Path-2 consolidation) through the 302.ai proxy.

- **`local`** — the pristine in-process Transformers loader
  (`mmagent/utils/chat_qwen.py`). It loads whatever checkpoint
  `configs/processing_config.json` → `ckpt` points at, on first generation
  call, with `device_map="auto"`. Point `ckpt` at any checkpoint that loader
  supports; install the matching `transformers` version. The pristine loader
  requests `attn_implementation="flash_attention_2"`, so `flash-attn` must be
  installed for this mode.
- **`openai_compatible`** — any chat-completions endpoint: a cloud API
  (Gemini/OpenAI-style) or a GPU checkpoint you serve yourself, e.g.
  `vllm serve <hf-model-id> --port 8000`. `bench` sends ordered, timestamped
  JPEG `image_url` parts (default 2 frames/s) to both cloud and locally served
  VLMs. Confirm the served model actually processes multiple images.

A run config (`bench/configs/runs/*.json`) then picks backends by name:
`memory_backend` (generation VLM, both paths) and `consolidation.backend`
(Path 2 only) — they may be the same or different backends.

Fetch and verify the identity checkpoints as described in the
[Jake DAY1 runbook](benchmark.md#2-provision-the-gpu-and-all-dependencies).
The historical `gpu_setup/download_models.py` helper is absent from this
checkout.

### 1.2 Remote APIs

| Stage | Model | Provider key / env var | Used by |
| --- | --- | --- | --- |
| ASR + utterance timing | **MAI-Transcribe-2** | alias `openrouter-mai-transcribe-2` in `configs/api_config.json`; `OPENROUTER_API_KEY` | `process_voices` per clip (both paths); produces timestamped transcript segments |
| Text embedding (native M3) | **`text-embedding-3-large`** (3072-D) | alias `text-embedding-3-large` in `configs/api_config.json`; any OpenAI-compatible endpoint | Episodic/semantic node embeddings at write time **and** query embeddings at retrieval/QA time — required by both paths, including pure-local-VLM runs |
| Memory-generation VLM (cloud or served locally) | e.g. Gemini, or an image-capable checkpoint behind an OpenAI-compatible chat endpoint | `bench/configs/backends.json` entry | Path-1/Path-2 memory generation when `type != local`. Endpoint must process multiple JPEG `image_url` parts |
| Consolidation LLM (Path 2 only) | Any OpenAI-compatible chat model (e.g. GPT-5.x, Gemini) | `bench` run config `consolidation.backend`; `CONSOLIDATION_API_KEY` (or the backend's `api_key_env`) | `consolidation.llm_consolidator.propose` at each 20-min boundary; runs outside the live mutation path |
| Consolidation audio evidence (Path 2, required) | **MOSS-Transcribe-Diarize** | `moss` field in bench run config: `{"endpoint","media_root","revision"}` for an inference server, or `{"checkpoint","revision","repository"}` for a local checkpoint | Per-window re-transcription and speaker evidence; missing MOSS stops consolidation before the LLM call |

Embedding spaces are **not interchangeable**: CAM++ 192-D (voice), Buffalo-L
(face), `text-embedding-3-large` 3072-D (text) are three separate spaces.

---

## 2. Software stack

Reference host: Ubuntu 22.04, NVIDIA driver with **CUDA 12.4**, Python **3.10** venv
(validated on RTX 4090; the parent project's GPU runbook describes that host).

```bash
sudo apt install -y build-essential ffmpeg git git-lfs \
  libgl1 libglib2.0-0 libsndfile1 python3.10 python3.10-dev python3.10-venv

python3.10 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip setuptools wheel

# Core GPU (pinned, CUDA 12.4 wheels)
pip install torch==2.6.0+cu124 torchvision==0.21.0 torchaudio==2.6.0+cu124 \
  --index-url https://download.pytorch.org/whl/cu124

# Identity + pipeline (validated matrix from the GPU runbook)
pip install \
  accelerate==1.15.0 albumentations==2.0.8 av==17.1.0 easydict==1.13 \
  hdbscan==0.8.44 httpx==0.28.1 insightface==0.7.3 matplotlib==3.10.9 \
  moviepy==2.2.1 numpy==1.26.4 onnx==1.17.0 onnxruntime-gpu==1.21.1 \
  openai==1.109.1 opencv-python-headless==4.11.0.86 pillow==11.3.0 \
  pydub==0.25.1 qwen-vl-utils==0.0.14 requests==2.32.3 scikit-image==0.25.2 \
  scikit-learn==1.6.1 scipy==1.14.1 soundfile==0.13.1 \
  transformers==5.17.0 tqdm==4.67.1

# flash-attn: required only for the `local` (in-process) VLM backend
pip install flash-attn --no-build-isolation

# Consolidation package
pip install -r consolidation/requirements-native.txt   # jsonschema, numpy, openai, httpx, ...

# CAM++ model code
git clone https://github.com/modelscope/3D-Speaker.git repos/3D-Speaker
git -C repos/3D-Speaker checkout 065629c313eaf1a01c65c640c46d77e61e9607b4
# ensure repos/3D-Speaker is importable (PYTHONPATH) for `speakerlab.*`
```

`causal-conv1d` is optional — without it Transformers uses a slower but correct
PyTorch fallback; do not block setup on it.

---

## 3. Configuration & credentials

```bash
export OPENROUTER_API_KEY=...        # text-embedding-3-large via OpenRouter (required, both paths)
export API_302_KEY=...               # 302.ai proxy: gemini-3.8-flash + gpt-5.6-sol (inline fallback keys exist in configs)
export CONSOLIDATION_API_KEY=...     # only if a consolidation backend has no api_key_env/api_key of its own
export VLLM_API_KEY=...              # only for a self-hosted vLLM VLM backend
```

- `StreamMeCo/configs/api_config.json`: ships with the live aliases —
  `text-embedding-3-large` (OpenRouter, `openai/text-embedding-3-large`),
  `gemini-3.8-flash` and `gpt-5.6-sol` (302.ai), and
  `openrouter-mai-transcribe-2` (OpenRouter, `microsoft/mai-transcribe-2`).
- `StreamMeCo/configs/processing_config.json`: `ckpt` → your local VLM
  checkpoint path (only read by the `local` backend); `asr_provider:
  "openrouter-mai-transcribe-2"` is already set.
- `bench/configs/backends.json` + `bench/configs/runs/*.json`: select path
  (1 or 2), memory-generation backend, consolidation backend, `period_s` (1200).

## 4. Preflight

```python
import torch, onnxruntime as ort
assert torch.__version__ == '2.6.0+cu124' and torch.cuda.is_available()
assert 'CUDAExecutionProvider' in ort.get_available_providers()
```

Then: `python -m bench bench/configs/runs/<run>.json --validate`, and verify
checkpoints exist (`models/camplus/...`, `models/insightface/models/buffalo_l/`,
plus your local VLM checkpoint if a run uses the `local` backend).
