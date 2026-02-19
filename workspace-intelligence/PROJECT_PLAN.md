# Workspace Intelligence - Project Plan

> Single source of truth for project status, architecture, and roadmap.
> Last updated: 2026-02-19

## What This Is

A semantic graph/metadata layer that gives AI agents deep understanding of codebases.
Not just "what files exist" but "what does this code DO, what does it connect to, and what breaks if I change it."

**Stack**: Python, NetworkX, Pydantic, Tree-sitter, Anthropic API
**Location**: `workspace-intelligence/`
**Test target**: `test-shop/` (Express.js e-commerce app)

---

## Current State: What's Built

### Core (Working)
- `ontology.py` — Full v2 schema: 20 node types, 27 edge types, 3 tiers
- `scanner.py` — Detects projects, infra files, marker files
- `graph_store.py` — NetworkX-backed storage with traversal, validation
- `bridge.py` — Converts scanner output → graph nodes (Pass 0)

### Pipeline Passes (All 5 Passes Wired — Phases A+D Done)
- `orchestrator.py` — Chains Pass 0→0b→1→2→2b→3→4, 107 nodes/269 edges in 82ms (free passes)
- `pass1_treesitter.py` — Extracts File, Function, Import nodes (ES6 + CommonJS)
- `pass2_patterns.py` — Regex patterns for endpoints, models, events
- `pass2b_connections.py` — Behavioral edges: CALLS, EMITS_EVENT, etc.
- `pass3_llm.py` — ✓ LLM semantic analysis (Anthropic API, tool_use, prompt caching)
- `pass4_validation.py` — ✓ Validation & confidence scoring (orphan detection, edge constraints)

### Viewer (Fully Featured — Phases B+F+G Done)
- `viewer/server.py` — HTTP server with SSE, live updates, scan API, subgraph API, expansion scan
- `viewer/index.html` — ~5100 line monolith with full 2D/3D visualization:
  - **Layouts**: Force, Tree, Radial, Bloom (Fibonacci explosion), Force Graph 3D (vasturiano)
  - **View layers**: All, Structure, Dependency, Runtime (filter edge groups)
  - **2D**: D3.js SVG with edge bundling (bezier curves), arrow markers, focal navigation
  - **3D**: Three.js with bloom post-processing, directional arrows, breathing animation
  - **Interaction**: Drag (children follow), click fly-to, neighbor highlight, dblclick collapse
  - **Glyphs**: Link count badges on nodes (color-coded by connectivity)
  - **Combo nodes**: Dblclick collapse/expand subtrees with visual indicators
  - **Runtime**: SSE live events, animated particles on edges, activity log

### Runtime Layer (Working, Built 2026-02-10)
- `test-shop/src/services/wi-probe.js` — Express middleware + event listener
- Viewer SSE integration — Live node highlighting, flow paths, activity log
- Runtime events flow: test-shop → probe → WI server → SSE → viewer

### Incremental Updates (Phase C Done, Built 2026-02-11)
- `incremental/watcher.py` — GraphWatcher with watchdog, debouncing, SSE broadcast
- `incremental/change_detector.py` — Git diff parsing, change → graph node mapping
- `incremental/selective_reindex.py` — Re-run passes on changed files only, merge into graph
- `incremental/staleness.py` — 2-hop cascade propagation for stale markers
- Viewer auto-navigate to changed nodes on live updates

### AI Agent Consumption (Phase E Done, Built 2026-02-11)
- `api/mcp_server.py` — MCP server (JSON-RPC 2.0 over stdio) with 5 tools
- `graph_store.py` — Enhanced ContextPack with token budgets (L1: 200, L2: 1K, L3: 4K tokens)
- `cli.py` — Full CLI with 7 commands: index, update, watch, status, query, impact, stats, export

