"""
Workspace Intelligence Layer - Pass 4: Validation & Confidence Scoring
========================================================================

Pass 4 is a quality gate that checks graph integrity, detects issues, and
adjusts confidence scores based on cross-pass corroboration.

Pipeline:
  Pass 0 (File Discovery): Scans workspace for source files
  Pass 1 (Static Analysis): Extracts File, Function, DataModel, TypeDef nodes
  Pass 2 (Structural Edges): Creates endpoints, models, events via regex
  Pass 3 (LLM Analysis): Discovers operational edges (READS_DB, CALLS_API, etc.)
  Pass 4 (Validation): Checks graph quality and adjusts confidence scores

This pass is FREE (no LLM calls) and runs AFTER all other passes.

Validation checks:
  1. Orphan detection: Nodes with no edges (excluding WORKSPACE/PROJECT)
  2. Dangling edges: Edges referencing non-existent node IDs
  3. Type constraint validation: All edges checked against EDGE_CONSTRAINTS
  4. Bidirectional edge verification: Paired edges (e.g., EMITS_EVENT / CONSUMES_EVENT)
  5. Confidence scoring: Boost multi-source nodes, penalize single-source nodes

Confidence adjustments:
  - Node found by both tree-sitter AND regex: +0.1 boost
  - Node found by LLM only (no AST confirmation): -0.1 penalty
  - Edge with both endpoints confirmed: +0.05 boost
  - Stale node: -0.2 penalty
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Set, Tuple

# Allow imports from the project root (parent of pipeline/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import (
    GraphNode,
    GraphEdge,
    NodeType,
    EdgeType,
    Provenance,
    Tier,
    EDGE_CONSTRAINTS,
    validate_edge_with_nodes,
)


# =============================================================================
# CONSTANTS
# =============================================================================

# Confidence adjustment magnitudes
BOOST_MULTI_PASS = 0.1        # Node confirmed by both tree-sitter and regex
PENALTY_LLM_ONLY = -0.1       # Node found by LLM only, not confirmed by AST
BOOST_CONFIRMED_EDGE = 0.05   # Edge whose both endpoints are high-confidence
PENALTY_STALE = -0.2          # Stale node penalty

# Minimum confidence for a node to be considered "confirmed"
CONFIRMED_THRESHOLD = 0.8

# Bidirectional edge pairs: if edge A exists for a target, edge B should exist
# somewhere for the same target (not necessarily from/to the same source).
BIDIRECTIONAL_PAIRS: List[Tuple[EdgeType, EdgeType]] = [
    (EdgeType.EMITS_EVENT, EdgeType.CONSUMES_EVENT),
    (EdgeType.ENQUEUES, EdgeType.DEQUEUES),
    (EdgeType.READS_DB, EdgeType.WRITES_DB),
    (EdgeType.CACHE_READ, EdgeType.CACHE_WRITE),
    (EdgeType.WEBHOOK_SEND, EdgeType.WEBHOOK_RECEIVE),
]


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class ValidationIssue:
    """A single validation issue found during graph quality checks."""
    severity: str           # "error", "warning", "info"
    category: str           # "orphan", "dangling", "constraint", "bidirectional", "confidence"
    message: str
    node_id: Optional[str] = None
    edge_key: Optional[str] = None


@dataclass
class ValidationResult:
    """Summary of all validation checks on the graph."""
    issues: List[ValidationIssue] = field(default_factory=list)
    confidence_adjustments: int = 0   # Number of nodes whose confidence was adjusted
    total_checks: int = 0
    errors: int = 0
    warnings: int = 0
    infos: int = 0


# =============================================================================
# VALIDATION CHECKS
# =============================================================================

def check_orphans(store: GraphStore) -> List[ValidationIssue]:
    """
    Detect orphan nodes -- nodes with no incoming or outgoing edges.

    WORKSPACE and PROJECT nodes are excluded since they are natural roots.

    Args:
        store: The graph store to check.

    Returns:
        List of ValidationIssue for each orphan found.
    """
    issues: List[ValidationIssue] = []
    orphans = store.find_orphans()

    for node in orphans:
        issues.append(ValidationIssue(
            severity="warning",
            category="orphan",
            message=(
                f"Orphan node: {node.type.value} '{node.name}' (id={node.id}) "
                f"has no edges. It may be unreferenced or missing connections."
            ),
            node_id=node.id,
        ))

    return issues


def check_dangling_edges(store: GraphStore) -> List[ValidationIssue]:
    """
    Detect dangling edges -- edges that reference node IDs not present in the graph.

    Args:
        store: The graph store to check.

    Returns:
        List of ValidationIssue for each dangling edge found.
    """
    issues: List[ValidationIssue] = []

    for edge in store.get_all_edges():
        edge_key = f"{edge.source_id}->{edge.target_id}:{edge.type.value}"

        source_node = store.get_node(edge.source_id)
        target_node = store.get_node(edge.target_id)

        if source_node is None:
            issues.append(ValidationIssue(
                severity="error",
                category="dangling",
                message=(
                    f"Dangling edge source: edge {edge.type.value} references "
                    f"non-existent source node '{edge.source_id}'."
                ),
                edge_key=edge_key,
            ))

        if target_node is None:
            issues.append(ValidationIssue(
                severity="error",
                category="dangling",
                message=(
                    f"Dangling edge target: edge {edge.type.value} references "
                    f"non-existent target node '{edge.target_id}'."
                ),
                edge_key=edge_key,
            ))

    return issues


def check_edge_constraints(store: GraphStore) -> List[ValidationIssue]:
    """
    Validate all edges against EDGE_CONSTRAINTS from ontology.py.

    Each edge type has allowed source and target node types. This check
    reports violations as warnings (not errors) because LLM-inferred edges
    may legitimately stretch beyond strict type constraints.

    Args:
        store: The graph store to check.

    Returns:
        List of ValidationIssue for each constraint violation.
    """
    issues: List[ValidationIssue] = []

    for edge in store.get_all_edges():
        edge_key = f"{edge.source_id}->{edge.target_id}:{edge.type.value}"

        source_node = store.get_node(edge.source_id)
        target_node = store.get_node(edge.target_id)

        # Skip if either endpoint is missing (already caught by dangling check)
        if source_node is None or target_node is None:
            continue

        violations = validate_edge_with_nodes(edge, source_node, target_node)

        for violation in violations:
            issues.append(ValidationIssue(
                severity="warning",
                category="constraint",
                message=(
                    f"Edge constraint violation: {violation} "
                    f"(edge: {edge.source_id} -> {edge.target_id})"
                ),
                edge_key=edge_key,
            ))

    return issues


def check_bidirectional(store: GraphStore) -> List[ValidationIssue]:
    """
    Check that bidirectional edge pairs are balanced.

    For each pair (A, B), if there are edges of type A pointing to a target node,
    there should be at least one edge of type B also referencing that same target.

    For example:
      - If EMITS_EVENT edges exist for Event X, there should be at least one
        CONSUMES_EVENT edge for Event X.
      - If ENQUEUES edges exist for Queue Y, there should be at least one
        DEQUEUES edge for Queue Y.

    Missing counterparts are reported as info-level issues (not errors), because
    consumers may live in code not yet indexed or in external services.

    Args:
        store: The graph store to check.

    Returns:
        List of ValidationIssue for unmatched bidirectional edges.
    """
    issues: List[ValidationIssue] = []

    for edge_type_a, edge_type_b in BIDIRECTIONAL_PAIRS:
        # Collect target node IDs for each edge type
        targets_a: Set[str] = set()
        targets_b: Set[str] = set()

        for edge in store.get_edges_by_type(edge_type_a):
            targets_a.add(edge.target_id)

        for edge in store.get_edges_by_type(edge_type_b):
            targets_b.add(edge.target_id)

        # Check: targets of A should have at least one B edge
        unmatched_a = targets_a - targets_b
        for target_id in unmatched_a:
            target_node = store.get_node(target_id)
            target_name = target_node.name if target_node else target_id
            issues.append(ValidationIssue(
                severity="info",
                category="bidirectional",
                message=(
                    f"Unmatched {edge_type_a.value}: '{target_name}' "
                    f"(id={target_id}) has {edge_type_a.value} edges but "
                    f"no corresponding {edge_type_b.value} edges. "
                    f"The counterpart may be in unindexed code."
                ),
                node_id=target_id,
            ))

        # Check: targets of B should have at least one A edge
        unmatched_b = targets_b - targets_a
        for target_id in unmatched_b:
            target_node = store.get_node(target_id)
            target_name = target_node.name if target_node else target_id
            issues.append(ValidationIssue(
                severity="info",
                category="bidirectional",
                message=(
                    f"Unmatched {edge_type_b.value}: '{target_name}' "
                    f"(id={target_id}) has {edge_type_b.value} edges but "
                    f"no corresponding {edge_type_a.value} edges. "
                    f"The counterpart may be in unindexed code."
                ),
                node_id=target_id,
            ))

    return issues


# =============================================================================
# CONFIDENCE ADJUSTMENT
# =============================================================================

def adjust_confidence(store: GraphStore) -> int:
    """
    Adjust confidence scores for all nodes based on cross-pass corroboration.

    Rules:
      1. Multi-pass confirmation (+0.1): A node found by both SCANNER (pass 1
         tree-sitter or pass 2 regex) AND has edges from another pass gets a
         boost, indicating multiple passes agree on its existence.
      2. LLM-only penalty (-0.1): A node with provenance=LLM that has no
         edges from SCANNER provenance is penalized, since it lacks static
         analysis confirmation.
      3. Confirmed edge boost (+0.05): An edge whose both source and target
         nodes have confidence >= CONFIRMED_THRESHOLD gets a small boost on
         both endpoint nodes.
      4. Stale penalty (-0.2): Nodes marked is_stale=True are penalized
         because they may no longer reflect the current codebase.

    All confidence values are clamped to [0.0, 1.0] after adjustment.

    Args:
        store: The graph store to adjust (modified in-place).

    Returns:
        Number of nodes whose confidence was adjusted.
    """
    adjusted_count = 0

    # Build provenance lookup: for each node, collect provenance of its edges
    node_edge_provenances: Dict[str, Set[Provenance]] = {}
    for edge in store.get_all_edges():
        for node_id in (edge.source_id, edge.target_id):
            if node_id not in node_edge_provenances:
                node_edge_provenances[node_id] = set()
            node_edge_provenances[node_id].add(edge.provenance)

    # Pass 1: Per-node adjustments
    for node in store.get_all_nodes():
        original_confidence = node.confidence
        adjustment = 0.0

        edge_provenances = node_edge_provenances.get(node.id, set())

        # Rule 1: Multi-pass confirmation
        # Node is from scanner AND has edges from a different provenance (LLM, etc.)
        # OR node is from LLM AND has edges from scanner
        if node.provenance == Provenance.SCANNER and Provenance.LLM in edge_provenances:
            adjustment += BOOST_MULTI_PASS
        elif node.provenance == Provenance.LLM and Provenance.SCANNER in edge_provenances:
            adjustment += BOOST_MULTI_PASS

        # Rule 2: LLM-only penalty
        # Node is LLM-provenance and no scanner edges touch it
        if node.provenance == Provenance.LLM and Provenance.SCANNER not in edge_provenances:
            adjustment += PENALTY_LLM_ONLY

        # Rule 4: Stale penalty
        if node.is_stale:
            adjustment += PENALTY_STALE

        # Apply adjustment
        if adjustment != 0.0:
            new_confidence = max(0.0, min(1.0, node.confidence + adjustment))
            if new_confidence != node.confidence:
                node.confidence = new_confidence
                # Re-add node to update the NetworkX graph attributes
                store.add_node(node)
                adjusted_count += 1

    # Pass 2: Confirmed edge boost
    # Edges where both endpoints have high confidence boost those endpoints further
    for edge in store.get_all_edges():
        source_node = store.get_node(edge.source_id)
        target_node = store.get_node(edge.target_id)

        if source_node is None or target_node is None:
            continue

        if (source_node.confidence >= CONFIRMED_THRESHOLD
                and target_node.confidence >= CONFIRMED_THRESHOLD):
            # Boost both endpoint nodes
            for ep_node in (source_node, target_node):
                new_confidence = max(0.0, min(1.0, ep_node.confidence + BOOST_CONFIRMED_EDGE))
                if new_confidence != ep_node.confidence:
                    ep_node.confidence = new_confidence
                    store.add_node(ep_node)
                    adjusted_count += 1

    return adjusted_count


# =============================================================================
# MAIN VALIDATION ENTRY POINT
# =============================================================================

def validate_graph(store: GraphStore, fix: bool = False) -> ValidationResult:
    """
    Run all validation checks on the graph and optionally apply fixes.

    Checks run in order:
      1. Orphan detection
      2. Dangling edge detection
      3. Edge constraint validation
      4. Bidirectional edge verification
      5. Confidence scoring (only applied if fix=True)

    Args:
        store: The graph store to validate.
        fix: If True, apply confidence adjustments to the graph in-place.
             If False, only report issues without modifying the graph.

    Returns:
        ValidationResult with all issues and summary counts.
    """
    all_issues: List[ValidationIssue] = []
    total_checks = 0

    # 1. Orphan detection
    orphan_issues = check_orphans(store)
    all_issues.extend(orphan_issues)
    total_checks += len(store.get_all_nodes())

    # 2. Dangling edges
    dangling_issues = check_dangling_edges(store)
    all_issues.extend(dangling_issues)
    total_checks += len(store.get_all_edges())

    # 3. Edge constraint validation
    constraint_issues = check_edge_constraints(store)
    all_issues.extend(constraint_issues)
    total_checks += len(store.get_all_edges())

    # 4. Bidirectional edge verification
    bidirectional_issues = check_bidirectional(store)
    all_issues.extend(bidirectional_issues)
    total_checks += len(BIDIRECTIONAL_PAIRS) * 2

    # 5. Confidence adjustments
    confidence_adjustments = 0
    if fix:
        confidence_adjustments = adjust_confidence(store)

        # Report confidence adjustments as info issues
        if confidence_adjustments > 0:
            all_issues.append(ValidationIssue(
                severity="info",
                category="confidence",
                message=(
                    f"Adjusted confidence scores on {confidence_adjustments} "
                    f"node(s) based on cross-pass corroboration."
                ),
            ))

    total_checks += len(store.get_all_nodes())

    # Tally severity counts
    errors = sum(1 for i in all_issues if i.severity == "error")
    warnings = sum(1 for i in all_issues if i.severity == "warning")
    infos = sum(1 for i in all_issues if i.severity == "info")

    return ValidationResult(
        issues=all_issues,
        confidence_adjustments=confidence_adjustments,
        total_checks=total_checks,
        errors=errors,
        warnings=warnings,
        infos=infos,
    )


# =============================================================================
# CLI / TESTING ENTRY POINT
# =============================================================================

def print_validation_report(result: ValidationResult) -> None:
    """
    Print a human-readable validation report to stdout.

    Args:
        result: The ValidationResult to display.
    """
    print("=" * 70)
    print("  PASS 4: VALIDATION REPORT")
    print("=" * 70)
    print(f"  Total checks performed: {result.total_checks}")
    print(f"  Confidence adjustments: {result.confidence_adjustments}")
    print(f"  Errors:   {result.errors}")
    print(f"  Warnings: {result.warnings}")
    print(f"  Infos:    {result.infos}")
    print("-" * 70)

    if not result.issues:
        print("  No issues found. Graph looks healthy.")
    else:
        # Group by category
        by_category: Dict[str, List[ValidationIssue]] = {}
        for issue in result.issues:
            by_category.setdefault(issue.category, []).append(issue)

        for category, issues in by_category.items():
            print(f"\n  [{category.upper()}] ({len(issues)} issue(s))")
            for issue in issues:
                severity_marker = {
                    "error": "ERR",
                    "warning": "WRN",
                    "info": "INF",
                }.get(issue.severity, "???")
                print(f"    [{severity_marker}] {issue.message}")

    print("=" * 70)


if __name__ == "__main__":
    """
    Standalone test: build a small graph, run validation, print report.
    """
    from ontology import SourceLocation

    print("Building test graph...")
    store = GraphStore()

    # -- Create some nodes --
    workspace = GraphNode(
        id="workspace:test",
        type=NodeType.WORKSPACE,
        name="Test Workspace",
        provenance=Provenance.SCANNER,
        confidence=1.0,
    )
    store.add_node(workspace)

    project = GraphNode(
        id="project:test:myapp",
        type=NodeType.PROJECT,
        name="My App",
        provenance=Provenance.SCANNER,
        confidence=1.0,
    )
    store.add_node(project)

    service = GraphNode(
        id="service:test:api",
        type=NodeType.SERVICE,
        name="API Service",
        provenance=Provenance.SCANNER,
        confidence=0.95,
    )
    store.add_node(service)

    file_node = GraphNode(
        id="file:test:routes.py",
        type=NodeType.FILE,
        name="routes.py",
        provenance=Provenance.SCANNER,
        confidence=1.0,
        language="python",
        location=SourceLocation(file_path="routes.py", start_line=1, end_line=100),
    )
    store.add_node(file_node)

    func_node = GraphNode(
        id="function:test:create_user",
        type=NodeType.FUNCTION,
        name="create_user",
        provenance=Provenance.SCANNER,
        confidence=0.9,
        language="python",
    )
    store.add_node(func_node)

    # LLM-only node (no scanner edges)
    llm_event = GraphNode(
        id="event:test:USER_CREATED",
        type=NodeType.EVENT,
        name="USER_CREATED",
        provenance=Provenance.LLM,
        confidence=0.7,
    )
    store.add_node(llm_event)

    # Orphan node (no edges)
    orphan_node = GraphNode(
        id="function:test:unused_helper",
        type=NodeType.FUNCTION,
        name="unused_helper",
        provenance=Provenance.SCANNER,
        confidence=0.8,
    )
    store.add_node(orphan_node)

    # Stale node
    stale_node = GraphNode(
        id="function:test:old_handler",
        type=NodeType.FUNCTION,
        name="old_handler",
        provenance=Provenance.SCANNER,
        confidence=0.85,
        is_stale=True,
    )
    store.add_node(stale_node)

    # -- Create some edges --

    # Structural edges (CONTAINS, DEFINES)
    store.add_edge(GraphEdge(
        source_id="workspace:test",
        target_id="project:test:myapp",
        type=EdgeType.CONTAINS,
        provenance=Provenance.SCANNER,
    ), validate=False)

    store.add_edge(GraphEdge(
        source_id="project:test:myapp",
        target_id="service:test:api",
        type=EdgeType.CONTAINS,
        provenance=Provenance.SCANNER,
    ), validate=False)

    store.add_edge(GraphEdge(
        source_id="file:test:routes.py",
        target_id="function:test:create_user",
        type=EdgeType.DEFINES,
        provenance=Provenance.SCANNER,
    ), validate=False)

    # LLM-discovered edge: function emits event
    store.add_edge(GraphEdge(
        source_id="function:test:create_user",
        target_id="event:test:USER_CREATED",
        type=EdgeType.EMITS_EVENT,
        provenance=Provenance.LLM,
        confidence=0.7,
    ), validate=False)

    # Connect stale node so it is not an orphan
    store.add_edge(GraphEdge(
        source_id="file:test:routes.py",
        target_id="function:test:old_handler",
        type=EdgeType.DEFINES,
        provenance=Provenance.SCANNER,
    ), validate=False)

    # Dangling edge (target does not exist)
    store.add_edge(GraphEdge(
        source_id="function:test:create_user",
        target_id="collection:test:nonexistent_table",
        type=EdgeType.WRITES_DB,
        provenance=Provenance.LLM,
        confidence=0.5,
    ), validate=False)

    # Edge with wrong type constraint (Function CONTAINS Event -- invalid)
    store.add_edge(GraphEdge(
        source_id="function:test:create_user",
        target_id="event:test:USER_CREATED",
        type=EdgeType.CONTAINS,
        provenance=Provenance.LLM,
        confidence=0.4,
    ), validate=False)

    print(f"Graph: {len(store.get_all_nodes())} nodes, {len(store.get_all_edges())} edges\n")

    # Run validation without fixing
    print("--- Validation (report only, no fixes) ---")
    result = validate_graph(store, fix=False)
    print_validation_report(result)

    # Run validation with fixes
    print("\n--- Validation (with confidence fixes) ---")
    result_fixed = validate_graph(store, fix=True)
    print_validation_report(result_fixed)

    # Show adjusted confidences
    print("\nNode confidences after adjustment:")
    for node in store.get_all_nodes():
        print(f"  {node.id}: {node.confidence:.2f} (provenance={node.provenance.value}, stale={node.is_stale})")
