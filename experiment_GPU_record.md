# Remote experiment ledger

## Jake Day1 mandatory MOSS consolidation from saved snapshot — 2026-09-21

- **Status:** completed
- **Provider:** Hyperstack
- **Instance ID:** 1042997 (`streammeco-a6000-20260914`)
- **Purpose:** Restore old-style MOSS speaker evidence to the first 20-minute Jake Day1 Sol input and check its effect on character consolidation.
- **Method:** Resume the checksum-verified pre-consolidation graph from the 41-clip MAI/Gemini run. Feed the exact 0–1217.96 s audio window to pinned local MOSS and include its full segment timeline, per-utterance overlap alignments, and top-five CAM++ evidence in the Sol request. Preserve the first clip's recorded 17.63 s media followed by the explicit 0.29 s source gap. No QA or construction rerun.
- **Environment:** `/opt/streammeco/.venv/bin/python`, RTX A6000, CUDA tensor preflight passed; 41 source MP4s and pinned MOSS checkpoint/repository verified. Snapshot SHA-256 `9dd7694476cc81d7a8202f6a14ed3eb59894da2c6daa0d00ac0c1701366c8d67`.
- **Command:** `bash bench/launches/jake_mai_moss_consolidation_v3_20260921.sh` in `tmux` session `jake-mai-moss-consolidation-v3-20260921`.
- **Results:** Exit status `0`; pinned MOSS completed the exact 0–1217.96 s window in 234.29 s, producing 290 segments with four local speaker labels. The Sol packet contains 200 utterance alignments (194 with nonzero segment overlap), four cross-voice MOSS merge hypotheses, and the explicit first-clip source gap. Packet size 418,645/524,288 bytes. One `gpt-5.6-sol` call ended normally; 10 accepted decisions, zero rejected. Four character mappings contain voices after consolidation, down from 82 populated mappings before; 12 additional empty graph mapping rows remain. Node count stayed 501 because this was a consolidation-only resume.
- **Artifacts:** remote and local `bench/results/jake_path2_gemini_mai_moss_day1_first20min_consolidation_v3_20260921/`, including `resume.log`, `exit_status`, `consolidation/snapshot_41/moss/`, `prompt_packet.json`, `llm_input.json`, `llm_response.json`, and before/after graphs. Local copies of the exact request (`c3725512556b9a273e574b250d0c39cf86a830f0a2ef0b2ead2c919a47775bf2`), MOSS window (`90737fc200bba9d1a176348e0924c77e38440cd668c3bda55f0ccc5bfef5f849`), and after graph Markdown (`c3fcd9cd12e9c53c45294a07657471d8cb73c4555e03627ebba681831e4f90f9`) matched remote SHA-256 hashes.
- **Fidelity audit:** The configured 41 clips are the first 41 chronologically named local Jake Day1 MP4s, and every local/GPU file hash matches (ordered SHA-256 manifest `11d9133de7d5f2b73da20b42cd70a6558450c6dfc0c9ca677bfc3b01e4f138f7`). The saved 1,217.96 s WAV reproduces byte for byte from those MP4s. The upstream MOSS parser reproduces all saved segments; the Sol request reproduces from the saved evidence, and its returned message equals the accepted patch. Before/after Markdown reproduces from the graph pickles, and the final reviewable graph equals the after pickle. The source transcript distinguishes six named speakers in this interval; MOSS has four local labels and the final graph likely merges Katrina with Lucia and Tasha with Alice. See `bench/reports/jake_moss_mandatory_v3.md`. Treat four populated mappings as the model's output, not verified truth.

## Path 2 raw ASR reproduction — 2026-09-21

- **Status:** running
- **Provider:** Hyperstack
- **Instance ID:** 1042997 (`streammeco-a6000-20260914`)
- **Purpose:** Locate the loss of speech observations before voice-node construction.
- **Method:** Reproduce MoviePy PCM decode for three exact new-run clips and the old first clip; compare Deepgram `latest` and `v2`; save raw responses and normalized filtering decisions. CPU decoding and remote ASR; no GPU inference beyond capability preflight.
- **Environment:** `/opt/streammeco/.venv/bin/python`, MoviePy 2.1.2; RTX A6000 CUDA tensor preflight passed. Remote repository is a filesystem mirror without `.git`; script copied from local `bench/diagnostics/path2_asr_probe.py`.
- **Entry point:** remote `bench/path2_asr_probe.py`, repository `/opt/streammeco/run/StreamMeCo-consolidation`.
- **Session:** `path2-asr-diagnostic-20260921`
- **Artifacts:** remote `bench/results/path2_voice_diagnostic/`, including `probe.log`, `exit_status`, raw ASR JSON and audio WAVs; local mirror to follow.
- **Results:** pending.

