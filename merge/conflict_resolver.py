"""
Workspace Intelligence Layer - Conflict Resolver
=================================================

Story 5.2: Deterministic conflict resolution for merging parallel analysis
results into a single coherent graph.

When multiple passes (tree-sitter, regex, LLM) produce conflicting information
about the same entity, this module resolves them using a priority chain:

  1. Higher confidence wins (>0.1 difference)
  2. More specific type wins (ENDPOINT > FUNCTION when confidence is close)
  3. Higher provenance trust wins: HUMAN > RUNTIME > SCANNER > LLM > IMPORT
  4. Tie -> flag for human review

The merge is INTO the base graph -- base is modified in place.
Conflicts with resolution="flag_review" are NOT applied; the existing value
is preserved until a human decides.
"""

import sys
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path setup: allow imports from the project root (one level up from merge/)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import (
    GraphNode,
    GraphEdge,
    NodeType,
    EdgeType,
    Provenance,
    Tier,
    NODE_TIER,
    validate_edge_with_nodes,
)

logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class Conflict:
    """
    A single field-level conflict between an existing and an incoming node.

    Attributes:
        node_id:         ID of the node in conflict.
        field:           Which field diverges (type, description, tags, etc.).
        existing_value:  The current value in the base graph.
        incoming_value:  The proposed value from the incoming graph.
        resolution:      One of: keep_existing, use_incoming, merge, flag_review.
        reason:          Human-readable explanation of why this resolution was chosen.
    """
    node_id: str
    field: str
    existing_value: Any
    incoming_value: Any
    resolution: str     # "keep_existing" | "use_incoming" | "merge" | "flag_review"
    reason: str


@dataclass
class MergeResult:
    """
    Summary of a merge operation.

    Attributes:
        conflicts_found:     Total conflicts detected across all nodes.
        auto_resolved:       Conflicts resolved automatically by the priority chain.
        flagged_for_review:  Conflicts that require human intervention.
        nodes_updated:       Nodes whose fields were actually changed in base.
        edges_added:         New edges added from incoming graph.
        edges_removed:       Edges removed (currently unused; reserved for future).
        conflicts:           Full list of Conflict objects for audit/inspection.
    """
    conflicts_found: int = 0
    auto_resolved: int = 0
    flagged_for_review: int = 0
    nodes_updated: int = 0
    edges_added: int = 0
    edges_removed: int = 0
    conflicts: List[Conflict] = field(default_factory=list)


# =============================================================================
# PROVENANCE TRUST ORDER
# =============================================================================

_PROVENANCE_TRUST: Dict[Provenance, int] = {
    Provenance.HUMAN:   50,
    Provenance.RUNTIME: 40,
    Provenance.SCANNER: 30,
    Provenance.LLM:     20,
    Provenance.IMPORT:  10,
}


# =============================================================================
# NODE TYPE SPECIFICITY
# =============================================================================

# Tier-level specificity: MICRO > MESO > MACRO
_TIER_SPECIFICITY: Dict[Tier, int] = {
    Tier.MICRO: 30,
    Tier.MESO:  20,
    Tier.MACRO: 10,
}

# Within-tier specificity overrides.  Types with richer semantics get a bonus.
# ENDPOINT has route semantics on top of FUNCTION, DATA_MODEL has schema
# semantics on top of TYPE_DEF, ASYNC_HANDLER has event/trigger semantics.
_TYPE_SPECIFICITY_BONUS: Dict[NodeType, int] = {
    NodeType.ENDPOINT:      5,
    NodeType.ASYNC_HANDLER: 4,
    NodeType.DATA_MODEL:    3,
    NodeType.MIDDLEWARE:     2,
    NodeType.ROUTER:        2,
}


def _type_specificity(node_type: NodeType) -> int:
    """Return a numeric specificity score for a node type."""
    tier = NODE_TIER[node_type]
    base = _TIER_SPECIFICITY[tier]
    bonus = _TYPE_SPECIFICITY_BONUS.get(node_type, 0)
    return base + bonus


