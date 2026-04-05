"""Universal Graph Adapter - Base classes for Phase H0."""
from abc import ABC, abstractmethod
from typing import List


class GraphSource:
    """Describes an available data source."""
    __slots__ = ("id", "name", "adapter", "description", "group", "config")

    def __init__(self, id: str, name: str, adapter: str,
                 description: str = "", group: str = "", config: dict = None):
        self.id = id
        self.name = name
        self.adapter = adapter
        self.description = description
        self.group = group      # for <optgroup> in viewer dropdown
        self.config = config or {}

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "adapter": self.adapter,
            "description": self.description,
            "group": self.group,
        }


class UniversalGraph:
    """Universal graph format returned by all adapters."""
    __slots__ = ("source", "nodes", "edges", "capabilities")

    def __init__(self, source: GraphSource, nodes: list, edges: list,
                 capabilities: dict = None):
        self.source = source
        self.nodes = nodes
        self.edges = edges
        self.capabilities = capabilities or {}

    def to_dict(self):
        return {
            "source": self.source.to_dict(),
            "nodes": self.nodes,
            "edges": self.edges,
            "capabilities": self.capabilities,
        }


class BaseAdapter(ABC):
    @abstractmethod
    def list_sources(self) -> List[GraphSource]:
        """Return available graph sources from this adapter."""
        ...

    @abstractmethod
    def load_graph(self, source_id: str) -> UniversalGraph:
        """Load and normalize graph data for a given source."""
        ...
