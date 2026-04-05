"""
Workspace Intelligence Layer - Pass 5: Execution Flow Tracing
==============================================================

Detects entry points and traces execution flows through the call graph.
Inspired by GitNexus's process-processor.

Entry Point Scoring:
  - Route handlers (express router callbacks):  score 1.0
  - Exported main/start functions:              score 0.9
  - Event handlers (on/consume patterns):       score 0.8
  - Middleware functions:                        score 0.7
  - Exported functions (general):               score 0.6

Flow Tracing:
  - BFS from each entry point following CALLS edges
  - Max depth configurable (default: 10)
  - Creates Flow nodes and STEP_IN_FLOW edges
  - Names flows based on entry point name

Cost: FREE (graph traversal only, no LLM calls)
"""

import sys
import logging
from pathlib import Path
from typing import Dict, List, Set, Tuple
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Provenance

logger = logging.getLogger("workspace-intelligence")


# =============================================================================
# ENTRY POINT SCORING
# =============================================================================

# Patterns that indicate entry points (checked against function name, lowercase)
ENTRY_POINT_PATTERNS = {
    # Route handlers / controllers
    "handler": 0.9,
    "controller": 0.9,
    # Server lifecycle
    "start": 0.9,
    "main": 0.9,
    "init": 0.85,
    "bootstrap": 0.85,
    "setup": 0.8,
    # Event handlers
    "listener": 0.8,
    "subscriber": 0.8,
    "consumer": 0.8,
    "onmessage": 0.8,
    "onevent": 0.8,
    # Queue workers
    "worker": 0.8,
    "processor": 0.8,
    # Middleware
    "middleware": 0.7,
    "interceptor": 0.7,
}


@dataclass
class EntryPoint:
    """A detected entry point with its score."""
    node: GraphNode
    score: float
    reason: str  # why this is an entry point


@dataclass
class Flow:
    """An execution flow traced from an entry point."""
    id: str
    name: str
    entry_point_id: str
    terminal_id: str
    steps: List[str]  # ordered list of node IDs
    step_count: int
    flow_type: str  # "route", "event", "startup", "general"


def score_entry_points(store: GraphStore) -> List[EntryPoint]:
    """
    Score all functions/endpoints as potential entry points.

    Returns sorted list (highest score first).
    """
    entry_points: List[EntryPoint] = []

    # 1. Endpoints are always entry points (score 1.0)
    for node in store.get_nodes_by_type(NodeType.ENDPOINT):
        entry_points.append(EntryPoint(
            node=node, score=1.0, reason="HTTP endpoint",
        ))

    # 2. Score functions by name patterns and properties
    for node in store.get_nodes_by_type(NodeType.FUNCTION):
        name_lower = node.name.lower()
        best_score = 0.0
        best_reason = ""

        # Check name patterns
        for pattern, score in ENTRY_POINT_PATTERNS.items():
            if pattern in name_lower:
                if score > best_score:
                    best_score = score
                    best_reason = f"name matches '{pattern}'"

        # Boost exported functions
        if node.metadata.get("is_exported"):
            if best_score > 0:
                best_score = min(best_score + 0.1, 1.0)
            else:
                best_score = 0.6
                best_reason = "exported function"

        # Functions with no incoming CALLS edges are likely entry points
        incoming_calls = store.get_edges_to(node.id)
        call_edges = [e for e in incoming_calls if e.type == EdgeType.CALLS]
        if not call_edges and best_score == 0:
            # Only if it has outgoing calls (not a leaf)
            outgoing_calls = store.get_edges_from(node.id)
            out_call_edges = [e for e in outgoing_calls if e.type == EdgeType.CALLS]
            if out_call_edges:
                best_score = 0.5
                best_reason = "uncalled function with outgoing calls"

        if best_score > 0:
            entry_points.append(EntryPoint(
                node=node, score=best_score, reason=best_reason,
            ))

    # 3. AsyncHandlers are entry points
    for node in store.get_nodes_by_type(NodeType.ASYNC_HANDLER):
        entry_points.append(EntryPoint(
            node=node, score=0.85, reason="async handler",
        ))

    # 4. Middleware
    for node in store.get_nodes_by_type(NodeType.MIDDLEWARE):
        entry_points.append(EntryPoint(
            node=node, score=0.7, reason="middleware",
        ))

    # Sort by score descending
    entry_points.sort(key=lambda ep: -ep.score)
    return entry_points


# =============================================================================
# FLOW TRACING
# =============================================================================

def trace_flow(
    store: GraphStore,
    entry_node_id: str,
    max_depth: int = 10,
) -> List[str]:
    """
    Trace execution flow from an entry point via BFS on CALLS edges.

    Returns ordered list of node IDs representing the flow steps.
    """
    visited: Set[str] = set()
    steps: List[str] = []
    queue: List[Tuple[str, int]] = [(entry_node_id, 0)]

    while queue:
        node_id, depth = queue.pop(0)

        if node_id in visited or depth > max_depth:
            continue
        visited.add(node_id)

        # Only include function-like nodes in the flow
        node = store.get_node(node_id)
        if node is None:
            continue
        if node.type not in (NodeType.FUNCTION, NodeType.ASYNC_HANDLER,
                             NodeType.MIDDLEWARE, NodeType.ENDPOINT):
            continue

        steps.append(node_id)

        # Follow CALLS edges
        outgoing = store.get_edges_from(node_id)
        for edge in outgoing:
            if edge.type == EdgeType.CALLS and edge.target_id not in visited:
                queue.append((edge.target_id, depth + 1))

    return steps


