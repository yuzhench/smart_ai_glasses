# Path2 prompt review

v8 was stopped after discovering the unintended global identity inventory. The local correction removes that block entirely and annotates only current transcript voice IDs with known names, e.g. `<voice_0> (Jake)`. No replacement run has been started.

## Construction addition (exact)

```text
PATH2 MEMORY CONSTRUCTION
Use RECENT TEMPORAL CONTEXT as background that is already established, not as
current-clip evidence. In both video_description and especially high_level_conclusions,
avoid merely restating that background. Output information supported by the current
clip that is new or temporally meaningful and adds value: events, facts, changes,
or useful inferences. A repeated action can still be a distinct event. Return empty
lists when nothing meaningful can be added.

Names in parentheses beside voice IDs are established identities; do not re-infer them.
Keep the voice ID when using its supplied name.
```

## Consolidation temporal addition (exact, revised)

```text
SECONDARY OUTPUT: TEMPORAL HANDOFF
After identity and alias decisions, summarize the story/content of this 20-minute
window in temporal_handoff: a compact, structured high-level summary for the next
window's construction. Use short labeled sections: Setting & people; Main events
& topics; Plans & ongoing tasks; End state. Omit empty sections.
Use only this window's actual evidence and supported canonical names; preserve
uncertainty. Do not restate individual memories, write a transcript, or summarize
previous handoffs. Fewer than 1000 characters; be sharp. Return an empty string
if there is no usable evidence.
```

## Consolidation alias addition (exact, unchanged)

```text
IDENTITY ALIASES: AFTER IDENTITY AND NAME DECISIONS
Use assign_alias to map an identity-reference phrase to an established character
(entity_id), with evidence_ids and a concrete rationale. For example, camera wearer,
camera holder, camera operator, or person recording the video may refer to Jake,
but only if the evidence supports that physical-person identity. A known voice name
alone does not establish who wears the camera. Do not automatically treat leader,
instructor, participant, technician, or friend as aliases: these are descriptions or
roles unless strong evidence makes the phrase a direct identity reference.
The executor scopes each alias to text nodes CREATED in THIS consolidation window,
using previous_cutoff_clip < clip_id <= current_cutoff_clip. Never supply timestamps
or widen the scope. An alias from a prior window does NOT apply in this window;
assign it again only when current evidence supports it. If the phrase refers to
multiple people within this window, use resolve_reference for supported occurrences
instead of a window-wide alias. set_name.aliases is legacy descriptive metadata;
only assign_alias creates a trusted retrieval mapping.
Existing identity_aliases include stable alias_id and pipeline-owned window bounds.
Use revise_alias with the owning entity_id, alias_id and target_entity_id to correct
an existing mapping, or remove_alias to revoke it. Cite provided evidence and a
rationale; these operations retain the original record's window. Use depends_on
when aliases rely on naming/assignment decisions in this response. Do not repeat
unchanged aliases for an already recorded window.
```

## Request assembly

`consolidation/llm_consolidator.py:request_payload` places the entire system.md in the system message, then appends `Patch JSON Schema` and the serialized PATCH_SCHEMA. The user message holds the current evidence packet. The temporal instructions are the last prose section of system.md, after identity and alias instructions; they are part of the same consolidation call, not a separate call. The schema requires a `temporal_handoff` string of at most 1800 characters. Previous handoffs are not included in the evidence packet.
