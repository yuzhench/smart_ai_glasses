"""Adapters for the existing evidence, Astra, native application and index cycle."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import pickle
import shutil
import tempfile

from .common import write
from .runtime import ConsolidationPatch


class NativeConsolidationWorker:
    """evidence(snapshot) supplies the existing replay/MOSS/assignment records.

    propose(packet, directory) is the existing configured Astra callable (or a
    saved-decision callable for tests). Execution is scoped to the compact packet.
    moss(replay, previous_cutoff, directory) runs before proposal. Alternatively,
    supply exact-window MOSS in evidence.
    """
    def __init__(self, evidence, propose, directory, *, moss=None):
        if moss is not None and not callable(moss):
            raise ValueError('moss must be a window callable or supplied in evidence')
        self.evidence, self.propose = evidence, propose
        self.directory = Path(directory)
        self.moss = moss

    def __call__(self, snapshot):
        from .native import proposal_state, project, validate_source
        from .evidence_builder import build_evidence
        from .patch_executor import execute
        inputs = self.evidence(snapshot)
        replay = inputs['replay']
        replay = dict(replay, current_cutoff_clip=snapshot.cutoff_clip_id)
        if replay['current_cutoff'] != snapshot.cutoff_timestamp:
            raise ValueError('evidence cutoff differs from frozen snapshot')
        if any(o['clip_id'] > snapshot.cutoff_clip_id or o['end_time'] > snapshot.cutoff_timestamp
               for o in replay['observations']):
            raise ValueError('evidence includes observations after snapshot cutoff')
        validate_source(snapshot.graph, replay['memories'])
        state = proposal_state(snapshot.graph, replay['session_id'], replay['observations'],
                               replay['source_graph_version'])
        directory = self.directory / ('snapshot_' + str(snapshot.graph_version))
        directory.mkdir(parents=True, exist_ok=True)
        with (directory/'snapshot.pkl').open('wb') as handle:
            pickle.dump(snapshot.graph, handle, protocol=pickle.HIGHEST_PROTOCOL)
        write(directory/'snapshot.json', dict(graph_version=snapshot.graph_version,
            cutoff_clip_id=snapshot.cutoff_clip_id, cutoff_timestamp=snapshot.cutoff_timestamp))
        moss = inputs.get('moss')
        runner = self.moss
        if moss is None and runner:
            moss = runner(replay,state['cutoff'],directory/'moss')
        if moss is None:
            raise ValueError('MOSS evidence is required before consolidation reasoning')
        from .moss_alignment import validate_window
        validate_window(moss, replay['session_id'], state['cutoff'], replay['current_cutoff'])
        if not moss['segments'] and any(o['end_time'] > state['cutoff'] for o in replay['observations']):
            raise ValueError('MOSS returned no segments for a window with speech observations')
        packet = build_evidence(replay, state, moss, inputs.get('assignments', ()))
        write(directory/'evidence.json', packet)
        from .prompt_packet import prepare_prompt
        prepare_prompt(packet, directory)
        patch = self.propose(packet, directory)
        state, execution = execute(state, packet, patch)
        result = deepcopy(snapshot.graph)
        report = project(result, state, packet)
        write(directory/'patch.json', patch)
        write(directory/'execution.json', execution)
        write(directory/'identity_changes.json', report)
        return ConsolidationPatch(snapshot, result)


class RetrievalPublisher:
    """Background native publication after the runtime's native reindex.

    The live graph is never replaced by this historical retrieval checkpoint.
    """
    def __init__(self, directory):
        self.directory = Path(directory)

    def __call__(self, graph):
        import fcntl
        from .common import read
        directory = self.directory
        directory.mkdir(parents=True, exist_ok=True)
        versions = directory/'versions'
        versions.mkdir(exist_ok=True)
        # This is a publication lock, unrelated to the live graph writer.
        with (directory/'.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            pointer = directory/'CURRENT.json'
            if pointer.exists():
                previous = read(versions/read(pointer)['version']/'runtime.json')
                if previous['current_graph_version'] >= graph.current_graph_version:
                    raise ValueError('stale runtime retrieval publication')
            for node in graph.nodes.values():
                node.metadata.pop('embedding_stale', None)
            graph.graph_version = 'v_' + hashlib.sha256(pickle.dumps(graph)).hexdigest()
            staged = Path(tempfile.mkdtemp(prefix='.staged-', dir=versions))
            try:
                with (staged/'graph.pkl').open('wb') as handle:
                    pickle.dump(graph, handle, protocol=pickle.HIGHEST_PROTOCOL)
                write(staged/'runtime.json', {key: getattr(graph, key) for key in (
                    'current_graph_version', 'last_consolidated_clip_id',
                    'last_consolidated_timestamp', 'entity_registry_version')})
                write(staged/'retrieval_ready.json', dict(status='ready', graph_version=graph.graph_version,
                    native_graph='graph.pkl'))
                write(staged/'manifest.json', dict(version=graph.graph_version, retrieval_complete=True,
                    sha256={str(p.relative_to(staged)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in staged.rglob('*') if p.is_file()}))
                os.rename(staged, versions/graph.graph_version)
                write(pointer, {'version': graph.graph_version})
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
