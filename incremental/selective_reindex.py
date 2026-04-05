"""
Workspace Intelligence Layer - Story 3.3: Selective Re-indexing
================================================================

Re-runs Passes 1-3 on stale files only, merging results into the existing
graph without replacing it wholesale.

Handles:
  - DELETED files: remove all nodes sourced from that file
  - MODIFIED files: remove old nodes from that file, then re-process
  - ADDED files: full processing as new
  - RENAMED files: update paths in existing nodes, or re-process

Supports skipping Pass 3 (LLM) to save cost.

Usage:
    from incremental.selective_reindex import selective_reindex
    result = selective_reindex(store, changeset, passes=["treesitter", "patterns"])
"""

import sys
import time
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass, field

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Provenance, SourceLocation
from pipeline.pass1_treesitter import TreeSitterPass
from pipeline.pass2_patterns import PatternPass
from incremental.change_detector import ChangeSet, ChangeType, FileChange

logger = logging.getLogger("workspace-intelligence.selective-reindex")


# =============================================================================
# RESULT DATA MODEL
# =============================================================================

@dataclass
class ReindexResult:
    """Result of a selective re-indexing run."""
    files_processed: int = 0
    nodes_added: int = 0
    nodes_updated: int = 0
    nodes_removed: int = 0
    edges_added: int = 0
    edges_removed: int = 0
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    passes_run: List[str] = field(default_factory=list)


# Language mapping (mirrored from orchestrator)
PROJECT_LANGUAGE_MAP = {
    "nodejs": "typescript",
    "python": "python",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "dotnet": "csharp",
}


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _normalize_path(file_path: Path) -> str:
    """
    Normalize a file path to its posix representation for matching
    against node locations and metadata.

    Nodes store paths using Path.as_posix() so we need to match consistently.
    """
    return file_path.resolve().as_posix()


def _path_matches_node(node: GraphNode, file_path_posix: str) -> bool:
    """
    Check whether a graph node was sourced from the given file path.

    Matches against:
      1. node.location.file_path (SourceLocation)
      2. node.metadata.get("relative_path")
      3. node.metadata.get("path")

    Uses suffix matching to handle absolute vs relative path differences.
    """
    candidates: List[str] = []

    if node.location and node.location.file_path:
        candidates.append(node.location.file_path)

    if node.metadata:
        for key in ("relative_path", "path"):
            val = node.metadata.get(key)
            if val:
                candidates.append(val)

    for candidate in candidates:
        # Normalize both sides to forward slashes for comparison
        candidate_normalized = candidate.replace("\\", "/")
        if (
            candidate_normalized == file_path_posix
            or file_path_posix.endswith("/" + candidate_normalized)
            or candidate_normalized.endswith("/" + file_path_posix)
            # Handle exact tail match (relative stored, absolute queried)
            or file_path_posix.endswith(candidate_normalized)
            or candidate_normalized.endswith(file_path_posix)
        ):
            return True

    return False


def _remove_nodes_for_file(store: GraphStore, file_path: Path) -> Tuple[int, int]:
    """
    Find and remove all nodes whose source location matches *file_path*.

    Also removes all edges connected to those nodes (GraphStore.delete_node
    handles edge cleanup).

    Args:
        store: The graph store to mutate.
        file_path: Absolute path of the file whose nodes should be purged.

    Returns:
        Tuple of (nodes_removed, edges_removed).
    """
    file_posix = _normalize_path(file_path)

    # Collect node IDs to delete (iterate over a snapshot to avoid mutation
    # during iteration).
    node_ids_to_delete: List[str] = []
    for node_id, node in list(store._nodes.items()):
        if _path_matches_node(node, file_posix):
            node_ids_to_delete.append(node_id)

    # Count edges that will be removed along with the nodes.
    edges_removed = 0
    for node_id in node_ids_to_delete:
        edges_removed += len(store.get_edges_from(node_id))
        edges_removed += len(store.get_edges_to(node_id))

    # Edges shared between two nodes being deleted would be double-counted,
    # but since GraphStore.delete_node silently skips already-missing edges
    # the functional result is still correct.  The count is an upper bound.

    for node_id in node_ids_to_delete:
        try:
            store.delete_node(node_id)
        except Exception as exc:
            logger.warning("Failed to delete node %s: %s", node_id, exc)

    return len(node_ids_to_delete), edges_removed