## Jake Day1 ASR language-mode diagnosis — 2026-09-21

- **Status:** completed
- **Provider:** Hyperstack
- **Instance ID:** 1042997 (`streammeco-a6000-20260914`)
- **Purpose:** Diagnose low Mandarin speech yield and assess a bilingual ASR candidate before rebuilding the first-41-clip graph.
- **Method:** Paired Deepgram Nova-3 `multi`, `zh`, and `detect_language=zh&en` calls on exact GPU MP4 audio; separate `v2`/`latest` and original/reencoded-media checks; compare saved Deepgram and MAI-Transcribe-2 outputs for the 41 source clips.
- **Environment:** `/opt/streammeco/.venv/bin/python`, MoviePy 2.1.2; RTX A6000 CUDA tensor preflight passed. Audio decoding and remote ASR requests ran on the VM; the GPU itself was not used for ASR inference.
- **Results:** `multi` returned no words on three paired Mandarin clips; `zh` returned Mandarin utterances on all three. `v2` versus `latest` did not resolve the empty `multi` result. Restricted `zh`/`en` detection chose low-confidence English and returned no speech on the first two clips. Archived 41-clip paired outputs: Deepgram speech on 12/41, MAI-Transcribe-2 on 40/41. No ground-truth WER was calculated.
- **Artifacts:** local `bench/reports/jake_day1_asr_error_diagnosis.md`; archived paired outputs under `/Users/nijiachen/StreamMeCo/benchmark/egolife_m3_jake_day1/provenance/raw/qwen_thinking/results/`.
- **Notes:** The short API probes preceded a durable `tmux` launch and their full raw provider responses were not saved; only their summarized outputs are available in this session. No new 41-clip ASR sweep was launched because the archived paired run already supplied session-wide coverage data.

## Jake Day1 first 41 clips — MAI audio and timestamped Gemini frames — 2026-09-21

- **Status:** completed (faithful GPU consolidation resume after exact prompt review)
- **Provider:** Hyperstack
- **Instance ID:** 1042997 (`streammeco-a6000-20260914`)
- **Purpose:** Rebuild the online Path 2 memory graph and 1,200-second consolidation after correcting the Mandarin/English ASR and Gemini visual input.
- **Method:** Stream the original 41 Jake Day1 MP4s in chronological order, 11:09:42.08 through 11:30:00.04 wall clock, with no QA or MOSS. MAI-Transcribe-2 via OpenRouter supplies timed diarized speech; Gemini 3.8 Flash via 302.ai receives timestamped JPEG `image_url` parts sampled at 2 frames/s; GPT-5.6 Sol proposes consolidation.
- **Environment:** `/opt/streammeco/.venv/bin/python` (Python 3.10), CUDA 12.4, RTX A6000; source and configs staged to `/opt/streammeco/run/StreamMeCo-consolidation`. Remote validation confirmed 41 files, active MAI alias, QA disabled, writer imports, CUDA tensor operation, and a fresh output directory. Staged `bench/backends.py` SHA-256: `a393c0c04d0636f204d0e6454e034d9a8432d73604eb1009a34ae31beb790823`.
- **Command:** `bash bench/launches/jake_mai_frames_day1_first20min_20260921.sh` from the staged repository, in `tmux` session `jake-mai-frames-20260921-1817`. First recovery was interrupted for prompt review: `bash bench/launches/jake_mai_frames_resume_20260921.sh`, `tmux` session `jake-mai-faithful-resume-20260921`, log `bench/results/jake_path2_gemini_mai_frames_day1_first20min_20260921/resume_20260921.log`. Current retry: `bash bench/launches/jake_mai_frames_resume_retry_20260921.sh`, `tmux` session `jake-mai-sol-retry-20260921`, log `bench/results/jake_path2_gemini_mai_frames_day1_first20min_20260921/resume_retry_20260921.log`.
- **Results:** 41/41 clips constructed with no skipped clips and 41 clip audits. Initial process exit status `1`: the final consolidation packet was 1,100,088 bytes against a 524,288-byte limit (assignment evidence 917,667 bytes). First resume with the existing `CONSOLIDATION_PACKET_BYTES=2097152` override was interrupted for prompt review (exit `143`). The final GPU retry used the same prompt (SHA-256 `fa81b97c63099a2d5130fc43b7e50a50f1b0141e57033d1ca19a18e26613fedc`) and exited `0`. The runtime accepted 40 decisions and rejected 2 name decisions for lacking semantic/event text support; its consolidation log reports `status: accepted`, one LLM call, cutoff 1217.96 s. Final graph has 501 nodes, 248 directed edges, 81 character mappings, graph version 42, and last consolidated timestamp 1217.96 s. The post-consolidation replay graph equals the final graph in its reviewable state.
- **Artifacts:** remote `bench/results/jake_path2_gemini_mai_frames_day1_first20min_20260921/`; checksum-verified local copy at the same relative path, including `graph_final.pkl`, `consolidation.jsonl`, `graphs/before_first_consolidation.md`, `graphs/after_first_consolidation.md`, and `consolidation/snapshot_41/{llm_input.json,llm_response.json,patch.json,execution.json}`. The before graph has 501 nodes, 248 directed edges, 82 character mappings. Exact local Sol request: `consolidation/snapshot_41/llm_input.json`; system and user messages extracted to `sol_system_prompt_exact.txt` and `sol_user_prompt_exact.json`. Local run config `bench/configs/runs/jake_path2_gemini_mai_frames_day1_first20min.json`.
- **VM lifecycle:** hibernation completed after checksum verification; restored the same VM for resume. A second hibernation was mistakenly requested during prompt review. The VM was restored again, confirmed `ACTIVE`/`RUNNING`, given a new public IP, and verified reachable by SSH. It remains running after the completed consolidation, per the user's correction.
- **Subsequent prompt revision:** Updated `consolidation/prompt_packet.py` (top five CAM++ candidates) and `consolidation/prompts/system.md` were staged on the same VM. From the unchanged `snapshot_41/evidence.json`, the current code generated `consolidation/request_top5_20260921/llm_input.json` and exact message files. User message: 332,534 bytes against the default 524,288-byte limit; request SHA-256 `96c7395f9810881f68e55b089a204c239d814d163311f93f30b192aac8aeea04`. This is an input artifact only; the completed graph above came from the earlier Sol request.

