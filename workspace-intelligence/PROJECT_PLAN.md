# Workspace Intelligence - Project Plan

> Single source of truth for project status, architecture, and roadmap.
> Last updated: 2026-02-11

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

### Pipeline Passes (Code Exists, Not Wired Together)
- `pass1_treesitter.py` — Extracts File, Function, Import nodes from AST
- `pass2_patterns.py` — Regex patterns for endpoints, models, events
- `pass3_llm.py` — LLM semantic analysis framework
- `pass4_validation.py` — Cross-reference validation stub
- `orchestrator.py` — Skeleton only, passes NOT connected

### Viewer (Working)
- `viewer/server.py` — HTTP server with SSE, live updates, scan API
- `viewer/index.html` — D3.js force graph, filtering, search, node details

### Runtime Layer (Working, Built 2026-02-10)
- `test-shop/src/services/wi-probe.js` — Express middleware + event listener
- Viewer SSE integration — Live node highlighting, flow paths, activity log
- Runtime events flow: test-shop → probe → WI server → SSE → viewer

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

### What's MISSING from the Graph
| What | Why It Matters | Status |
|------|---------------|--------|
| **Directory/MODULE nodes** | No folder hierarchy (src/, routes/, services/) | Not built |
| **IMPORTS edges** | No file-to-file dependencies (app.js → admin.js) | Code exists, not wired |
| **ROUTES_TO edges** | No HTTP routing (app.js routes /api/admin → admin.js) | Not built |
| **Operational edges** | No READS_DB, CALLS_API, CACHE_READ | Needs LLM (Pass 3) |

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

### Phase A: Wire the Pipeline (Priority 1 - Next Sprint)
*Goal: Make the graph actually useful by connecting existing code*

| # | Task | What It Fixes |
|---|------|--------------|
| A1 | Wire orchestrator: Pass 0 → Pass 1 → Pass 2 | Graph gets File, Function, IMPORTS, DEFINES edges automatically |
| A2 | Add MODULE nodes for directories | Folder hierarchy visible in graph (src/ → routes/ → products.js) |
| A3 | Add IMPORTS edge detection in Pass 1 | File-to-file connections (app.js → admin.js) visible |
| A4 | Add ROUTES_TO pattern in Pass 2 | HTTP routing visible (app.use('/api/products', productsRouter)) |
| A5 | CLI: `python cli.py index <path>` runs full pipeline | One command to scan a codebase |

**Definition of Done**: Scan test-shop, graph shows folder tree + imports + routes + 300+ nodes with structural edges.

### Phase B: Viewer Layers (Priority 2)
*Goal: Make the viewer understandable at any scale*

| # | Task | What It Fixes |
|---|------|--------------|
| B1 | Tree/hierarchy view mode | Shows folder structure like file explorer |
| B2 | Focal point navigation | Click a node → expand 1-2 hops, don't load everything |
| B3 | Layer toggle (structure / dependency / runtime) | User picks what to see |
| B4 | Node limit + pagination | Never render 1000+ nodes at once |
| B5 | Subgraph extraction API | Server returns focused subgraph, not full graph |

**Definition of Done**: Open viewer, see folder tree. Click a file, see its connections expand outward. 1000-node project doesn't hang.

### Phase C: Incremental & Change-Driven (Priority 3)
*Goal: Graph stays fresh without re-scanning everything*

| # | Task | What It Fixes |
|---|------|--------------|
| C1 | File watcher (watchdog / git hook) | Detect changes automatically |
| C2 | Change-driven scan | Only re-scan changed files + 2-hop cascade |
| C3 | Change-driven viewer | Auto-navigate to changed node, highlight affected subgraph |
| C4 | Expansion mode scan | Scan folder-by-folder on demand, not all at once |

**Definition of Done**: Change a file, graph updates in <10s. Viewer auto-focuses on the changed node.

### Phase D: Intelligence (LLM-Powered)
*Goal: Add the "story" — what code DOES, not just what it IS*

| # | Task | What It Fixes |
|---|------|--------------|
| D1 | LLM client + prompts (Anthropic API) | Foundation for semantic analysis |
| D2 | Pass 3: LLM semantic edges | READS_DB, CALLS_API, EMITS_EVENT discovered |
| D3 | Node descriptions / stories | "This endpoint creates a product and emits PRODUCT_CREATED" |
| D4 | Pass 4: Validation | Cross-reference LLM claims against AST |

**Cost**: ~$2 for 500 files.

### Phase E: Consumption (AI Agents Use the Graph)
*Goal: Other AI tools can query the intelligence*

| # | Task |
|---|------|
| E1 | MCP server (SearchEntity, GetContext, ImpactAnalysis) |
| E2 | Enhanced ContextPack with token budgets |
| E3 | CLI commands (query, impact, status, export) |

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
    orchestrator.py        # Pipeline coordinator (skeleton)
    pass1_treesitter.py    # AST extraction (FREE)
    pass2_patterns.py      # Regex patterns (FREE)
    pass3_llm.py           # LLM semantic analysis (~$2)
    pass4_validation.py    # Cross-reference check
    chunker.py             # File splitting for large files

  viewer/
    server.py              # HTTP + SSE server
    index.html             # D3.js graph viewer

  graphs/                  # Generated graph JSON files
```

---

## References

- RESEARCH_SYNTHESIS.md — Full competitive analysis, 5 epics, 25 stories
- ONTOLOGY_DESIGN.md — Why 20 node types, edge constraint rules
- AGENT3_ANALYSIS_PIPELINE_RESEARCH.md — Pipeline details
