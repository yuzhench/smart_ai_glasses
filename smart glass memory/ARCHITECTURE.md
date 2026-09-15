# Architecture

## 1. Scope and evidence boundary

M3-streamconative combines three concerns:

1. Construct multimodal memory from clips and audio.
2. Optionally compress an existing memory through a callable StreamMCCO-derived operation.
3. Retrieve evidence through one of two alternative backends: native M3 or Mandol through an adapter.

Persistent character/entity consolidation is the current development area.

**Basis:** the maintainer’s description supplied with the documentation request. The repository was not accessible during preparation. “Present” therefore means maintainer-reported, not independently verified in code. Exact class names, file paths, storage engines, models, and execution commands are outside the verified scope.

The graph depicts logical components and interfaces. In particular, **memory view**, **native searchable memory**, and **Mandol search representations** are architectural labels, not asserted implementation classes.

![Architecture overview](architecture.png)


## 2. System boundaries

```text
CONSTRUCTION / PREPARATION
clip + audio → M3 construction → multimodal memory
                                     │
                                     ├─ raw memory ────────────────────┐
                                     └─ optional compression → output ┤
                                                                      ▼
                                                           selected memory view
                                                              /             \
                                                 native representation   M3–Mandol adapter
                                                                               │
                                                                       Mandol representations

QUERY TIME — CHOOSE ONE BACKEND BY CONFIGURATION
query → native retrieval ───────────────────────────────────────→ evidence
  OR
query → Mandol hybrid retrieval → reranking ────────────────────→ evidence

IN PROGRESS
character/entity consolidation → identity and representation consistency
```

There is no required LLM controller between the user query and either retrieval backend. There is also no required dynamic router. Backend selection can be a fixed configuration choice for a run or experiment.

**One retrieval call does not mean one internal operation.** Encoding, multiple candidate searches, fusion, and reranking may occur inside that single call. An optional answer-generation step consumes the returned evidence and lies outside this retrieval boundary.

## 3. Canonical memory: the M3 representation

### 3.1 Inputs and construction

The construction pipeline takes a video clip and its associated audio. Its logical outputs are episodic, semantic, voice, and face nodes, plus the mappings and indexes needed to organize them.

The current description does not establish a particular transcription service, face detector, speaker model, or feature-extraction package. The architecture therefore shows these activities as **M3 memory construction**, rather than introducing unverified dependencies.

### 3.2 Memory components

| Component | Meaning | Architectural distinction |
| --- | --- | --- |
| Episodic node | An observation or event grounded in a clip. | Records what happened in a particular context. |
| Semantic node | An extracted fact or semantic description. | Encodes meaning, not necessarily a standalone physical identity. |
| Voice node | Speaker-related audio evidence/features. | Evidence associated with a voice, not proof of a unique persistent person. |
| Face node | Visual identity evidence/features. | Evidence associated with a face, not proof of complete cross-clip identity resolution. |
| Character mapping | Associations between character identifiers and face/voice evidence. | A mapping structure; it is not equivalent to a fully consolidated person registry. |
| Temporal index | Access structures linking memory to clip/time context. | An index, not an independent memory modality. |

“Face node” is the logical term used here. The exact implementation type tag is not asserted; an inherited implementation could name the corresponding modality differently.

### 3.3 Source-of-truth boundary

For architectural clarity, treat the M3 representation as the canonical memory and Mandol’s units, spaces, entities, and indexes as a derived retrieval representation. The adapter must retain a route back to the originating M3 evidence.

This is the intended ownership boundary, not a claim that the repository already enforces every invariant below. Recommended adapter contracts are:

| Contract | Why it matters |
| --- | --- |
| Retain source node references. | A retrieved unit must be traceable to its evidence. |
| Retain clip/time provenance. | Grouping, temporal questions, and evidence display depend on it. |
| Preserve episodic versus semantic roles. | An extracted fact should not silently become a clip-grounded event. |
| Preserve unresolved identity state. | A fragmented or uncertain identity should not become a confident canonical person merely through adaptation. |
| Track the source memory version. | A derived index should not silently refer to an older compression or identity state. |

## 4. Optional StreamMCCO compression

### 4.1 Invocation model

StreamMCCO-derived compression is an operation that can be called on an existing memory. It is not the construction pipeline itself, a mandatory per-clip stage, or the query-time controller.

```text
Existing M3 memory
    ├─ do not invoke compression → retrieve from raw memory
    └─ invoke compression        → retrieve from a compressed output
```

