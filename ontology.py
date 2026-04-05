"""
Workspace Intelligence Layer - Ontology v2
===========================================

Defines the semantic graph schema for representing codebases as a knowledge graph.

Design principles:
  - Three-tier hierarchy (Macro / Meso / Micro) for scoping queries
  - Tags over types: only concepts with unique edge semantics become first-class types
  - Operational edges tell the runtime "story", not just static structure
  - Provenance tracking on every node and edge
  - Multi-language by design (language is a field, not baked into types)

Changes from v1:
  - 20 node types (was 14): +ROUTER, +COLLECTION, +QUEUE, +MIDDLEWARE, +TYPE_DEF, +ENV_VAR
  - 27 edge types (was 15): +IMPLEMENTS, +INHERITS, +MIGRATES, +CALLS, +ENQUEUES,
    +DEQUEUES, +SCHEDULES, +ROUTES_TO, +INTERCEPTS, +VALIDATES, +AUTHENTICATES,
    +CONFIGURES, +TESTS
  - GraphNode: +provenance, +source_hash, +language, +tags, +parent_id, +version
  - GraphEdge: +provenance, +is_stale, +location, +weight, +conditional
  - ContextPack: +relevant_edges, +related_files, +code_snippets, +invariants,
    +patterns, +stale_warnings, +depth, +total_nodes_in_scope

See ONTOLOGY_DESIGN.md for full rationale.
"""

from enum import Enum
from typing import Optional, Dict, Any, List, Set
from pydantic import BaseModel, Field, computed_field
from datetime import datetime, timezone


# =============================================================================
# TIER CLASSIFICATION
# =============================================================================

class Tier(str, Enum):
    """
    Abstraction tier for node types.

    Controls query scoping, context window sizing, and visualization granularity.
    - MACRO: Architecture diagram level (architect's view)
    - MESO:  Module/component level (tech lead's view)
    - MICRO: Code element level (developer's view)
    """
    MACRO = "macro"
    MESO = "meso"
    MICRO = "micro"


# =============================================================================
# NODE TYPES
# =============================================================================

class NodeType(str, Enum):
    """
    Classification of nodes in the workspace graph.

    Organized into three tiers. Each type exists because it has unique edge
    semantics -- if a concept can be expressed as a tag on an existing type,
    it should be a tag, not a new type.

    Types that were considered but kept as TAGS instead:
      - TEST     -> Function with tags=["test"]
      - SCRIPT   -> Function with tags=["script"]
      - UTILITY  -> Function with tags=["utility"]
      - MIGRATION -> Function with tags=["migration"] + MIGRATES edge
    """

    # --- Tier 1: Architecture Level (Macro) ---
    WORKSPACE = "Workspace"        # Root container for the dev environment
    PROJECT = "Project"            # A repository / deployable package
    SERVICE = "Service"            # Running deployable unit (API, Worker, SPA)
    RESOURCE = "Resource"          # Infrastructure dependency (DB, Redis, S3)
    EXTERNAL_API = "ExternalAPI"   # Third-party service (Stripe, Twilio, Auth0)

    # --- Tier 2: Component Level (Meso) ---
    MODULE = "Module"              # Logical grouping (folder / package / namespace)
    FILE = "File"                  # Physical source file
    ROUTER = "Router"              # Route group with shared prefix/middleware
    COLLECTION = "Collection"      # Database table / collection (physical storage)
    INFRA_CONFIG = "InfraConfig"   # Deployment config (Docker, k8s, terraform, .env)
    QUEUE = "Queue"                # Message queue / topic / channel

    # --- Tier 3: Code Element Level (Micro) ---
    ENDPOINT = "Endpoint"          # HTTP / gRPC / GraphQL handler
    FUNCTION = "Function"          # Architecturally significant business logic
    ASYNC_HANDLER = "AsyncHandler"  # Event consumer / background job / cron task
    DATA_MODEL = "DataModel"       # ORM entity / schema definition
    EVENT = "Event"                # Named domain event (ORDER_CREATED)
    MIDDLEWARE = "Middleware"       # Request/response interceptor
    TYPE_DEF = "TypeDef"           # Interface / type alias / enum / DTO
    CACHE_KEY = "CacheKey"         # Named cache pattern (user:{id}:profile)
    ENV_VAR = "EnvVar"             # Environment variable / config key


