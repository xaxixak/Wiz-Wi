"""
Workspace Intelligence Layer - Unified CLI
===========================================

Story 3.4: Main CLI entry point for the workspace intelligence pipeline.

Commands:
  index   <workspace>  - Run the full pipeline on a workspace
  update  [workspace]  - Incremental update (detect changes, cascade, reindex)
  status  [workspace]  - Show graph statistics and health
  query   <node_name>  - Search for a node and show details
  impact  <node_name>  - Show impact analysis for a node
"""

import sys
import json
import time
import logging
import argparse
from pathlib import Path

# Ensure project root is on sys.path for sibling imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from graph_store import GraphStore
from pipeline.orchestrator import run_pipeline, print_summary

logger = logging.getLogger("workspace-intelligence")

DEFAULT_GRAPH_FILE = "workspace_graph.json"


# =============================================================================
# Helpers
# =============================================================================

def _setup_logging(verbose: bool) -> None:
    """Configure logging level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_graph(graph_path: Path) -> GraphStore:
    """Load an existing graph from JSON, or exit with a helpful message."""
    if not graph_path.exists():
        print(f"ERROR: Graph file not found: {graph_path}")
        print("  Run 'python cli.py index <workspace>' first to build the graph.")
        sys.exit(1)

    store = GraphStore()
    store.load(graph_path)
    return store


def _resolve_graph_path(args_graph: str, workspace_path: Path) -> Path:
    """Resolve the graph file path from CLI args or workspace default."""
    if args_graph:
        return Path(args_graph).resolve()
    return workspace_path / DEFAULT_GRAPH_FILE


def _find_node(store: GraphStore, name: str) -> list:
    """Search for nodes matching the given name (case-insensitive)."""
    matches = []
    for node in store._nodes.values():
        if name.lower() in node.name.lower() or name.lower() in node.id.lower():
            matches.append(node)
    return matches


def _print_node_detail(node, store: GraphStore, depth: int = 2) -> None:
    """Print detailed info about a single node."""
    print(f"\n  Node: {node.name}")
    print(f"  ID:   {node.id}")
    print(f"  Type: {node.type.value}")
    print(f"  Tier: {node.tier.value}")
    print(f"  Stale: {'YES' if node.is_stale else 'no'}")
    if node.description:
        print(f"  Desc: {node.description}")
    if node.language:
        print(f"  Lang: {node.language}")
    if node.tags:
        print(f"  Tags: {', '.join(node.tags)}")
    if node.location:
        print(f"  File: {node.location.file_path}:{node.location.start_line}")
    if node.parent_id:
        parent = store.get_node(node.parent_id)
        parent_name = parent.name if parent else node.parent_id
        print(f"  Parent: {parent_name}")

    # Edges from this node
    edges_out = store.get_edges_from(node.id)
    if edges_out:
        print(f"\n  Outgoing edges ({len(edges_out)}):")
        for edge in edges_out[:15]:
            target = store.get_node(edge.target_id)
            target_name = target.name if target else edge.target_id
            stale_mark = " [STALE]" if edge.is_stale else ""
            print(f"    --[{edge.type.value}]--> {target_name}{stale_mark}")
        if len(edges_out) > 15:
            print(f"    ... and {len(edges_out) - 15} more")

    # Edges to this node
    edges_in = store.get_edges_to(node.id)
    if edges_in:
        print(f"\n  Incoming edges ({len(edges_in)}):")
        for edge in edges_in[:15]:
            source = store.get_node(edge.source_id)
            source_name = source.name if source else edge.source_id
            stale_mark = " [STALE]" if edge.is_stale else ""
            print(f"    {source_name} --[{edge.type.value}]-->{stale_mark}")
        if len(edges_in) > 15:
            print(f"    ... and {len(edges_in) - 15} more")

    # Neighbors
    upstream = store.get_upstream(node.id, max_depth=depth)
    downstream = store.get_downstream(node.id, max_depth=depth)

    if upstream:
        print(f"\n  Upstream ({len(upstream)} nodes, depth {depth}):")
        for u in upstream[:10]:
            print(f"    <- {u.type.value}: {u.name}")
        if len(upstream) > 10:
            print(f"    ... and {len(upstream) - 10} more")

    if downstream:
        print(f"\n  Downstream ({len(downstream)} nodes, depth {depth}):")
        for d in downstream[:10]:
            print(f"    -> {d.type.value}: {d.name}")
        if len(downstream) > 10:
            print(f"    ... and {len(downstream) - 10} more")


# =============================================================================
# Command: index
# =============================================================================

def cmd_index(args: argparse.Namespace) -> None:
    """Run the full pipeline on a workspace."""
    _setup_logging(args.verbose)
    workspace_path = Path(args.workspace_path).resolve()

    if not workspace_path.is_dir():
        print(f"ERROR: Not a directory: {workspace_path}")
        sys.exit(1)

    output_path = args.output or str(workspace_path / DEFAULT_GRAPH_FILE)
    passes = args.passes if args.passes else None

    print(f"Indexing workspace: {workspace_path}")
    print(f"Output: {output_path}")

    result = run_pipeline(
        workspace_path=workspace_path,
        output_path=output_path,
        max_depth=args.depth,
        passes=passes,
    )

    print_summary(result)

    if result.errors:
        print(f"Completed with {len(result.errors)} errors.")
        sys.exit(1)
    else:
        print("Indexing complete.")


# =============================================================================
# Command: update
# =============================================================================

def cmd_update(args: argparse.Namespace) -> None:
    """Incremental update: detect changes, cascade staleness, selective reindex."""
    _setup_logging(args.verbose)
    workspace_path = Path(args.workspace_path).resolve()

    if not workspace_path.is_dir():
        print(f"ERROR: Not a directory: {workspace_path}")
        sys.exit(1)

    graph_path = _resolve_graph_path(args.graph, workspace_path)

    # Step 1: Load existing graph
    print(f"Loading graph from: {graph_path}")
    store = _load_graph(graph_path)
    stats_before = store.stats()

    # Import incremental modules (may not exist yet)
    try:
        from incremental.change_detector import (
            detect_changes, map_changes_to_graph,
        )
        from incremental.staleness import propagate_staleness, get_stale_summary
        from incremental.selective_reindex import selective_reindex
    except ImportError as e:
        print(f"ERROR: Incremental modules not available: {e}")
        print("  The incremental package is required for the 'update' command.")
        print("  Ensure incremental/ directory exists with:")
        print("    - change_detector.py")
        print("    - staleness.py")
        print("    - selective_reindex.py")
        sys.exit(1)

    passes = args.passes.split(",") if args.passes else ["treesitter", "patterns"]
    ref = args.ref

    # Step 2: Detect changes
    print(f"Detecting changes since {ref}...")
    try:
        changes = detect_changes(workspace_path, ref=ref)
    except Exception as e:
        print(f"ERROR detecting changes: {e}")
        sys.exit(1)

    if not changes:
        print("No changes detected. Graph is up to date.")
        return

    print(f"  Found {len(changes)} changed files.")
    for change in changes[:10]:
        status = getattr(change, "status", "modified")
        path = getattr(change, "path", str(change))
        print(f"    [{status}] {path}")
    if len(changes) > 10:
        print(f"    ... and {len(changes) - 10} more")

    # Step 3: Map changes to graph nodes
    print("Mapping changes to graph nodes...")
    affected_nodes = map_changes_to_graph(changes, store)
    print(f"  {len(affected_nodes)} graph nodes affected.")

    # Step 4: Propagate staleness
    print("Propagating staleness...")
    stale_count = propagate_staleness(store, affected_nodes)
    print(f"  {stale_count} nodes marked stale (including cascade).")

    # Step 5: Selective reindex
    print("Running selective reindex...")
    reindex_result = selective_reindex(
        store=store,
        workspace_path=workspace_path,
        changed_files=changes,
        passes=passes,
    )
    print(f"  Reindexed {getattr(reindex_result, 'files_processed', '?')} files.")

    # Step 6: Save updated graph
    print(f"Saving updated graph to: {graph_path}")
    store.save(graph_path)

    # Step 7: Print summary
    stats_after = store.stats()
    print(f"\n{'='*60}")
    print(f"  Incremental Update Summary")
    print(f"{'='*60}")
    print(f"  Workspace:    {workspace_path}")
    print(f"  Ref:          {ref}")
    print(f"  Files changed: {len(changes)}")
    print(f"  Nodes affected: {len(affected_nodes)}")
    print(f"  Nodes staled:   {stale_count}")
    print(f"{'-'*60}")
    print(f"  Nodes before:  {stats_before['total_nodes']}")
    print(f"  Nodes after:   {stats_after['total_nodes']}")
    print(f"  Edges before:  {stats_before['total_edges']}")
    print(f"  Edges after:   {stats_after['total_edges']}")
    print(f"  Stale nodes:   {stats_after['stale_nodes']}")
    print(f"  Stale edges:   {stats_after['stale_edges']}")
    print(f"{'='*60}\n")


# =============================================================================
# Command: status
# =============================================================================

def cmd_status(args: argparse.Namespace) -> None:
    """Show graph statistics and health."""
    workspace_path = Path(args.workspace_path).resolve()
    graph_path = _resolve_graph_path(getattr(args, "graph", None), workspace_path)

    store = _load_graph(graph_path)
    stats = store.stats()

    print(f"\n{'='*60}")
    print(f"  Workspace Intelligence - Graph Status")
    print(f"{'='*60}")
    print(f"  Graph file:   {graph_path}")
    print(f"  Total nodes:  {stats['total_nodes']}")
    print(f"  Total edges:  {stats['total_edges']}")
    print(f"  Stale nodes:  {stats['stale_nodes']}")
    print(f"  Stale edges:  {stats['stale_edges']}")

    # Health indicator
    total = stats["total_nodes"]
    stale = stats["stale_nodes"]
    if total > 0:
        health_pct = ((total - stale) / total) * 100
        if health_pct >= 90:
            health = "HEALTHY"
        elif health_pct >= 70:
            health = "DEGRADED"
        else:
            health = "STALE"
        print(f"  Health:       {health} ({health_pct:.0f}% fresh)")
    else:
        print(f"  Health:       EMPTY (no nodes)")

    # Node breakdown by tier
    if stats.get("nodes_by_tier"):
        print(f"{'-'*60}")
        print(f"  Nodes by tier:")
        for tier, count in stats["nodes_by_tier"].items():
            if count > 0:
                print(f"    {tier:>8}: {count}")

    # Top node types
    print(f"{'-'*60}")
    print(f"  Node types:")
    sorted_types = sorted(
        stats["nodes_by_type"].items(), key=lambda x: x[1], reverse=True
    )
    for ntype, count in sorted_types[:15]:
        if count > 0:
            print(f"    {ntype:>20}: {count}")

    # Top edge types
    sorted_edges = sorted(
        stats["edges_by_type"].items(), key=lambda x: x[1], reverse=True
    )
    active_edges = [(e, c) for e, c in sorted_edges if c > 0]
    if active_edges:
        print(f"{'-'*60}")
        print(f"  Edge types:")
        for etype, count in active_edges[:15]:
            print(f"    {etype:>20}: {count}")

    # Stale node details
    if stale > 0:
        print(f"{'-'*60}")
        print(f"  Stale nodes ({stale}):")
        stale_nodes = [n for n in store._nodes.values() if n.is_stale]
        for node in stale_nodes[:10]:
            print(f"    - [{node.type.value}] {node.name}")
        if len(stale_nodes) > 10:
            print(f"    ... and {len(stale_nodes) - 10} more")
        print(f"\n  Run 'python cli.py update' to refresh stale nodes.")

    print(f"{'='*60}\n")


# =============================================================================
# Command: query
# =============================================================================

def cmd_query(args: argparse.Namespace) -> None:
    """Search for a node by name and show its details."""
    graph_path = Path(args.graph).resolve() if args.graph else Path(DEFAULT_GRAPH_FILE).resolve()
    store = _load_graph(graph_path)

    name = args.node_name
    matches = _find_node(store, name)

    if not matches:
        print(f"No nodes found matching '{name}'.")
        print("  Try a broader search term, or check available nodes with 'status'.")
        return

    print(f"\nFound {len(matches)} node(s) matching '{name}':")
    print(f"{'='*60}")

    for node in matches[:5]:
        _print_node_detail(node, store, depth=args.depth)
        print(f"{'-'*60}")

    if len(matches) > 5:
        print(f"\n  Showing first 5 of {len(matches)} matches.")
        print(f"  Refine your search for more specific results.")

    print()


# =============================================================================
# Command: impact
# =============================================================================

def cmd_impact(args: argparse.Namespace) -> None:
    """Show impact analysis for a node."""
    graph_path = Path(args.graph).resolve() if args.graph else Path(DEFAULT_GRAPH_FILE).resolve()
    store = _load_graph(graph_path)

    name = args.node_name
    matches = _find_node(store, name)

    if not matches:
        print(f"No nodes found matching '{name}'.")
        return

    # Use the first match
    node = matches[0]
    if len(matches) > 1:
        print(f"Multiple matches found, showing impact for first: {node.name}")
        print(f"  Other matches: {', '.join(m.name for m in matches[1:5])}")
        print()

    depth = args.depth
    upstream = store.get_upstream(node.id, max_depth=depth)
    downstream = store.get_downstream(node.id, max_depth=depth)

    print(f"\n{'='*60}")
    print(f"  Impact Analysis: {node.name}")
    print(f"{'='*60}")
    print(f"  Type: {node.type.value}")
    print(f"  Tier: {node.tier.value}")
    print(f"  ID:   {node.id}")
    print(f"  Stale: {'YES' if node.is_stale else 'no'}")

    # What depends on this node (upstream = things that point TO it)
    print(f"\n{'-'*60}")
    print(f"  DEPENDS ON THIS ({len(upstream)} nodes, depth {depth}):")
    print(f"  (Changing this node may affect these)")
    if upstream:
        # Group by type
        by_type = {}
        for u in upstream:
            type_val = u.type.value
            if type_val not in by_type:
                by_type[type_val] = []
            by_type[type_val].append(u)

        for type_val, nodes in sorted(by_type.items()):
            print(f"\n    {type_val} ({len(nodes)}):")
            for n in nodes[:8]:
                stale_mark = " [STALE]" if n.is_stale else ""
                print(f"      <- {n.name}{stale_mark}")
            if len(nodes) > 8:
                print(f"      ... and {len(nodes) - 8} more")
    else:
        print(f"    (none)")

    # What this node depends on (downstream = things it points TO)
    print(f"\n{'-'*60}")
    print(f"  THIS DEPENDS ON ({len(downstream)} nodes, depth {depth}):")
    print(f"  (This node uses/calls these)")
    if downstream:
        by_type = {}
        for d in downstream:
            type_val = d.type.value
            if type_val not in by_type:
                by_type[type_val] = []
            by_type[type_val].append(d)

        for type_val, nodes in sorted(by_type.items()):
            print(f"\n    {type_val} ({len(nodes)}):")
            for n in nodes[:8]:
                stale_mark = " [STALE]" if n.is_stale else ""
                print(f"      -> {n.name}{stale_mark}")
            if len(nodes) > 8:
                print(f"      ... and {len(nodes) - 8} more")
    else:
        print(f"    (none)")

    # Risk summary
    print(f"\n{'-'*60}")
    total_impact = len(upstream) + len(downstream)
    if total_impact == 0:
        risk = "ISOLATED - no connections found"
    elif len(upstream) > 10:
        risk = f"HIGH - {len(upstream)} components depend on this"
    elif len(upstream) > 5:
        risk = f"MEDIUM - {len(upstream)} components depend on this"
    elif total_impact > 0:
        risk = f"LOW - {len(upstream)} upstream, {len(downstream)} downstream"
    else:
        risk = "MINIMAL"

    print(f"  Risk: {risk}")
    print(f"  Total blast radius: {total_impact} nodes")
    print(f"{'='*60}\n")


# =============================================================================
# Argument Parser
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="cli",
        description="Workspace Intelligence - Build and query code knowledge graphs",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -- index ----------------------------------------------------------------
    p_index = subparsers.add_parser(
        "index",
        help="Run the full pipeline on a workspace",
        description="Scan a workspace and build the intelligence graph from scratch.",
    )
    p_index.add_argument(
        "workspace_path",
        help="Path to the workspace directory to index",
    )
    p_index.add_argument(
        "-o", "--output",
        help=f"Output JSON file path (default: <workspace>/{DEFAULT_GRAPH_FILE})",
        default=None,
    )
    p_index.add_argument(
        "--depth", type=int, default=5,
        help="Max directory scan depth (default: 5)",
    )
    p_index.add_argument(
        "--passes",
        nargs="+",
        choices=["scan", "treesitter", "patterns"],
        default=None,
        help="Which passes to run (default: all)",
    )
    p_index.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose/debug logging",
    )
    p_index.set_defaults(func=cmd_index)

    # -- update ---------------------------------------------------------------
    p_update = subparsers.add_parser(
        "update",
        help="Incremental update from recent changes",
        description="Detect changes since a git ref, cascade staleness, and selectively reindex.",
    )
    p_update.add_argument(
        "workspace_path",
        nargs="?",
        default=".",
        help="Path to the workspace directory (default: current directory)",
    )
    p_update.add_argument(
        "--ref",
        default="HEAD~1",
        help="Git ref to compare against (default: HEAD~1)",
    )
    p_update.add_argument(
        "--passes",
        default="treesitter,patterns",
        help="Comma-separated passes to run (default: treesitter,patterns)",
    )
    p_update.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose/debug logging",
    )
    p_update.add_argument(
        "--graph",
        default=None,
        help=f"Path to existing graph JSON (default: <workspace>/{DEFAULT_GRAPH_FILE})",
    )
    p_update.set_defaults(func=cmd_update)

    # -- status ---------------------------------------------------------------
    p_status = subparsers.add_parser(
        "status",
        help="Show graph statistics and health",
        description="Load the graph and display node/edge counts, staleness, and health.",
    )
    p_status.add_argument(
        "workspace_path",
        nargs="?",
        default=".",
        help="Path to the workspace directory (default: current directory)",
    )
    p_status.add_argument(
        "--graph",
        default=None,
        help=f"Path to graph JSON (default: <workspace>/{DEFAULT_GRAPH_FILE})",
    )
    p_status.set_defaults(func=cmd_status)

    # -- query ----------------------------------------------------------------
    p_query = subparsers.add_parser(
        "query",
        help="Search for a node and show its details",
        description="Find a node by name (case-insensitive) and display connections.",
    )
    p_query.add_argument(
        "node_name",
        help="Node name to search for (case-insensitive substring match)",
    )
    p_query.add_argument(
        "--depth", type=int, default=2,
        help="Traversal depth for upstream/downstream (default: 2)",
    )
    p_query.add_argument(
        "--graph",
        default=None,
        help=f"Path to graph JSON (default: {DEFAULT_GRAPH_FILE})",
    )
    p_query.set_defaults(func=cmd_query)

    # -- impact ---------------------------------------------------------------
    p_impact = subparsers.add_parser(
        "impact",
        help="Show impact analysis for a node",
        description="Analyze what depends on a node and what it depends on.",
    )
    p_impact.add_argument(
        "node_name",
        help="Node name to analyze (case-insensitive substring match)",
    )
    p_impact.add_argument(
        "--depth", type=int, default=3,
        help="Traversal depth for impact analysis (default: 3)",
    )
    p_impact.add_argument(
        "--graph",
        default=None,
        help=f"Path to graph JSON (default: {DEFAULT_GRAPH_FILE})",
    )
    p_impact.set_defaults(func=cmd_impact)

    return parser


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
