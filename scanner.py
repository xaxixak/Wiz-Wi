"""
Workspace Intelligence Layer - Workspace Scanner

Discovers project roots within a workspace by detecting marker files.
Supports monorepo detection and CI/CD infrastructure discovery.
"""

import os
import fnmatch
from pathlib import Path
from typing import List, Dict, Set, Optional
from dataclasses import dataclass, field
from enum import Enum


class ProjectType(str, Enum):
    """Detected project type based on marker files."""
    NODEJS = "nodejs"
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    DOTNET = "dotnet"
    DOCKER = "docker"
    UNKNOWN = "unknown"


# Marker files that indicate a project root.
# Exact filenames matched directly; glob patterns matched with fnmatch.
PROJECT_MARKERS: Dict[str, ProjectType] = {
    # Node.js / JavaScript
    "package.json": ProjectType.NODEJS,
    # Python
    "pyproject.toml": ProjectType.PYTHON,
    "setup.py": ProjectType.PYTHON,
    "requirements.txt": ProjectType.PYTHON,
    # Go
    "go.mod": ProjectType.GO,
    # Rust
    "Cargo.toml": ProjectType.RUST,
    # Java / JVM
    "pom.xml": ProjectType.JAVA,
    "build.gradle": ProjectType.JAVA,
    "build.gradle.kts": ProjectType.JAVA,
    # .NET (glob patterns)
    "*.csproj": ProjectType.DOTNET,
    "*.sln": ProjectType.DOTNET,
    "*.fsproj": ProjectType.DOTNET,
}

# Separate exact markers from glob patterns for efficient matching
_EXACT_MARKERS = {k: v for k, v in PROJECT_MARKERS.items() if "*" not in k}
_GLOB_MARKERS = {k: v for k, v in PROJECT_MARKERS.items() if "*" in k}

# Monorepo root markers — these indicate a workspace root that CONTAINS sub-projects
MONOREPO_MARKERS: Set[str] = {
    "nx.json",               # Nx monorepo
    "turbo.json",            # Turborepo
    "lerna.json",            # Lerna
    "pnpm-workspace.yaml",   # pnpm workspaces
    "rush.json",             # Rush
}

# Infrastructure markers (not full projects, but important)
INFRA_MARKERS: Set[str] = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "kubernetes",
    "k8s",
    "terraform",
    ".env.example",
}

# CI/CD markers
CICD_MARKERS: Dict[str, str] = {
    ".github":            "github-actions",
    ".gitlab-ci.yml":     "gitlab-ci",
    "Jenkinsfile":        "jenkins",
    ".circleci":          "circleci",
    "azure-pipelines.yml": "azure-devops",
    "bitbucket-pipelines.yml": "bitbucket",
    ".travis.yml":        "travis-ci",
}

# Directories to skip during scanning
SKIP_DIRS: Set[str] = {
    "node_modules",
    ".git",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".turbo",
    ".nx",
}


@dataclass
class DiscoveredProject:
    """A discovered project within the workspace."""
    path: Path
    name: str
    project_type: ProjectType
    marker_file: str
    has_git: bool = False
    is_monorepo: bool = False
    infra_files: List[str] = field(default_factory=list)
    cicd_systems: List[str] = field(default_factory=list)


@dataclass
class ScanResult:
    """Result of scanning a workspace."""
    workspace_root: Path
    projects: List[DiscoveredProject]
    infra_paths: List[Path]
    total_files_scanned: int = 0


def _match_marker(filename: str) -> Optional[ProjectType]:
    """Match a filename against project markers (exact + glob)."""
    if filename in _EXACT_MARKERS:
        return _EXACT_MARKERS[filename]
    for pattern, ptype in _GLOB_MARKERS.items():
        if fnmatch.fnmatch(filename, pattern):
            return ptype
    return None


