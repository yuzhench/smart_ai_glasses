# Path1 and Path2 — assembled memory-construction prompts

## Scope and reading notes

This review reflects the **current local code**, including the removal of the global character inventory and the temporal-handoff acceptance fix. It is not a transcript of the stopped v8 run: v8 sent the old identity block and omitted the handoff.

Both examples use the bench Gemini/OpenAI-compatible frame route. Each request contains one **`role: user` multimodal message**; there is no separate API system message. The instruction text below is extracted verbatim from each path’s source. Text blocks are shown together in their actual order; bracketed media descriptions are review placeholders, not literal prompt text.

For comparison, both use the same **illustrative transcript**. In Path2 only, assume the current audio matches stored `voice_0`, whose reliably consolidated character name is Jake; `voice_900` has no known name. The Path2 handoff is the exact 1,258-character output from v8’s first consolidation. Its use here illustrates the corrected assembly; it was absent from the historical v8 request.

## Path1 — complete text assembly

No temporal handoff or canonical-name annotation is added.

```text
You will be given a video and a set of character features. Each feature is either a face (represented by a video frame with a bounding box) or a voice (represented by one or more speech segments, each with MM:SS start and end times, and transcript content). Each feature has a unique ID enclosed in angle brackets. Some features may belong to the same character.

Your task consists of two parts:
1.	Video Description:
Generate a detailed and cohesive description of the current video clip. Use the provided feature IDs as references to characters (when applicable). Your description should cover all observable and inferable events. Each description should focus on a single atomic event or fact.
2.	High-Level Conclusions:
Generate high-level reasoning-based conclusions that go beyond surface-level observations. Use logical inference to identify character intentions, relationships, and identities. If a face and a voice feature refer to the same character, indicate it using this exact format: Equivalence: <face_x>, <voice_y>

Output Format:
Your output must be a JSON object with the following structure:

{
	"video_description": [
		"...",  // each string is one atomic event description
		"..."
	],
	"high_level_conclusions": [
		"...",  // each string is one high-level inference or identity resolution
		"Equivalence: <face_1>, <voice_2>"
	]
}

Please only return the valid JSON object, without any additional explanation or formatting.

Chronological video frames follow. Times are relative to this clip. Use the Voice features below for speech; do not infer speech from images.

Video frame at 00:00.0:
[IMAGE PART: current clip JPEG]
Video frame at 00:00.4:
[IMAGE PART: current clip JPEG]
[Remaining timestamped frame/image parts follow in the same pattern.]

Face features:
[Face ID and image pairs, when detected; none when no qualified faces are detected.]

Voice features:
{
  "<voice_0>": [
    {
      "start_time": "00:01",
      "end_time": "00:03",
      "asr": "Let’s invite four to six people."
    }
  ],
  "<voice_900>": [
    {
      "start_time": "00:04",
      "end_time": "00:05",
      "asr": "Who should we ask?"
    }
  ]
}
```

## Path2 — complete text assembly after consolidation

Only voice IDs already present in the current transcript receive known-name annotations. No global character/voice inventory is sent.