# =============================================================================
# CONFIDENCE THRESHOLD
# =============================================================================

_CONFIDENCE_DIFF_THRESHOLD = 0.1


# =============================================================================
# COMPARISON HELPERS
# =============================================================================

def _compare_confidence(existing: float, incoming: float) -> str:
    """
    Compare two confidence values.

    If the difference exceeds the threshold, the higher value wins.
    Otherwise returns "tie" to fall through to the next comparator.

    Returns:
        "keep_existing", "use_incoming", or "tie".
    """
    diff = incoming - existing
    if diff > _CONFIDENCE_DIFF_THRESHOLD:
        return "use_incoming"
    if diff < -_CONFIDENCE_DIFF_THRESHOLD:
        return "keep_existing"
    return "tie"


def _compare_type(existing_type: NodeType, incoming_type: NodeType) -> str:
    """
    Compare two node types by specificity.

    More specific type wins.  If specificity is equal, returns "tie".

    Returns:
        "keep_existing", "use_incoming", or "tie".
    """
    existing_spec = _type_specificity(existing_type)
    incoming_spec = _type_specificity(incoming_type)
    if incoming_spec > existing_spec:
        return "use_incoming"
    if incoming_spec < existing_spec:
        return "keep_existing"
    return "tie"


def _compare_provenance(existing: Provenance, incoming: Provenance) -> str:
    """
    Compare two provenances by trust level.

    Higher trust wins.  If trust is equal, returns "tie".

    Returns:
        "keep_existing", "use_incoming", or "tie".
    """
    existing_trust = _PROVENANCE_TRUST.get(existing, 0)
    incoming_trust = _PROVENANCE_TRUST.get(incoming, 0)
    if incoming_trust > existing_trust:
        return "use_incoming"
    if incoming_trust < existing_trust:
        return "keep_existing"
    return "tie"


# =============================================================================
# COLLECTION MERGE HELPERS
# =============================================================================

def _merge_tags(existing_tags: List[str], incoming_tags: List[str]) -> List[str]:
    """
    Merge two tag lists by taking their union.

    Preserves order: existing tags first, then new incoming tags appended.
    Duplicates are removed.
    """
    seen = set(existing_tags)
    merged = list(existing_tags)
    for tag in incoming_tags:
        if tag not in seen:
            seen.add(tag)
            merged.append(tag)
    return merged


