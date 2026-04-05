"""
LLM Prompts and Tool Schemas for Code Analysis
==============================================

This module provides:
  1. Tool schemas (Anthropic tool_use format) for structured LLM outputs
  2. Prompt builders for file classification and edge discovery
  3. Anti-hallucination rules and guidelines

Used in Pass 3 of the analysis pipeline to discover operational edges
and architectural entities that static analysis cannot detect.
"""

from typing import List, Dict, Any


# =============================================================================
# TOOL SCHEMAS (Anthropic tool_use format)
# =============================================================================

FILE_CLASSIFICATION_TOOL: Dict[str, Any] = {
    "name": "classify_file",
    "description": "Classify a source code file's role and characteristics",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_role": {
                "type": "string",
                "enum": [
                    "service",
                    "controller",
                    "model",
                    "utility",
                    "test",
                    "config",
                    "migration",
                    "script",
                    "middleware",
                    "router",
                    "type_definition"
                ],
                "description": "Primary role of this file in the application architecture"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Classification tags such as: test, script, utility, migration, "
                    "deprecated, critical-path, entry-point, unit-test, integration-test, "
                    "e2e-test, experimental, stable, hot-path, public-api, auth-required, "
                    "rate-limited, sensitive-data"
                )
            },
            "frameworks_detected": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Frameworks or libraries detected in this file (e.g., express, "
                    "flask, fastapi, react, vue, prisma, mongoose, sqlalchemy)"
                )
            },
            "primary_responsibility": {
                "type": "string",
                "description": (
                    "A one-sentence description of what this file is responsible for. "
                    "Focus on business logic and architectural purpose."
                )
            },
            "complexity": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "Subjective assessment of code complexity. Consider: cyclomatic complexity, "
                    "number of dependencies, business logic density, error handling complexity."
                )
            }
        },
        "required": ["file_role", "tags", "primary_responsibility"]
    }
}


DISCOVER_EDGES_TOOL: Dict[str, Any] = {
    "name": "discover_edges",
    "description": "Discover operational relationships (edges) in source code that static analysis cannot detect",
    "input_schema": {
        "type": "object",
        "properties": {
            "edges": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_name": {
                            "type": "string",
                            "description": (
                                "Name of the source entity (function, endpoint, etc.). "
                                "MUST be an exact name from the code, not invented."
                            )
                        },
                        "target_name": {
                            "type": "string",
                            "description": (
                                "Name of the target entity. MUST match a node from the "
                                "known_nodes list provided in the prompt, or be an exact "
                                "name visible in the code. Never invent target names."
                            )
                        },
                        "edge_type": {
                            "type": "string",
                            "enum": [
                                "CONTAINS",
                                "DEFINES",
                                "IMPORTS",
                                "IMPLEMENTS",
                                "INHERITS",
                                "READS_DB",
                                "WRITES_DB",
                                "MIGRATES",
                                "CALLS_API",
                                "CALLS_SERVICE",
                                "CALLS",
                                "WEBHOOK_SEND",
                                "WEBHOOK_RECEIVE",
                                "EMITS_EVENT",
                                "CONSUMES_EVENT",
                                "ENQUEUES",
                                "DEQUEUES",
                                "SCHEDULES",
                                "CACHE_READ",
                                "CACHE_WRITE",
                                "ROUTES_TO",
                                "INTERCEPTS",
                                "VALIDATES",
                                "AUTHENTICATES",
                                "DEPENDS_ON",
                                "DEPLOYED_BY",
                                "CONFIGURES",
                                "TESTS"
                            ],
                            "description": (
                                "Type of relationship. Choose the MOST SPECIFIC type that applies. "
                                "Prefer operational edges (READS_DB, CALLS_API, EMITS_EVENT) over "
                                "generic ones (CALLS). Use CALLS only for direct function invocations "
                                "you can see in the code."
                            )
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "Explain WHY this edge exists and WHAT it does at runtime. "
                                "Examples: 'writes user signup data to users table', "
                                "'calls Stripe API to process payment', 'emits ORDER_CREATED event "
                                "after successful order placement'."
                            )
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "description": (
                                "Confidence in this relationship (0.0-1.0). Use 1.0 for edges you "
                                "can see directly in the code. Use 0.5-0.8 for inferred edges. "
                                "Use <0.5 if uncertain about the target or edge type."
                            )
                        },
                        "conditional": {
                            "type": "boolean",
                            "description": (
                                "Is this edge conditional? (e.g., only happens in certain code paths, "
                                "based on if statements, feature flags, environment variables)"
                            )
                        },
                        "line_number": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "Line number where this relationship is expressed in the source code. "
                                "Omit if the edge spans multiple lines or is not traceable to a single line."
                            )
                        }
                    },
                    "required": ["source_name", "target_name", "edge_type", "description", "confidence"]
                },
                "description": (
                    "List of operational edges discovered in this file. Only include edges "
                    "that represent runtime behavior, not static structure (IMPORTS, DEFINES "
                    "are already handled by Pass 1)."
                )
            }
        },
        "required": ["edges"]
    }
}


