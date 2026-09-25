"""Path2-only construction instructions and committed temporal background."""
import json

DELTA_INSTRUCTION = """
PATH2 MEMORY CONSTRUCTION
Use RECENT TEMPORAL CONTEXT as background that is already established, not as
current-clip evidence. In both video_description and especially high_level_conclusions,
avoid merely restating that background. Output information supported by the current
clip that is new or temporally meaningful and adds value: events, facts, changes,
or useful inferences. A repeated action can still be a distinct event. Return empty
lists when nothing meaningful can be added.

Names in parentheses beside voice IDs are established identities; do not re-infer them.
Keep the voice ID when using its supplied name.
""".strip()


def construction_context(graph):
    """Only the preceding-window handoff; no global identity inventory."""
    if graph is None:
        return []
    context = []
    handoff = getattr(graph, 'temporal_handoff', {})
    summary = handoff.get('summary', '')
    if (summary and handoff.get('session_id') == getattr(graph, 'identity_session', None)
            and handoff.get('current_cutoff') == getattr(graph, 'identity_cutoff', None)):
        context.append({'type': 'text', 'content': 'RECENT TEMPORAL CONTEXT\n' + summary})
    return context



def label_voice_transcripts(video_context, graph):
    """Label the existing transcript block, preserving times, text and raw voice IDs.

    Run after frame/backend adaptation but before VLM message construction. Only
    native feature ownership is used; mixed/scoped assignments are never promoted
    to an unscoped speaker name, nor are unreviewed singleton containers trusted.
    """
    if graph is None:
        return video_context
    from mmagent.character_identity import consolidated, resolve_identity
    if not consolidated(graph):
        return video_context
    result = list(video_context)
    for index, part in enumerate(video_context[:-1]):
        if part.get('type') != 'text' or part.get('content') != 'Voice features:':
            continue
        transcript = video_context[index + 1]
        voices = json.loads(transcript['content'])
        labeled = {}
        for token, segments in voices.items():
            feature = token.strip('<>')
            label = token
            try:
                resolution = resolve_identity(graph, feature)
            except ValueError:
                resolution = {}  # Conflicting ownership is not a reliable mapping.
            name = resolution.get('canonical_name')
            if name:
                label = token + ' (' + name + ')'
            labeled[label] = segments
        result[index + 1] = dict(transcript, content=json.dumps(labeled, ensure_ascii=False))
    return result
