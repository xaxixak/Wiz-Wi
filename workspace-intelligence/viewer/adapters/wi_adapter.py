"""WI Code Graph Adapter - wraps existing graphs/*.json files.

Enriches raw graph data with auto-generated tags, categories, and timestamps
so that Semantic, Concept Cluster, and Timeline layouts work for code graphs.
"""
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Any

from .base import BaseAdapter, GraphSource, UniversalGraph

GRAPHS_DIR = Path(__file__).resolve().parent.parent.parent / "graphs"

# WI edge groups (mirrors viewer's EDGE_GROUPS)
WI_EDGE_GROUPS = {
    "structural": ["CONTAINS", "DEFINES", "IMPORTS", "IMPLEMENTS", "INHERITS"],
    "data_flow": ["READS_DB", "WRITES_DB", "MIGRATES"],
    "communication": ["CALLS_API", "CALLS_SERVICE", "CALLS", "WEBHOOK_SEND", "WEBHOOK_RECEIVE"],
    "event_async": ["EMITS_EVENT", "CONSUMES_EVENT", "ENQUEUES", "DEQUEUES", "SCHEDULES"],
    "caching": ["CACHE_READ", "CACHE_WRITE"],
    "routing": ["ROUTES_TO", "INTERCEPTS", "VALIDATES", "AUTHENTICATES"],
    "config_deploy": ["DEPENDS_ON", "DEPLOYED_BY", "CONFIGURES"],
    "quality": ["TESTS"],
}

# Auto-category mapping: node type → functional category
TYPE_CATEGORY = {
    "Workspace": "structure",
    "Project": "structure",
    "Service": "structure",
    "Module": "structure",
    "File": "files",
    "Function": "logic",
    "AsyncHandler": "logic",
    "Middleware": "logic",
    "Endpoint": "api",
    "Router": "api",
    "ExternalAPI": "api",
    "DataModel": "data",
    "Collection": "data",
    "TypeDef": "data",
    "Event": "events",
    "Queue": "events",
    "Resource": "infra",
    "InfraConfig": "infra",
    "EnvVar": "infra",
    "CacheKey": "infra",
}

# Auto-tag rules: infer concept tags from node properties
TAG_RULES = {
    # From file extensions
    ".test.": "testing",
    ".spec.": "testing",
    "__test__": "testing",
    "test_": "testing",
    ".config.": "configuration",
    ".env": "configuration",
    "middleware": "middleware",
    "auth": "authentication",
    "login": "authentication",
    "route": "routing",
    "router": "routing",
    "model": "data-model",
    "schema": "data-model",
    "migration": "database",
    "database": "database",
    "cache": "caching",
    "queue": "messaging",
    "event": "events",
    "webhook": "webhooks",
    "stripe": "payments",
    "payment": "payments",
    "order": "orders",
    "product": "products",
    "user": "users",
    "admin": "admin",
    "dashboard": "ui",
    "api": "api",
    "service": "services",
}