DISCOVER_NODES_TOOL: Dict[str, Any] = {
    "name": "discover_nodes",
    "description": (
        "Discover architectural entities (nodes) that static analysis missed. "
        "Focus on high-level concepts like Services, Resources, ExternalAPIs, Events, Queues."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Name of the entity. Use exact names from the code or "
                                "well-known names (e.g., 'PostgreSQL', 'Stripe', 'ORDER_CREATED'). "
                                "Never invent names."
                            )
                        },
                        "node_type": {
                            "type": "string",
                            "enum": [
                                "Workspace",
                                "Project",
                                "Service",
                                "Resource",
                                "ExternalAPI",
                                "Module",
                                "File",
                                "Router",
                                "Collection",
                                "InfraConfig",
                                "Queue",
                                "Endpoint",
                                "Function",
                                "AsyncHandler",
                                "DataModel",
                                "Event",
                                "Middleware",
                                "TypeDef",
                                "CacheKey",
                                "EnvVar"
                            ],
                            "description": (
                                "Type of entity. Choose the most accurate type. "
                                "Note: File, Function, DataModel, TypeDef are already discovered by "
                                "Pass 1. Focus on: Service, Resource, ExternalAPI, Event, Queue, "
                                "CacheKey, EnvVar, Router, Collection, AsyncHandler."
                            )
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "What is this entity and what role does it play in the architecture? "
                                "Be specific about its purpose."
                            )
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Tags for classification (e.g., critical-path, deprecated, "
                                "experimental, third-party, internal)"
                            )
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "description": (
                                "Confidence in this node's classification (0.0-1.0). "
                                "Use 1.0 for entities with clear evidence in the code. "
                                "Use 0.5-0.8 for inferred entities. Use <0.5 if uncertain."
                            )
                        },
                        "line_number": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "Line number where this entity is defined or referenced. "
                                "Omit if not applicable."
                            )
                        }
                    },
                    "required": ["name", "node_type", "description", "confidence"]
                },
                "description": (
                    "List of nodes discovered. Only include entities that Pass 1 and Pass 2 "
                    "could not detect."
                )
            }
        },
        "required": ["nodes"]
    }
}


# =============================================================================
# SYSTEM PROMPT BUILDER
# =============================================================================

