"""Path 2 adaptor patch (writer-side) for the pristine ``streammeco`` module.

``remove_text_nodes_from_graph`` is lifted verbatim from the EDITED module; it
additionally prunes ``reference_character_mappings`` and
``memory_claim_revisions`` (hasattr-guarded). The benchmark-only
``compress_graph(retain_ratio=...)`` knob is deliberately not ported.
"""
from typing import Iterable

from m3_adaptors._shared import rebind


def remove_text_nodes_from_graph(graph, node_ids: Iterable[int]):
    """Remove nodes and all related references from the graph."""
    removal_set = set(node_ids)
    if not removal_set:
        return graph
    for node_id in removal_set:
        graph.nodes.pop(node_id, None)
    if hasattr(graph, "reference_character_mappings"):
        graph.reference_character_mappings = {key: record for key, record in graph.reference_character_mappings.items()
            if int(record['node_id']) not in removal_set}
    if hasattr(graph, "memory_claim_revisions"):
        graph.memory_claim_revisions = {key: record for key, record in graph.memory_claim_revisions.items()
            if int(key) not in removal_set}
    if hasattr(graph, "text_nodes"):
        graph.text_nodes = [node_id for node_id in graph.text_nodes if node_id not in removal_set]
    if hasattr(graph, "edges"):
        for edge in list(graph.edges.keys()):
            if edge[0] in removal_set or edge[1] in removal_set:
                graph.edges.pop(edge, None)
    if hasattr(graph, "text_nodes_by_clip"):
        for clip_id in list(graph.text_nodes_by_clip.keys()):
            filtered = [node_id for node_id in graph.text_nodes_by_clip[clip_id] if node_id not in removal_set]
            if filtered:
                graph.text_nodes_by_clip[clip_id] = filtered
            else:
                del graph.text_nodes_by_clip[clip_id]
    if hasattr(graph, "event_sequence_by_clip"):
        for clip_id in list(graph.event_sequence_by_clip.keys()):
            filtered = [node_id for node_id in graph.event_sequence_by_clip[clip_id] if node_id not in removal_set]
            if filtered:
                graph.event_sequence_by_clip[clip_id] = filtered
            else:
                del graph.event_sequence_by_clip[clip_id]
    return graph


def apply():
    import streammeco

    streammeco.remove_text_nodes_from_graph = rebind(
        remove_text_nodes_from_graph, streammeco)