# Tier lookup: constant-time tier resolution for any node type.
NODE_TIER: Dict[NodeType, Tier] = {
    # Macro
    NodeType.WORKSPACE: Tier.MACRO,
    NodeType.PROJECT: Tier.MACRO,
    NodeType.SERVICE: Tier.MACRO,
    NodeType.RESOURCE: Tier.MACRO,
    NodeType.EXTERNAL_API: Tier.MACRO,
    # Meso
    NodeType.MODULE: Tier.MESO,
    NodeType.FILE: Tier.MESO,
    NodeType.ROUTER: Tier.MESO,
    NodeType.COLLECTION: Tier.MESO,
    NodeType.INFRA_CONFIG: Tier.MESO,
    NodeType.QUEUE: Tier.MESO,
    # Micro
    NodeType.ENDPOINT: Tier.MICRO,
    NodeType.FUNCTION: Tier.MICRO,
    NodeType.ASYNC_HANDLER: Tier.MICRO,
    NodeType.DATA_MODEL: Tier.MICRO,
    NodeType.EVENT: Tier.MICRO,
    NodeType.MIDDLEWARE: Tier.MICRO,
    NodeType.TYPE_DEF: Tier.MICRO,
    NodeType.CACHE_KEY: Tier.MICRO,
    NodeType.ENV_VAR: Tier.MICRO,
}


# =============================================================================
# EDGE TYPES
# =============================================================================

class EdgeType(str, Enum):
    """
    Classification of relationships between nodes.

    Organized into semantic groups:
      - Structural:     Graph skeleton (containment, definition, imports)
      - Data Flow:      Database reads/writes, migrations
      - Communication:  Inter-service/function calls, webhooks
      - Event/Async:    Event publishing/consuming, queue operations, scheduling
      - Caching:        Cache reads/writes
      - Routing:        URL routing, middleware interception, auth, validation
      - Config/Deploy:  Infrastructure dependencies, deployment, env config
      - Quality:        Test coverage
    """

    # --- Structural (Graph Skeleton) ---
    CONTAINS = "CONTAINS"          # Workspace->Project, Project->Service, Module->Function
    DEFINES = "DEFINES"            # File -> DataModel/TypeDef/Endpoint/Function
    IMPORTS = "IMPORTS"            # File -> File, Module -> Module
    IMPLEMENTS = "IMPLEMENTS"      # DataModel/Function -> TypeDef (satisfies interface)
    INHERITS = "INHERITS"          # DataModel -> DataModel, TypeDef -> TypeDef

    # --- Data Flow (The Story) ---
    READS_DB = "READS_DB"          # Function/Endpoint -> Collection
    WRITES_DB = "WRITES_DB"        # Function/Endpoint -> Collection
    MIGRATES = "MIGRATES"          # Function -> Collection (schema migration)

    # --- Communication (Inter-Component) ---
    CALLS_API = "CALLS_API"        # Function/Service -> ExternalAPI
    CALLS_SERVICE = "CALLS_SERVICE"  # Service -> Service (inter-service)
    CALLS = "CALLS"                # Function -> Function (direct invocation)
    WEBHOOK_SEND = "WEBHOOK_SEND"      # Service -> ExternalAPI
    WEBHOOK_RECEIVE = "WEBHOOK_RECEIVE"  # Endpoint -> ExternalAPI

    # --- Event / Async ---
    EMITS_EVENT = "EMITS_EVENT"        # Function/Service -> Event
    CONSUMES_EVENT = "CONSUMES_EVENT"  # AsyncHandler -> Event
    ENQUEUES = "ENQUEUES"              # Function -> Queue
    DEQUEUES = "DEQUEUES"              # AsyncHandler -> Queue
    SCHEDULES = "SCHEDULES"            # InfraConfig/Function -> AsyncHandler

    # --- Caching ---
    CACHE_READ = "CACHE_READ"      # Function/Endpoint -> CacheKey
    CACHE_WRITE = "CACHE_WRITE"    # Function/Endpoint -> CacheKey

    # --- Routing & Middleware ---
    ROUTES_TO = "ROUTES_TO"        # Router -> Endpoint
    INTERCEPTS = "INTERCEPTS"      # Middleware -> Endpoint/Router
    VALIDATES = "VALIDATES"        # Middleware/Function -> DataModel/TypeDef
    AUTHENTICATES = "AUTHENTICATES"  # Middleware -> Endpoint/Router

    # --- Configuration & Deployment ---
    DEPENDS_ON = "DEPENDS_ON"      # Service -> Resource/Queue
    DEPLOYED_BY = "DEPLOYED_BY"    # Service -> InfraConfig
    CONFIGURES = "CONFIGURES"      # EnvVar -> Service/Resource/Function

    # --- Quality & Testing ---
    TESTS = "TESTS"                # Function -> Function/Endpoint/DataModel


