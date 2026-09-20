# StreamMeCo + Consolidation — dual-path project

This project bundles the original **M3 agent + StreamMeCo** code with the
**consolidation** package, exposing two runnable paths over **one shared,
unmodified copy** of the original code:

- **Path 1 — Original**: the pristine StreamMeCo + M3 agent, runnable exactly as-is.
- **Path 2 — Consolidation**: the same pristine code + the `m3_adaptors/` interface
  layer, which injects the consolidation adaptor modules and hooks at runtime
  (no original file is modified or duplicated), plus `consolidation/`.

## Layout

```
StreamMeCo-consolidation/
├── StreamMeCo/          # PRISTINE original code (byte-identical to
│                        #  github.com/Celina-love-sweet/StreamMeCo). Shared by both paths.
├── consolidation/       # the consolidation package
├── m3_adaptors/         # PATH 2 INTERFACE: all consolidation adaptor logic
│   ├── __init__.py      #   apply() / apply_writer() — injection + patch orchestration
│   ├── identity.py      #   → mmagent.character_identity (reviewed identity state;
│   │                      #    consolidation calls apply_conclusions() here)
│   ├── evidence.py      #   → mmagent.consolidation_evidence (replay/assignment export)
│   ├── audit.py         #   → mmagent.clip_audit (per-clip audit serialization)
│   ├── speaker_mapping.py#  → mmagent.speaker_mapping (pre-mutation voice scoring)
│   ├── asr_*.py, cloud_http.py, memory_backend.py, chat_api_ext.py, chat_qwen_lazy.py
│   │                    #   supporting services the adaptor needs (prepared ASR,
│   │                      #    batched embeddings, backend selection)
│   ├── online_state.py  #   checkpoint store (m3_adaptors.online_state)
│   ├── patches/         #   the adaptor hunks, lifted verbatim from the edited code and
│   │   │                #    applied onto the original modules at runtime:
│   │   ├── videograph.py        # runtime-mutation locking, load_current, identity hooks
│   │   ├── retrieve.py          # _resolve_graph, canonical translate, cutoff enforcement
│   │   ├── memory_processing*.py# canonical retrieval contents / precomputed embeddings
│   │   ├── voice_processing.py  # assignment_scores + provider provenance (writer-side)
│   │   ├── memorization.py      # process_segment runtime wrapper + per-clip audit (writer-side)
│   │   └── streammeco.py        # identity-aware node removal (writer-side)
│   ├── consolidation.py #   CLI wrapper: python -m m3_adaptors.consolidation ...
│   └── tests/test_online_services.py  # adaptor contract test
└── conftest.py          # applies m3_adaptors for the test suite
```

`StreamMeCo/` is untouched. Publication stages `graph.pkl` + a native
`retrieval_ready.json` receipt. `consolidation/native.py`
(`m3_module`) checks `sys.modules` first, so the adaptor modules injected by
`m3_adaptors.apply()` resolve exactly as if they lived inside `mmagent/` —
without modifying `mmagent/`.

## Path 1 — original StreamMeCo + M3 agent

Unmodified upstream. Follow `StreamMeCo/README.md`:

```bash
cd StreamMeCo
conda create -n streammeco python=3.11.14 -y && conda activate streammeco
bash setup.sh
pip install qwen-omni-utils==0.0.4 transformers==4.51.0
# fill in configs/api_config.json with your API keys
python -m m3_agent.memorization_intermediate_outputs --data_file memory_videomme.jsonl
python -m m3_agent.memorization_memory_graphs --data_file memory_videomme.jsonl
python streammeco.py        # memory-graph compression
```

The adaptor is never imported on this path; behavior is byte-for-byte upstream.

## Path 2 — consolidation over the online M3 graph

Same pristine code, with the adaptor applied at runtime:

```python
import m3_adaptors
m3_adaptors.apply()          # reader-side: identity/evidence/retrieve/graph patches (CPU-safe)
m3_adaptors.apply_writer()   # writer-side: voice/memorization/streammeco patches (needs full GPU deps)

# then run the normal streaming pipeline from StreamMeCo/ and attach consolidation:
from consolidation.runtime import ConsolidationRuntime
from consolidation.runtime_io import NativeConsolidationWorker
from consolidation.moss_runner import WindowMoss

worker = NativeConsolidationWorker(collect_evidence, propose_fn, directory=jobs_dir,
                                   moss=WindowMoss(moss_endpoint, media_root))
runtime = ConsolidationRuntime(video_graph, worker, period_s=1200)
# ... existing process_segment calls participate automatically ...
runtime.close()
```

(See `consolidation/RUNTIME.md` for the full wiring contract.) All adaptor hooks are
inert unless a `ConsolidationRuntime` is attached or the graph is consolidated, so a
patched process without consolidation behaves like Path 1.

Offline review cycles use the wrapper (which applies the adaptor before delegating
to the untouched consolidation CLI):