def _enrich_node(node: Dict[str, Any], edge_index: Dict) -> Dict[str, Any]:
    """Enrich a WI graph node with tags, categories, and timestamp."""
    nid = node.get("id", "")
    ntype = node.get("type", "")
    name = node.get("name", "").lower()
    desc = (node.get("description") or "").lower()
    meta = node.get("metadata", {})

    # --- Categories (from node type) ---
    cat = TYPE_CATEGORY.get(ntype, "other")
    node["categories"] = [cat]

    # --- Concepts/Tags (auto-inferred from name + metadata) ---
    tags = list(node.get("tags", []))

    # Always add the node type as a tag
    tags.append(ntype.lower())

    # Check name and description against tag rules
    text = f"{name} {desc} {nid.lower()}"
    for pattern, tag in TAG_RULES.items():
        if pattern in text:
            tags.append(tag)

    # From metadata
    if meta.get("is_async"):
        tags.append("async")
    if meta.get("http_method"):
        tags.append(f"http-{meta['http_method'].lower()}")
    if meta.get("project_type"):
        tags.append(meta["project_type"])
    if meta.get("framework"):
        tags.append(meta["framework"])
    if meta.get("orm"):
        tags.append(meta["orm"])

    # From edges: what behaviors does this node participate in?
    node_edges = edge_index.get(nid, [])
    for e in node_edges:
        etype = e.get("type", "")
        if etype in ("READS_DB", "WRITES_DB"):
            tags.append("database")
        elif etype in ("EMITS_EVENT", "CONSUMES_EVENT"):
            tags.append("events")
        elif etype in ("CACHE_READ", "CACHE_WRITE"):
            tags.append("caching")
        elif etype in ("CALLS_API", "CALLS_SERVICE"):
            tags.append("api-consumer")
        elif etype in ("ENQUEUES", "DEQUEUES"):
            tags.append("messaging")

    # Deduplicate
    node["concepts"] = list(dict.fromkeys(tags))
    node["tags"] = node["concepts"]

    # --- Timestamp (from last_updated or file modification time) ---
    ts = node.get("last_updated")
    if ts:
        # Extract date portion: "2026-02-15T10:21:54.581477Z" → "2026-02-15"
        date_match = re.match(r"(\d{4}-\d{2}-\d{2})", str(ts))
        if date_match:
            node["timestamp"] = date_match.group(1)

    # If no last_updated, try to get from file path in metadata
    if not node.get("timestamp") and meta.get("path"):
        try:
            mtime = os.path.getmtime(meta["path"])
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            node["timestamp"] = dt.strftime("%Y-%m-%d")
        except (OSError, ValueError):
            pass

    return node


def _build_edge_index(edges: List[Dict]) -> Dict[str, List[Dict]]:
    """Build node_id → list of edges index for quick lookup."""
    idx: Dict[str, List[Dict]] = {}
    for e in edges:
        src = e.get("source_id", e.get("source", ""))
        tgt = e.get("target_id", e.get("target", ""))
        if isinstance(src, dict):
            src = src.get("id", "")
        if isinstance(tgt, dict):
            tgt = tgt.get("id", "")
        idx.setdefault(src, []).append(e)
        idx.setdefault(tgt, []).append(e)
    return idx


class WIAdapter(BaseAdapter):
    def list_sources(self) -> List[GraphSource]:
        """One source per scanned graph file."""
        sources = []
        if not GRAPHS_DIR.exists():
            return sources
        for f in sorted(GRAPHS_DIR.glob("*.json")):
            size_kb = f.stat().st_size // 1024
            sources.append(GraphSource(
                id=f"wi:{f.stem}",
                name=f.stem,
                adapter="wi",
                description=f"{size_kb} KB",
                group="Recent Scans",
                config={"path": str(f)},
            ))
        return sources

    def load_graph(self, source_id: str) -> UniversalGraph:
        for src in self.list_sources():
            if src.id == source_id:
                graph_path = src.config["path"]
                break
        else:
            raise ValueError(f"Unknown WI source: {source_id}")

        src = GraphSource(
            id=source_id,
            name=Path(graph_path).stem,
            adapter="wi",
            group="Recent Scans",
            description=f"Code graph ({Path(graph_path).stat().st_size // 1024} KB)",
            config={"path": graph_path},
        )

        with open(graph_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        # Enrich nodes with auto-generated tags, categories, timestamps
        edge_index = _build_edge_index(edges)
        for node in nodes:
            _enrich_node(node, edge_index)

        has_cats = any(n.get("categories") for n in nodes)
        has_concepts = any(n.get("concepts") for n in nodes)
        has_ts = any(n.get("timestamp") for n in nodes)

        return UniversalGraph(
            source=src,
            nodes=nodes,
            edges=edges,
            capabilities={
                "has_hierarchy": True,
                "has_categories": has_cats,
                "has_concepts": has_concepts,
                "has_timestamps": has_ts,
                "has_confidence": True,
                "edge_groups": WI_EDGE_GROUPS,
                "type_colors": {},
                "type_tiers": {},
            },
        )