## Jake Day1 revised Sol consolidation from saved snapshot — 2026-09-21

- **Status:** completed
- **Provider:** Hyperstack
- **Instance ID:** 1042997 (`streammeco-a6000-20260914`)
- **Purpose:** Re-run consolidation on the unchanged 41-clip Jake Day1 pre-consolidation graph with the user's revised top-five CAM++ prompt code; save a distinct LLM input and post-consolidation graph.
- **Method:** Stage `consolidation/prompt_packet.py` and `consolidation/prompts/system.md`; resume the original `snapshot_41/snapshot.pkl` into a new result directory with the original 41 clip audits copied. Same GPT-5.6 Sol backend, no MOSS or QA. The revised packet is 332,534 bytes, below the unmodified 524,288-byte guard; no packet-limit override is set.
- **Environment:** `/opt/streammeco/.venv/bin/python`, RTX A6000 and CUDA tensor check passed. Staged prompt code SHA-256: `898f6b7eb50066efd5134bc37de17dbca587f07bac5c34e144bbfecdc101bca1`; system prompt SHA-256: `af8699cc3d4b25da2dd3ae3129b29b80b9ab6bab1db29314cd249c6e7f5df5c1`. Source snapshot SHA-256: `9dd7694476cc81d7a8202f6a14ed3eb59894da2c6daa0d00ac0c1701366c8d67`. Local prompt-packet tests passed (15); remote virtualenv lacks pytest.
- **Command:** `bash bench/launches/jake_mai_frames_consolidation_v2_20260921.sh` from the staged repository in `tmux` session `jake-mai-sol-consolidation-v2-20260921`.
- **Results:** Exit status `0`; one Sol call, cutoff 1217.96 s, runtime status `accepted`, 34 accepted decisions and 0 rejected. Pre graph: 501 nodes, 248 directed edges, 82 character mappings, graph version 41. Post/final graph: 501 nodes, 248 directed edges, 80 character mappings, graph version 42, last consolidated timestamp 1217.96 s. The post replay graph and final graph have identical reviewable state. The exact new request SHA-256 is `5f5e032e61764b5a324543d090c65460a3ad994a30d155e5e8a63c577f7333be`; its 332,534-byte user message and system message were verified against the current code and saved packet. The request differs from the earlier top-five preview only in the updated system wording about absent candidates.
- **Artifacts:** remote and checksum-verified local `bench/results/jake_path2_gemini_mai_frames_day1_first20min_consolidation_v2_20260921/`; source snapshot in original result directory. Key files: `consolidation/snapshot_41/{llm_input.json,sol_system_prompt_exact.txt,sol_user_prompt_exact.json,llm_response.json,patch.json,execution.json}`, `graphs/{before_first_consolidation.md,after_first_consolidation.md}`, `graph_final.pkl`, `consolidation.jsonl`, `resume.log`, and `exit_status`. The VM remains active per the user's instruction.