Whether the implementation mutates the original memory or produces a separate output is not established by the supplied description. The diagram uses a **selected memory view** to cover either case without claiming a particular API.

### 4.2 Downstream implications

Any representation derived from changed content must be refreshed before it is used. For example, changed text can affect native embeddings and Mandol lexical, dense, and sparse representations; changed identifiers can affect adapter references and entities.

These are dependency requirements, not evidence that the current code already implements incremental refresh, snapshot isolation, or automatic invalidation. The compression-to-refresh contract should be verified explicitly.

The current description also does not specify which modalities are compressed, the compression objective, or the treatment of temporal and identity metadata. No lossless-preservation guarantee is claimed.

### 4.3 Compression versus consolidation

**Compression changes how memory is represented or reduces redundancy. Consolidation resolves identity across observations.** They may interact, but the existence of the callable compressor does not establish persistent person identity.

## 5. Retrieval path A: native single-shot retrieval

### 5.1 Execution

```text
User query
    → prepare/encode query
    → native retrieval over the selected memory representation
    → ranked evidence
```

The public retrieval boundary is one invocation. It does not call an LLM controller to plan searches, examine intermediate evidence, or issue a second search.

The current description establishes the single-shot control flow, but not the exact native scoring function, top-k value, candidate filtering order, or index implementation. The graph therefore does not label the native backend as a specific exact-search or approximate-nearest-neighbor algorithm.

### 5.2 Identity and time

Character mappings and temporal indexes remain available in the memory representation. Their existence does not, by itself, establish automatic entity resolution or temporal parsing of every natural-language query.

Where identity/time constraints are used, they should be handled inside the fixed retrieval procedure or supplied as explicit retrieval inputs. This should not be confused with restoring a multi-step LLM controller.

### 5.3 Output boundary

The output is retrieved evidence rather than necessarily a completed natural-language answer. Useful evidence metadata includes the source node, memory type, clip/time provenance, score, and any resolved or unresolved entity references. These fields are a recommended output contract; exact existing return fields need source verification.

## 6. Retrieval path B: M3–Mandol adapter

### 6.1 Purpose

The adapter is the boundary between M3’s multimodal memory and Mandol’s retrieval organization. It translates representations so the project can reuse Mandol’s built-in algorithms rather than replacing the M3 construction pipeline.

The native backend remains independent of this adapter.

### 6.2 Mapping rules

| M3 source | Mandol target | Scope and caveat |
| --- | --- | --- |
| Episodic memory | Memory units | Carry the episodic content and references to source evidence. Exact one-to-one cardinality is not specified. |
| Clip membership | Clip-based spaces containing the corresponding units | The stated grouping boundary is a clip. Do not infer an additional fixed-size parent window. |
| Semantic memory | Hierarchical-memory-like representation | The existence of this mapping does not establish particular levels, automatically generated summaries, or a specific hierarchy depth. |
| Character mapping | Entity representation | Mapping a character into an entity does not repair upstream identity fragmentation. |

Voice and face evidence remains part of M3 memory and can be reached through source/identity references. No separate native Mandol face or voice search branch is claimed.

### 6.3 Preparation versus query time

**Preparation / refresh:** adapt the selected M3 memory, organize units/spaces/entities, and prepare the searchable lexical, dense, and sparse representations.

**Query time:** prepare the query, search the existing representations, combine candidates, rerank, and return evidence.

The whole graph should not need to be re-adapted merely because a new query arrives. This separation is an architectural requirement; the repository’s exact caching and refresh mechanisms remain to be checked.

### 6.4 Hybrid retrieval topology

```text
                                   ┌─ BM25 lexical search ───────┐
User query → query preparation ─────┼─ 1024-D dense search ──────┼─→ candidate fusion
             tokenize / encode     └─ Sparse-vector search ─────┘          │
                                                                          ▼
                                                                       reranker
                                                                          │
                                                                          ▼
                                                                    ranked evidence
```

The three candidate branches contribute to a combined candidate pool. **The diagram specifies logical parallel branches, not a claim that the implementation runs them concurrently.**

Important boundaries:

- **Encoding is upstream.** Memory-side encoding prepares searchable representations; query-side encoding produces query representations before the relevant searches.
- **1024 dimensions apply to dense vectors.** The sparse representation is a separate branch and is not described as a 1024-dimensional dense vector.
- **BM25 is not sparse-vector search.** The architecture keeps lexical matching and the distinct sparse-vector branch separate, as stated in the project description.
- **Fusion precedes reranking in the logical flow.** The specific fusion formula, branch weights, candidate limits, and reranking model are not established here.