def _find_project_id(store: GraphStore, file_path: Path) -> Optional[str]:
    """
    Given a file path, find its parent project node ID in the graph.

    Searches through PROJECT nodes whose metadata["path"] (or whose
    name / id) is a parent directory of *file_path*.

    Returns:
        The project node's ID, or None if no matching project is found.
    """
    file_posix = _normalize_path(file_path)

    for node_id, node in store._nodes.items():
        if node.type != NodeType.PROJECT:
            continue

        # Check metadata["path"]
        project_path_raw = node.metadata.get("path", "")
        if project_path_raw:
            project_path_posix = str(project_path_raw).replace("\\", "/")
            if file_posix.startswith(project_path_posix.rstrip("/") + "/"):
                return node.id

        # Fallback: check if the project name is embedded in the file path
        # Project IDs are typically "project:{workspace}:{name}"
        parts = node.id.split(":")
        if len(parts) >= 3:
            project_name = parts[-1]
            if project_name and ("/" + project_name + "/" in file_posix):
                return node.id

    return None


def _count_nodes_for_file(store: GraphStore, file_path: Path) -> int:
    """Count how many nodes in the store originate from *file_path*."""
    file_posix = _normalize_path(file_path)
    count = 0
    for node in store._nodes.values():
        if _path_matches_node(node, file_posix):
            count += 1
    return count


def _process_file(
    store: GraphStore,
    file_path: Path,
    project_id: str,
    language: str,
    passes: List[str],
) -> Tuple[int, int]:
    """
    Run the specified pipeline passes on a single file.

    Args:
        store: The graph store to populate.
        file_path: Absolute path to the source file.
        project_id: Project node ID for scoping.
        language: Programming language identifier (e.g. "typescript").
        passes: Which passes to run (subset of ["treesitter", "patterns", "llm"]).

    Returns:
        (nodes_added, edges_added) -- counts of entities created by all passes.
    """
    nodes_before = len(store._nodes)
    edges_before = len(store._edges)

    # --- Pass 1: Tree-sitter AST extraction --------------------------------
    if "treesitter" in passes:
        try:
            ts_pass = TreeSitterPass(store)
            ts_pass.process_file(file_path, project_id, language)
        except Exception as exc:
            raise RuntimeError(f"Pass 1 (treesitter) failed on {file_path}: {exc}") from exc

    # --- Pass 2: Pattern matching ------------------------------------------
    if "patterns" in passes:
        try:
            pat_pass = PatternPass(store)
            pat_pass.process_file(file_path, project_id, language)
        except Exception as exc:
            raise RuntimeError(f"Pass 2 (patterns) failed on {file_path}: {exc}") from exc

    # --- Pass 3: LLM semantic enrichment (lazy import) ---------------------
    if "llm" in passes:
        try:
            from pipeline.pass3_llm import LLMPass
            llm_pass = LLMPass(store)
            llm_pass.process_file_sync(file_path, project_id, language)
        except ImportError as exc:
            raise RuntimeError(
                "Pass 3 (LLM) requires the LLM dependencies. "
                f"Import error: {exc}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Pass 3 (llm) failed on {file_path}: {exc}") from exc

    nodes_added = len(store._nodes) - nodes_before
    edges_added = len(store._edges) - edges_before

    return nodes_added, edges_added


