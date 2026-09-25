"""Path2 window aliases and temporal handoff through native publication."""
from copy import deepcopy
import json
import pickle
from pathlib import Path

import pytest

from consolidation.evidence_builder import build_evidence
from consolidation.native import project, proposal_state, m3_module
from consolidation.patch_executor import execute
from consolidation.prompt_packet import prepare_prompt
from consolidation.runtime import ConsolidationPatch, ConsolidationSnapshot, reconcile, validate_patch
from m3_adaptors.construction import construction_context, DELTA_INSTRUCTION
from .test_native_characters import graph, publication_inputs

identity = m3_module('mmagent.character_identity')


def first_window():
    g = graph()
    g.nodes[3].metadata.update(contents=['The camera wearer helps. Jake wears the camera.'])
    replay, state, packet, patch = publication_inputs(g)
    replay['current_cutoff'] = 1187.92
    replay['current_cutoff_clip'] = 1
    packet = build_evidence(replay, state)
    patch.update(evidence_cutoff_s=1187.92, temporal_handoff='Jake and others are around the table.')
    patch['decisions'].extend([
        dict(op='set_name', decision_id='name', entity_id='person_0', name='Jake', evidence_ids=['episodic_3']),
        dict(op='assign_alias', decision_id='wearer', entity_id='person_0', phrase='camera wearer',
             evidence_ids=['episodic_3'], rationale='The clip identifies Jake as the wearer.', depends_on=['name'])])
    return g, replay, state, packet, patch


def run_first():
    g, replay, state, packet, patch = first_window()
    prepare_prompt(packet)
    state, report = execute(state, packet, patch)
    assert report['rejected'] == []
    project(g, state, packet)
    return g, replay, state


def next_window(g, replay, *, decisions=(), summary='The group leaves the table.'):
    replay = deepcopy(replay)
    replay.update(current_cutoff=2388.1, current_cutoff_clip=2)
    state = proposal_state(g, 's', replay['observations'], 'source')
    packet = build_evidence(replay, state)
    patch = dict(schema_version=1, session_id='s', base_graph_version=state['graph_version'],
                 evidence_cutoff_s=replay['current_cutoff'], decisions=list(decisions), temporal_handoff=summary)
    state, report = execute(state, packet, patch)
    assert report['rejected'] == []
    project(g, state, packet)
    return state, packet


def test_alias_uses_actual_window_and_selective_reindex():
    g, replay, state = run_first()
    record = g.character_metadata['character_0']['identity_aliases'][0]
    assert record['window'] == dict(session_id='s', previous_cutoff=0, current_cutoff=1187.92,
                                   previous_cutoff_clip=-1, current_cutoff_clip=1)
    batches = []
    report = identity.reindex_text(g, lambda texts: batches.append(texts) or [[0., 1., 0.] for _ in texts])
    assert g.nodes[3].metadata['contents'][0].startswith('The camera wearer')
    assert g.nodes[3].metadata['retrieval_contents'] == ['Jake helps. Jake wears the camera.']
    assert 6 not in report['changed_node_ids']  # unchanged room description
    assert identity.reindex_text(g, lambda _: pytest.fail('unchanged input re-embedded'))['changed_node_ids'] == []
    text = ['The camera wearer helps.']
    assert identity.canonicalize_contents(g, text, clip_id=1)[0] == ['Jake helps.']
    assert identity.canonicalize_contents(g, text, clip_id=2)[0] == text
    assert identity.canonicalize_contents(g, text, clip_id=-1)[0] == text
    assert identity.canonicalize_contents(g, text)[0] == text