# Edge semantic groups for filtering and display
EDGE_GROUPS: Dict[str, List[EdgeType]] = {
    "structural": [
        EdgeType.CONTAINS, EdgeType.DEFINES, EdgeType.IMPORTS,
        EdgeType.IMPLEMENTS, EdgeType.INHERITS,
    ],
    "data_flow": [
        EdgeType.READS_DB, EdgeType.WRITES_DB, EdgeType.MIGRATES,
    ],
    "communication": [
        EdgeType.CALLS_API, EdgeType.CALLS_SERVICE, EdgeType.CALLS,
        EdgeType.WEBHOOK_SEND, EdgeType.WEBHOOK_RECEIVE,
    ],
    "event_async": [
        EdgeType.EMITS_EVENT, EdgeType.CONSUMES_EVENT,
        EdgeType.ENQUEUES, EdgeType.DEQUEUES, EdgeType.SCHEDULES,
    ],
    "caching": [
        EdgeType.CACHE_READ, EdgeType.CACHE_WRITE,
    ],
    "routing": [
        EdgeType.ROUTES_TO, EdgeType.INTERCEPTS,
        EdgeType.VALIDATES, EdgeType.AUTHENTICATES,
    ],
    "config_deploy": [
        EdgeType.DEPENDS_ON, EdgeType.DEPLOYED_BY, EdgeType.CONFIGURES,
    ],
    "quality": [
        EdgeType.TESTS,
    ],
}


# =============================================================================
# PROVENANCE
# =============================================================================

class Provenance(str, Enum):
    """
    How a node or edge was discovered.

    Critical for trust calibration:
      - SCANNER:  Found by deterministic parser/heuristic (high trust)
      - LLM:      Inferred by language model (verify before trusting)
      - HUMAN:    Manually annotated (highest trust)
      - IMPORT:   Imported from external tool (trust depends on source)
      - RUNTIME:  Observed from runtime telemetry (high trust, may be partial)
    """
    SCANNER = "scanner"
    LLM = "llm"
    HUMAN = "human"
    IMPORT = "import"
    RUNTIME = "runtime"


# =============================================================================
# SOURCE LOCATION
# =============================================================================

class SourceLocation(BaseModel):
    """Points to a specific location in source code."""
    file_path: str
    start_line: int
    end_line: int
    start_col: Optional[int] = None
    end_col: Optional[int] = None


# =============================================================================
# GRAPH NODE
# =============================================================================