def _classify_flow(entry_point: EntryPoint) -> str:
    """Classify a flow based on its entry point."""
    if entry_point.node.type == NodeType.ENDPOINT:
        return "route"
    name_lower = entry_point.node.name.lower()
    if any(p in name_lower for p in ("listener", "consumer", "onevent", "onmessage")):
        return "event"
    if any(p in name_lower for p in ("start", "main", "init", "bootstrap", "setup")):
        return "startup"
    return "general"


def _flow_name(entry_node: GraphNode, terminal_node: GraphNode) -> str:
    """Generate a human-readable flow name."""
    entry_name = entry_node.name
    terminal_name = terminal_node.name
    if entry_name == terminal_name:
        return entry_name
    return f"{entry_name} -> {terminal_name}"


# =============================================================================
# PASS 5 MAIN
# =============================================================================

def run_flow_tracing(
    store: GraphStore,
    max_flows: int = 50,
    min_steps: int = 2,
    max_depth: int = 10,
    min_entry_score: float = 0.5,
) -> Dict:
    """
    Run Pass 5: detect entry points and trace execution flows.

    Args:
        store: GraphStore with CALLS edges from earlier passes.
        max_flows: Maximum number of flows to trace.
        min_steps: Minimum steps for a flow to be included.
        max_depth: Maximum BFS depth per flow.
        min_entry_score: Minimum entry point score to trace.

    Returns:
        Summary dict with counts and created nodes/edges.
    """
    # 1. Score entry points
    entry_points = score_entry_points(store)
    entry_points = [ep for ep in entry_points if ep.score >= min_entry_score]

    logger.info(f"  Found {len(entry_points)} entry points (score >= {min_entry_score})")

    # 2. Trace flows from each entry point
    flows: List[Flow] = []
    seen_step_sets: Set[frozenset] = set()  # deduplicate identical flows

    for ep in entry_points[:max_flows * 2]:  # trace extra, then filter
        steps = trace_flow(store, ep.node.id, max_depth=max_depth)

        if len(steps) < min_steps:
            continue

        # Deduplicate: skip flows with identical step sets
        step_key = frozenset(steps)
        if step_key in seen_step_sets:
            continue
        seen_step_sets.add(step_key)

        # Get terminal node
        terminal_node = store.get_node(steps[-1])
        if terminal_node is None:
            continue

        flow_idx = len(flows)
        flow_type = _classify_flow(ep)
        name = _flow_name(ep.node, terminal_node)

        flows.append(Flow(
            id=f"flow_{flow_idx}_{ep.node.name.lower().replace(' ', '_')}",
            name=name,
            entry_point_id=ep.node.id,
            terminal_id=steps[-1],
            steps=steps,
            step_count=len(steps),
            flow_type=flow_type,
        ))

        if len(flows) >= max_flows:
            break

    logger.info(f"  Traced {len(flows)} execution flows")

    # 3. Create Flow nodes and STEP_IN_FLOW edges
    nodes_created = 0
    edges_created = 0

    for flow in flows:
        # Create the Flow node (we reuse the Event node type for now,
        # but tag it as a flow — future: add FLOW to NodeType)
        flow_node = GraphNode(
            id=flow.id,
            type=NodeType.EVENT,  # Reuse Event type, tagged as flow
            name=flow.name,
            description=f"Execution flow: {flow.name} ({flow.step_count} steps)",
            provenance=Provenance.SCANNER,
            confidence=0.8,
            tags=["flow", flow.flow_type],
            metadata={
                "flow_type": flow.flow_type,
                "step_count": flow.step_count,
                "entry_point_id": flow.entry_point_id,
                "terminal_id": flow.terminal_id,
                "steps": flow.steps,
            },
        )
        store.add_node(flow_node)
        nodes_created += 1

        # Create EMITS_EVENT edges from each step to the flow
        # (representing participation in the flow)
        for step_idx, step_id in enumerate(flow.steps):
            edge = GraphEdge(
                source_id=step_id,
                target_id=flow.id,
                type=EdgeType.EMITS_EVENT,  # Reuse — represents "participates in flow"
                description=f"Step {step_idx + 1} in flow: {flow.name}",
                provenance=Provenance.SCANNER,
                confidence=0.8,
                weight=1.0 - (step_idx / max(len(flow.steps), 1)),  # Earlier steps weigh more
                metadata={"step": step_idx + 1},
            )
            store.add_edge(edge, validate=False)
            edges_created += 1

    return {
        "entry_points_found": len(entry_points),
        "flows_traced": len(flows),
        "nodes_created": nodes_created,
        "edges_created": edges_created,
        "flows": [
            {
                "id": f.id,
                "name": f.name,
                "type": f.flow_type,
                "steps": f.step_count,
                "entry": f.entry_point_id,
                "terminal": f.terminal_id,
            }
            for f in flows
        ],
    }


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    store = GraphStore()
    graph_path = "graphs/test-shop_graph.json"
    store.load(graph_path)

    print(f"Loaded graph: {graph_path}")
    stats = store.stats()
    print(f"  Nodes: {stats['total_nodes']}, Edges: {stats['total_edges']}")
    print()

    # Run flow tracing
    result = run_flow_tracing(store)
    print(f"\nResults:")
    print(f"  Entry points: {result['entry_points_found']}")
    print(f"  Flows traced: {result['flows_traced']}")
    print(f"  Nodes created: {result['nodes_created']}")
    print(f"  Edges created: {result['edges_created']}")

    print(f"\nFlows:")
    for f in result["flows"]:
        print(f"  [{f['type']:<8}] {f['name']} ({f['steps']} steps)")
        print(f"           entry: {f['entry']}")
        print(f"           terminal: {f['terminal']}")
