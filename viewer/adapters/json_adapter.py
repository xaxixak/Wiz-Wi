"""JSON Import Adapter - reads graphs/imports/*.json files."""
import json
from pathlib import Path
from typing import List

from .base import BaseAdapter, GraphSource, UniversalGraph

IMPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "graphs" / "imports"


class JSONAdapter(BaseAdapter):
    def list_sources(self) -> List[GraphSource]:
        sources = []
        if not IMPORTS_DIR.exists():
            return sources
        for f in sorted(IMPORTS_DIR.glob("*.json")):
            size_kb = f.stat().st_size // 1024
            sources.append(GraphSource(
                id=f"json:{f.stem}",
                name=f.stem,
                adapter="json",
                description=f"JSON import ({size_kb} KB)",
                group="Imports",
                config={"path": str(f)},
            ))
        return sources

    def load_graph(self, source_id: str) -> UniversalGraph:
        for src in self.list_sources():
            if src.id == source_id:
                file_path = src.config["path"]
                break
        else:
            raise ValueError(f"Unknown JSON source: {source_id}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Normalize: accept both {nodes, edges} and {nodes, links}
        raw_nodes = data.get("nodes", [])
        raw_edges = data.get("edges", data.get("links", []))

        # Normalize nodes
        nodes = []
        for n in raw_nodes:
            node = {
                "id": n.get("id", ""),
                "type": n.get("type", "node"),
                "name": n.get("name", n.get("label", n.get("id", ""))),
                "tier": n.get("tier", "meso"),
            }
            # Pass through optional universal fields
            for field in ("parent_id", "tags", "concepts", "categories", "timestamp",
                          "confidence", "is_stale", "metadata", "origin", "maturity"):
                if field in n:
                    node[field] = n[field]
            nodes.append(node)

        # Normalize edges
        edges = []
        for e in raw_edges:
            edge = {
                "source_id": e.get("source_id", e.get("source", "")),
                "target_id": e.get("target_id", e.get("target", "")),
                "type": e.get("type", e.get("linkType", "connects")),
                "weight": e.get("weight", 0.5),
            }
            for field in ("confidence", "is_stale", "description", "metadata"):
                if field in e:
                    edge[field] = e[field]
            edges.append(edge)

        # Detect capabilities
        has_hier = any(n.get("parent_id") for n in nodes) or any(e.get("type") == "CONTAINS" for e in edges)
        has_cats = any(n.get("categories") for n in nodes)
        has_concepts = any(n.get("concepts") for n in nodes)
        has_ts = any(n.get("timestamp") for n in nodes)
        edge_types = list(set(e.get("type", "connects") for e in edges))

        return UniversalGraph(
            source=src,
            nodes=nodes,
            edges=edges,
            capabilities={
                "has_hierarchy": has_hier,
                "has_categories": has_cats,
                "has_concepts": has_concepts,
                "has_timestamps": has_ts,
                "has_confidence": any(e.get("confidence") is not None for e in edges),
                "edge_groups": {"imported": edge_types},
                "type_colors": {},
                "type_tiers": {},
            },
        )
