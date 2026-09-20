"""Path 2 adaptor patch for the pristine ``mmagent.memory_processing_qwen``.

``process_memories`` is lifted verbatim from the EDITED module and installed
with globals rebound to the pristine module namespace, so a monkeypatched
``memory_processing_qwen.get_embeddings_batch`` is honored at call time.
"""
from m3_adaptors._shared import rebind


def process_memories(
    video_graph, memory_contents, clip_id, type="episodic", metrics=None,
    precomputed_embeddings=None, precomputed_embedding_ms=None, precomputed_contents=None,
):
    """Embed generated text and apply its node/edge mutations to the graph."""
    metrics = metrics if metrics is not None else {}
    metrics.update({
        "memory_type": type,
        "input_count": len(memory_contents),
        "text_embedding_ms": 0.0,
        "text_embedding_count": 0,
        "graph_update_ms": 0.0,
        "nodes_added": 0,
        "directed_edges_added": 0,
    })
    if not memory_contents:
        return metrics

    nodes_before = set(video_graph.nodes)
    edges_before = set(video_graph.edges)
    from .character_identity import prepare_texts, consolidated
    retrieval_texts = prepare_texts(video_graph, memory_contents)
    if precomputed_embeddings is None:
        embedding_started = time.perf_counter()
        embeddings = get_embeddings_batch(
            "text-embedding-3-large", retrieval_texts
        )[0]
        metrics["text_embedding_ms"] = (
            time.perf_counter() - embedding_started
        ) * 1000
        metrics["text_embedding_execution"] = "current_process"
    else:
        if consolidated(video_graph) and precomputed_contents != retrieval_texts:
            raise ValueError('precomputed embedding input does not match canonical retrieval text')
        embeddings = precomputed_embeddings
        if len(embeddings) != len(memory_contents):
            raise ValueError("precomputed embedding count does not match memories")
        metrics["text_embedding_ms"] = float(precomputed_embedding_ms or 0.0)
        metrics["text_embedding_execution"] = "precomputed_handoff"
    metrics["text_embedding_count"] = len(embeddings)
    memories = [
        {"contents": [memory], "embeddings": [embedding], "retrieval_contents": [retrieval]}
        for memory, retrieval, embedding in zip(memory_contents, retrieval_texts, embeddings)
    ]

    def insert_memory(memory):
        new_node_id = video_graph.add_text_node(memory, clip_id, type)
        entities = parse_video_caption(video_graph, memory["contents"][0])
        for entity in entities:
            video_graph.add_edge(new_node_id, entity[1])

    graph_started = time.perf_counter()
    if type == "episodic":
        for memory in memories:
            insert_memory(memory)
    elif type == "semantic":
        for memory in memories:
            entities = parse_video_caption(video_graph, memory["contents"][0])
            if not entities:
                insert_memory(memory)
                continue
            positive_threshold = 0.85
            negative_threshold = 0
            entity_node_id = entities[0][1]
            related_nodes = video_graph.get_connected_nodes(
                entity_node_id, type=["semantic"]
            )
            create_new_node = True
            for related_node_id in related_nodes:
                related_node_entities = parse_video_caption(
                    video_graph,
                    video_graph.nodes[related_node_id].metadata["contents"][0],
                )
                embedding = video_graph.nodes[related_node_id].embeddings[0]
                if all(entity in related_node_entities for entity in entities):
                    similarity = np.dot(memory["embeddings"][0], embedding) / (
                        np.linalg.norm(memory["embeddings"][0])
                        * np.linalg.norm(embedding)
                    )
                    if similarity > positive_threshold:
                        video_graph.reinforce_node(related_node_id)
                        create_new_node = False
                    elif similarity < negative_threshold:
                        video_graph.weaken_node(related_node_id)
                        create_new_node = False
            if create_new_node:
                insert_memory(memory)
    else:
        raise ValueError("type must be episodic or semantic")
    metrics["graph_update_ms"] = (time.perf_counter() - graph_started) * 1000
    metrics["nodes_added"] = len(set(video_graph.nodes) - nodes_before)
    metrics["directed_edges_added"] = len(set(video_graph.edges) - edges_before)
    return metrics


def apply():
    import time

    import mmagent.memory_processing_qwen as module
    import mmagent.utils.chat_api as chat_api

    module.time = time
    module.get_embeddings_batch = chat_api.get_embeddings_batch
    module.process_memories = rebind(process_memories, module)
