"""
Workspace Intelligence Layer - Ranking Package

Story 5.5: PageRank-style importance ranking for graph nodes.

Computes centrality scores to identify architecturally important nodes.
High fan-in nodes (many things depend on them) score higher.
Used to weight context pack results and prioritize token budget allocation.

Centrality measures:
  - PageRank:     Random-walk importance (like Google's original algorithm)
  - Betweenness:  How often a node sits on shortest paths between others
  - Degree:       Simple in-degree / out-degree counts
  - Composite:    Weighted blend of all three (default ranking)
"""

from .importance import (
    NodeImportance,
    RankingResult,
    WEIGHTS,
    compute_importance,
    get_top_nodes,
    get_importance,
    rank_nodes_for_context,
    print_rankings,
)

__all__ = [
    "NodeImportance",
    "RankingResult",
    "WEIGHTS",
    "compute_importance",
    "get_top_nodes",
    "get_importance",
    "rank_nodes_for_context",
    "print_rankings",
]
