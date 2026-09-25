"""Automatic pre/post-consolidation graph replay (path 2 only).

Whenever a path-2 run reaches a consolidation boundary, the replayer captures
the live VideoGraph twice — right before ``consolidate_until`` and right
after — and renders both states as reviewable Markdown plus a replayable
pickle:

    output_dir/graphs/
        before_first_consolidation.md / .pkl
        after_first_consolidation.md / .pkl
        before_second_consolidation.md / .pkl
        ...
        README.md                 index table, refreshed after each pair

The Markdown renderer is vendored from the 20260918_sol_astra run's
``mmagent/videograph_markdown.py`` so the output format matches the earlier
review documents exactly. Standard library only; the graph is rendered from
the live in-process object, no model runtime is touched.
"""
import base64
from collections import Counter, defaultdict
import html
import json
import os
from pathlib import Path
import pickle
import re

_ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh",
             "eighth", "ninth", "tenth", "eleventh", "twelfth"]


def _ordinal(n):
    return _ORDINALS[n - 1] if n <= len(_ORDINALS) else f"{n}th"


def _cutoff_label(cutoff_s):
    return f"{cutoff_s:,.0f}" if float(cutoff_s).is_integer() else f"{cutoff_s:,.2f}"


class GraphReplayer:
    """Captures before/after graph states around each consolidation."""

    def __init__(self, output_dir):
        self.dir = Path(output_dir) / "graphs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.records = []

    def before(self, graph, cutoff_s):
        """Stash the pre-consolidation state; returns a token for after()."""
        self.count += 1
        return {"ordinal": _ordinal(self.count), "cutoff_s": float(cutoff_s),
                "graph": graph}

    def after(self, pending, graph):
        """Write the before/after pair and refresh the index."""
        ordinal, cutoff_s = pending["ordinal"], pending["cutoff_s"]
        label = f"{_cutoff_label(cutoff_s)} s"
        for phase, state in (("before", pending["graph"]), ("after", graph)):
            stem = f"{phase}_{ordinal}_consolidation"
            title = f"{phase.capitalize()} the {ordinal} consolidation ({label})"
            self._write_pickle(state, self.dir / f"{stem}.pkl")
            view = graph_view(state)
            text = "\n".join(render_graph(view, title)) + "\n"
            (self.dir / f"{stem}.md").write_text(text)
            self.records.append({
                "state": f"{phase.capitalize()} {ordinal} consolidation",
                "cutoff_s": cutoff_s,
                "nodes": len(view["nodes"]), "edges": len(view.get("edges", [])),
                "characters": len(view["graph_attributes"].get("character_mappings") or {}),
                "file": f"{stem}.md",
            })
        self._write_readme()

    def discard(self, pending):
        """Drop a stashed before-state whose consolidation never happened."""
        self.count -= 1

    @staticmethod
    def _write_pickle(graph, path):
        temporary = str(path) + ".tmp"
        with open(temporary, "wb") as handle:
            pickle.dump(graph, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, path)

    def _write_readme(self):
        lines = ["# Graph checkpoints", "",
                 "Graph states captured automatically around each completed consolidation.", "",
                 "| State | Cutoff | Nodes | Edges | Character mappings | Graph replay |",
                 "| --- | --- | ---: | --- | --- | --- |"]
        for record in self.records:
            lines.append(
                f"| {record['state']} | {_cutoff_label(record['cutoff_s'])} s "
                f"| {record['nodes']:,} | {record['edges']:,} | {record['characters']} "
                f"| [{Path(record['file']).stem}]({record['file']}) |")
        (self.dir / "README.md").write_text("\n".join(lines) + "\n")


# --- Renderer (vendored from mmagent/videograph_markdown.py, 20260918_sol_astra) ---

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


def _cell(value):
    return html.escape(str(value), quote=False).replace('|', '&#124;').replace('\n', '<br>')


def _table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |',
            '| ' + ' | '.join('---' for _ in headers) + ' |'] + [
                '| ' + ' | '.join(str(v) for v in row) + ' |' for row in rows] + ['']


