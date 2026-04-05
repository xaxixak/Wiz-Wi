"""Universal Graph Adapter Registry — Phase H0."""
from .wi_adapter import WIAdapter
from .oracle_adapter import OracleAdapter
from .csv_adapter import CSVAdapter
from .json_adapter import JSONAdapter

ADAPTERS = {
    "wi": WIAdapter(),
    "oracle": OracleAdapter(),
    "csv": CSVAdapter(),
    "json": JSONAdapter(),
}


def list_all_sources():
    """Aggregate sources from all adapters."""
    sources = []
    for name, adapter in ADAPTERS.items():
        try:
            sources.extend(adapter.list_sources())
        except Exception as e:
            print(f"[WARN] Adapter '{name}' failed: {e}")
    return sources


def load_graph(source_id):
    """Load graph from the right adapter based on source_id."""
    for adapter in ADAPTERS.values():
        try:
            for source in adapter.list_sources():
                if source.id == source_id:
                    return adapter.load_graph(source_id)
        except Exception:
            continue
    raise ValueError(f"Unknown source: {source_id}")
