# Agent 3: Analysis Pipeline & LLM Strategy Research

## Executive Summary

This document provides a deep, research-backed blueprint for the **analysis pipeline** that populates the Workspace Intelligence Layer's semantic graph. The core challenge: getting from an empty schema to **500+ nodes and 1800+ edges** requires a carefully orchestrated multi-pass pipeline where each pass builds on the previous, escalating from free heuristics to expensive LLM inference only when necessary.

The key insight from studying tools like Aider, Cursor, Sourcegraph SCIP, CodeQL, and Code-Graph-RAG: **80% of graph nodes can be extracted with zero AI cost** (heuristics + Tree-sitter + regex). The LLM is reserved for the 20% that requires semantic understanding -- the "story" edges (EMITS_EVENT, CALLS_SERVICE, READS_DB) and architectural intent.

---

## 1. Multi-Pass Pipeline Design

### Architecture: Five-Pass Escalation Model

The pipeline follows a **cost-escalation principle**: extract everything possible at each cheap tier before moving to the next. Each pass enriches the graph, and later passes use earlier results as context.

```
Pass 0: Workspace Discovery (FREE - filesystem heuristics)
  |
  v
Pass 1: Structural Extraction (FREE - Tree-sitter AST)
  |
  v
Pass 2: Pattern Matching (FREE - regex/heuristic rules)
  |
  v
Pass 3: LLM Semantic Analysis ($$$ - API calls)
  |
  v
Pass 4: Cross-Reference Validation ($$ - targeted LLM)
```

---

### Pass 0: Workspace Discovery (Zero Cost)

**What it does**: Already implemented in `scanner.py`. Detects project roots via marker files.

| Aspect | Detail |
|--------|--------|
| **Input** | Workspace root path |
| **Output** | `Workspace` node, `Project` nodes, `InfraConfig` nodes |
| **Method** | Filesystem traversal, marker file detection |
| **Confidence** | 0.95-1.0 (marker files are definitive) |
| **Cost** | Zero (pure filesystem) |
| **Speed** | <1 second for 500 files |
| **Nodes Created** | ~5-20 (workspace, projects, infra configs) |

**What to extract**:
- `Workspace` node (the root)
- `Project` nodes (detected by `package.json`, `pyproject.toml`, `go.mod`, etc.)
- `InfraConfig` nodes (Dockerfile, docker-compose.yml, k8s manifests, terraform)
- Preliminary `CONTAINS` edges (Workspace -> Project)
- File inventory per project (paths, sizes, extensions)
- `.env.example` parsing for resource hints (DB URLs, Redis, S3 bucket names)

**Enhancement over current scanner.py**: Parse `.env.example` and `docker-compose.yml` to pre-discover `Resource` nodes (databases, Redis, queues) before any code analysis begins. A `docker-compose.yml` that defines `postgres`, `redis`, and `rabbitmq` services immediately gives you 3 Resource nodes with high confidence.

---

### Pass 1: Structural Extraction (Tree-sitter AST -- Zero LLM Cost)

**What it does**: Parse every source file into an AST using Tree-sitter, extracting structural entities.

| Aspect | Detail |
|--------|--------|
| **Input** | All source files from discovered projects |
| **Output** | `File`, `Module`, `Function`, `DataModel` nodes; `CONTAINS`, `DEFINES`, `IMPORTS` edges |
| **Method** | Tree-sitter parsing with language-specific queries |
| **Confidence** | 0.85-0.95 (structural facts, not semantic) |
| **Cost** | Zero LLM cost (Tree-sitter is a local parser) |
| **Speed** | ~2-5 seconds for 500 files |
| **Nodes Created** | ~200-400 (bulk of the graph) |

**What Tree-sitter extracts reliably**:

```
PER FILE:
  - File node (path, language, size, last modified)
  - All class definitions (name, base classes, line range)
  - All function/method definitions (name, parameters, return type hints, line range)
  - All import statements (what is imported, from where)
  - Module-level constants and variables

RELATIONSHIPS:
  - File CONTAINS Function/Class
  - File IMPORTS File (resolved via import paths)
  - Module CONTAINS File
  - Class CONTAINS Method (methods within classes)
```