def build_system_prompt(known_nodes: List[str]) -> str:
    """
    Build the system prompt that explains the ontology and anti-hallucination rules.

    Args:
        known_nodes: List of node names that already exist in the graph.
                     The LLM can reference these when creating edges.

    Returns:
        System prompt string
    """
    known_nodes_section = ""
    if known_nodes:
        # Limit to first 200 nodes to avoid context overflow
        displayed_nodes = known_nodes[:200]
        truncated = len(known_nodes) > 200
        known_nodes_section = f"""
## Known Nodes in the Graph

The following nodes already exist in the workspace graph. When creating edges,
you MUST reference these exact names as targets (do not invent new names).

{chr(10).join(f"  - {node}" for node in displayed_nodes)}
{"  ... and " + str(len(known_nodes) - 200) + " more nodes" if truncated else ""}
"""

    return f"""You are a code analysis expert helping build a semantic knowledge graph of a codebase.

## Your Task

Analyze source code files to discover:
  1. **Operational Edges**: Runtime relationships that static analysis cannot detect
     - Database reads/writes (READS_DB, WRITES_DB)
     - API calls (CALLS_API to external services)
     - Function calls (CALLS between functions)
     - Event emissions/consumption (EMITS_EVENT, CONSUMES_EVENT)
     - Queue operations (ENQUEUES, DEQUEUES)
     - Cache operations (CACHE_READ, CACHE_WRITE)
     - Service-to-service calls (CALLS_SERVICE)
     - Webhooks (WEBHOOK_SEND, WEBHOOK_RECEIVE)

  2. **Architectural Nodes**: High-level entities that static parsers miss
     - External services (ExternalAPI: Stripe, Twilio, Auth0, AWS S3)
     - Infrastructure resources (Resource: PostgreSQL, Redis, S3 buckets)
     - Domain events (Event: ORDER_CREATED, USER_SIGNED_UP)
     - Message queues (Queue: order-processing, email-notifications)
     - Cache keys (CacheKey: user:{{id}}:profile, session:{{token}})
     - Environment variables (EnvVar: DATABASE_URL, STRIPE_API_KEY)

## The Ontology

### Node Types (20 total)
Organized into three tiers:

**MACRO (Architecture Level)**:
  - Workspace: Root container
  - Project: Repository / deployable package
  - Service: Running deployable unit (API, Worker, SPA)
  - Resource: Infrastructure dependency (PostgreSQL, Redis, S3)
  - ExternalAPI: Third-party service (Stripe, Twilio, Auth0)

**MESO (Component Level)**:
  - Module: Logical grouping (folder/package)
  - File: Physical source file
  - Router: Route group with shared prefix/middleware
  - Collection: Database table/collection (physical storage)
  - InfraConfig: Deployment config (Docker, k8s, terraform, .env)
  - Queue: Message queue/topic/channel

**MICRO (Code Element Level)**:
  - Endpoint: HTTP/gRPC/GraphQL handler
  - Function: Business logic function
  - AsyncHandler: Event consumer/background job/cron task
  - DataModel: ORM entity/schema definition
  - Event: Named domain event (ORDER_CREATED)
  - Middleware: Request/response interceptor
  - TypeDef: Interface/type alias/enum/DTO
  - CacheKey: Named cache pattern (user:{{id}}:profile)
  - EnvVar: Environment variable/config key

### Edge Types (28 total)
Organized by semantic purpose:

**Structural** (handled by Pass 1 - you don't need to create these):
  - CONTAINS, DEFINES, IMPORTS, IMPLEMENTS, INHERITS

**Data Flow** (you should discover these):
  - READS_DB: Function/Endpoint reads from Collection
  - WRITES_DB: Function/Endpoint writes to Collection
  - MIGRATES: Function migrates Collection schema

**Communication** (you should discover these):
  - CALLS_API: Function/Service calls ExternalAPI
  - CALLS_SERVICE: Service calls another Service
  - CALLS: Function directly invokes another Function (visible in code)
  - WEBHOOK_SEND: Service sends webhook to ExternalAPI
  - WEBHOOK_RECEIVE: Endpoint receives webhook from ExternalAPI

**Event/Async** (you should discover these):
  - EMITS_EVENT: Function/Service publishes Event
  - CONSUMES_EVENT: AsyncHandler consumes Event
  - ENQUEUES: Function adds message to Queue
  - DEQUEUES: AsyncHandler reads from Queue
  - SCHEDULES: InfraConfig/Function schedules AsyncHandler

**Caching** (you should discover these):
  - CACHE_READ: Function/Endpoint reads from CacheKey
  - CACHE_WRITE: Function/Endpoint writes to CacheKey

**Routing** (partially handled by Pass 2, you can augment):
  - ROUTES_TO: Router routes to Endpoint
  - INTERCEPTS: Middleware intercepts Endpoint/Router
  - VALIDATES: Middleware/Function validates DataModel/TypeDef
  - AUTHENTICATES: Middleware authenticates Endpoint/Router

**Config/Deploy** (you should discover these):
  - DEPENDS_ON: Service depends on Resource/Queue/ExternalAPI
  - DEPLOYED_BY: Service deployed by InfraConfig
  - CONFIGURES: EnvVar configures Service/Resource/Function

**Quality** (you should discover these):
  - TESTS: Function tests another Function/Endpoint/DataModel

{known_nodes_section}

## Anti-Hallucination Rules (CRITICAL)

1. **Only create edges to nodes that exist**:
   - Check the "Known Nodes" list above before creating an edge
   - If the target is not in the list, only create the edge if you can see it clearly in the code
   - Never invent node names based on assumptions

2. **Set confidence accurately**:
   - confidence=1.0: You can see the exact code that creates this relationship
   - confidence=0.7-0.9: Strong evidence but some inference required
   - confidence=0.5-0.7: Moderate evidence, some uncertainty
   - confidence<0.5: Weak evidence, highly uncertain (avoid these)

3. **Never invent node names**:
   - Use exact names from the code (function names, variable names, string literals)
   - For external APIs: use well-known names (Stripe, Twilio, OpenAI, AWS S3)
   - For database tables: use exact table/collection names from the code
   - For events: use exact event name constants or strings

4. **Prefer specific edge types over generic ones**:
   - Use CALLS_API instead of CALLS for external service calls
   - Use READS_DB/WRITES_DB instead of CALLS for database operations
   - Use CALLS only for direct function invocations you can see in the code
   - Use EMITS_EVENT/CONSUMES_EVENT for event-driven patterns

5. **Mark conditional edges appropriately**:
   - Set conditional=true if the edge only happens in certain code paths
   - Examples: feature flags, if statements, environment-based logic

6. **Focus on operational edges, not structural ones**:
   - IMPORTS and DEFINES are already handled by Pass 1
   - Focus on runtime behavior: API calls, database operations, events, queues

7. **Provide line numbers when possible**:
   - Include line_number for edges that point to specific code locations
   - This helps developers trace relationships back to source

## Examples of Good Analysis

### Example 1: Database Operation
```python
async def create_user(user_data: dict):
    # Line 42: writes to users table
    await db.users.insert_one(user_data)
```

Good edge:
{{
  "source_name": "create_user",
  "target_name": "users",
  "edge_type": "WRITES_DB",
  "description": "inserts new user document into users collection",
  "confidence": 1.0,
  "conditional": false,
  "line_number": 42
}}

### Example 2: External API Call
```typescript
async function processPayment(amount: number, token: string) {{
  // Line 78: calls Stripe API
  const charge = await stripe.charges.create({{
    amount,
    currency: 'usd',
    source: token,
  }});
  return charge;
}}
```

Good edges:
1. Edge to ExternalAPI:
{{
  "source_name": "processPayment",
  "target_name": "Stripe",
  "edge_type": "CALLS_API",
  "description": "creates charge via Stripe API for payment processing",
  "confidence": 1.0,
  "conditional": false,
  "line_number": 78
}}

2. Node for ExternalAPI (if not already in known_nodes):
{{
  "name": "Stripe",
  "node_type": "ExternalAPI",
  "description": "Payment processing service for handling credit card transactions",
  "tags": ["third-party", "critical-path"],
  "confidence": 1.0
}}

### Example 3: Event Emission
```javascript
function completeOrder(orderId) {{
  // Line 156: emit domain event
  eventBus.publish('ORDER_COMPLETED', {{ orderId, timestamp: Date.now() }});
  // Line 158: enqueue notification job
  queue.add('email-notifications', {{ orderId, type: 'order_confirmation' }});
}}
```

Good edges:
1. Event emission:
{{
  "source_name": "completeOrder",
  "target_name": "ORDER_COMPLETED",
  "edge_type": "EMITS_EVENT",
  "description": "publishes ORDER_COMPLETED event when order is finalized",
  "confidence": 1.0,
  "conditional": false,
  "line_number": 156
}}

2. Queue operation:
{{
  "source_name": "completeOrder",
  "target_name": "email-notifications",
  "edge_type": "ENQUEUES",
  "description": "enqueues order confirmation email job",
  "confidence": 1.0,
  "conditional": false,
  "line_number": 158
}}

Good nodes (if not already discovered):
1. Event:
{{
  "name": "ORDER_COMPLETED",
  "node_type": "Event",
  "description": "Domain event emitted when an order is successfully completed",
  "tags": ["domain-event"],
  "confidence": 1.0,
  "line_number": 156
}}

2. Queue:
{{
  "name": "email-notifications",
  "node_type": "Queue",
  "description": "Queue for asynchronous email notification jobs",
  "tags": ["async", "notifications"],
  "confidence": 1.0,
  "line_number": 158
}}

## Your Approach

1. Read the code carefully, line by line
2. Identify operational patterns: database calls, API calls, events, queues, cache operations
3. For each pattern, determine:
   - What is the source entity? (function, endpoint, etc.)
   - What is the target entity? (table, API, event, queue, etc.)
   - What type of edge represents this relationship?
   - Is the target in the known_nodes list?
   - What is the confidence level?
   - Is this edge conditional?
4. Create edges and nodes using the tool schemas
5. Always include descriptive explanations and line numbers when possible

Remember: Quality over quantity. It's better to have 5 high-confidence edges with good
descriptions than 20 low-confidence edges with vague descriptions.
"""


