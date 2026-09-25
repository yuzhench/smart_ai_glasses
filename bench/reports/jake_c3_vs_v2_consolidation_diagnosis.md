# Jake Day1: C3 versus revised consolidation diagnosis

## Conclusion

The node-count gap is created during online construction, before either consolidation. The identity-count gap is mainly explained by different speaker evidence and by what Sol chose to propose. Model routing is a legitimate uncontrolled variable, but the saved artifacts do not prove that 302.ai substituted a weaker model.

## Compared artifacts and scope

- Old: `/Users/nijiachen/StreamMeCo/benchmark/latest_benchmark/20260918_sol_astra/results_terra/jake/C3/results/terra/graphs/{before,after}_first_consolidation.md`, cutoff 1,200.00 s. The benchmark event plan has 45 media-bearing events and two gaps before that cutoff, drawn from 41 unique Jake MP4s. It splits some recordings at scheduled boundaries and processes only the first 12.08 s of the last MP4.
- New: `bench/results/jake_path2_gemini_mai_frames_day1_first20min_consolidation_v2_20260921/graphs/{before,after}_first_consolidation.md`, cutoff 1,217.96 s. It processes the same 41 MP4 names as 41 whole-clip construction calls, including the full final MP4.
- Speaker reference: `/Users/nijiachen/StreamMeCo/egolife_ori_transcript/A1_JAKE_DAY1_first_1_hour.md`. Its 11:09:42.08–11:30:00.04 span contains 521 labeled subtitle cues from six labels: Jake 231, Shure 106, Lucia 101, Alice 40, Katrina 35, Tasha 8. These are reference labels, not a perfect diarization metric for the pipeline's ASR segments.

## Node-count accounting

| State before first consolidation | Old C3 | New v2 | New minus old |
| --- | ---: | ---: | ---: |
| Episodic/event nodes | 614 | 313 | -301 |
| Semantic/inference nodes | 184 | 106 | -78 |
| Voice nodes | 25 | 82 | +57 |
| Total nodes | 823 | 501 | -322 |

The exact arithmetic is `-301 - 78 + 57 = -322`. Both before/after graph pairs preserve their node counts, so Sol consolidation did not delete these 322 nodes. Grouping both graphs by the same 41 MP4 basenames gives 798 old versus 419 new memory nodes; the old graph has more memories for **all 41** MP4s. Excluding the differently cut last MP4 gives 782 versus 409 memories across the first 40 files. The first MP4 alone contributes 16 episodic plus 4 semantic nodes in C3, versus 8 plus 3 now.

The new clip audits record 313 generated and 313 effective episodic items, plus 109 generated and 109 effective semantic items. Graph update retains 106 semantic nodes. Thus the large gap is upstream memory generation (plus the different event segmentation and some semantic merging), not a consolidation cleanup. Old construction used official GPT-5.6 Terra at medium reasoning; new construction used Gemini 3.8 Flash via 302.ai. The old plan's extra split events mean the memory count difference cannot be attributed solely to model choice without a matched construction ablation.

## Identity-count accounting

| Graph after first consolidation | Old C3 | New v2 |
| --- | ---: | ---: |
| Voice nodes entering consolidation | 25 | 82 |
| Character-map rows | 4 | 80 |
| Rows with no feature | 2 | 50 |
| Rows with at least one voice feature | 2 | 30 |
| Rows with multiple voice features | 2 | 2 |

The Markdown renderer calls every non-singleton row a “mapping joining multiple features,” including empty rows (`bench/graphreplay.py:177–179`). Thus its printed `52 mappings joining multiple features` in the new graph really means 50 empty and two multi-voice rows. The old printed four characters comprise only two occupied groups; one occupied row contains 21 voice IDs and the other contains two. The six labeled speakers in the reference transcript show why neither the old headline of four nor its two occupied groups should be treated as verified ground truth.

The new online CAM++ stage created 82 voice IDs from 200 MAI speech observations (118 matches, 82 new-voice decisions at threshold 0.6). Of the 81 new-voice decisions with candidate scores, 50 had a top score in `[0.5, 0.6)`. These are near-threshold fragments, not evidence that lowering the threshold blindly would be correct. The new Sol patch has 28 `assign_cluster` decisions, each assigning one voice to its own existing entity; two `merge_voice` decisions; three `set_name`; and one `defer` covering 50 voice IDs. The executor accepted all 34. The result therefore reflects the proposed patch rather than an executor rejection. The earlier full-candidate run was also fragmented (81 character rows, 45 empty, 36 occupied), so top-five truncation alone does not explain the issue.

### Direct diarization example

Around 11:26, the reference transcript labels “Lu Ya?” and “then your studio?” as **Jake** (source cues at 00:26:14.733 and 00:26:20.800). The current evidence assigns the first to `voice_388` and the second to `voice_239`, while adjacent Jake turns use `voice_0`. The words are largely transcribed, but speaker identity breaks across nearby turns. The current prompt contains no MOSS alignment (`run_id: null`, no segments or alignments). The old C3 configuration enabled local MOSS in addition to TST speaker mapping. The old ASR was Deepgram; the new ASR is MAI. These differences make the two consolidation inputs materially different despite the same source recordings.

## Sol route and code comparison

The old C3 benchmark used `consolidation.port.attach_online` with `configs/official_consolidation.json`; its official proposer targeted `api.openai.com/v1/responses` and specified `reasoning.effort: high`. Its launch verification records official Sol high. The new run used `https://api.302.ai/v1/chat/completions`. Its backend config contains `reasoning_effort: high`, but `bench/backends.py:177–184` does not forward that setting and `consolidation/llm_consolidator.py:10–16` does not put it in the request. The saved v2 request has only `model`, `messages`, and `response_format`. Its response reports `model: gpt-5.6-sol`, 97,536 prompt tokens, 6,436 completion tokens, and 2,131 reasoning tokens. That returned model name does not independently attest to the model serving behind the proxy; the saved response has no signed provider attestation. The old raw LLM request/response is unavailable in the local C3 archive, so reasoning-token use cannot be compared directly.

Core consolidation schema, evidence builder, observation logic, entity registry, canonicalizer, and `llm_consolidator.py` are byte-identical between the two repositories. The current `prompt_packet.py` truncates CAM++ candidates to five and the system prompt explains missing candidates. Runtime, port, and native differences mainly remove Mandol publication from the current repository. The large observed behavior difference is therefore better traced to input evidence, model transport/reasoning configuration, and the model's emitted patch than to a changed merge executor.

## What would isolate the causes

1. **Model/route:** submit the *same saved v2 prompt* to official Sol with explicit high reasoning and to 302.ai with explicit high reasoning, then compare the accepted patches and alignment to reference speaker labels. This tests behavior but still cannot prove how 302.ai internally routes requests without provider evidence.
2. **Diarization:** hold the saved construction graph and Sol route fixed while adding a real MOSS window, then compare assignments; separately evaluate CAM++/TST voice assignments against timestamp-aligned reference cues. Avoid using the graph's character-row count as accuracy.
3. **Memory generation:** run Terra and Gemini on the same 41 clip inputs and identical prompt/frames, recording per-clip episodic and semantic counts and factual coverage. This isolates the main source of the node-count gap.

No additional Sol or construction run was launched for this diagnosis.
