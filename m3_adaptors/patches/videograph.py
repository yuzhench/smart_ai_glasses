"""Path 2 adaptor patch for the pristine ``mmagent.videograph`` module.

The definitions below are lifted verbatim from the consolidation-aware EDITED
``mmagent/videograph.py``. ``apply()`` installs them onto the pristine
``VideoGraph`` class, rebinding each function's globals to the pristine module
namespace so helper lookups (``character_identity``, ``np``, ``logger`` ...)
resolve through the target module at call time.
"""
from m3_adaptors._shared import rebind

_WRAP_ONLY = (
    'add_edge', 'update_edge_weight', 'reinforce_node', 'weaken_node',
    'fix_collisions', 'prune_memory_by_node_type',
)

_LIFTED = (
    '__getstate__', 'resolve_identity', 'reindex_identity_text',
    'get_retrieval_contents', 'load_current', 'add_img_node', 'add_voice_node',
    'add_text_node', 'update_node', 'refresh_equivalences', 'order_character',
    'search_text_nodes', 'truncate_memory_by_clip',
)

# Decorators from the EDITED class body, applied in apply() after the raw
# functions are rebound into the pristine module namespace.
_RUNTIME_DECORATED = (
    'add_img_node', 'add_voice_node', 'add_text_node', 'update_node',
    'refresh_equivalences', 'order_character', 'truncate_memory_by_clip',
)
_CLASSMETHODS = ('load_current',)

_originals = {}


def _runtime_mutation(method):
    """Serialize small native mutations with snapshot reads, never model calls."""
    from functools import wraps
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        runtime = getattr(self, '_consolidation_runtime', None)
        if runtime is None:
            return method(self, *args, **kwargs)
        with runtime.lock:
            return method(self, *args, **kwargs)
    return wrapped


