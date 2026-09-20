"""Reviewable JSON view of a native VideoGraph, without embeddings.

Lifted verbatim from the edited ``mmagent.videograph_markdown.graph_view`` so
the consolidation publication no longer depends on that (Mandol-adjacent)
module. Pure read-only rendering; no graph mutations.
"""


def _visible(key):
    return 'embedding' not in str(key).lower()


def graph_view(graph):
    """Preserve reviewable graph state without embeddings or derived identities."""
    if isinstance(graph, dict) and isinstance(graph.get('nodes'), list):
        return graph
    state = vars(graph) if not isinstance(graph, dict) else graph
    nodes = []
    for node in state['nodes'].values():
        fields = vars(node) if not isinstance(node, dict) else node
        nodes.append({k: v for k, v in fields.items() if _visible(k)})
    return {
        'nodes': sorted(nodes, key=lambda n: n['id']),
        'edges': [{'source': s, 'target': t, 'weight': w}
                  for (s, t), w in sorted(state['edges'].items())],
        'edge_storage': 'directed',
        'state_complete': True,
        'graph_attributes': {k: v for k, v in state.items()
                             if k not in ('nodes', 'edges') and _visible(k)},
    }