## Jake Day1 new Path2 first 30 minutes, GPT-6 Sol — 2026-09-24

- **Status:** cancelled by user after 22 completed clips; process terminated with exit status 143.
- **Provider / instance:** Hyperstack, `1042997` (`streammeco-a6000-20260914`), private IP `10.0.0.37`; SSH verified at `38.80.122.119`.
- **Purpose:** Exercise window-scoped aliases, temporal handoff replacement, delta construction, and canonical transcript labels in the new Path2 pipeline.
- **Method:** Fresh construction of 61 chronological full clips (0–1817.96 s); first 41 manifest entries identical to the v3 reference. Preserve its Gemini 3.8 Flash / 302.ai, MAI-Transcribe-2 / OpenRouter, embeddings, CAM++, Buffalo-L and pinned MOSS settings. Change consolidation model only to `gpt-6-sol` / 302.ai; period 1200 s, plus normal end-of-stream tail consolidation; QA disabled.
- **Environment:** Isolated staging root `/opt/streammeco/run/StreamMeCo-consolidation-v5-20260924`; existing remote StreamMeCo files copied unchanged. Updated local `m3_adaptors`, `consolidation`, and `bench` staged with a 126-file SHA-256 manifest. `/opt/streammeco/.venv/bin/python`, Torch 2.6.0+cu124, RTX A6000; CUDA tensor operation, writer imports, face/CAM++/MOSS dependencies and all videos verified. Local regression suite: 138 passed.
- **Launch:** `bash bench/launches/jake_mai_moss_first30min_v5_20260924.sh`, tmux session `jake-path2-v5-30min-20260924`. Recording wrapper delegates to the normal bench entry point, preserving model inputs and saving full gzip requests plus readable construction text. Script records process exit status and durable `run.log`.
- **Artifacts:** Remote `bench/results/jake_path2_gemini_mai_moss_day1_first30min_consolidation_v5_20260924/{preflight.json,code_manifest.json}` relative to staging root. Planned automatic graph replay pairs, consolidation requests/responses, clip audits, construction calls and final graph under that directory.
- **Results:** Initial preflight rejected the old v3 credential (401); user supplied a replacement, stored in a protected remote credential file. Both Gemini 3.8 Flash and GPT-6 Sol routes now pass JSON-mode API checks and return the requested model IDs. First three clips (40182, 40200, 40230) completed: episodic/semantic counts 5/1, 5/2, 3/2; generated output and audit files verified, no manual overrides. Exact first request confirms delta instructions, authoritative identities, 36 image parts, voice provenance, and no initial temporal handoff block. Monitoring stopped at this requested checkpoint; full run remains running and completion is not yet verified.
- **Monitoring:** User explicitly requested monitoring only until the first three clips construct successfully, then leaving tmux running; this overrides full-run monitoring from the GPU experiment skill. Do not report run completion at that checkpoint.

## Jake Day1 tightened Path2 prompts, v6 first 30 minutes — 2026-09-24