# =============================================================================
# PROMPT BUILDERS
# =============================================================================

def build_classify_prompt(file_content: str, file_path: str) -> List[Dict[str, Any]]:
    """
    Build prompt messages for file classification.

    Args:
        file_content: Content of the file to classify
        file_path: Path to the file being classified

    Returns:
        List of message dicts in Anthropic format [{"role": "user", "content": "..."}]
    """
    return [
        {
            "role": "user",
            "content": f"""Classify this source code file.

File path: {file_path}

```
{file_content}
```

Analyze the file and classify its role, tags, frameworks, primary responsibility, and complexity.
Use the classify_file tool to provide your analysis.
"""
        }
    ]


def build_edge_discovery_prompt(
    file_content: str,
    file_path: str,
    known_nodes: List[str]
) -> List[Dict[str, Any]]:
    """
    Build prompt messages for edge discovery.

    Args:
        file_content: Content of the file to analyze
        file_path: Path to the file being analyzed
        known_nodes: List of node names that already exist in the graph

    Returns:
        List of message dicts in Anthropic format
    """
    # Build a condensed view of known nodes for the prompt
    known_nodes_text = ""
    if known_nodes:
        # Group nodes by type prefix if possible (helps LLM understand structure)
        displayed = known_nodes[:100]  # Limit to avoid context bloat
        truncated = len(known_nodes) > 100
        known_nodes_text = f"""
## Known Nodes (you can create edges to these)
{chr(10).join(f"  - {node}" for node in displayed)}
{"  ... and " + str(len(known_nodes) - 100) + " more" if truncated else ""}
"""

    return [
        {
            "role": "user",
            "content": f"""Discover operational edges and architectural nodes in this source code file.

File path: {file_path}
{known_nodes_text}

```
{file_content}
```

Analyze the code to find:
  1. **Operational Edges**: Runtime relationships like database operations, API calls,
     function calls, event emissions, queue operations, cache operations
  2. **Architectural Nodes**: High-level entities like external APIs, infrastructure
     resources, domain events, queues, cache keys, environment variables

Guidelines:
  - Only create edges to nodes that exist in the "Known Nodes" list or are clearly
    visible in the code
  - Set confidence accurately (1.0 for direct evidence, lower for inference)
  - Prefer specific edge types (READS_DB, CALLS_API) over generic ones (CALLS)
  - Include line numbers when possible
  - Mark conditional edges appropriately
  - Provide clear descriptions explaining WHY each edge exists

Use the discover_edges and discover_nodes tools to report your findings.
If you don't find any operational edges or nodes, return empty arrays.
"""
        }
    ]