class GraphNode(BaseModel):
    """
    A node in the workspace intelligence graph.

    Represents any semantic entity: Service, Endpoint, DataModel, Event, etc.

    Fields are organized into four groups:
      - Identity:    id, type, name, description
      - Location:    location, parent_id
      - Provenance:  provenance, confidence, is_stale, source_hash, version
      - Metadata:    language, tags, metadata, last_updated
    """

    # --- Identity ---
    id: str = Field(
        ...,
        description=(
            "Unique identifier. Format: '{type}:{namespace}:{name}' "
            "e.g., 'endpoint:user-api:POST:/users'"
        ),
    )
    type: NodeType
    name: str = Field(..., description="Human-readable name")
    description: Optional[str] = Field(
        None,
        description="Agent-generated summary of responsibility/purpose",
    )

    # --- Location ---
    location: Optional[SourceLocation] = None
    parent_id: Optional[str] = Field(
        None,
        description=(
            "ID of the containing node (for fast containment lookups). "
            "A Function's parent is its Module; a Module's parent is its Project."
        ),
    )

    # --- Provenance & Confidence ---
    provenance: Provenance = Field(
        default=Provenance.SCANNER,
        description="How this node was discovered (scanner, llm, human, import, runtime)",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this node's classification (0.0-1.0)",
    )
    is_stale: bool = Field(
        default=False,
        description="Marked for re-indexing after source change",
    )
    source_hash: Optional[str] = Field(
        None,
        description=(
            "Content hash of the source code defining this node. "
            "Used for staleness detection: if source changes, hash mismatches."
        ),
    )
    version: int = Field(
        default=1,
        ge=1,
        description="Monotonically increasing version counter for change tracking",
    )

    # --- Metadata ---
    language: Optional[str] = Field(
        None,
        description="Programming language (e.g., 'typescript', 'python', 'go')",
    )
    tags: List[str] = Field(
        default_factory=list,
        description=(
            "Flexible classification tags. Supports: test, script, utility, "
            "migration, deprecated, critical-path, entry-point, etc."
        ),
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Arbitrary metadata. Common keys: framework, orm, http_method, "
            "http_path, schedule, sensitivity, test_framework, etc."
        ),
    )
    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # --- Computed ---
    @computed_field
    @property
    def tier(self) -> Tier:
        """The abstraction tier this node belongs to, derived from its type."""
        return NODE_TIER[self.type]


# =============================================================================
# GRAPH EDGE
# =============================================================================

class GraphEdge(BaseModel):
    """
    A directed edge in the workspace intelligence graph.

    Represents a relationship between two nodes. Edges carry operational
    semantics -- they tell the story of what happens at runtime.

    Fields are organized into three groups:
      - Identity:    source_id, target_id, type, description
      - Provenance:  provenance, confidence, is_stale, weight, conditional
      - Context:     location, metadata
    """

    # --- Identity ---
    source_id: str
    target_id: str
    type: EdgeType
    description: Optional[str] = Field(
        None,
        description=(
            "Context about the relationship. "
            "e.g., 'writes to users table on signup', "
            "'calls Stripe API for payment processing'"
        ),
    )

    # --- Provenance & Confidence ---
    provenance: Provenance = Field(
        default=Provenance.SCANNER,
        description="How this edge was discovered",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this relationship (0.0-1.0)",
    )
    is_stale: bool = Field(
        default=False,
        description=(
            "Edge-level staleness (independent of node staleness). "
            "E.g., a function still exists but no longer calls another."
        ),
    )
    weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Importance/frequency of this relationship (0.0-1.0). "
            "Can be set by static analysis (call frequency) or runtime telemetry."
        ),
    )
    conditional: bool = Field(
        default=False,
        description=(
            "Is this edge conditional? E.g., 'CALLS_API to Stripe only when "
            "payment_method == card'. Helps AI understand edge activation."
        ),
    )

    # --- Context ---
    location: Optional[SourceLocation] = Field(
        None,
        description=(
            "Where in source code this relationship is expressed. "
            "Critical for 'show me the line where A calls B'."
        ),
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# CONTEXT PACK (Skill API Output)
# =============================================================================

class ContextPack(BaseModel):
    """
    The output of the Skill API.

    Provides architectural context for AI agents working on a specific task.
    This is the primary interface between the graph and consuming AI agents.

    The ContextPack answers: "What does an AI agent need to know to safely
    modify code in this scope?"

    Sections:
      - Scope:      What we are looking at and why
      - Graph:      Nodes and edges in the relevant subgraph
      - Code:       Related files and pre-fetched code snippets
      - Knowledge:  Invariants, patterns, and warnings
      - Metadata:   Traversal depth, total scope size
    """

    # --- Scope ---
    scope: str = Field(
        ...,
        description="The queried scope (e.g., 'Service: OrderService')",
    )
    focus: str = Field(
        ...,
        description="The task focus (e.g., 'Refactoring database schema')",
    )

    # --- Graph ---
    relevant_nodes: List[GraphNode] = Field(default_factory=list)
    relevant_edges: List[GraphEdge] = Field(
        default_factory=list,
        description=(
            "Edges between relevant nodes. Carries the operational story: "
            "'Function A WRITES_DB to Collection B'. Without edges, an AI "
            "agent sees dots without lines."
        ),
    )
    upstream: List[GraphNode] = Field(
        default_factory=list,
        description="Nodes that depend on / call into the scope",
    )
    downstream: List[GraphNode] = Field(
        default_factory=list,
        description="Nodes that the scope calls / triggers",
    )

    # --- Code ---
    related_files: List[SourceLocation] = Field(
        default_factory=list,
        description="Source files involved in this scope, for the AI agent to read",
    )
    code_snippets: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Pre-fetched relevant code keyed by node_id. "
            "Saves the AI agent a round-trip to read files."
        ),
    )

    # --- Knowledge ---
    invariants: List[str] = Field(
        default_factory=list,
        description=(
            "Business rules that must be preserved. "
            "E.g., 'Order total must never be negative', "
            "'User email must be unique'."
        ),
    )
    patterns: List[str] = Field(
        default_factory=list,
        description=(
            "Detected architectural patterns. "
            "E.g., 'CQRS: separate read/write models', "
            "'Saga pattern: multi-step order fulfillment'."
        ),
    )
    stale_warnings: List[str] = Field(
        default_factory=list,
        description=(
            "Warnings about stale nodes/edges in the context. "
            "E.g., 'WARNING: Collection orders was last indexed 30 days ago'."
        ),
    )
    risk_assessment: Optional[str] = Field(
        None,
        description=(
            "Agent-generated risk summary. "
            "E.g., 'High Risk: 12 services read from this table'"
        ),
    )

    # --- Metadata ---
    depth: int = Field(
        default=3,
        ge=0,
        description="How many hops upstream/downstream were traversed",
    )
    total_nodes_in_scope: int = Field(
        default=0,
        ge=0,
        description=(
            "Total nodes in this scope (even if not all returned). "
            "Signals complexity to the consuming agent."
        ),
    )


