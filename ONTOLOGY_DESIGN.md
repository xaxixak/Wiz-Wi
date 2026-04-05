# Workspace Intelligence Layer -- Ontology Design Document

## Agent 2: Ontology & Schema Architect

---

## 1. Research Foundation

This design synthesizes insights from five major approaches to code knowledge graphs:

1. **Joern Code Property Graph (CPG)** -- Multi-layered specification with 18 layers
   (FileSystem, Namespace, Method, Type, AST, CallGraph, CFG, etc.). Key insight:
   *layers of abstraction* are the organizing principle, not flat lists.

2. **LSIF/SCIP (Microsoft/Sourcegraph)** -- Language Server Index Format for
   cross-language code intelligence. Key insight: *language-agnostic intermediate
   representations* using vertices and edges with extensible kinds.

3. **Software Archaeology Ontology (SAR)** -- 96 classes, 23 properties, 1076 triples.
   Key insight: distinguishes *Continuants* (persistent entities) from *Occurrents*
   (processes/events), grounded in formal ontology (BFO).

4. **GraphGen4Code (IBM/WALA)** -- 2 billion triples from 1.3M Python files. Key
   insight: captures *interprocedural data flow* and links code to external knowledge
   (docs, forums).

5. **Neo4j Codebase Knowledge Graphs** -- Production patterns for .NET/Java analysis.
   Key insight: *Projects and Packages at the top for architectural overview,
   Classes and Methods at the bottom for detail*.

---

## 2. Design Principles

### P1: Three-Tier Hierarchy (Macro / Meso / Micro)

Every node type belongs to exactly one tier. This is not cosmetic -- it determines
query scope, context window sizing, and visualization granularity.

- **Tier 1 (Macro)**: Workspace, Project, Service, Resource, ExternalAPI -- the
  "architecture diagram" level. An architect thinks at this level.
- **Tier 2 (Meso)**: Module, Router, Collection, InfraConfig, Script, Queue -- the
  "module/component" level. A tech lead thinks at this level.
- **Tier 3 (Micro)**: Endpoint, Function, DataModel, Event, AsyncHandler, Middleware,
  TypeDef, CacheKey, EnvVar -- the "code element" level. A developer thinks at this
  level.

### P2: Tags Over Types (The Boundary Rule)

A concept becomes a first-class NodeType when it has **unique edge semantics** --
meaning it participates in relationships that no other type does. If a concept only
differs by metadata, it should be a tag on an existing type.

**Promoted to first-class types** (have unique edges):
- MIDDLEWARE -- has INTERCEPTS edges (no other type does)
- COLLECTION -- has distinct READS_DB/WRITES_DB semantics separate from DataModel
- QUEUE -- has ENQUEUES/DEQUEUES edges
- ROUTER -- has ROUTES_TO edges
- ENV_VAR -- has CONFIGURES edges

**Kept as tags** (no unique edge semantics):
- TEST -- it is a Function with tag `role:test` and edge TESTS (which Functions can have)
- SCRIPT -- it is a Function with tag `role:script` (standalone entry point)
- UTILITY -- it is a Function with tag `role:utility`
- MIGRATION -- it is a Function with tag `role:migration` plus edge MIGRATES
- TYPE_DEFINITION -- it is a DataModel with tag `kind:type_def` or `kind:interface`

Rationale: Thorbit had 53 script nodes and 25 utility nodes. These are functionally
just Functions that happen to be standalone or helper-shaped. Making them separate
types doubles the query complexity without adding unique traversal capability. A tag
lets you filter (`Function WHERE tag=script`) without polluting the type enum.

### P3: Operational Edges Tell The Story

The most important innovation in this ontology is that edges are not just structural
("contains", "imports") but *operational* -- they describe what happens at runtime.
"Endpoint X WRITES_DB to Collection Y on user signup" is a story. "File A IMPORTS
File B" is plumbing. Both matter, but the operational edges are what make this system
valuable for AI code understanding.

### P4: Provenance Is Not Optional

Every node and edge must track *how it was discovered* (scanner heuristic vs. LLM
inference vs. human annotation) and *how confident we are*. This is critical for:
- Knowing when to re-scan (staleness)
- Trusting AI-generated edges vs. parser-derived edges
- Debugging graph quality issues

### P5: Multi-Language By Design

The schema must be language-agnostic. We achieve this by:
- Using `language` as a field on GraphNode, not baking it into types
- Using semantic types (Endpoint, Function) not syntactic types (class_declaration,
  function_definition)
- Storing language-specific metadata in the `metadata` dict

---

## 3. Node Type Taxonomy

### Tier 1: Architecture Level (Macro)