def test_alias_correction_and_removal_preserve_scope_and_raw_text():
    g, replay, _ = run_first()
    original = deepcopy(g.nodes[3].metadata['contents'])
    record = deepcopy(g.character_metadata['character_0']['identity_aliases'][0])
    state, _ = next_window(g, replay, decisions=[dict(op='revise_alias', decision_id='correct',
        entity_id='person_0', target_entity_id='person_2', alias_id=record['alias_id'],
        evidence_ids=['episodic_3'], rationale='The original wearer attribution was mistaken.')])
    changed = g.character_metadata['character_2']['identity_aliases'][0]
    assert changed['window'] == record['window']
    assert not g.character_metadata['character_0']['identity_aliases']
    assert identity.retrieval_contents(g, 3)[0].startswith('character_2 helps')
    replay = dict(replay, current_cutoff=3580, current_cutoff_clip=3)
    state = proposal_state(g, 's', replay['observations'], 'source')
    packet = build_evidence(replay, state)
    state, report = execute(state, packet, dict(schema_version=1, session_id='s',
        base_graph_version=state['graph_version'], evidence_cutoff_s=3580, temporal_handoff='',
        decisions=[dict(op='remove_alias', decision_id='remove', entity_id='person_2',
            alias_id=record['alias_id'], evidence_ids=['episodic_3'], rationale='Identity not established.')]))
    assert not report['rejected']
    project(g, state, packet)
    assert identity.retrieval_contents(g, 3) == original
    assert g.nodes[3].metadata['contents'] == original


def test_rename_conflicts_occurrence_precedence_and_phrase_boundaries():
    g, _, _ = run_first()
    g.character_metadata['character_0']['canonical_name'] = 'Jacob'
    assert identity.retrieval_contents(g, 3)[0].startswith('Jacob helps')
    text = ['Camera wearer helps; camera wearers wait.']
    assert identity.canonicalize_contents(g, text, clip_id=1)[0] == ['Jacob helps; camera wearers wait.']
    record = deepcopy(g.character_metadata['character_0']['identity_aliases'][0])
    record['alias_id'] = 'conflict'
    g.character_metadata.setdefault('character_2', {})['identity_aliases'] = [record]
    assert identity.retrieval_contents(g, 3)[0].startswith('The camera wearer helps')
    g.reference_character_mappings['3:0:0:17'] = dict(node_id=3, content_index=0, start=0, end=17,
        mention='The camera wearer', character_id='character_0', evidence_ids=['episodic_3'])
    assert identity.retrieval_contents(g, 3)[0].startswith('Jacob helps')


def test_temporal_context_replaces_and_survives_checkpoint_without_recursion():
    g, replay, state, packet, patch = first_window()
    assert not any('RECENT TEMPORAL CONTEXT' in p['content'] for p in construction_context(g))
    state, _ = execute(state, packet, patch)
    project(g, state, packet)
    restored = pickle.loads(pickle.dumps(g))
    context = construction_context(restored)
    assert context[0]['content'] == 'RECENT TEMPORAL CONTEXT\nJake and others are around the table.'
    state, packet = next_window(restored, replay)
    view = prepare_prompt(packet)
    assert 'Jake and others are around the table.' not in json.dumps(view)
    assert 'temporal_handoff' not in view
    assert construction_context(restored)[0]['content'] == 'RECENT TEMPORAL CONTEXT\nThe group leaves the table.'
    assert restored.temporal_handoff['previous_cutoff'] == 1187.92
    assert restored.temporal_handoff['current_cutoff'] == 2388.1


@pytest.mark.parametrize('handoff', [None, {'bad': 'shape'}, 'x' * 1801])
def test_invalid_or_missing_handoff_does_not_reuse_old_or_discard_identity(handoff):
    g, replay, _ = run_first()
    next_window(g, replay, summary=handoff)
    assert g.temporal_handoff['summary'] == ''
    assert g.character_metadata['character_0']['canonical_name'] == 'Jake'
    assert not any('RECENT TEMPORAL CONTEXT' in p['content'] for p in construction_context(g))


