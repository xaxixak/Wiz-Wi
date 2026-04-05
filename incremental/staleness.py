"""
Workspace Intelligence Layer - Staleness Propagation (Story 3.2)
================================================================

Smarter staleness cascade that wraps GraphStore.cascade_stale() with
type-aware propagation logic.

Key behaviours:
  - Operational edges (CALLS, READS_DB, EMITS_EVENT, ...) cascade for
    the full configured hop count.
  - Structural edges (CONTAINS, DEFINES, IMPORTS) cascade only 1 hop so
    that marking a function stale does not cascade to the entire project.
  - Edges connecting two stale nodes are themselves marked stale.
  - Tracks which nodes were newly marked stale vs already stale, and
    reports on cascade scope.

Usage:
    from incremental.staleness import smart_cascade, propagate_staleness

    result = smart_cascade(store, "endpoint:order-api:POST:/orders", hops=2)
    report = propagate_staleness(store, {"fn:a", "fn:b"}, hops=2)
    summary = get_stale_summary(store)
"""

from __future__ import annotations

import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Optional

# ---------------------------------------------------------------------------
# Path setup so we can import from the project root
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore  # noqa: E402
from ontology import (  # noqa: E402
    GraphNode,
    GraphEdge,
    NodeType,
    EdgeType,
    Tier,
    OPERATIONAL_EDGES,
    STRUCTURAL_EDGES,
    NODE_TIER,
)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class CascadeResult:
    """Result of a single staleness cascade from one trigger node."""

    trigger_node_id: str
    newly_stale_nodes: List[str] = field(default_factory=list)
    already_stale_nodes: List[str] = field(default_factory=list)
    stale_edges: List[str] = field(default_factory=list)
    hops_used: int = 0
    total_affected: int = 0

    def __post_init__(self):
        self.total_affected = (
            len(self.newly_stale_nodes)
            + len(self.already_stale_nodes)
        )

    def _recompute_total(self) -> None:
        self.total_affected = (
            len(self.newly_stale_nodes)
            + len(self.already_stale_nodes)
        )


@dataclass
class CascadeReport:
    """Aggregated report for multiple cascade operations."""

    trigger_count: int = 0
    results: List[CascadeResult] = field(default_factory=list)
    total_newly_stale: int = 0
    total_already_stale: int = 0
    total_stale_edges: int = 0

    def _recompute_totals(self) -> None:
        self.trigger_count = len(self.results)
        self.total_newly_stale = sum(
            len(r.newly_stale_nodes) for r in self.results
        )
        self.total_already_stale = sum(
            len(r.already_stale_nodes) for r in self.results
        )
        self.total_stale_edges = sum(
            len(r.stale_edges) for r in self.results
        )


# =============================================================================
# CORE: smart_cascade
# =============================================================================

def smart_cascade(
    store: GraphStore,
    node_id: str,
    hops: int = 2,
) -> CascadeResult:
    """
    Intelligent staleness cascade from a single trigger node.

    Unlike GraphStore.cascade_stale() which does a blind BFS in all
    directions for N hops, smart_cascade differentiates edge types:

      - **Operational edges** (CALLS, READS_DB, EMITS_EVENT, CONSUMES_EVENT,
        CALLS_API, CALLS_SERVICE, ENQUEUES, DEQUEUES, CACHE_READ,
        CACHE_WRITE, WEBHOOK_SEND, WEBHOOK_RECEIVE)
        -> traverse for the full ``hops`` depth.

      - **Structural edges** (CONTAINS, DEFINES, IMPORTS, IMPLEMENTS,
        INHERITS)
        -> traverse for at most 1 hop (prevents cascading to entire project).

    After collecting stale nodes, every edge where *both* endpoints are stale
    is also marked stale.

    Args:
        store:   The graph store to operate on.
        node_id: ID of the trigger node.
        hops:    Maximum operational hops (default 2, configurable).

    Returns:
        CascadeResult with newly/already stale nodes, stale edges, and stats.
    """
    trigger = store.get_node(node_id)
    if trigger is None:
        return CascadeResult(trigger_node_id=node_id)

    # Collect nodes that should be marked stale via BFS
    # Key: node_id -> hop depth at which it was reached
    visited: Dict[str, int] = {}
    queue: deque[tuple[str, int, Optional[EdgeType]]] = deque()

    # Seed the queue with the trigger itself at hop 0
    queue.append((node_id, 0, None))

    while queue:
        current_id, depth, arriving_edge_type = queue.popleft()

        # Skip if already visited at an equal or shorter depth
        if current_id in visited and visited[current_id] <= depth:
            continue
        visited[current_id] = depth

        # Determine max allowed depth for edges leaving this node
        # If we arrived via a structural edge, do NOT continue further
        # (structural edges get 1 hop total from the trigger)
        if arriving_edge_type is not None and arriving_edge_type in STRUCTURAL_EDGES:
            # We got here via a structural edge; don't cascade further
            continue

        if depth >= hops:
            continue

        # Traverse outgoing edges
        for edge in store.get_edges_from(current_id):
            neighbour = edge.target_id
            if _should_traverse(edge.type, depth, hops):
                queue.append((neighbour, depth + 1, edge.type))

        # Traverse incoming edges (staleness propagates both directions)
        for edge in store.get_edges_to(current_id):
            neighbour = edge.source_id
            if _should_traverse(edge.type, depth, hops):
                queue.append((neighbour, depth + 1, edge.type))

    # --- Partition into newly-stale vs already-stale ---
    newly_stale: List[str] = []
    already_stale: List[str] = []

    for nid in visited:
        node = store.get_node(nid)
        if node is None:
            continue
        if node.is_stale:
            already_stale.append(nid)
        else:
            newly_stale.append(nid)
            store.mark_stale(nid)

    # --- Mark edges stale when both endpoints are stale ---
    all_stale_ids = set(visited.keys())
    stale_edge_keys = _mark_edges_between_stale_nodes(store, all_stale_ids)

    result = CascadeResult(
        trigger_node_id=node_id,
        newly_stale_nodes=newly_stale,
        already_stale_nodes=already_stale,
        stale_edges=stale_edge_keys,
        hops_used=max(visited.values()) if visited else 0,
    )
    result._recompute_total()
    return result