# =============================================================================
# VALIDATION RULES
# =============================================================================

# Valid edge source -> target type constraints.
# This prevents nonsensical edges like "Event CONTAINS Workspace".
# Format: EdgeType -> list of (allowed_source_types, allowed_target_types)

EDGE_CONSTRAINTS: Dict[EdgeType, Dict[str, Set[NodeType]]] = {
    EdgeType.CONTAINS: {
        "sources": {
            NodeType.WORKSPACE, NodeType.PROJECT, NodeType.SERVICE,
            NodeType.MODULE, NodeType.ROUTER,
        },
        "targets": {
            NodeType.PROJECT, NodeType.SERVICE, NodeType.MODULE,
            NodeType.FILE, NodeType.ENDPOINT, NodeType.FUNCTION,
            NodeType.ASYNC_HANDLER, NodeType.DATA_MODEL, NodeType.EVENT,
            NodeType.MIDDLEWARE, NodeType.TYPE_DEF, NodeType.CACHE_KEY,
            NodeType.ENV_VAR, NodeType.ROUTER, NodeType.COLLECTION,
            NodeType.QUEUE, NodeType.INFRA_CONFIG, NodeType.RESOURCE,
        },
    },
    EdgeType.DEFINES: {
        "sources": {NodeType.FILE},
        "targets": {
            NodeType.ENDPOINT, NodeType.FUNCTION, NodeType.DATA_MODEL,
            NodeType.TYPE_DEF, NodeType.MIDDLEWARE, NodeType.ASYNC_HANDLER,
            NodeType.EVENT, NodeType.CACHE_KEY, NodeType.ENV_VAR,
            NodeType.ROUTER, NodeType.COLLECTION,
        },
    },
    EdgeType.IMPORTS: {
        "sources": {NodeType.FILE, NodeType.MODULE},
        "targets": {NodeType.FILE, NodeType.MODULE},
    },
    EdgeType.IMPLEMENTS: {
        "sources": {NodeType.DATA_MODEL, NodeType.FUNCTION, NodeType.TYPE_DEF},
        "targets": {NodeType.TYPE_DEF},
    },
    EdgeType.INHERITS: {
        "sources": {NodeType.DATA_MODEL, NodeType.TYPE_DEF},
        "targets": {NodeType.DATA_MODEL, NodeType.TYPE_DEF},
    },
    EdgeType.READS_DB: {
        "sources": {NodeType.FUNCTION, NodeType.ENDPOINT, NodeType.ASYNC_HANDLER},
        "targets": {NodeType.COLLECTION},
    },
    EdgeType.WRITES_DB: {
        "sources": {NodeType.FUNCTION, NodeType.ENDPOINT, NodeType.ASYNC_HANDLER},
        "targets": {NodeType.COLLECTION},
    },
    EdgeType.MIGRATES: {
        "sources": {NodeType.FUNCTION},
        "targets": {NodeType.COLLECTION},
    },
    EdgeType.CALLS_API: {
        "sources": {NodeType.FUNCTION, NodeType.SERVICE, NodeType.ENDPOINT},
        "targets": {NodeType.EXTERNAL_API},
    },
    EdgeType.CALLS_SERVICE: {
        "sources": {NodeType.SERVICE, NodeType.FUNCTION, NodeType.ENDPOINT},
        "targets": {NodeType.SERVICE},
    },
    EdgeType.CALLS: {
        "sources": {NodeType.FUNCTION, NodeType.ENDPOINT, NodeType.ASYNC_HANDLER, NodeType.MIDDLEWARE},
        "targets": {NodeType.FUNCTION},
    },
    EdgeType.WEBHOOK_SEND: {
        "sources": {NodeType.SERVICE, NodeType.FUNCTION},
        "targets": {NodeType.EXTERNAL_API},
    },
    EdgeType.WEBHOOK_RECEIVE: {
        "sources": {NodeType.ENDPOINT},
        "targets": {NodeType.EXTERNAL_API},
    },
    EdgeType.EMITS_EVENT: {
        "sources": {NodeType.FUNCTION, NodeType.SERVICE, NodeType.ENDPOINT},
        "targets": {NodeType.EVENT},
    },
    EdgeType.CONSUMES_EVENT: {
        "sources": {NodeType.ASYNC_HANDLER},
        "targets": {NodeType.EVENT},
    },
    EdgeType.ENQUEUES: {
        "sources": {NodeType.FUNCTION, NodeType.ENDPOINT, NodeType.ASYNC_HANDLER},
        "targets": {NodeType.QUEUE},
    },
    EdgeType.DEQUEUES: {
        "sources": {NodeType.ASYNC_HANDLER},
        "targets": {NodeType.QUEUE},
    },
    EdgeType.SCHEDULES: {
        "sources": {NodeType.INFRA_CONFIG, NodeType.FUNCTION},
        "targets": {NodeType.ASYNC_HANDLER},
    },
    EdgeType.CACHE_READ: {
        "sources": {NodeType.FUNCTION, NodeType.ENDPOINT, NodeType.MIDDLEWARE},
        "targets": {NodeType.CACHE_KEY},
    },
    EdgeType.CACHE_WRITE: {
        "sources": {NodeType.FUNCTION, NodeType.ENDPOINT, NodeType.MIDDLEWARE},
        "targets": {NodeType.CACHE_KEY},
    },
    EdgeType.ROUTES_TO: {
        "sources": {NodeType.ROUTER},
        "targets": {NodeType.ENDPOINT, NodeType.ROUTER},
    },
    EdgeType.INTERCEPTS: {
        "sources": {NodeType.MIDDLEWARE},
        "targets": {NodeType.ENDPOINT, NodeType.ROUTER, NodeType.SERVICE},
    },
    EdgeType.VALIDATES: {
        "sources": {NodeType.MIDDLEWARE, NodeType.FUNCTION},
        "targets": {NodeType.DATA_MODEL, NodeType.TYPE_DEF},
    },
    EdgeType.AUTHENTICATES: {
        "sources": {NodeType.MIDDLEWARE},
        "targets": {NodeType.ENDPOINT, NodeType.ROUTER, NodeType.SERVICE},
    },
    EdgeType.DEPENDS_ON: {
        "sources": {NodeType.SERVICE, NodeType.PROJECT},
        "targets": {NodeType.RESOURCE, NodeType.QUEUE, NodeType.EXTERNAL_API},
    },
    EdgeType.DEPLOYED_BY: {
        "sources": {NodeType.SERVICE},
        "targets": {NodeType.INFRA_CONFIG},
    },
    EdgeType.CONFIGURES: {
        "sources": {NodeType.ENV_VAR, NodeType.INFRA_CONFIG},
        "targets": {
            NodeType.SERVICE, NodeType.RESOURCE, NodeType.FUNCTION,
            NodeType.EXTERNAL_API, NodeType.QUEUE,
        },
    },
    EdgeType.TESTS: {
        "sources": {NodeType.FUNCTION},
        "targets": {
            NodeType.FUNCTION, NodeType.ENDPOINT, NodeType.DATA_MODEL,
            NodeType.MIDDLEWARE, NodeType.ASYNC_HANDLER,
        },
    },
}


