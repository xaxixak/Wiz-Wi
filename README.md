# Workspace Intelligence Layer - Architecture Blueprint

## Goal
Build a **Workspace Intelligence Layer** that provides AI agents with semantic, architectural awareness of a software ecosystem. Unlike standard static analysis tools (AST, dependency graphs), this system uses AI agents to interpret *intent* ("story") and maintains a **self-healing** graph of relationships.

---

## 1. Core Ontology

### Node Types

| Level | Type | Description |
|-------|------|-------------|
| **Workspace** | `Workspace` | Root container (the folder you point at) |
| **Macro** | `Project` | A repo/app/package inside workspace (detected by `package.json`, `.git`, etc.) |
| | `Service` | Deployable unit (API, Worker, Frontend) |
| | `Resource` | Infrastructure: Database, Redis, S3, Queue |
| | `ExternalAPI` | Third-party dependency (Stripe, Twilio) |
| | `InfraConfig` | Docker, k8s, Terraform, `.env.example` |
| **Micro** | `File` | Physical source file |
| | `Module` | Logical grouping (folder/package) |
| | `Endpoint` | HTTP route handler (`POST /api/orders`) |
| | `AsyncHandler` | Event consumer, background job, cron task |
| | `Function` | Architecturally significant business logic |
| | `DataModel` | DB schema / ORM entity |
| | `Event` | Named business event (`ORDER_CREATED`) |
| | `CacheKey` | Named cache entry pattern |

### Edge Types

| Category | Type | Example |
|----------|------|---------|
| **Structural** | `CONTAINS` | Service → Endpoint |
| | `DEFINES` | File → DataModel |
| | `IMPORTS` | File → File |
| **Operational** | `READS_DB` / `WRITES_DB` | Endpoint → DataModel |
| | `CALLS_API` | Function → ExternalAPI |
| | `CALLS_SERVICE` | Service → Service (inter-service) |
| | `EMITS_EVENT` / `CONSUMES_EVENT` | Service → Event |
| | `CACHE_READ` / `CACHE_WRITE` | Function → CacheKey |
| | `WEBHOOK_SEND` / `WEBHOOK_RECEIVE` | Service → ExternalAPI |
| **Deployment** | `DEPLOYED_BY` | Service → InfraConfig |
| | `DEPENDS_ON` | Service → Resource |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     WORKSPACE ROOT                          │
└─────────────────────────────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │ Project A│    │ Project B│    │  Infra   │
    │ (API)    │    │ (Worker) │    │ (Docker) │
    └──────────┘    └──────────┘    └──────────┘
           │               │
           └───────┬───────┘
                   ▼
    ┌─────────────────────────────┐
    │   INGESTION PIPELINE        │
    │  1. Scanner (heuristics)    │
    │  2. Parser (Tree-sitter)    │
    │  3. Agent (LLM semantic)    │
    └─────────────────────────────┘
                   │
                   ▼
    ┌─────────────────────────────┐
    │        STORAGE              │
    │  • Graph DB (Neo4j/NetworkX)│
    │  • Vector DB (embeddings)   │
    │  • Metadata (Postgres/JSON) │
    └─────────────────────────────┘
                   │
                   ▼
    ┌─────────────────────────────┐
    │       SKILL API             │
    │  get_context(scope, focus)  │
    └─────────────────────────────┘
```

### A. Discovery & Ingestion

1. **Workspace Scanner**: Identifies `Project` roots by marker files (`package.json`, `go.mod`, `Dockerfile`).
2. **Static Parser (Tree-sitter)**: Extracts symbols (functions, classes, imports).
3. **Agentic Semantic Analyzer**: LLM agent reads code and outputs:
   - Node type classification
   - Edge discovery (what does this code *do*?)
   - Confidence score (0.0 - 1.0)

### B. Storage (Hybrid)

| Store | Purpose |
|-------|---------|
| **Graph DB** | Nodes + Edges for traversal queries ("blast radius") |
| **Vector DB** | Embeddings of node descriptions for semantic search |
| **Metadata Store** | Confidence scores, agent logs, timestamps |

---

## 3. Self-Healing Workflow

**Trigger**: File watcher (`watchdog`) or Git hook detects change.

**Flow**:
1. **Detect**: `payment-service/src/charge.ts` modified.
2. **Invalidate**: Mark derived nodes as `stale`.
3. **Re-index**: Agent re-reads file, updates graph.
4. **Propagate**: If new `Event` discovered, scan consumers.

---

## 4. Skill API

```python
def get_architectural_context(scope: str, focus: str) -> ContextPack:
    """
    scope: "Service: OrderService"
    focus: "Refactoring database schema"
    
    Returns:
    - relevant_nodes: List[GraphNode]
    - upstream: Who calls this?
    - downstream: What does this trigger?
    - risk_assessment: str
    """
```

---

## 5. MVP Roadmap

| Step | Task | Output |
|------|------|--------|
| 1 | Define Ontology | `ontology.py` ✅ |
| 2 | Build Scanner | `scanner.py` ✅ |
| 3 | Graph Store | `graph_store.py` ✅ |
| 4 | Static Ingest | Tree-sitter → File/Function nodes |
| 5 | Agent Enrichment | LLM → semantic edges, descriptions |
| 6 | File Watcher | `watchdog` for live updates |

---

## 6. Tech Stack

| Component | Tool |
|-----------|------|
| Parser | `tree-sitter` (multi-language) |
| Graph (MVP) | `NetworkX` (in-memory, simple) |
| Graph (Prod) | `Neo4j` or `FalkorDB` |
| Vector DB | `ChromaDB` or `Qdrant` |
| Agent Framework | `LangGraph` or plain Python + OpenAI |
| File Watcher | `watchdog` |
| API | `FastAPI` |