```text
You will be given a video and a set of character features. Each feature is either a face (represented by a video frame with a bounding box) or a voice (represented by one or more speech segments, each with MM:SS start and end times, and transcript content). Each feature has a unique ID enclosed in angle brackets. Some features may belong to the same character.

Your task consists of two parts:
1.	Video Description:
Generate a detailed and cohesive description of the current video clip. Use the provided feature IDs as references to characters (when applicable). Your description should cover all observable and inferable events. Each description should focus on a single atomic event or fact.
2.	High-Level Conclusions:
Generate high-level reasoning-based conclusions that go beyond surface-level observations. Use logical inference to identify character intentions, relationships, and identities. If a face and a voice feature refer to the same character, indicate it using this exact format: Equivalence: <face_x>, <voice_y>

Output Format:
Your output must be a JSON object with the following structure:

{
	"video_description": [
		"...",  // each string is one atomic event description
		"..."
	],
	"high_level_conclusions": [
		"...",  // each string is one high-level inference or identity resolution
		"Equivalence: <face_1>, <voice_2>"
	]
}

Please only return the valid JSON object, without any additional explanation or formatting.


PATH2 MEMORY CONSTRUCTION
Use RECENT TEMPORAL CONTEXT as background that is already established, not as
current-clip evidence. In both video_description and especially high_level_conclusions,
avoid merely restating that background. Output information supported by the current
clip that is new or temporally meaningful and adds value: events, facts, changes,
or useful inferences. A repeated action can still be a distinct event. Return empty
lists when nothing meaningful can be added.

Names in parentheses beside voice IDs are established identities; do not re-infer them.
Keep the voice ID when using its supplied name.

RECENT TEMPORAL CONTEXT
Setting & people: Jake wears the camera in a shared villa/workspace. Xiushuo and several other participants work with him around a checkered dining table; an unnamed participant gives sustained hardware-assembly guidance.
Main events & topics: The group synchronizes a phone timer, unboxes recording equipment and hard-drive enclosures, and visits Jake's bedroom workstation for an explanation of glasses, laptops, storage and three-hour data offloading. Back at the table they fit components, locate screws, check that hair does not block recording glasses, and pack completed enclosures. Conversation ranges from music and Changchun to joking about a factory assembly line and a hard-drive competition.
Plans & ongoing tasks: After finishing the first packing task, they plan a final-day gathering, discussed as Sunday afternoon. A participant writes and draws at the whiteboard while the group considers invitees, hosts, uncertain attendance and an estimate of four to six guests. Jake retrieves a clapperboard and marker; a shelf package is also brought into the room.
End state: The group is still discussing recruitment, including nearby people, Xiaohongshu and approaching people near Universal Studios, and asks what incentive visitors could receive.

Chronological video frames follow. Times are relative to this clip. Use the Voice features below for speech; do not infer speech from images.

Video frame at 00:00.0:
[IMAGE PART: current clip JPEG]
Video frame at 00:00.4:
[IMAGE PART: current clip JPEG]
[Remaining timestamped frame/image parts follow in the same pattern.]

Face features:
[Face ID and image pairs, when detected; none when no qualified faces are detected.]

Voice features:
{
  "<voice_0> (Jake)": [
    {
      "start_time": "00:01",
      "end_time": "00:03",
      "asr": "Let’s invite four to six people."
    }
  ],
  "<voice_900>": [
    {
      "start_time": "00:04",
      "end_time": "00:05",
      "asr": "Who should we ask?"
    }
  ]
}
```

## Path2 before the first consolidation

Use the same Path2 instructions, frames, and transcript structure above, but omit the entire `RECENT TEMPORAL CONTEXT` block. Without a reliable consolidated name, the transcript key remains `<voice_0>`. No empty context header or identity inventory is inserted.

## Consolidation handoff length instruction

The consolidation prompt now says: **“Fewer than 1000 characters; be sharp.”** The 1,258-character handoff above remains the exact historical v8 example, generated before this instruction changed.

## Handoff acceptance and replacement

A string handoff of at most 1,800 characters is accepted independently of rejected individual consolidation decisions. A nonempty accepted handoff is persisted and inserted into subsequent construction requests under `RECENT TEMPORAL CONTEXT`, provided its session and cutoff match the active consolidation state. Each consolidation replaces the previous handoff. Missing, invalid, or empty handoffs leave no temporal-context block; an older summary is not substituted. This behavior is request assembly logic, not extra prompt text.

## Source references

- Path1 instruction: `StreamMeCo/mmagent/prompts.py`, `prompt_generate_memory_with_ids_sft`.
- Path1 assembly: `StreamMeCo/mmagent/memory_processing_qwen.py`, `generate_all_memories` and `generate_memories`.
- Path2 instruction: `m3_adaptors/prompts/memory_construction.md` plus `DELTA_INSTRUCTION` in `m3_adaptors/construction.py`.
- Path2 assembly: `m3_adaptors/memory_construction.py`, `generate_memories`; temporal context and inline speaker labeling: `m3_adaptors/construction.py`.
- Shared frame formatting: `bench/backends.py`, `_frame_context`; actual sampled timestamps depend on source and target frame rates.
- Handoff acceptance: `consolidation/patch_executor.py`.
