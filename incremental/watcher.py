"""
Workspace Intelligence Layer - File Watcher (Real-time Self-Healing)
====================================================================

Monitors a workspace directory for file changes and automatically
updates the graph in real-time: detect → cascade → reindex → save.

Uses watchdog for cross-platform file system monitoring with debouncing
to avoid rapid-fire updates.

Usage:
    from incremental.watcher import GraphWatcher

    watcher = GraphWatcher(
        workspace_path=Path("./my-project"),
        graph_path=Path("./graphs/my-project_graph.json"),
        on_update=lambda event: print(f"Graph updated: {event}"),
    )
    watcher.start()  # non-blocking
    watcher.stop()   # clean shutdown
"""

from __future__ import annotations

import logging
import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

# Path setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Provenance
from incremental.change_detector import (
    ChangeSet, ChangeType, FileChange, map_changes_to_graph,
)
from incremental.staleness import propagate_staleness
from incremental.selective_reindex import selective_reindex
from pipeline.pass2b_connections import ConnectionPass

logger = logging.getLogger("workspace-intelligence.watcher")


# Extensions to SKIP (binary/generated files we never want to track)
SKIP_EXTENSIONS = {
    # Binary / media
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".webm",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    # Generated / lock
    ".pyc", ".pyo", ".class", ".o", ".obj",
    ".map", ".min.js", ".min.css",
    ".lock",
    # Documents / databases / logs
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".db", ".sqlite", ".sqlite3",
    ".log",
    # OS metadata
    ".DS_Store",
}

# Directories to always ignore
IGNORE_DIRS = {
    "node_modules", "__pycache__", ".git", "venv", ".venv",
    "dist", "build", ".next", ".nuxt", "target", "bin", "obj",
    ".tox", ".mypy_cache", ".pytest_cache", "coverage",
}

# Extension → language for Pass 2b
_EXT_LANG_MAP = {
    ".js": "typescript", ".ts": "typescript", ".jsx": "typescript",
    ".tsx": "typescript", ".mjs": "typescript", ".cjs": "typescript",
    ".py": "python", ".pyi": "python",
    ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp",
}


def _ext_to_language(ext: str) -> Optional[str]:
    """Map file extension to language name for Pass 2b."""
    return _EXT_LANG_MAP.get(ext.lower())


@dataclass
class UpdateEvent:
    """Describes what happened during a graph update."""
    timestamp: float
    changed_files: List[str]
    nodes_affected: int
    nodes_stale: int
    nodes_added: int
    nodes_removed: int
    edges_after: int
    duration_ms: float
    graph_path: str


class _DebouncedHandler(FileSystemEventHandler):
    """
    Collects file system events and debounces them before triggering
    an update. Avoids running the pipeline on every keystroke/save.
    """

    def __init__(self, watcher: "GraphWatcher", debounce_ms: int = 800):
        super().__init__()
        self._watcher = watcher
        self._debounce_s = debounce_ms / 1000.0
        self._pending: Dict[str, ChangeType] = {}
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def _should_track(self, path: str) -> bool:
        """Check if this file change is relevant (track all files except binary/generated)."""
        p = Path(path)
        # Ignore directories we don't care about
        for part in p.parts:
            if part in IGNORE_DIRS:
                return False
        # Skip binary/generated files, track everything else
        return p.suffix.lower() not in SKIP_EXTENSIONS

    def _schedule_update(self):
        """Reset the debounce timer."""
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self._debounce_s, self._flush)
        self._timer.daemon = True
        self._timer.start()

    def _flush(self):
        """Debounce expired — run the update pipeline."""
        with self._lock:
            if not self._pending:
                return
            changes = dict(self._pending)
            self._pending.clear()

        self._watcher._run_update(changes)

    def on_created(self, event: FileSystemEvent):
        if event.is_directory or not self._should_track(event.src_path):
            return
        with self._lock:
            self._pending[event.src_path] = ChangeType.ADDED
        self._schedule_update()

    def on_modified(self, event: FileSystemEvent):
        if event.is_directory or not self._should_track(event.src_path):
            return
        with self._lock:
            # Don't downgrade ADDED to MODIFIED
            if event.src_path not in self._pending:
                self._pending[event.src_path] = ChangeType.MODIFIED
        self._schedule_update()

    def on_deleted(self, event: FileSystemEvent):
        if event.is_directory or not self._should_track(event.src_path):
            return
        with self._lock:
            self._pending[event.src_path] = ChangeType.DELETED
        self._schedule_update()

    def on_moved(self, event: FileSystemEvent):
        if event.is_directory:
            return
        if self._should_track(event.src_path) or self._should_track(event.dest_path):
            with self._lock:
                self._pending[event.src_path] = ChangeType.DELETED
                self._pending[event.dest_path] = ChangeType.ADDED
            self._schedule_update()