def render_graph(graph, title='Video graph', level=1, source=None, output=None):
    """Compact review: node text, adjacent edges, and stored character mappings."""
    graph = graph_view(graph)
    nodes = sorted(graph['nodes'], key=lambda n: n['id'])
    edges = graph.get('edges', [])
    attrs = graph.get('graph_attributes', {})
    prefix = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    anchor = lambda i: f'{prefix}-node-{i}'
    ref = lambda i: f'[{i}](#{anchor(i)})'
    section = lambda name: ['#' * (level + 1) + ' ' + name, '']
    counts = Counter(n['type'] for n in nodes)
    adjacency = defaultdict(list)
    lookup = {(e['source'], e['target']): e.get('weight', 1) for e in edges}
    seen = set()
    link_count = 0
    for (a, b), weight in lookup.items():
        if (a, b) in seen:
            continue
        symmetric = graph.get('edge_storage') != 'directed' or lookup.get((b, a)) == weight
        suffix = '' if weight == 1 else f' (weight {_cell(weight)})'
        adjacency[a].append(('↔' if symmetric else '→') + ' ' + ref(b) + suffix)
        if a != b:
            adjacency[b].append(('↔' if symmetric else '←') + ' ' + ref(a) + suffix)
        seen.add((a, b))
        if symmetric:
            seen.add((b, a))
        link_count += 1
    result = ['#' * level + ' ' + title, '',
              f"**{len(nodes):,} nodes · {link_count:,} links** — "
              f"{counts['episodic']:,} events, {counts['semantic']:,} inferences, "
              f"{counts['voice']:,} voices, {counts['img']:,} faces.", '',
              'Links appear beside each node. ↔ means a shared connection; arrows show '
              'one-way connections. Unmarked weights are 1. Nodes without links are unconnected.', '']
    result += section('Character mappings')
    mappings = attrs.get('character_mappings')
    character_metadata = attrs.get('character_metadata') or {}
    if mappings is None:
        result += [('Not stored in this construction checkpoint.' if graph.get('state_complete')
                    else 'Not included in this JSON export.'), '']
    elif not mappings:
        result += ['Empty — no character mappings stored.', '']
    else:
        single = sum(len(v) == 1 for v in mappings.values())
        result += [f'{len(mappings)} characters: {single} single-feature mappings, '
                   f'{len(mappings)-single} mappings joining multiple features.', '',
                   '<details><summary>Show character → face / voice mappings</summary>', '']

        def feature(value):
            match = re.fullmatch(r'(voice|face)_(\d+)', str(value))
            return f'[{value}](#{anchor(int(match[2]))})' if match else _cell(value)
        result += _table(['Character', 'Canonical name', 'Face / voice'],
                         [[_cell(k), _cell(character_metadata.get(k, {}).get('canonical_name') or '—'),
                           ', '.join(feature(v) for v in values)]
                          for k, values in mappings.items()])
        result += ['</details>', '']
    kinds = [('episodic', 'Events'), ('semantic', 'Inferences'), ('voice', 'Voices'), ('img', 'Faces')]
    kinds += [(kind, str(kind)) for kind in sorted(set(counts) - {k for k, _ in kinds})]
    for kind, heading in kinds:
        selected = [n for n in nodes if n['type'] == kind]
        if not selected:
            continue
        result += section(heading)
        for node in selected:
            node_id = node['id']
            metadata = node.get('metadata', {})
            contents = metadata.get('contents', [])
            if isinstance(contents, str):
                contents = [contents]
            links = ' · '.join(adjacency[node_id])
            # Keep complete transcripts available without dominating the review page.
            result += [f'<a id="{anchor(node_id)}"></a>', '']
            if kind in ('voice', 'img'):
                name = ('voice' if kind == 'voice' else 'face') + f'_{node_id}'
                result += [f'<details><summary>{name} · {len(contents)} '
                           f'{"speech entries" if kind == "voice" else "images"} · '
                           f'{len(adjacency[node_id])} links</summary>', '']
                if links:
                    result += [links, '']
                for index, content in enumerate(contents, 1):
                    media = _face_image(content) if kind == 'img' else None
                    if media and output:
                        data, extension = media
                        output_path = Path(output)
                        asset = output_path.parent / (output_path.stem + '.assets') / f'face_{node_id}_{index}.{extension}'
                        asset.parent.mkdir(parents=True, exist_ok=True)
                        asset.write_bytes(data)
                        result += [f'![{name}]({os.path.relpath(asset, output_path.parent)})', '']
                    elif media:
                        result += [f'- Face image ({media[1]}).']
                    else:
                        result += ['- ' + _cell(content)]
                result += ['', '</details>', '']
            else:
                clip = f' · clip {metadata["timestamp"]}' if 'timestamp' in metadata else ''
                content = ' / '.join(_cell(v) for v in contents) or '(no text)'
                result += [f'- **{node_id}**{clip}: {content}' +
                           (f'  **Links:** {links}' if links else ''), '']
    if source:
        target = os.path.relpath(source, Path(output).parent) if output else str(Path(source).resolve())
        result += [f'[Original graph](<{target}>) · Embeddings and internal metadata omitted.', '']
    return result


def _face_image(content):
    if not isinstance(content, str):
        return None
    try:
        data = base64.b64decode(content, validate=True)
    except (ValueError, TypeError):
        return None
    if data.startswith(b'\xff\xd8\xff'):
        return data, 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return data, 'png'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return data, 'webp'
    return None