def _should_traverse(edge_type: EdgeType, current_depth: int, max_hops: int) -> bool:
    """
    Decide whether to follow an edge during cascade.

    - Operational edges: allowed up to max_hops.
    - Structural edges: allowed only from depth 0 (i.e. 1 hop from trigger).
    """
    if edge_type in OPERATIONAL_EDGES:
        return current_depth + 1 <= max_hops
    if edge_type in STRUCTURAL_EDGES:
        return current_depth == 0  # Only traverse structural at hop 0 -> 1
    # For any edge type not in either set (e.g. ROUTES_TO, TESTS, etc.),
    # treat as operational (full hops).
    return current_depth + 1 <= max_hops


def _mark_edges_between_stale_nodes(
    store: GraphStore,
    stale_ids: Set[str],
) -> List[str]:
    """
    Mark all edges where both source and target are in ``stale_ids`` as stale.

    Returns the list of edge keys that were marked stale.
    """
    stale_edge_keys: List[str] = []

    for nid in stale_ids:
        for edge in store.get_edges_from(nid):
            if edge.target_id in stale_ids:
                edge_key = f"{edge.source_id}->{edge.target_id}:{edge.type.value}"
                if not edge.is_stale:
                    store.mark_edge_stale(edge.source_id, edge.target_id, edge.type)
                    stale_edge_keys.append(edge_key)

    return stale_edge_keys


# =============================================================================
# BATCH: propagate_staleness
# =============================================================================

def propagate_staleness(
    store: GraphStore,
    node_ids: Set[str],
    hops: int = 2,
) -> CascadeReport:
    """
    Propagate staleness for multiple trigger nodes and aggregate results.

    This is the primary entry point for the incremental pipeline.
    After change detection identifies which files changed, their
    corresponding graph node IDs are passed here for cascade.

    Deduplication: if node A's cascade already marked node B stale,
    then when we process B, it shows up in ``already_stale_nodes``
    rather than ``newly_stale_nodes``.

    Args:
        store:    The graph store to operate on.
        node_ids: Set of trigger node IDs.
        hops:     Maximum operational hops (default 2).

    Returns:
        CascadeReport aggregating all individual cascade results.
    """
    report = CascadeReport()
    results: List[CascadeResult] = []

    for nid in sorted(node_ids):  # sorted for deterministic ordering
        result = smart_cascade(store, nid, hops=hops)
        results.append(result)

    report.results = results
    report._recompute_totals()
    return report


# =============================================================================
# REPORTING: get_stale_summary
# =============================================================================

