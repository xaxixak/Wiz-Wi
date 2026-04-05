"""
Workspace Intelligence Layer - Scanner to Graph Bridge

Converts ScanResult from scanner.py into graph nodes and edges.
This is Pass 0 of the pipeline: deterministic, structure-only graph construction.

Overview
--------
The bridge module converts workspace scanning results into a semantic knowledge graph:
- Creates WORKSPACE nodes for workspace roots
- Creates PROJECT nodes for each discovered project
- Creates INFRA_CONFIG nodes for infrastructure files/directories
- Links everything with CONTAINS edges
- Sets provenance=SCANNER, confidence=1.0 (deterministic detection)

Node Types Created
------------------
- WORKSPACE: Root container for the development environment
- PROJECT: A repository/deployable package (with language, tags, metadata)
- INFRA_CONFIG: Infrastructure configuration (Docker, k8s, terraform, etc.)

Edge Types Created
------------------
- CONTAINS: Workspace -> Project, Workspace -> InfraConfig

Node ID Format
--------------
All node IDs follow the pattern: {type_lower}:{workspace_name}:{entity_name}
Examples:
  - workspace:my-workspace:my-workspace
  - project:my-workspace:api-service
  - infra_config:my-workspace:docker-compose_yml

Project Metadata
----------------
Each PROJECT node stores:
  - project_type: The detected project type (nodejs, python, go, rust, java, dotnet)
  - marker_file: The file that identified this project (package.json, requirements.txt, etc.)
  - has_git: Boolean indicating if .git directory exists
  - is_monorepo: Boolean indicating if monorepo markers found (nx.json, turbo.json, etc.)
  - infra_files: List of infrastructure files in the project (Dockerfile, etc.)
  - cicd_systems: List of CI/CD systems detected (github-actions, gitlab-ci, etc.)
  - path: Absolute path to the project directory

Language Mapping
----------------
The bridge automatically maps ProjectType to language field:
  - NODEJS -> typescript
  - PYTHON -> python
  - GO -> go
  - RUST -> rust
  - JAVA -> java
  - DOTNET -> csharp

Monorepo Support
----------------
If is_monorepo=True, the PROJECT node gets a "monorepo" tag.

Usage Example
-------------
```python
from scanner import WorkspaceScanner
from bridge import scan_result_to_graph
from graph_store import GraphStore

# Step 1: Scan workspace
scanner = WorkspaceScanner("/path/to/workspace")
result = scanner.scan()

# Step 2: Build graph
store = GraphStore()
scan_result_to_graph(result, store)

# Step 3: Query graph
workspace_nodes = store.get_nodes_by_type(NodeType.WORKSPACE)
project_nodes = store.get_nodes_by_type(NodeType.PROJECT)

# Step 4: Save graph
store.save("workspace_graph.json")
```

Design Philosophy
-----------------
This is Pass 0 - we only create structural nodes/edges that can be detected
deterministically. Future passes (1, 2, 3) will add:
  - Pass 1: Code-level entities (Functions, Endpoints, DataModels)
  - Pass 2: Cross-cutting relationships (CALLS, READS_DB, EMITS_EVENT)
  - Pass 3: LLM-inferred semantics (business logic, invariants, patterns)
"""

from pathlib import Path
from typing import Dict, List

from scanner import ScanResult, DiscoveredProject, ProjectType
from ontology import (
    GraphNode,
    GraphEdge,
    NodeType,
    EdgeType,
    Provenance,
)
from graph_store import GraphStore


# Mapping from ProjectType to language field
PROJECT_TYPE_TO_LANGUAGE: Dict[ProjectType, str] = {
    ProjectType.NODEJS: "typescript",
    ProjectType.PYTHON: "python",
    ProjectType.GO: "go",
    ProjectType.RUST: "rust",
    ProjectType.JAVA: "java",
    ProjectType.DOTNET: "csharp",
    ProjectType.DOCKER: None,
    ProjectType.UNKNOWN: None,
}