**Tree-sitter query examples** (from research on py-tree-sitter and Aider's implementation):

```python
# Python function definitions
"(function_definition name: (identifier) @name) @definition.function"

# Python class definitions
"(class_definition name: (identifier) @name) @definition.class"

# Python imports
"(import_from_statement module_name: (dotted_name) @module) @import"

# TypeScript/JavaScript route definitions (Express)
"(call_expression
  function: (member_expression
    property: (property_identifier) @method)
  arguments: (arguments (string) @path)) @route"
```

**Key implementation detail from Aider's architecture**: Aider builds a graph where each source file is a node, and edges connect files that have dependencies. It uses Tree-sitter to extract both **definitions** and **references** -- tracking where symbols are defined AND where they are used. This cross-referencing is critical and should be replicated.

**What Tree-sitter CANNOT tell you**:
- Whether a class is a DataModel vs. a utility class
- Whether a function is an endpoint handler vs. helper logic
- Business semantics (what events does this code emit?)
- Cross-service relationships
- The "why" behind any code structure

---

### Pass 2: Pattern Matching (Regex/Heuristic Rules -- Zero LLM Cost)

**What it does**: Apply framework-specific regex patterns and heuristics to classify nodes discovered in Pass 1 and discover new nodes.

| Aspect | Detail |
|--------|--------|
| **Input** | AST nodes from Pass 1 + raw source code |
| **Output** | Reclassified nodes (Function -> Endpoint, Class -> DataModel), new nodes (Event, CacheKey, ExternalAPI) |
| **Method** | Regex patterns, naming conventions, decorator detection |
| **Confidence** | 0.70-0.90 (pattern-based, framework-dependent) |
| **Cost** | Zero LLM cost |
| **Speed** | ~1-3 seconds for 500 files |
| **Nodes Reclassified/Created** | ~50-150 |

**Pattern rules organized by framework**:

```python
PATTERN_RULES = {
    # === ENDPOINT DETECTION ===
    "express_route": {
        "pattern": r"(app|router)\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]",
        "node_type": "Endpoint",
        "extract": {"method": "group(2)", "path": "group(3)"},
    },
    "fastapi_route": {
        "pattern": r"@(app|router)\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]",
        "node_type": "Endpoint",
        "extract": {"method": "group(2)", "path": "group(3)"},
    },
    "flask_route": {
        "pattern": r"@\w+\.route\s*\(\s*['\"]([^'\"]+)['\"].*methods\s*=\s*\[([^\]]+)\]",
        "node_type": "Endpoint",
    },
    "nestjs_route": {
        "pattern": r"@(Get|Post|Put|Delete|Patch)\s*\(\s*['\"]?([^'\")\s]*)",
        "node_type": "Endpoint",
    },

    # === DATA MODEL DETECTION ===
    "sqlalchemy_model": {
        "pattern": r"class\s+(\w+)\(.*(?:Base|Model|db\.Model).*\):",
        "node_type": "DataModel",
    },
    "django_model": {
        "pattern": r"class\s+(\w+)\(models\.Model\):",
        "node_type": "DataModel",
    },
    "prisma_model": {
        "file_pattern": "*.prisma",
        "pattern": r"model\s+(\w+)\s*\{",
        "node_type": "DataModel",
    },
    "typeorm_entity": {
        "pattern": r"@Entity\(\s*(?:['\"](\w+)['\"])?\s*\)",
        "node_type": "DataModel",
    },
    "mongoose_schema": {
        "pattern": r"new\s+(?:mongoose\.)?Schema\s*\(",
        "node_type": "DataModel",
    },

    # === EVENT DETECTION ===
    "event_emit": {
        "pattern": r"(?:emit|publish|dispatch|send|produce)\s*\(\s*['\"]([A-Z_]+)['\"]",
        "node_type": "Event",
        "edge_type": "EMITS_EVENT",
    },
    "event_consume": {
        "pattern": r"(?:on|subscribe|consume|listen|handle)\s*\(\s*['\"]([A-Z_]+)['\"]",
        "edge_type": "CONSUMES_EVENT",
    },

    # === EXTERNAL API DETECTION ===
    "http_client_call": {
        "pattern": r"(?:axios|fetch|requests|http|got)\.(get|post|put|delete)\s*\(\s*['\"]?(https?://[^'\")\s]+)",
        "node_type": "ExternalAPI",
        "edge_type": "CALLS_API",
    },

    # === CACHE KEY DETECTION ===
    "redis_key": {
        "pattern": r"(?:redis|cache)\.(get|set|del|hget|hset)\s*\(\s*['\"`]([^'\"`]+)",
        "node_type": "CacheKey",
    },

    # === ASYNC HANDLER DETECTION ===
    "celery_task": {
        "pattern": r"@(?:shared_task|app\.task|celery\.task)",
        "node_type": "AsyncHandler",
    },
    "bull_queue": {
        "pattern": r"(?:Queue|Worker)\s*\(\s*['\"](\w+)['\"]",
        "node_type": "AsyncHandler",
    },
    "cron_job": {
        "pattern": r"@(?:Cron|crontab|schedule)\s*\(",
        "node_type": "AsyncHandler",
    },
}
```

**Why this matters**: The creator said "Grep can't tell you subprocess, nested relations, interdependencies, cross-dimensional stuff." True -- but grep CAN tell you that `@app.post("/api/orders")` is an endpoint and `emit("ORDER_CREATED")` is an event emission. That is deterministic, free, and high-confidence. Save the LLM for the stuff grep genuinely cannot determine.

---

### Pass 3: LLM Semantic Analysis (The Expensive Pass)

**What it does**: Uses LLM to understand code semantics that cannot be determined structurally.

| Aspect | Detail |
|--------|--------|
| **Input** | Source code + Pass 1/2 context (known nodes/edges) |
| **Output** | Semantic edges, node descriptions, service classifications, confidence scores |
| **Method** | Anthropic API (Haiku for simple, Sonnet for complex) |
| **Confidence** | 0.60-0.85 (LLM-assessed, validated in Pass 4) |
| **Cost** | $2-15 for 500-file codebase (see cost section) |
| **Speed** | 5-30 minutes (parallelized) |
| **Nodes/Edges Created** | ~100-300 edges, ~50-100 new/reclassified nodes |

**What REQUIRES the LLM** (things Tree-sitter and regex genuinely cannot determine):

1. **Service boundary classification**: Is this project an API, a worker, a frontend, a shared library? Requires reading the code's purpose.

2. **Cross-service communication patterns**: When `ServiceA` calls `http://localhost:3001/api/orders`, the LLM must understand that `localhost:3001` refers to `ServiceB` based on docker-compose or config.

3. **Implicit database operations**: Code that calls `self.repository.save(user)` is a WRITES_DB edge, but the connection between the repository method and the actual DataModel requires understanding ORM patterns.

4. **Business event semantics**: Code like `notify_stakeholders(order)` might emit an event, but the event name is not explicit in the code.

5. **Architectural role assessment**: Is this function "architecturally significant" (business logic) or just a utility helper?

6. **Indirect dependencies**: Function A calls Function B which calls Function C which writes to the database. The transitive WRITES_DB relationship from A to the DataModel requires call-chain analysis.

7. **Config-to-code mapping**: Connecting environment variable `DATABASE_URL` to the actual database resource and the ORM models that use it.

**Model routing strategy** (from research on Anthropic pricing):

| Task | Model | Cost/1M tokens (input) | Rationale |
|------|-------|------------------------|-----------|
| Node description generation | Haiku 3.5 | $0.80 | Simple summarization task |
| Service classification | Haiku 3.5 | $0.80 | Pattern recognition with context |
| Endpoint -> DataModel edge discovery | Sonnet 4 | $3.00 | Requires tracing data flow |
| Cross-service communication mapping | Sonnet 4 | $3.00 | Complex multi-file reasoning |
| Business event discovery (implicit) | Sonnet 4 | $3.00 | Semantic understanding needed |
| Conflict resolution / ambiguity | Opus 4 | $15.00 | Only for genuinely hard cases |

---

### Pass 4: Cross-Reference Validation Pass

**What it does**: Validates LLM outputs from Pass 3, resolves conflicts, fills gaps, and ensures graph consistency.

| Aspect | Detail |
|--------|--------|
| **Input** | Complete graph from Passes 0-3 |
| **Output** | Validated edges, corrected confidence scores, gap alerts |
| **Method** | Graph analysis + targeted LLM verification |
| **Confidence** | Adjusts all scores based on cross-validation |
| **Cost** | $0.50-3.00 (targeted, not exhaustive) |
| **Speed** | 1-5 minutes |

**Validation checks**:

```python
VALIDATION_RULES = [
    # Structural consistency
    "Every Endpoint must have a CONTAINS edge from a Service",
    "Every DataModel must have a DEFINES edge from a File",
    "Every IMPORTS edge must connect two File nodes",

    # Semantic plausibility
    "An Endpoint that READS_DB must have a plausible code path to a DataModel",
    "A Service with EMITS_EVENT should have a consumer somewhere in the graph",
    "Events with no CONSUMES_EVENT edges are orphaned -- flag for review",

    # Bidirectional verification
    "If Service A CALLS_SERVICE Service B, verify B has an Endpoint matching the call",
    "If Function CALLS_API ExternalAPI, verify the URL/SDK is actually used in code",

    # Confidence calibration
    "Edges where source and target are in different projects get -0.1 confidence (higher chance of error)",
    "Edges confirmed by both regex (Pass 2) and LLM (Pass 3) get +0.1 confidence",
    "Orphan nodes (no edges) get flagged for re-analysis",
]
```

