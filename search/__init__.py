"""
Workspace Intelligence Layer - Search Module (Story 5.3)
========================================================

Semantic/vector search over the workspace intelligence graph.

Provides hybrid search combining:
  - Exact substring matching on node names
  - Keyword matching with tokenized query terms
  - TF-IDF cosine similarity for semantic search

All implemented with stdlib only (math, collections, re) -- no sklearn,
no external ML models. Designed as a lightweight MVP that can be upgraded
to sentence-transformers or LanceDB later.

Usage:
    from search import SearchIndex, SearchResult
    from graph_store import GraphStore

    store = GraphStore()
    # ... populate store ...

    index = SearchIndex(store)
    results = index.search("payment processing", limit=5)
    for r in results:
        print(f"{r.score:.2f}  [{r.match_type}]  {r.node.name}")
"""

from search.vector_search import SearchIndex, SearchResult

__all__ = ["SearchIndex", "SearchResult"]