def test_rejected_alias_timestamps_do_not_discard_handoff(monkeypatch):
    g, _, state, packet, patch = first_window()
    patch['decisions'][-1]['start_s'] = 0
    state, report = execute(state, packet, patch)
    assert len(report['rejected']) == 1
    assert len(report['accepted']) == 2
    assert state['temporal_handoff']['summary'] == patch['temporal_handoff']
    project(g, state, packet)
    restored = pickle.loads(pickle.dumps(g))
    module = m3_module('mmagent.memory_processing_qwen')
    captured = []
    monkeypatch.setattr(module, 'generate_video_context', lambda *args: [])
    monkeypatch.setattr(module, 'generate_messages', lambda parts: captured.append(parts) or parts)
    monkeypatch.setattr(module, 'get_response', lambda _: ('{"video_description": [], "high_level_conclusions": []}', 0))
    module.generate_memories([], {}, {}, 'next_clip', video_graph=restored)
    assert captured[0][1]['content'] == 'RECENT TEMPORAL CONTEXT\n' + patch['temporal_handoff']


def test_handoff_and_alias_survive_runtime_reconcile():
    g, _, state, packet, patch = first_window()
    base = deepcopy(g)
    state, _ = execute(state, packet, patch)
    project(g, state, packet)
    snapshot = ConsolidationSnapshot(0, 1, 1187.92, base)
    result = ConsolidationPatch(snapshot, g)
    validate_patch(result)
    live = reconcile(base, result)
    assert live.temporal_handoff == g.temporal_handoff
    assert identity.retrieval_contents(live, 3)[0].startswith('Jake helps')


def test_path2_generation_receives_context_frames_and_transcript(monkeypatch):
    g, _, _ = run_first()
    module = m3_module('mmagent.memory_processing_qwen')
    video = [{'type': 'text', 'content': 'CURRENT FRAMES/TRANSCRIPT'}]
    monkeypatch.setattr(module, 'generate_video_context', lambda *args: video)
    captured = []
    monkeypatch.setattr(module, 'generate_all_memories', lambda context, model: captured.append(context) or ([], []))
    assert module.generate_memories([], {}, {}, 'clip', video_graph=g) == ([], [])
    assert captured[0][0]['content'].startswith('RECENT TEMPORAL CONTEXT\n')
    assert captured[0][1:] == video
    assert captured[0][-1] == video[0]
    assert DELTA_INSTRUCTION in module.prompt_generate_memory_with_ids_sft
    # The shared path1 source is not edited to install this behavior.
    pristine = Path('StreamMeCo/mmagent/memory_processing_qwen.py').read_text()
    assert 'DELTA_INSTRUCTION' not in pristine and 'RECENT TEMPORAL CONTEXT' not in pristine


def test_adjacent_windows_can_assign_same_alias_to_different_people():
    g, replay, _ = run_first()
    processing = m3_module('mmagent.memory_processing_qwen')
    # This new clip has no alias yet, so the raw phrase is its initial embedding input.
    raw = 'The camera wearer packs. Xiu wears the camera.'
    g.identity_reindex_async = True
    processing.process_memories(g, [raw], 2, precomputed_contents=[raw],
                                precomputed_embeddings=[[1., 0., 0.]])
    node_id = g.next_node_id - 1
    replay = deepcopy(replay)
    replay['memories'].append(dict(memory_node_id=str(node_id), kind='episodic', clip_id=2,
                                   available_at=2388.1, raw_text=raw))
    evidence = ['episodic_' + str(node_id)]
    next_window(g, replay, decisions=[
        dict(op='set_name', decision_id='xiu', entity_id='person_2', name='Xiu', evidence_ids=evidence),
        dict(op='assign_alias', decision_id='wearer2', entity_id='person_2', phrase='camera wearer',
             evidence_ids=evidence, rationale='Xiu is now wearing the camera.')])
    assert identity.retrieval_contents(g, 3)[0].startswith('Jake helps')
    assert identity.retrieval_contents(g, node_id)[0].startswith('Xiu packs')
    assert identity.canonicalize_contents(g, [raw], clip_id=3)[0] == [raw]