| Type | Description | Example | Why First-Class |
|------|-------------|---------|-----------------|
| WORKSPACE | Root container for entire dev environment | `~/projects/acme-corp` | Unique: only type that CONTAINS Projects |
| PROJECT | A repository / deployable package | `order-service`, `shared-lib` | Unique: has build system, dependency manifest |
| SERVICE | A running deployable unit | API server, worker, frontend SPA | Unique: has deployment config, health endpoints |
| RESOURCE | Infrastructure dependency | PostgreSQL, Redis, S3 bucket | Unique: target of DEPENDS_ON, no source code |
| EXTERNAL_API | Third-party service | Stripe API, Twilio, Auth0 | Unique: target of CALLS_API, defined by spec not code |

### Tier 2: Component Level (Meso)

| Type | Description | Example | Why First-Class |
|------|-------------|---------|-----------------|
| MODULE | Logical grouping (folder/package/namespace) | `src/auth/`, `com.acme.orders` | Unique: CONTAINS code elements, has import boundaries |
| FILE | Physical source file | `user.controller.ts` | Unique: has source_hash, language, physical location |
| ROUTER | Route group with shared config | Express Router, Flask Blueprint | Unique: ROUTES_TO endpoints, carries shared middleware/prefix |
| COLLECTION | Database table/collection (physical) | `users` table, `orders` collection | Unique: target of READS_DB/WRITES_DB distinct from ORM model |
| INFRA_CONFIG | Deployment/infrastructure config | Dockerfile, k8s manifest, .env | Unique: target of DEPLOYED_BY, CONFIGURES |
| QUEUE | Message queue / topic / channel | `order.events` Kafka topic, SQS queue | Unique: target of ENQUEUES/DEQUEUES, distinct from Event |

### Tier 3: Code Element Level (Micro)

| Type | Description | Example | Why First-Class |
|------|-------------|---------|-----------------|
| ENDPOINT | HTTP/gRPC/GraphQL handler | `POST /api/users`, `query { users }` | Unique: has HTTP method, path, request/response schema |
| FUNCTION | Architecturally significant logic | `calculateShipping()`, `validateOrder()` | Unique: core unit of business logic, has call graph |
| ASYNC_HANDLER | Event consumer / background job / cron | `processPaymentJob`, `onOrderCreated` | Unique: has trigger source, schedule, retry config |
| DATA_MODEL | ORM entity / schema definition | `UserModel`, `OrderSchema` | Unique: DEFINES schema, has field definitions |
| EVENT | Named domain event | `ORDER_CREATED`, `USER_SIGNED_UP` | Unique: target of EMITS_EVENT/CONSUMES_EVENT |
| MIDDLEWARE | Request/response interceptor | `authMiddleware`, `rateLimiter` | Unique: INTERCEPTS endpoints, has order/priority |
| TYPE_DEF | Interface / type alias / enum / DTO | `UserDTO`, `OrderStatus` enum | Unique: target of IMPLEMENTS/CONFORMS_TO |
| CACHE_KEY | Named cache pattern | `user:{id}:profile` | Unique: target of CACHE_READ/CACHE_WRITE |
| ENV_VAR | Environment variable / config key | `DATABASE_URL`, `STRIPE_SECRET_KEY` | Unique: target of CONFIGURES, has sensitivity level |

### Total: 20 node types (up from 14)

Added 6 new types: ROUTER, COLLECTION, QUEUE, MIDDLEWARE, TYPE_DEF, ENV_VAR.
Did NOT add: TEST, SCRIPT, UTILITY, MIGRATION (handled by tags on Function).

---

## 4. Why Specific Types Were NOT Added

### TEST (kept as tag)
Tests are Functions that happen to assert things. They share all edge semantics with
Functions (CALLS, IMPORTS, READS_DB). The only unique edge would be TESTS, but any
Function can test another Function. Instead: `Function` with `tags: ["test"]` and
metadata `{test_framework: "pytest", test_type: "unit"}`.

### SCRIPT (kept as tag)
Scripts are standalone entry-point Functions. They share call graphs, imports, and
DB access with Functions. Instead: `Function` with `tags: ["script", "standalone"]`
and metadata `{entry_point: true, schedule: "daily"}`.

### UTILITY (kept as tag)
Utilities are Functions with high fan-in (many callers). This is a graph metric, not
a type. Instead: `Function` with `tags: ["utility", "helper"]`. You can compute
"utility-ness" from the graph topology.

### MIGRATION (kept as tag)
Migrations are Functions that transform database schemas. They have temporal ordering
and target Collections. Instead: `Function` with `tags: ["migration"]` and metadata
`{migration_version: "20240101_001", direction: "up"}` plus a MIGRATES edge.

