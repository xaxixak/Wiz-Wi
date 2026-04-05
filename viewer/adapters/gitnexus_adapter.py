"""GitNexus Code Graph Adapter - reads KuzuDB indexes created by GitNexus.

Discovers .gitnexus/ directories in known locations and loads the knowledge
graph (nodes, edges, communities, processes) into our universal viewer format.
"""
import json
import os
from pathlib import Path
from typing import List, Dict, Any

from .base import BaseAdapter, GraphSource, UniversalGraph

# Where to scan for GitNexus-indexed repos
SCAN_ROOTS = [
    Path(os.environ.get("GITNEXUS_SCAN_ROOT", "")).resolve()
    if os.environ.get("GITNEXUS_SCAN_ROOT") else None,
    Path(__file__).resolve().parent.parent.parent.parent,  # Smart RPL root
]

# Also check the GitNexus registry for indexed repos
REGISTRY_PATH = Path.home() / ".gitnexus" / "registry.json"

# Node type → WI-compatible category
GITNEXUS_TYPE_CATEGORY = {
    "Folder": "structure",
    "Module": "structure",
    "File": "files",
    "Function": "logic",
    "Method": "logic",
    "Class": "data",
    "Interface": "data",
    "Struct": "data",
    "Enum": "data",
    "Typedef": "data",
    "TypeAlias": "data",
    "Record": "data",
    "Trait": "data",
    "Impl": "logic",
    "Constructor": "logic",
    "Property": "data",
    "Const": "data",
    "Static": "data",
    "CodeElement": "logic",
    "Macro": "logic",
    "Template": "logic",
    "Namespace": "structure",
    "Union": "data",
    "Delegate": "logic",
    "Annotation": "logic",
    "Community": "cluster",
    "Process": "flow",
}

# Node type → tier mapping
GITNEXUS_TYPE_TIER = {
    "Folder": "macro",
    "Module": "macro",
    "File": "meso",
    "Community": "macro",
    "Process": "macro",
    "Function": "micro",
    "Method": "micro",
    "Class": "meso",
    "Interface": "meso",
    "Struct": "meso",
    "Enum": "meso",
    "Trait": "meso",
    "Namespace": "macro",
}

GITNEXUS_EDGE_GROUPS = {
    "structural": ["CONTAINS", "DEFINES", "IMPORTS", "MEMBER_OF"],
    "calls": ["CALLS"],
    "inheritance": ["EXTENDS", "IMPLEMENTS", "INHERITS"],
    "flow": ["STEP_IN_PROCESS"],
    "other": ["USES", "DECORATES", "OVERRIDES"],
}

GITNEXUS_TYPE_COLORS = {
    "Folder": "#6c757d",
    "File": "#4fc3f7",
    "Function": "#81c784",
    "Method": "#aed581",
    "Class": "#ffb74d",
    "Interface": "#ce93d8",
    "Community": "#ff8a65",
    "Process": "#f06292",
    "Struct": "#ffcc80",
    "Enum": "#b39ddb",
    "Module": "#90a4ae",
}


def _find_gitnexus_repos() -> List[Dict[str, Any]]:
    """Find all repos with .gitnexus/ indexes."""
    repos = []

    # 1. Check the GitNexus registry (primary source — always up to date)
    if REGISTRY_PATH.exists():
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                registry = json.load(f)
            for entry in registry:
                if isinstance(entry, dict):
                    repo_path = Path(entry.get("path", ""))
                    storage_path = Path(entry.get("storagePath", ""))
                    kuzu_file = storage_path / "kuzu" if storage_path.exists() else repo_path / ".gitnexus" / "kuzu"
                    if kuzu_file.exists():
                        meta = entry.get("stats", {})
                        repos.append({
                            "path": str(repo_path),
                            "name": entry.get("name", repo_path.name),
                            "gitnexus_dir": str(storage_path or (repo_path / ".gitnexus")),
                            "kuzu_path": str(kuzu_file),
                            "meta": {
                                "stats": meta,
                                "lastCommit": entry.get("lastCommit"),
                                "indexedAt": entry.get("indexedAt"),
                            },
                        })
        except Exception as e:
            print(f"[WARN] Failed to read GitNexus registry: {e}")

    # 2. Walk scan roots (1 level deep to find .gitnexus dirs)
    for root in SCAN_ROOTS:
        if root is None or not root.exists():
            continue
        # Check root itself
        _check_dir(root, repos)
        # Check immediate children
        try:
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    _check_dir(child, repos)
                    # Also check one more level (for monorepos)
                    for grandchild in child.iterdir():
                        if grandchild.is_dir() and not grandchild.name.startswith("."):
                            _check_dir(grandchild, repos)
        except PermissionError:
            pass

    # Deduplicate by path
    seen = set()
    unique = []
    for r in repos:
        if r["path"] not in seen:
            seen.add(r["path"])
            unique.append(r)
    return unique


