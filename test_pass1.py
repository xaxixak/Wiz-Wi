"""
Test script for Pass 1: Tree-sitter AST extraction.

Run this to verify the pipeline works correctly.
"""

from pathlib import Path
from graph_store import GraphStore
from pipeline import TreeSitterPass
from ontology import NodeType, EdgeType

def test_pass1():
    """Test Pass 1 on a sample Python file."""

    # Create a test Python file
    test_dir = Path(__file__).parent / "test_data"
    test_dir.mkdir(exist_ok=True)

    test_file = test_dir / "sample.py"
    test_file.write_text("""
# Sample Python file for testing

import os
import sys
from typing import Optional

class User:
    '''User model for database'''
    def __init__(self, name: str):
        self.name = name

    def get_name(self) -> str:
        return self.name

async def fetch_user(user_id: int) -> Optional[User]:
    '''Async function to fetch user'''
    # Simulated async fetch
    return User("test")

def process_data(data: dict):
    '''Synchronous function'''
    return data

class UserInterface:
    '''Abstract interface'''
    pass
""")

    # Initialize store and pass
    store = GraphStore()
    pass1 = TreeSitterPass(store)

    # Process the test file
    print("Processing test file...")
    nodes = pass1.process_file(
        file_path=test_file,
        project_id="test-project",
    )

    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"\nCreated {len(nodes)} nodes:")

    # Print nodes by type
    for node_type in [NodeType.FILE, NodeType.FUNCTION, NodeType.DATA_MODEL, NodeType.TYPE_DEF]:
        type_nodes = [n for n in nodes if n.type == node_type]
        if type_nodes:
            print(f"\n{node_type.value} nodes ({len(type_nodes)}):")
            for node in type_nodes:
                print(f"  - {node.name} (id: {node.id})")
                if node.metadata:
                    print(f"    Metadata: {node.metadata}")

    # Print edges
    print(f"\nEdges:")
    defines_edges = store.get_edges_by_type(EdgeType.DEFINES)
    print(f"  DEFINES edges: {len(defines_edges)}")
    for edge in defines_edges:
        source = store.get_node(edge.source_id)
        target = store.get_node(edge.target_id)
        if source and target:
            print(f"    {source.name} -> {target.name}")

    imports_edges = store.get_edges_by_type(EdgeType.IMPORTS)
    print(f"  IMPORTS edges: {len(imports_edges)}")
    for edge in imports_edges:
        source = store.get_node(edge.source_id)
        print(f"    {source.name if source else edge.source_id} -> {edge.target_id}")

    # Print stats
    print(f"\n{'='*60}")
    print("GRAPH STATS")
    print(f"{'='*60}")
    stats = store.stats()
    print(f"Total nodes: {stats['total_nodes']}")
    print(f"Total edges: {stats['total_edges']}")
    print(f"\nNodes by type:")
    for node_type, count in stats['nodes_by_type'].items():
        if count > 0:
            print(f"  {node_type}: {count}")

    # Cleanup
    test_file.unlink()
    test_dir.rmdir()

    print(f"\n{'='*60}")
    print("TEST COMPLETED SUCCESSFULLY!")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    test_pass1()