### What the Graph Actually Contains (test-shop scan)
| What | Count | Source |
|------|-------|--------|
| Workspace, Project | 2 | Pass 0 (scanner) |
| InfraConfig | 1 | Pass 0 |
| File nodes | ~15 | Pass 1 (tree-sitter) |
| Function nodes | ~40 | Pass 1 (tree-sitter) |
| Event nodes | ~12 | Pass 2 (patterns) |
| CONTAINS edges | few | Pass 0 |
| DEFINES edges | ~40 | Pass 1 |
| CALLS edges | ~15 | Pass 1 |
| EMITS edges | ~10 | Pass 2 |

### What Was MISSING → Now Built
| What | Why It Matters | Status |
|------|---------------|--------|
| **Directory/MODULE nodes** | Folder hierarchy (src/, routes/, services/) | ✓ Built — 8 MODULE nodes |
| **IMPORTS edges** | File-to-file dependencies (app.js → admin.js) | ✓ Built — 82 IMPORTS edges |
| **ROUTES_TO edges** | HTTP routing (app.js routes /api/admin → admin.js) | Deferred to Phase D LLM |
| **Operational edges** | READS_DB, CALLS_API, CACHE_READ | ✓ Framework Built — Pass 3 LLM ready |

---

## Architecture Insights (Learned from Usage)

### Problem: The Graph is Confusing

When viewing the test-shop graph, everything looks like a flat soup of dots.
The folder structure (`src/` → `routes/` → `products.js`) is invisible.
Import connections (`app.js` requires `admin.js`) are invisible.
Users can't tell what connects to what.

**Root cause**: The pipeline passes aren't wired. Only Pass 0 + partial Pass 1-2 run.

### Problem: Scale (1000+ Nodes Will Hang)

D3 force-directed graph with 1000+ nodes = browser freeze.
A real project has thousands of files, functions, events.

**Solution needed**: Don't show everything. Show a focused subgraph.

### Three Scan Modes Needed

| Mode | When | What It Does |
|------|------|-------------|
| **Snapshot** | First scan, CI/CD | Full scan of entire codebase. Creates complete graph. Slow but thorough. |
| **Incremental** | Ongoing development | Watch for file changes (git diff / file watcher). Re-scan only changed files + 2-hop cascade. Fast, cheap. |
| **Expansion** | Exploring/debugging | Start from one file/folder. Scan it. Then expand outward to connected files on demand. Interactive. |

### Three Viewer Modes Needed

| Mode | What It Shows | When |
|------|-------------|------|
| **Structure** | Folder hierarchy (tree view) + containment. Like a file explorer but showing what's inside each file. | Understanding codebase layout |
| **Dependency** | Import/call graph. Which files depend on which. IMPORTS, CALLS, ROUTES_TO edges. | Understanding connections, impact analysis |
| **Runtime** | Live activity overlay. Which code is executing right now. Events, HTTP requests. | Debugging, monitoring |

### The "Fix Mode" Concept

