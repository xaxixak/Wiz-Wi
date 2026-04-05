"""
Test suite for bridge.py

Tests the conversion of ScanResult to graph nodes and edges.
"""

from pathlib import Path
from scanner import ScanResult, DiscoveredProject, ProjectType
from bridge import scan_result_to_graph
from graph_store import GraphStore
from ontology import NodeType, EdgeType, Provenance


def test_basic_workspace():
    """Test basic workspace with one project."""
    workspace_root = Path("/test/workspace")
    projects = [
        DiscoveredProject(
            path=Path("/test/workspace/api"),
            name="api",
            project_type=ProjectType.NODEJS,
            marker_file="package.json",
            has_git=True,
            is_monorepo=False,
            infra_files=["Dockerfile"],
            cicd_systems=["github-actions"],
        )
    ]
    result = ScanResult(
        workspace_root=workspace_root,
        projects=projects,
        infra_paths=[],
        total_files_scanned=100,
    )

    store = GraphStore()
    scan_result_to_graph(result, store)

    # Verify workspace node
    workspace_nodes = store.get_nodes_by_type(NodeType.WORKSPACE)
    assert len(workspace_nodes) == 1
    workspace = workspace_nodes[0]
    assert workspace.name == "workspace"
    assert workspace.provenance == Provenance.SCANNER
    assert workspace.confidence == 1.0

    # Verify project node
    project_nodes = store.get_nodes_by_type(NodeType.PROJECT)
    assert len(project_nodes) == 1
    project = project_nodes[0]
    assert project.name == "api"
    assert project.language == "typescript"
    assert project.parent_id == workspace.id
    assert project.metadata["project_type"] == "nodejs"
    assert project.metadata["has_git"] is True
    assert project.metadata["is_monorepo"] is False
    assert "Dockerfile" in project.metadata["infra_files"]
    assert "github-actions" in project.metadata["cicd_systems"]

    # Verify edges
    edges = store.get_edges_by_type(EdgeType.CONTAINS)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_id == workspace.id
    assert edge.target_id == project.id
    assert edge.provenance == Provenance.SCANNER
    assert edge.confidence == 1.0

    print("[PASS] test_basic_workspace")


def test_monorepo_workspace():
    """Test workspace with monorepo projects."""
    workspace_root = Path("/test/monorepo")
    projects = [
        DiscoveredProject(
            path=Path("/test/monorepo"),
            name="monorepo",
            project_type=ProjectType.NODEJS,
            marker_file="package.json",
            has_git=True,
            is_monorepo=True,
            infra_files=["nx.json"],
            cicd_systems=["github-actions"],
        ),
        DiscoveredProject(
            path=Path("/test/monorepo/apps/web"),
            name="web",
            project_type=ProjectType.NODEJS,
            marker_file="package.json",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        ),
        DiscoveredProject(
            path=Path("/test/monorepo/apps/api"),
            name="api",
            project_type=ProjectType.NODEJS,
            marker_file="package.json",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        ),
    ]
    result = ScanResult(
        workspace_root=workspace_root,
        projects=projects,
        infra_paths=[],
        total_files_scanned=500,
    )

    store = GraphStore()
    scan_result_to_graph(result, store)

    # Verify project nodes
    project_nodes = store.get_nodes_by_type(NodeType.PROJECT)
    assert len(project_nodes) == 3

    # Find monorepo root
    monorepo = [p for p in project_nodes if p.name == "monorepo"][0]
    assert "monorepo" in monorepo.tags
    assert monorepo.metadata["is_monorepo"] is True

    # Find sub-projects
    web = [p for p in project_nodes if p.name == "web"][0]
    api = [p for p in project_nodes if p.name == "api"][0]
    assert "monorepo" not in web.tags
    assert "monorepo" not in api.tags

    print("[PASS] test_monorepo_workspace passed")