---

## 5. Edge Type Taxonomy

### Structural Edges (Graph Skeleton)

| Edge | Source -> Target | Semantics |
|------|-----------------|-----------|
| CONTAINS | Workspace->Project, Project->Service, Module->Function, etc. | Hierarchical containment |
| DEFINES | File -> DataModel/TypeDef/Endpoint/Function | "This file defines this entity" |
| IMPORTS | File -> File, Module -> Module | Static dependency |
| IMPLEMENTS | DataModel/Function -> TypeDef | Satisfies an interface/type contract |
| INHERITS | DataModel -> DataModel, TypeDef -> TypeDef | Class/type inheritance |

### Data Flow Edges (The Story)

| Edge | Source -> Target | Semantics |
|------|-----------------|-----------|
| READS_DB | Function/Endpoint -> Collection | Queries/reads from a data store |
| WRITES_DB | Function/Endpoint -> Collection | Inserts/updates/deletes in a data store |
| MIGRATES | Function -> Collection | Schema migration targeting a collection |

### Communication Edges (Inter-Component)

| Edge | Source -> Target | Semantics |
|------|-----------------|-----------|
| CALLS_API | Function/Service -> ExternalAPI | Outbound HTTP/SDK call to third party |
| CALLS_SERVICE | Service -> Service | Inter-service communication (HTTP/gRPC) |
| CALLS | Function -> Function | Direct function invocation |
| WEBHOOK_SEND | Service -> ExternalAPI | Outbound webhook delivery |
| WEBHOOK_RECEIVE | Endpoint -> ExternalAPI | Inbound webhook reception |

### Event/Async Edges

| Edge | Source -> Target | Semantics |
|------|-----------------|-----------|
| EMITS_EVENT | Function/Service -> Event | Publishes a domain event |
| CONSUMES_EVENT | AsyncHandler -> Event | Subscribes to a domain event |
| ENQUEUES | Function -> Queue | Puts work onto a message queue |
| DEQUEUES | AsyncHandler -> Queue | Processes work from a message queue |
| SCHEDULES | InfraConfig/Function -> AsyncHandler | Triggers on a cron/schedule |

### Caching Edges

| Edge | Source -> Target | Semantics |
|------|-----------------|-----------|
| CACHE_READ | Function/Endpoint -> CacheKey | Reads from cache |
| CACHE_WRITE | Function/Endpoint -> CacheKey | Writes to cache |

### Routing & Middleware Edges

| Edge | Source -> Target | Semantics |
|------|-----------------|-----------|
| ROUTES_TO | Router -> Endpoint | Maps a route prefix to handler |
| INTERCEPTS | Middleware -> Endpoint/Router | Middleware applied to route(s) |
| VALIDATES | Middleware/Function -> DataModel/TypeDef | Input validation against schema |
| AUTHENTICATES | Middleware -> Endpoint/Router | Auth requirement on route(s) |

### Configuration & Deployment Edges

| Edge | Source -> Target | Semantics |
|------|-----------------|-----------|
| DEPENDS_ON | Service -> Resource/Queue | Runtime infrastructure dependency |
| DEPLOYED_BY | Service -> InfraConfig | Deployment configuration |
| CONFIGURES | EnvVar -> Service/Resource/Function | Configuration injection |

### Quality & Testing Edges

| Edge | Source -> Target | Semantics |
|------|-----------------|-----------|
| TESTS | Function -> Function/Endpoint/DataModel | Test coverage relationship |

### Total: 27 edge types (up from 15)

Added 12 new edges: IMPLEMENTS, INHERITS, MIGRATES, CALLS, ENQUEUES, DEQUEUES,
SCHEDULES, ROUTES_TO, INTERCEPTS, VALIDATES, AUTHENTICATES, CONFIGURES, TESTS.

---

## 6. Data Model Improvements

### 6.1 GraphNode Improvements

New fields added to GraphNode:

| Field | Type | Why Needed |
|-------|------|-----------|
| `provenance` | enum (scanner, llm, human, import) | Know HOW this node was discovered. A scanner-found endpoint has higher confidence than an LLM-inferred one. |
| `source_hash` | Optional[str] | Content hash of the source code that defined this node. Used for staleness detection: if source changes, hash mismatches, node is stale. |
| `language` | Optional[str] | Multi-language workspaces need to know which language a node belongs to. |
| `tags` | List[str] | Flexible classification without type explosion. Supports `test`, `script`, `utility`, `migration`, `deprecated`, `critical-path`, etc. |
| `parent_id` | Optional[str] | Fast containment lookups without traversing CONTAINS edges. A Function's parent_id is its Module; a Module's parent_id is its Project. |
| `tier` | computed | Derived from type. Used for query scoping and context window management. |
| `version` | int | Monotonically increasing version counter. Enables optimistic concurrency and change tracking. |

