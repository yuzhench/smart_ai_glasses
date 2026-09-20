"""Path 2 adaptor patch for the pristine ``mmagent.memory_processing`` module.

``process_memories`` is lifted verbatim from the EDITED module and installed
with globals rebound to the pristine module namespace, so a monkeypatched
``memory_processing.parallel_get_embedding`` is honored at call time.
"""
from m3_adaptors._shared import rebind


def process_memories(video_graph, memory_contents, clip_id, type='episodic'):
    def get_memory_embeddings(memory_contents):
        # calculate the embedding for each memory
        model = 'text-embedding-3-large' if getattr(video_graph, 'identity_revision', 0) else 'text-embedding'
        embeddings = parallel_get_embedding(model, memory_contents)[0]
        return embeddings

    def insert_memory(video_graph, memory, type='episodic'):
        # create a new text node for each memory
        new_node_id = video_graph.add_text_node(memory, clip_id, type)
        entities = parse_video_caption(video_graph, memory['contents'][0])
        for entity in entities:
            video_graph.add_edge(new_node_id, entity[1])

    def update_video_graph(video_graph, memories, type='episodic'):
        # append all episodic memories to the graph
        if type == 'episodic':
            # create a new text node for each memory
            for memory in memories:
                insert_memory(video_graph, memory, type)
        # semantic memories can be used to update the existing text nodes, or create new text nodes
        elif type == 'semantic':
            for memory in memories:
                entities = parse_video_caption(video_graph, memory['contents'][0])

                if len(entities) == 0:
                    insert_memory(video_graph, memory, type)
                    continue
                
                # update the existing text node for each memory, if needed
                positive_threshold = 0.85
                negative_threshold = 0
                
                # get all (possible) related nodes            
                node_id = entities[0][1]
                related_nodes = video_graph.get_connected_nodes(node_id, type=['semantic'])
                
                # if there is a node with similarity > positive_threshold, then update the edge weight by +1
                # if there is a node with similarity < negative_threshold, then update the edge weight by -1, and add a new text node and connect it to the existing node
                # otherwise, add a new text node and connect it to the existing node
                create_new_node = True
                
                for node_id in related_nodes:
                    # related nodes to be updated should satisfy two condtions:
                    # 1. the caption entities are a subset of the existing node entities
                    # 2. the semantic similarity between the memory and the existing node shows a positive correlation or a negative correlation
                    
                    # see if the memory entities are a subset of the existing node entities
                    related_node_entities = parse_video_caption(video_graph, video_graph.nodes[node_id].metadata['contents'][0])
                    embedding = video_graph.nodes[node_id].embeddings[0]
                    if all(entity in related_node_entities for entity in entities):
                        similarity = np.dot(memory['embeddings'][0], embedding) / (np.linalg.norm(memory['embeddings'][0]) * np.linalg.norm(embedding))
                        if similarity > positive_threshold:
                            video_graph.reinforce_node(node_id)
                            create_new_node = False
                        elif similarity < negative_threshold:
                            video_graph.weaken_node(node_id)
                            create_new_node = False
                
                if create_new_node:
                    insert_memory(video_graph, memory, type)
    
    from .character_identity import prepare_texts
    retrieval_texts = prepare_texts(video_graph, memory_contents)
    memories_embeddings = get_memory_embeddings(retrieval_texts)

    memories = []
    for memory, retrieval, embedding in zip(memory_contents, retrieval_texts, memories_embeddings):
        memories.append({
            'contents': [memory],
            'embeddings': [embedding],
            'retrieval_contents': [retrieval]
        })

    update_video_graph(video_graph, memories, type)


def apply():
    import mmagent.memory_processing as module

    module.process_memories = rebind(process_memories, module)
