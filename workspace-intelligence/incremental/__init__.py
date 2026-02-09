"""
Workspace Intelligence Layer - Incremental Package

Incremental update pipeline for keeping the workspace graph in sync
with source code changes without full re-indexing.

Story 3.1: Change detection via git diff
Story 3.2: Selective re-indexing (future)
Story 3.3: Stale cascade and graph maintenance (future)
"""

from .change_detector import (
    ChangeType,
    FileChange,
    ChangeSet,
    detect_changes,
    detect_uncommitted,
    map_changes_to_graph,
    get_files_to_reindex,
)

__all__ = [
    "ChangeType",
    "FileChange",
    "ChangeSet",
    "detect_changes",
    "detect_uncommitted",
    "map_changes_to_graph",
    "get_files_to_reindex",
]