When a developer is fixing a bug:
1. They start with **one file** (the file they're changing)
2. They need to see **what it connects to** (imports, calls, events)
3. They need to see **what connects to it** (who imports it, who calls it)
4. They DON'T need the entire codebase graph

This is a **focal point navigation** pattern:
- Start with a node
- Expand 1 hop (direct connections)
- Expand 2 hops (indirect connections)
- Stop there — don't load the whole graph

This solves both the **scale problem** (never load 1000+ nodes) and the **usability problem** (focused context, not information overload).

---

## Updated Roadmap

### Phase A: Wire the Pipeline ✓ DONE (2026-02-11)
*Goal: Make the graph actually useful by connecting existing code*

| # | Task | Status |
|---|------|--------|
| A1 | Wire orchestrator: Pass 0 → 0b → 1 → 2 → 2b | ✓ Done — 107 nodes, 269 edges in 82ms |
| A2 | Add MODULE nodes for directories | ✓ Done — 8 MODULE nodes (src/, routes/, models/, etc.) |
| A3 | Add IMPORTS edge detection (CommonJS + ES6) | ✓ Done — 82 IMPORTS edges (33 resolved to files) |
| A4 | Add ROUTES_TO pattern in Pass 2 | Deferred to Phase D (needs LLM) |
| A5 | CLI: `python cli.py index <path>` runs full pipeline | Was already working via orchestrator.py CLI |

### Phase B: Viewer Layers ✓ DONE (2026-02-11)
*Goal: Make the viewer understandable at any scale*

| # | Task | Status |
|---|------|--------|
| B1 | Tree/hierarchy view mode | ✓ Done — Collapsible folder tree from CONTAINS edges |
| B2 | Focal point navigation | ✓ Done — Click node → Focus 1/2/3 hops, +/- depth controls |
| B3 | Layer toggle (structure / dependency / runtime) | ✓ Done — 4 buttons in toolbar: All / Structure / Dependency / Runtime |
| B4 | Node limit + warning | ✓ Done — Warning banner when >150 nodes, suggests focus mode |
| B5 | Subgraph extraction API | ✓ Done — `/api/subgraph?node_id=X&depth=N&graph_path=Y` |

**Result**: Viewer now has folder tree, focal point navigation (app.js 2-hop = 63 of 107 nodes), layer presets, and scale warnings.

### Phase C: Incremental & Change-Driven ✓ DONE (2026-02-11)
*Goal: Graph stays fresh without re-scanning everything*

| # | Task | Status |
|---|------|--------|
| C1 | File watcher (watchdog / git hook) | ✓ Done — GraphWatcher with debouncing, SSE broadcast |
| C2 | Change-driven scan | ✓ Done — selective_reindex.py, 2-hop stale cascade |
| C3 | Change-driven viewer | ✓ Done — Auto-navigate to changed node, highlight changes |
| C4 | Expansion mode scan | ✓ Done — `/api/expand` endpoint merges subfolder scan into graph |

**Result**: File changes update graph in <5s. Viewer auto-focuses on changed nodes. Expansion mode scans folders on demand.

### Phase D: Intelligence (LLM-Powered) ✓ DONE (2026-02-11)
*Goal: Add the "story" — what code DOES, not just what it IS*

| # | Task | Status |
|---|------|--------|
| D1 | LLM client + prompts (Anthropic API) | ✓ Done — llm/client.py, prompts.py, model_router.py |
| D2 | Pass 3: LLM semantic edges | ✓ Done — Wired into orchestrator, async file processing |
| D3 | Node descriptions / stories | ✓ Done — Tool schema for discover_nodes with descriptions |
| D4 | Pass 4: Validation | ✓ Done — Orphan detection, edge constraints, confidence scoring |

**Result**: Full LLM pipeline ready. Pass 3 classifies files, discovers edges/nodes. Pass 4 validates and adjusts confidence. Cost: ~$2/500 files.

### Phase E: Consumption (AI Agents Use the Graph) ✓ DONE (2026-02-11)
*Goal: Other AI tools can query the intelligence*

| # | Task | Status |
|---|------|--------|
| E1 | MCP server (SearchEntity, GetContext, ImpactAnalysis) | ✓ Done — 5 tools via JSON-RPC 2.0 stdio |
| E2 | Enhanced ContextPack with token budgets | ✓ Done — L1: 200 tokens, L2: 1K, L3: 4K |
| E3 | CLI commands (query, impact, status, export) | ✓ Done — 7 commands fully implemented |

**Result**: MCP server ready for AI agent consumption. Token-budgeted context packs. Full CLI for all operations.

---

### Phase F: Viewer 3D & Visual Polish ✓ DONE (2026-02-15 to 2026-02-19)
*Goal: Make the viewer visually rich and interactive in both 2D and 3D*

| # | Task | Status |
|---|------|--------|
| F1 | 3D viewer with Three.js (scene, camera, orbit controls) | ✓ Done — `9fe2398` |
| F2 | 4 layout modes (Force, Tree, Radial, Bloom) in both 2D/3D | ✓ Done — `f64ecf1` to `5486a32` |
| F3 | Bloom layout (Fibonacci explosion + subtree-weighted orbits) | ✓ Done — `e76b6b9` |
| F4 | Drag-with-children in fixed layouts | ✓ Done — `81c6481` |
| F5 | Force Graph 3D (vasturiano library) as tester mode | ✓ Done — `6842a58` |
| F6 | UI overhaul (drawer, dropdowns, collapsible sidebar) | ✓ Done — `b2c68dd`, `d72cd7c` |

**Result**: Full 2D↔3D toggle. 5 layout modes. Professional UI with sidebar filters, detail drawer, and toolbar dropdowns.

### Phase G: Advanced Visual Techniques ✓ DONE (2026-02-19)
*Goal: Apply research-backed visualization techniques from Cambridge Intelligence + iCAVE*

| # | Task | Status |
|---|------|--------|
| G1 | Camera fly-to on node click (3D) | ✓ Done — `1db2ef2` |
| G2 | Neighbor highlight on hover (3D) | ✓ Done — `1db2ef2` |
| G3 | Directional arrows on all 3D edges | ✓ Done — `1db2ef2` |
| G4 | Animated runtime particles (3D) | ✓ Done — `1db2ef2` |
| G5 | Right-click unpin + node dragging (3D) | ✓ Done — `1db2ef2` |
| G6 | Combo/group nodes (dblclick collapse/expand) | ✓ Done — `e76b6b9` |
| G7 | Edge bundling (bezier curves for non-structural) | ✓ Done — `e76b6b9` |
| G8 | Bloom glow post-processing (UnrealBloomPass) | ✓ Done — `334c831` |
| G9 | Node glyphs (link count badges, color-coded) | ✓ Done — `334c831` |
| G10 | Progressive disclosure | ✓ Partial — combo nodes + focal point = basic form |

**Result**: 3D mode now has fly-to camera, neighbor glow, arrows, particles, bloom, and glyphs. 2D has edge bundling, combo nodes, and glyphs. Research techniques from Cambridge Intelligence and iCAVE applied.

---

## Upcoming Phases

### Phase H: Universal Graph Visualization (NEXT)
*Goal: Turn WI into a general-purpose graph visualizer — any data with vectors, categories, or timestamps gets new modes automatically*
*Source: Research from Cambridge Intelligence, iCAVE, arxiv 2412.05289, CorGIE*

**Key insight**: Modes should be **data-property driven**, not source-specific. Any node with vectors gets Semantic mode. Any node with categories gets Cluster mode. Any graph with timestamps gets Temporal mode.

#### H0: Universal Data Adapter (prerequisite — build first)

| # | Task | What It Enables |
|---|------|----------------|
| H0.1 | **Generic graph format spec** — define universal `{nodes, links}` with optional properties: `vectors[]`, `categories[]`, `timestamp`, `parent`, `metadata{}` | All modes work on any data |
| H0.2 | **Data source plugins** — pluggable adapters: WI code graph, Oracle v2, CSV import, JSON import | Not locked to one source |
| H0.3 | **`/api/graph-sources` endpoint** — list available sources, load/switch between them | Server-side routing |
| H0.4 | **Viewer data source switcher** — dropdown: "test-shop (code)" / "Oracle v2 (knowledge)" / "Import..." | User picks data in UI |
| H0.5 | **Vector embedding pipeline** (optional) — embed code nodes via LLM so code gets Semantic mode too | Code + Knowledge unified |

#### H1-H7: Property-Driven Modes

| # | Mode | Works when node has... | Works for | Priority |
|---|------|----------------------|-----------|----------|
| H1 | **Semantic** — UMAP/t-SNE projection, nearby=similar | `vectors[]` | ANY data with embeddings | HIGH |
| H2 | **Concept cluster** — transparent bubbles by tag, click to expand | `categories[]` or `tags[]` | ANY categorized data | HIGH |
| H3 | **Temporal** — X=time, Y=concept, evolution over time | `timestamp` | ANY timestamped data | MEDIUM |
| H4 | **Lineage** — directed tree of parent→child / extends / supersedes | directed `parent`→`child` links | ANY DAG (code hierarchy, Oracle supersede, git history) | MEDIUM |
| H5 | **Similarity heatmap** — matrix view, color=similarity score | `vectors[]` | ANY embeddings | LOW |
| H6 | **Dual-view** — topology (links) vs semantics (vectors) side by side | `vectors[]` + explicit links | ANY hybrid data | LOW |
| H7 | **Peculiarity** — highlight unexpected embedding neighbors | `vectors[]` | ANY embeddings | LOW |

#### H8: Mode Overlay System

| # | Feature | What It Does |
|---|---------|-------------|
| H8.1 | **Position = one mode** | e.g. Semantic (UMAP) sets X/Y/Z |
| H8.2 | **Edges = another mode** | e.g. show Dependency edges on Semantic positions |
| H8.3 | **Color = another dimension** | e.g. color by staleness on Temporal layout, or by category on Semantic layout |

This means: "show me code functions positioned by semantic similarity, colored by folder, with dependency arrows" — all composable.

#### Server Endpoints Needed

| Endpoint | What | Used By |
|----------|------|---------|
| `/api/graph-sources` | List available graph sources | H0.4 viewer switcher |
| `/api/graph?source=X` | Load graph from any source (unified format) | H0.2 plugins |
| `/api/umap?source=X` | Project vectors → 2D/3D coords via UMAP | H1 Semantic mode |
| `/api/similarity?source=X` | Pairwise similarity matrix | H5 Heatmap, H7 Peculiarity |
| `/api/clusters?source=X&by=categories` | Compute cluster assignments | H2 Concept cluster |

#### Framework — What drives node position?

| Mode | Position Driver | Requires | Works For |
|------|----------------|----------|-----------|
| Structure | Containment hierarchy | `CONTAINS` edges | Code |
| Dependency | Import/call topology | `IMPORTS`/`CALLS` edges | Code |
| Runtime | Live events (overlay) | SSE events | Code |
| **Semantic** | **Vector similarity** | **`vectors[]`** | **Any** |
| **Concept** | **Category tags** | **`categories[]`** | **Any** |
| **Temporal** | **Creation time** | **`timestamp`** | **Any** |
| **Lineage** | **Parent→child chains** | **directed links** | **Any** |

### Phase I: Code Quality & Maintainability
*Goal: Keep the codebase healthy as it grows*

| # | Task | Priority |
|---|------|----------|
| I1 | Split `index.html` monolith (~5100 lines) into JS/CSS modules | HIGH |
| I2 | Add unit tests for layout algorithms | MEDIUM |
| I3 | Performance profiling (benchmark with 500+ node graphs) | MEDIUM |
| I4 | Give "Tester" layout (Force Graph 3D) a real purpose or remove | LOW |

### Phase J: Platform Extensions (Future)
*Goal: Bring WI to other tools and workflows*

| # | Task | Priority |
|---|------|----------|
| J1 | Neo4j backend (when NetworkX becomes too slow, 100K+ nodes) | WHEN NEEDED |
| J2 | More languages (Ruby, PHP, Swift, Kotlin) — 1 day/language | ON DEMAND |
| J3 | Database schema detection (Prisma, Sequelize, Django ORM) | MEDIUM |
| J4 | External API dependency graph ("If Stripe goes down, what breaks?") | MEDIUM |
| J5 | CI/CD integration (GitHub Action: auto-update graph on PR) | LOW |
| J6 | VSCode extension (sidebar graph view of current file) | LOW |
| J7 | Annotation mode (manual notes/warnings on nodes) | LOW |

---

## Key ADRs (Architecture Decisions)

| ADR | Decision | Rationale |
|-----|----------|-----------|
| ADR-001 | Tags vs Types | TEST/SCRIPT/UTILITY = tags (no unique edge semantics) |
| ADR-002 | NetworkX MVP → Neo4j at scale | Zero-config for MVP, scale later |
| ADR-003 | Anthropic API + tool_use | Best structured output, prompt caching |
| ADR-004 | 5-pass pipeline (80% free) | LLM only for semantic tasks |
| ADR-005 | Git diff → 2-hop stale cascade | Cheapest incremental update |
| ADR-006 | BMAD + PRP hybrid methodology | Structured phases for complex system |
| ADR-007 | Focal point navigation over full graph | Scale solution — never render 1000+ nodes |
| ADR-008 | Three scan modes (snapshot/incremental/expansion) | Different needs at different times |
| ADR-009 | Runtime layer is overlay, not persistent | Runtime events don't modify the structural graph |
| ADR-010 | In-layout orbit scaling, NOT post-processing | Post-processing push causes cascading spikes — recursive positionNode() moves entire subtrees naturally |
| ADR-011 | 2D↔3D toggle over 3D-only replacement | Keep 2D strengths (SVG text, edge bundling, markers) + add 3D for spatial exploration |
| ADR-012 | 3d-force-graph as tester mode, not replacement | Library is good for native 3D physics but our hand-rolled 3D has better layout integration + runtime overlay |
| ADR-013 | "What drives layout?" framework for new modes | Each mode maps one data dimension to node position — prevents mode confusion |
| ADR-014 | Property-driven modes, not source-specific | Modes activate based on node properties (vectors, categories, timestamps) not data source — makes WI a general-purpose graph visualizer |
| ADR-015 | Mode overlay system (position + edges + color composable) | Don't lock position/edge/color to same mode — let users mix dimensions for richer exploration |

---

## File Structure

```
workspace-intelligence/
  PROJECT_PLAN.md          # THIS FILE - main plan
  README.md                # Original architecture blueprint
  RESEARCH_SYNTHESIS.md    # Detailed research + BMAD epics
  ONTOLOGY_DESIGN.md       # Ontology design rationale

  ontology.py              # v2 schema (20 nodes, 27 edges)
  scanner.py               # Project/infra detection
  graph_store.py           # NetworkX graph storage
  bridge.py                # Scanner → Graph conversion

  pipeline/
    orchestrator.py        # Pipeline coordinator (all 5 passes wired)
    pass1_treesitter.py    # AST extraction (FREE)
    pass2_patterns.py      # Regex patterns (FREE)
    pass2b_connections.py  # Behavioral edges (FREE)
    pass3_llm.py           # LLM semantic analysis (~$2/500 files)
    pass4_validation.py    # Validation & confidence scoring (FREE)
    chunker.py             # File splitting for large files

  incremental/             # Phase C: Change-driven updates
    watcher.py             # GraphWatcher with watchdog + SSE
    change_detector.py     # Git diff parsing, change mapping
    selective_reindex.py   # Re-scan changed files only
    staleness.py           # 2-hop stale cascade

  llm/                     # Phase D: LLM infrastructure
    client.py              # Anthropic API client with retry
    prompts.py             # Tool schemas for edge/node discovery
    model_router.py        # Route tasks to haiku/sonnet/opus

  viewer/
    server.py              # HTTP + SSE server + expansion scan API
    index.html             # ~5100 line monolith: D3 2D + Three.js 3D + vasturiano 3D

  api/                     # Phase E: AI agent consumption
    mcp_server.py          # MCP server (5 tools, JSON-RPC 2.0)

  cli.py                   # Full CLI (7 commands)
  graphs/                  # Generated graph JSON files
```

---

## References

- RESEARCH_SYNTHESIS.md — Full competitive analysis, 5 epics, 25 stories
- ONTOLOGY_DESIGN.md — Why 20 node types, edge constraint rules
- AGENT3_ANALYSIS_PIPELINE_RESEARCH.md — Pipeline details
