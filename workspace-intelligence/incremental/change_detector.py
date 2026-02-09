"""
Workspace Intelligence Layer - Change Detector (Story 3.1)
==========================================================

Parses git diff output to identify changed files and maps them to
existing graph nodes, enabling selective re-indexing instead of
full workspace re-scans.

Supports:
  - Committed changes between any two refs (default: HEAD~1..HEAD)
  - Uncommitted changes (staged + unstaged)
  - Mapping changed files to graph node IDs
  - Grouping files by change type for the selective reindexer

Usage:
    from incremental.change_detector import detect_changes, map_changes_to_graph

    changeset = detect_changes(Path("./my-repo"))
    changeset = map_changes_to_graph(changeset, graph_store)
    to_reindex = get_files_to_reindex(changeset)
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Project imports -- add parent directory so we can import graph_store/ontology
# when running as a sub-package or standalone.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import GraphNode, NodeType

logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODEL
# =============================================================================

class ChangeType(str, Enum):
    """Classification of how a file was changed."""
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass
class FileChange:
    """A single file change detected from git diff."""
    path: Path                           # relative path from repo root
    change_type: ChangeType
    old_path: Optional[Path] = None      # populated for renames


@dataclass
class ChangeSet:
    """
    Complete set of changes between two git refs (or uncommitted state).

    ``affected_node_ids`` is populated by ``map_changes_to_graph`` after
    initial detection.
    """
    repo_root: Path
    ref_range: str                       # e.g. "HEAD~1..HEAD" or "uncommitted"
    changes: List[FileChange] = field(default_factory=list)
    affected_node_ids: Set[str] = field(default_factory=set)

    # ----- convenience accessors -----

    @property
    def added(self) -> List[FileChange]:
        return [c for c in self.changes if c.change_type == ChangeType.ADDED]

    @property
    def modified(self) -> List[FileChange]:
        return [c for c in self.changes if c.change_type == ChangeType.MODIFIED]

    @property
    def deleted(self) -> List[FileChange]:
        return [c for c in self.changes if c.change_type == ChangeType.DELETED]

    @property
    def renamed(self) -> List[FileChange]:
        return [c for c in self.changes if c.change_type == ChangeType.RENAMED]

    def summary(self) -> str:
        """Human-readable one-line summary."""
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.modified:
            parts.append(f"{len(self.modified)} modified")
        if self.deleted:
            parts.append(f"{len(self.deleted)} deleted")
        if self.renamed:
            parts.append(f"{len(self.renamed)} renamed")
        body = ", ".join(parts) if parts else "no changes"
        return f"[{self.ref_range}] {body}"


# =============================================================================
# GIT DIFF PARSING
# =============================================================================

# Map git status letters to our ChangeType enum.
_STATUS_MAP: Dict[str, ChangeType] = {
    "A": ChangeType.ADDED,
    "M": ChangeType.MODIFIED,
    "D": ChangeType.DELETED,
    # R (rename) is handled separately because it has a similarity score suffix
}


def _parse_name_status(output: str) -> List[FileChange]:
    """
    Parse the output of ``git diff --name-status``.

    Each line looks like one of:
        A\tpath/to/file
        M\tpath/to/file
        D\tpath/to/file
        R100\told/path\tnew/path
        R085\told/path\tnew/path

    Returns a list of ``FileChange`` objects.
    """
    changes: List[FileChange] = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split("\t")
        if len(parts) < 2:
            logger.warning("Skipping unparseable git diff line: %r", line)
            continue

        status_code = parts[0]

        # Rename: status code starts with 'R' followed by similarity percentage
        if status_code.startswith("R"):
            old_path = Path(parts[1])
            new_path = Path(parts[2]) if len(parts) > 2 else old_path
            changes.append(FileChange(
                path=new_path,
                change_type=ChangeType.RENAMED,
                old_path=old_path,
            ))
        # Copy: treat as added (the new copy is effectively a new file)
        elif status_code.startswith("C"):
            new_path = Path(parts[2]) if len(parts) > 2 else Path(parts[1])
            changes.append(FileChange(
                path=new_path,
                change_type=ChangeType.ADDED,
            ))
        else:
            change_type = _STATUS_MAP.get(status_code)
            if change_type is None:
                logger.warning(
                    "Unknown git status code %r in line: %r",
                    status_code, line,
                )
                continue
            changes.append(FileChange(
                path=Path(parts[1]),
                change_type=change_type,
            ))

    return changes


def _run_git(args: List[str], cwd: Path) -> Optional[str]:
    """
    Run a git command and return stdout, or ``None`` on failure.

    Handles repos with no history, missing git binary, and other error
    conditions gracefully.
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.debug(
                "git %s returned %d: %s",
                " ".join(args), result.returncode, stderr,
            )
            return None
        return result.stdout
    except FileNotFoundError:
        logger.warning("git executable not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("git command timed out: git %s", " ".join(args))
        return None
    except Exception:
        logger.exception("Unexpected error running git %s", " ".join(args))
        return None


# =============================================================================
# PUBLIC API
# =============================================================================

def detect_changes(repo_path: Path, ref: str = "HEAD~1") -> ChangeSet:
    """
    Detect file changes between ``ref`` and HEAD in the given repository.

    Args:
        repo_path: Path to the repository root (or any directory inside it).
        ref:       Git ref to diff against HEAD (default ``HEAD~1``).
                   Can be a commit hash, branch name, tag, or ref expression.

    Returns:
        A ``ChangeSet`` with all detected file changes.  Returns an empty
        ``ChangeSet`` if the repo has no git history or the command fails.
    """
    repo_path = Path(repo_path).resolve()
    ref_range = f"{ref}..HEAD"

    output = _run_git(["diff", "--name-status", ref], cwd=repo_path)
    if output is None:
        return ChangeSet(repo_root=repo_path, ref_range=ref_range)

    changes = _parse_name_status(output)
    return ChangeSet(
        repo_root=repo_path,
        ref_range=ref_range,
        changes=changes,
    )


def detect_uncommitted(repo_path: Path) -> ChangeSet:
    """
    Detect all uncommitted changes (both staged and unstaged).

    Runs ``git diff --name-status HEAD`` which captures everything that
    differs from the last commit, regardless of staging state.  Falls back
    to ``git diff --name-status`` (unstaged only) if HEAD is not available
    (empty repo).

    Args:
        repo_path: Path to the repository root.

    Returns:
        A ``ChangeSet`` with all uncommitted file changes.
    """
    repo_path = Path(repo_path).resolve()

    # Try HEAD first (staged + unstaged vs last commit)
    output = _run_git(["diff", "--name-status", "HEAD"], cwd=repo_path)

    if output is None:
        # Fallback: maybe there are no commits yet; try unstaged only
        output = _run_git(["diff", "--name-status"], cwd=repo_path)

    if output is None:
        # Also check for untracked files (new files not yet staged)
        return _detect_untracked(repo_path)

    changes = _parse_name_status(output)

    # Merge in untracked files (git diff won't show them)
    untracked = _get_untracked_files(repo_path)
    for upath in untracked:
        changes.append(FileChange(
            path=Path(upath),
            change_type=ChangeType.ADDED,
        ))

    return ChangeSet(
        repo_root=repo_path,
        ref_range="uncommitted",
        changes=changes,
    )


def _get_untracked_files(repo_path: Path) -> List[str]:
    """Return list of untracked file paths relative to repo root."""
    output = _run_git(
        ["ls-files", "--others", "--exclude-standard"],
        cwd=repo_path,
    )
    if not output:
        return []
    return [line.strip() for line in output.strip().splitlines() if line.strip()]


def _detect_untracked(repo_path: Path) -> ChangeSet:
    """Build a ChangeSet from only untracked files (no git history at all)."""
    untracked = _get_untracked_files(repo_path)
    changes = [
        FileChange(path=Path(p), change_type=ChangeType.ADDED)
        for p in untracked
    ]
    return ChangeSet(
        repo_root=repo_path,
        ref_range="uncommitted",
        changes=changes,
    )


# =============================================================================
# GRAPH MAPPING
# =============================================================================

def _normalize_path(p: Path) -> str:
    """
    Normalize a path for comparison: forward slashes, lowercase, no leading
    dot-slash or slash.
    """
    return str(p).replace("\\", "/").lower().strip("/").lstrip("./")


def map_changes_to_graph(changeset: ChangeSet, store: GraphStore) -> ChangeSet:
    """
    Map changed files in the ``changeset`` to existing graph node IDs.

    Matching strategy (checked in order for each node):
      1. Node ``metadata["path"]`` ends with or contains the relative file path.
      2. Node ``id`` contains the relative file path.

    For renames, both the old and new paths are checked.

    Args:
        changeset: A ``ChangeSet`` from ``detect_changes`` or
                   ``detect_uncommitted``.
        store:     The ``GraphStore`` containing the current workspace graph.

    Returns:
        The same ``changeset`` with ``affected_node_ids`` populated.
    """
    # Collect all relative paths we need to match (including old_path for renames)
    search_paths: List[str] = []
    for change in changeset.changes:
        search_paths.append(_normalize_path(change.path))
        if change.old_path is not None:
            search_paths.append(_normalize_path(change.old_path))

    if not search_paths:
        return changeset

    # Scan all nodes in the graph for matches
    matched_ids: Set[str] = set()

    # Check all node types that could represent files (FILE is the primary one,
    # but other node types may also carry file path metadata).
    for node_id, node in store._nodes.items():
        node_path_meta = node.metadata.get("path", "")
        if node_path_meta:
            normalized_meta = _normalize_path(Path(node_path_meta))
        else:
            normalized_meta = ""

        normalized_id = node_id.lower().replace("\\", "/")

        for sp in search_paths:
            # Strategy 1: metadata path match
            if normalized_meta and (
                normalized_meta.endswith(sp) or sp in normalized_meta
            ):
                matched_ids.add(node_id)
                break

            # Strategy 2: node ID contains the relative path
            if sp in normalized_id:
                matched_ids.add(node_id)
                break

    changeset.affected_node_ids = matched_ids
    return changeset


# =============================================================================
# REINDEX HELPER
# =============================================================================

def get_files_to_reindex(changeset: ChangeSet) -> Dict[ChangeType, List[Path]]:
    """
    Group changed files by change type for the selective reindexer.

    Returns a dict keyed by ``ChangeType`` with lists of relative paths.
    Only change types that have at least one file are included.

    Example::

        {
            ChangeType.ADDED: [Path("src/new_module.py")],
            ChangeType.MODIFIED: [Path("src/existing.py"), Path("tests/test_x.py")],
            ChangeType.DELETED: [Path("src/old_code.py")],
        }
    """
    result: Dict[ChangeType, List[Path]] = {}
    for change in changeset.changes:
        result.setdefault(change.change_type, []).append(change.path)
    return result


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Detect file changes in a git repository.",
    )
    parser.add_argument(
        "repo",
        nargs="?",
        default=".",
        help="Path to the git repository (default: current directory)",
    )
    parser.add_argument(
        "--ref",
        default="HEAD~1",
        help="Git ref to diff against HEAD (default: HEAD~1)",
    )
    parser.add_argument(
        "--uncommitted",
        action="store_true",
        help="Detect uncommitted (staged + unstaged) changes instead",
    )
    parser.add_argument(
        "--graph",
        type=str,
        default=None,
        help="Path to a saved graph JSON file to map changes to node IDs",
    )

    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    print(f"Repository: {repo}")
    print()

    # Detect changes
    if args.uncommitted:
        cs = detect_uncommitted(repo)
    else:
        cs = detect_changes(repo, ref=args.ref)

    print(f"Summary: {cs.summary()}")
    print()

    # Print changes by type
    reindex_groups = get_files_to_reindex(cs)
    for ct, paths in reindex_groups.items():
        print(f"  {ct.value.upper()} ({len(paths)}):")
        for p in paths:
            print(f"    {p}")
    print()

    # Optionally map to graph
    if args.graph:
        graph_path = Path(args.graph)
        if graph_path.exists():
            store = GraphStore()
            store.load(graph_path)
            cs = map_changes_to_graph(cs, store)
            print(f"Affected graph nodes ({len(cs.affected_node_ids)}):")
            for nid in sorted(cs.affected_node_ids):
                node = store.get_node(nid)
                label = f" ({node.name})" if node else ""
                print(f"  {nid}{label}")
        else:
            print(f"Graph file not found: {graph_path}")
    else:
        print("(No --graph file provided; skipping node mapping)")