**Hallucination detection strategy** (from research):
1. **Cross-reference with AST**: If the LLM claims a function calls another function, verify the call exists in the AST.
2. **Symbol existence check**: If the LLM references a function name, verify it exists in the Tree-sitter symbol table.
3. **Bidirectional validation**: If Agent A says "X calls Y", check whether Agent B (analyzing Y) confirms "Y is called by X".
4. **Confidence thresholding**: Edges below 0.5 confidence are flagged for human review rather than included.

---

## 2. LLM Prompting Strategy

### Analysis Granularity: The Hybrid Approach

**Research finding**: Neither pure file-by-file nor function-by-function is optimal. The best approach is **file-level analysis with function-level focus**.

**Recommended strategy**:

| Granularity | When to Use | Why |
|-------------|-------------|-----|
| **File-level** | Initial classification (what type of file is this?) | Cheap, gives context for deeper analysis |
| **Function-level** | For architecturally significant functions identified in Pass 1/2 | Focused, reduces tokens, better accuracy |
| **Service-level** | Cross-file relationship discovery (after individual files analyzed) | Requires multi-file context |
| **Cluster-level** | Related files that form a logical unit (controller + service + repository) | Best for data flow tracing |

**The "cluster" approach** (inspired by Cursor's semantic chunking): Group related files before LLM analysis:

```python
def build_analysis_clusters(project_files, import_graph):
    """
    Group files into clusters for LLM analysis.

    A cluster = files that form a logical unit:
    - A controller + its service + its repository
    - A route file + its middleware + its handlers
    - A model + its migration + its seed file
    """
    clusters = []
    visited = set()

    for file in project_files:
        if file in visited:
            continue

        # Find strongly connected component in import graph
        cluster = get_connected_component(file, import_graph, max_size=5)
        clusters.append(cluster)
        visited.update(cluster)

    return clusters
```

### Prompt Structure: Use Anthropic's tool_use with Structured Outputs

**Research conclusion**: Anthropic's `tool_use` with `strict: true` (structured outputs) is the best approach for code analysis. It provides:
- **Schema enforcement**: The model literally cannot produce invalid output
- **Type safety**: Pydantic model validation on the Python side
- **No post-processing**: No regex parsing of LLM output needed
- **Higher accuracy**: Constrained generation reduces hallucination

**The tool definition for code analysis**:

```python
CODE_ANALYSIS_TOOL = {
    "name": "report_code_analysis",
    "description": "Report the semantic analysis results for a code file or function.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_classification": {
                "type": "string",
                "enum": ["endpoint_handler", "service_logic", "data_model",
                         "utility", "config", "middleware", "test", "migration"]
            },
            "service_name": {
                "type": "string",
                "description": "The service/module this file belongs to"
            },
            "nodes_discovered": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["Endpoint", "AsyncHandler", "Function",
                                     "DataModel", "Event", "CacheKey", "ExternalAPI"]
                        },
                        "description": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                    },
                    "required": ["name", "type", "description", "confidence"]
                }
            },
            "edges_discovered": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["READS_DB", "WRITES_DB", "CALLS_API",
                                     "CALLS_SERVICE", "EMITS_EVENT", "CONSUMES_EVENT",
                                     "CACHE_READ", "CACHE_WRITE", "WEBHOOK_SEND",
                                     "WEBHOOK_RECEIVE", "DEPENDS_ON"]
                        },
                        "description": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                    },
                    "required": ["source", "target", "type", "confidence"]
                }
            },
            "architectural_notes": {
                "type": "string",
                "description": "Any architectural observations not captured in nodes/edges"
            }
        },
        "required": ["file_classification", "nodes_discovered", "edges_discovered"]
    }
}
```

**System prompt for the analysis agent**:

```
You are a senior software architect analyzing code for a workspace intelligence system.

CONTEXT: You are analyzing files from the project "{project_name}" ({project_type}).
The workspace graph already contains these known entities:
{known_nodes_summary}

YOUR TASK: Analyze the provided code and identify:
1. What semantic entities exist (endpoints, data models, events, etc.)
2. What relationships exist between entities (data flow, API calls, event emission)
3. Your confidence level for each discovery (0.0-1.0)

RULES:
- Only report entities you can see evidence for in the code
- Use confidence < 0.7 for inferred relationships (not explicit in code)
- Reference existing known entities by their IDs when creating edges
- Do NOT hallucinate entity names -- use names from the actual code
- For endpoints, always include the HTTP method and path
- For events, use the exact event name string from code if available

Use the report_code_analysis tool to report your findings.
```

### Handling Large Files

**Strategy**: AST-guided chunking (not naive sliding window).

```python
def chunk_large_file(file_path: str, max_tokens: int = 6000) -> list[CodeChunk]:
    """
    Split large files into semantically meaningful chunks using Tree-sitter.

    Strategy:
    1. Parse the AST
    2. Extract top-level definitions (functions, classes)
    3. Each definition becomes a chunk (with import context prepended)
    4. If a single definition exceeds max_tokens, split at method boundaries
    """
    tree = parser.parse(source_code)

    # Always include imports as context prefix (typically <500 tokens)
    import_section = extract_imports(tree)

    chunks = []
    for definition in extract_top_level_definitions(tree):
        chunk_source = import_section + "\n\n" + definition.text

        if count_tokens(chunk_source) <= max_tokens:
            chunks.append(CodeChunk(
                source=chunk_source,
                start_line=definition.start_line,
                end_line=definition.end_line,
                context_type="full_definition"
            ))
        else:
            # Split class into individual methods
            for method in extract_methods(definition):
                method_source = import_section + "\n\n# class " + definition.name + ":\n" + method.text
                chunks.append(CodeChunk(
                    source=method_source,
                    start_line=method.start_line,
                    end_line=method.end_line,
                    context_type="method_in_class",
                    parent_class=definition.name
                ))

    return chunks
```

**Key principle from research**: Context quality matters more than context size. Sending the LLM a 2000-token focused chunk with relevant imports outperforms sending 50,000 tokens of the entire file. Aider proves this -- it uses a 1K token budget for repo maps by default and achieves excellent results.

---

## 3. Token Efficiency & Cost Analysis

### How Aider Manages Context

Aider's approach (from detailed research):

1. **Tree-sitter Symbol Extraction**: Parse all files, extract definitions (functions, classes) and references (where symbols are used).
2. **Dependency Graph**: Build a NetworkX graph where files are nodes and import/reference relationships are edges.
3. **PageRank Ranking**: Run personalized PageRank on the dependency graph, with personalization biased toward files the user is currently editing.
4. **Token Budget Binary Search**: Use binary search to find the maximum set of important symbols that fits within the configured token budget (default 1024 tokens).
5. **Disk Caching**: Cache parse results keyed by file modification time to avoid re-parsing unchanged files.

**Key takeaway**: Aider proves that you can represent a 500-file codebase in ~1K tokens using smart ranking. For our use case, this means the "known context" we send to the LLM in Pass 3 can be extremely compact.

### How Cursor Manages Codebase Indexing

Cursor's approach (from research):

1. **Semantic Chunking**: Break code into meaningful units (functions, classes) rather than arbitrary text splits.
2. **Embedding Generation**: Convert chunks to vectors using embedding models.
3. **Vector Storage**: Store in Turbopuffer (backed by S3) for fast similarity search.
4. **Retrieval**: Fetch top-25 chunks by vector similarity, then rerank to top-5 for LLM context.

### Cost Estimate for 500-File Codebase

**Assumptions**:
- Average file: 150 lines, ~600 tokens
- Total source tokens: 500 files x 600 tokens = 300,000 tokens
- Not every file needs LLM analysis (test files, configs, generated code excluded)
- ~60% of files need LLM analysis (300 files)
- Each LLM call includes ~500 tokens of system prompt + context

**Cost breakdown by pass**:

| Pass | Model | Files Analyzed | Tokens/Call (in+out) | Total Tokens | Cost |
|------|-------|---------------|---------------------|-------------|------|
| 0: Scanner | None | All | 0 | 0 | $0.00 |
| 1: Tree-sitter | None | All | 0 | 0 | $0.00 |
| 2: Regex | None | All | 0 | 0 | $0.00 |
| 3a: Classification | Haiku 3.5 | 300 | ~1,200 | 360K | $0.29 |
| 3b: Semantic edges | Sonnet 4 | 150 | ~2,000 | 300K | $0.90 |
| 3c: Complex analysis | Sonnet 4 | 50 | ~4,000 | 200K | $0.60 |
| 4: Validation | Haiku 3.5 | ~50 | ~1,500 | 75K | $0.06 |
| **Total** | | | | **~935K** | **~$1.85** |

**With prompt caching** (Anthropic's caching at 0.1x read cost):

The system prompt + ontology schema + known-nodes summary is ~2,000 tokens and is identical across all calls within a pass. With caching:
- First call: 2,000 tokens at full price (write: 1.25x)
- Subsequent 299 calls: 2,000 tokens at 0.1x price
- **Savings**: ~$0.50 on a $1.85 total, bringing it to ~$1.35

**With Batch API** (50% cost reduction for async processing):

If real-time results are not needed, use Anthropic's Message Batches API:
- Submit all Pass 3 analyses as a batch of up to 10,000 requests
- Results within 1 hour (most batches), guaranteed within 24 hours
- 50% cost reduction: $1.85 becomes **~$0.93**

**Combined optimization** (caching + batch): **~$0.68 for a 500-file codebase**

### Token Optimization Techniques

```python
# 1. COMPACT CONTEXT: Send only relevant known nodes, not the entire graph
def build_analysis_context(file_path, graph_store, import_graph):
    """Build minimal but sufficient context for LLM analysis."""

    # Only include nodes from files this file imports or is imported by
    related_files = import_graph.neighbors(file_path)
    related_nodes = []
    for f in related_files:
        related_nodes.extend(graph_store.get_nodes_by_file(f))

    # Format as compact summary (not full node objects)
    context = "Known entities in related files:\n"
    for node in related_nodes[:20]:  # Cap at 20 to save tokens
        context += f"- {node.type.value}: {node.name} (in {node.location.file_path})\n"

    return context  # ~200-400 tokens vs. 5000+ for full graph dump

# 2. SKIP ANALYSIS for obvious non-targets
SKIP_PATTERNS = [
    r".*\.test\.(ts|js|py)$",      # Test files
    r".*\.spec\.(ts|js)$",          # Spec files
    r".*/__tests__/.*",              # Test directories
    r".*/migrations/.*",            # DB migrations (use DataModel detection instead)
    r".*/generated/.*",             # Generated code
    r".*\.d\.ts$",                  # TypeScript declarations
    r".*/node_modules/.*",          # Dependencies
    r".*\.(css|scss|less|svg)$",    # Stylesheets and assets
]

# 3. BATCH similar files together (same controller pattern = same prompt)
def batch_similar_files(files, file_classifications):
    """Group files by their Pass 2 classification for batch processing."""
    batches = defaultdict(list)
    for f in files:
        classification = file_classifications.get(f, "unknown")
        batches[classification].append(f)
    return batches
```

---

## 4. Self-Healing / Incremental Updates

### Strategy: Git-Diff-Driven Incremental Re-indexing

**Research finding from Microsoft GraphRAG**: Version 0.5+ supports incremental updates by maintaining consistent entity IDs, allowing insert-update-merge operations. Our system should follow this pattern.

### Change Detection Flow

```
Git Commit / File Save
        |
        v
  [Change Detector]
        |
  git diff --name-status HEAD~1
        |
        v
  Changed files list:
    M src/orders/service.ts
    A src/orders/events.ts
    D src/orders/legacy.ts
        |
        v
  [Impact Analyzer]
        |
  For each changed file:
    1. Find all nodes sourced from this file
    2. Mark those nodes as STALE
    3. Find all edges from/to stale nodes
    4. Find downstream consumers of stale nodes
        |
        v
  [Selective Re-indexer]
        |
  Re-run Passes 1-3 ONLY for stale files
  Re-run Pass 4 for affected subgraph
```

### Staleness Propagation

This is the critical part the creator was describing -- "if file A changes and it defines event X, all consumers of X might be affected."

```python
class StalenessPropagor:
    """Propagate staleness through the graph when source files change."""

    def propagate(self, changed_files: list[str], graph: GraphStore) -> set[str]:
        """
        Returns set of all node IDs that need re-analysis.

        Propagation rules:
        1. All nodes sourced from changed files are stale
        2. If a stale node is an Event, all CONSUMES_EVENT edges' targets are stale
        3. If a stale node is a DataModel, all READS_DB/WRITES_DB sources are stale
        4. If a stale node is an Endpoint, all CALLS_SERVICE sources are stale
        5. Propagation stops at 2 hops to prevent cascade explosion
        """
        stale_nodes = set()

        # Direct staleness: nodes from changed files
        for file_path in changed_files:
            for node in graph.get_nodes_by_file(file_path):
                stale_nodes.add(node.id)
                graph.mark_stale(node.id)

        # Propagation pass 1: semantic dependencies
        newly_stale = set()
        for node_id in stale_nodes:
            node = graph.get_node(node_id)
            if not node:
                continue

            if node.type == NodeType.EVENT:
                # All consumers of this event need re-check
                for edge in graph.get_edges_to(node_id):
                    if edge.type == EdgeType.CONSUMES_EVENT:
                        newly_stale.add(edge.source_id)

            elif node.type == NodeType.DATA_MODEL:
                # All readers/writers of this model need re-check
                for edge in graph.get_edges_to(node_id):
                    if edge.type in (EdgeType.READS_DB, EdgeType.WRITES_DB):
                        newly_stale.add(edge.source_id)

            elif node.type == NodeType.ENDPOINT:
                # All callers of this endpoint need re-check
                for edge in graph.get_edges_to(node_id):
                    if edge.type == EdgeType.CALLS_SERVICE:
                        newly_stale.add(edge.source_id)

        stale_nodes.update(newly_stale)
        for nid in newly_stale:
            graph.mark_stale(nid)

        return stale_nodes
```

### Cost of Incremental vs. Full Re-index

| Scenario | Files Changed | Files Re-analyzed | Cost | Time |
|----------|--------------|-------------------|------|------|
| Single file edit | 1 | 1-5 (with propagation) | ~$0.01 | <10s |
| Feature branch (10 files) | 10 | 15-30 | ~$0.10 | <30s |
| Major refactor (50 files) | 50 | 80-120 | ~$0.40 | 1-2min |
| Full re-index (500 files) | 500 | 300 (LLM-analyzed) | ~$1.85 | 5-30min |

**Trigger strategy recommendation**:

| Trigger | Mechanism | Best For |
|---------|-----------|----------|
| **Git hook (post-commit)** | `post-commit` hook runs diff-based re-index | CI/CD integration, team workflows |
| **File watcher (watchdog)** | Debounced (5s) watcher triggers on save | Real-time development feedback |
| **On-demand** | CLI command or API call | Manual control, debugging |
| **Scheduled** | Cron job for full re-index weekly | Catch drift, validate consistency |

**Recommendation**: Start with **on-demand + git hook**. File watchers add complexity (debouncing, ignoring build artifacts) that is not necessary for MVP.

---

## 5. Agent Orchestration

### Architecture: Coordinator + Worker Pool

The creator's "hundreds of agents" maps to a **coordinator pattern** with a pool of parallel workers. Not literally hundreds of LLM calls in parallel (rate limits would prevent that), but a structured parallelization.

```
                    ┌──────────────┐
                    │  Coordinator │
                    │  (Orchestr.) │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
         ┌────▼────┐  ┌───▼────┐  ┌───▼────┐
         │ Worker 1 │  │ Worker 2│  │ Worker N│
         │ (files   │  │ (files  │  │ (files  │
         │  1-50)   │  │ 51-100) │  │ 451-500)│
         └────┬────┘  └───┬────┘  └───┬────┘
              │            │            │
              └────────────┼────────────┘
                           │
                    ┌──────▼───────┐
                    │    Merger    │
                    │ (Reconcile)  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Validator   │
                    │  (Pass 4)    │
                    └──────────────┘
```

### Work Splitting Strategy

**Research conclusion**: Split by **file** within each pass, but split by **pass** sequentially. Passes must run in order (each depends on the previous), but within a pass, files can be analyzed in parallel.

```python
import asyncio
from anthropic import AsyncAnthropic

class AnalysisOrchestrator:
    """Coordinates parallel analysis of code files."""

    def __init__(self, max_concurrent: int = 10):
        self.client = AsyncAnthropic()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.results: dict[str, AnalysisResult] = {}
        self.errors: list[AnalysisError] = []

    async def analyze_pass3(self, files: list[str], graph: GraphStore):
        """Run Pass 3 (LLM analysis) with parallel workers."""

        # Phase 1: Classification (Haiku, all files, parallel)
        classification_tasks = [
            self._classify_file(f, graph) for f in files
            if not self._should_skip(f)
        ]
        classifications = await asyncio.gather(
            *classification_tasks, return_exceptions=True
        )

        # Phase 2: Semantic analysis (Sonnet, complex files only, parallel)
        complex_files = [
            f for f, c in zip(files, classifications)
            if isinstance(c, dict) and c.get("needs_deep_analysis", False)
        ]
        semantic_tasks = [
            self._analyze_semantics(f, graph) for f in complex_files
        ]
        semantics = await asyncio.gather(
            *semantic_tasks, return_exceptions=True
        )

        return self._merge_results(classifications, semantics)

    async def _classify_file(self, file_path: str, graph: GraphStore):
        """Classify a single file using Haiku (cheap, fast)."""
        async with self.semaphore:
            source = read_file(file_path)
            context = build_compact_context(file_path, graph)

            response = await self.client.messages.create(
                model="claude-haiku-3-5-latest",
                max_tokens=1024,
                system=CLASSIFICATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"{context}\n\n```\n{source}\n```"}],
                tools=[CLASSIFICATION_TOOL],
                tool_choice={"type": "tool", "name": "classify_file"},
            )
            return parse_tool_result(response)

    async def _analyze_semantics(self, file_path: str, graph: GraphStore):
        """Deep semantic analysis using Sonnet (expensive, thorough)."""
        async with self.semaphore:
            source = read_file(file_path)
            context = build_rich_context(file_path, graph)

            response = await self.client.messages.create(
                model="claude-sonnet-4-latest",
                max_tokens=4096,
                system=SEMANTIC_ANALYSIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"{context}\n\n```\n{source}\n```"}],
                tools=[CODE_ANALYSIS_TOOL],
                tool_choice={"type": "tool", "name": "report_code_analysis"},
            )
            return parse_tool_result(response)
```

### Conflict Resolution When Agents Disagree

**Research finding**: Duplicate rate averages ~11% pre-merge and ~2% post-merge in parallel agent systems. For code analysis specifically:

```python
class ResultMerger:
    """Merge results from parallel analysis workers."""

    def merge(self, results: list[AnalysisResult]) -> MergedGraph:
        merged_nodes = {}
        merged_edges = {}
        conflicts = []

        for result in results:
            for node in result.nodes:
                if node.id in merged_nodes:
                    existing = merged_nodes[node.id]
                    if existing.type != node.type:
                        # Type conflict: keep higher confidence
                        conflicts.append(NodeConflict(existing, node))
                        if node.confidence > existing.confidence:
                            merged_nodes[node.id] = node
                    else:
                        # Same type: merge metadata, keep higher confidence
                        merged_nodes[node.id] = self._merge_node(existing, node)
                else:
                    merged_nodes[node.id] = node

            for edge in result.edges:
                edge_key = f"{edge.source}->{edge.target}:{edge.type}"
                if edge_key in merged_edges:
                    existing = merged_edges[edge_key]
                    # Average confidence scores
                    merged_edges[edge_key].confidence = (
                        existing.confidence + edge.confidence
                    ) / 2
                else:
                    merged_edges[edge_key] = edge

        # Flag conflicts for resolution
        if conflicts:
            self._resolve_conflicts(conflicts)

        return MergedGraph(
            nodes=list(merged_nodes.values()),
            edges=list(merged_edges.values()),
            conflict_count=len(conflicts),
        )

    def _resolve_conflicts(self, conflicts: list[NodeConflict]):
        """
        Resolution priority:
        1. Higher confidence wins
        2. If confidence is equal, more specific type wins
           (Endpoint > Function, DataModel > Function)
        3. If still tied, flag for human review
        """
        TYPE_SPECIFICITY = {
            NodeType.ENDPOINT: 10,
            NodeType.ASYNC_HANDLER: 9,
            NodeType.DATA_MODEL: 9,
            NodeType.EVENT: 8,
            NodeType.CACHE_KEY: 8,
            NodeType.FUNCTION: 5,
            NodeType.FILE: 1,
        }
        for conflict in conflicts:
            if abs(conflict.a.confidence - conflict.b.confidence) > 0.1:
                # Clear confidence winner
                conflict.resolution = "higher_confidence"
            else:
                # Use specificity
                spec_a = TYPE_SPECIFICITY.get(conflict.a.type, 0)
                spec_b = TYPE_SPECIFICITY.get(conflict.b.type, 0)
                if spec_a != spec_b:
                    conflict.resolution = "more_specific_type"
                else:
                    conflict.resolution = "needs_human_review"
```

### Parallelization Limits

Based on Anthropic API rate limits:

| Tier | Requests/Minute | Tokens/Minute | Practical Concurrency |
|------|----------------|---------------|----------------------|
| Tier 1 (new) | 50 | 40,000 | 5-8 concurrent |
| Tier 2 | 1,000 | 80,000 | 10-15 concurrent |
| Tier 3 | 2,000 | 160,000 | 15-20 concurrent |
| Tier 4 | 4,000 | 400,000 | 20-30 concurrent |

**Recommendation**: Start with `max_concurrent=10`, use exponential backoff with jitter for rate limit errors. The Batch API is better for initial full-index since it handles scheduling internally.

---

## 6. Practical Python Architecture

### Module Structure

```
workspace-intelligence/
├── ontology.py                    # [EXISTS] Node/Edge types, Pydantic models
├── scanner.py                     # [EXISTS] Pass 0: Workspace discovery
├── graph_store.py                 # [EXISTS] NetworkX storage + CRUD
│
├── pipeline/
│   ├── __init__.py
│   ├── orchestrator.py            # Main pipeline coordinator
│   ├── pass1_treesitter.py        # AST extraction
│   ├── pass2_patterns.py          # Regex/heuristic pattern matching
│   ├── pass3_llm.py               # LLM semantic analysis
│   ├── pass4_validation.py        # Cross-reference validation
│   └── chunker.py                 # AST-guided file chunking
│
├── llm/
│   ├── __init__.py
│   ├── client.py                  # Anthropic API wrapper with caching
│   ├── prompts.py                 # System prompts and tool definitions
│   ├── model_router.py            # Haiku/Sonnet/Opus routing logic
│   └── batch.py                   # Batch API integration
│
├── incremental/
│   ├── __init__.py
│   ├── change_detector.py         # Git diff parsing
│   ├── staleness.py               # Staleness propagation
│   └── selective_reindex.py       # Targeted re-analysis
│
├── merge/
│   ├── __init__.py
│   ├── result_merger.py           # Parallel result merging
│   └── conflict_resolver.py       # Disagreement resolution
│
├── config.py                      # Configuration (API keys, model choices, thresholds)
├── state.py                       # Pipeline state management (resume support)
├── cli.py                         # CLI interface
└── tests/
    ├── test_pass1.py
    ├── test_pass2.py
    ├── test_pass3.py
    ├── test_merge.py
    └── fixtures/                   # Sample code files for testing
```

### Execution Flow

```python
# cli.py - Main entry point
import asyncio
from pipeline.orchestrator import PipelineOrchestrator
from config import PipelineConfig

async def main():
    config = PipelineConfig.from_env()
    orchestrator = PipelineOrchestrator(config)

    # Full index
    result = await orchestrator.run_full_index("/path/to/workspace")
    print(f"Indexed: {result.nodes_created} nodes, {result.edges_created} edges")
    print(f"Cost: ${result.total_cost:.2f}")
    print(f"Duration: {result.duration_seconds:.1f}s")

    # Incremental update
    result = await orchestrator.run_incremental("/path/to/workspace")
    print(f"Updated: {result.nodes_updated} nodes, {result.stale_resolved} stale resolved")

asyncio.run(main())
```

```python
# pipeline/orchestrator.py
class PipelineOrchestrator:
    """
    Coordinates the multi-pass analysis pipeline.
    Supports resumption from any pass.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.graph = GraphStore()
        self.state = PipelineState()
        self.llm_client = CachedAnthropicClient(config)

    async def run_full_index(self, workspace_path: str) -> IndexResult:
        """Run the complete 5-pass pipeline."""

        # Check for resumable state
        if self.state.has_checkpoint(workspace_path):
            return await self._resume_from_checkpoint(workspace_path)

        try:
            # Pass 0: Discovery (always runs first)
            self.state.set_pass(0, "discovery")
            scan_result = self._run_pass0(workspace_path)
            self.state.checkpoint()

            # Pass 1: Tree-sitter extraction
            self.state.set_pass(1, "treesitter")
            ast_result = self._run_pass1(scan_result)
            self.state.checkpoint()

            # Pass 2: Pattern matching
            self.state.set_pass(2, "patterns")
            pattern_result = self._run_pass2(scan_result, ast_result)
            self.state.checkpoint()

            # Pass 3: LLM analysis (async, parallelized)
            self.state.set_pass(3, "llm_analysis")
            llm_result = await self._run_pass3(scan_result, ast_result, pattern_result)
            self.state.checkpoint()

            # Pass 4: Validation
            self.state.set_pass(4, "validation")
            validation_result = await self._run_pass4()
            self.state.checkpoint()

            self.state.mark_complete()
            return self._compile_result()

        except Exception as e:
            self.state.mark_error(str(e))
            raise

    def _run_pass0(self, workspace_path: str) -> ScanResult:
        """Pass 0: Workspace discovery."""
        scanner = WorkspaceScanner(workspace_path)
        scan_result = scanner.scan()

        # Create workspace node
        self.graph.add_node(GraphNode(
            id=f"workspace:{Path(workspace_path).name}",
            type=NodeType.WORKSPACE,
            name=Path(workspace_path).name,
            confidence=1.0,
        ))

        # Create project nodes
        for project in scan_result.projects:
            node = GraphNode(
                id=f"project:{project.name}",
                type=NodeType.PROJECT,
                name=project.name,
                metadata={"project_type": project.project_type.value},
                confidence=0.95,
            )
            self.graph.add_node(node)
            self.graph.add_edge(GraphEdge(
                source_id=f"workspace:{Path(workspace_path).name}",
                target_id=node.id,
                type=EdgeType.CONTAINS,
                confidence=1.0,
            ))

        # Parse docker-compose for resource discovery
        self._discover_resources_from_docker(workspace_path)

        return scan_result

    def _run_pass1(self, scan_result: ScanResult) -> ASTResult:
        """Pass 1: Tree-sitter structural extraction."""
        extractor = TreeSitterExtractor()
        ast_result = ASTResult()

        for project in scan_result.projects:
            for file_path in self._get_source_files(project):
                try:
                    file_ast = extractor.extract(file_path, project)
                    ast_result.add(file_path, file_ast)

                    # Create File node
                    self.graph.add_node(GraphNode(
                        id=f"file:{file_path}",
                        type=NodeType.FILE,
                        name=Path(file_path).name,
                        location=SourceLocation(
                            file_path=str(file_path),
                            start_line=1,
                            end_line=file_ast.total_lines,
                        ),
                        confidence=1.0,
                    ))

                    # Create Function/Class nodes
                    for symbol in file_ast.definitions:
                        self.graph.add_node(GraphNode(
                            id=f"function:{project.name}:{symbol.name}",
                            type=NodeType.FUNCTION,  # May be reclassified in Pass 2/3
                            name=symbol.name,
                            location=SourceLocation(
                                file_path=str(file_path),
                                start_line=symbol.start_line,
                                end_line=symbol.end_line,
                            ),
                            confidence=0.90,
                            metadata={
                                "kind": symbol.kind,  # "function", "class", "method"
                                "parameters": symbol.parameters,
                            }
                        ))

                    # Create IMPORTS edges
                    for imp in file_ast.imports:
                        resolved = self._resolve_import(imp, project)
                        if resolved:
                            self.graph.add_edge(GraphEdge(
                                source_id=f"file:{file_path}",
                                target_id=f"file:{resolved}",
                                type=EdgeType.IMPORTS,
                                confidence=0.95,
                            ))

                except Exception as e:
                    ast_result.add_error(file_path, str(e))

        return ast_result
```

### Error Handling & Retries

```python
# llm/client.py
class CachedAnthropicClient:
    """Anthropic API client with caching, retries, and cost tracking."""

    def __init__(self, config: PipelineConfig):
        self.client = AsyncAnthropic(api_key=config.anthropic_api_key)
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cost = 0.0
        self.retry_config = RetryConfig(
            max_retries=3,
            base_delay=1.0,
            max_delay=30.0,
            retry_on=[
                "overloaded_error",
                "rate_limit_error",
                "api_connection_error",
            ],
        )

    async def analyze(
        self,
        model: str,
        system: str,
        user_content: str,
        tools: list,
        tool_choice: dict,
    ) -> dict:
        """Make an API call with retries, caching, and cost tracking."""

        for attempt in range(self.retry_config.max_retries + 1):
            try:
                response = await self.client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=[{
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},  # Enable caching
                    }],
                    messages=[{"role": "user", "content": user_content}],
                    tools=tools,
                    tool_choice=tool_choice,
                )

                # Track usage
                self._track_usage(response.usage, model)

                # Extract tool result
                for block in response.content:
                    if block.type == "tool_use":
                        return block.input

                raise ValueError("No tool_use block in response")

            except anthropic.RateLimitError:
                if attempt == self.retry_config.max_retries:
                    raise
                delay = self.retry_config.base_delay * (2 ** attempt)
                delay += random.uniform(0, delay * 0.1)  # Jitter
                await asyncio.sleep(delay)

            except anthropic.APIStatusError as e:
                if e.status_code == 529:  # Overloaded
                    if attempt == self.retry_config.max_retries:
                        raise
                    await asyncio.sleep(self.retry_config.base_delay * (2 ** attempt))
                else:
                    raise

    def _track_usage(self, usage, model: str):
        """Track token usage and estimated cost."""
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens

        cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
        self.total_cache_read_tokens += cache_read

        # Cost calculation
        pricing = MODEL_PRICING[model]
        input_cost = (usage.input_tokens - cache_read) * pricing["input"] / 1_000_000
        cache_cost = cache_read * pricing["cache_read"] / 1_000_000
        output_cost = usage.output_tokens * pricing["output"] / 1_000_000

        self.total_cost += input_cost + cache_cost + output_cost

MODEL_PRICING = {
    "claude-haiku-3-5-latest": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
        "cache_write": 1.00,
    },
    "claude-sonnet-4-latest": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "claude-opus-4-latest": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
}
```

### Pipeline State Management (Resume Support)

```python
# state.py
import json
from pathlib import Path
from datetime import datetime, timezone

class PipelineState:
    """
    Manages pipeline state for resumability.

    If the pipeline crashes during Pass 3 (the expensive LLM pass),
    we can resume from where we left off instead of re-running Passes 0-2.
    """

    def __init__(self, state_dir: str = ".workspace-intelligence"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(exist_ok=True)
        self.state_file = self.state_dir / "pipeline_state.json"
        self.state = self._load_or_create()

    def _load_or_create(self) -> dict:
        if self.state_file.exists():
            with open(self.state_file) as f:
                return json.load(f)
        return {
            "status": "idle",
            "current_pass": None,
            "completed_passes": [],
            "files_processed": {},  # pass -> [file_paths]
            "errors": [],
            "started_at": None,
            "last_checkpoint": None,
        }

    def has_checkpoint(self, workspace_path: str) -> bool:
        return (
            self.state["status"] == "in_progress"
            and self.state.get("workspace_path") == workspace_path
        )

    def set_pass(self, pass_number: int, pass_name: str):
        self.state["status"] = "in_progress"
        self.state["current_pass"] = {"number": pass_number, "name": pass_name}
        if not self.state["started_at"]:
            self.state["started_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def mark_file_processed(self, pass_name: str, file_path: str):
        """Track which files have been processed in each pass (for resume)."""
        if pass_name not in self.state["files_processed"]:
            self.state["files_processed"][pass_name] = []
        self.state["files_processed"][pass_name].append(file_path)

    def get_unprocessed_files(self, pass_name: str, all_files: list[str]) -> list[str]:
        """Get files that haven't been processed yet in this pass."""
        processed = set(self.state["files_processed"].get(pass_name, []))
        return [f for f in all_files if f not in processed]

    def checkpoint(self):
        self.state["last_checkpoint"] = datetime.now(timezone.utc).isoformat()
        current = self.state["current_pass"]
        if current and current["name"] not in [p["name"] for p in self.state["completed_passes"]]:
            self.state["completed_passes"].append(current)
        self._save()

    def mark_complete(self):
        self.state["status"] = "complete"
        self.state["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def mark_error(self, error: str):
        self.state["errors"].append({
            "error": error,
            "pass": self.state["current_pass"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._save()

    def _save(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)
```

---

## 7. Key Recommendations Summary

### What to Build First (Priority Order)

1. **Pass 1: Tree-sitter extraction** (`pass1_treesitter.py`) -- This creates the bulk of your graph (60-70% of nodes) with zero cost. Start here.

2. **Pass 2: Pattern matching** (`pass2_patterns.py`) -- Framework-specific regex rules reclassify nodes and discover edges. Still free. Adds another 15-20% of the graph.

3. **Pass 3: LLM analysis** (`pass3_llm.py`) -- Start with Haiku classification only. Get the cheap wins before investing in Sonnet analysis. Adds the remaining 15-25%.

4. **Incremental updates** (`change_detector.py`, `staleness.py`) -- Essential for the "self-healing" promise. Without this, the graph rots within days.

5. **Pass 4: Validation** (`pass4_validation.py`) -- Cross-reference check. Important for quality but can be deferred after MVP.

### Architecture Decisions

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| LLM provider | Anthropic (Claude) | Best structured outputs, prompt caching, batch API |
| Output format | tool_use with strict:true | Schema-guaranteed output, no parsing errors |
| Parsing library | Tree-sitter (via py-tree-sitter) | Multi-language, fast, proven (Aider uses it) |
| Parallelism | asyncio + semaphore (10 concurrent) | Respects rate limits, simple to implement |
| State management | JSON checkpoint files | Simple, resumable, no external dependencies |
| Initial full index | Batch API | 50% cheaper, results within 1 hour |
| Incremental updates | Git hook + diff-based | Precise change detection, low cost |
| Graph storage (MVP) | NetworkX (already built) | Good enough for 500-node graphs |
| Graph storage (prod) | Neo4j or FalkorDB | Needed when graph exceeds ~10K nodes |

### What NOT to Build

- **Do not build a LangGraph pipeline**: Direct Anthropic API calls with asyncio are simpler, cheaper, and more controllable. LangGraph adds abstraction overhead without benefit for this use case.
- **Do not embed code for RAG**: The graph IS the retrieval mechanism. Embeddings are useful for the Skill API (finding relevant nodes by natural language query) but not for the analysis pipeline itself.
- **Do not analyze test files with LLM**: Test files rarely contain architecturally significant nodes. Skip them in Pass 3.
- **Do not attempt to trace runtime behavior**: Static analysis + LLM semantic understanding is the right level. Runtime tracing (profiling, logging analysis) is a different system.

---

## Sources

### Multi-Pass Pipeline & Static Analysis
- [Semantic Code Indexing with AST and Tree-sitter for AI Agents](https://medium.com/@email2dineshkuppan/semantic-code-indexing-with-ast-and-tree-sitter-for-ai-agents-part-1-of-3-eb5237ba687a)
- [STALL+: Boosting LLM-based Repository-level Code Completion with Static Analysis](https://arxiv.org/html/2406.10018v1)
- [LLM-Driven SAST-Genius: A Hybrid Static Analysis Framework](https://www.arxiv.org/pdf/2509.15433)
- [AI Static Analysis Pipeline](https://github.com/247arjun/ai-static-analysis-pipeline/blob/main/Elevating%20Code%20Security%20and%20Reliability%20via%20LLM-Augmented%20Static%20Analysis.md)
- [IRIS: LLM-Assisted Static Analysis for Whole-Repository Reasoning](https://openreview.net/pdf?id=9LdJDU7E91)

### Aider Architecture
- [Building a better repository map with tree sitter (Aider)](https://aider.chat/2023/10/22/repomap.html)
- [Aider Repository Map Documentation](https://aider.chat/docs/repomap.html)
- [Understanding Code Context (Aider Deep Wiki)](https://opendeep.wiki/Aider-AI/aider/core-concepts-code-context)

### Cursor Indexing
- [How Cursor Actually Indexes Your Codebase](https://towardsdatascience.com/how-cursor-actually-indexes-your-codebase/)
- [Cursor Codebase Indexing Documentation](https://cursor.com/docs/context/codebase-indexing)

### Sourcegraph SCIP
- [SCIP Code Intelligence Protocol](https://github.com/sourcegraph/scip)
- [SCIP: A better code indexing format than LSIF](https://sourcegraph.com/blog/announcing-scip)
- [Writing an Indexer (Sourcegraph)](https://sourcegraph.com/docs/code-search/code-navigation/writing_an_indexer)

### CodeQL
- [CodeQL Data Flow Analysis](https://codeql.github.com/docs/writing-codeql-queries/about-data-flow-analysis/)

### Code-Graph-RAG
- [Code-Graph-RAG: Knowledge Graph + Tree-sitter RAG](https://github.com/vitali87/code-graph-rag)
- [GraphRAG for Devs (Memgraph)](https://memgraph.com/blog/graphrag-for-devs-coding-assistant)
- [How I Built CodeRAG with Dependency Graph Using Tree-Sitter](https://medium.com/@shsax/how-i-built-coderag-with-dependency-graph-using-tree-sitter-0a71867059ae)

### Continue.dev
- [Continue.dev Codebase Retrieval](https://docs.continue.dev/features/codebase-embeddings)
- [Continue.dev + LanceDB Architecture](https://lancedb.com/blog/the-future-of-ai-native-development-is-local-inside-continues-lancedb-powered-evolution/)

### Anthropic API & Costs
- [Anthropic Prompt Caching Documentation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Anthropic Batch Processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing)
- [Anthropic Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)
- [Claude Model Selection Guide](https://claudefa.st/blog/models/model-selection)
- [Prompt Caching: 10x Cheaper LLM Tokens](https://ngrok.com/blog/prompt-caching/)

### Agent Orchestration
- [Multi-Agent Orchestration: Running 10+ Claude Instances in Parallel](https://dev.to/bredmond1019/multi-agent-orchestration-running-10-claude-instances-in-parallel-part-3-29da)
- [AI Agent Design Patterns (Microsoft)](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
- [Multi-Agent Orchestration (Agentic Systems Series)](https://gerred.github.io/building-an-agentic-system/second-edition/part-iv-advanced-patterns/chapter-10-multi-agent-orchestration.html)

### GraphRAG Incremental Updates
- [Microsoft GraphRAG Incremental Update Discussion](https://github.com/microsoft/graphrag/discussions/511)

### Context Window & Chunking
- [Chunking Strategies for LLM Applications (Pinecone)](https://www.pinecone.io/learn/chunking-strategies/)
- [Context Window Management Strategies](https://www.getmaxim.ai/articles/context-window-management-strategies-for-long-context-ai-agents-and-chatbots/)

### Tree-sitter
- [Tree-sitter Python Bindings](https://github.com/tree-sitter/py-tree-sitter)
- [Tree-sitter Code Navigation](https://tree-sitter.github.io/tree-sitter/4-code-navigation.html)
- [Diving into Tree-Sitter: Parsing Code with Python](https://dev.to/shrsv/diving-into-tree-sitter-parsing-code-with-python-like-a-pro-17h8)

### Hallucination Detection
- [CodeHalu: Investigating Code Hallucinations in LLMs](https://ojs.aaai.org/index.php/AAAI/article/download/34717/36872)
- [Detecting Hallucinations with LLM-as-a-judge (Datadog)](https://www.datadoghq.com/blog/ai/llm-hallucination-detection/)