- **Status:** cancelled by user after four completed clips; verified exit status 143.
- **Provider / instance:** Hyperstack `1042997` (`streammeco-a6000-20260914`), RTX A6000, private IP `10.0.0.37`, SSH `38.80.122.119`.
- **Purpose:** Restart from the first clip using the approved shorter construction instructions and structured high-level consolidation handoff.
- **Method:** Fresh 61-clip construction (0–1817.96 s), same dataset and Gemini 3.8 Flash / 302.ai, GPT-6 Sol / 302.ai, MAI-Transcribe-2 / OpenRouter, embeddings, CAM++, Buffalo-L and pinned MOSS configuration as v5. Consolidation period 1200 s plus normal final tail; no QA or prior graph reuse. User cancelled v5 after 22 clips; its artifacts remain untouched.
- **Environment:** Isolated root `/opt/streammeco/run/StreamMeCo-consolidation-v6-20260924`; original remote StreamMeCo files copied unchanged; updated adaptors, consolidation and bench code checksum-verified. Python `/opt/streammeco/.venv/bin/python`, Torch 2.6.0+cu124, GPU tensor test and writer imports passed; both revised prompts verified in the loaded runtime. Same protected remote credential file as the successful v5 launch.
- **Launch:** `bash bench/launches/jake_mai_moss_first30min_v6_20260924.sh`; tmux session `jake-path2-v6-30min-20260924`. Exact construction requests saved through the existing recording wrapper; automatic graph replays and consolidation requests/responses enabled.
- **Artifacts:** Remote `bench/results/jake_path2_gemini_mai_moss_day1_first30min_consolidation_v6_20260924/` relative to v6 staging root. Durable `run.log`, `exit_status` on termination, preflight/code manifest and exact prompt text files; construction calls, clip audits, graph replays and final graph as produced.
- **Results:** First three clips verified (40182, 40200, 40230): episodic/semantic counts 6/2, 4/2, 4/2; no manual overrides or fatal log markers. Four clips were complete at the checkpoint check; process remains running. Monitoring stopped as requested; full completion not yet verified. A subsequent local prompt-only edit adds an explicit `Jake <voice_489>` example; it is not hot-loaded into this run.
- **Monitoring:** Stop monitoring after three successful constructions and leave tmux running, per the user's instruction.

## Jake Day1 explicit named-speaker provenance, v7 first 30 minutes — 2026-09-24

- **Status:** cancelled by user after three completed clips; verified exit status 143.
- **Provider / instance:** Hyperstack `1042997` (`streammeco-a6000-20260914`), RTX A6000, private IP `10.0.0.37`, SSH `38.80.122.119`.
- **Purpose:** Fresh rerun with all approved prompt changes, now explicitly requiring `Jake <voice_489>` instead of a name-only label; underlying identity nodes remain unchanged.
- **Method:** Same 61 clips (0–1817.96 s), Gemini 3.8 Flash and GPT-6 Sol through 302.ai, MAI-Transcribe-2 and embeddings through OpenRouter, CAM++, Buffalo-L, pinned MOSS; consolidation every 1200 s plus final tail, no QA. No previous graph or constructed memories reused. v6 cancelled at the user's request; its artifacts preserved.
- **Environment:** Isolated root `/opt/streammeco/run/StreamMeCo-consolidation-v7-20260924`; updated local bench/adaptors/consolidation with SHA-256 manifest; remote StreamMeCo source/service files copied unchanged. `/opt/streammeco/.venv/bin/python`; GPU tensor, writer imports, all media and checkpoints, and loaded revised prompts verified. Credentials sourced from the existing protected GPU file.
- **Launch:** `bash bench/launches/jake_mai_moss_first30min_v7_20260924.sh`; tmux `jake-path2-v7-30min-20260924`.
- **Artifacts:** Remote `bench/results/jake_path2_gemini_mai_moss_day1_first30min_consolidation_v7_20260924/` under the v7 staging root: durable `run.log`, eventual `exit_status`, exact prompts, code manifest/preflight, recorded construction calls, audits, consolidation requests/responses, automatic graph replays and final graph.
- **Results:** User requested stopping to revise the speaker-label instruction. Process 15870 terminated with SIGTERM; exit status 143 and three completed construction records verified. Partial artifacts preserved; no replacement run launched.
- **Monitoring:** Stop after three successful constructions and leave tmux running, per user instruction.

## Jake Day1 conditional speaker labeling, v8 first 30 minutes — 2026-09-24

- **Status:** cancelled after 48 completed clips; exit status 143 verified.
- **Provider / instance:** Hyperstack `1042997` (`streammeco-a6000-20260914`), private IP `10.0.0.37`, SSH `38.80.122.119`, RTX A6000.
- **Purpose:** Fresh rerun with the approved short conditional instruction to retain both mapped name and supplied voice node in the speaker label.
- **Method:** Same 61 clips, 0–1817.96 s; Gemini 3.8 Flash and GPT-6 Sol through 302.ai; unchanged MAI/OpenRouter, embeddings, CAM++, Buffalo-L and pinned MOSS configuration. Consolidation every 1200 s plus final tail; no QA or previous graph reuse.
- **Environment:** `/opt/streammeco/run/StreamMeCo-consolidation-v8-20260924`; StreamMeCo copied unchanged from v7. All 136 staged files checksum-verified; all clip/model paths, pinned MOSS revision, GPU tensor operation and writer imports verified. Python `/opt/streammeco/.venv/bin/python`, Torch 2.6.0+cu124. Loaded construction prompt confirmed. Local window-context tests: 16 passed.
- **Launch:** `bash bench/launches/jake_mai_moss_first30min_v8_20260924.sh`, tmux `jake-path2-v8-30min-20260924`.
- **Artifacts:** Remote `bench/results/jake_path2_gemini_mai_moss_day1_first30min_consolidation_v8_20260924/` under the v8 staging root; durable `run.log`, eventual `exit_status`, preflight and code manifest, exact construction requests/responses, audits, consolidation outputs and graph replays.
- **Results:** First three clips (40182, 40200, 40230) successfully constructed, with episodic/semantic counts 4/1, 4/2, 4/1. No manual overrides or fatal log markers. Saved first Gemini request verified to contain the corrected conditional speaker-label instruction. Monitoring stopped at the requested milestone; run remains active and full completion is unverified.
- **Monitoring:** User explicitly requested stopping monitoring after three successful clip constructions, leaving tmux running; full completion will remain unverified.

