# Runtime Error Log — Jake 20-min Path1/Path2 Runs

Date: 2026-09-20 · GPU: `streammeco-a6000-20260914` (RTX A6000) · Code: `/opt/streammeco/run/StreamMeCo-consolidation`

Runs launched: `jake_path1_gemini.json` (path 1, pristine) and `jake_path2_gemini.json` (path 2, consolidation), dataset `egolife_jake_20min` (40 clips). Both crashed on clip 1 (and once at startup). This file records every runtime error, its root cause, and a classification: **code bug** (and in which component) vs **usage/configuration error**.

## Summary

| # | Error | Where it surfaced | Root cause | Classification |
|---|-------|-------------------|------------|----------------|
| 1 | `ModuleNotFoundError: qwen_omni_utils` | both paths, at startup | package missing from writer venv; undeclared in `StreamMeCo/requirements.txt`, imported eagerly | **Usage/environment**, aggravated by a StreamMeCo packaging gap |
| 2 | `KeyError: Model client 'gemini-1.5-pro-002' is not initialized` | path 1, clip 1, voice stage | hardcoded model name in pristine `voice_processing.diarize_audio` | **StreamMeCo pristine-code rigidity**, exposed by our config (we dropped gemini-1.5-pro) |
| 3 | `KeyError: 'video_descriptions'` | path 2, clip 1, memory generation | prompt asks for key `video_description` (singular), parser reads `video_descriptions` (plural) | **StreamMeCo pristine-code bug** (prompt/parser mismatch). Not the model, not our usage |
| 4 | `OSError: File name too long` | diagnosis repro script only | repro passed raw base64 where the real flow passes a file path | **Not a product bug** (repro artifact); documents a `_media_url` footgun |
| 5 | `FileNotFoundError: models/camplus/campplus_cn_common.pt` | both paths, first clip containing speech | `rsync --delete` of the repo wiped the GPU-only symlink `StreamMeCo/models/camplus/…` → CAM++ checkpoint missing | **Usage/deployment error** (sync procedure), not a code bug |
| 6 | `RuntimeError: consolidation shutdown requires retry: [('patch', 'string indices must be integers')]` | path 2, `runtime.close()` during the error-5 abort | tail-flush consolidation ran on a 1-clip graph while shutting down; patch application failed | **Unclassified / transient** — observed only during the abortive shutdown; re-check at the real 1200 s boundary and final close |
| 7 | `AttributeError: 'VideoGraph' object has no attribute 'reverse_character_mappings'` | path 1, first QA question (clip 9) | pristine `process_segment` never calls `refresh_equivalences()` (only `streaming_process_video` does, once at the end — and bench doesn't use it), so QA retrieval's `translate` hits a missing attribute | **Pristine-code/bench integration gap** — fixed bench-side in `bench/runner.py` (refresh per clip on path 1), no pristine edit needed |
| 8 | path 2 process vanished mid-clip (no traceback); duplicate path-1 writers | both, ~60–75 min into runs | **operator error**: the launcher chained `... && nohup python ... &`, so `$!` was the `&&`-subshell's PID, not python's; `kill <pid>` killed shells but left two stale path-1 python processes running (~15 GB RAM each), which OOM-starved path 2 and double-wrote path 1's output dir | **Usage/operator error** — not a code bug. Fixed by killing the real python PIDs and launching with `;`-separated commands so `$!` is python's PID |
| 9 | `RuntimeError: scheduled consolidation failed: [('patch', 'string indices must be integers')]` | path 2, at the real t=1200 s consolidation boundary after a clean 40/40-clip construction | `bench/config.py:104-108` copies the dataset manifest's top-level `"source"` (a documentation **string**) into every plan event, but `m3_adaptors/evidence.py:42` expects `event["source"]` to be a **dict** (`{path, start_s, source_offset_s}`) | **Interface/contract mismatch** between bench's manifest schema and the consolidation evidence contract — deterministic, not transient (this is also what error 6 actually was). Sol was never called |
| 10 | `AttributeError: 'VideoGraph' object has no attribute 'segment_times'` (latent — not yet hit) | would be `m3_adaptors/evidence.py:141` immediately after error 9 is fixed | nothing in pristine StreamMeCo or the m3_adaptors writer chain ever sets `graph.segment_times`; only `evidence.py` and `online_state.py` read it | **m3_adaptors code gap** (missing graph state population), masked by error 9 |
| 11 | `RuntimeError: scheduled consolidation failed: [('patch', '41370')]` | path 2 (day1-first20min window), t≥1200 s consolidation boundary after a clean 41/41-clip construction | my error-10 fix populated `graph.segment_times[clip_id]` **after** `process_segment` returned, but the runtime deep-copies the pending snapshot at `segment()` **exit** — the snapshot's `segment_times` missed the boundary clip itself → `KeyError(41370)` in evidence export | **Bench-side fix-ordering bug** (introduced by the error-10 fix), not consolidation code. Sol was never called |

Non-errors observed (benign): `No qualified faces detected`, `No qualified voices detected`, `empty content, skip` on clip 1 — normal for a quiet/dark intro clip; `chat_qwen.generate_messages` (chat_qwen.py:105) skips empty content parts by design.

---

## Error 1 — `ModuleNotFoundError: No module named 'qwen_omni_utils'`

**Symptom (both paths, at startup, before any clip):**

```
bench/backends.py:114 apply_memory_backend
  → import mmagent.memory_processing_qwen
    → StreamMeCo/mmagent/utils/chat_qwen.py:18
      from qwen_omni_utils import process_mm_info
ModuleNotFoundError: No module named 'qwen_omni_utils'
```

Path 2 hit the same import lazily via `m3_adaptors/chat_qwen_lazy.py:23`.

**Root cause:** `chat_qwen.py` imports `qwen_omni_utils` at module top, so the package is required merely to *import* the module — even though we never run a Qwen model (the import survives in `m3_adaptors/chat_qwen_lazy.py` because the lazy shim eventually loads the real module). `StreamMeCo/requirements.txt` does **not** list `qwen-omni-utils` (verified by grep), so a from-requirements venv is guaranteed to miss it. Its own transitive deps (`audioread`, `librosa`) are also needed.

**Classification:** primarily a **usage/environment** gap (the GPU writer venv was assembled without it), rooted in an **upstream StreamMeCo packaging/code issue**: undeclared dependency + eager top-level import of a package only needed for the local-Qwen path.

**Fix (applied):** made the import lazy — `chat_qwen.py` now imports
`qwen_omni_utils` inside `get_response` (the local-Qwen inference path, the
only place `process_mm_info` is used). The package is therefore optional: it
is only needed when running `memory_backend: qwen-local`. The stopgap
`qwen-omni-utils`/`audioread`/`librosa` installs in `/opt/streammeco/.venv`
were uninstalled again, and the module imports fine without them.

---

## Error 2 — path 1: `KeyError: Model client 'gemini-1.5-pro-002' is not initialized`

**Symptom (clip 1, voice stage):**

```
m3_agent/memorization_memory_graphs.py:44 process_segment
  → mmagent/voice_processing.py:239 process_voices
    → voice_processing.py:161 diarize_audio
      → chat_api.get_response(model="gemini-1.5-pro-002", ...)
KeyError: "Model client 'gemini-1.5-pro-002' is not initialized.
Available model clients: ['deepgram-asr', 'gemini-3.8-flash', 'gpt-5.6-sol', 'text-embedding-3-large']."
```

**Root cause:** pristine `StreamMeCo/mmagent/voice_processing.py:157` hardcodes `model = "gemini-1.5-pro-002"` inside `diarize_audio` — the pristine pipeline does audio segmentation/diarization by asking a Gemini VLM (via `chat_api`, which sends the alias name verbatim as the upstream model id). Our `configs/api_config.json` deliberately defines only `text-embedding-3-large`, `gemini-3.8-flash`, `gpt-5.6-sol`, `deepgram-asr`, so no client exists under that name.

Notes:
- This hits **path 1 only**. Path 1 is the pristine baseline: it does not use the m3 deepgram ASR chain (`m3_adaptors.apply_writer()` is path-2-only in `bench/runner.py:40-42`), so its diarization goes through the VLM by design.
- Not a `bench` / `consolidation` / `m3_adaptors` bug.

**Classification:** **StreamMeCo pristine-code rigidity** (unconfigurable hardcoded model), exposed by our configuration choice (user decision: no gemini-1.5-pro at all).

**Fix (applied, user-authorized pristine edit):** two-stage. First, `voice_processing.py:157` was made config-driven (`processing_config["diarization_model"]`, default `gemini-3.8-flash`). The user then decided path 1 should use the same ASR as path 2, so `diarize_audio` now calls the configured `processing_config["asr_provider"]` (deepgram, via a self-contained `transcribe_with_asr_provider` helper in pristine `voice_processing.py` — utterances → words normalization mirrors `m3_adaptors/chat_api_ext.py`) whenever `asr_provider` is set; `diarization_model` remains only as the VLM fallback when no `asr_provider` is configured. No gemini-1.5-pro anywhere.

**Latent siblings (same pattern, NOT hit by our runs, no action needed now):**
- `StreamMeCo/mmagent/retrieve.py:378` `plan_model = "gemini-1.5-pro-002"` — only used when `answer_with_retrieval` is called with `video_clip_base64`; bench QA (`bench/qa.py:73-78`) never passes it and routes through the registered chat alias instead.
- `StreamMeCo/mmagent/memory_processing.py:175` `model = "gemini-1.5"` — module not used by either bench path (both use `memory_processing_qwen`).
- `StreamMeCo/m3_agent/control.py:38` `gpt_model = "gpt-4o-2024-11-20"` — not used by bench.

---

## Error 3 — path 2: `KeyError: 'video_descriptions'`

**Symptom (clip 1, memory-generation stage):**

```
m3_adaptors/patches/memorization.py:62 generate_memories
  → mmagent/memory_processing_qwen.py:180 generate_memories
    → memory_processing_qwen.py:171 generate_all_memories
      episodic_memories = memories[epi_key]      # epi_key = "video_descriptions"
KeyError: 'video_descriptions'
```

**Root cause — prompt/parser key mismatch in pristine StreamMeCo.** Reproduced standalone on the GPU (same prompt, same clip, same gemini-3.8-flash backend); the model's verbatim response:

```json
{
  "video_description": [],
  "high_level_conclusions": []
}
```

The model did exactly what the prompt asks: `prompt_generate_memory_with_ids_sft` (`StreamMeCo/mmagent/prompts.py:316-328`) specifies the output object as `{"video_description": [...], "high_level_conclusions": [...]}` — **singular** `video_description`. But `generate_all_memories` (`memory_processing_qwen.py:153-171`) reads `epi_key = "video_descriptions"` — **plural**. So `validate_and_fix_json` parses the response successfully, the retry loop accepts it (it only retries on unparseable `None`), and the dict lookup crashes.

Corroboration: the consolidation/m3 side consistently uses the singular form (`m3_adaptors/patches/memorization.py:69` keys its audit as `video_description`), so the pristine parser's plural key is the odd one out.

**Classification:** **StreamMeCo pristine-code bug**. Not model misbehavior (gemini followed the prompt schema exactly), not our configuration. Any conforming backend model would crash this line.

**Fix (applied, user-authorized pristine edit):** `generate_all_memories` now normalizes key variants (`video_descriptions`/`video_description`, `high_level_conclusions`/`high_level_conclusion`) and treats a wrong-schema or non-dict parse exactly like the existing unparseable case — retry up to `MAX_RETRIES`, then fall back to empty memories. This also fixes a second latent crash in the same function: an empty model response was coerced to `"[]"`, which parsed to a *list* and would have raised `TypeError` at the dict lookup.

---

## Error 4 — `OSError: [Errno 36] File name too long` (diagnosis repro only)

**Symptom:** the standalone repro script for Error 3 crashed in `bench/backends.py:_media_url` → `Path(value).read_bytes()`.

**Root cause:** my repro passed the raw base64 string as the `video_base64/mp4` content; the real flow passes the **clip file path** (`clip_path`, see `m3_adaptors/patches/memorization.py:62-67` and pristine `m3_agent/memorization_memory_graphs.py:59`), which `_media_url` reads and inlines as a data URI. `chat_qwen.generate_messages` passes the content string through verbatim, and `to_openai_messages/_media_url` treats any string that is not a `data:`/`http(s):` URI as a filesystem path.

**Classification:** **not a product bug** — repro-script misuse. Recorded because it documents a footgun: with bench backends, the "video" content must be a path or URI, never raw base64.

---

## Error 5 — both paths: `FileNotFoundError: models/camplus/campplus_cn_common.pt`

**Symptom (both paths, on the first clip that contains speech):**

```
voice_processing.py (pristine, and m3_adaptors/patches/voice_processing.py)
  → _get_embedding_model
    → torch.load("models/camplus/campplus_cn_common.pt")
FileNotFoundError
```

**Root cause:** the CAM++ checkpoint on the GPU lives at
`/opt/streammeco/models/camplus/v1.0.0/campplus_cn_en_common.pt` and was wired
into the staged repo as a **GPU-only symlink**
(`StreamMeCo/models/camplus/campplus_cn_en_common.pt`). A repo re-sync done
with `rsync --delete` (to ship the error-1/2/3 fixes) deleted the symlink
because it does not exist in the local tree. Clip 1 of path 2 survived only
because it has no speech — CAM++ loads lazily on the first voice segment;
clip 2 had speech (deepgram succeeded, 857 ms, 1 segment) and died at the
embedding step.

**Classification:** **usage/deployment error** (sync procedure deleted
GPU-local wiring). Not a code bug — the lazy loader and the
`speaker_embedding_checkpoint` config behaved as designed.

**Fix (applied):** recreated the symlink and verified the checkpoint loads
(937 tensors). Syncs from now on must exclude `StreamMeCo/models/`.

## Error 6 — path 2 shutdown: `RuntimeError: consolidation shutdown requires retry: [('patch', 'string indices must be integers')]`

**Symptom:** after error 5 aborted the clip loop, the runner's `finally`
called `runtime.close()`, which ran a tail-flush consolidation on the 1-clip
graph; the proposer's patch failed to apply with `string indices must be
integers`, and `close()` raised, masking nothing (the original traceback was
already logged above it).

**Classification:** ~~unclassified, likely transient~~ → **resolved: same root
cause as error 9** (deterministic evidence-export contract mismatch, reproduced
at the real 1200 s boundary with a full 40-clip graph on 2026-09-21). The error
string was never about the sol patch format — the worker died in evidence
export before any LLM call.

## Error 7 — path 1 QA: `AttributeError: 'VideoGraph' object has no attribute 'reverse_character_mappings'`

**Symptom (path 1, first QA question after clip 9):**

```
bench/qa.py:73 answer_with_retrieval
  → mmagent/retrieve.py:339 search
    → retrieve.py:38 translate
      if entity_str in video_graph.reverse_character_mappings.keys()
AttributeError
```

**Root cause:** `VideoGraph.reverse_character_mappings` is created only by
`refresh_equivalences()` (`videograph.py:511,537`). Pristine
`process_segment` never calls it — the pristine entry point
`streaming_process_video` calls it once after all clips, and the bench runner
uses per-clip `process_segment` instead. Path 2 is immune because the m3
patch calls `refresh_equivalences()` after every clip
(`m3_adaptors/patches/memorization.py:124`). So this is the pristine
single-shot-driver assumption meeting bench's online per-clip loop, surfacing
at the first QA retrieval on path 1.

**Classification:** **integration gap between pristine StreamMeCo and bench
usage** — not a bug in either in isolation (pristine works via its own
driver; bench path 2 works via the m3 patch).

**Fix (applied, bench-side, no pristine edit):** `bench/runner.py` calls
`graph.refresh_equivalences()` after each clip on path 1, mirroring the m3
per-clip semantics. (A pristine-edit variant in `memorization_memory_graphs.py`
was tried and reverted at the user's direction — the fix lives in the bench
runner.)

Related mitigation (same QA-stall investigation): pristine `chat_api.get_response`/`get_response_with_retry`/`parallel_get_response` defaulted to `timeout=30`, which 302.ai frequently exceeds on long retrieval prompts — each timeout cost a full retry round and made one QA question take ~14 min wall. Defaults raised to 120 s.

## Error 8 — stale duplicate writers, path 2 OOM-starved (operator error)

**Symptom:** path 2's log ended mid-clip with no traceback and the process was
gone; meanwhile `ps` showed **two** path-1 python processes running
simultaneously (~15 GB RSS each on a 56 GB box).

**Root cause:** the launch commands chained `source env && cd dir && nohup
python ... & echo PID=$!`. Because `&` binds the whole `&&` list, `$!` was the
subshell's PID, not python's (python was `$!`+3). Later `kill <pid>` calls
killed the wrapper shells and reported success, but the actual python
processes kept running: two superseded path-1 instances survived their
"relaunches", double-writing the output dir (their writes went to deleted
inodes after `rm -rf`, so the final path-1 output was verified clean: 40
unique ordered clips) and together OOM-starved path 2, which the kernel
killed silently.

**Classification:** **usage/operator error** — no code bug anywhere.

**Fix (applied):** killed the real python PIDs; relaunched with
`;`-separated commands so `$!` is the python PID (verified: `ps -p $!` shows
the python command line). Path 2 restarted from scratch (the runner has no
resume; append-mode outputs require a clean output dir).

## Error 9 — path 2 consolidation: `RuntimeError: scheduled consolidation failed: [('patch', 'string indices must be integers')]` (deterministic)

**Symptom (2026-09-21 run, QA disabled):** construction completed cleanly —
40/40 clips, zero tracebacks — then at the t=1200.02 s boundary
`runtime.consolidate_until()` raised `scheduled consolidation failed:
[('patch', 'string indices must be integers')]`, and the runner's `finally`
re-raised it from `runtime.close()` as `consolidation shutdown requires retry`.
No `consolidation.jsonl`, no `graph_final.pkl`, no `graphs/` replay artifacts,
and **no `snapshot_*` job directory** — the worker (`consolidation/runtime_io.py:44`)
died before creating it, so **gpt-5.6-sol was never called and no sol prompt
exists**.

**Root cause (reproduced offline against the preserved snapshot):**
`bench/config.py:104-108` (`load_dataset`) copies the dataset manifest's
top-level `"source"` into every plan event. In
`bench/configs/datasets/egolife_jake_20min.json` that field is a documentation
**string** (`"EgoLife DAY1_A1_JAKE (egolifeqa-imu-benchmark); stream t0 = …"`),
but `m3_adaptors/evidence.py:35-50` (`export_consolidation_evidence`) expects
`event["source"]` to be a **dict** with `path` / `start_s` / `source_offset_s`
keys — `source["path"]` on a string throws exactly
`TypeError: string indices must be integers`. The runtime's worker thread
captures only the message string (`runtime.py:305`), which is why the log
showed no original traceback.

**Classification:** **interface/contract mismatch** between bench's manifest
schema (ambiguous `"source"` — documentation string at manifest level) and the
consolidation evidence contract (per-event source dict). Not the model, not
302.ai, not the consolidation engine. This was also the true cause of error 6.

**Fix (applied):** `bench/config.py:load_dataset` now builds a per-clip source
dict for each plan event. Verified by the successful resumed consolidation —
see "Fixes applied (bench-side, 2026-09-21)".

**State preservation (no reconstruction needed after fix):** the exact
pre-consolidation graph is preserved as
`bench/results/jake_path2_pending_snapshot/snapshot.pkl` (=
`…/jake_path2_gemini_sol_20min/consolidation/audits/clip_72180_graph.pkl`,
md5 `ae9e07e3…`, both on GPU and locally) with `snapshot.json` metadata
(`graph_version=40`, `cutoff_clip_id=72180`, `cutoff_timestamp=1200.02`) plus
the run config and dataset manifest. The full crashed run dir is kept on GPU
and mirrored locally as `bench/results/jake_path2_gemini_sol_20min_crashed/`
(all 40 clip audits + intermediate voice/face JSONs that evidence export
needs). Resume notes: the pickle was written inside `segment()` exit, so
`last_completed_clip_id`/`last_completed_timestamp` read one clip stale
(72150/1170.02) and `_consolidation_runtime` must be reset to `None` before
`attach_online`.

## Error 10 — latent: `graph.segment_times` never populated (next crash after error 9)

**Symptom:** not yet hit at runtime — found while reproducing error 9. The
preserved pre-consolidation graph has **no `segment_times` attribute**.

**Root cause:** `m3_adaptors/evidence.py:141` reads
`snapshot.graph.segment_times[clip_id][1]` (and `m3_adaptors/online_state.py:153`
reads it too), but nothing in pristine StreamMeCo or the m3_adaptors writer
chain ever sets it — the only place it appears is the adaptor test fixture
(`m3_adaptors/tests/test_online_services.py:78`, a hand-built fake graph).
Semantically it should map `clip_id → (start_s, end_s)`.

**Classification:** **m3_adaptors code gap** (missing graph state population),
masked by error 9.

**Fix (applied):** `bench/runner.py` populates `graph.segment_times[clip_id]`
per committed clip; `bench/resume.py` restores it from the dataset plan when
resuming from a snapshot. Verified by the successful consolidation run (see
"Fixes applied (bench-side, 2026-09-21)"). Note: the first version of this
fix populated the entry **after** `process_segment`, which missed the boundary
clip in the segment-exit snapshot — see error 11.

## Error 11 — path 2 (day1 first-20-min window): `RuntimeError: scheduled consolidation failed: [('patch', '41370')]`

**Symptom:** new-window run (t0 = DAY1 11:09:42.08, 41 clips): construction
clean, 41/41 clips, zero tracebacks — then at the boundary
`consolidate_until(1217.96)` raised with worker error `('patch', '41370')`
(a `KeyError` str), re-raised by `close()`. No consolidation artifacts; sol
was never called.

**Root cause:** the error-10 fix added
`graph.segment_times[clip_id] = (start_s, end_s)` **after**
`process_segment(...)` returned. But the consolidation runtime takes its
pending snapshot (`deepcopy(graph)`) inside `segment()`'s exit path — i.e.
during `process_segment`, one statement earlier. The snapshot's
`segment_times` therefore ended at the previous clip, and evidence export's
`snapshot.graph.segment_times[41370]` (`m3_adaptors/evidence.py:141`) raised
`KeyError(41370)` for the memory nodes of the boundary clip itself.

**Classification:** **bench-side fix-ordering bug**, introduced by my error-10
fix — not consolidation/m3_adaptors/StreamMeCo code, not the data.

**Fix (applied):** populate `segment_times` **before** `process_segment` in
`bench/runner.py` (with a comment explaining why). The pre-consolidation graph
was preserved as `bench/results/jake_path2_pending_snapshot_day1/snapshot.pkl`
(graph_version=41, cutoff_clip_id=41370, cutoff_timestamp=1217.96) and the
consolidation resumed from it with `--resume-from`.

## Fixes applied (all user-authorized pristine edits)

1. `StreamMeCo/mmagent/utils/chat_qwen.py` — `qwen_omni_utils` import moved inside `get_response`; the package is now optional (only `memory_backend: qwen-local` needs it). The stopgap pip installs in `/opt/streammeco/.venv` were uninstalled.
2. `StreamMeCo/mmagent/voice_processing.py` — `diarize_audio` uses the configured `processing_config["asr_provider"]` (deepgram) when set, else falls back to the VLM named by `processing_config["diarization_model"]` (default `gemini-3.8-flash`); both keys added to `configs/processing_config.json`. No gemini-1.5-pro anywhere.
3. `StreamMeCo/mmagent/memory_processing_qwen.py` — `generate_all_memories` accepts singular/plural key variants; wrong-schema and non-dict responses retry then fall back to empty memories.

## Fixes applied (bench-side, 2026-09-21)

4. **Error 9** — `bench/config.py:load_dataset`: plan events now carry a per-clip source dict `{"path", "start_s", "source_offset_s"}` instead of the manifest's documentation string (which stays on `dataset["source"]` as metadata). Validated by exporting consolidation evidence from the preserved pre-consolidation snapshot locally: 298 memories, 40 segments, 2 voice observations.
5. **Error 10** — `bench/runner.py`: the runner populates `graph.segment_times[clip_id] = (start_s, end_s)` after each committed clip (both paths; harmless on path 1). `bench/resume.py` also populates it from the plan when resuming from a snapshot.
6. **New resume driver** — `bench/resume.py` + `python -m bench <run-config> --resume-from <snapshot_dir>`: re-attaches the consolidation runtime to a preserved pre-consolidation `snapshot.pkl` (restoring the post-segment bookkeeping the in-`segment()` pickle lacks), then runs only the consolidation barrier, graph-replay capture, and close — no re-construction.
7. **Verified end-to-end on the GPU (2026-09-21):** resumed the crashed jake path-2 run from `jake_path2_pending_snapshot/`; gpt-5.6-sol via 302.ai accepted the evidence packet (20,767 prompt tokens, one call, 19.1 s LLM / 20.7 s total wall) and returned a valid patch — **5 accepted / 0 rejected decisions** (e.g. `voice_0` → person_0 "Tanjiro Kamado"). Outputs: `consolidation.jsonl` (status `accepted`), `graph_final.pkl`, `graphs/before_first_consolidation.{md,pkl}` + `after_first_consolidation.{md,pkl}` + `README.md` (300 nodes, 8 edges, 2 character mappings, cutoff 1200.02 s). Exact sol prompt: `consolidation/snapshot_40/llm_input.json`; raw response: `llm_response.json` / `llm_output.txt`.
8. **Hyperstack hibernation/restore API notes:** hibernate is `GET /v1/core/virtual-machines/{id}/hibernate` (not POST); restore is `GET .../hibernate-restore`; hibernation dropped the floating IP even though the VM record showed no hibernation constraint — re-attach with `POST /v1/core/virtual-machines/{id}/attach-floatingip` and expect a **new** IP (185.216.22.119 → 38.128.233.166; `~/.ssh/config` host `streammeco-gpu` updated accordingly).