def _update_paths_for_rename(
    store: GraphStore,
    old_path: Path,
    new_path: Path,
) -> int:
    """
    Update file paths in existing nodes when a file is renamed/moved.

    Patches:
      - node.location.file_path
      - node.metadata["relative_path"]
      - node.metadata["path"]
      - node.id (if it embeds the old posix path -- requires re-insertion)

    Returns:
        Number of nodes whose paths were updated.
    """
    old_posix = _normalize_path(old_path)
    new_posix = _normalize_path(new_path)
    updated = 0

    # Collect nodes that reference the old path.
    matching_nodes: List[GraphNode] = []
    for node in list(store._nodes.values()):
        if _path_matches_node(node, old_posix):
            matching_nodes.append(node)

    for node in matching_nodes:
        changed = False

        # Patch location
        if node.location and node.location.file_path:
            old_loc = node.location.file_path.replace("\\", "/")
            if old_loc == old_posix or old_posix.endswith(old_loc):
                node.location.file_path = new_posix
                changed = True

        # Patch metadata paths
        for key in ("relative_path", "path"):
            if key in node.metadata:
                val = str(node.metadata[key]).replace("\\", "/")
                if val == old_posix or old_posix.endswith(val):
                    node.metadata[key] = new_posix
                    changed = True

        # Patch node ID if it contains the old posix path
        old_id = node.id
        if old_posix in old_id or old_path.as_posix() in old_id:
            new_id = old_id.replace(old_path.as_posix(), new_path.as_posix())
            if new_id == old_id:
                # Try with the normalized posix form
                new_id = old_id.replace(old_posix, new_posix)

            if new_id != old_id:
                # Must remove old and re-add with new ID since ID is the dict key.
                # Preserve edges by updating them too.
                _rekey_node(store, old_id, new_id, node)
                changed = True

        if changed:
            # Re-add to refresh the NetworkX attributes.
            store.add_node(node)
            updated += 1

    return updated


def _rekey_node(
    store: GraphStore,
    old_id: str,
    new_id: str,
    node: GraphNode,
) -> None:
    """
    Change a node's ID in the store, updating all connected edges.

    This is a low-level operation: removes the old entry, updates the
    node object, inserts under the new key, and patches edge references.
    """
    # Collect edges referencing old_id before deletion.
    outgoing = list(store.get_edges_from(old_id))
    incoming = list(store.get_edges_to(old_id))

    # Remove old node (and its edges from the NetworkX graph + _edges dict).
    if old_id in store._nodes:
        store.delete_node(old_id)

    # Update node identity.
    node.id = new_id
    store.add_node(node)

    # Re-create edges with updated IDs.
    for edge in outgoing:
        edge.source_id = new_id
        store.add_edge(edge, validate=False)

    for edge in incoming:
        edge.target_id = new_id
        store.add_edge(edge, validate=False)


# =============================================================================
# PUBLIC API
# =============================================================================

def selective_reindex(
    store: GraphStore,
    changeset: ChangeSet,
    passes: Optional[List[str]] = None,
    project_language: str = "typescript",
) -> ReindexResult:
    """
    Re-run Passes 1-3 on stale files only, merging results into the existing graph.

    For each change in *changeset*:
      - DELETED: remove all graph nodes sourced from that file.
      - MODIFIED: remove old nodes, re-process with specified passes.
      - ADDED: process as new with specified passes.
      - RENAMED: attempt in-place path update; fall back to delete + re-add.

    Args:
        store: The existing GraphStore to update in-place.
        changeset: Set of file-level changes detected by change_detector.
        passes: Which pipeline passes to run.
                Defaults to ["treesitter", "patterns"].
                Add "llm" to include Pass 3 (costs money).
        project_language: Fallback language when project_id lookup fails.
                          One of: typescript, python, go, rust, java, csharp.

    Returns:
        ReindexResult with counts of what was added, updated, and removed.
    """
    if passes is None:
        passes = ["treesitter", "patterns"]

    start = time.perf_counter()
    result = ReindexResult(passes_run=list(passes))

    for change in changeset.changes:
        abs_path = changeset.repo_root / change.path

        try:
            if change.change_type == ChangeType.DELETED:
                _handle_deleted(store, abs_path, result)

            elif change.change_type == ChangeType.MODIFIED:
                _handle_modified(store, abs_path, passes, project_language, result)

            elif change.change_type == ChangeType.ADDED:
                _handle_added(store, abs_path, passes, project_language, result)

            elif change.change_type == ChangeType.RENAMED:
                _handle_renamed(
                    store, change, changeset.repo_root,
                    passes, project_language, result,
                )

            else:
                logger.warning(
                    "Unknown change type %s for %s, treating as MODIFIED",
                    change.change_type, change.path,
                )
                _handle_modified(store, abs_path, passes, project_language, result)

            result.files_processed += 1

        except Exception as exc:
            error_msg = f"Error processing {change.path} ({change.change_type}): {exc}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            result.files_processed += 1

    result.duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Selective reindex complete: %d files processed, "
        "+%d nodes, ~%d updated, -%d removed, +%d edges, -%d edges, "
        "%d errors in %.0fms",
        result.files_processed,
        result.nodes_added,
        result.nodes_updated,
        result.nodes_removed,
        result.edges_added,
        result.edges_removed,
        len(result.errors),
        result.duration_ms,
    )
    return result


