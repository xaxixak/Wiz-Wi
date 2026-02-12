"""
Workspace Intelligence - MCP Server (Story 4.3)
================================================

Stdio-based MCP server exposing graph intelligence to AI agents.

Protocol: JSON-RPC 2.0 over stdin/stdout (MCP spec 2024-11-05)
Transport: stdio (newline-delimited JSON)

Tools exposed:
  1. search_entity    - Search nodes by name/type/tag
  2. traverse_graph   - Walk upstream/downstream from a node
  3. get_context      - Generate a ContextPack for AI consumption
  4. impact_analysis  - Show blast radius of a node
  5. get_stats        - Graph statistics

Usage:
  python -m api.mcp_server --graph workspace_graph.json
"""

import json
import sys
import argparse
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

# Add project root to path so we can import graph_store / ontology
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Tier, ContextPack


# =============================================================================
# TOOL DEFINITIONS (MCP schema)
# =============================================================================

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_entity",
        "description": (
            "Search for entities in the code knowledge graph by name. "
            "Returns matching nodes with id, type, name, description, tier, tags, and confidence. "
            "Useful for finding functions, endpoints, services, data models, etc."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (case-insensitive substring match on node name)",
                },
                "type_filter": {
                    "type": "string",
                    "description": (
                        "Filter by NodeType. Valid values: Workspace, Project, Service, "
                        "Resource, ExternalAPI, Module, File, Router, Collection, InfraConfig, "
                        "Queue, Endpoint, Function, AsyncHandler, DataModel, Event, Middleware, "
                        "TypeDef, CacheKey, EnvVar"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "traverse_graph",
        "description": (
            "Traverse the knowledge graph from a starting node. "
            "Follow edges upstream (callers/dependents), downstream (callees/dependencies), "
            "or both directions. Returns the traversed nodes and connecting edges."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "ID of the starting node (e.g., 'endpoint:user-api:POST:/users')",
                },
                "direction": {
                    "type": "string",
                    "enum": ["upstream", "downstream", "both"],
                    "description": (
                        "Traversal direction. 'upstream' = who calls/depends on this node, "
                        "'downstream' = what this node calls/depends on, 'both' = both directions"
                    ),
                },
                "depth": {
                    "type": "integer",
                    "description": "Maximum traversal depth in hops (default: 3)",
                    "default": 3,
                },
                "edge_type_filter": {
                    "type": "string",
                    "description": (
                        "Filter edges by type (e.g., 'CALLS', 'READS_DB', 'CONTAINS'). "
                        "If omitted, all edge types are included."
                    ),
                },
            },
            "required": ["node_id", "direction"],
        },
    },
    {
        "name": "get_context",
        "description": (
            "Generate a ContextPack for AI consumption. Provides architectural context "
            "around a scope (node ID or name) for a given task focus. Includes relevant nodes, "
            "edges, upstream/downstream dependencies, stale warnings, and risk assessment. "
            "Use detail_level to control verbosity and token budget."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": (
                        "Node ID or name to center the context on "
                        "(e.g., 'service:order-api' or 'OrderService')"
                    ),
                },
                "focus": {
                    "type": "string",
                    "description": "Task description (e.g., 'Refactoring database schema')",
                },
                "depth": {
                    "type": "integer",
                    "description": "Traversal depth in hops (default: 3)",
                    "default": 3,
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["L1", "L2", "L3"],
                    "description": (
                        "Verbosity level. L1: names only (~200 tokens), "
                        "L2: names + descriptions (~1K tokens), "
                        "L3: full detail + code snippets (~4K tokens). Default: L2"
                    ),
                    "default": "L2",
                },
            },
            "required": ["scope", "focus"],
        },
    },
    {
        "name": "impact_analysis",
        "description": (
            "Analyze the blast radius of a node. Shows what depends on this node "
            "(upstream callers/consumers) and what it depends on (downstream). "
            "Returns upstream nodes, downstream nodes, risk assessment, and blast radius count. "
            "Essential before making changes to understand what could break."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "ID of the node to analyze impact for",
                },
                "depth": {
                    "type": "integer",
                    "description": "How many hops to traverse for impact (default: 3)",
                    "default": 3,
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "get_stats",
        "description": (
            "Get graph statistics: total nodes, total edges, breakdown by type and tier, "
            "stale node/edge counts. Useful for understanding the overall graph health and size."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

def _serialize_node(node: GraphNode, detail: str = "L2") -> Dict[str, Any]:
    """Serialize a GraphNode at the requested detail level."""
    if detail == "L1":
        return {
            "id": node.id,
            "type": node.type.value,
            "name": node.name,
            "tier": node.tier.value,
        }
    elif detail == "L2":
        return {
            "id": node.id,
            "type": node.type.value,
            "name": node.name,
            "description": node.description,
            "tier": node.tier.value,
            "tags": node.tags,
            "confidence": node.confidence,
            "is_stale": node.is_stale,
        }
    else:  # L3
        return node.model_dump(mode="json")


def _serialize_edge(edge: GraphEdge, detail: str = "L2") -> Dict[str, Any]:
    """Serialize a GraphEdge at the requested detail level."""
    if detail == "L1":
        return {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "type": edge.type.value,
        }
    elif detail == "L2":
        return {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "type": edge.type.value,
            "description": edge.description,
            "confidence": edge.confidence,
            "conditional": edge.conditional,
        }
    else:  # L3
        return edge.model_dump(mode="json")


def tool_search_entity(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Search nodes by name, optionally filtered by type."""
    query = arguments.get("query", "").lower()
    type_filter = arguments.get("type_filter")
    limit = arguments.get("limit", 10)

    # Get candidate nodes
    if type_filter:
        try:
            node_type = NodeType(type_filter)
            candidates = store.get_nodes_by_type(node_type)
        except ValueError:
            return {
                "error": f"Unknown NodeType: '{type_filter}'. Valid types: {[t.value for t in NodeType]}",
                "results": [],
            }
    else:
        candidates = list(store._nodes.values())

    # Filter by substring match on name (case-insensitive)
    matches = [n for n in candidates if query in n.name.lower()]

    # Sort by confidence descending, then name
    matches.sort(key=lambda n: (-n.confidence, n.name))

    # Apply limit
    matches = matches[:limit]

    return {
        "total_matches": len(matches),
        "results": [_serialize_node(n, "L2") for n in matches],
    }


def tool_traverse_graph(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Traverse the graph from a starting node."""
    node_id = arguments.get("node_id", "")
    direction = arguments.get("direction", "both")
    depth = arguments.get("depth", 3)
    edge_type_filter = arguments.get("edge_type_filter")

    # Validate starting node
    start_node = store.get_node(node_id)
    if not start_node:
        return {"error": f"Node not found: '{node_id}'", "nodes": [], "edges": []}

    # Collect traversed nodes
    upstream_nodes: List[GraphNode] = []
    downstream_nodes: List[GraphNode] = []

    if direction in ("upstream", "both"):
        upstream_nodes = store.get_upstream(node_id, depth)
    if direction in ("downstream", "both"):
        downstream_nodes = store.get_downstream(node_id, depth)

    # Collect all relevant node IDs
    all_ids = {node_id}
    all_ids.update(n.id for n in upstream_nodes)
    all_ids.update(n.id for n in downstream_nodes)

    # Gather edges between traversed nodes
    edges = [
        e for e in store._edges.values()
        if e.source_id in all_ids and e.target_id in all_ids
    ]

    # Apply edge type filter if specified
    if edge_type_filter:
        try:
            et = EdgeType(edge_type_filter)
            edges = [e for e in edges if e.type == et]
        except ValueError:
            return {
                "error": f"Unknown EdgeType: '{edge_type_filter}'. Valid types: {[t.value for t in EdgeType]}",
                "nodes": [],
                "edges": [],
            }

    # Combine all nodes (deduplicated)
    all_nodes_map: Dict[str, GraphNode] = {node_id: start_node}
    for n in upstream_nodes + downstream_nodes:
        all_nodes_map[n.id] = n

    return {
        "start_node": _serialize_node(start_node, "L2"),
        "direction": direction,
        "depth": depth,
        "total_nodes": len(all_nodes_map),
        "total_edges": len(edges),
        "nodes": [_serialize_node(n, "L2") for n in all_nodes_map.values()],
        "edges": [_serialize_edge(e, "L2") for e in edges],
    }


def tool_get_context(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a ContextPack for AI consumption."""
    scope = arguments.get("scope", "")
    focus = arguments.get("focus", "")
    depth = arguments.get("depth", 3)
    detail_level = arguments.get("detail_level", "L2")

    # Validate detail level
    if detail_level not in ("L1", "L2", "L3"):
        detail_level = "L2"

    # Map detail level to token budget
    token_budgets = {"L1": 200, "L2": 1000, "L3": 4000}
    max_tokens = token_budgets.get(detail_level, 1000)

    # Get context pack from the store with token budget
    context = store.get_context(scope, focus, max_depth=depth, max_tokens=max_tokens)

    # Serialize at the requested detail level
    result: Dict[str, Any] = {
        "scope": context.scope,
        "focus": context.focus,
        "depth": context.depth,
        "total_nodes_in_scope": context.total_nodes_in_scope,
    }

    if detail_level == "L1":
        # Names only (~200 tokens)
        result["relevant_nodes"] = [
            {"id": n.id, "type": n.type.value, "name": n.name}
            for n in context.relevant_nodes
        ]
        result["upstream"] = [
            {"id": n.id, "type": n.type.value, "name": n.name}
            for n in context.upstream
        ]
        result["downstream"] = [
            {"id": n.id, "type": n.type.value, "name": n.name}
            for n in context.downstream
        ]
        result["edges"] = [
            {"source_id": e.source_id, "target_id": e.target_id, "type": e.type.value}
            for e in context.relevant_edges
        ]

    elif detail_level == "L2":
        # Names + descriptions (~1K tokens)
        result["relevant_nodes"] = [_serialize_node(n, "L2") for n in context.relevant_nodes]
        result["upstream"] = [_serialize_node(n, "L2") for n in context.upstream]
        result["downstream"] = [_serialize_node(n, "L2") for n in context.downstream]
        result["edges"] = [_serialize_edge(e, "L2") for e in context.relevant_edges]
        result["stale_warnings"] = context.stale_warnings
        result["risk_assessment"] = context.risk_assessment
        result["patterns"] = context.patterns
        result["invariants"] = context.invariants

    else:  # L3
        # Full detail + code snippets (~4K tokens)
        result["relevant_nodes"] = [_serialize_node(n, "L3") for n in context.relevant_nodes]
        result["upstream"] = [_serialize_node(n, "L3") for n in context.upstream]
        result["downstream"] = [_serialize_node(n, "L3") for n in context.downstream]
        result["edges"] = [_serialize_edge(e, "L3") for e in context.relevant_edges]
        result["stale_warnings"] = context.stale_warnings
        result["risk_assessment"] = context.risk_assessment
        result["patterns"] = context.patterns
        result["invariants"] = context.invariants
        result["related_files"] = [
            loc.model_dump(mode="json") for loc in context.related_files
        ]
        result["code_snippets"] = context.code_snippets

    return result


def tool_impact_analysis(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze the blast radius of a node."""
    node_id = arguments.get("node_id", "")
    depth = arguments.get("depth", 3)

    # Validate node
    node = store.get_node(node_id)
    if not node:
        return {"error": f"Node not found: '{node_id}'"}

    # Get upstream (who depends on this) and downstream (what this depends on)
    upstream = store.get_upstream(node_id, depth)
    downstream = store.get_downstream(node_id, depth)

    # Classify upstream by tier for risk assessment
    upstream_by_tier: Dict[str, int] = {"macro": 0, "meso": 0, "micro": 0}
    for n in upstream:
        upstream_by_tier[n.tier.value] += 1

    # Build risk assessment
    blast_radius = len(upstream) + len(downstream)
    if len(upstream) > 10:
        risk_level = "CRITICAL"
        risk_summary = (
            f"CRITICAL: {len(upstream)} components depend on this node. "
            f"Changes here have a wide blast radius ({blast_radius} total nodes affected)."
        )
    elif len(upstream) > 5:
        risk_level = "HIGH"
        risk_summary = (
            f"HIGH RISK: {len(upstream)} upstream dependencies. "
            f"Blast radius: {blast_radius} nodes."
        )
    elif len(upstream) > 2:
        risk_level = "MEDIUM"
        risk_summary = (
            f"MEDIUM RISK: {len(upstream)} upstream dependencies. "
            f"Blast radius: {blast_radius} nodes."
        )
    else:
        risk_level = "LOW"
        risk_summary = (
            f"LOW RISK: {len(upstream)} upstream dependencies. "
            f"Blast radius: {blast_radius} nodes."
        )

    # Check for stale nodes in the impact zone
    stale_in_zone = [n for n in upstream + downstream if n.is_stale]
    stale_warning = None
    if stale_in_zone:
        stale_warning = (
            f"{len(stale_in_zone)} node(s) in the impact zone are stale and may have "
            f"outdated information: {[n.name for n in stale_in_zone[:5]]}"
        )

    # Get edges directly connected to this node
    edges_from = store.get_edges_from(node_id)
    edges_to = store.get_edges_to(node_id)

    return {
        "node": _serialize_node(node, "L2"),
        "risk_level": risk_level,
        "risk_summary": risk_summary,
        "blast_radius": blast_radius,
        "upstream_count": len(upstream),
        "downstream_count": len(downstream),
        "upstream_by_tier": upstream_by_tier,
        "upstream": [_serialize_node(n, "L2") for n in upstream],
        "downstream": [_serialize_node(n, "L2") for n in downstream],
        "direct_edges_in": [_serialize_edge(e, "L2") for e in edges_to],
        "direct_edges_out": [_serialize_edge(e, "L2") for e in edges_from],
        "stale_warning": stale_warning,
    }


def tool_get_stats(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Return graph statistics."""
    stats = store.stats()

    # Filter out zero-count entries for cleaner output
    stats["nodes_by_type"] = {
        k: v for k, v in stats["nodes_by_type"].items() if v > 0
    }
    stats["edges_by_type"] = {
        k: v for k, v in stats["edges_by_type"].items() if v > 0
    }
    stats["nodes_by_tier"] = {
        k: v for k, v in stats["nodes_by_tier"].items() if v > 0
    }

    return stats


# =============================================================================
# TOOL DISPATCH
# =============================================================================

TOOL_HANDLERS = {
    "search_entity": tool_search_entity,
    "traverse_graph": tool_traverse_graph,
    "get_context": tool_get_context,
    "impact_analysis": tool_impact_analysis,
    "get_stats": tool_get_stats,
}


_VIEWER_URL = "http://127.0.0.1:8080/api/agent-activity"

def _broadcast_activity(tool_name: str, arguments: Dict[str, Any], result_summary: str):
    """Broadcast agent activity to viewer (fire-and-forget, non-blocking)."""
    def _send():
        try:
            # Extract the focus target from arguments
            focus = (
                arguments.get("node_id")
                or arguments.get("query")
                or arguments.get("start_node_id")
                or "graph"
            )
            payload = json.dumps({
                "type": "agent_activity",
                "tool": tool_name,
                "focus": focus,
                "args": {k: v for k, v in arguments.items() if k != "token_budget"},
                "summary": result_summary,
                "source": "mcp_server",
            }).encode("utf-8")
            req = Request(_VIEWER_URL, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            urlopen(req, timeout=2)
        except (URLError, OSError):
            pass  # Viewer not running — that's fine
    threading.Thread(target=_send, daemon=True).start()


def call_tool(store: GraphStore, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a tool call to the appropriate handler."""
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: '{tool_name}'. Available: {list(TOOL_HANDLERS.keys())}"}
    try:
        result = handler(store, arguments)
        # Broadcast activity to viewer (Oracle v2 pattern: agent uses tool → WI sees it)
        summary = ""
        if "matches" in result:
            summary = f"{len(result['matches'])} matches"
        elif "nodes" in result:
            summary = f"{len(result['nodes'])} nodes"
        elif "total_nodes" in result:
            summary = f"{result['total_nodes']} nodes, {result['total_edges']} edges"
        elif "affected_nodes" in result:
            summary = f"{len(result['affected_nodes'])} affected"
        _broadcast_activity(tool_name, arguments, summary)
        return result
    except Exception as exc:
        return {"error": f"Tool '{tool_name}' failed: {str(exc)}"}


# =============================================================================
# JSON-RPC REQUEST HANDLING
# =============================================================================

def handle_request(store: GraphStore, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Handle a single JSON-RPC 2.0 request.

    Returns a response dict, or None for notifications (requests without an id).
    """
    method = request.get("method")
    params = request.get("params", {})
    req_id = request.get("id")

    log(f"<-- {method}" + (f" (id={req_id})" if req_id is not None else " (notification)"))

    # Notifications (no id) don't need a response
    is_notification = req_id is None

    try:
        result = _dispatch_method(store, method, params)
    except Exception as exc:
        if is_notification:
            log(f"Error handling notification '{method}': {exc}")
            return None
        return _error_response(req_id, -32603, f"Internal error: {str(exc)}")

    if is_notification:
        return None

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


def _dispatch_method(store: GraphStore, method: str, params: Dict[str, Any]) -> Any:
    """Route a method call to the appropriate handler."""

    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": "workspace-intelligence",
                "version": "0.1.0",
            },
        }

    elif method == "notifications/initialized":
        # Client acknowledges initialization -- nothing to return
        return None

    elif method == "tools/list":
        return {"tools": TOOLS}

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        result = call_tool(store, tool_name, arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2, default=str),
                }
            ],
        }

    elif method == "ping":
        return {}

    else:
        raise ValueError(f"Unknown method: '{method}'")


def _error_response(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    """Build a JSON-RPC error response."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


# =============================================================================
# LOGGING (stderr only -- stdout is the protocol channel)
# =============================================================================

def log(message: str) -> None:
    """Log to stderr. Never write logs to stdout (that's the MCP channel)."""
    print(f"[mcp-server] {message}", file=sys.stderr, flush=True)


# =============================================================================
# MAIN LOOP
# =============================================================================

def run_server(graph_path: str) -> None:
    """
    Main server loop.

    Reads newline-delimited JSON-RPC from stdin, writes responses to stdout.
    """
    store = GraphStore()

    # Load graph if the file exists
    graph_file = Path(graph_path)
    if graph_file.exists():
        log(f"Loading graph from {graph_file}")
        store.load(graph_file)
        stats = store.stats()
        log(f"Loaded {stats['total_nodes']} nodes, {stats['total_edges']} edges")
    else:
        log(f"Graph file not found: {graph_file} -- starting with empty graph")

    log("MCP server ready (stdio transport)")

    # Read from stdin line by line
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # Parse JSON-RPC request
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            error = _error_response(None, -32700, f"Parse error: {str(exc)}")
            _write_response(error)
            continue

        # Handle the request
        response = handle_request(store, request)

        # Only write a response for requests (not notifications)
        if response is not None:
            _write_response(response)


def _write_response(response: Dict[str, Any]) -> None:
    """Write a JSON-RPC response to stdout."""
    output = json.dumps(response, default=str)
    sys.stdout.write(output + "\n")
    sys.stdout.flush()


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Workspace Intelligence MCP Server",
    )
    parser.add_argument(
        "--graph",
        type=str,
        default="workspace_graph.json",
        help="Path to the graph JSON file (default: workspace_graph.json)",
    )
    args = parser.parse_args()
    run_server(args.graph)


if __name__ == "__main__":
    main()