def test_retirement_keeps_aliases_and_original_character_token_source():
    g, _, _ = run_first()
    # A separate native identity can retire into Jake without losing scoped aliases.
    record = deepcopy(g.character_metadata['character_0']['identity_aliases'][0])
    g.character_metadata['character_0']['identity_aliases'] = []
    g.character_metadata['character_2'] = dict(identity_aliases=[record], merged_character_ids=[])
    raw = '<character_2> watches.'
    g.add_text_node(dict(contents=[raw], retrieval_contents=['character_2 watches.'],
                         embeddings=[[1., 0., 0.]]), 1)
    node_id = g.next_node_id - 1
    base = deepcopy(g)
    identity.apply_conclusions(g, {'a': dict(native_character_id='character_0', canonical_name='Jake')},
        [dict(observation_id='face', feature_id='face_2', entity_id='a')], [], cutoff=1187.92, provenance={})
    assert 'character_2' in g.retired_character_ids
    assert identity.retrieval_contents(g, 3)[0].startswith('Jake helps')
    assert g.nodes[node_id].metadata['source_contents'] == [raw]
    assert identity.retrieval_contents(g, node_id) == ['Jake watches.']
    validate_patch(ConsolidationPatch(ConsolidationSnapshot(0, 1, 1187.92, base), g))


def test_two_window_online_worker_commit_and_checkpoint(tmp_path):
    from consolidation.port import attach_online
    from consolidation.runtime_io import RetrievalPublisher
    g, replay, _, _, first_patch = first_window()
    prompts = []

    def evidence(snapshot):
        updated = deepcopy(replay)
        updated['current_cutoff'] = snapshot.cutoff_timestamp
        updated['current_cutoff_clip'] = snapshot.cutoff_clip_id
        return dict(replay=updated)

    def proposer(packet, directory):
        prompts.append(json.loads((directory / 'prompt_packet.json').read_text()))
        if len(prompts) == 1:
            return dict(first_patch, base_graph_version=packet['base_graph_version'])
        return dict(first_patch, base_graph_version=packet['base_graph_version'],
                    evidence_cutoff_s=packet['current_cutoff'], decisions=[],
                    temporal_handoff='Everyone moves outside.')

    def moss(window, start, directory):
        return dict(session_id='s', start_s=start, cutoff_s=window['current_cutoff'],
                    timestamp_origin='session', run_id='window_' + str(start),
                    segments=[dict(start=start, end=start+1, speaker='S1', text='test')])

    runtime = attach_online(g, evidence, tmp_path / 'jobs', proposer=proposer, moss=moss,
                            embed=lambda texts: [[0., 1., 0.] for _ in texts])
    try:
        assert not g.temporal_handoff
        with runtime.segment(1, 1187.92):
            pass
        runtime.consolidate_until(1187.92)
        assert construction_context(g)[0]['content'].endswith('Jake and others are around the table.')
        assert g.nodes[3].metadata['retrieval_contents'][0].startswith('Jake helps')
        with runtime.segment(2, 2388.1):
            pass
        runtime.consolidate_until(2388.1)
        assert construction_context(g)[0]['content'].endswith('Everyone moves outside.')
        assert 'Jake and others are around the table.' not in json.dumps(prompts[1])
        RetrievalPublisher(tmp_path / 'checkpoints')(runtime.read_graph())
        pointer = json.loads((tmp_path / 'checkpoints/CURRENT.json').read_text())
        with (tmp_path / 'checkpoints/versions' / pointer['version'] / 'graph.pkl').open('rb') as handle:
            restored = pickle.load(handle)
        assert construction_context(restored) == construction_context(g)
        assert identity.retrieval_contents(restored, 3) == identity.retrieval_contents(g, 3)
    finally:
        runtime.close(flush_final=False)


