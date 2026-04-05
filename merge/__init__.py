"""
Workspace Intelligence Layer - Merge Package
=============================================

Story 5.2: Conflict resolution for merging parallel analysis results.

When multiple passes (tree-sitter, regex, LLM) produce conflicting information
about the same entity, this package resolves them using a deterministic priority
chain:

  1. Higher confidence wins (>0.1 difference)
  2. More specific type wins (e.g., ENDPOINT > FUNCTION if confidence is close)
  3. Higher provenance trust wins: HUMAN > RUNTIME > SCANNER > LLM > IMPORT
  4. Tie -> flag for human review
"""

from merge.conflict_resolver import (  # noqa: E402
    Conflict,
    MergeResult,
    merge_graphs,
    resolve_node_conflict,
)

# Re-export for convenient `from merge import merge_graphs` usage.
# The import above uses the fully-qualified package path so that both
# `python -m merge.conflict_resolver` and `from merge import ...` work.

__all__ = [
    "Conflict",
    "MergeResult",
    "merge_graphs",
    "resolve_node_conflict",
]