### 6.2 GraphEdge Improvements

New fields added to GraphEdge:

| Field | Type | Why Needed |
|-------|------|-----------|
| `provenance` | enum (scanner, llm, human, import) | Same rationale as nodes. An LLM-inferred "CALLS_API" edge might be wrong; a parser-found "IMPORTS" edge is definitive. |
| `is_stale` | bool | Edges can become stale independently of nodes (e.g., a function still exists but no longer calls another). |
| `location` | Optional[SourceLocation] | WHERE in the source code is this relationship expressed? Critical for "show me the line where Service A calls Service B". |
| `weight` | float (0.0-1.0) | How important/frequent is this relationship? A function called 1000x/day has higher weight than one called once at startup. Can be set by static analysis (call frequency) or runtime telemetry. |
| `conditional` | bool | Is this edge always active or only under certain conditions? E.g., "CALLS_API to Stripe only when payment_method == 'card'". |

### 6.3 ContextPack Improvements

New fields added to ContextPack:

| Field | Type | Why Needed |
|-------|------|-----------|
| `relevant_edges` | List[GraphEdge] | Currently only returns nodes. Edges carry the operational story -- "Function A WRITES_DB to Collection B". Without edges, the AI agent sees dots without lines. |
| `related_files` | List[SourceLocation] | Quick access to the actual source files involved. The AI agent needs to read code, not just see graph structure. |
| `code_snippets` | Dict[str, str] | Pre-fetched relevant code (keyed by node_id). Saves the AI agent a round-trip to read files. |
| `invariants` | List[str] | Business rules that must be preserved. E.g., "Order total must never be negative", "User email must be unique". Extracted from code comments, tests, and validation logic. |
| `patterns` | List[str] | Detected architectural patterns. E.g., "CQRS pattern detected: separate read/write models", "Saga pattern: multi-step order fulfillment". |
| `stale_warnings` | List[str] | Warnings about stale nodes/edges in the context. E.g., "WARNING: Collection 'orders' was last indexed 30 days ago". |
| `depth` | int | How many hops upstream/downstream were traversed. Lets the consumer know the context radius. |
| `total_nodes_in_scope` | int | How many nodes exist in this scope (even if not all returned). Signals complexity. |

---

## 7. The Tags vs. Types Decision Framework

To prevent future ontology bloat, here is the decision framework:

```
SHOULD THIS CONCEPT BE A FIRST-CLASS NODE TYPE?

  1. Does it have UNIQUE EDGE SEMANTICS?
     (Participates in relationships that no other type does)
     NO  --> Make it a TAG on the closest existing type
     YES --> Continue to question 2

  2. Will there be MORE THAN ~10 instances in a typical workspace?
     NO  --> Consider: is it really worth a type for 3 instances?
     YES --> Continue to question 3

  3. Does an AI agent need to QUERY for all instances of this type?
     (e.g., "show me all middleware" is useful; "show me all utilities" is vague)
     NO  --> Make it a TAG
     YES --> Make it a TYPE

  4. When in doubt: START AS A TAG, PROMOTE LATER.
     It is much easier to promote a tag to a type (additive change)
     than to demote a type to a tag (breaking change).
```

### Examples Applied:

| Concept | Q1: Unique Edges? | Q2: >10 instances? | Q3: Queryable? | Decision |
|---------|-------------------|---------------------|----------------|----------|
| Middleware | YES (INTERCEPTS) | YES (auth, logging, cors, rate-limit...) | YES ("what middleware protects /admin?") | TYPE |
| Queue | YES (ENQUEUES/DEQUEUES) | YES (email, payments, notifications...) | YES ("what queues does OrderService use?") | TYPE |
| Test | NO (TESTS edge can come from Function) | YES (hundreds) | Partially ("show test coverage" -- but that is a graph query, not a type filter) | TAG |
| Script | NO (same edges as Function) | Maybe (53 in Thorbit) | Weakly ("list all scripts" -- but they are just entry-point functions) | TAG |
| Migration | NO (MIGRATES edge can come from Function) | YES (dozens) | YES-ish, but temporal ordering is metadata | TAG |

---

## 8. Multi-Language Support Strategy

The ontology is deliberately language-agnostic. Here is how language-specific
concepts map to our types:

