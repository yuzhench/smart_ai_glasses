# Path 2 pre-consolidation voice construction diagnosis

Compared the current Path 2 artifacts against the earlier Jake C3 Terra graph and the supplied older source tree. No inference rerun, graph mutation, or model/prompt change was performed.

## Exact observed bottleneck

The new graph has 2 voices, 210 events and 88 inferences. Its 40 per-clip audits and 40 voice cache files contain exactly 2 retained speech observations. All 40 ASR calls are recorded as successful, with no ASR failures and no voice-cache hits. These counts are after duration filtering and audio slicing, not raw provider counts.

- clip 71040: 00:15–00:17; created voice_0; no previous candidate.
- clip 71820: 00:00–00:02; transcript “Okay.”; candidate voice_0 cosine 0.4773711085172693, below threshold 0.6; created voice_217.
- The other 38 clips supplied no voice observations to the VLM.

Thus there is no observed over-merging: 2 retained segments became 2 identities. The earlier graph contains 25 voice nodes with 33 displayed speech entries. Displayed entries are not necessarily a complete historical ASR count.

## Actual construction differences

1. **Speaker encoder changed:** old C3 attaches TSTVoiceMapper (SpeechBrain ECAPA, 192 dimensions, windowed extraction); current bench/runner.py creates a graph without speaker_mapper, so Path 2 uses CAM++. Both use native mean-pairwise cosine match/update/create with threshold 0.6. The shared process_voices function and assign_voice implementation are unchanged relative to the supplied older source. Changing encoder affects similarity results but cannot recover observations that never reached embedding.
2. **Deepgram configuration changed:** archived benchmark config selects diarize_model=v2; current config selects latest. Both select nova-3 and default language=multi. Normalization and duration filtering are unchanged. The effect of this provider model change on segment counts is not established by the saved artifacts.
3. **Memory VLM changed:** old Terra uses timestamped JPEG frames at 2 fps, Responses JSON mode, medium reasoning, and max_output_tokens=8192. Current Gemini uses an MP4 video_url via chat completions. The base prompt_generate_memory_with_ids_sft is byte-identical. Terra appends a request for nonempty arrays and grounding in supplied chronological frames/face/voice evidence; the current path omits that suffix.
4. **Validation and diagnostics weakened:** old Terra validates nonempty lists of nonempty strings and stores exact request/response artifacts. Current generate_all_memories accepts empty arrays and falls back to empty memory after parse retries; its VLM audit is empty. Eight new clips have zero generated memories: 71010, 71070, 71220, 71280, 71970, 72000, 72120, 72150. The saved artifacts cannot distinguish intentional empty outputs from exhausted parsing retries. This affects event/inference construction, not the voice nodes already created upstream.

## What the VLM does with voices

Construction order is ASR → duration filter / audio slicing → speaker embedding → match or create voice nodes → VLM memory generation → event/inference nodes and links. The VLM receives existing voice IDs, timestamps and transcripts. Its prompt asks it to reference those IDs and infer face/voice equivalences; it does not create the voice nodes. The prompt is passed as user content, not a separate system message, in both paths.

## Remaining uncertainty and next diagnostic

The saved voice caches contain only surviving segments. Raw Deepgram responses, pre-filter counts, short-segment rejections and invalid-boundary rejections are not persisted. Therefore the exact cause of the low upstream yield cannot be assigned between provider output and filtering from this run alone. Do not tune the matching threshold or alter the VLM prompt as a remedy for that missing evidence.

The next controlled check should use the same audio bytes, record raw normalized ASR segments and each rejection reason, and compare v2 versus latest. To isolate the encoder change, replay identical retained segments through TST and CAM++ with fresh graphs. To compare full construction, hold media, ASR output and speaker mapper fixed while changing the VLM.

The media schedules also differ (old origin DAY1 11:09:42.08; new origin 19:43:30); this prevents aggregate node counts alone from establishing a code regression, but does not change the proven pipeline bottleneck above.

## Evidence locations

- New: bench/results/jake_path2_gemini_sol_20min/consolidation/audits/clip_*_audit.json
- New: bench/results/jake_path2_gemini_sol_20min/intermediate/clip_*_voices.json
- New: m3_adaptors/patches/voice_processing.py; m3_adaptors/speaker_mapping.py; bench/runner.py; bench/backends.py
- Old: /Users/nijiachen/StreamMeCo/StreamMeCo/mmagent/memory_processing_terra.py; mmagent/tst_mapper.py
- Old benchmark: /Users/nijiachen/StreamMeCo/benchmark/latest_benchmark/20260918_sol_astra/scripts/online_benchmark.py; configs/tst_jake.json; configs/processing_config.json