def _merge_metadata(
    existing_meta: Dict[str, Any],
    incoming_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deep-merge two metadata dicts.

    Strategy:
      - Keys only in existing: kept.
      - Keys only in incoming: added.
      - Keys in both with dict values: recursively merged.
      - Keys in both with non-dict values: incoming overrides.
    """
    merged: Dict[str, Any] = {}

    all_keys = set(existing_meta.keys()) | set(incoming_meta.keys())
    for key in all_keys:
        if key in existing_meta and key not in incoming_meta:
            merged[key] = existing_meta[key]
        elif key not in existing_meta and key in incoming_meta:
            merged[key] = incoming_meta[key]
        else:
            # Key in both
            ev = existing_meta[key]
            iv = incoming_meta[key]
            if isinstance(ev, dict) and isinstance(iv, dict):
                merged[key] = _merge_metadata(ev, iv)
            else:
                merged[key] = iv  # incoming overrides
    return merged


# =============================================================================
# NODE CONFLICT RESOLUTION
# =============================================================================

def resolve_node_conflict(
    existing: GraphNode,
    incoming: GraphNode,
) -> List[Conflict]:
    """
    Compare every relevant field between an existing and incoming node
    and produce a list of Conflict objects with resolutions.

    Resolution priority chain (per field):
      1. Confidence difference > 0.1  ->  higher confidence wins
      2. Type specificity              ->  more specific type wins
      3. Provenance trust              ->  higher provenance wins
      4. Tie                           ->  flag for human review

    Fields compared:
      - type, name, description, confidence, provenance,
        tags (union merge), metadata (deep merge), language,
        parent_id, location, source_hash

    Returns:
        List of Conflict objects (may be empty if nodes are identical).
    """
    conflicts: List[Conflict] = []
    node_id = existing.id

    # ------------------------------------------------------------------
    # type
    # ------------------------------------------------------------------
    if existing.type != incoming.type:
        resolution, reason = _resolve_via_priority_chain(
            existing, incoming, field_name="type"
        )
        conflicts.append(Conflict(
            node_id=node_id,
            field="type",
            existing_value=existing.type.value,
            incoming_value=incoming.type.value,
            resolution=resolution,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # name
    # ------------------------------------------------------------------
    if existing.name != incoming.name:
        resolution, reason = _resolve_via_priority_chain(
            existing, incoming, field_name="name"
        )
        conflicts.append(Conflict(
            node_id=node_id,
            field="name",
            existing_value=existing.name,
            incoming_value=incoming.name,
            resolution=resolution,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # description
    # ------------------------------------------------------------------
    if existing.description != incoming.description:
        # Prefer whichever is non-None; if both non-None, use priority chain
        if existing.description is None and incoming.description is not None:
            resolution = "use_incoming"
            reason = "Existing description is empty; adopting incoming description."
        elif existing.description is not None and incoming.description is None:
            resolution = "keep_existing"
            reason = "Incoming description is empty; keeping existing description."
        else:
            resolution, reason = _resolve_via_priority_chain(
                existing, incoming, field_name="description"
            )
        conflicts.append(Conflict(
            node_id=node_id,
            field="description",
            existing_value=existing.description,
            incoming_value=incoming.description,
            resolution=resolution,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # confidence  (always take higher)
    # ------------------------------------------------------------------
    if existing.confidence != incoming.confidence:
        if incoming.confidence > existing.confidence:
            resolution = "use_incoming"
            reason = (
                f"Incoming confidence ({incoming.confidence:.2f}) is higher "
                f"than existing ({existing.confidence:.2f})."
            )
        else:
            resolution = "keep_existing"
            reason = (
                f"Existing confidence ({existing.confidence:.2f}) is higher "
                f"than incoming ({incoming.confidence:.2f})."
            )
        conflicts.append(Conflict(
            node_id=node_id,
            field="confidence",
            existing_value=existing.confidence,
            incoming_value=incoming.confidence,
            resolution=resolution,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # provenance  (higher trust wins)
    # ------------------------------------------------------------------
    if existing.provenance != incoming.provenance:
        prov_result = _compare_provenance(existing.provenance, incoming.provenance)
        if prov_result == "use_incoming":
            resolution = "use_incoming"
            reason = (
                f"Incoming provenance ({incoming.provenance.value}) has higher "
                f"trust than existing ({existing.provenance.value})."
            )
        elif prov_result == "keep_existing":
            resolution = "keep_existing"
            reason = (
                f"Existing provenance ({existing.provenance.value}) has higher "
                f"trust than incoming ({incoming.provenance.value})."
            )
        else:
            resolution = "flag_review"
            reason = (
                f"Provenance tie between {existing.provenance.value} and "
                f"{incoming.provenance.value}; flagging for human review."
            )
        conflicts.append(Conflict(
            node_id=node_id,
            field="provenance",
            existing_value=existing.provenance.value,
            incoming_value=incoming.provenance.value,
            resolution=resolution,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # tags  (always union-merged)
    # ------------------------------------------------------------------
    if set(existing.tags) != set(incoming.tags):
        merged_tags = _merge_tags(existing.tags, incoming.tags)
        conflicts.append(Conflict(
            node_id=node_id,
            field="tags",
            existing_value=existing.tags,
            incoming_value=incoming.tags,
            resolution="merge",
            reason="Tags are union-merged; all tags from both sources are kept.",
        ))

    # ------------------------------------------------------------------
    # metadata  (always deep-merged)
    # ------------------------------------------------------------------
    if existing.metadata != incoming.metadata:
        conflicts.append(Conflict(
            node_id=node_id,
            field="metadata",
            existing_value=existing.metadata,
            incoming_value=incoming.metadata,
            resolution="merge",
            reason="Metadata is deep-merged; incoming keys override on conflict.",
        ))

    # ------------------------------------------------------------------
    # language
    # ------------------------------------------------------------------
    if existing.language != incoming.language:
        if existing.language is None and incoming.language is not None:
            resolution = "use_incoming"
            reason = "Existing language is unset; adopting incoming language."
        elif existing.language is not None and incoming.language is None:
            resolution = "keep_existing"
            reason = "Incoming language is unset; keeping existing language."
        else:
            resolution, reason = _resolve_via_priority_chain(
                existing, incoming, field_name="language"
            )
        conflicts.append(Conflict(
            node_id=node_id,
            field="language",
            existing_value=existing.language,
            incoming_value=incoming.language,
            resolution=resolution,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # parent_id
    # ------------------------------------------------------------------
    if existing.parent_id != incoming.parent_id:
        if existing.parent_id is None and incoming.parent_id is not None:
            resolution = "use_incoming"
            reason = "Existing parent_id is unset; adopting incoming parent_id."
        elif existing.parent_id is not None and incoming.parent_id is None:
            resolution = "keep_existing"
            reason = "Incoming parent_id is unset; keeping existing parent_id."
        else:
            resolution, reason = _resolve_via_priority_chain(
                existing, incoming, field_name="parent_id"
            )
        conflicts.append(Conflict(
            node_id=node_id,
            field="parent_id",
            existing_value=existing.parent_id,
            incoming_value=incoming.parent_id,
            resolution=resolution,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # location
    # ------------------------------------------------------------------
    if existing.location != incoming.location:
        if existing.location is None and incoming.location is not None:
            resolution = "use_incoming"
            reason = "Existing location is unset; adopting incoming location."
        elif existing.location is not None and incoming.location is None:
            resolution = "keep_existing"
            reason = "Incoming location is unset; keeping existing location."
        else:
            resolution, reason = _resolve_via_priority_chain(
                existing, incoming, field_name="location"
            )
        conflicts.append(Conflict(
            node_id=node_id,
            field="location",
            existing_value=(
                existing.location.model_dump() if existing.location else None
            ),
            incoming_value=(
                incoming.location.model_dump() if incoming.location else None
            ),
            resolution=resolution,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # source_hash
    # ------------------------------------------------------------------
    if existing.source_hash != incoming.source_hash:
        if existing.source_hash is None and incoming.source_hash is not None:
            resolution = "use_incoming"
            reason = "Existing source_hash is unset; adopting incoming source_hash."
        elif existing.source_hash is not None and incoming.source_hash is None:
            resolution = "keep_existing"
            reason = "Incoming source_hash is unset; keeping existing source_hash."
        else:
            resolution, reason = _resolve_via_priority_chain(
                existing, incoming, field_name="source_hash"
            )
        conflicts.append(Conflict(
            node_id=node_id,
            field="source_hash",
            existing_value=existing.source_hash,
            incoming_value=incoming.source_hash,
            resolution=resolution,
            reason=reason,
        ))

    return conflicts


def _resolve_via_priority_chain(
    existing: GraphNode,
    incoming: GraphNode,
    field_name: str,
) -> tuple:
    """
    Walk the resolution priority chain for a generic field conflict.

    Priority chain:
      1. Confidence difference > 0.1
      2. Type specificity (more specific wins)
      3. Provenance trust
      4. Tie -> flag for human review

    Returns:
        (resolution: str, reason: str)
    """
    # Step 1: Confidence
    conf_result = _compare_confidence(existing.confidence, incoming.confidence)
    if conf_result == "use_incoming":
        return (
            "use_incoming",
            f"Field '{field_name}': incoming node has significantly higher "
            f"confidence ({incoming.confidence:.2f} vs {existing.confidence:.2f}).",
        )
    if conf_result == "keep_existing":
        return (
            "keep_existing",
            f"Field '{field_name}': existing node has significantly higher "
            f"confidence ({existing.confidence:.2f} vs {incoming.confidence:.2f}).",
        )

    # Step 2: Type specificity (only meaningful when types differ)
    if existing.type != incoming.type:
        type_result = _compare_type(existing.type, incoming.type)
        if type_result == "use_incoming":
            return (
                "use_incoming",
                f"Field '{field_name}': incoming type {incoming.type.value} is "
                f"more specific than existing type {existing.type.value}.",
            )
        if type_result == "keep_existing":
            return (
                "keep_existing",
                f"Field '{field_name}': existing type {existing.type.value} is "
                f"more specific than incoming type {incoming.type.value}.",
            )

    # Step 3: Provenance trust
    prov_result = _compare_provenance(existing.provenance, incoming.provenance)
    if prov_result == "use_incoming":
        return (
            "use_incoming",
            f"Field '{field_name}': incoming provenance "
            f"({incoming.provenance.value}) has higher trust than existing "
            f"({existing.provenance.value}).",
        )
    if prov_result == "keep_existing":
        return (
            "keep_existing",
            f"Field '{field_name}': existing provenance "
            f"({existing.provenance.value}) has higher trust than incoming "
            f"({incoming.provenance.value}).",
        )

    # Step 4: True tie -> flag for human review
    return (
        "flag_review",
        f"Field '{field_name}': confidence, type specificity, and provenance "
        f"are all tied; flagging for human review.",
    )


# =============================================================================
# APPLY CONFLICTS TO A NODE
# =============================================================================

def _apply_conflicts(
    base_store: GraphStore,
    existing: GraphNode,
    incoming: GraphNode,
    conflicts: List[Conflict],
) -> bool:
    """
    Apply resolved conflicts to the existing node in the base graph.

    Only applies conflicts whose resolution is NOT "flag_review".
    Bumps the node's version counter if any field was changed.

    Returns:
        True if the node was modified, False otherwise.
    """
    modified = False

    for conflict in conflicts:
        if conflict.resolution == "flag_review":
            # Do NOT apply -- leave existing value intact
            continue

        if conflict.field == "type" and conflict.resolution == "use_incoming":
            existing.type = incoming.type
            modified = True

        elif conflict.field == "name" and conflict.resolution == "use_incoming":
            existing.name = incoming.name
            modified = True

        elif conflict.field == "description":
            if conflict.resolution == "use_incoming":
                existing.description = incoming.description
                modified = True

        elif conflict.field == "confidence":
            if conflict.resolution == "use_incoming":
                existing.confidence = incoming.confidence
                modified = True

        elif conflict.field == "provenance":
            if conflict.resolution == "use_incoming":
                existing.provenance = incoming.provenance
                modified = True

        elif conflict.field == "tags":
            # Always merge
            existing.tags = _merge_tags(existing.tags, incoming.tags)
            modified = True

        elif conflict.field == "metadata":
            # Always deep-merge
            existing.metadata = _merge_metadata(
                existing.metadata, incoming.metadata
            )
            modified = True

        elif conflict.field == "language":
            if conflict.resolution == "use_incoming":
                existing.language = incoming.language
                modified = True

        elif conflict.field == "parent_id":
            if conflict.resolution == "use_incoming":
                existing.parent_id = incoming.parent_id
                modified = True

        elif conflict.field == "location":
            if conflict.resolution == "use_incoming":
                existing.location = incoming.location
                modified = True

        elif conflict.field == "source_hash":
            if conflict.resolution == "use_incoming":
                existing.source_hash = incoming.source_hash
                modified = True

    if modified:
        existing.version += 1
        existing.last_updated = datetime.now(timezone.utc)
        # Re-add to base store so the NetworkX graph data stays in sync
        base_store.add_node(existing)

    return modified


# =============================================================================
# EDGE MERGING
# =============================================================================

def _edge_key(edge: GraphEdge) -> str:
    """Compute the canonical key for an edge (matches GraphStore convention)."""
    return f"{edge.source_id}->{edge.target_id}:{edge.type.value}"


def _merge_edges(
    base: GraphStore,
    incoming: GraphStore,
) -> int:
    """
    Add edges from the incoming graph that do not exist in the base graph.

    Validates each new edge against EDGE_CONSTRAINTS before adding.
    Invalid edges are logged but still added (advisory constraints).

    Returns:
        Number of edges added.
    """
    base_edge_keys = {_edge_key(e) for e in base.get_all_edges()}
    edges_added = 0

    for edge in incoming.get_all_edges():
        key = _edge_key(edge)
        if key not in base_edge_keys:
            # Only add if both endpoints exist in the base graph
            source_node = base.get_node(edge.source_id)
            target_node = base.get_node(edge.target_id)
            if source_node is None or target_node is None:
                logger.debug(
                    "Skipping edge %s: endpoint(s) missing from base graph.",
                    key,
                )
                continue

            violations = base.add_edge(edge, validate=True)
            if violations:
                logger.info(
                    "Edge %s added with advisory violations: %s",
                    key,
                    violations,
                )
            edges_added += 1

    return edges_added


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def merge_graphs(base: GraphStore, incoming: GraphStore) -> MergeResult:
    """
    Merge an incoming graph into a base graph, resolving conflicts in place.

    Workflow:
      1. For each node in incoming that also exists in base: resolve conflicts
         and apply resolutions (except flag_review, which preserves existing).
      2. For each node only in incoming: add it to base.
      3. For each edge only in incoming: validate endpoints and add.

    Args:
        base:     The target graph (modified in place).
        incoming: The source graph to merge from (not modified).

    Returns:
        MergeResult with counts and the full conflict list.
    """
    result = MergeResult()

    base_node_ids = {n.id for n in base.get_all_nodes()}
    incoming_nodes = incoming.get_all_nodes()

    nodes_updated_ids: set = set()

    # ------------------------------------------------------------------
    # Phase 1: Resolve conflicts for nodes present in both graphs
    # ------------------------------------------------------------------
    for inc_node in incoming_nodes:
        existing_node = base.get_node(inc_node.id)

        if existing_node is not None:
            # Node exists in both -- resolve conflicts
            conflicts = resolve_node_conflict(existing_node, inc_node)
            if conflicts:
                result.conflicts.extend(conflicts)
                result.conflicts_found += len(conflicts)

                for c in conflicts:
                    if c.resolution == "flag_review":
                        result.flagged_for_review += 1
                    else:
                        result.auto_resolved += 1

                modified = _apply_conflicts(base, existing_node, inc_node, conflicts)
                if modified:
                    nodes_updated_ids.add(inc_node.id)
        else:
            # Node only in incoming -- add to base
            base.add_node(inc_node)

    result.nodes_updated = len(nodes_updated_ids)

    # ------------------------------------------------------------------
    # Phase 2: Merge edges
    # ------------------------------------------------------------------
    result.edges_added = _merge_edges(base, incoming)

    logger.info(
        "Merge complete: %d conflicts found, %d auto-resolved, "
        "%d flagged for review, %d nodes updated, %d edges added.",
        result.conflicts_found,
        result.auto_resolved,
        result.flagged_for_review,
        result.nodes_updated,
        result.edges_added,
    )

    return result


# =============================================================================
# MAIN (demo / smoke test)
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    )

    # -- Build a base graph with a couple of nodes -------------------------
    base = GraphStore()
    base.add_node(GraphNode(
        id="function:auth:validate_token",
        type=NodeType.FUNCTION,
        name="validate_token",
        description="Validates a JWT token.",
        provenance=Provenance.SCANNER,
        confidence=0.80,
        tags=["auth"],
        language="python",
    ))
    base.add_node(GraphNode(
        id="endpoint:users:GET:/users",
        type=NodeType.ENDPOINT,
        name="GET /users",
        description="List users.",
        provenance=Provenance.SCANNER,
        confidence=0.90,
        tags=["api"],
        language="python",
        metadata={"http_method": "GET", "http_path": "/users"},
    ))
    base.add_edge(GraphEdge(
        source_id="endpoint:users:GET:/users",
        target_id="function:auth:validate_token",
        type=EdgeType.CALLS,
        provenance=Provenance.SCANNER,
        confidence=0.85,
    ))

    # -- Build an incoming graph with overlapping + new nodes --------------
    incoming = GraphStore()

    # Same node, but LLM identified it as an ENDPOINT with higher confidence
    incoming.add_node(GraphNode(
        id="function:auth:validate_token",
        type=NodeType.ENDPOINT,
        name="validate_token",
        description="Validates a JWT bearer token and extracts claims.",
        provenance=Provenance.LLM,
        confidence=0.92,
        tags=["auth", "security"],
        language="python",
    ))

    # Same endpoint, different description from a human annotation
    incoming.add_node(GraphNode(
        id="endpoint:users:GET:/users",
        type=NodeType.ENDPOINT,
        name="GET /users",
        description="List all users with pagination support.",
        provenance=Provenance.HUMAN,
        confidence=0.95,
        tags=["api", "public-api"],
        language="python",
        metadata={"http_method": "GET", "http_path": "/users", "paginated": True},
    ))

    # Brand-new node only in incoming
    incoming.add_node(GraphNode(
        id="function:auth:refresh_token",
        type=NodeType.FUNCTION,
        name="refresh_token",
        description="Refreshes an expired JWT token.",
        provenance=Provenance.LLM,
        confidence=0.70,
        tags=["auth"],
        language="python",
    ))

    # New edge in incoming
    incoming.add_edge(GraphEdge(
        source_id="endpoint:users:GET:/users",
        target_id="function:auth:refresh_token",
        type=EdgeType.CALLS,
        provenance=Provenance.LLM,
        confidence=0.60,
    ))

    # -- Merge ------------------------------------------------------------
    result = merge_graphs(base, incoming)

    # -- Report ------------------------------------------------------------
    print()
    print("=" * 72)
    print("  MERGE RESULT")
    print("=" * 72)
    print(f"  Conflicts found:      {result.conflicts_found}")
    print(f"  Auto-resolved:        {result.auto_resolved}")
    print(f"  Flagged for review:   {result.flagged_for_review}")
    print(f"  Nodes updated:        {result.nodes_updated}")
    print(f"  Edges added:          {result.edges_added}")
    print(f"  Edges removed:        {result.edges_removed}")
    print()

    for c in result.conflicts:
        marker = "  [REVIEW]" if c.resolution == "flag_review" else ""
        print(f"  {c.node_id}  .{c.field}{marker}")
        print(f"    existing : {c.existing_value}")
        print(f"    incoming : {c.incoming_value}")
        print(f"    resolution: {c.resolution}")
        print(f"    reason    : {c.reason}")
        print()

    # -- Verify base graph state -------------------------------------------
    print("-" * 72)
    print("  BASE GRAPH AFTER MERGE")
    print("-" * 72)
    for node in base.get_all_nodes():
        print(
            f"  {node.id}  type={node.type.value}  confidence={node.confidence:.2f}  "
            f"provenance={node.provenance.value}  tags={node.tags}  v{node.version}"
        )
    print()
    for edge in base.get_all_edges():
        print(
            f"  {edge.source_id} --[{edge.type.value}]--> {edge.target_id}  "
            f"confidence={edge.confidence:.2f}"
        )
    print()
