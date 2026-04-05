"""
Workspace Intelligence Layer - Importance Ranking
==================================================

Story 5.5: PageRank-style centrality scoring for graph nodes.

Inspired by Aider's approach to identifying architecturally important files:
nodes with high fan-in (many dependents) are structurally critical and should
be weighted higher in context packs and token budget allocation.

Composite score formula:
    composite = 0.5 * pagerank + 0.3 * betweenness + 0.2 * degree_centrality

All individual scores are normalized to the [0, 1] range before blending.
"""

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import networkx as nx

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Tier


# ---------------------------------------------------------------------------
# Composite score weights
# ---------------------------------------------------------------------------

WEIGHTS: Dict[str, float] = {
    "pagerank": 0.5,
    "betweenness": 0.3,
    "degree": 0.2,
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NodeImportance:
    """Centrality metrics for a single graph node."""

    node_id: str
    node_name: str
    node_type: NodeType
    tier: Tier
    pagerank: float        # PageRank score (0-1, normalized)
    in_degree: int         # incoming edge count
    out_degree: int        # outgoing edge count
    betweenness: float     # betweenness centrality (0-1, normalized)
    composite_score: float  # weighted combination of all metrics


@dataclass
class RankingResult:
    """Complete ranking output for the entire graph."""

    rankings: List[NodeImportance]  # sorted by composite_score descending
    computation_ms: float
    total_nodes: int
    top_10_summary: str             # human-readable summary


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

_cache: Dict[int, Tuple[RankingResult, float]] = {}
_CACHE_TTL_S = 30.0  # seconds before a cached result is considered stale


def _cache_key(store: GraphStore) -> int:
    """Derive a lightweight cache key from node/edge counts and node ids hash."""
    node_ids = tuple(sorted(store._nodes.keys()))
    return hash((len(store._nodes), len(store._edges), node_ids))


def _get_cached(store: GraphStore) -> Optional[RankingResult]:
    """Return a cached RankingResult if still fresh, else None."""
    key = _cache_key(store)
    entry = _cache.get(key)
    if entry is None:
        return None
    result, ts = entry
    if (time.monotonic() - ts) > _CACHE_TTL_S:
        del _cache[key]
        return None
    return result


def _set_cached(store: GraphStore, result: RankingResult) -> None:
    """Store a RankingResult in the cache."""
    key = _cache_key(store)
    _cache[key] = (result, time.monotonic())


def invalidate_cache() -> None:
    """Clear the entire ranking cache.  Useful after graph mutations."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize(scores: Dict[str, float]) -> Dict[str, float]:
    """Normalize a dict of scores to [0, 1] range (max becomes 1.0)."""
    if not scores:
        return scores
    max_val = max(scores.values())
    if max_val == 0:
        return {k: 0.0 for k in scores}
    return {k: v / max_val for k, v in scores.items()}


# ---------------------------------------------------------------------------
# Core: compute_importance
# ---------------------------------------------------------------------------

def compute_importance(
    store: GraphStore,
    alpha: float = 0.85,
    *,
    use_cache: bool = True,
) -> RankingResult:
    """
    Run PageRank + betweenness + degree centrality on the graph and produce
    a composite importance ranking for every node.

    Args:
        store:     The GraphStore containing the workspace graph.
        alpha:     PageRank damping factor (probability of following an edge).
        use_cache: If True, return a cached result when available.

    Returns:
        RankingResult with nodes sorted by composite_score descending.
    """
    # Cache check
    if use_cache:
        cached = _get_cached(store)
        if cached is not None:
            return cached

    t0 = time.perf_counter()

    G = store.graph
    node_ids = list(G.nodes)
    n = len(node_ids)

    # -- Trivial / empty graph: uniform scores --
    if n < 3:
        rankings = _uniform_rankings(store)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        result = RankingResult(
            rankings=rankings,
            computation_ms=round(elapsed_ms, 2),
            total_nodes=n,
            top_10_summary=_build_summary(rankings[:10]),
        )
        _set_cached(store, result)
        return result

    # -- PageRank --
    try:
        raw_pr = nx.pagerank(G, alpha=alpha)
    except (nx.PowerIterationFailedConvergence, ModuleNotFoundError, ImportError):
        # scipy not installed or convergence failed — fall back to degree centrality
        raw_pr = nx.in_degree_centrality(G)

    # -- Betweenness centrality --
    raw_bw = nx.betweenness_centrality(G)

    # -- Degree centrality (in-degree on a DiGraph) --
    raw_dc = nx.in_degree_centrality(G)

    # Normalize each metric to [0, 1]
    norm_pr = _normalize(raw_pr)
    norm_bw = _normalize(raw_bw)
    norm_dc = _normalize(raw_dc)

    # -- Build NodeImportance list --
    rankings: List[NodeImportance] = []
    for nid in node_ids:
        node: Optional[GraphNode] = store.get_node(nid)
        if node is None:
            # Node exists in NetworkX but not in _nodes dict (shouldn't happen,
            # but defensive).
            continue

        pr = norm_pr.get(nid, 0.0)
        bw = norm_bw.get(nid, 0.0)
        dc = norm_dc.get(nid, 0.0)

        composite = (
            WEIGHTS["pagerank"] * pr
            + WEIGHTS["betweenness"] * bw
            + WEIGHTS["degree"] * dc
        )

        rankings.append(NodeImportance(
            node_id=nid,
            node_name=node.name,
            node_type=node.type,
            tier=node.tier,
            pagerank=round(pr, 6),
            in_degree=G.in_degree(nid),
            out_degree=G.out_degree(nid),
            betweenness=round(bw, 6),
            composite_score=round(composite, 6),
        ))

    # Sort descending by composite score
    rankings.sort(key=lambda r: r.composite_score, reverse=True)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    result = RankingResult(
        rankings=rankings,
        computation_ms=round(elapsed_ms, 2),
        total_nodes=n,
        top_10_summary=_build_summary(rankings[:10]),
    )

    _set_cached(store, result)
    return result


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def get_top_nodes(
    store: GraphStore,
    n: int = 10,
    tier: Optional[Tier] = None,
) -> List[NodeImportance]:
    """
    Get the top *n* most important nodes, optionally filtered by tier.

    Args:
        store: The GraphStore.
        n:     How many nodes to return.
        tier:  If provided, only include nodes in this tier.

    Returns:
        List of NodeImportance sorted by composite_score descending.
    """
    result = compute_importance(store)
    rankings = result.rankings
    if tier is not None:
        rankings = [r for r in rankings if r.tier == tier]
    return rankings[:n]


def get_importance(
    store: GraphStore,
    node_id: str,
) -> Optional[NodeImportance]:
    """
    Get the importance metrics for a single node.

    Returns None if the node is not in the graph.
    """
    result = compute_importance(store)
    for r in result.rankings:
        if r.node_id == node_id:
            return r
    return None


def rank_nodes_for_context(
    store: GraphStore,
    node_ids: List[str],
) -> List[str]:
    """
    Sort a list of node IDs by importance (most important first).

    Useful for prioritizing which nodes to include in a ContextPack when
    operating under a token budget.  Nodes not present in the graph are
    placed at the end in their original order.

    Args:
        store:    The GraphStore.
        node_ids: Node IDs to rank.

    Returns:
        The same node IDs reordered by descending composite score.
    """
    result = compute_importance(store)
    score_map: Dict[str, float] = {
        r.node_id: r.composite_score for r in result.rankings
    }

    # Partition into known (scored) and unknown
    known = [nid for nid in node_ids if nid in score_map]
    unknown = [nid for nid in node_ids if nid not in score_map]

    known.sort(key=lambda nid: score_map[nid], reverse=True)
    return known + unknown


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_rankings(result: RankingResult, limit: int = 20) -> None:
    """
    Pretty-print the ranking table to stdout.

    All output uses ASCII characters only (safe for Windows cp1252 consoles).
    """
    header = (
        f"{'Rank':<5} "
        f"{'Composite':>10} "
        f"{'PageRank':>10} "
        f"{'Between.':>10} "
        f"{'In':>4} "
        f"{'Out':>4} "
        f"{'Tier':<6} "
        f"{'Type':<16} "
        f"{'Name'}"
    )
    sep = "-" * len(header)

    print()
    print("=== Importance Rankings ===")
    print(f"Total nodes: {result.total_nodes}  |  Computed in {result.computation_ms:.1f} ms")
    print(sep)
    print(header)
    print(sep)

    shown = min(limit, len(result.rankings))
    for i, r in enumerate(result.rankings[:shown], start=1):
        print(
            f"{i:<5} "
            f"{r.composite_score:>10.6f} "
            f"{r.pagerank:>10.6f} "
            f"{r.betweenness:>10.6f} "
            f"{r.in_degree:>4} "
            f"{r.out_degree:>4} "
            f"{r.tier.value:<6} "
            f"{r.node_type.value:<16} "
            f"{r.node_name}"
        )

    if shown < len(result.rankings):
        print(f"... and {len(result.rankings) - shown} more nodes")
    print(sep)
    print()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _uniform_rankings(store: GraphStore) -> List[NodeImportance]:
    """
    Produce uniform (equal) scores for trivially small graphs (<3 nodes).
    """
    G = store.graph
    n = len(G.nodes)
    uniform = 1.0 / n if n > 0 else 0.0

    rankings: List[NodeImportance] = []
    for nid in G.nodes:
        node = store.get_node(nid)
        if node is None:
            continue
        rankings.append(NodeImportance(
            node_id=nid,
            node_name=node.name,
            node_type=node.type,
            tier=node.tier,
            pagerank=round(uniform, 6),
            in_degree=G.in_degree(nid),
            out_degree=G.out_degree(nid),
            betweenness=0.0,
            composite_score=round(uniform, 6),
        ))

    rankings.sort(key=lambda r: r.composite_score, reverse=True)
    return rankings


def _build_summary(top: List[NodeImportance]) -> str:
    """Build a compact human-readable summary of the top-ranked nodes."""
    if not top:
        return "(empty graph)"
    lines = []
    for i, r in enumerate(top, start=1):
        lines.append(
            f"  {i}. {r.node_name} ({r.node_type.value}, {r.tier.value}) "
            f"- score {r.composite_score:.4f}"
        )
    return "Top important nodes:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Demo graph builder (only used by __main__)
# ---------------------------------------------------------------------------

def _build_demo_graph(store: GraphStore) -> None:
    """
    Populate a GraphStore with a realistic demo graph for testing rankings.

    Topology:
      - 1 Project (root)
      - 2 Services
      - 3 Modules
      - 6 Functions (with varying fan-in/out)
      - 2 DataModels
      - 1 Collection
      - 1 Endpoint
      - Various edges creating a non-trivial topology
    """
    from ontology import Provenance

    # -- Nodes --
    nodes_data = [
        ("project:demo", NodeType.PROJECT, "DemoProject"),
        ("service:api", NodeType.SERVICE, "ApiService"),
        ("service:worker", NodeType.SERVICE, "WorkerService"),
        ("module:auth", NodeType.MODULE, "AuthModule"),
        ("module:orders", NodeType.MODULE, "OrdersModule"),
        ("module:shared", NodeType.MODULE, "SharedModule"),
        ("function:login", NodeType.FUNCTION, "login"),
        ("function:validate_token", NodeType.FUNCTION, "validate_token"),
        ("function:create_order", NodeType.FUNCTION, "create_order"),
        ("function:calc_total", NodeType.FUNCTION, "calc_total"),
        ("function:send_email", NodeType.FUNCTION, "send_email"),
        ("function:log_event", NodeType.FUNCTION, "log_event"),
        ("model:user", NodeType.DATA_MODEL, "UserModel"),
        ("model:order", NodeType.DATA_MODEL, "OrderModel"),
        ("collection:users", NodeType.COLLECTION, "users"),
        ("endpoint:post_order", NodeType.ENDPOINT, "POST /orders"),
    ]
    for nid, ntype, name in nodes_data:
        store.add_node(GraphNode(
            id=nid,
            type=ntype,
            name=name,
            provenance=Provenance.SCANNER,
        ))

    # -- Edges (creating intentional fan-in on log_event and validate_token) --
    edges_data = [
        # Structural
        ("project:demo", "service:api", EdgeType.CONTAINS),
        ("project:demo", "service:worker", EdgeType.CONTAINS),
        ("service:api", "module:auth", EdgeType.CONTAINS),
        ("service:api", "module:orders", EdgeType.CONTAINS),
        ("service:api", "module:shared", EdgeType.CONTAINS),
        ("module:auth", "function:login", EdgeType.CONTAINS),
        ("module:auth", "function:validate_token", EdgeType.CONTAINS),
        ("module:orders", "function:create_order", EdgeType.CONTAINS),
        ("module:orders", "function:calc_total", EdgeType.CONTAINS),
        ("module:shared", "function:send_email", EdgeType.CONTAINS),
        ("module:shared", "function:log_event", EdgeType.CONTAINS),
        # Calls -- log_event has high fan-in (everyone calls it)
        ("function:login", "function:validate_token", EdgeType.CALLS),
        ("function:login", "function:log_event", EdgeType.CALLS),
        ("function:create_order", "function:calc_total", EdgeType.CALLS),
        ("function:create_order", "function:validate_token", EdgeType.CALLS),
        ("function:create_order", "function:log_event", EdgeType.CALLS),
        ("function:create_order", "function:send_email", EdgeType.CALLS),
        ("function:send_email", "function:log_event", EdgeType.CALLS),
        ("function:calc_total", "function:log_event", EdgeType.CALLS),
        # Data flow
        ("function:login", "collection:users", EdgeType.READS_DB),
        ("function:create_order", "collection:users", EdgeType.READS_DB),
        # Endpoint routing
        ("endpoint:post_order", "function:create_order", EdgeType.CALLS),
    ]
    for src, tgt, etype in edges_data:
        store.add_edge(GraphEdge(
            source_id=src,
            target_id=tgt,
            type=etype,
        ), validate=False)

    print(f"  Demo graph: {len(nodes_data)} nodes, {len(edges_data)} edges")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Compute importance rankings for a workspace graph.",
    )
    parser.add_argument(
        "graph_file",
        nargs="?",
        default=None,
        help="Path to a graph JSON file (saved by GraphStore.save).  "
             "If omitted, a demo graph is generated.",
    )
    parser.add_argument(
        "-n", "--top",
        type=int,
        default=20,
        help="Number of top nodes to display (default: 20).",
    )
    parser.add_argument(
        "--tier",
        choices=["macro", "meso", "micro"],
        default=None,
        help="Filter results by tier.",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.85,
        help="PageRank damping factor (default: 0.85).",
    )
    args = parser.parse_args()

    # -- Load or build graph --
    store = GraphStore()

    if args.graph_file:
        graph_path = Path(args.graph_file)
        if not graph_path.exists():
            print(f"ERROR: File not found: {graph_path}")
            sys.exit(1)
        print(f"Loading graph from {graph_path} ...")
        store.load(graph_path)
    else:
        # Build a demo graph so the script is runnable without a real workspace
        print("No graph file provided -- building demo graph ...")
        _build_demo_graph(store)

    # -- Compute --
    tier_filter = None
    if args.tier:
        tier_filter = Tier(args.tier)

    if tier_filter:
        top = get_top_nodes(store, n=args.top, tier=tier_filter)
        # Wrap in a pseudo-result for printing
        result = RankingResult(
            rankings=top,
            computation_ms=0.0,
            total_nodes=len(top),
            top_10_summary=_build_summary(top[:10]),
        )
    else:
        result = compute_importance(store, alpha=args.alpha)

    print_rankings(result, limit=args.top)

    # -- Demo: rank_nodes_for_context --
    if not args.graph_file:
        sample_ids = [r.node_id for r in result.rankings[:5]]
        # Reverse so we can show re-ordering
        sample_ids.reverse()
        print("rank_nodes_for_context demo:")
        print(f"  Input order:  {sample_ids}")
        ranked = rank_nodes_for_context(store, sample_ids)
        print(f"  Ranked order: {ranked}")
        print()