# =============================================================================
# CHANGE TYPE HANDLERS
# =============================================================================

def _handle_deleted(
    store: GraphStore,
    abs_path: Path,
    result: ReindexResult,
) -> None:
    """Remove all graph nodes sourced from a deleted file."""
    nodes_removed, edges_removed = _remove_nodes_for_file(store, abs_path)
    result.nodes_removed += nodes_removed
    result.edges_removed += edges_removed
    logger.debug("DELETED %s: removed %d nodes, %d edges", abs_path, nodes_removed, edges_removed)


def _handle_modified(
    store: GraphStore,
    abs_path: Path,
    passes: List[str],
    project_language: str,
    result: ReindexResult,
) -> None:
    """Re-process a modified file: remove old nodes, then re-run passes."""
    # Count existing nodes for the "updated" stat.
    existing_count = _count_nodes_for_file(store, abs_path)

    # Remove old nodes from this file.
    nodes_removed, edges_removed = _remove_nodes_for_file(store, abs_path)
    result.nodes_removed += nodes_removed
    result.edges_removed += edges_removed

    # Determine project context.
    project_id = _find_project_id(store, abs_path)
    language = project_language
    if project_id:
        # Try to infer language from the project node.
        project_node = store.get_node(project_id)
        if project_node and project_node.language:
            language = project_node.language

    if not project_id:
        # Synthesize a project_id from the path so passes still work.
        project_id = f"project:unknown:{abs_path.parent.name}"
        logger.debug("No project node found for %s, using fallback: %s", abs_path, project_id)

    # Re-process the file.
    if abs_path.exists():
        nodes_added, edges_added = _process_file(store, abs_path, project_id, language, passes)
        result.nodes_added += nodes_added
        result.edges_added += edges_added
        # Nodes that existed before and were re-created count as "updated".
        result.nodes_updated += min(existing_count, nodes_added)
        logger.debug(
            "MODIFIED %s: removed %d, added %d nodes (%d updated), %d edges",
            abs_path, nodes_removed, nodes_added, min(existing_count, nodes_added), edges_added,
        )
    else:
        logger.warning("MODIFIED %s: file no longer exists on disk, treating as deleted", abs_path)


def _handle_added(
    store: GraphStore,
    abs_path: Path,
    passes: List[str],
    project_language: str,
    result: ReindexResult,
) -> None:
    """Process a newly added file with the full pipeline passes."""
    if not abs_path.exists():
        logger.warning("ADDED %s: file does not exist on disk, skipping", abs_path)
        return

    project_id = _find_project_id(store, abs_path)
    language = project_language
    if project_id:
        project_node = store.get_node(project_id)
        if project_node and project_node.language:
            language = project_node.language

    if not project_id:
        project_id = f"project:unknown:{abs_path.parent.name}"
        logger.debug("No project node found for %s, using fallback: %s", abs_path, project_id)

    nodes_added, edges_added = _process_file(store, abs_path, project_id, language, passes)
    result.nodes_added += nodes_added
    result.edges_added += edges_added
    logger.debug("ADDED %s: %d nodes, %d edges", abs_path, nodes_added, edges_added)


def _handle_renamed(
    store: GraphStore,
    change: FileChange,
    repo_root: Path,
    passes: List[str],
    project_language: str,
    result: ReindexResult,
) -> None:
    """
    Handle a renamed/moved file.

    Strategy:
      1. Try to update paths in-place (cheap, preserves node identity).
      2. If the old path had no nodes, treat as ADDED.
      3. If in-place update succeeded but the content also changed,
         fall through to MODIFIED handling for the new path.
    """
    old_abs = repo_root / change.old_path if change.old_path else None
    new_abs = repo_root / change.path

    if old_abs is None:
        # No old path info -- treat as a new addition.
        _handle_added(store, new_abs, passes, project_language, result)
        return

    # Try in-place path update.
    updated = _update_paths_for_rename(store, old_abs, new_abs)

    if updated > 0:
        result.nodes_updated += updated
        logger.debug("RENAMED %s -> %s: updated %d node paths in-place", old_abs, new_abs, updated)

        # If the file content also changed (common with renames), re-process.
        if new_abs.exists():
            # Check if content hash differs by comparing source_hash.
            # For simplicity, always re-process on rename to be safe.
            _handle_modified(store, new_abs, passes, project_language, result)
    else:
        # No existing nodes for old path -- just process as new.
        logger.debug("RENAMED %s -> %s: no existing nodes found, processing as ADDED", old_abs, new_abs)
        _handle_added(store, new_abs, passes, project_language, result)