Mandol may perform multiple internal search/scoring operations while still exposing one controller-free retrieval call to the application. It is an alternative to native retrieval, not a mandatory second stage following it.

## 7. Dependency map

| Component | Depends on | Does not inherently require |
| --- | --- | --- |
| M3 memory construction | Clip/audio inputs and the configured construction models/processors. | Mandol retrieval or an LLM retrieval controller. |
| StreamMCCO compression | Existing memory and the callable compression implementation. | Invocation for every clip or every query. |
| Native retrieval | Selected M3 memory representation and its query/search machinery. | The M3–Mandol adapter or Mandol indexes. |
| M3–Mandol adapter | Selected M3 content, clip membership, semantic content, and character mappings. | Replacement of the M3 memory-construction pipeline. |
| Mandol hybrid retrieval | Adapted memory, BM25/dense/sparse representations, query preparation, candidate fusion, and reranker. | A preceding native retrieval call or an LLM controller loop. |
| Character/entity consolidation, in progress | Identity evidence and existing associations; final evidence/model dependencies are still being designed. | A claim that compression has already resolved identity. |
| Optional answer generation | Query and retrieved evidence. | Inclusion within the retrieval-latency boundary. |

The documentation does not assert a particular vector database, approximate-search library, ASR provider, embedding/reranker checkpoint, model-hosting location, GPU requirement, or package version. Those are implementation dependencies to populate after auditing the source.

## 8. Ongoing work: character/entity consolidation

### 8.1 The unresolved problem

The system already has character mappings and a character-to-entity adapter mapping. What remains under development is a reliable process for deciding which observations and character identifiers belong to the same persistent person.

A retrieval adapter preserves the quality of its input identity representation; it does not automatically improve it. Multiple character IDs for the same person can therefore remain multiple entities downstream.

### 8.2 Proposed integration boundary — not completed functionality

```text
Existing voice/face evidence + character associations
    → consolidation process
    → accepted identity/alias updates
    → update M3 character mappings
    → refresh affected Mandol entities and other derived representations
```

The central design obligation is consistency between the canonical associations and derived retrieval data. A merge should retain evidence provenance and uncertainty rather than silently replacing all historical observations with an unsupported identity assertion.

The precise inference method, confidence rules, use of embedding similarity, scheduling, and write-back API remain design choices. This document does not claim a particular consolidation interval or stronger-model endpoint is implemented.

If a future design rewrites memory text into canonical names, the corresponding embeddings and lexical/sparse indexes must be updated. Canonical-name rewriting is **not** presented as existing functionality here.

### 8.3 Interactions with compression

Compression and consolidation may both change content or references used by retrieval. Their relative execution order must therefore be made explicit in the implementation. The present architecture does not assume that either one automatically triggers the other.

## 9. Validation checklist

The following are acceptance checks, not claimed test results:

| Check | Expected architectural property |
| --- | --- |
| Retrieve without invoking compression. | The raw-memory route remains usable. |
| Retrieve after compression and refresh. | Evidence refers to the selected memory state, not stale derived data. |
| Run the native backend without Mandol initialization. | Optional-backend separation is real. |
| Trace a Mandol result to its source. | Units retain M3 evidence and clip/time provenance. |
| Inspect a query trace. | Exactly one application-level retrieval invocation and zero controller calls. |
| Inspect hybrid search. | BM25, dense, and sparse candidates are combined before final reranking. |
| Compare identity state before/after adaptation. | The adapter does not claim to have consolidated fragmented characters. |
| Apply a future identity update. | Mappings, entities, references, and affected indexes remain consistent. |

A useful timing record separates query preparation, candidate search, fusion, reranking, evidence assembly, and any later answer generation. Total retrieval latency should be measured end to end; summing branch durations is misleading when searches execute concurrently.

## 10. Figure semantics

Solid arrows indicate primary data or control flow. Dotted arrows indicate reads from prepared representations. The dashed orange box marks ongoing consolidation work. The native and Mandol paths are alternatives selected by configuration; showing both does not imply both execute for every query.

The architecture deliberately makes three distinctions visible: **optional compression versus required construction**, **preparation versus query-time retrieval**, and **existing character mappings versus unfinished identity consolidation**.