| Language Concept | Our Type | Stored In |
|-----------------|----------|-----------|
| Express Router (JS) | ROUTER | `metadata.framework: "express"` |
| Flask Blueprint (Python) | ROUTER | `metadata.framework: "flask"` |
| Spring Controller (Java) | ROUTER | `metadata.framework: "spring"` |
| Mongoose Model (JS) | DATA_MODEL | `metadata.orm: "mongoose"` |
| SQLAlchemy Model (Python) | DATA_MODEL | `metadata.orm: "sqlalchemy"` |
| JPA Entity (Java) | DATA_MODEL | `metadata.orm: "jpa"` |
| TypeScript Interface | TYPE_DEF | `metadata.kind: "interface"` |
| Python Protocol | TYPE_DEF | `metadata.kind: "protocol"` |
| Java Interface | TYPE_DEF | `metadata.kind: "interface"` |
| Python Celery Task | ASYNC_HANDLER | `tags: ["celery"], metadata.trigger: "queue"` |
| Node.js Bull Job | ASYNC_HANDLER | `tags: ["bull"], metadata.trigger: "queue"` |
| Java @Scheduled | ASYNC_HANDLER | `tags: ["spring-scheduled"], metadata.trigger: "cron"` |
| .env variable | ENV_VAR | `metadata.source: ".env"` |
| Kubernetes ConfigMap key | ENV_VAR | `metadata.source: "k8s-configmap"` |

---

## 9. Comparison: Current vs. Proposed

| Dimension | Current | Proposed | Delta |
|-----------|---------|----------|-------|
| Node types | 14 | 20 | +6 (strategic additions only) |
| Edge types | 15 | 27 | +12 (operational richness) |
| GraphNode fields | 8 | 15 | +7 (provenance, hash, language, tags, parent, tier, version) |
| GraphEdge fields | 5 | 10 | +5 (provenance, stale, location, weight, conditional) |
| ContextPack fields | 5 | 13 | +8 (edges, files, snippets, invariants, patterns, warnings, depth, total) |
| Tag support | No | Yes | Prevents future type explosion |
| Multi-language | Implicit | Explicit | Language field + framework metadata |
| Staleness tracking | Node only | Node + Edge | Edges go stale independently |

---

## 10. Edge Frequency Predictions

Based on the Thorbit analysis and typical microservice architectures:

| Edge Type | Expected Frequency | Priority |
|-----------|-------------------|----------|
| CONTAINS | Very High (every node has a parent) | P0 - Core |
| DEFINES | High (every code entity is in a file) | P0 - Core |
| IMPORTS | High (most files import others) | P0 - Core |
| CALLS | High (function call graph) | P0 - Core |
| READS_DB | Medium-High | P0 - Core |
| WRITES_DB | Medium | P0 - Core |
| ROUTES_TO | Medium (one per endpoint) | P1 - Important |
| INTERCEPTS | Medium (middleware applied broadly) | P1 - Important |
| EMITS_EVENT | Medium | P1 - Important |
| CONSUMES_EVENT | Medium | P1 - Important |
| CALLS_API | Medium | P1 - Important |
| DEPENDS_ON | Low-Medium | P1 - Important |
| CONFIGURES | Medium | P1 - Important |
| ENQUEUES | Low-Medium | P2 - Nice to have |
| DEQUEUES | Low-Medium | P2 - Nice to have |
| TESTS | Low-Medium | P2 - Nice to have |
| VALIDATES | Low | P2 - Nice to have |
| AUTHENTICATES | Low | P2 - Nice to have |
| IMPLEMENTS | Low | P2 - Nice to have |
| INHERITS | Low | P2 - Nice to have |

---

## 11. Sources

- [Joern Code Property Graph Specification](https://cpg.joern.io/)
- [ShiftLeft Code Property Graph](https://github.com/ShiftLeftSecurity/codepropertygraph)
- [LSIF Specification (Microsoft)](https://microsoft.github.io/language-server-protocol/specifications/lsif/0.6.0/specification/)
- [SCIP Code Intelligence Protocol (Sourcegraph)](https://github.com/sourcegraph/scip)
- [Ontology -- The Queryable Brain of Software Archaeology](https://bennycheung.github.io/ontology-the-queryable-brain-of-software-archaeology)
- [GraphGen4Code (IBM WALA)](https://wala.github.io/graph4code/)
- [Neo4j Codebase Knowledge Graph](https://neo4j.com/blog/developer/codebase-knowledge-graph/)
- [Software Architectures and Knowledge Graphs (Springer)](https://link.springer.com/chapter/10.1007/978-3-031-71333-0_12)
- [code-graph-rag (GitHub)](https://github.com/vitali87/code-graph-rag)
- [Ontology in Graph Models (graph.build)](https://graph.build/resources/ontology)