def validate_edge(edge: GraphEdge) -> List[str]:
    """
    Validate an edge against the constraint rules.

    Returns a list of violation messages. Empty list means the edge is valid.
    This is advisory, not enforced -- LLM-inferred edges may not always fit
    the expected patterns, and that is informative rather than fatal.
    """
    violations: List[str] = []
    constraint = EDGE_CONSTRAINTS.get(edge.type)

    if constraint is None:
        return violations  # No constraints defined for this edge type

    # We need node types to validate, but edges only store IDs.
    # This function validates at the schema level; runtime validation
    # requires resolving IDs to nodes (done in GraphStore).
    return violations


def validate_edge_with_nodes(
    edge: GraphEdge,
    source_node: GraphNode,
    target_node: GraphNode,
) -> List[str]:
    """
    Validate an edge against constraints using resolved node types.

    Returns a list of violation messages. Empty list means valid.
    """
    violations: List[str] = []
    constraint = EDGE_CONSTRAINTS.get(edge.type)

    if constraint is None:
        return violations

    allowed_sources = constraint.get("sources", set())
    allowed_targets = constraint.get("targets", set())

    if allowed_sources and source_node.type not in allowed_sources:
        violations.append(
            f"Edge {edge.type.value}: source type {source_node.type.value} "
            f"not in allowed sources {[t.value for t in allowed_sources]}"
        )

    if allowed_targets and target_node.type not in allowed_targets:
        violations.append(
            f"Edge {edge.type.value}: target type {target_node.type.value} "
            f"not in allowed targets {[t.value for t in allowed_targets]}"
        )

    return violations


