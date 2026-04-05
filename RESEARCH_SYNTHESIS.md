# Workspace Intelligence Layer - Research Synthesis

## Date: 2026-02-09
## Updated: 2026-02-11 (BMAD Status Update)
## Agents: 3 parallel research agents (Code Intelligence Systems, Ontology Design, Pipeline Strategy)
## Methodology: BMAD Method (Level 2-3, BMad Method Track)

> **See PROJECT_PLAN.md for the current main plan and roadmap.**
> This document contains the original research, BMAD epics, and detailed ADRs.

---

## BMAD Status Tracker (Updated 2026-02-11)

### Phase Status
| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1: Analysis | DONE | 3-agent deep research |
| Phase 2: Planning | DONE | This document |
| Phase 3: Solutioning | DONE | ontology.py v2, ONTOLOGY_DESIGN.md |
| Phase 4: Implementation | IN PROGRESS | See epic status below |

### Epic Status
| Epic | Status | Completion | Blockers |
|------|--------|-----------|----------|
| 1. Foundation | PARTIAL | ~40% | Orchestrator not wired. MODULE nodes missing. IMPORTS edges not connected. |
| 2. Intelligence | NOT STARTED | 0% | Blocked by Epic 1 completion |
| 3. Self-Healing | NOT STARTED | 0% | Blocked by Epic 2 |
| 4. Consumption | NOT STARTED | 0% | Blocked by Epic 1-2 |
| 5. Quality & Scale | PARTIAL | ~20% | Viewer built early (5.4 done). Scale strategy defined (ADR-007/008). |

### Story Status (Epic 1 - Foundation)
| Story | Status | Notes |
|-------|--------|-------|
| 1.1 Upgrade ontology to v2 | DONE | ontology.py has 20 nodes, 27 edges |
| 1.2 Fix scanner.py bugs | PARTIAL | .csproj bug still exists, monorepo not done |
| 1.3 Bridge scanner → graph | DONE | bridge.py converts scanner → graph |
| 1.4 Build pass1_treesitter.py | DONE (code) | File exists but NOT wired into orchestrator |
| 1.5 Build pass2_patterns.py | DONE (code) | File exists but NOT wired into orchestrator |
| 1.6 Build pipeline orchestrator | NOT DONE | Skeleton only. This is the critical blocker. |

### Story Status (Epic 5 - Quality & Scale) — Built Early
| Story | Status | Notes |
|-------|--------|-------|
| 5.4 Build graph viewer | DONE | D3.js viewer with filtering, search, SSE live updates |
| 5.4+ Runtime layer | DONE | test-shop probe → WI server → viewer (not in original plan) |

### New Work Not in Original Plan
| What | Status | Where |
|------|--------|-------|
| Runtime activity probe | DONE | test-shop/src/services/wi-probe.js |
| SSE runtime events | DONE | viewer/server.py, viewer/index.html |
| Viewer folder picker | DONE | viewer/server.py (native Windows dialog) |
| PROJECT_PLAN.md | DONE | Main plan document created |
| ADR-007/008/009 | DONE | Scale/scan/runtime decisions documented |

### Critical Gaps Identified
1. **Pipeline orchestrator** (Story 1.6) — passes exist but don't run together
2. **MODULE nodes for directories** — ontology defines MODULE but nothing creates it
3. **IMPORTS edges** — pass1_treesitter.py has code but isn't wired
4. **Viewer scale strategy** — 1000+ nodes will freeze browser (ADR-007: focal point navigation)
5. **3 scan modes needed** — snapshot/incremental/expansion (ADR-008)

---

## Executive Summary

After deep research into Sourcegraph SCIP, Aider, Cursor, Continue.dev, CodeQL, Tree-sitter Stack Graphs, Blar, CodePrism, LocAgent, and GraphGen4Code, the findings converge on one key insight:

**No open-source project combines all of:**
1. Behavioral edge types (db_read, api_call, cache_write)
2. Cross-service architectural graph
3. Natural-language "stories" for LLM consumption
4. Self-healing auto-updating metadata
5. MCP-exposed AI-agent-consumable format

This is the market gap. Our system fills it.

---

## 1. Competitive Landscape