def get_stale_summary(store: GraphStore) -> Dict:
    """
    Return a summary of all stale nodes and edges in the graph.

    Returns:
        Dict with keys:
          - stale_nodes_by_type:  {NodeType.value: count}
          - stale_nodes_by_tier:  {Tier.value: count}
          - stale_edges_by_type:  {EdgeType.value: count}
          - total_stale_nodes:    int
          - total_stale_edges:    int
          - total_nodes:          int
          - total_edges:          int
          - stale_node_pct:       float  (0.0 - 100.0)
          - stale_edge_pct:       float  (0.0 - 100.0)
    """
    stats = store.stats()

    # --- Stale nodes by type ---
    stale_by_type: Dict[str, int] = defaultdict(int)
    stale_by_tier: Dict[str, int] = defaultdict(int)

    for node in store._nodes.values():
        if node.is_stale:
            stale_by_type[node.type.value] += 1
            stale_by_tier[node.tier.value] += 1

    # --- Stale edges by type ---
    stale_edges_by_type: Dict[str, int] = defaultdict(int)
    for edge in store._edges.values():
        if edge.is_stale:
            stale_edges_by_type[edge.type.value] += 1

    total_nodes = stats["total_nodes"]
    total_edges = stats["total_edges"]
    total_stale_nodes = stats["stale_nodes"]
    total_stale_edges = stats["stale_edges"]

    return {
        "stale_nodes_by_type": dict(stale_by_type),
        "stale_nodes_by_tier": dict(stale_by_tier),
        "stale_edges_by_type": dict(stale_edges_by_type),
        "total_stale_nodes": total_stale_nodes,
        "total_stale_edges": total_stale_edges,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "stale_node_pct": (
            round(total_stale_nodes / total_nodes * 100, 1)
            if total_nodes > 0 else 0.0
        ),
        "stale_edge_pct": (
            round(total_stale_edges / total_edges * 100, 1)
            if total_edges > 0 else 0.0
        ),
    }


# =============================================================================
# __main__  --  quick smoke test
# =============================================================================