def _check_dir(d: Path, repos: list):
    """Check if a directory has a .gitnexus/kuzu index."""
    gn_dir = d / ".gitnexus"
    if gn_dir.exists() and (gn_dir / "kuzu").exists():
        meta = _read_meta(gn_dir)
        repos.append({
            "path": str(d),
            "name": d.name,
            "gitnexus_dir": str(gn_dir),
            "kuzu_path": str(gn_dir / "kuzu"),
            "meta": meta,
        })


def _read_meta(gn_dir: Path) -> dict:
    """Read .gitnexus/meta.json if it exists."""
    meta_file = gn_dir / "meta.json"
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _load_kuzu_graph(kuzu_path: str) -> Dict[str, Any]:
    """Load all nodes and edges from a KuzuDB database."""
    try:
        import kuzu
    except ImportError:
        raise RuntimeError("kuzu package required: pip install kuzu")

    db = kuzu.Database(kuzu_path)
    conn = kuzu.Connection(db)

    nodes = []
    edges = []

    # Node tables to query (code structure)
    code_tables = [
        ("File", ["id", "name", "filePath", "content"]),
        ("Folder", ["id", "name", "filePath"]),
        ("Function", ["id", "name", "filePath", "startLine", "endLine", "isExported", "content"]),
        ("Method", ["id", "name", "filePath", "startLine", "endLine", "isExported", "content"]),
        ("Class", ["id", "name", "filePath", "startLine", "endLine", "isExported", "content"]),
        ("Interface", ["id", "name", "filePath", "startLine", "endLine", "isExported", "content"]),
        ("Struct", ["id", "name", "filePath", "startLine", "endLine", "content"]),
        ("Enum", ["id", "name", "filePath", "startLine", "endLine", "content"]),
        ("CodeElement", ["id", "name", "filePath", "startLine", "endLine", "isExported", "content"]),
        ("Module", ["id", "name", "filePath", "startLine", "endLine", "content"]),
        ("Const", ["id", "name", "filePath", "startLine", "endLine", "content"]),
        ("Property", ["id", "name", "filePath", "startLine", "endLine", "content"]),
        ("Constructor", ["id", "name", "filePath", "startLine", "endLine", "content"]),
    ]

    for table_name, columns in code_tables:
        try:
            cols = ", ".join(f"n.{c}" for c in columns)
            result = conn.execute(f"MATCH (n:{table_name}) RETURN {cols}")
            while result.has_next():
                row = result.get_next()
                node = {"type": table_name}
                for i, col in enumerate(columns):
                    val = row[i]
                    if val is not None:
                        node[col] = val
                # Normalize to our format
                node["name"] = node.get("name", node.get("id", "unknown"))
                nodes.append(node)
        except Exception:
            pass  # Table might be empty or not exist

    # Community nodes (special columns)
    try:
        result = conn.execute(
            "MATCH (n:Community) RETURN n.id, n.label, n.heuristicLabel, "
            "n.cohesion, n.symbolCount, n.keywords, n.description"
        )
        while result.has_next():
            row = result.get_next()
            label = row[2] or row[1] or row[0]
            nodes.append({
                "id": row[0],
                "type": "Community",
                "name": f"Cluster: {label}",
                "metadata": {
                    "label": row[1],
                    "heuristic_label": row[2],
                    "cohesion": row[3],
                    "symbol_count": row[4],
                    "keywords": row[5] or [],
                    "description": row[6],
                },
                "confidence": row[3] or 0.5,
            })
    except Exception:
        pass

    # Process nodes (special columns)
    try:
        result = conn.execute(
            "MATCH (n:Process) RETURN n.id, n.label, n.heuristicLabel, "
            "n.processType, n.stepCount, n.entryPointId, n.terminalId"
        )
        while result.has_next():
            row = result.get_next()
            label = row[2] or row[1] or row[0]
            nodes.append({
                "id": row[0],
                "type": "Process",
                "name": f"Flow: {label}",
                "metadata": {
                    "label": row[1],
                    "heuristic_label": row[2],
                    "process_type": row[3],
                    "step_count": row[4],
                    "entry_point_id": row[5],
                    "terminal_id": row[6],
                },
            })
    except Exception:
        pass

    # All edges via CodeRelation
    try:
        result = conn.execute(
            "MATCH (a)-[r:CodeRelation]->(b) "
            "RETURN a.id, b.id, r.type, r.confidence, r.reason, r.step"
        )
        while result.has_next():
            row = result.get_next()
            edge = {
                "source_id": row[0],
                "target_id": row[1],
                "type": row[2] or "RELATED",
                "confidence": row[3] if row[3] is not None else 1.0,
            }
            if row[4]:
                edge["metadata"] = {"reason": row[4]}
            if row[5] is not None:
                edge.setdefault("metadata", {})["step"] = row[5]
            edges.append(edge)
    except Exception as e:
        print(f"[WARN] GitNexus edge query failed: {e}")

    return {"nodes": nodes, "edges": edges}


