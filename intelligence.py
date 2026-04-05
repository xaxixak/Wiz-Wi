"""
Graph Intelligence - PageRank, Community Detection, Orphan Analysis, Architecture Score

Computes intelligence metrics directly from WI graph JSON files.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

import networkx as nx


# Edge types that represent structural containment (not semantic connections)
_STRUCTURAL_EDGE_TYPES = {"CONTAINS", "DEFINES"}

# Node types excluded from orphan analysis (they are root containers)
_ROOT_NODE_TYPES = {"Workspace", "Project"}

# Node types included in community detection (coarse-grained view)
_COMMUNITY_NODE_TYPES = {"Module", "File"}


class GraphIntelligence:
    """Compute intelligence metrics from a WI graph JSON file."""

    def __init__(self, graph_path: str):
        self._path = Path(graph_path)
        self._data: Dict[str, Any] = {}
        self._graph = nx.DiGraph()
        self._nodes: Dict[str, dict] = {}
        self._edges: List[dict] = []
        self._load()

    def _load(self):
        with open(self._path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        for node in self._data.get("nodes", []):
            nid = node["id"]
            self._nodes[nid] = node
            self._graph.add_node(nid, **{k: v for k, v in node.items() if k != "id"})

        for edge in self._data.get("edges", []):
            self._edges.append(edge)
            self._graph.add_edge(
                edge["source_id"],
                edge["target_id"],
                type=edge.get("type", ""),
                weight=edge.get("weight", 0.5),
            )

    def pagerank(self) -> Dict[str, float]:
        """Compute PageRank for all nodes."""
        if self._graph.number_of_nodes() == 0:
            return {}
        try:
            pr = nx.pagerank(self._graph, weight="weight")
            # Round for clean JSON output
            return {nid: round(score, 6) for nid, score in pr.items()}
        except (nx.NetworkXError, nx.PowerIterationFailedConvergence, Exception):
            return {}

    def communities(self) -> Dict[str, int]:
        """Detect communities using Louvain on FILE/MODULE nodes."""
        if self._graph.number_of_nodes() == 0:
            return {}

        # Build undirected subgraph of FILE + MODULE nodes only
        eligible = {
            nid for nid, data in self._nodes.items()
            if data.get("type") in _COMMUNITY_NODE_TYPES
        }

        if len(eligible) < 2:
            # Assign all to community 0
            return {nid: 0 for nid in eligible}

        subgraph = self._graph.subgraph(eligible).to_undirected()

        # Remove isolates from community detection
        connected = {n for n in subgraph.nodes() if subgraph.degree(n) > 0}
        isolates = eligible - connected

        if not connected:
            return {nid: 0 for nid in eligible}

        sub_connected = subgraph.subgraph(connected)

        try:
            communities_list = nx.community.louvain_communities(
                sub_connected, seed=42
            )
        except Exception:
            return {nid: 0 for nid in eligible}

        result = {}
        for idx, community in enumerate(communities_list):
            for nid in community:
                result[nid] = idx

        # Assign isolates to their own community id
        next_id = len(communities_list)
        for nid in isolates:
            result[nid] = next_id

        return result

    def orphans(self) -> List[str]:
        """Find orphan nodes - nodes with no semantic edges (only CONTAINS/DEFINES)."""
        orphan_ids = []
        for nid, node in self._nodes.items():
            if node.get("type") in _ROOT_NODE_TYPES:
                continue

            # Check if node has any non-structural edges
            has_semantic = False
            for _, _, edata in self._graph.out_edges(nid, data=True):
                if edata.get("type") not in _STRUCTURAL_EDGE_TYPES:
                    has_semantic = True
                    break
            if not has_semantic:
                for _, _, edata in self._graph.in_edges(nid, data=True):
                    if edata.get("type") not in _STRUCTURAL_EDGE_TYPES:
                        has_semantic = True
                        break

            if not has_semantic:
                orphan_ids.append(nid)

        return orphan_ids

    def architecture_score(self) -> Dict[str, Any]:
        """
        Calculate architecture health score 0-100.

        Components:
        - depth_score: max depth from project root (optimal 3-5, penalty for >7)
        - coupling_score: cross-module edges / total edges (< 30% is healthy)
        - cohesion_score: intra-module edges / module size (> 60% healthy)
        - orphan_penalty: % orphan nodes * 20
        - cycle_penalty: number of import cycles * 5
        """
        total_nodes = self._graph.number_of_nodes()
        if total_nodes == 0:
            return {
                "total": 0,
                "depth": 0,
                "coupling": 0,
                "cohesion": 0,
                "orphan_penalty": 0,
                "cycle_penalty": 0,
            }

        depth = self._compute_depth_score()
        coupling = self._compute_coupling_score()
        cohesion = self._compute_cohesion_score()
        orphan_pen = self._compute_orphan_penalty()
        cycle_pen = self._compute_cycle_penalty()

        raw = (depth + coupling + cohesion) / 3.0 - orphan_pen - cycle_pen
        total = max(0, min(100, round(raw)))

        return {
            "total": total,
            "depth": round(depth),
            "coupling": round(coupling),
            "cohesion": round(cohesion),
            "orphan_penalty": round(orphan_pen),
            "cycle_penalty": round(cycle_pen),
        }

    def _compute_depth_score(self) -> float:
        """Score based on max containment depth. Optimal 3-5, penalty for >7."""
        # Build containment tree to find max depth
        contains_edges = [
            e for e in self._edges if e.get("type") == "CONTAINS"
        ]
        if not contains_edges:
            return 50.0

        # Find roots (nodes with no incoming CONTAINS)
        children = {e["target_id"] for e in contains_edges}
        parents = {e["source_id"] for e in contains_edges}
        roots = parents - children

        if not roots:
            return 50.0

        # BFS from roots to find max depth
        containment = nx.DiGraph()
        for e in contains_edges:
            containment.add_edge(e["source_id"], e["target_id"])

        max_depth = 0
        for root in roots:
            lengths = nx.single_source_shortest_path_length(containment, root)
            if lengths:
                max_depth = max(max_depth, max(lengths.values()))

        # Score: optimal 3-5, penalty for deviations
        if 3 <= max_depth <= 5:
            return 100.0
        elif max_depth < 3:
            return 70.0 + (max_depth / 3.0) * 30.0
        elif max_depth <= 7:
            return 100.0 - (max_depth - 5) * 15.0
        else:
            return max(20.0, 100.0 - (max_depth - 5) * 15.0)

    def _compute_coupling_score(self) -> float:
        """Score based on cross-module edges ratio. < 30% cross-module = healthy."""
        # Build module membership: map each node to its parent module
        module_map = {}
        for nid, node in self._nodes.items():
            parent = node.get("parent_id")
            if parent and self._nodes.get(parent, {}).get("type") == "Module":
                module_map[nid] = parent
            elif node.get("type") == "Module":
                module_map[nid] = nid

        semantic_edges = [
            e for e in self._edges
            if e.get("type") not in _STRUCTURAL_EDGE_TYPES
        ]

        if not semantic_edges:
            return 80.0  # No semantic edges = no coupling problems

        cross_module = 0
        for e in semantic_edges:
            src_mod = module_map.get(e["source_id"])
            tgt_mod = module_map.get(e["target_id"])
            if src_mod and tgt_mod and src_mod != tgt_mod:
                cross_module += 1

        ratio = cross_module / len(semantic_edges)

        # < 30% is healthy (score 100), > 60% is bad (score 0)
        if ratio <= 0.3:
            return 100.0
        elif ratio >= 0.6:
            return max(0.0, 100.0 - (ratio - 0.3) * 333.0)
        else:
            return 100.0 - (ratio - 0.3) * 333.0

    def _compute_cohesion_score(self) -> float:
        """Score based on intra-module connectivity."""
        # Group nodes by module
        modules: Dict[str, List[str]] = {}
        for nid, node in self._nodes.items():
            parent = node.get("parent_id")
            if parent and self._nodes.get(parent, {}).get("type") == "Module":
                modules.setdefault(parent, []).append(nid)

        if not modules:
            return 50.0

        cohesion_scores = []
        for mod_id, members in modules.items():
            if len(members) < 2:
                continue

            member_set = set(members)
            # Count intra-module semantic edges
            intra_edges = 0
            for e in self._edges:
                if e.get("type") in _STRUCTURAL_EDGE_TYPES:
                    continue
                if e["source_id"] in member_set and e["target_id"] in member_set:
                    intra_edges += 1

            # Max possible edges
            max_edges = len(members) * (len(members) - 1)
            if max_edges > 0:
                cohesion_scores.append(intra_edges / max_edges)

        if not cohesion_scores:
            return 50.0

        avg_cohesion = sum(cohesion_scores) / len(cohesion_scores)

        # > 60% cohesion = 100 score, 0% = 30 score
        return min(100.0, 30.0 + avg_cohesion * 116.7)

    def _compute_orphan_penalty(self) -> float:
        """Penalty based on percentage of orphan nodes."""
        orphan_ids = self.orphans()
        non_root = [
            n for n in self._nodes.values()
            if n.get("type") not in _ROOT_NODE_TYPES
        ]
        if not non_root:
            return 0.0

        orphan_ratio = len(orphan_ids) / len(non_root)
        return orphan_ratio * 20.0

    def _compute_cycle_penalty(self) -> float:
        """Penalty based on import cycles."""
        # Build import-only graph
        import_graph = nx.DiGraph()
        for e in self._edges:
            if e.get("type") == "IMPORTS":
                import_graph.add_edge(e["source_id"], e["target_id"])

        if import_graph.number_of_nodes() == 0:
            return 0.0

        try:
            cycles = list(nx.simple_cycles(import_graph))
            return min(25.0, len(cycles) * 5.0)
        except Exception:
            return 0.0

    def all_metrics(self) -> Dict[str, Any]:
        """Return all metrics as one JSON-serializable dict."""
        pr = self.pagerank()
        comm = self.communities()
        orph = self.orphans()
        arch = self.architecture_score()

        return {
            "pagerank": pr,
            "communities": comm,
            "orphans": orph,
            "architecture_score": arch,
            "stats": {
                "total_nodes": self._graph.number_of_nodes(),
                "total_edges": self._graph.number_of_edges(),
                "community_count": len(set(comm.values())) if comm else 0,
                "orphan_count": len(orph),
            },
        }
