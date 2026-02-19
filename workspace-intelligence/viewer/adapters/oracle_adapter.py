"""Oracle v2 Knowledge Graph Adapter - fetches via HTTP API."""
import json
import urllib.request
import urllib.error
from typing import List

from .base import BaseAdapter, GraphSource, UniversalGraph

ORACLE_API = "http://localhost:47778"

# Map Oracle doc types to viewer tiers
ORACLE_TYPE_TIERS = {
    "principle": "macro",
    "pattern": "meso",
    "learning": "micro",
    "retro": "meso",
    "log": "micro",
}

ORACLE_TYPE_COLORS = {
    "principle": "#FFD700",
    "pattern": "#4A90D9",
    "learning": "#50C878",
    "retro": "#9B59B6",
    "log": "#888888",
}

ORACLE_EDGE_GROUPS = {
    "semantic": ["extends", "depends_on", "refutes", "related", "informed_by"],
    "concept": ["shared_concept"],
}


def _oracle_available() -> bool:
    """Check if Oracle v2 server is reachable by trying the graph endpoint."""
    try:
        # Oracle v2 may not have /api/health — use /api/graph with tiny limit
        url = f"{ORACLE_API}/api/graph?mode=hybrid&limit=1"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        # Server responded (even with error) = it's running
        return e.code < 500
    except Exception:
        return False


class OracleAdapter(BaseAdapter):
    def list_sources(self) -> List[GraphSource]:
        if not _oracle_available():
            return []
        return [GraphSource(
            id="oracle-v2",
            name="Oracle v2 Knowledge",
            adapter="oracle",
            description="Knowledge graph (principles, patterns, learnings)",
            group="Knowledge Graphs",
        )]

    def load_graph(self, source_id: str) -> UniversalGraph:
        if source_id != "oracle-v2":
            raise ValueError(f"Unknown Oracle source: {source_id}")

        # Fetch graph from Oracle v2 API
        url = f"{ORACLE_API}/api/graph?mode=hybrid&limit=2500"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        # Transform nodes
        nodes = []
        for n in raw.get("nodes", []):
            # Build clean display name: prefer label, but fall back to readable id
            # if label looks like a file path (contains / or \)
            raw_label = n.get("label", n.get("name", ""))
            if raw_label and ("/" in raw_label or "\\" in raw_label):
                # file path label — derive name from doc id instead
                # e.g. "retro_2026-01-15_session-summary" → "session-summary"
                # e.g. "learning_2026-01-22_hooks" → "hooks"
                doc_id = n["id"]
                parts = doc_id.split("_", 2)  # [type, date?, slug]
                display_name = parts[-1] if len(parts) >= 2 else doc_id
            elif raw_label:
                display_name = raw_label
            else:
                display_name = n["id"]
            node = {
                "id": n["id"],
                "type": n.get("type", "learning"),
                "name": display_name,
                "tier": ORACLE_TYPE_TIERS.get(n.get("type", ""), "meso"),
                "tags": n.get("concepts", []),
                "concepts": n.get("concepts", []),
                "categories": n.get("categories", []) if isinstance(n.get("categories"), list) else [],
                "confidence": 1.0,
                "is_stale": False,
                "metadata": {
                    "source_file": n.get("source_file", ""),
                    "oracle_type": n.get("type", ""),
                },
            }
            nodes.append(node)

        # Transform edges
        edges = []
        for link in raw.get("links", []):
            conf_raw = link.get("confidence", 100)
            conf = conf_raw / 100.0 if conf_raw > 1 else conf_raw
            edge = {
                "source_id": link.get("source", link.get("source_id", "")),
                "target_id": link.get("target", link.get("target_id", "")),
                "type": link.get("linkType", link.get("type", "related")),
                "weight": link.get("weight", conf),
                "confidence": conf,
                "is_stale": False,
                "description": link.get("context", ""),
            }
            edges.append(edge)

        src = GraphSource(
            id="oracle-v2",
            name="Oracle v2 Knowledge",
            adapter="oracle",
            description=f"{len(nodes)} docs, {len(edges)} links",
        )

        return UniversalGraph(
            source=src,
            nodes=nodes,
            edges=edges,
            capabilities={
                "has_hierarchy": False,
                "has_categories": any(n.get("categories") for n in nodes),
                "has_concepts": any(n.get("concepts") for n in nodes),
                "has_timestamps": False,
                "has_confidence": True,
                "edge_groups": ORACLE_EDGE_GROUPS,
                "type_colors": ORACLE_TYPE_COLORS,
                "type_tiers": ORACLE_TYPE_TIERS,
            },
        )