def test_workspace_with_infra():
    """Test workspace with infrastructure files."""
    workspace_root = Path("/test/infra-workspace")
    projects = [
        DiscoveredProject(
            path=Path("/test/infra-workspace/backend"),
            name="backend",
            project_type=ProjectType.PYTHON,
            marker_file="requirements.txt",
            has_git=True,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        )
    ]
    infra_paths = [
        Path("/test/infra-workspace/docker-compose.yml"),
        Path("/test/infra-workspace/Dockerfile"),
        Path("/test/infra-workspace/kubernetes"),
    ]
    result = ScanResult(
        workspace_root=workspace_root,
        projects=projects,
        infra_paths=infra_paths,
        total_files_scanned=75,
    )

    store = GraphStore()
    scan_result_to_graph(result, store)

    # Verify infra nodes
    infra_nodes = store.get_nodes_by_type(NodeType.INFRA_CONFIG)
    assert len(infra_nodes) == 3

    # Verify each infra node has proper metadata
    for node in infra_nodes:
        assert node.provenance == Provenance.SCANNER
        assert node.confidence == 1.0
        assert node.parent_id is not None
        assert "path" in node.metadata
        assert "is_file" in node.metadata or "is_directory" in node.metadata

    # Verify CONTAINS edges for infra
    workspace_nodes = store.get_nodes_by_type(NodeType.WORKSPACE)
    workspace = workspace_nodes[0]
    edges_from_workspace = store.get_edges_from(workspace.id)
    # Should have 1 edge to project + 3 edges to infra = 4 total
    assert len(edges_from_workspace) == 4

    print("[PASS] test_workspace_with_infra passed")


def test_language_mapping():
    """Test that ProjectType correctly maps to language field."""
    workspace_root = Path("/test/multi-lang")
    projects = [
        DiscoveredProject(
            path=Path("/test/multi-lang/nodejs-app"),
            name="nodejs-app",
            project_type=ProjectType.NODEJS,
            marker_file="package.json",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        ),
        DiscoveredProject(
            path=Path("/test/multi-lang/python-app"),
            name="python-app",
            project_type=ProjectType.PYTHON,
            marker_file="requirements.txt",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        ),
        DiscoveredProject(
            path=Path("/test/multi-lang/go-app"),
            name="go-app",
            project_type=ProjectType.GO,
            marker_file="go.mod",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        ),
        DiscoveredProject(
            path=Path("/test/multi-lang/rust-app"),
            name="rust-app",
            project_type=ProjectType.RUST,
            marker_file="Cargo.toml",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        ),
        DiscoveredProject(
            path=Path("/test/multi-lang/java-app"),
            name="java-app",
            project_type=ProjectType.JAVA,
            marker_file="pom.xml",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        ),
        DiscoveredProject(
            path=Path("/test/multi-lang/dotnet-app"),
            name="dotnet-app",
            project_type=ProjectType.DOTNET,
            marker_file="app.csproj",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        ),
    ]
    result = ScanResult(
        workspace_root=workspace_root,
        projects=projects,
        infra_paths=[],
        total_files_scanned=200,
    )

    store = GraphStore()
    scan_result_to_graph(result, store)

    project_nodes = store.get_nodes_by_type(NodeType.PROJECT)
    assert len(project_nodes) == 6

    # Verify language mappings
    language_map = {node.name: node.language for node in project_nodes}
    assert language_map["nodejs-app"] == "typescript"
    assert language_map["python-app"] == "python"
    assert language_map["go-app"] == "go"
    assert language_map["rust-app"] == "rust"
    assert language_map["java-app"] == "java"
    assert language_map["dotnet-app"] == "csharp"

    print("[PASS] test_language_mapping passed")


def test_node_id_format():
    """Test that node IDs follow the correct format."""
    workspace_root = Path("/test/id-format")
    projects = [
        DiscoveredProject(
            path=Path("/test/id-format/my-api"),
            name="my-api",
            project_type=ProjectType.NODEJS,
            marker_file="package.json",
            has_git=False,
            is_monorepo=False,
            infra_files=[],
            cicd_systems=[],
        )
    ]
    result = ScanResult(
        workspace_root=workspace_root,
        projects=projects,
        infra_paths=[],
        total_files_scanned=50,
    )

    store = GraphStore()
    scan_result_to_graph(result, store)

    # Check workspace node ID format
    workspace_nodes = store.get_nodes_by_type(NodeType.WORKSPACE)
    workspace = workspace_nodes[0]
    assert workspace.id == "workspace:id-format:id-format"

    # Check project node ID format
    project_nodes = store.get_nodes_by_type(NodeType.PROJECT)
    project = project_nodes[0]
    assert project.id == "project:id-format:my-api"

    print("[PASS] test_node_id_format passed")


if __name__ == "__main__":
    print("Running bridge.py tests...\n")
    test_basic_workspace()
    test_monorepo_workspace()
    test_workspace_with_infra()
    test_language_mapping()
    test_node_id_format()
    print("\n[PASS] All tests passed!")