# =============================================================================
# CONVENIENCE: TYPE SETS FOR QUERYING
# =============================================================================

MACRO_TYPES: Set[NodeType] = {
    nt for nt, tier in NODE_TIER.items() if tier == Tier.MACRO
}

MESO_TYPES: Set[NodeType] = {
    nt for nt, tier in NODE_TIER.items() if tier == Tier.MESO
}

MICRO_TYPES: Set[NodeType] = {
    nt for nt, tier in NODE_TIER.items() if tier == Tier.MICRO
}

# Types that represent "executable" code (vs. structural/config)
EXECUTABLE_TYPES: Set[NodeType] = {
    NodeType.ENDPOINT,
    NodeType.FUNCTION,
    NodeType.ASYNC_HANDLER,
    NodeType.MIDDLEWARE,
}

# Types that represent data schema
DATA_TYPES: Set[NodeType] = {
    NodeType.DATA_MODEL,
    NodeType.COLLECTION,
    NodeType.TYPE_DEF,
    NodeType.CACHE_KEY,
}

# Types that represent infrastructure
INFRA_TYPES: Set[NodeType] = {
    NodeType.RESOURCE,
    NodeType.INFRA_CONFIG,
    NodeType.QUEUE,
    NodeType.ENV_VAR,
}

# Edges that represent runtime behavior (vs. static structure)
OPERATIONAL_EDGES: Set[EdgeType] = {
    EdgeType.READS_DB, EdgeType.WRITES_DB, EdgeType.CALLS_API,
    EdgeType.CALLS_SERVICE, EdgeType.CALLS, EdgeType.EMITS_EVENT,
    EdgeType.CONSUMES_EVENT, EdgeType.ENQUEUES, EdgeType.DEQUEUES,
    EdgeType.CACHE_READ, EdgeType.CACHE_WRITE, EdgeType.WEBHOOK_SEND,
    EdgeType.WEBHOOK_RECEIVE,
}