def build_node_discovery_prompt(
    file_content: str,
    file_path: str
) -> List[Dict[str, Any]]:
    """
    Build prompt messages for node discovery (focused on high-level architecture).

    Args:
        file_content: Content of the file to analyze
        file_path: Path to the file being analyzed

    Returns:
        List of message dicts in Anthropic format
    """
    return [
        {
            "role": "user",
            "content": f"""Discover high-level architectural entities in this source code file.

File path: {file_path}

```
{file_content}
```

Focus on discovering:
  - **External APIs**: Third-party services (Stripe, Twilio, Auth0, OpenAI, AWS services)
  - **Resources**: Infrastructure dependencies (PostgreSQL, Redis, MongoDB, S3, etc.)
  - **Events**: Domain events (ORDER_CREATED, USER_SIGNED_UP, PAYMENT_PROCESSED)
  - **Queues**: Message queues/topics (order-processing, email-notifications)
  - **Cache Keys**: Named cache patterns (user:{{id}}:profile, session:{{token}})
  - **Environment Variables**: Config keys (DATABASE_URL, API_KEY, STRIPE_SECRET)

Do NOT include:
  - Functions, Classes, TypeDefs (already discovered by Pass 1)
  - Low-level implementation details

Use the discover_nodes tool to report architectural entities you find.
If you don't find any, return an empty array.
"""
        }
    ]
