"""
Workspace Intelligence Layer - Pipeline Orchestrator

Runs the multi-pass pipeline to build a workspace intelligence graph.

Pipeline passes:
  Pass 0: Workspace scanner + bridge (FREE) — discovers projects, creates top-level nodes
  Pass 1: Tree-sitter AST extraction (FREE) — creates File/Function/Class nodes
  Pass 2: Regex pattern matching (FREE) — detects endpoints, models, events, etc.
  Pass 2b: Behavioral connections (FREE) — CALLS, READS_DB, EMITS_EVENT, etc.
  Pass 3: LLM semantic enrichment (PAID) — operational edges, node descriptions
  Pass 4: Validation & confidence scoring (FREE) — quality gate
"""

import sys
import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from scanner import WorkspaceScanner, ScanResult
from bridge import scan_result_to_graph
from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Provenance
from pipeline.pass1_treesitter import TreeSitterPass
from pipeline.pass2_patterns import PatternPass
from pipeline.pass2b_connections import ConnectionPass
from pipeline.pass3_llm import LLMPass
from pipeline.pass4_validation import validate_graph

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
        passes: Which passes to run (default: all free passes).
                Options: ["scan", "treesitter", "patterns", "connections", "llm", "validation"]
                Note: "llm" requires ANTHROPIC_API_KEY and costs ~$2 per 500 files.

    Returns:
        PipelineResult with the populated GraphStore.
    """
    workspace_path = Path(workspace_path).resolve()
    if passes is None:
        passes = ["scan", "treesitter", "patterns", "connections", "validation"]

    start = time.perf_counter()
    store = GraphStore()
    errors: List[str] = []
    passes_run: List[str] = []
    scan_result = None
    _dir_maps = {}  # project_id → directory info for MODULE containment

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

    # -- Pass 0b: Create MODULE nodes for directory structure -----------
    if "scan" in passes:
        logger.info("Pass 0b: Creating directory structure (MODULE nodes)...")
        total_modules = 0

        # If no projects detected, create MODULE nodes for entire workspace
        if not scan_result.projects:
            logger.info("  No projects detected, creating MODULE nodes for entire workspace...")
            workspace_id = f"workspace:{workspace_path.name}"

            # Walk all directories
            dirs_to_process = []
            for root, dirs, files in os.walk(workspace_path):
                # Skip excluded directories
                skip_dirs = {
                    "node_modules", ".git", "__pycache__", ".venv", "venv",
                    "dist", "build", "target", ".next", ".nuxt", ".turbo",
                    ".nx", "coverage", ".pytest_cache", ".mypy_cache",
                    ".idea", ".vscode", "obj", "bin",
                }
                dirs[:] = [d for d in dirs if d not in skip_dirs]

                root_path = Path(root)
                if root_path != workspace_path:
                    dirs_to_process.append(root_path)

            # Sort by depth (parents first)
            dirs_to_process.sort(key=lambda d: len(d.parts))

            for dir_path in dirs_to_process:
                relative = dir_path.relative_to(workspace_path)
                module_name = str(relative).replace("\\", "/")
                module_id = f"module:{workspace_id}:{module_name}"

                # Determine parent: workspace or parent directory
                if dir_path.parent == workspace_path:
                    parent_id = workspace_id
                else:
                    parent_relative = dir_path.parent.relative_to(workspace_path)
                    parent_name = str(parent_relative).replace("\\", "/")
                    parent_id = f"module:{workspace_id}:{parent_name}"

                module_node = GraphNode(
                    id=module_id,
                    type=NodeType.MODULE,
                    name=dir_path.name + "/",
                    description=f"Directory: {module_name}",
                    parent_id=parent_id,
                    provenance=Provenance.SCANNER,
                    confidence=1.0,
                    metadata={"path": str(dir_path), "relative_path": module_name},
                )
                store.add_node(module_node)

                # CONTAINS edge: parent → module
                contains_edge = GraphEdge(
                    source_id=parent_id,
                    target_id=module_id,
                    type=EdgeType.CONTAINS,
                    provenance=Provenance.SCANNER,
                    confidence=1.0,
                )
                store.add_edge(contains_edge, validate=False)
                total_modules += 1

            # Create File nodes for all files in workspace
            total_files = 0
            for root, dirs, files in os.walk(workspace_path):
                skip_dirs = {
                    "node_modules", ".git", "__pycache__", ".venv", "venv",
                    "dist", "build", "target", ".next", ".nuxt", ".turbo",
                    ".nx", "coverage", ".pytest_cache", ".mypy_cache",
                    ".idea", ".vscode", "obj", "bin",
                }
                dirs[:] = [d for d in dirs if d not in skip_dirs]

                root_path = Path(root)
                for file_name in files:
                    file_path = root_path / file_name
                    relative_file = file_path.relative_to(workspace_path)
                    file_id = f"file:{workspace_id}:{str(relative_file).replace(chr(92), '/')}"

                    # Determine parent module
                    if file_path.parent == workspace_path:
                        parent_id = workspace_id
                    else:
                        parent_relative = file_path.parent.relative_to(workspace_path)
                        parent_name = str(parent_relative).replace("\\", "/")
                        parent_id = f"module:{workspace_id}:{parent_name}"

                    file_node = GraphNode(
                        id=file_id,
                        type=NodeType.FILE,
                        name=file_name,
                        description=f"File: {str(relative_file)}",
                        parent_id=parent_id,
                        provenance=Provenance.SCANNER,
                        confidence=1.0,
                        metadata={"path": str(file_path), "relative_path": str(relative_file)},
                    )
                    store.add_node(file_node)

                    # CONTAINS edge: parent → file
                    contains_edge = GraphEdge(
                        source_id=parent_id,
                        target_id=file_id,
                        type=EdgeType.CONTAINS,
                        provenance=Provenance.SCANNER,
                        confidence=1.0,
                    )
                    store.add_edge(contains_edge, validate=False)
                    total_files += 1

            logger.info(f"  Created {total_modules} MODULE nodes and {total_files} FILE nodes for workspace")

        # If projects detected, create MODULE + FILE nodes for ALL dirs/files
        skip_dirs = {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            "dist", "build", "target", ".next", ".nuxt", ".turbo",
            ".nx", "coverage", ".pytest_cache", ".mypy_cache",
            ".idea", ".vscode", "obj", "bin",
        }
        total_file_nodes = 0

        for project in scan_result.projects:
            language = PROJECT_LANGUAGE_MAP.get(project.project_type.value, "")
            project_id = f"project:{workspace_path.name}:{project.name}"

            # Walk ALL directories (not just source-code dirs)
            dirs_seen = set()
            for root, dirs, files in os.walk(project.path):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                root_path = Path(root)
                if root_path != project.path:
                    dirs_seen.add(root_path)

            # Create MODULE nodes for each directory, sorted by depth (parents first)
            sorted_dirs = sorted(dirs_seen, key=lambda d: len(d.parts))
            for dir_path in sorted_dirs:
                relative = dir_path.relative_to(project.path)
                module_name = str(relative).replace("\\", "/")
                module_id = f"module:{project_id}:{module_name}"

                # Determine parent: project or parent directory
                if dir_path.parent == project.path:
                    parent_id = project_id
                else:
                    parent_relative = dir_path.parent.relative_to(project.path)
                    parent_name = str(parent_relative).replace("\\", "/")
                    parent_id = f"module:{project_id}:{parent_name}"

                module_node = GraphNode(
                    id=module_id,
                    type=NodeType.MODULE,
                    name=dir_path.name + "/",
                    description=f"Directory: {module_name}",
                    parent_id=parent_id,
                    provenance=Provenance.SCANNER,
                    confidence=1.0,
                    metadata={"path": str(dir_path), "relative_path": module_name},
                )
                store.add_node(module_node)

                # CONTAINS edge: parent → module
                contains_edge = GraphEdge(
                    source_id=parent_id,
                    target_id=module_id,
                    type=EdgeType.CONTAINS,
                    provenance=Provenance.SCANNER,
                    confidence=1.0,
                )
                store.add_edge(contains_edge, validate=False)
                total_modules += 1

            # Create FILE nodes for ALL files in project (not just source files)
            # Use absolute posix paths as IDs to match tree-sitter's ID format
            for root, dirs, files in os.walk(project.path):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                root_path = Path(root)
                for file_name in files:
                    file_path = root_path / file_name
                    relative_file = file_path.relative_to(project.path)
                    # Use absolute posix path as ID (matches tree-sitter format)
                    file_id = f"file:{project_id}:{file_path.resolve().as_posix()}"

                    # Determine parent module
                    if file_path.parent == project.path:
                        parent_id = project_id
                    else:
                        parent_relative = file_path.parent.relative_to(project.path)
                        parent_name = str(parent_relative).replace("\\", "/")
                        parent_id = f"module:{project_id}:{parent_name}"

                    file_node = GraphNode(
                        id=file_id,
                        type=NodeType.FILE,
                        name=file_name,
                        description=f"File: {str(relative_file)}",
                        parent_id=parent_id,
                        provenance=Provenance.SCANNER,
                        confidence=1.0,
                        metadata={
                            "path": str(file_path),
                            "relative_path": str(relative_file).replace("\\", "/"),
                        },
                    )
                    store.add_node(file_node)

                    # CONTAINS edge: parent → file
                    contains_edge = GraphEdge(
                        source_id=parent_id,
                        target_id=file_id,
                        type=EdgeType.CONTAINS,
                        provenance=Provenance.SCANNER,
                        confidence=1.0,
                    )
                    store.add_edge(contains_edge, validate=False)
                    total_file_nodes += 1

            # Store the directory map for Pass 1
            _dir_maps[project_id] = {
                "project_path": project.path,
                "dirs": sorted_dirs,
            }

        logger.info(f"  Created {total_modules} MODULE nodes, {total_file_nodes} FILE nodes")

    # -- Pass 1: Tree-sitter AST Extraction ----------------------------
    if "treesitter" in passes and scan_result.projects:
        logger.info("Pass 1: Tree-sitter AST extraction...")
        ts_pass = TreeSitterPass(store)
        total_files = 0
        total_nodes = 0
        total_imports = 0

        for project in scan_result.projects:
            language = PROJECT_LANGUAGE_MAP.get(project.project_type.value, "")
            if not language:
                continue

            project_id = f"project:{workspace_path.name}:{project.name}"
            source_files = _collect_source_files(project.path, language)

            # Build a file map for import resolution
            file_map = {}
            for fp in source_files:
                file_map[fp.stem] = fp  # products → products.js path
                file_map[str(fp.relative_to(project.path)).replace("\\", "/")] = fp

            for file_path in source_files:
                try:
                    nodes = ts_pass.process_file(file_path, project_id, language)
                    total_files += 1
                    total_nodes += len(nodes)
                    # CONTAINS edges are already created by Pass 0b
                except Exception as e:
                    errors.append(f"Pass 1 error on {file_path}: {e}")

        # Post-process: resolve IMPORTS edges to actual file nodes
        import_edges_to_fix = []
        for edge in store.get_edges_by_type(EdgeType.IMPORTS):
            target_node = store.get_node(edge.target_id)
            if target_node is None:
                import_edges_to_fix.append(edge)

        # Build lookup: file nodes by their actual file path
        file_nodes_by_path = {}
        for node in store.get_nodes_by_type(NodeType.FILE):
            if node.location:
                file_nodes_by_path[node.location.file_path] = node

        for edge in import_edges_to_fix:
            source_node = store.get_node(edge.source_id)
            if not source_node or not source_node.location:
                continue

            import_path = edge.target_id.split(":")[-1]  # raw import path
            source_file = Path(source_node.location.file_path)

            # Resolve relative imports
            resolved = None
            if import_path.startswith("."):
                candidate = (source_file.parent / import_path).resolve()
                for ext in [".js", ".ts", ".jsx", ".tsx", "/index.js", "/index.ts", ""]:
                    test_path = Path(str(candidate) + ext)
                    if test_path.exists():
                        resolved = test_path
                        break
            # else: package import (express, mongoose, etc.) — skip

            if resolved:
                resolved_posix = resolved.as_posix()
                target = file_nodes_by_path.get(resolved_posix)
                if target:
                    # Remove old broken edge, add resolved one
                    try:
                        store.graph.remove_edge(edge.source_id, edge.target_id)
                    except Exception:
                        pass
                    new_edge = GraphEdge(
                        source_id=edge.source_id,
                        target_id=target.id,
                        type=EdgeType.IMPORTS,
                        description=f"Imports {target.name}",
                        provenance=Provenance.SCANNER,
                        confidence=0.9,
                    )
                    store.add_edge(new_edge, validate=False)
                    total_imports += 1
            else:
                # Remove dangling import edge (package imports, etc.)
                try:
                    store.graph.remove_edge(edge.source_id, edge.target_id)
                except Exception:
                    pass

        passes_run.append("treesitter")
        logger.info(f"  Processed {total_files} files, created {total_nodes} nodes, resolved {total_imports} imports")

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

    # -- Pass 3: LLM Semantic Analysis (PAID) --------------------------
    if "llm" in passes and scan_result.projects:
        logger.info("Pass 3: LLM semantic analysis (PAID)...")
        llm_pass = LLMPass(store)
        total_nodes = 0
        total_edges = 0
        total_cost = 0.0

        for project in scan_result.projects:
            language = PROJECT_LANGUAGE_MAP.get(project.project_type.value, "")
            if not language:
                continue

            project_id = f"project:{workspace_path.name}:{project.name}"

            try:
                summary = asyncio.run(
                    llm_pass.process_project(project.path, project_id, language)
                )
                total_nodes += summary.get("nodes_created", 0)
                total_edges += summary.get("edges_created", 0)
                total_cost += summary.get("total_cost_usd", 0.0)

                for err in summary.get("errors", []):
                    errors.append(f"Pass 3: {err}")
            except Exception as e:
                errors.append(f"Pass 3 error on {project.name}: {e}")
                logger.error(f"  Pass 3 failed for {project.name}: {e}")

        passes_run.append("llm")
        logger.info(
            f"  Created {total_nodes} nodes, {total_edges} edges, "
            f"cost: ${total_cost:.4f}"
        )

    # -- Pass 4: Validation & Confidence Scoring (FREE) ----------------
    if "validation" in passes:
        logger.info("Pass 4: Validation & confidence scoring...")
        try:
            val_result = validate_graph(store, fix=True)
            passes_run.append("validation")

            error_count = sum(
                1 for i in val_result.issues if i.severity == "error"
            )
            warn_count = sum(
                1 for i in val_result.issues if i.severity == "warning"
            )
            logger.info(
                f"  {val_result.total_checks} checks, "
                f"{error_count} errors, {warn_count} warnings, "
                f"{val_result.confidence_adjustments} confidence adjustments"
            )

            if error_count > 0:
                for issue in val_result.issues:
                    if issue.severity == "error":
                        errors.append(f"Pass 4: {issue.message}")
        except Exception as e:
            errors.append(f"Pass 4 error: {e}")
            logger.error(f"  Pass 4 failed: {e}")

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
        choices=["scan", "treesitter", "patterns", "connections", "llm", "validation"],
        default=["scan", "treesitter", "patterns", "connections", "validation"],
        help="Which passes to run (default: all free passes; add 'llm' for paid LLM analysis)",
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