# =============================================================================
# CLI / TESTING
# =============================================================================

def print_reindex_result(result: ReindexResult) -> None:
    """Print a human-readable summary of the reindex result."""
    print(f"\n{'='*60}")
    print(f"  Selective Re-index Result")
    print(f"{'='*60}")
    print(f"  Files processed:  {result.files_processed}")
    print(f"  Passes run:       {', '.join(result.passes_run)}")
    print(f"  Duration:         {result.duration_ms:.0f}ms")
    print(f"{'-'*60}")
    print(f"  Nodes added:      {result.nodes_added}")
    print(f"  Nodes updated:    {result.nodes_updated}")
    print(f"  Nodes removed:    {result.nodes_removed}")
    print(f"  Edges added:      {result.edges_added}")
    print(f"  Edges removed:    {result.edges_removed}")
    if result.errors:
        print(f"{'-'*60}")
        print(f"  Errors ({len(result.errors)}):")
        for err in result.errors[:10]:
            print(f"    - {err}")
        if len(result.errors) > 10:
            print(f"    ... and {len(result.errors) - 10} more")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    """
    Smoke test: create a minimal graph, simulate changes, and reindex.

    This block is for manual testing only -- it does NOT require a real
    codebase or git repository.
    """
    import argparse
    import tempfile
    import os

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Selective Re-index -- Story 3.3 smoke test"
    )
    parser.add_argument(
        "--workspace",
        help="Workspace directory to use (creates temp dir if omitted)",
        default=None,
    )
    parser.add_argument(
        "--graph",
        help="Path to an existing graph JSON to load",
        default=None,
    )
    parser.add_argument(
        "--passes",
        nargs="+",
        choices=["treesitter", "patterns", "llm"],
        default=["treesitter", "patterns"],
        help="Which passes to run (default: treesitter patterns)",
    )
    args = parser.parse_args()

    # --- Setup ---------------------------------------------------------------
    store = GraphStore()
    if args.graph:
        store.load(args.graph)
        print(f"Loaded graph from {args.graph}: {store.stats()['total_nodes']} nodes")

    if args.workspace:
        workspace = Path(args.workspace).resolve()
    else:
        # Create a temp workspace with a sample file for testing.
        workspace = Path(tempfile.mkdtemp(prefix="wi_reindex_test_"))
        sample_file = workspace / "sample.py"
        sample_file.write_text(
            "def hello():\n    print('hello world')\n\ndef goodbye():\n    print('goodbye')\n",
            encoding="utf-8",
        )
        print(f"Created temp workspace at: {workspace}")

    # --- Simulate a ChangeSet ------------------------------------------------
    # Build a synthetic changeset with an ADDED file.
    sample_files = list(workspace.glob("**/*.py"))
    if not sample_files:
        print("No Python files found in workspace. Nothing to test.")
        sys.exit(0)

    changes: List[FileChange] = []
    for f in sample_files[:5]:  # Limit to 5 files for smoke test
        rel = f.relative_to(workspace)
        changes.append(FileChange(
            path=rel,
            change_type=ChangeType.ADDED,
        ))

    changeset = ChangeSet(
        repo_root=workspace,
        changes=changes,
        base_ref="HEAD~1",
        head_ref="HEAD",
    )

    print(f"\nSimulated changeset: {len(changeset.changes)} files")
    for ch in changeset.changes:
        print(f"  {ch.change_type.value:>10}  {ch.path}")

    # --- Run selective reindex -----------------------------------------------
    result = selective_reindex(
        store=store,
        changeset=changeset,
        passes=args.passes,
        project_language="python",
    )

    print_reindex_result(result)

    # Print final graph stats
    stats = store.stats()
    print(f"Final graph: {stats['total_nodes']} nodes, {stats['total_edges']} edges")