class GraphWatcher:
    """
    Watches a workspace directory and keeps the graph up-to-date in real-time.

    Args:
        workspace_path: Root directory of the project to watch.
        graph_path: Path to the graph JSON file to update.
        on_update: Optional callback invoked after each graph update.
        debounce_ms: Milliseconds to wait after last change before updating.
        passes: Which pipeline passes to run on changed files.
    """

    def __init__(
        self,
        workspace_path: Path,
        graph_path: Path,
        on_update: Optional[Callable[[UpdateEvent], None]] = None,
        debounce_ms: int = 800,
        passes: Optional[List[str]] = None,
    ):
        self.workspace_path = Path(workspace_path).resolve()
        self.graph_path = Path(graph_path).resolve()
        self.on_update = on_update
        self.passes = passes or ["treesitter", "patterns"]
        self._store = GraphStore()
        self._observer: Optional[Observer] = None
        self._handler = _DebouncedHandler(self, debounce_ms=debounce_ms)
        self._lock = threading.Lock()
        self._running = False
        self._update_count = 0
        self._pending_contains: List[tuple] = []

    def start(self) -> None:
        """Start watching (non-blocking). Loads the graph first."""
        if self._running:
            return

        # Load existing graph
        if self.graph_path.is_file():
            logger.info(f"Loading graph: {self.graph_path}")
            self._store.load(self.graph_path)
            stats = self._store.stats()
            logger.info(
                f"  Loaded: {stats['total_nodes']} nodes, {stats['total_edges']} edges"
            )
        else:
            logger.warning(f"No graph file at {self.graph_path} — starting empty.")

        # Start watchdog observer
        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(self.workspace_path),
            recursive=True,
        )
        self._observer.daemon = True
        self._observer.start()
        self._running = True

        logger.info(f"Watching: {self.workspace_path}")
        logger.info(f"Graph:    {self.graph_path}")
        logger.info(f"Passes:   {', '.join(self.passes)}")

    def _find_project_id(self) -> str:
        """Find the project node ID in the graph, or create a fallback."""
        workspace_name = self.workspace_path.name
        for nid in self._store.graph.nodes():
            if "project:" in nid and workspace_name.lower() in nid.lower():
                return nid
        return f"project:{workspace_name}"

    def stop(self) -> None:
        """Stop watching and clean up."""
        if not self._running:
            return
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        logger.info(f"Watcher stopped. {self._update_count} updates performed.")

    def _run_update(self, changes: Dict[str, ChangeType]) -> None:
        """Execute the self-healing pipeline on detected changes."""
        with self._lock:
            self._pending_contains.clear()
            t0 = time.time()

            # Build a ChangeSet from the file system events
            file_changes = []
            for path_str, change_type in changes.items():
                rel_path = Path(path_str).relative_to(self.workspace_path)
                file_changes.append(FileChange(
                    path=rel_path,
                    change_type=change_type,
                ))

            changeset = ChangeSet(
                repo_root=self.workspace_path,
                ref_range="live-watcher",
                changes=file_changes,
            )

            changed_names = [str(fc.path) for fc in file_changes]
            logger.info(
                f"Update triggered: {len(file_changes)} file(s) changed"
            )
            for fc in file_changes:
                logger.info(f"  [{fc.change_type.value}] {fc.path}")

            try:
                # Step 0: Ensure Module nodes exist for new directories
                project_id = self._find_project_id()
                for fc in file_changes:
                    if fc.change_type == ChangeType.DELETED:
                        continue
                    abs_path = self.workspace_path / fc.path
                    if not abs_path.exists():
                        continue
                    # Walk from file's parent up to workspace root, create missing Modules
                    current = abs_path.parent
                    while current != self.workspace_path and current != self.workspace_path.parent:
                        relative = current.relative_to(self.workspace_path)
                        module_name = str(relative).replace("\\", "/")
                        module_id = f"module:{project_id}:{module_name}"
                        if self._store.get_node(module_id):
                            break  # Already exists, parents must too
                        # Determine parent
                        if current.parent == self.workspace_path:
                            parent_id = project_id
                        else:
                            parent_rel = current.parent.relative_to(self.workspace_path)
                            parent_name = str(parent_rel).replace("\\", "/")
                            parent_id = f"module:{project_id}:{parent_name}"
                        module_node = GraphNode(
                            id=module_id,
                            type=NodeType.MODULE,
                            name=current.name + "/",
                            description=f"Directory: {module_name}",
                            parent_id=parent_id,
                            provenance=Provenance.SCANNER,
                            confidence=1.0,
                            metadata={"path": str(current), "relative_path": module_name},
                        )
                        self._store.add_node(module_node)
                        contains_edge = GraphEdge(
                            source_id=parent_id,
                            target_id=module_id,
                            type=EdgeType.CONTAINS,
                            provenance=Provenance.SCANNER,
                            confidence=1.0,
                        )
                        self._store.add_edge(contains_edge, validate=False)
                        logger.info(f"  [module] Created {module_name}/")
                        current = current.parent

                    # Also ensure CONTAINS edge from module → file after reindex
                    # (selective_reindex creates the File node, we add CONTAINS here)
                    file_id = f"file:{project_id}:{abs_path.resolve().as_posix()}"
                    if abs_path.parent == self.workspace_path:
                        file_parent_id = project_id
                    else:
                        rel = abs_path.parent.relative_to(self.workspace_path)
                        file_parent_id = f"module:{project_id}:{str(rel).replace(chr(92), '/')}"
                    # Defer CONTAINS edge creation until after reindex creates the File node
                    self._pending_contains.append((file_parent_id, file_id))

                # Step 1: Map changes to graph nodes
                changeset = map_changes_to_graph(changeset, self._store)

                # Step 2: Propagate staleness
                stale_count = 0
                if changeset.affected_node_ids:
                    cascade_report = propagate_staleness(
                        self._store, changeset.affected_node_ids, hops=2,
                    )
                    stale_count = cascade_report.total_newly_stale

                # Step 3: Selective reindex (tree-sitter + patterns)
                reindex_result = selective_reindex(
                    store=self._store,
                    changeset=changeset,
                    passes=self.passes,
                )

                # Step 3b: Create CONTAINS edges for new files
                for parent_id, file_id in self._pending_contains:
                    if self._store.get_node(file_id):
                        contains_edge = GraphEdge(
                            source_id=parent_id,
                            target_id=file_id,
                            type=EdgeType.CONTAINS,
                            provenance=Provenance.SCANNER,
                            confidence=1.0,
                        )
                        self._store.add_edge(contains_edge, validate=False)
                self._pending_contains.clear()

                # Step 4: Pass 2b — re-extract behavioral edges for changed files
                try:
                    conn_pass = ConnectionPass(self._store)
                    conn_files = []
                    for fc in file_changes:
                        if fc.change_type == ChangeType.DELETED:
                            continue
                        abs_path = self.workspace_path / fc.path
                        if abs_path.is_file():
                            # Detect language from extension
                            lang = _ext_to_language(abs_path.suffix)
                            if lang:
                                # Find parent project node
                                project_id = self._find_project_id()
                                conn_files.append((abs_path, project_id, lang))
                    if conn_files:
                        conn_pass.process_all(conn_files)
                except Exception as e:
                    logger.warning(f"Pass 2b error: {e}")

                # Step 5: Save
                self._store.save(self.graph_path)

                duration_ms = (time.time() - t0) * 1000
                self._update_count += 1
                stats = self._store.stats()

                logger.info(
                    f"  Done in {duration_ms:.0f}ms: "
                    f"+{reindex_result.nodes_added} -{reindex_result.nodes_removed} nodes, "
                    f"{stale_count} stale, "
                    f"{stats['total_nodes']} total nodes, "
                    f"{stats['total_edges']} total edges"
                )

                # Notify callback
                if self.on_update:
                    event = UpdateEvent(
                        timestamp=time.time(),
                        changed_files=changed_names,
                        nodes_affected=len(changeset.affected_node_ids),
                        nodes_stale=stale_count,
                        nodes_added=reindex_result.nodes_added,
                        nodes_removed=reindex_result.nodes_removed,
                        edges_after=stats["total_edges"],
                        duration_ms=duration_ms,
                        graph_path=str(self.graph_path),
                    )
                    try:
                        self.on_update(event)
                    except Exception as e:
                        logger.warning(f"on_update callback error: {e}")

            except Exception as e:
                self._pending_contains.clear()
                logger.error(f"Update pipeline error: {e}", exc_info=True)
