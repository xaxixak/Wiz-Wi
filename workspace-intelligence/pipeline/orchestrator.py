"""
Workspace Intelligence Layer - Pipeline Orchestrator

Runs the multi-pass pipeline to build a workspace intelligence graph.

Pipeline passes:
  Pass 0: Workspace scanner + bridge (FREE) — discovers projects, creates top-level nodes
  Pass 1: Tree-sitter AST extraction (FREE) — creates File/Function/Class nodes
  Pass 2: Regex pattern matching (FREE) — detects endpoints, models, events, etc.
  Pass 2b: Behavioral connections (FREE) — CALLS, READS_DB, EMITS_EVENT, etc.
  Pass 3: LLM semantic enrichment (PAID) — future
  Pass 4: Validation & scoring (FREE) — future
"""

import sys
import time
import logging
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from scanner import WorkspaceScanner, ScanResult
from bridge import scan_result_to_graph
from pipeline.pass1_treesitter import TreeSitterPass
from pipeline.pass2_patterns import PatternPass
from pipeline.pass2b_connections import ConnectionPass

logger = logging.getLogger("workspace-intelligence")


# Language file extensions for each project type
LANGUAGE_EXTENSIONS = {
    "typescript": {".ts", ".tsx", ".js", ".jsx"},
    "javascript": {".js", ".jsx"},
    "python": {".py"},
    "go": {".go"},
    "rust": {".rs"},
    "java": {".java"},
    "csharp": {".cs"},
}

# Map project type to language for tree-sitter
PROJECT_LANGUAGE_MAP = {
    "nodejs": "typescript",
    "python": "python",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "dotnet": "csharp",
}


@dataclass
class PipelineResult:
    """Result of running the pipeline."""
    workspace_path: Path
    scan_result: ScanResult
    store: GraphStore
    duration_ms: float
    passes_run: List[str]
    errors: List[str]


def _collect_source_files(project_path: Path, language: str) -> List[Path]:
    """Collect all source files in a project directory for a given language."""
    extensions = LANGUAGE_EXTENSIONS.get(language, set())
    if not extensions:
        return []

    import os
    source_files = []
    skip_dirs = {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "dist", "build", "target", ".next", ".nuxt", ".turbo",
        ".nx", "coverage", ".pytest_cache", ".mypy_cache",
    }

    for root, dirs, files in os.walk(project_path):
        # Prune skip directories
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if Path(f).suffix in extensions:
                source_files.append(Path(root) / f)

    return source_files


def run_pipeline(
    workspace_path: str | Path,
    output_path: Optional[str | Path] = None,
    max_depth: int = 5,
    passes: Optional[List[str]] = None,
) -> PipelineResult:
    """
    Run the workspace intelligence pipeline.

    Args:
        workspace_path: Root directory to scan.
        output_path: Where to save the graph JSON (optional).
        max_depth: Max directory depth for scanner.
        passes: Which passes to run (default: all available).
                Options: ["scan", "treesitter", "patterns", "connections"]

    Returns:
        PipelineResult with the populated GraphStore.
    """
    workspace_path = Path(workspace_path).resolve()
    if passes is None:
        passes = ["scan", "treesitter", "patterns", "connections"]

    start = time.perf_counter()
    store = GraphStore()
    errors: List[str] = []
    passes_run: List[str] = []
    scan_result = None

    # -- Pass 0: Scanner + Bridge --------------------------------------
    if "scan" in passes:
        logger.info("Pass 0: Scanning workspace...")
        scanner = WorkspaceScanner(workspace_path, max_depth=max_depth)
        scan_result = scanner.scan()
        scan_result_to_graph(scan_result, store)
        passes_run.append("scan")
        logger.info(
            f"  Found {len(scan_result.projects)} projects, "
            f"scanned {scan_result.total_files_scanned} entries"
        )

    if scan_result is None:
        scan_result = ScanResult(
            workspace_root=workspace_path, projects=[], infra_paths=[]
        )

    # -- Pass 1: Tree-sitter AST Extraction ----------------------------
    if "treesitter" in passes and scan_result.projects:
        logger.info("Pass 1: Tree-sitter AST extraction...")
        ts_pass = TreeSitterPass(store)
        total_files = 0
        total_nodes = 0

        for project in scan_result.projects:
            language = PROJECT_LANGUAGE_MAP.get(project.project_type.value, "")
            if not language:
                continue

            project_id = f"project:{workspace_path.name}:{project.name}"
            source_files = _collect_source_files(project.path, language)

            for file_path in source_files:
                try:
                    nodes = ts_pass.process_file(file_path, project_id, language)
                    total_files += 1
                    total_nodes += len(nodes)
                except Exception as e:
                    errors.append(f"Pass 1 error on {file_path}: {e}")

        passes_run.append("treesitter")
        logger.info(f"  Processed {total_files} files, created {total_nodes} nodes")

    # -- Pass 2: Regex Pattern Matching --------------------------------
    if "patterns" in passes and scan_result.projects:
        logger.info("Pass 2: Regex pattern matching...")
        pat_pass = PatternPass(store)
        total_files = 0
        total_nodes = 0

        for project in scan_result.projects:
            language = PROJECT_LANGUAGE_MAP.get(project.project_type.value, "")
            if not language:
                continue

            project_id = f"project:{workspace_path.name}:{project.name}"
            source_files = _collect_source_files(project.path, language)

            for file_path in source_files:
                try:
                    nodes = pat_pass.process_file(file_path, project_id, language)
                    total_files += 1
                    total_nodes += len(nodes)
                except Exception as e:
                    errors.append(f"Pass 2 error on {file_path}: {e}")

        passes_run.append("patterns")
        logger.info(f"  Processed {total_files} files, found {total_nodes} patterns")

    # -- Pass 2b: Behavioral Connections --------------------------------
    if "connections" in passes and scan_result.projects:
        logger.info("Pass 2b: Behavioral connection extraction...")
        conn_pass = ConnectionPass(store)

        # Collect all source files with project context
        all_files = []
        for project in scan_result.projects:
            language = PROJECT_LANGUAGE_MAP.get(project.project_type.value, "")
            if not language:
                continue
            project_id = f"project:{workspace_path.name}:{project.name}"
            source_files = _collect_source_files(project.path, language)
            for file_path in source_files:
                all_files.append((file_path, project_id, language))

        try:
            summary = conn_pass.process_all(all_files)
            passes_run.append("connections")
            logger.info(
                f"  {summary['signals']} signals, "
                f"{summary['edges_created']} edges created, "
                f"{summary['nodes_created']} nodes created"
            )
        except Exception as e:
            errors.append(f"Pass 2b error: {e}")
            logger.error(f"  Pass 2b failed: {e}")

    # -- Save Output ---------------------------------------------------
    duration_ms = (time.perf_counter() - start) * 1000

    if output_path:
        store.save(output_path)
        logger.info(f"Graph saved to {output_path}")

    result = PipelineResult(
        workspace_path=workspace_path,
        scan_result=scan_result,
        store=store,
        duration_ms=duration_ms,
        passes_run=passes_run,
        errors=errors,
    )

    return result