if __name__ == "__main__":
    print("=== Staleness Module Smoke Test ===\n")

    # Build a small test graph:
    #
    #   Project --CONTAINS--> Service --CONTAINS--> Endpoint
    #                                                  |
    #                                    CALLS --------+-----> Function
    #                                                             |
    #                                              READS_DB ------+---> Collection
    #                                                             |
    #                                              EMITS_EVENT ---+---> Event
    #                                                                     |
    #                                                    CONSUMES_EVENT --+---> AsyncHandler

    from ontology import Provenance

    store = GraphStore()

    # -- Nodes --
    nodes = [
        GraphNode(id="proj:demo", type=NodeType.PROJECT, name="Demo Project"),
        GraphNode(id="svc:order", type=NodeType.SERVICE, name="Order Service"),
        GraphNode(id="ep:create-order", type=NodeType.ENDPOINT, name="POST /orders"),
        GraphNode(id="fn:process-order", type=NodeType.FUNCTION, name="processOrder"),
        GraphNode(id="col:orders", type=NodeType.COLLECTION, name="orders"),
        GraphNode(id="evt:order-created", type=NodeType.EVENT, name="ORDER_CREATED"),
        GraphNode(id="ah:notify", type=NodeType.ASYNC_HANDLER, name="notifyCustomer"),
        GraphNode(id="fn:validate", type=NodeType.FUNCTION, name="validateOrder"),
    ]
    store.add_nodes(nodes)

    # -- Edges --
    edges = [
        GraphEdge(
            source_id="proj:demo", target_id="svc:order",
            type=EdgeType.CONTAINS,
        ),
        GraphEdge(
            source_id="svc:order", target_id="ep:create-order",
            type=EdgeType.CONTAINS,
        ),
        GraphEdge(
            source_id="ep:create-order", target_id="fn:process-order",
            type=EdgeType.CALLS,
        ),
        GraphEdge(
            source_id="fn:process-order", target_id="col:orders",
            type=EdgeType.WRITES_DB,
        ),
        GraphEdge(
            source_id="fn:process-order", target_id="evt:order-created",
            type=EdgeType.EMITS_EVENT,
        ),
        GraphEdge(
            source_id="ah:notify", target_id="evt:order-created",
            type=EdgeType.CONSUMES_EVENT,
        ),
        GraphEdge(
            source_id="ep:create-order", target_id="fn:validate",
            type=EdgeType.CALLS,
        ),
    ]
    store.add_edges(edges, validate=False)

    # ---- Test 1: smart_cascade from the endpoint (2 hops) ----
    print("--- Test 1: smart_cascade from ep:create-order (hops=2) ---")
    result = smart_cascade(store, "ep:create-order", hops=2)
    print(f"  Trigger:        {result.trigger_node_id}")
    print(f"  Newly stale:    {sorted(result.newly_stale_nodes)}")
    print(f"  Already stale:  {sorted(result.already_stale_nodes)}")
    print(f"  Stale edges:    {len(result.stale_edges)}")
    print(f"  Hops used:      {result.hops_used}")
    print(f"  Total affected: {result.total_affected}")
    print()

    # Structural edges (CONTAINS) should only go 1 hop, so:
    #   ep:create-order -> svc:order (1 structural hop) YES
    #   svc:order -> proj:demo (would be 2nd structural hop) NO
    assert "svc:order" in result.newly_stale_nodes or "svc:order" in result.already_stale_nodes, \
        "svc:order should be stale (1 structural hop from trigger)"
    assert "proj:demo" not in result.newly_stale_nodes, \
        "proj:demo should NOT be stale (structural edges limited to 1 hop)"

    # Operational edges should go full 2 hops:
    #   ep:create-order -CALLS-> fn:process-order (1 hop)
    #   fn:process-order -WRITES_DB-> col:orders (2 hops)
    #   fn:process-order -EMITS_EVENT-> evt:order-created (2 hops)
    assert "fn:process-order" in result.newly_stale_nodes or \
           "fn:process-order" in result.already_stale_nodes, \
        "fn:process-order should be stale (1 operational hop)"
    assert "col:orders" in result.newly_stale_nodes or \
           "col:orders" in result.already_stale_nodes, \
        "col:orders should be stale (2 operational hops)"
    assert "evt:order-created" in result.newly_stale_nodes or \
           "evt:order-created" in result.already_stale_nodes, \
        "evt:order-created should be stale (2 operational hops)"

    # ah:notify CONSUMES evt:order-created, but that would be hop 3 -> should NOT cascade
    assert "ah:notify" not in result.newly_stale_nodes, \
        "ah:notify should NOT be stale (would require 3 hops)"

    print("  [PASS] Structural edges limited to 1 hop")
    print("  [PASS] Operational edges cascade to full 2 hops")
    print("  [PASS] 3rd-hop nodes excluded")
    print()

    # ---- Test 2: propagate_staleness with multiple triggers ----
    print("--- Test 2: propagate_staleness for {fn:validate} (fresh graph) ---")
    # Reset graph staleness
    for n in store._nodes.values():
        n.is_stale = False
    for e in store._edges.values():
        e.is_stale = False

    report = propagate_staleness(store, {"fn:validate"}, hops=2)
    print(f"  Trigger count:      {report.trigger_count}")
    print(f"  Total newly stale:  {report.total_newly_stale}")
    print(f"  Total already stale:{report.total_already_stale}")
    print(f"  Total stale edges:  {report.total_stale_edges}")
    assert report.trigger_count == 1
    assert report.total_newly_stale > 0
    print("  [PASS] propagate_staleness works")
    print()

    # ---- Test 3: get_stale_summary ----
    print("--- Test 3: get_stale_summary ---")
    summary = get_stale_summary(store)
    print(f"  Stale nodes: {summary['total_stale_nodes']} / {summary['total_nodes']}"
          f"  ({summary['stale_node_pct']}%)")
    print(f"  Stale edges: {summary['total_stale_edges']} / {summary['total_edges']}"
          f"  ({summary['stale_edge_pct']}%)")
    print(f"  By type: {summary['stale_nodes_by_type']}")
    print(f"  By tier: {summary['stale_nodes_by_tier']}")
    print("  [PASS] get_stale_summary works")
    print()

    # ---- Test 4: configurable hops ----
    print("--- Test 4: smart_cascade with hops=1 ---")
    # Reset
    for n in store._nodes.values():
        n.is_stale = False
    for e in store._edges.values():
        e.is_stale = False

    result_1hop = smart_cascade(store, "ep:create-order", hops=1)
    print(f"  Newly stale:    {sorted(result_1hop.newly_stale_nodes)}")
    # With hops=1, we should get ep:create-order + 1-hop operational
    # + 1-hop structural neighbours only
    assert "col:orders" not in result_1hop.newly_stale_nodes, \
        "col:orders should NOT be stale at hops=1 (needs 2 operational hops)"
    assert "fn:process-order" in result_1hop.newly_stale_nodes or \
           "fn:process-order" in result_1hop.already_stale_nodes, \
        "fn:process-order should be stale (1 operational hop)"
    print("  [PASS] hops=1 limits cascade correctly")
    print()

    # ---- Test 5: already-stale tracking ----
    print("--- Test 5: already-stale tracking ---")
    # fn:process-order is already stale from Test 4
    result_again = smart_cascade(store, "ep:create-order", hops=2)
    # Some nodes were freshly stale in Test 4 and should now show as already_stale
    assert len(result_again.already_stale_nodes) > 0, \
        "Should detect already-stale nodes from previous cascade"
    print(f"  Newly stale:    {sorted(result_again.newly_stale_nodes)}")
    print(f"  Already stale:  {sorted(result_again.already_stale_nodes)}")
    print("  [PASS] Already-stale nodes tracked correctly")
    print()

    print("=== All smoke tests passed ===")
