"""CSV Import Adapter - reads graphs/imports/*.csv files."""
import csv
from pathlib import Path
from typing import List

from .base import BaseAdapter, GraphSource, UniversalGraph

IMPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "graphs" / "imports"


class CSVAdapter(BaseAdapter):
    def list_sources(self) -> List[GraphSource]:
        sources = []
        if not IMPORTS_DIR.exists():
            return sources
        for f in sorted(IMPORTS_DIR.glob("*.csv")):
            size_kb = f.stat().st_size // 1024
            sources.append(GraphSource(
                id=f"csv:{f.stem}",
                name=f.stem,
                adapter="csv",
                description=f"CSV import ({size_kb} KB)",
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
            raise ValueError(f"Unknown CSV source: {source_id}")

        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            return UniversalGraph(source=src, nodes=[], edges=[])

        cols = set(rows[0].keys())

        # Detect format: edge list (has source+target) or node list
        is_edge_list = {"source", "target"}.issubset(cols)

        nodes = []
        edges = []
        seen_ids = set()

        if is_edge_list:
            for row in rows:
                s, t = row["source"], row["target"]
                for nid in (s, t):
                    if nid not in seen_ids:
                        seen_ids.add(nid)
                        nodes.append({
                            "id": nid,
                            "type": row.get("source_type", "node") if nid == s else row.get("target_type", "node"),
                            "name": nid,
                            "tier": "meso",
                        })
                edges.append({
                    "source_id": s,
                    "target_id": t,
                    "type": row.get("type", row.get("label", "connects")),
                    "weight": float(row["weight"]) if "weight" in row else 0.5,
                    "confidence": 1.0,
                })
        else:
            # Node list — use id or first column as ID
            id_col = "id" if "id" in cols else list(cols)[0]
            name_col = "name" if "name" in cols else id_col
            type_col = "type" if "type" in cols else None
            parent_col = "parent_id" if "parent_id" in cols else ("parent" if "parent" in cols else None)

            for row in rows:
                nid = row[id_col]
                node = {
                    "id": nid,
                    "type": row[type_col] if type_col else "node",
                    "name": row.get(name_col, nid),
                    "tier": "meso",
                    "metadata": {k: v for k, v in row.items() if k not in {id_col, name_col, type_col, parent_col}},
                }
                if "categories" in row and row["categories"]:
                    node["categories"] = [c.strip() for c in row["categories"].split(";")]
                if "timestamp" in row and row["timestamp"]:
                    node["timestamp"] = row["timestamp"]
                nodes.append(node)

                # Create hierarchy edges from parent_id
                if parent_col and row.get(parent_col):
                    edges.append({
                        "source_id": row[parent_col],
                        "target_id": nid,
                        "type": "CONTAINS",
                        "weight": 0.5,
                    })

        # Detect capabilities
        has_ts = any(n.get("timestamp") for n in nodes)
        has_cats = any(n.get("categories") for n in nodes)
        edge_types = list(set(e.get("type", "connects") for e in edges))

        return UniversalGraph(
            source=src,
            nodes=nodes,
            edges=edges,
            capabilities={
                "has_hierarchy": any(e.get("type") == "CONTAINS" for e in edges),
                "has_categories": has_cats,
                "has_concepts": False,
                "has_timestamps": has_ts,
                "has_confidence": False,
                "edge_groups": {"imported": edge_types},
                "type_colors": {},
                "type_tiers": {},
            },
        )