### Cleanup requested 2026-09-24

Deleted local v5/v6 result directories and remote isolated v5/v6/v7 staging roots with their partial results, per explicit user confirmation. No local v7 result directory existed. Preserved v3, local v4, and active v8; retained launch/config source files and this experiment history. Verified v8 has no symlink dependency on deleted staging roots. At progress check v8 had completed 21/61 clips (617.92 s), tmux active, no fatal log markers, first consolidation not yet reached.

### v8 result fetch — 2026-09-24

Fetched available remote v8 results into the matching local bench/results directory while the run remains active. Observed 46/61 completed clips (1367.94 s), no fatal log markers, and first consolidation accepted at 1217.96 s with 19 accepted / 3 rejected decisions. Before/after replay and core consolidation input/output/snapshot hashes verified against GPU (10 files). Full run and final tail consolidation remain pending.

### v8 prompt correction

Stopped v8 after the user identified the unintended global character inventory in construction requests. Local adaptor now omits that inventory and annotates only current transcript voice IDs with established names (`<voice_0> (Jake)`); unnamed/unmapped voices remain raw. Temporal handoff behavior is unchanged. Original v8 artifacts retained; no replacement run started.

## Jake Day1 corrected construction and handoff, v9 first 25 minutes — 2026-09-24

- **Status:** running.
- **Provider / instance:** Hyperstack `1042997` (`streammeco-a6000-20260914`), private IP `10.0.0.37`, SSH `38.80.122.119`, RTX A6000.
- **Purpose:** Rerun with no global identity inventory, inline `<voice_N> (Name)` annotation, Path1's original two-part task wording, concise temporal instructions, and valid handoffs accepted despite individual rejected decisions.
- **Method:** Fresh first 51 full clips, 0–1517.96 s (25m18s). Same Gemini 3.8 Flash and GPT-6 Sol through 302.ai; unchanged MAI/OpenRouter, embeddings, CAM++, Buffalo-L and MOSS revision `704aa4a9c304e8520be88901e0d1960158ef5b15`. Consolidation every 1200 s plus final tail; no QA or previous graph reuse. Handoff prompt asks for fewer than 1000 characters; acceptance limit remains 1800.
- **Environment:** `/opt/streammeco/run/StreamMeCo-consolidation-v9-20260924`; StreamMeCo copied unchanged from v8. All 140 staged files checksum-verified; clip/model paths, pinned MOSS revision, GPU tensor operation and writer imports passed. Loaded prompt, inline labeling, absence of global inventory and handoff acceptance fix verified. Python `/opt/streammeco/.venv/bin/python`, Torch 2.6.0+cu124. Relevant local tests: 55 passed for handoff fix; 16 passed after final prompt edit.
- **Launch:** `bash bench/launches/jake_mai_moss_first25min_v9_20260924.sh`, tmux `jake-path2-v9-25min-20260924`.
- **Artifacts:** Remote `bench/results/jake_path2_gemini_mai_moss_day1_first25min_consolidation_v9_20260924/` under v9 staging root; durable `run.log`, eventual `exit_status`, preflight/code manifest, exact construction requests and responses, audits, consolidation outputs and graph replays.
- **Results:** First three clips (40182, 40200, 40230) completed with episodic/semantic counts 7/2, 6/2, 6/2; no manual overrides or fatal log markers. Exact first request confirms restored two-part task wording and absence of global identity inventory. Run remains active; monitoring ended at the requested milestone.
- **Monitoring:** Stop monitoring after three successful clip constructions and leave tmux running, per user instruction. Full completion and live post-20-minute handoff injection remain unverified until later inspection.