```bash
cd StreamMeCo-consolidation
python -m m3_adaptors.consolidation prepare --native-graph graph.pkl --cutoff 1200 --work <workdir> --root <replay_root>
python -m m3_adaptors.consolidation run     --native-graph graph.pkl --cutoff 1200 --work <workdir> --patch patch.json
```

Pass `--root`/`--work` explicitly — the default `benchmark/egolife_m3_jake_day1`
data root is not included in this project.

### Setup

```bash
cd StreamMeCo-consolidation
python3 -m venv .venv && source .venv/bin/activate
pip install -r StreamMeCo/requirements.txt                     # full writer deps (GPU box)
pip install -r consolidation/requirements.txt -r consolidation/requirements-native.txt
# fill in StreamMeCo/configs/api_config.json with your API keys
pytest consolidation/tests m3_adaptors/tests -q
```

Expected test baseline: **96 passed**, 1 failed + 2 errors — every remaining
failure is the missing `egolife_m3_jake_day1` benchmark replay data
(`consolidation/tests/test_replays.py`). Nothing else.

## How the adaptor works (mechanics)

- `apply()` injects stub `mmagent`/`mmagent.utils` packages into `sys.modules`
  (with `__path__` pointing at the pristine dirs) so the pristine eager
  `__init__.py` — which imports torch and loads a speaker checkpoint — never runs
  for CPU-side readers. It then inserts `StreamMeCo/` on `sys.path`, injects the
  standalone adaptor modules under their `mmagent.*` names, and installs the patches.
- Patches are verbatim-lifted functions from the edited code, rebound so module-level
  helpers resolve through the target module's namespace at call time — preserving the
  monkeypatch contracts consolidation's tests rely on. Pickles keep their original
  class paths (`mmagent.videograph.VideoGraph`), so snapshots interchange cleanly.
- `apply_writer()` additionally patches the writer-side modules (voice/memorization/
  streammeco) and requires the full GPU dependency set; `StreamMeCo/models/`
  checkpoints (e.g. the speaker model) must be present, as upstream requires.

## Benchmark harness (bench/)

`bench/` runs either path end-to-end with pluggable model backends, selected
entirely by a run-config JSON — no changes to `StreamMeCo/`, `consolidation/`,
or `m3_adaptors/`:

```bash
python -m bench bench/configs/runs/jake_path1_qwen.json     # path 1, qwen VLM
python -m bench bench/configs/runs/jake_path2_gemini.json   # path 2, gemini VLM + gemini consolidation
```

`bench/configs/backends.json` names every backend once (`local` = pristine
in-process VLM loader; `openai_compatible` = cloud API or a GPU checkpoint you
serve, e.g. via `vllm serve`). A run config picks `path` (1 or 2),
`memory_backend` (generation VLM), `consolidation_backend` (path 2 only),
`period_s` (default 1200 s = 20 min), and an optional online-QA schedule
(`qa.questions` with `ask_at_s`, answered via pristine
`retrieve.answer_with_retrieval` on any configured backend). See
`bench/README.md`.

## Known limitations

- Face/VLM stage metrics in clip audits are `None` (all consolidation-consumed
  fields — `assignment_scores`, `voice_observations`, `retrieval_contents`,
  provenance — are preserved).
- `chat_api_ext.py` (batched embeddings, transcription retry) is a behavior-preserving
  port onto the pristine `chat_api` plumbing rather than a byte-verbatim lift.
- **API keys**: `StreamMeCo/configs/api_config.json` ships upstream placeholders;
  fill in real keys before running either path.

## Launch notes (writer/GPU box)

Full dependency + checkpoint + credential cookbook: **`GPU_DEPLOYMENT.md`**.

Recorded constraints for actually launching the pipeline (deliberately not
worked around in code):

- **API keys must be inline.** Pristine `chat_api` builds clients from
  `api_key` in `configs/api_config.json` only; `api_key_env`-style indirection
  is not read. Put real key strings in the config.
- **Launch the writer with cwd = `StreamMeCo/`.** Pristine modules read
  `configs/*.json` relative to the process cwd at import time, and the local
  VLM (`mmagent/utils/chat_qwen.py`) is loaded lazily at the first generation
  call — it reads `configs/processing_config.json` relative to the cwd *at
  that moment*. Running from the pristine root makes both resolve.
- **Checkpoints.** Pristine `voice_processing.py` eager-loads
  `models/pretrained_eres2netv2.ckpt` at import (must merely exist; it is
  unused after the adaptor swaps in the CAM++ chain). Speaker embeddings
  actually come from CAM++: `CAMPLUS_CHECKPOINT` env, else
  `processing_config["speaker_embedding_checkpoint"]`, else
  `models/camplus/campplus_cn_en_common.pt`.
- **Generation is pristine upstream**: with the `local` bench backend, memory
  generation uses the pristine in-process loader and
  `memory_processing_qwen` generate path, loading whatever checkpoint
  `processing_config["ckpt"]` names; any `openai_compatible` bench backend
  (cloud API or self-served checkpoint) is selected purely by run config.