class WorkspaceScanner:
    """
    Scans a workspace directory to discover project roots and infrastructure.

    Uses heuristic-based detection via marker files (package.json, pyproject.toml, etc.)
    For monorepo roots, continues recursing into sub-projects.
    """

    def __init__(self, workspace_root: str | Path, max_depth: int = 5):
        self.workspace_root = Path(workspace_root).resolve()
        self.max_depth = max_depth
        self._files_scanned = 0

    def scan(self) -> ScanResult:
        """
        Scan the workspace and return discovered projects.

        Returns:
            ScanResult with all discovered projects and infra paths
        """
        projects: List[DiscoveredProject] = []
        infra_paths: List[Path] = []
        self._files_scanned = 0

        self._scan_directory(
            self.workspace_root,
            depth=0,
            projects=projects,
            infra_paths=infra_paths
        )

        return ScanResult(
            workspace_root=self.workspace_root,
            projects=projects,
            infra_paths=infra_paths,
            total_files_scanned=self._files_scanned,
        )

    def _scan_directory(
        self,
        directory: Path,
        depth: int,
        projects: List[DiscoveredProject],
        infra_paths: List[Path],
    ) -> Optional[DiscoveredProject]:
        """Recursively scan a directory for project markers."""

        if depth > self.max_depth:
            return None

        if directory.name in SKIP_DIRS:
            return None

        try:
            entries = list(directory.iterdir())
        except PermissionError:
            return None

        self._files_scanned += len(entries)

        entry_names = {e.name for e in entries}

        # Check for project markers
        found_marker: Optional[str] = None
        found_type: ProjectType = ProjectType.UNKNOWN
        found_infra: List[str] = []
        found_cicd: List[str] = []
        has_git = False
        is_monorepo = False

        for entry in entries:
            name = entry.name

            # Check for .git
            if name == ".git" and entry.is_dir():
                has_git = True

            # Check for project markers
            if entry.is_file():
                matched_type = _match_marker(name)
                if matched_type is not None and found_marker is None:
                    found_marker = name
                    found_type = matched_type

                # Check for infra files
                if name in INFRA_MARKERS:
                    found_infra.append(name)
                    infra_paths.append(entry)

                # Check for CI/CD files
                if name in CICD_MARKERS:
                    found_cicd.append(CICD_MARKERS[name])

            # Check for infra directories
            if entry.is_dir() and name in INFRA_MARKERS:
                found_infra.append(name)
                infra_paths.append(entry)

            # Check for CI/CD directories
            if entry.is_dir() and name in CICD_MARKERS:
                found_cicd.append(CICD_MARKERS[name])

            # Check for monorepo markers
            if name in MONOREPO_MARKERS:
                is_monorepo = True

        # If we found a project marker, record this as a project
        if found_marker:
            project = DiscoveredProject(
                path=directory,
                name=directory.name,
                project_type=found_type,
                marker_file=found_marker,
                has_git=has_git,
                is_monorepo=is_monorepo,
                infra_files=found_infra,
                cicd_systems=found_cicd,
            )
            projects.append(project)

            # Monorepo roots contain sub-projects — keep recursing
            if is_monorepo:
                for entry in entries:
                    if entry.is_dir() and entry.name not in SKIP_DIRS:
                        self._scan_directory(entry, depth + 1, projects, infra_paths)

            return project

        # Otherwise, recurse into subdirectories
        for entry in entries:
            if entry.is_dir() and entry.name not in SKIP_DIRS:
                self._scan_directory(entry, depth + 1, projects, infra_paths)

        return None


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scanner.py <workspace_path>")
        sys.exit(1)

    workspace_path = sys.argv[1]
    scanner = WorkspaceScanner(workspace_path)
    result = scanner.scan()

    print(f"\n{'='*60}")
    print(f"Workspace: {result.workspace_root}")
    print(f"Files scanned: {result.total_files_scanned}")
    print(f"Projects found: {len(result.projects)}")
    print(f"{'='*60}\n")

    for project in result.projects:
        label = project.project_type.value
        if project.is_monorepo:
            label += " (monorepo)"
        print(f"  [{label}] {project.name}")
        print(f"      Path: {project.path}")
        print(f"      Marker: {project.marker_file}")
        if project.has_git:
            print(f"      Git: yes")
        if project.infra_files:
            print(f"      Infra: {', '.join(project.infra_files)}")
        if project.cicd_systems:
            print(f"      CI/CD: {', '.join(project.cicd_systems)}")
        print()
