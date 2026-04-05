"""
Workspace Intelligence Layer - Graph Store

In-memory graph storage using NetworkX for MVP.
Provides CRUD operations for nodes and edges.
"""

import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Set
import networkx as nx

from ontology import (
    GraphNode, GraphEdge, NodeType, EdgeType, ContextPack,
    Tier, Provenance, SourceLocation, validate_edge_with_nodes,
)


class GraphStore:
    """
    In-memory graph storage using NetworkX.

    For MVP/prototyping. Can be swapped for Neo4j/FalkorDB in production.
    """

    def __init__(self):
        self.graph = nx.DiGraph()
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: Dict[str, GraphEdge] = {}  # key: "{source_id}->{target_id}:{type}"

    # =========================================================================
    # NODE OPERATIONS
    # =========================================================================

    def add_node(self, node: GraphNode) -> None:
        """Add or update a node in the graph."""
        self._nodes[node.id] = node
        self.graph.add_node(
            node.id,
            type=node.type.value,
            name=node.name,
            description=node.description,
            confidence=node.confidence,
            is_stale=node.is_stale,
            provenance=node.provenance.value,
            source_hash=node.source_hash,
            language=node.language,
            tags=list(node.tags),
            parent_id=node.parent_id,
            version=node.version,
            tier=node.tier.value,
            metadata=node.metadata,
        )

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Retrieve a node by ID."""
        return self._nodes.get(node_id)

    def get_nodes_by_type(self, node_type: NodeType) -> List[GraphNode]:
        """Get all nodes of a specific type."""
        return [n for n in self._nodes.values() if n.type == node_type]

    def get_nodes_by_tier(self, tier: Tier) -> List[GraphNode]:
        """Get all nodes in a specific tier (macro/meso/micro)."""
        return [n for n in self._nodes.values() if n.tier == tier]

    def get_nodes_by_tag(self, tag: str) -> List[GraphNode]:
        """Get all nodes with a specific tag."""
        return [n for n in self._nodes.values() if tag in n.tags]

    def get_children(self, parent_id: str) -> List[GraphNode]:
        """Get all nodes whose parent_id matches."""
        return [n for n in self._nodes.values() if n.parent_id == parent_id]

    def mark_stale(self, node_id: str) -> None:
        """Mark a node as stale (needs re-indexing)."""
        if node_id in self._nodes:
            self._nodes[node_id].is_stale = True
            self.graph.nodes[node_id]["is_stale"] = True

    def delete_node(self, node_id: str) -> None:
        """Delete a node and all its edges."""
        if node_id in self._nodes:
            del self._nodes[node_id]
            self.graph.remove_node(node_id)
            # Clean up edges
            self._edges = {
                k: v for k, v in self._edges.items()
                if v.source_id != node_id and v.target_id != node_id
            }

    # =========================================================================
    # EDGE OPERATIONS
    # =========================================================================

    def add_edge(self, edge: GraphEdge, validate: bool = True) -> List[str]:
        """
        Add or update an edge in the graph.

        Args:
            edge: The edge to add.
            validate: If True, validate against EDGE_CONSTRAINTS (advisory).

        Returns:
            List of validation violation messages (empty if valid or validate=False).
        """
        violations = []
        if validate:
            source = self.get_node(edge.source_id)
            target = self.get_node(edge.target_id)
            if source and target:
                violations = validate_edge_with_nodes(edge, source, target)

        edge_key = f"{edge.source_id}->{edge.target_id}:{edge.type.value}"
        self._edges[edge_key] = edge
        self.graph.add_edge(
            edge.source_id,
            edge.target_id,
            key=edge_key,
            type=edge.type.value,
            description=edge.description,
            confidence=edge.confidence,
            provenance=edge.provenance.value,
            is_stale=edge.is_stale,
            weight=edge.weight,
            conditional=edge.conditional,
            metadata=edge.metadata,
        )
        return violations

    def get_edges_from(self, node_id: str) -> List[GraphEdge]:
        """Get all edges originating from a node."""
        return [e for e in self._edges.values() if e.source_id == node_id]

    def get_edges_to(self, node_id: str) -> List[GraphEdge]:
        """Get all edges pointing to a node."""
        return [e for e in self._edges.values() if e.target_id == node_id]

    def get_edges_by_type(self, edge_type: EdgeType) -> List[GraphEdge]:
        """Get all edges of a specific type."""
        return [e for e in self._edges.values() if e.type == edge_type]

    def get_edges_between(self, source_id: str, target_id: str) -> List[GraphEdge]:
        """Get all edges between two specific nodes."""
        return [
            e for e in self._edges.values()
            if e.source_id == source_id and e.target_id == target_id
        ]

    def mark_edge_stale(self, source_id: str, target_id: str, edge_type: EdgeType) -> None:
        """Mark an edge as stale."""
        edge_key = f"{source_id}->{target_id}:{edge_type.value}"
        if edge_key in self._edges:
            self._edges[edge_key].is_stale = True
            if self.graph.has_edge(source_id, target_id):
                self.graph[source_id][target_id]["is_stale"] = True

    # =========================================================================
    # TRAVERSAL
    # =========================================================================

    def get_upstream(self, node_id: str, max_depth: int = 3) -> List[GraphNode]:
        """Get all nodes that point TO this node (callers, dependencies)."""
        upstream_ids = set()
        self._traverse_upstream(node_id, 0, max_depth, upstream_ids)
        return [self._nodes[nid] for nid in upstream_ids if nid in self._nodes]

    def get_downstream(self, node_id: str, max_depth: int = 3) -> List[GraphNode]:
        """Get all nodes that this node points TO (callees, dependents)."""
        downstream_ids = set()
        self._traverse_downstream(node_id, 0, max_depth, downstream_ids)
        return [self._nodes[nid] for nid in downstream_ids if nid in self._nodes]

    def _traverse_upstream(self, node_id: str, depth: int, max_depth: int, visited: set):
        if depth >= max_depth or node_id in visited:
            return
        for pred in self.graph.predecessors(node_id):
            if pred not in visited:
                visited.add(pred)
                self._traverse_upstream(pred, depth + 1, max_depth, visited)

    def _traverse_downstream(self, node_id: str, depth: int, max_depth: int, visited: set):
        if depth >= max_depth or node_id in visited:
            return
        for succ in self.graph.successors(node_id):
            if succ not in visited:
                visited.add(succ)
                self._traverse_downstream(succ, depth + 1, max_depth, visited)

    def get_subgraph(self, node_id: str, max_depth: int = 2) -> "GraphStore":
        """Extract a subgraph centered on a node (upstream + downstream)."""
        all_ids = {node_id}
        upstream_ids = set()
        downstream_ids = set()
        self._traverse_upstream(node_id, 0, max_depth, upstream_ids)
        self._traverse_downstream(node_id, 0, max_depth, downstream_ids)
        all_ids.update(upstream_ids, downstream_ids)

        sub = GraphStore()
        for nid in all_ids:
            node = self.get_node(nid)
            if node:
                sub.add_node(node)
        for edge in self._edges.values():
            if edge.source_id in all_ids and edge.target_id in all_ids:
                sub.add_edge(edge, validate=False)
        return sub

    # =========================================================================
    # BATCH OPERATIONS
    # =========================================================================

    def add_nodes(self, nodes: List[GraphNode]) -> None:
        """Add multiple nodes at once."""
        for node in nodes:
            self.add_node(node)

    def add_edges(self, edges: List[GraphEdge], validate: bool = True) -> Dict[str, List[str]]:
        """
        Add multiple edges at once.

        Returns:
            Dict of edge_key -> violations for edges with constraint violations.
        """
        all_violations = {}
        for edge in edges:
            violations = self.add_edge(edge, validate=validate)
            if violations:
                edge_key = f"{edge.source_id}->{edge.target_id}:{edge.type.value}"
                all_violations[edge_key] = violations
        return all_violations

    # =========================================================================
    # STALE CASCADE
    # =========================================================================

    def cascade_stale(self, node_id: str, hops: int = 2) -> List[str]:
        """
        Mark a node and its N-hop neighbors as stale.

        Returns list of all node IDs marked stale.
        """
        stale_ids = set()
        self._cascade_stale_impl(node_id, 0, hops, stale_ids)
        for nid in stale_ids:
            self.mark_stale(nid)
        return list(stale_ids)

    def _cascade_stale_impl(self, node_id: str, depth: int, max_depth: int, visited: set):
        if depth > max_depth or node_id in visited:
            return
        visited.add(node_id)
        # Stale cascade goes both directions
        for neighbor in self.graph.predecessors(node_id):
            self._cascade_stale_impl(neighbor, depth + 1, max_depth, visited)
        for neighbor in self.graph.successors(node_id):
            self._cascade_stale_impl(neighbor, depth + 1, max_depth, visited)

    # =========================================================================
    # CONTEXT PACK (SKILL API)
    # =========================================================================

    def get_context(
        self,
        scope: str,
        focus: str,
        max_depth: int = 3,
        max_tokens: int = 0,
    ) -> ContextPack:
        """
        Generate a context pack for the Skill API.

        Args:
            scope: Node ID or pattern to focus on (e.g., "service:order-api")
            focus: Task description (e.g., "Refactoring database schema")
            max_depth: How many hops to traverse
            max_tokens: Token budget (0 = unlimited). When set, truncates
                        context to fit within the budget, prioritizing:
                        1. Target node + direct connections (depth 1)
                        2. Stale warnings + risk assessment
                        3. Deeper connections (depth 2+)
                        4. Code snippets

        Returns:
            ContextPack with relevant nodes, edges, upstream, downstream, and risk.
        """
        # Find the target node
        target = self.get_node(scope)
        if not target:
            # Try fuzzy match on name
            matches = [n for n in self._nodes.values() if scope.lower() in n.name.lower()]
            target = matches[0] if matches else None

        if not target:
            return ContextPack(scope=scope, focus=focus)

        upstream = self.get_upstream(target.id, max_depth)
        downstream = self.get_downstream(target.id, max_depth)

        # Collect all relevant node IDs for edge filtering
        all_node_ids = {target.id}
        all_node_ids.update(n.id for n in upstream)
        all_node_ids.update(n.id for n in downstream)

        # Gather edges between relevant nodes
        relevant_edges = [
            e for e in self._edges.values()
            if e.source_id in all_node_ids and e.target_id in all_node_ids
        ]

        # Collect related files
        related_files = []
        for node in [target] + upstream + downstream:
            if node.location and node.location not in related_files:
                related_files.append(node.location)

        # Stale warnings
        stale_warnings = []
        for node in [target] + upstream + downstream:
            if node.is_stale:
                stale_warnings.append(
                    f"WARNING: {node.type.value} '{node.name}' is stale (needs re-indexing)"
                )
        for edge in relevant_edges:
            if edge.is_stale:
                stale_warnings.append(
                    f"WARNING: Edge {edge.type.value} from {edge.source_id} -> {edge.target_id} is stale"
                )

        # Risk assessment
        risk = None
        if len(upstream) > 5:
            risk = f"High Risk: {len(upstream)} components depend on this."
        elif len(downstream) > 10:
            risk = f"Medium Risk: This touches {len(downstream)} downstream nodes."

        pack = ContextPack(
            scope=scope,
            focus=focus,
            relevant_nodes=[target],
            relevant_edges=relevant_edges,
            upstream=upstream,
            downstream=downstream,
            related_files=related_files,
            stale_warnings=stale_warnings,
            risk_assessment=risk,
            depth=max_depth,
            total_nodes_in_scope=len(all_node_ids),
        )

        # Apply token budget if set
        if max_tokens > 0:
            pack = self._apply_token_budget(pack, max_tokens)

        return pack

    @staticmethod
    def _estimate_tokens(obj) -> int:
        """Rough token estimate: ~4 chars per token for JSON-serialized data."""
        try:
            text = json.dumps(obj, default=str)
            return len(text) // 4
        except Exception:
            return 0

    def _apply_token_budget(self, pack: ContextPack, max_tokens: int) -> ContextPack:
        """
        Truncate a ContextPack to fit within a token budget.

        Priority order (highest to lowest):
          1. Scope, focus, risk, stale warnings (always kept)
          2. Target node (relevant_nodes)
          3. Direct upstream/downstream (depth 1)
          4. Relevant edges
          5. Related files
          6. Deeper upstream/downstream (depth 2+)
          7. Code snippets (trimmed last)
        """
        import json

        # Always keep: scope, focus, risk, stale_warnings, metadata
        base_tokens = self._estimate_tokens({
            "scope": pack.scope,
            "focus": pack.focus,
            "risk_assessment": pack.risk_assessment,
            "stale_warnings": pack.stale_warnings,
            "depth": pack.depth,
            "total_nodes_in_scope": pack.total_nodes_in_scope,
        })
        remaining = max_tokens - base_tokens

        # 1. Target node
        node_tokens = self._estimate_tokens(
            [n.model_dump() for n in pack.relevant_nodes]
        )
        if node_tokens > remaining:
            pack.relevant_nodes = pack.relevant_nodes[:1]  # Keep at least the target
        remaining -= min(node_tokens, remaining)

        # 2. Upstream (sort by confidence desc, keep what fits)
        if remaining > 0 and pack.upstream:
            sorted_up = sorted(pack.upstream, key=lambda n: n.confidence, reverse=True)
            kept_up = []
            for n in sorted_up:
                cost = self._estimate_tokens(n.model_dump())
                if cost <= remaining:
                    kept_up.append(n)
                    remaining -= cost
                else:
                    break
            pack.upstream = kept_up

        # 3. Downstream (same strategy)
        if remaining > 0 and pack.downstream:
            sorted_down = sorted(pack.downstream, key=lambda n: n.confidence, reverse=True)
            kept_down = []
            for n in sorted_down:
                cost = self._estimate_tokens(n.model_dump())
                if cost <= remaining:
                    kept_down.append(n)
                    remaining -= cost
                else:
                    break
            pack.downstream = kept_down

        # 4. Edges (keep only edges connecting kept nodes)
        kept_ids = {n.id for n in pack.relevant_nodes + pack.upstream + pack.downstream}
        pack.relevant_edges = [
            e for e in pack.relevant_edges
            if e.source_id in kept_ids and e.target_id in kept_ids
        ]

        # 5. Related files (trim to budget)
        if remaining > 0 and pack.related_files:
            kept_files = []
            for loc in pack.related_files:
                cost = self._estimate_tokens({"file": loc.file_path})
                if cost <= remaining:
                    kept_files.append(loc)
                    remaining -= cost
                else:
                    break
            pack.related_files = kept_files

        # 6. Code snippets (trim last, most expensive)
        if remaining <= 0:
            pack.code_snippets = {}
        elif pack.code_snippets:
            kept_snippets = {}
            for key, snippet in pack.code_snippets.items():
                cost = len(snippet) // 4
                if cost <= remaining:
                    kept_snippets[key] = snippet
                    remaining -= cost
                else:
                    # Truncate the snippet to fit
                    if remaining > 100:
                        kept_snippets[key] = snippet[:remaining * 4] + "\n... (truncated)"
                    break
            pack.code_snippets = kept_snippets

        return pack

    # =========================================================================
    # SEARCH & ANALYSIS
    # =========================================================================

    def search_nodes(
        self,
        query: str,
        type_filter: Optional[NodeType] = None,
        tag_filter: Optional[str] = None,
        limit: int = 10,
    ) -> List[GraphNode]:
        """
        Case-insensitive substring search across node name and description.

        Args:
            query: Substring to search for in name and description.
            type_filter: Optional filter by node type.
            tag_filter: Optional filter by tag.
            limit: Maximum number of results to return.

        Returns:
            Matching nodes sorted by confidence (highest first).
        """
        query_lower = query.lower()
        results = []
        for node in self._nodes.values():
            # Apply type filter
            if type_filter is not None and node.type != type_filter:
                continue
            # Apply tag filter
            if tag_filter is not None and tag_filter not in node.tags:
                continue
            # Substring match on name or description
            name_match = query_lower in node.name.lower()
            desc_match = node.description and query_lower in node.description.lower()
            if name_match or desc_match:
                results.append(node)
        # Sort by confidence descending
        results.sort(key=lambda n: n.confidence, reverse=True)
        return results[:limit]

    def shortest_path(self, source_id: str, target_id: str) -> List[str]:
        """
        Find the shortest path between two nodes using NetworkX.

        Args:
            source_id: Starting node ID.
            target_id: Ending node ID.

        Returns:
            List of node IDs in the path, or empty list if no path exists.
        """
        try:
            return nx.shortest_path(self.graph, source_id, target_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def graph_diff(self, other: "GraphStore") -> Dict:
        """
        Compare this graph with another GraphStore instance.

        A node is considered "modified" if its source_hash or version changed.

        Returns:
            Dict with keys: added_nodes, removed_nodes, modified_nodes,
            added_edges, removed_edges.
        """
        self_node_ids = set(self._nodes.keys())
        other_node_ids = set(other._nodes.keys())

        added_nodes = list(other_node_ids - self_node_ids)
        removed_nodes = list(self_node_ids - other_node_ids)

        modified_nodes = []
        for nid in self_node_ids & other_node_ids:
            self_node = self._nodes[nid]
            other_node = other._nodes[nid]
            if (self_node.source_hash != other_node.source_hash
                    or self_node.version != other_node.version):
                modified_nodes.append(nid)

        self_edge_keys = set(self._edges.keys())
        other_edge_keys = set(other._edges.keys())

        added_edges = list(other_edge_keys - self_edge_keys)
        removed_edges = list(self_edge_keys - other_edge_keys)

        return {
            "added_nodes": added_nodes,
            "removed_nodes": removed_nodes,
            "modified_nodes": modified_nodes,
            "added_edges": added_edges,
            "removed_edges": removed_edges,
        }

    def filter_by_confidence(self, min_confidence: float) -> List[GraphNode]:
        """
        Return all nodes with confidence >= min_confidence.

        Sorted by confidence descending.
        """
        results = [n for n in self._nodes.values() if n.confidence >= min_confidence]
        results.sort(key=lambda n: n.confidence, reverse=True)
        return results

    def get_connected_component(self, node_id: str) -> Set[str]:
        """
        Return all node IDs in the same connected component (ignoring edge direction).

        Uses nx.node_connected_component on an undirected copy of the graph.
        """
        undirected = self.graph.to_undirected()
        try:
            return nx.node_connected_component(undirected, node_id)
        except nx.NetworkXError:
            return set()

    def find_orphans(self) -> List[GraphNode]:
        """
        Find nodes with no edges at all (no incoming or outgoing).

        Excludes WORKSPACE and PROJECT nodes since they are naturally root nodes.
        """
        excluded_types = {NodeType.WORKSPACE, NodeType.PROJECT}
        orphans = []
        for node in self._nodes.values():
            if node.type in excluded_types:
                continue
            if self.graph.degree(node.id) == 0:
                orphans.append(node)
        return orphans

    def get_all_nodes(self) -> List[GraphNode]:
        """Return all nodes in the graph."""
        return list(self._nodes.values())

    def get_all_edges(self) -> List[GraphEdge]:
        """Return all edges in the graph."""
        return list(self._edges.values())

    # =========================================================================
    # PERSISTENCE
    # =========================================================================

    def save(self, filepath: str | Path) -> None:
        """Save graph to JSON file."""
        data = {
            "nodes": [n.model_dump(mode="json") for n in self._nodes.values()],
            "edges": [e.model_dump(mode="json") for e in self._edges.values()],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def load(self, filepath: str | Path) -> None:
        """Load graph from JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.graph.clear()
        self._nodes.clear()
        self._edges.clear()

        for node_data in data.get("nodes", []):
            node = GraphNode(**node_data)
            self.add_node(node)

        for edge_data in data.get("edges", []):
            edge = GraphEdge(**edge_data)
            self.add_edge(edge, validate=False)

    # =========================================================================
    # STATS
    # =========================================================================

    def stats(self) -> Dict[str, Any]:
        """Return graph statistics."""
        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "nodes_by_type": {
                t.value: len(self.get_nodes_by_type(t))
                for t in NodeType
            },
            "nodes_by_tier": {
                t.value: len(self.get_nodes_by_tier(t))
                for t in Tier
            },
            "edges_by_type": {
                t.value: len(self.get_edges_by_type(t))
                for t in EdgeType
            },
            "stale_nodes": len([n for n in self._nodes.values() if n.is_stale]),
            "stale_edges": len([e for e in self._edges.values() if e.is_stale]),
        }
