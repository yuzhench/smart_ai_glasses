# Jake Day1 first 41 clips: ASR diagnosis (2026-09-21)

## Finding

The principal speech loss is the Deepgram request's implicit `language=multi` setting. Nova-3's `multi` model excludes Mandarin, although Nova-3 supports Mandarin when explicitly requested as `zh`. The Jake session is predominantly Mandarin. The setting is inherited by both the older benchmark and the current pipeline because neither Deepgram configuration specifies `language` and both transcription clients default to `multi`.

Deepgram's [model/language table](https://developers.deepgram.com/docs/models-languages-overview) lists `multi` as English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese, Italian and Dutch; Mandarin is a separate `zh` option. Deepgram's [diarization documentation](https://developers.deepgram.com/docs/diarization) says `latest` currently resolves to `v2` for batch requests. Thus the old `v2` versus new `latest` setting is not the primary explanation.

## Pipeline and controlled checks

- Current: `bench/runner.py` processes each original MP4 in order. `StreamMeCo/mmagent/utils/video_processing.py` decodes its audio to 16 kHz PCM WAV. `m3_adaptors/patches/voice_processing.py` sends that WAV to one configured ASR provider, takes utterances (or falls back to words), rounds boundaries to whole seconds, filters durations below 2 seconds, slices audio, computes CAM++ speaker embeddings and inserts/matches voice nodes. The memory VLM receives retained transcript text and voice IDs.
- Old C1: `scripts/online_benchmark.py` reencodes exact event intervals before decoding WAV, runs the same Deepgram Nova-3 ASR selection and 2-second filter, then CAM++ voice mapping. Old C1 does not use TST; C2–C4 do. Old config pins `diarize_model=v2`; current uses `latest`. Both default to `language=multi` in their transcription client.
- GPU audio provenance: reextracting WAV from three original GPU MP4s with the current MoviePy pipeline reproduced the saved `audio_sha256` values. Audio is reaching the ASR request from the correct MP4s.
- Same first-clip WAV SHA-256 `c9877d9addbab0dec7bac6cb3db85488357085d9ce85df3326d2db3c4433d897`: Deepgram `multi` returned zero words/utterances, with either `diarize_model=v2` or `latest`. Deepgram `zh` returned four Mandarin utterances, including “然后一个秒表”, “时间戳对”, and “戳一下”. The archived MAI-Transcribe-2 cache for these exact bytes has three coherent Mandarin segments about the stopwatch and timestamp.
- On the second clip, `multi` again returned zero; `zh` returned eight Mandarin utterances covering the timer and discussion. On the 11:25:30 clip, `multi` returned zero; `zh` returned three Mandarin utterances. Reencoding the first and 11:25:30 clips and switching `v2`/`latest` still yielded zero under `multi`.
- A restricted `detect_language=zh&detect_language=en` call chose English for each of the first two Mandarin clips, with confidence 0.083 and 0.054, and returned zero utterances. This is not a reliable substitute for these short clips. Deepgram documents language detection as a choice of the dominant language per channel: https://developers.deepgram.com/docs/language-detection.

## Error-rate evidence and limits

| Source | First 41 source clips with any ASR transcript | Count |
| --- | ---: | ---: |
| Current Deepgram `multi`, after filtering | 4/41 | 8 retained segments |
| Older C1 Deepgram `multi`, through the 1,200-second graph (different event splits) | 14 nonempty events | 33 retained segments |
| Archived parallel Deepgram `multi`, same 41 source clips | 12/41 | 31 raw segments |
| Archived parallel MAI-Transcribe-2, same 41 source clips | 40/41 | 282 raw segments |

The archived parallel run has 42 events because it divides the 11:21:00 clip once. On the 40 MAI-positive source clips, Deepgram was empty on 28 (70% disagreement in speech presence). This is a detection disagreement rate, **not** word error rate. A defensible WER/CER requires a human-corrected transcript for representative audio. MAI is an independent ASR output, not ground truth, though its first two transcripts align with the audible/visual activity and with Deepgram's `zh` output.

The older C1 graph's visual events are well grounded because its Terra memory model used timestamped JPEG frames. Its ASR was not uniformly accurate: saved C1 transcripts include fragments such as “好意外好bie你”, “E tu menare?”, and “Ich war auch wieder mal.” The old graph's 17 voice nodes therefore do not establish good transcription quality.

## Choice for Mandarin and English

For this session, MAI-Transcribe-2 is the best evidenced single-provider candidate. [Microsoft documents](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe?pivots=ai-foundry) automatic multilingual operation with both `zh` and `en`, and [OpenRouter lists](https://openrouter.ai/microsoft/mai-transcribe-2) a transcription endpoint with speaker diarization and timestamps. The repository already contains an OpenRouter transcription adapter, but the current run config registers only Deepgram. Before rebuilding the graph, verify the currently available MAI endpoint and its speaker/timestamp outputs on representative clips; keep the original 41-clip order and save raw ASR responses and rejection reasons.

If staying with Deepgram for Mandarin-only clips, use explicit `language=zh`; do not use `multi` for this Chinese session. `zh` does not establish reliable English code-switching, and the restricted language-detection pilot failed.

## Source artifacts

- Current: `bench/results/jake_path2_gemini_sol_day1_first20min/consolidation/audits/clip_*_audit.json` and `intermediate/clip_*_voices.json`.
- Old C1: `/Users/nijiachen/StreamMeCo/benchmark/latest_benchmark/20260918_sol_astra/results_terra/jake/reusable_preprocessing/C1/intermediate/clip_*_voices.json`.
- Archived paired run: `/Users/nijiachen/StreamMeCo/benchmark/egolife_m3_jake_day1/provenance/raw/qwen_thinking/results/asr_calls.jsonl`, `asr_cache/`, and `segment_plan.json`.
- Controlled API probes were run on Hyperstack VM 1042997 against WAV bytes decoded from `/opt/streammeco/data/egolife_day1/`; their summarized outputs are recorded above. The provider's full raw responses were not saved, so the pilot is evidence of output counts and text, not a reproducible request archive.