def print_summary(result: PipelineResult) -> None:
    """Print a human-readable summary of pipeline results."""
    stats = result.store.stats()
    print(f"\n{'='*60}")
    print(f"  Workspace Intelligence Pipeline")
    print(f"{'='*60}")
    print(f"  Workspace:  {result.workspace_path}")
    print(f"  Projects:   {len(result.scan_result.projects)}")
    print(f"  Passes run: {', '.join(result.passes_run)}")
    print(f"  Duration:   {result.duration_ms:.0f}ms")
    print(f"{'-'*60}")
    print(f"  Total nodes:  {stats['total_nodes']}")
    print(f"  Total edges:  {stats['total_edges']}")
    print(f"  Stale nodes:  {stats['stale_nodes']}")

    # Node breakdown by tier
    if stats.get("nodes_by_tier"):
        print(f"{'-'*60}")
        print(f"  Nodes by tier:")
        for tier, count in stats["nodes_by_tier"].items():
            if count > 0:
                print(f"    {tier:>8}: {count}")

    # Top node types
    print(f"{'-'*60}")
    print(f"  Top node types:")
    sorted_types = sorted(
        stats["nodes_by_type"].items(), key=lambda x: x[1], reverse=True
    )
    for ntype, count in sorted_types[:10]:
        if count > 0:
            print(f"    {ntype:>20}: {count}")

    # Top edge types
    sorted_edges = sorted(
        stats["edges_by_type"].items(), key=lambda x: x[1], reverse=True
    )
    active_edges = [(e, c) for e, c in sorted_edges if c > 0]
    if active_edges:
        print(f"{'-'*60}")
        print(f"  Top edge types:")
        for etype, count in active_edges[:10]:
            print(f"    {etype:>20}: {count}")

    if result.errors:
        print(f"{'-'*60}")
        print(f"  Errors ({len(result.errors)}):")
        for err in result.errors[:5]:
            print(f"    - {err}")
        if len(result.errors) > 5:
            print(f"    ... and {len(result.errors) - 5} more")

    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI entry point for the pipeline."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Workspace Intelligence Pipeline — build a code knowledge graph"
    )
    parser.add_argument("workspace", help="Path to workspace directory")
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file path (default: workspace_graph.json)",
        default="workspace_graph.json",
    )
    parser.add_argument(
        "--depth", type=int, default=5,
        help="Max directory scan depth (default: 5)",
    )
    parser.add_argument(
        "--passes",
        nargs="+",
        choices=["scan", "treesitter", "patterns", "connections"],
        default=["scan", "treesitter", "patterns", "connections"],
        help="Which passes to run (default: all)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    result = run_pipeline(
        workspace_path=args.workspace,
        output_path=args.output,
        max_depth=args.depth,
        passes=args.passes,
    )

    print_summary(result)


if __name__ == "__main__":
    main()