def test_path2_uses_copied_construction_and_accepts_empty_output(monkeypatch):
    module = m3_module('mmagent.memory_processing_qwen')
    for name in ('generate_video_context', 'generate_all_memories', 'generate_memories'):
        assert Path(getattr(module, name).__code__.co_filename).name == 'memory_construction.py'
        assert 'm3_adaptors' in getattr(module, name).__code__.co_filename
    messages = []
    monkeypatch.setattr(module, 'generate_messages', lambda value: messages.append(value) or value)
    monkeypatch.setattr(module, 'get_response', lambda value: ('{"video_description": [], "high_level_conclusions": []}', 0))
    assert module.generate_all_memories([{'type': 'text', 'content': 'Current clip'}]) == ([], [])
    assert 'Generate a detailed and cohesive description' in messages[0][0]['content']
    assert 'A repeated action can still be a distinct event.' in messages[0][0]['content']
    assert 'not as\ncurrent-clip evidence' in messages[0][0]['content']


def test_transcript_labels_use_native_names_and_preserve_voice_provenance(monkeypatch):
    from types import SimpleNamespace
    module = m3_module('mmagent.memory_processing_qwen')
    g = SimpleNamespace(identity_revision=1, temporal_handoff={},
        character_mappings={'character_0': ['voice_489'], 'character_3': ['voice_490'],
                            'character_4': ['voice_491']},
        character_metadata={'character_0': {'canonical_name': 'Jake'},
                            'character_3': {'canonical_name': None}},
        observation_character_mappings={}, reference_character_mappings={},
        identity_observations={},
        # Stale reverse cache must not override native ownership.
        reverse_character_mappings={'voice_489': 'character_3', 'voice_999': 'character_0'})
    voices = {i: [dict(start_time='00:01', end_time='00:03', asr='Keep this transcript verbatim.')]
              for i in (489, 490, 491, 999)}
    before = deepcopy(voices)
    captured = []
    monkeypatch.setattr(module, 'generate_messages', lambda value: captured.append(value) or value)
    monkeypatch.setattr(module, 'get_response', lambda _: ('{"video_description": [], "high_level_conclusions": []}', 0))
    assert module.generate_memories([], {}, voices, 'clip.mp4', video_graph=g) == ([], [])
    parts = captured[0]
    index = next(i for i, part in enumerate(parts) if part.get('content') == 'Voice features:')
    labeled = json.loads(parts[index + 1]['content'])
    assert list(labeled) == ['<voice_489> (Jake)', '<voice_490>', '<voice_491>', '<voice_999>']
    assert list(labeled.values()) == list(voices.values())
    assert voices == before
    assert 'established identities; do not re-infer them' in parts[0]['content']
    assert construction_context(g) == []
    assert not any('CURRENT CANONICAL' in part.get('content', '') for part in parts)


def test_transcript_labels_do_not_promote_ambiguous_or_unconsolidated_identities():
    from types import SimpleNamespace
    from m3_adaptors.construction import label_voice_transcripts
    context = [{'type': 'text', 'content': 'Voice features:'},
               {'type': 'text', 'content': json.dumps({'<voice_489>': [{'asr': 'Hello'}]})}]
    g = SimpleNamespace(identity_revision=1,
        character_mappings={'character_0': ['voice_489'], 'character_1': ['voice_489']},
        character_metadata={'character_0': {'canonical_name': 'Jake'},
                            'character_1': {'canonical_name': 'Xiu'}},
        observation_character_mappings={}, reference_character_mappings={})
    assert label_voice_transcripts(context, g) == context
    g.character_mappings = {'character_0': []}
    g.observation_character_mappings = {'u1': 'character_0'}
    assert label_voice_transcripts(context, g) == context
    g.character_mappings = {'character_0': ['voice_489']}
    g.identity_revision = 0
    assert label_voice_transcripts(context, g) == context