def _enrich_gitnexus_node(node: Dict[str, Any], edge_index: Dict) -> Dict[str, Any]:
    """Enrich a GitNexus node with categories, tags, and tier."""
    ntype = node.get("type", "")
    name = (node.get("name") or "").lower()
    file_path = (node.get("filePath") or "").lower()

    # Category
    node["categories"] = [GITNEXUS_TYPE_CATEGORY.get(ntype, "other")]

    # Tier
    node["tier"] = GITNEXUS_TYPE_TIER.get(ntype, "micro")

    # Parent (Folder → contains File → contains Function)
    if ntype in ("Function", "Method", "Class", "Interface", "Struct", "Enum",
                 "CodeElement", "Const", "Property", "Constructor"):
        # Parent is the file
        fp = node.get("filePath", "")
        if fp:
            node["parent_id"] = f"File:{fp}"

    elif ntype == "File":
        # Parent is the folder
        fp = node.get("filePath", "")
        if fp and "/" in fp:
            parent_path = "/".join(fp.split("/")[:-1])
            node["parent_id"] = f"Folder:{parent_path}"

    # Tags
    tags = [ntype.lower()]
    if "test" in name or "test" in file_path:
        tags.append("testing")
    if "route" in name or "route" in file_path:
        tags.append("routing")
    if "model" in name or "model" in file_path:
        tags.append("data-model")
    if "event" in name or "event" in file_path:
        tags.append("events")
    if "auth" in name or "auth" in file_path:
        tags.append("authentication")
    if "middleware" in name or "middleware" in file_path:
        tags.append("middleware")
    if "service" in name or "service" in file_path:
        tags.append("services")
    if "public" in file_path:
        tags.append("frontend")

    # Edge-based tags
    node_edges = edge_index.get(node.get("id", ""), [])
    for e in node_edges:
        etype = e.get("type", "")
        if etype == "CALLS":
            tags.append("caller")
        elif etype == "STEP_IN_PROCESS":
            tags.append("in-flow")

    if node.get("isExported"):
        tags.append("exported")

    node["concepts"] = list(dict.fromkeys(tags))
    node["tags"] = node["concepts"]

    # Build metadata from file info
    meta = node.get("metadata", {})
    if node.get("filePath"):
        meta["path"] = node["filePath"]
    if node.get("startLine"):
        meta["start_line"] = node["startLine"]
    if node.get("endLine"):
        meta["end_line"] = node["endLine"]
    if meta:
        node["metadata"] = meta

    # Don't send raw content to viewer (too big)
    node.pop("content", None)
    # Clean up internal fields
    node.pop("filePath", None)
    node.pop("startLine", None)
    node.pop("endLine", None)
    node.pop("isExported", None)

    return node


class GitNexusAdapter(BaseAdapter):
    """Adapter for GitNexus KuzuDB code graphs."""

    def list_sources(self) -> List[GraphSource]:
        sources = []
        for repo in _find_gitnexus_repos():
            meta = repo.get("meta", {})
            stats = meta.get("stats", {})
            desc_parts = []
            if stats.get("nodes"):
                desc_parts.append(f"{stats['nodes']} nodes")
            if stats.get("edges"):
                desc_parts.append(f"{stats['edges']} edges")
            if stats.get("communities"):
                desc_parts.append(f"{stats['communities']} clusters")
            if stats.get("processes"):
                desc_parts.append(f"{stats['processes']} flows")
            desc = ", ".join(desc_parts) if desc_parts else "GitNexus index"

            sources.append(GraphSource(
                id=f"gitnexus:{repo['name']}",
                name=repo["name"],
                adapter="gitnexus",
                description=desc,
                group="GitNexus Indexes",
                config={
                    "kuzu_path": repo["kuzu_path"],
                    "repo_path": repo["path"],
                    "meta": meta,
                },
            ))
        return sources

    def load_graph(self, source_id: str) -> UniversalGraph:
        # Find matching source
        src = None
        for s in self.list_sources():
            if s.id == source_id:
                src = s
                break
        if src is None:
            raise ValueError(f"Unknown GitNexus source: {source_id}")

        kuzu_path = src.config["kuzu_path"]

        # Load raw data from KuzuDB
        data = _load_kuzu_graph(kuzu_path)
        nodes = data["nodes"]
        edges = data["edges"]

        # Build edge index for enrichment
        edge_index: Dict[str, list] = {}
        for e in edges:
            sid = e.get("source_id", "")
            tid = e.get("target_id", "")
            edge_index.setdefault(sid, []).append(e)
            edge_index.setdefault(tid, []).append(e)

        # Enrich nodes
        for node in nodes:
            _enrich_gitnexus_node(node, edge_index)

        has_hierarchy = any(n.get("parent_id") for n in nodes)
        has_cats = any(n.get("categories") for n in nodes)
        has_concepts = any(n.get("concepts") for n in nodes)

        return UniversalGraph(
            source=src,
            nodes=nodes,
            edges=edges,
            capabilities={
                "has_hierarchy": has_hierarchy,
                "has_categories": has_cats,
                "has_concepts": has_concepts,
                "has_timestamps": False,  # GitNexus doesn't store timestamps per node
                "has_confidence": True,
                "edge_groups": GITNEXUS_EDGE_GROUPS,
                "type_colors": GITNEXUS_TYPE_COLORS,
                "type_tiers": GITNEXUS_TYPE_TIER,
            },
        )