def _generate_node_id(node_type: NodeType, workspace_name: str, entity_name: str) -> str:
    """
    Generate a unique node ID in the format: {type_lower}:{workspace_name}:{entity_name}

    Args:
        node_type: The type of the node
        workspace_name: The workspace name
        entity_name: The entity name

    Returns:
        Formatted node ID string
    """
    type_lower = node_type.value.lower()
    return f"{type_lower}:{workspace_name}:{entity_name}"


def _sanitize_name(name: str) -> str:
    """
    Sanitize a name to be used in node IDs.
    Replaces problematic characters with underscores.

    Args:
        name: The name to sanitize

    Returns:
        Sanitized name
    """
    # Replace spaces and special characters with underscores
    sanitized = name.replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_")
    return sanitized


def scan_result_to_graph(result: ScanResult, store: GraphStore) -> GraphStore:
    """
    Convert a ScanResult into graph nodes and edges, adding them to the GraphStore.

    This is Pass 0 of the pipeline: deterministic, structure-only graph construction.
    Creates WORKSPACE, PROJECT, and INFRA_CONFIG nodes with CONTAINS edges.

    Args:
        result: The ScanResult from WorkspaceScanner
        store: The GraphStore to populate

    Returns:
        The populated GraphStore (same instance that was passed in)
    """
    workspace_name = result.workspace_root.name
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []

    # Create WORKSPACE node
    workspace_id = _generate_node_id(NodeType.WORKSPACE, workspace_name, workspace_name)
    workspace_node = GraphNode(
        id=workspace_id,
        type=NodeType.WORKSPACE,
        name=workspace_name,
        description=f"Workspace root at {result.workspace_root}",
        provenance=Provenance.SCANNER,
        confidence=1.0,
        metadata={
            "workspace_root": str(result.workspace_root),
            "total_files_scanned": result.total_files_scanned,
            "total_projects": len(result.projects),
            "total_infra_paths": len(result.infra_paths),
        },
    )
    nodes.append(workspace_node)

    # Create PROJECT nodes
    for project in result.projects:
        project_name = _sanitize_name(project.name)
        project_id = _generate_node_id(NodeType.PROJECT, workspace_name, project_name)

        # Map project_type to language
        language = PROJECT_TYPE_TO_LANGUAGE.get(project.project_type)

        # Build tags
        tags: List[str] = []
        if project.is_monorepo:
            tags.append("monorepo")

        # Build metadata
        metadata = {
            "project_type": project.project_type.value,
            "marker_file": project.marker_file,
            "has_git": project.has_git,
            "is_monorepo": project.is_monorepo,
            "path": str(project.path),
        }

        if project.infra_files:
            metadata["infra_files"] = project.infra_files

        if project.cicd_systems:
            metadata["cicd_systems"] = project.cicd_systems

        project_node = GraphNode(
            id=project_id,
            type=NodeType.PROJECT,
            name=project.name,
            description=f"{project.project_type.value.capitalize()} project at {project.path.name}",
            parent_id=workspace_id,
            provenance=Provenance.SCANNER,
            confidence=1.0,
            language=language,
            tags=tags,
            metadata=metadata,
        )
        nodes.append(project_node)

        # Create CONTAINS edge from WORKSPACE to PROJECT
        contains_edge = GraphEdge(
            source_id=workspace_id,
            target_id=project_id,
            type=EdgeType.CONTAINS,
            description=f"Workspace contains project {project.name}",
            provenance=Provenance.SCANNER,
            confidence=1.0,
        )
        edges.append(contains_edge)

    # Create INFRA_CONFIG nodes for infrastructure paths
    for infra_path in result.infra_paths:
        # Generate a unique name for the infra config
        # Use relative path from workspace root for better readability
        try:
            relative_path = infra_path.relative_to(result.workspace_root)
            infra_name = _sanitize_name(str(relative_path))
        except ValueError:
            # If infra_path is not relative to workspace_root, use absolute path
            infra_name = _sanitize_name(infra_path.name)

        infra_id = _generate_node_id(NodeType.INFRA_CONFIG, workspace_name, infra_name)

        # Determine if it's a file or directory
        is_file = infra_path.is_file()

        infra_node = GraphNode(
            id=infra_id,
            type=NodeType.INFRA_CONFIG,
            name=infra_path.name,
            description=f"Infrastructure {'file' if is_file else 'directory'} at {relative_path if 'relative_path' in locals() else infra_path}",
            parent_id=workspace_id,
            provenance=Provenance.SCANNER,
            confidence=1.0,
            metadata={
                "path": str(infra_path),
                "is_file": is_file,
                "is_directory": not is_file,
            },
        )
        nodes.append(infra_node)

        # Create CONTAINS edge from WORKSPACE to INFRA_CONFIG
        contains_edge = GraphEdge(
            source_id=workspace_id,
            target_id=infra_id,
            type=EdgeType.CONTAINS,
            description=f"Workspace contains infrastructure config {infra_path.name}",
            provenance=Provenance.SCANNER,
            confidence=1.0,
        )
        edges.append(contains_edge)

    # Add all nodes and edges to the store
    store.add_nodes(nodes)
    store.add_edges(edges, validate=True)

    return store


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import sys
    from scanner import WorkspaceScanner

    if len(sys.argv) < 2:
        print("Usage: python bridge.py <workspace_path>")
        sys.exit(1)

    workspace_path = sys.argv[1]

    # Run scanner
    print(f"Scanning workspace: {workspace_path}")
    scanner = WorkspaceScanner(workspace_path)
    result = scanner.scan()

    print(f"Found {len(result.projects)} projects")
    print(f"Found {len(result.infra_paths)} infrastructure paths")

    # Convert to graph
    print("\nBuilding graph...")
    store = GraphStore()
    scan_result_to_graph(result, store)

    # Print statistics
    stats = store.stats()
    print(f"\nGraph Statistics:")
    print(f"{'='*60}")
    print(f"Total nodes: {stats['total_nodes']}")
    print(f"Total edges: {stats['total_edges']}")
    print(f"\nNodes by type:")
    for node_type, count in stats['nodes_by_type'].items():
        if count > 0:
            print(f"  {node_type}: {count}")
    print(f"\nNodes by tier:")
    for tier, count in stats['nodes_by_tier'].items():
        if count > 0:
            print(f"  {tier}: {count}")
    print(f"\nEdges by type:")
    for edge_type, count in stats['edges_by_type'].items():
        if count > 0:
            print(f"  {edge_type}: {count}")

    # Print detailed node information
    print(f"\n{'='*60}")
    print("Detailed Node Information:")
    print(f"{'='*60}")

    workspace_nodes = store.get_nodes_by_type(NodeType.WORKSPACE)
    for node in workspace_nodes:
        print(f"\n[{node.type.value}] {node.name}")
        print(f"  ID: {node.id}")
        print(f"  Description: {node.description}")
        print(f"  Metadata: {node.metadata}")

    project_nodes = store.get_nodes_by_type(NodeType.PROJECT)
    for node in project_nodes:
        print(f"\n[{node.type.value}] {node.name}")
        print(f"  ID: {node.id}")
        print(f"  Language: {node.language}")
        print(f"  Tags: {node.tags}")
        print(f"  Parent: {node.parent_id}")
        print(f"  Metadata: {node.metadata}")

    infra_nodes = store.get_nodes_by_type(NodeType.INFRA_CONFIG)
    if infra_nodes:
        print(f"\nInfrastructure Configs:")
        for node in infra_nodes:
            print(f"\n[{node.type.value}] {node.name}")
            print(f"  ID: {node.id}")
            print(f"  Parent: {node.parent_id}")
            print(f"  Metadata: {node.metadata}")
