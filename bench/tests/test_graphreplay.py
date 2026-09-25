"""Offline tests for bench.graphreplay: fake graph in, markdown pair out."""
import pickle
from types import SimpleNamespace

from bench import graphreplay


def _fake_graph(mappings=None, metadata=None):
    nodes = {
        0: SimpleNamespace(id=0, type="voice",
                           metadata={"contents": ["Deepgram: hello there"]},
                           embeddings=[[0.1]]),
        4: SimpleNamespace(id=4, type="episodic",
                           metadata={"contents": ["a scene"], "timestamp": 1},
                           embeddings=[[0.2]]),
    }
    return SimpleNamespace(
        nodes=nodes,
        edges={(0, 4): 1, (4, 0): 1},
        character_mappings=mappings or {"character_0": ["voice_0"]},
        character_metadata=metadata or {},
        refresh_info="internal",
    )


def test_capture_pair_writes_matching_format(tmp_path):
    replayer = graphreplay.GraphReplayer(tmp_path)
    graph = _fake_graph()
    pending = replayer.before(graph, 1200.0)
    assert pending["ordinal"] == "first"
    replayer.after(pending, _fake_graph(mappings={
        "character_0": ["voice_0", "face_4"], "character_1": ["voice_9"]},
        metadata={"character_0": {"canonical_name": "Jake", "aliases": ["J"],
                                  "identity_aliases": [{"phrase": "camera wearer"}]}}))

    before = (tmp_path / "graphs" / "before_first_consolidation.md").read_text()
    after = (tmp_path / "graphs" / "after_first_consolidation.md").read_text()
    assert before.startswith("# Before the first consolidation (1,200 s)\n")
    assert "**2 nodes · 1 links** — 1 events, 0 inferences, 1 voices, 0 faces." in before
    assert "1 characters: 1 single-feature mappings, 0 mappings joining" in before
    assert "[voice_0](#before-the-first-consolidation-1-200-s-node-0)" in before
    assert "- **4** · clip 1: a scene" in before
    assert "embeddings" not in before
    assert "refresh_info" not in before
    assert "2 characters: 1 single-feature mappings, 1 mappings joining" in after
    assert "| Alias | Character | Canonical name | Face / voice |" in after
    assert "| camera wearer | character_0 | Jake | [voice_0]" in after
    assert "| — | character_1 | — | [voice_9]" in after
    assert "| J | character_0" not in after

    # Pickle round-trips for real replay; README indexes the pair.
    with open(tmp_path / "graphs" / "before_first_consolidation.pkl", "rb") as handle:
        assert pickle.load(handle).nodes[4].type == "episodic"
    readme = (tmp_path / "graphs" / "README.md").read_text()
    assert "| Before first consolidation | 1,200 s | 2 | 2 | 1 |" in readme
    assert "| After first consolidation | 1,200 s | 2 | 2 | 2 |" in readme


def test_discard_rolls_back_ordinal(tmp_path):
    replayer = graphreplay.GraphReplayer(tmp_path)
    replayer.discard(replayer.before(_fake_graph(), 600.0))
    pending = replayer.before(_fake_graph(), 1200.0)
    assert pending["ordinal"] == "first"
    replayer.after(pending, _fake_graph())
    assert (tmp_path / "graphs" / "after_first_consolidation.md").exists()
    assert not (tmp_path / "graphs" / "after_second_consolidation.md").exists()