# Edges that represent static structure
STRUCTURAL_EDGES: Set[EdgeType] = {
    EdgeType.CONTAINS, EdgeType.DEFINES, EdgeType.IMPORTS,
    EdgeType.IMPLEMENTS, EdgeType.INHERITS,
}


# =============================================================================
# WELL-KNOWN TAGS
# =============================================================================

class WellKnownTag:
    """
    Standard tags for consistent classification without type explosion.

    Usage: node.tags = [WellKnownTag.TEST, WellKnownTag.UNIT_TEST]
    """

    # Role tags (what role does this Function play?)
    TEST = "test"
    SCRIPT = "script"
    UTILITY = "utility"
    MIGRATION = "migration"
    SEED = "seed"                  # Database seed script
    ENTRY_POINT = "entry-point"    # Main/index file or function

    # Test type tags
    UNIT_TEST = "unit-test"
    INTEGRATION_TEST = "integration-test"
    E2E_TEST = "e2e-test"

    # Lifecycle tags
    DEPRECATED = "deprecated"
    EXPERIMENTAL = "experimental"
    STABLE = "stable"

    # Importance tags
    CRITICAL_PATH = "critical-path"
    HOT_PATH = "hot-path"          # Performance-sensitive path
    PUBLIC_API = "public-api"      # Part of the public interface

    # Framework tags (set automatically by scanner)
    REACT_COMPONENT = "react-component"
    GRAPHQL_RESOLVER = "graphql-resolver"
    WEBSOCKET_HANDLER = "websocket-handler"

    # Security tags
    AUTH_REQUIRED = "auth-required"
    RATE_LIMITED = "rate-limited"
    SENSITIVE_DATA = "sensitive-data"


# =============================================================================
# WELL-KNOWN METADATA KEYS
# =============================================================================

class MetadataKey:
    """
    Standard metadata keys for consistent storage without schema changes.

    These are not enforced by the type system but documented for consistency.
    """

    # Endpoint metadata
    HTTP_METHOD = "http_method"        # GET, POST, PUT, DELETE, PATCH
    HTTP_PATH = "http_path"            # /api/users/:id
    GRAPHQL_TYPE = "graphql_type"      # query, mutation, subscription
    GRAPHQL_FIELD = "graphql_field"    # users, createUser

    # Function metadata
    FRAMEWORK = "framework"            # express, flask, spring, etc.
    ORM = "orm"                        # mongoose, sqlalchemy, prisma, etc.
    IS_ASYNC = "is_async"              # true/false

    # AsyncHandler metadata
    TRIGGER = "trigger"                # queue, event, cron, webhook
    SCHEDULE = "schedule"              # cron expression or interval
    RETRY_POLICY = "retry_policy"      # {max_retries: 3, backoff: "exponential"}

    # DataModel metadata
    TABLE_NAME = "table_name"          # Physical table/collection name
    FIELDS = "fields"                  # List of field definitions
    INDEXES = "indexes"                # List of index definitions

    # Collection metadata
    DATABASE = "database"              # Which database instance
    ENGINE = "engine"                  # postgres, mongodb, mysql, etc.

    # EnvVar metadata
    SENSITIVITY = "sensitivity"        # public, internal, secret
    DEFAULT_VALUE = "default_value"    # Default if not set
    REQUIRED = "required"              # true/false
    SOURCE = "source"                  # .env, k8s-configmap, vault, etc.

    # Middleware metadata
    ORDER = "order"                    # Execution order (lower = earlier)
    SCOPE = "scope"                    # global, router, endpoint

    # Router metadata
    PREFIX = "prefix"                  # Route prefix (e.g., /api/v1)
    VERSION = "version"                # API version

    # General
    LINE_COUNT = "line_count"          # Lines of code
    COMPLEXITY = "complexity"          # Cyclomatic complexity score
    LAST_COMMIT = "last_commit"        # Hash of last commit touching this entity
    LAST_AUTHOR = "last_author"        # Author of last commit