class _Lifted:
    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop('_consolidation_runtime', None)
        state.pop('speaker_mapper', None)
        return state

    def resolve_identity(self, feature_id, *, observation_id=None, memory_reference=None):
        return character_identity.resolve_identity(self, feature_id,
            observation_id=observation_id, memory_reference=memory_reference)

    def reindex_identity_text(self, embed=None):
        return character_identity.reindex_text(self, embed)

    def get_retrieval_contents(self, node_id):
        return character_identity.retrieval_contents(self, node_id)

    def load_current(cls, session_directory):
        """Pin one complete native publication for this retrieval request."""
        import hashlib
        import pickle
        import re
        from pathlib import Path
        root = Path(session_directory)
        version = json.loads((root/'CURRENT.json').read_text())['version']
        if not re.fullmatch(r'v_[0-9a-f]{64}', version):
            raise ValueError('invalid publication version')
        location = root/'versions'/version
        manifest = json.loads((location/'manifest.json').read_text())
        ready = json.loads((location/'retrieval_ready.json').read_text())
        if (not manifest.get('retrieval_complete') or manifest.get('version') != version
                or ready.get('status') != 'ready' or ready.get('graph_version') != version):
            raise ValueError('retrieval publication is not ready')
        for name, expected in manifest['sha256'].items():
            path = location/name
            if (Path(name).is_absolute() or '..' in Path(name).parts or not path.is_file()
                    or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ValueError('retrieval publication integrity failure')
        with (location/'graph.pkl').open('rb') as handle:
            graph = pickle.load(handle)
        if graph.graph_version != version:
            raise ValueError('native graph version does not match retrieval publication')
        return graph

    def add_img_node(self, imgs):
        """Add a new face node with initial image embedding(s).
        
        Args:
            img_embedding: Single embedding or list of embeddings
        """
        node = self.Node(self.next_node_id, 'img')
        
        img_embeddings = imgs['embeddings']
        node.embeddings.extend(img_embeddings[:self.max_img_embeddings])
        
        node.metadata['contents'] = imgs['contents']
        
        self.nodes[self.next_node_id] = node
        self.next_node_id += 1

        logger.debug(f"Image node added with ID {node.id}")
        if character_identity.consolidated(self):
            character_identity.admit_observations(self, 'face_' + str(node.id), imgs['contents'], 0)

        return node.id

    def add_voice_node(self, audios):
        """Add a new voice node with initial audio embedding(s).
        
        Args:
            audio_embedding: Single embedding or list of embeddings
        """
        node = self.Node(self.next_node_id, 'voice')
        
        audio_embeddings = audios['embeddings']
        node.embeddings.extend(audio_embeddings[:self.max_audio_embeddings])
        
        node.metadata['contents'] = audios['contents']
        
        self.nodes[self.next_node_id] = node
        self.next_node_id += 1

        logger.debug(f"Voice node added with ID {node.id}")
        if character_identity.consolidated(self):
            character_identity.admit_observations(self, 'voice_' + str(node.id), audios['contents'], 0)

        return node.id

    def add_text_node(self, text, clip_id, text_type='episodic'):
        """Add a new text node with episodic or semantic content.
        
        Args:
            text: Text content
            text_type: Type of text node ('episodic' or 'semantic')
        """
        if text_type not in ['episodic', 'semantic']:
            raise ValueError("text_type must be either 'episodic' or 'semantic'")

        node = self.Node(self.next_node_id, text_type)
        node.embeddings = text['embeddings']
        node.metadata['contents'] = text['contents']
        node.metadata['timestamp'] = clip_id
        if character_identity.consolidated(self):
            expected, trace = character_identity.canonicalize_contents(self, text['contents'])
            if text.get('retrieval_contents') != expected:
                raise ValueError('text embeddings must be computed from canonical retrieval contents')
            node.metadata.update(retrieval_contents=expected, retrieval_identity_trace=trace,
                                 embedding_input_fingerprint=character_identity.fingerprint(expected))
        
        self.nodes[self.next_node_id] = node
        self.text_nodes.append(node.id)  # Add to ordered list
        if clip_id not in self.text_nodes_by_clip:
            self.text_nodes_by_clip[clip_id] = []
        self.text_nodes_by_clip[clip_id].append(node.id)
        if text_type == 'episodic':
            if clip_id not in self.event_sequence_by_clip:
                self.event_sequence_by_clip[clip_id] = []
            self.event_sequence_by_clip[clip_id].append(node.id)

        self.next_node_id += 1

        logger.debug(f"Text node of type {text_type} added with ID {node.id} and contents: {text['contents']}")

        return node.id

    def update_node(self, node_id, update_info):
        """Update an existing node.
        
        Args:
            node_id: ID of target node
            update_info: Dictionary of update information
            
        Returns:
            Boolean indicating success
        """
        if node_id not in self.nodes:
            raise ValueError(f"Node {node_id} not found")

        node = self.nodes[node_id]
        
        first_index = len(node.metadata['contents'])
        node.metadata['contents'].extend(update_info['contents'])
        feature = ('face_' if node.type == 'img' else 'voice_') + str(node_id)
        character_identity.admit_observations(self, feature, update_info['contents'], first_index)
        
        embeddings = update_info['embeddings']

        if node.type == 'img':
            max_emb = self.max_img_embeddings
        elif node.type == 'voice':
            max_emb = self.max_audio_embeddings
        else:
            raise ValueError("Node type must be either 'img' or'voice' to add embeddings")

        # Combine existing and new embeddings
        all_embeddings = node.embeddings + embeddings

        # If exceeding max limit, randomly select embeddings
        if len(all_embeddings) > max_emb:
            node.embeddings = random.sample(all_embeddings, max_emb)
        else:
            node.embeddings = all_embeddings
        
        logger.debug(f"Node {node_id} updated with {len(embeddings)} embeddings")

        return True

    def refresh_equivalences(self):
        if character_identity.refresh_characters(self):
            return
        # Initialize disjoint set data structure
        parent = {}
        rank = {}
        
        def find(x):
            # Find root/representative of set with path compression
            if x not in parent:
                parent[x] = x
                rank[x] = 0
                return x
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]
            
        def union(x, y):
            # Union by rank
            px, py = find(x), find(y)
            if px == py:
                return
            if rank[px] < rank[py]:
                px, py = py, px
            parent[py] = px
            if rank[px] == rank[py]:
                rank[px] += 1
        
        # Process each face/voice node
        filtered_equivalence_nodes = []
        
        for node_id in self.nodes:
            if self.nodes[node_id].type == 'voice':        
                # every voice node should have no more than one voice-face mapping        
                # get filtered semantic nodes and their contents
                filtered_semantic_nodes = self.fix_collisions(node_id, mode='eq_only')
                
                filtered_equivalence_nodes.extend([node for node in filtered_semantic_nodes if self.nodes[node].metadata['contents'][0].lower().startswith("equivalence")])
            elif self.nodes[node_id].type == 'img':
                # no filtering for face nodes
                connected_semantic_nodes = self.get_connected_nodes(node_id, type=['semantic'])
                
                filtered_equivalence_nodes.extend([node for node in connected_semantic_nodes if self.nodes[node].metadata['contents'][0].lower().startswith("equivalence")])
            else:
                continue
            
        # remove duplicates and get the equivalences (equivalences can also be generated by LLM, not implemented here)
        filtered_equivalence_nodes = list(set(filtered_equivalence_nodes))
        equivalences = [self.nodes[node].metadata['contents'][0] for node in filtered_equivalence_nodes]
        
        # Add equivalent nodes to disjoint sets
        for equivalence in equivalences:
            entities = parse_video_caption(self, equivalence)
            if len(entities) >= 2:
                # Union all entities in this equivalence group
                anchor_node = entities[0][1]  # Get ID of first entity
                for entity in entities[1:]:
                    union(anchor_node, entity[1])

        # Group nodes by their representative (character)
        character_mappings = {}
        character_count = 0
        root_to_character = {}
        
        # Find all nodes that are in the disjoint sets
        for x in parent:
            root = find(x)
            tag = f"face_{x}" if self.nodes[x].type == 'img' else f"voice_{x}"
            if root not in root_to_character:
                root_to_character[root] = f"character_{character_count}"
                character_count += 1
            character = root_to_character[root]
            if character not in character_mappings:
                character_mappings[character] = []
            character_mappings[character].append(tag)
        
        for x in self.nodes:
            if x in parent or self.nodes[x].type not in ['img', 'voice']:
                continue
            root = find(x)
            tag = f"face_{x}" if self.nodes[x].type == 'img' else f"voice_{x}"
            if root not in root_to_character:
                root_to_character[root] = f"character_{character_count}"
                character_count += 1
            character = root_to_character[root]
            if character not in character_mappings:
                character_mappings[character] = []
            character_mappings[character].append(tag)
        
        # create reverse mapping
        reverse_character_mappings = {}
        for character, tags in character_mappings.items():
            for tag in tags:
                reverse_character_mappings[tag] = character
            
        self.character_mappings = character_mappings
        self.reverse_character_mappings = reverse_character_mappings

        logger.info(f"Found {character_count} characters")

    def order_character(self):
        """
        ablation study, without equivalence
        """
        if character_identity.refresh_characters(self):
            return
        character_mappings = {}
        reverse_character_mappings = {}
        character_count = 0

        for node_id in self.nodes:
            node_type = self.nodes[node_id].type
            if node_type not in ['img', 'voice']:
                continue

            character = f"character_{character_count}"
            character_count += 1

            tag = f"face_{node_id}" if node_type == 'img' else f"voice_{node_id}"

            character_mappings[character] = [tag]
            reverse_character_mappings[tag] = character

        self.character_mappings = character_mappings
        self.reverse_character_mappings = reverse_character_mappings

        logger.info(f"Assigned {character_count} characters (no equivalence used)")

    def search_text_nodes(self, query_embeddings, range_nodes=[], mode="max"):
        """Search for text nodes using text embeddings.
        
        Args:
            query_embeddings: Query embeddings
            range_nodes: Optional list of nodes to restrict search to
            mode: Similarity calculation mode ('mean', 'sum', 'max', 'min')
            
        Returns:
            List of (node_id, similarity_score) tuples sorted by score
        """
        if (character_identity.consolidated(self) and self.identity_dirty
                and not getattr(self, 'identity_reindex_async', False)):
            self.reindex_identity_text()
        # Get target nodes
        if range_nodes:
            text_nodes = []
            for node_id in range_nodes:
                text_nodes.extend(self.get_connected_nodes(node_id, type=['episodic', 'semantic']))
            text_nodes = list(set(text_nodes))
        else:
            text_nodes = self.text_nodes
        target_nodes = [(node_id, self.nodes[node_id].embeddings) for node_id in text_nodes]
        
        # Calculate similarities in parallel using numpy
        node_ids, node_embeddings = zip(*target_nodes) if target_nodes else ([], [])
        if not node_ids:
            return []
            
        # Convert to numpy arrays for vectorized operations
        node_embeddings = np.array(node_embeddings)
        query_embeddings = np.array(query_embeddings)
        
        # Get shape parameters for better readability
        n_queries = query_embeddings.shape[0]
        n_nodes = node_embeddings.shape[0]
        n_embeddings = node_embeddings.shape[1]
        embedding_dim = node_embeddings.shape[-1]
        
        # Calculate similarities
        similarities = cosine_similarity(query_embeddings.reshape(-1, embedding_dim), node_embeddings.reshape(-1, embedding_dim))
        # Reshape back to (n_queries, n_nodes, n_embeddings)
        similarities = similarities.reshape(n_queries, n_nodes, n_embeddings)
        
        # Apply the specified mode
        if mode == "sum":
            # For sum mode: first average across embeddings, then sum across queries
            similarities = np.sum(np.mean(similarities, axis=2), axis=0)
        else:
            # For other modes: apply directly to all similarities
            if mode == "mean":
                similarities = np.mean(similarities, axis=(0, 2))
            elif mode == "max":
                similarities = np.max(similarities, axis=(0, 2))
            elif mode == "min":
                similarities = np.min(similarities, axis=(0, 2))
            else:
                raise ValueError(f"Invalid mode: {mode}")
        
        # Create results
        results = [(node_id, sim) for node_id, sim in zip(node_ids, similarities)]
        return sorted(results, key=lambda x: x[1], reverse=True)

    def truncate_memory_by_clip(self, clip_id, refresh=True):
        if character_identity.consolidated(self) and clip_id < getattr(self, 'identity_cutoff_clip', 0):
            raise ValueError('select an earlier immutable graph version for this cutoff')
        # truncate the memory by clip_id
        # remove all nodes that are after the clip_id
        # return the truncated memory
        
        # find the last node with clip_id
        last_node_id = None
        for node_id, node in self.nodes.items():
            if node.type in ['episodic', 'semantic'] and node.metadata['timestamp'] == clip_id:
                last_node_id = node_id
        if last_node_id is None:
            return
        # remove all nodes that are after the last_node_id
        to_del = []
        for node_id in self.nodes.keys():
            if node_id > last_node_id:
                to_del.append(node_id)
        for node_id in to_del:
            del self.nodes[node_id]
        # remove all edges that are after the last_node_id
        to_del = []
        for edge in self.edges.keys():
            if edge[0] > last_node_id or edge[1] > last_node_id:
                to_del.append(edge)
        for edge in to_del:
            del self.edges[edge]
        # remove all text nodes that are after the last_node_id
        to_del = []
        for node_id in self.text_nodes:
            if node_id > last_node_id:
                to_del.append(node_id)
        for node_id in to_del:
            self.text_nodes.remove(node_id)
        # update the text_nodes_by_clip
        to_del = []
        for clip, _ in self.text_nodes_by_clip.items():
            if clip > clip_id:
                to_del.append(clip)
        for clip in to_del:
            del self.text_nodes_by_clip[clip]
        # update the event_sequence_by_clip
        to_del = []
        for clip, _ in self.event_sequence_by_clip.items():
            if clip > clip_id:
                to_del.append(clip)
        for clip in to_del:
            del self.event_sequence_by_clip[clip]
        # update the equivalences
        if refresh:
            self.refresh_equivalences()
        return


def apply():
    import mmagent.character_identity as character_identity
    import mmagent.videograph as module

    module.character_identity = character_identity
    module._runtime_mutation = _runtime_mutation
    cls = module.VideoGraph
    lifted = vars(_Lifted)
    for name in _LIFTED:
        func = rebind(lifted[name], module)
        if name in _RUNTIME_DECORATED:
            func = module._runtime_mutation(func)
        if name in _CLASSMETHODS:
            func = classmethod(func)
        setattr(cls, name, func)
    for name in _WRAP_ONLY:
        original = _originals.setdefault(name, getattr(cls, name))
        setattr(cls, name, module._runtime_mutation(original))