### Tier 1: Embedding/Similarity (market is saturated)
- **Cursor**: Merkle tree change detection, AST chunking, Turbopuffer vector DB
- **Continue.dev**: Tree-sitter AST chunking, LanceDB local vectors, all-MiniLM-L6-v2
- **Limitation**: No relationships, no structure, no architecture awareness

### Tier 2: Structural Graph (growing)
- **SCIP/Sourcegraph**: Human-readable symbol IDs, cross-repo references, 10+ language indexers
- **Aider**: Tree-sitter + NetworkX + PageRank ranking, token budget optimization
- **Blar**: SCIP + Tree-sitter + Neo4j, AI debugging agent
- **CodePrism**: Rust-based, 1000 files/second, MCP server with 20 tools
- **LocAgent (Yale)**: Graph-guided LLM navigation, 92.7% accuracy
- **Limitation**: No behavioral semantics (can't distinguish db_read from api_call)

### Tier 3: Semantic/Behavioral (our target)
- **Thorbit (inspiration)**: 12 node types, 9 behavioral edge types, self-healing
- **CodeQL**: Deep data flow + taint tracking, but no incremental updates, heavy
- **Our system**: Should combine Tier 2 structural analysis with Tier 3 behavioral semantics

---

## 2. Key Techniques to Adopt

| Technique | Source | Benefit |
|-----------|--------|---------|
| Human-readable symbol IDs | SCIP | Cross-repo globally unique identifiers |
| PageRank importance ranking | Aider | Not all code is equally important |
| Merkle tree change detection | Cursor | O(log n) detection of what changed |
| Content-hash caching | Cursor | Skip re-analysis of unchanged code |
| AST-guided chunking | Cursor/Continue | Semantic splits at function/class boundaries |
| Independent file analysis | Stack Graphs | Analyze files independently, merge at query time |
| SCIP + Tree-sitter hybrid | Blar | SCIP for references, Tree-sitter classifies type |
| Graph traversal tools for LLM | LocAgent | SearchEntity, TraverseGraph, RetrieveEntity |
| MCP server exposure | CodePrism/Augment | Any AI agent can consume the intelligence |
| Prompt caching | Anthropic | 10x cheaper on repeated system prompts |

---

## 3. Optimal Ontology (v2)

### Node Types: 20 (was 14, +6)

**Tier 1 - Macro (Architecture level): 5 types**
- WORKSPACE, PROJECT, SERVICE, RESOURCE, EXTERNAL_API

**Tier 2 - Meso (Component level): 6 types**
- MODULE, FILE, ROUTER (new), COLLECTION (new), INFRA_CONFIG, QUEUE (new)

**Tier 3 - Micro (Code element level): 9 types**
- ENDPOINT, FUNCTION, ASYNC_HANDLER, DATA_MODEL, EVENT
- MIDDLEWARE (new), TYPE_DEF (new), CACHE_KEY, ENV_VAR (new)

### Tags (NOT types): 4 concepts kept as tags
- TEST → Function with tags: ["test"]
- SCRIPT → Function with tags: ["script", "entry-point"]
- UTILITY → Function with tags: ["utility"]
- MIGRATION → Function with tags: ["migration"]

Decision rule: A concept becomes a TYPE only if it has unique edge semantics.

### Edge Types: 27 (was 15, +12)

**Structural (5):** CONTAINS, DEFINES, IMPORTS, IMPLEMENTS*, INHERITS*
**Data Flow (3):** READS_DB, WRITES_DB, MIGRATES*
**Communication (5):** CALLS_API, CALLS_SERVICE, CALLS*, WEBHOOK_SEND, WEBHOOK_RECEIVE
**Event/Async (5):** EMITS_EVENT, CONSUMES_EVENT, ENQUEUES*, DEQUEUES*, SCHEDULES*
**Caching (2):** CACHE_READ, CACHE_WRITE
**Routing (4):** ROUTES_TO*, INTERCEPTS*, VALIDATES*, AUTHENTICATES*
**Config/Deploy (3):** DEPENDS_ON, DEPLOYED_BY, CONFIGURES*
**Quality (1):** TESTS*

(* = new)

### Model Improvements

**GraphNode +7 fields:**
- provenance (scanner/llm/human/import/runtime)
- source_hash (SHA-256 for change detection)
- language (multi-language support)
- tags (flexible classification)
- parent_id (fast containment lookup)
- version (monotonic change counter)
- tier (computed from type)

**GraphEdge +5 fields:**
- provenance, is_stale, weight, conditional, location

**ContextPack +8 fields:**
- relevant_edges, related_files, code_snippets, invariants
- patterns, stale_warnings, depth, total_nodes_in_scope

---

## 4. Pipeline Architecture

### The 80/20 Rule: 80% of nodes cost $0

| Pass | What | Cost | Time | Confidence |
|------|------|------|------|------------|
| 0: Scanner | Projects, Infra | $0 | <1s | 0.95-1.0 |
| 1: Tree-sitter | Files, Functions, Imports | $0 | 2-5s | 0.85-0.95 |
| 2: Pattern Match | Endpoints, Models, Events | $0 | 1-3s | 0.70-0.90 |
| 3: LLM Semantic | Operational edges, stories | ~$1.85 | 5-30min | 0.60-0.85 |
| 4: Validation | Cross-reference check | ~$0.50 | 1-5min | boost/penalty |

**Total for 500 files: ~$2.35 (or ~$0.68 with caching + batch API)**

### LLM Model Routing

| Task | Model | Why |
|------|-------|-----|
| File classification | Haiku 3.5 | Simple, cheap |
| Service classification | Haiku 3.5 | Pattern recognition |
| Data flow edges | Sonnet 4.5 | Multi-file reasoning |
| Cross-service comms | Sonnet 4.5 | Complex tracing |
| Conflict resolution | Opus 4.6 | Hard cases only |

### Output Format
Anthropic tool_use with schema enforcement. No JSON parsing needed.

---

## 5. Self-Healing Design

### Change Detection Flow
```
Git commit/hook → git diff --name-status
→ identify changed files
→ find all nodes sourced from those files
→ mark nodes STALE
→ cascade staleness 2 hops:
  - Event stale → consumers stale
  - DataModel stale → readers/writers stale
  - Endpoint stale → callers stale
→ re-run Passes 1-3 on stale files only
→ re-run Pass 4 on affected subgraph
```

### Incremental Cost
| Change | Files Re-analyzed | Cost | Time |
|--------|------------------|------|------|
| 1 file | 1-5 | ~$0.01 | <10s |
| 10 files | 15-30 | ~$0.10 | <30s |
| 50 files | 80-120 | ~$0.40 | 1-2min |

### Techniques Combined
1. Merkle tree (Cursor) for file-level change detection
2. Content-hash (Cursor/Aider) for chunk-level caching
3. Independent analysis (Stack Graphs) for parallelism
4. source_hash on GraphNode for fingerprint validation

---

## 6. Module Structure

```
workspace-intelligence/
    ontology.py              # [EXISTS] → upgrade to v2 (20 nodes, 27 edges)
    scanner.py               # [EXISTS] → enhance docker-compose/CI parsing
    graph_store.py           # [EXISTS] → add batch ops, path finding, subgraph

    pipeline/
        orchestrator.py      # Main coordinator with JSON checkpoint resume
        pass1_treesitter.py  # Tree-sitter AST extraction (FREE)
        pass2_patterns.py    # 15+ framework regex patterns (FREE)
        pass3_llm.py         # Async LLM analysis (Haiku/Sonnet routing)
        pass4_validation.py  # Cross-reference + hallucination detection
        chunker.py           # AST-guided file splitting for large files

    llm/
        client.py            # Anthropic API + prompt caching + retries + cost tracking
        prompts.py           # System prompts + tool_use schema definitions
        model_router.py      # Haiku/Sonnet/Opus selection logic
        batch.py             # Batch API for initial full-index (50% cheaper)

    incremental/
        change_detector.py   # Git diff parsing
        staleness.py         # 2-hop cascade propagation
        selective_reindex.py # Targeted re-analysis of stale subgraphs

    merge/
        result_merger.py     # Parallel result reconciliation
        conflict_resolver.py # Confidence + specificity based resolution

    api/
        mcp_server.py        # MCP tools for AI agent consumption
        fastapi_app.py       # HTTP API for visualization/dashboard

    config.py                # API keys, model choices, thresholds
    state.py                 # Pipeline state for crash recovery
    cli.py                   # CLI entry point
```

---

## 7. BMAD Development Plan

### Methodology: BMAD Method Track (Level 2-3)
- **Complexity**: ~25 stories across 5 epics
- **Track**: BMad Method (Phase 2 PRD → Phase 3 Architecture → Phase 4 Sprints)
- **Phase 1 (Analysis)**: DONE (3-agent deep research)
- **Phase 2 (Planning)**: DONE (this document = PRD equivalent)
- **Phase 3 (Solutioning)**: DONE (ontology_v2.py = architecture, ONTOLOGY_DESIGN.md = ADR)
- **Phase 4 (Implementation)**: NEXT → Sprints below

---

### Epic 1: Foundation (Sprint 1-2) — FREE, no API cost
*Goal: Make the graph populate with structural data from any codebase*

| Story | Task | Acceptance Criteria | Est |
|-------|------|-------------------|-----|
| 1.1 | Upgrade ontology.py to v2 | 20 NodeTypes, 27 EdgeTypes, GraphNode has provenance/source_hash/tags/language/parent_id/version/tier, GraphEdge has provenance/is_stale/weight/conditional/location, ContextPack has 13 fields. All Pydantic models validate. | M |
| 1.2 | Fix scanner.py bugs | .csproj/sln glob matching works, monorepo detection (nx.json, turbo.json, lerna.json, pnpm-workspace.yaml), CI/CD detection (.github/workflows, .gitlab-ci.yml), API schema detection (openapi.yaml, schema.graphql, *.proto) | S |
| 1.3 | Bridge scanner → graph | ScanResult → GraphNode conversion. Running scanner on a workspace auto-populates Workspace, Project, InfraConfig, Resource nodes in GraphStore. docker-compose.yml parsing creates Resource nodes. | M |
| 1.4 | Build pass1_treesitter.py | Tree-sitter parses JS/TS/Python/Go files. Extracts File, Function, Class, Import nodes. Creates CONTAINS, DEFINES, IMPORTS edges. Content-hash caching per file. 500 files in <5s. | L |
| 1.5 | Build pass2_patterns.py | 15+ regex rules for Express/FastAPI/Django/NestJS endpoints, SQLAlchemy/Mongoose/Prisma models, event emit/consume, Redis cache ops, HTTP client calls. Reclassifies Pass 1 Function nodes to Endpoint/DataModel/Event/CacheKey. | L |
| 1.6 | Build pipeline orchestrator | Runs Pass 0→1→2 sequentially. JSON checkpoint file for resume on crash. Stats output (nodes/edges created per pass, time per pass). CLI: `python cli.py index <workspace_path>` | M |

**Sprint 1 Definition of Done**: Run `python cli.py index ./my-project` and get a populated graph with 300+ nodes and structural edges, saved to JSON.

---

### Epic 2: Intelligence (Sprint 3-4) — ~$0.68-2.35/500 files
*Goal: Add operational edges — the "story" that tells AI what code DOES*

| Story | Task | Acceptance Criteria | Est |
|-------|------|-------------------|-----|
| 2.1 | Build llm/client.py | Anthropic API wrapper. Prompt caching (cache_control: ephemeral). Exponential backoff + jitter on 429/529. Cost tracking (input/output/cache tokens). Configurable model per call. | M |
| 2.2 | Build llm/prompts.py | tool_use schema for code analysis (nodes_discovered, edges_discovered, file_classification). System prompt with ontology context + known-nodes summary. Anti-hallucination rules. | M |
| 2.3 | Build llm/model_router.py | Route by task: Haiku for file classification, Sonnet for semantic edges, Opus for conflicts. Configurable thresholds. | S |
| 2.4 | Build chunker.py | AST-guided file splitting at function/class boundaries. Prepend imports as context. Handle files >8K tokens. Chunk merging for small sibling functions. | M |
| 2.5 | Build pass3_llm.py | Async analysis with asyncio.Semaphore(10). Haiku classifies all files, Sonnet discovers operational edges (READS_DB, CALLS_API, EMITS_EVENT, etc.). Skips test/generated/config files. Results merged into graph. | XL |
| 2.6 | Build llm/batch.py | Anthropic Batch API for initial full-index (50% cheaper). Submit up to 10K requests. Poll for completion. Parse batch results into graph. | M |

**Sprint 3-4 DoD**: Run full index on a real project. Graph has operational edges (READS_DB, CALLS_API, EMITS_EVENT). Cost <$2.50 for 500 files.

---

### Epic 3: Self-Healing (Sprint 5) — ~$0.01/change
*Goal: Graph auto-updates when code changes*

| Story | Task | Acceptance Criteria | Est |
|-------|------|-------------------|-----|
| 3.1 | Build change_detector.py | Parse `git diff --name-status HEAD~1`. Return added/modified/deleted file lists. Map files to existing graph nodes. | S |
| 3.2 | Build staleness.py | Mark node stale → cascade 2 hops (Event stale → consumers stale, DataModel stale → readers/writers stale, Endpoint stale → callers stale). Stop at 2 hops. | M |
| 3.3 | Build selective_reindex.py | Re-run Passes 1-3 on stale files only. Merge results into existing graph (update, don't replace). Delete nodes from deleted files. | L |
| 3.4 | Git hook integration | post-commit hook script that triggers selective re-index. Debounce for rapid commits. CLI: `python cli.py update` | S |

**Sprint 5 DoD**: Change 1 file, run `python cli.py update`, graph updates in <10s, cost <$0.01.

---

### Epic 4: Consumption (Sprint 6-7) — AI agents use the graph
*Goal: Expose the intelligence via MCP and enhanced ContextPack*

| Story | Task | Acceptance Criteria | Est |
|-------|------|-------------------|-----|
| 4.1 | Enhance ContextPack | Add relevant_edges, related_files, code_snippets, invariants, patterns, stale_warnings, depth, total_nodes_in_scope. Token budget parameter. | M |
| 4.2 | Enhance GraphStore | Batch add, subgraph extraction by tag/type, shortest path finding, graph diff (compare two snapshots), filter by confidence. | L |
| 4.3 | Build MCP server | Tools: SearchEntity, TraverseGraph, GetContext, ImpactAnalysis, GetStory. Compatible with Claude Code, Cursor, Aider. | L |
| 4.4 | Token budget system | 3 levels: L1 (names only, ~200 tokens), L2 (names + descriptions, ~1K tokens), L3 (full detail + snippets, ~4K tokens). Auto-select based on budget param. | M |
| 4.5 | Build CLI commands | `index`, `query <node>`, `impact <node>`, `status`, `update`, `stats`, `export`. | M |

**Sprint 6-7 DoD**: Claude Code uses MCP to query the graph. `GetContext("OrderService", "refactor DB schema")` returns complete ContextPack with edges, files, risk assessment.

---

### Epic 5: Quality & Scale (Sprint 8+) — Production polish
*Goal: Validated data, visual UI, semantic search*

| Story | Task | Acceptance Criteria | Est |
|-------|------|-------------------|-----|
| 5.1 | Build pass4_validation.py | Orphan detection, dangling edges, type constraint validation, cross-reference AST vs LLM claims, bidirectional edge verification. Confidence boost/penalty. | L |
| 5.2 | Build conflict_resolver.py | Higher confidence wins (>0.1 diff), more specific type wins, tie → flag for human review. Merge parallel agent results. | M |
| 5.3 | Add LanceDB vector search | Embed node descriptions with MiniLM-L6-v2. Semantic search: "find payment code" → ranked nodes. | M |
| 5.4 | Build graph viewer (web UI) | D3.js/Cytoscape visualization like original Thorbit screenshot. Filter by node/edge type. Click node → side panel with details. | L |
| 5.5 | PageRank importance ranking | Aider-style centrality scoring. High fan-in nodes = architecturally important. Weight context pack results by importance. | M |

**Sprint 8+ DoD**: Graph viewer shows visual like the original screenshot. Semantic search works. Validation catches hallucinated edges.

---

### Size Estimates
- **S** = Small (1-2 hours)
- **M** = Medium (3-5 hours)
- **L** = Large (6-10 hours)
- **XL** = Extra Large (10+ hours)

### Total: ~25 stories across 5 epics, 8+ sprints

---

## 8. What NOT to Build (Revised 2026-02-11)

- LangGraph (direct API calls are simpler for this use case)
- ~~Runtime tracing~~ → **BUILT** (2026-02-10) as overlay layer, not APM — lightweight probe + SSE
- Test file LLM analysis (skip in Pass 3, waste of tokens)
- Full Neo4j for MVP (NetworkX is sufficient under 10K nodes)
- Custom embedding model (use off-the-shelf MiniLM or Voyage)
- Full graph rendering at scale (use focal point navigation instead — ADR-007)

## 9. Architecture Decision Records (ADRs)

### ADR-001: Tags vs Types
- **Decision**: TEST, SCRIPT, UTILITY, MIGRATION = tags; ROUTER, COLLECTION, MIDDLEWARE, QUEUE, TYPEDEF, ENVVAR = types
- **Rationale**: A concept becomes a TYPE only if it has unique edge semantics
- **Status**: Decided

### ADR-002: Graph Storage
- **Decision**: NetworkX for MVP, Neo4j for production (>10K nodes)
- **Rationale**: NetworkX is fast, in-memory, zero-config. Neo4j only needed at scale.
- **Status**: Decided

### ADR-003: LLM Provider
- **Decision**: Anthropic API (Claude) with tool_use for structured output
- **Rationale**: Best structured outputs, prompt caching (10x savings), batch API (50% savings)
- **Status**: Decided

### ADR-004: Multi-Pass Pipeline
- **Decision**: 5-pass (scanner → tree-sitter → regex → LLM → validation)
- **Rationale**: 80% of nodes cost $0. LLM reserved for semantic-only tasks.
- **Status**: Decided

### ADR-005: Self-Healing Strategy
- **Decision**: Git diff → stale cascade (2-hop) → selective re-index
- **Rationale**: Cheapest incremental update. Full re-index only weekly.
- **Status**: Decided

### ADR-006: Development Methodology
- **Decision**: BMAD Method Track (Level 2-3) with PRP-style context packing for story execution
- **Rationale**: Complex system (~25 stories) needs structured phased approach. BMAD provides epics/stories/sprints. PRP context packing ensures one-pass execution quality.
- **Status**: Decided

---

## Sources

### Code Intelligence Systems
- [SCIP Protocol](https://github.com/sourcegraph/scip)
- [Aider Repo Map](https://aider.chat/2023/10/22/repomap.html)
- [Continue.dev Codebase Retrieval](https://docs.continue.dev/features/codebase-embeddings)
- [Cursor Indexing Architecture](https://read.engineerscodex.com/p/how-cursor-indexes-codebases-fast)
- [CodeQL Data Flow Analysis](https://codeql.github.com/docs/writing-codeql-queries/about-data-flow-analysis/)
- [GitHub Stack Graphs](https://github.blog/open-source/introducing-stack-graphs/)
- [Blar Code Graph](https://blar.io/blog/how-we-built-a-tool-to-turn-any-code-base-into-a-graph-of-its-relationships)
- [CodePrism Architecture](https://rustic-ai.github.io/codeprism/blog/graph-based-code-analysis-engine/)
- [LocAgent (Yale, ACL 2025)](https://arxiv.org/abs/2503.09089)
- [Augment Code MCP](https://www.augmentcode.com/)

### Ontology & Knowledge Graphs
- [Joern Code Property Graph](https://cpg.joern.io/)
- [Software Archaeology Ontology](https://bennycheung.github.io/ontology-the-queryable-brain-of-software-archaeology)
- [GraphGen4Code (IBM WALA)](https://wala.github.io/graph4code/)
- [Neo4j Codebase Knowledge Graph](https://neo4j.com/blog/developer/codebase-knowledge-graph/)

### Pipeline & Cost
- [Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [Anthropic Batch API](https://docs.anthropic.com/en/docs/build-with-claude/batch-processing)
- [code-graph-rag](https://github.com/vitali87/code-graph-rag)
