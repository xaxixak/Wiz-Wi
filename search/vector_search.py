"""
Workspace Intelligence Layer - Vector Search (Story 5.3)
========================================================

Hybrid search engine for the workspace intelligence graph.

Search strategy (applied in order, results merged with deduplication):
  1. Exact match   -- substring match on node name (score boosted to 1.0)
  2. Keyword match  -- tokenized query words vs. node text fields
  3. TF-IDF search  -- cosine similarity on TF-IDF vectors (semantic)

TF-IDF is implemented from scratch using only stdlib (math, collections, re)
to avoid adding sklearn or numpy as dependencies.

The index is built lazily on first search if not already built.
Call build_index() explicitly after bulk graph mutations.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Project imports -- adjust sys.path so we can import from the project root
# regardless of where the script is invoked from.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore  # noqa: E402
from ontology import GraphNode, NodeType, Tier  # noqa: E402


# =============================================================================
# STOPWORDS
# =============================================================================

STOPWORDS: Set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
    "for", "of", "with", "and", "or", "not", "this", "that", "it", "from",
    "by", "as",
}


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class SearchResult:
    """A single search result with scoring metadata."""

    node: GraphNode
    score: float          # 0.0 to 1.0, higher = better match
    match_type: str       # "exact", "keyword", "semantic"
    matched_field: str    # "name", "description", "tags", "metadata"


# =============================================================================
# TOKENIZER
# =============================================================================

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> List[str]:
    """
    Split *text* into lowercase alpha-numeric tokens, filtering stopwords.

    Handles camelCase and snake_case by splitting on word boundaries before
    the main regex pass:
      - ``processPayment``  -> ["process", "payment"]
      - ``process_payment`` -> ["process", "payment"]
    """
    # Expand camelCase: insert space before uppercase runs
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Expand snake_case / kebab-case
    expanded = expanded.replace("_", " ").replace("-", " ")

    tokens = _TOKEN_RE.findall(expanded.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


# =============================================================================
# TF-IDF ENGINE (stdlib only)
# =============================================================================

class _TfidfEngine:
    """
    Minimal TF-IDF implementation using only stdlib.

    Vocabulary is built from a corpus of token lists.  Each document is
    represented as a sparse vector (dict mapping term index -> weight).
    Query vectors are built on the fly using the same IDF values.
    """

    def __init__(self) -> None:
        self._vocab: Dict[str, int] = {}       # term -> index
        self._idf: Dict[int, float] = {}        # index -> idf weight
        self._doc_vectors: List[Dict[int, float]] = []  # one per doc
        self._doc_norms: List[float] = []        # pre-computed L2 norms

    # --------------------------------------------------------------------- #
    # Index building
    # --------------------------------------------------------------------- #

    def fit(self, corpus: List[List[str]]) -> None:
        """
        Build vocabulary and IDF from *corpus* (list of token-lists).

        Each entry in *corpus* corresponds to one document (node).
        """
        n_docs = len(corpus)
        if n_docs == 0:
            return

        # --- build vocabulary & document frequencies -----------------------
        df: Counter = Counter()          # term -> num docs containing it
        vocab_set: Dict[str, int] = {}
        idx = 0

        for tokens in corpus:
            seen: Set[str] = set()
            for token in tokens:
                if token not in vocab_set:
                    vocab_set[token] = idx
                    idx += 1
                if token not in seen:
                    df[token] += 1
                    seen.add(token)

        self._vocab = vocab_set

        # --- IDF: log(N / df) with +1 smoothing to avoid division by zero --
        self._idf = {
            vocab_set[term]: math.log((n_docs + 1) / (count + 1)) + 1.0
            for term, count in df.items()
        }

        # --- TF-IDF vectors per document -----------------------------------
        self._doc_vectors = []
        self._doc_norms = []

        for tokens in corpus:
            tf = Counter(tokens)
            vec: Dict[int, float] = {}
            for term, count in tf.items():
                tidx = vocab_set[term]
                # sub-linear TF: 1 + log(tf) to dampen high-frequency terms
                tf_weight = 1.0 + math.log(count) if count > 0 else 0.0
                vec[tidx] = tf_weight * self._idf.get(tidx, 0.0)
            self._doc_vectors.append(vec)
            self._doc_norms.append(_l2_norm(vec))

    # --------------------------------------------------------------------- #
    # Query
    # --------------------------------------------------------------------- #

    def query(self, tokens: List[str], limit: int = 10) -> List[Tuple[int, float]]:
        """
        Return ``(doc_index, cosine_similarity)`` pairs sorted descending.

        Unknown tokens (not in vocabulary) are silently ignored.
        """
        if not self._doc_vectors or not tokens:
            return []

        # Build query vector using the same IDF weights
        tf = Counter(tokens)
        q_vec: Dict[int, float] = {}
        for term, count in tf.items():
            tidx = self._vocab.get(term)
            if tidx is not None:
                tf_weight = 1.0 + math.log(count) if count > 0 else 0.0
                q_vec[tidx] = tf_weight * self._idf.get(tidx, 0.0)

        if not q_vec:
            return []

        q_norm = _l2_norm(q_vec)
        if q_norm == 0.0:
            return []

        # Cosine similarity against every document
        results: List[Tuple[int, float]] = []
        for doc_idx, doc_vec in enumerate(self._doc_vectors):
            d_norm = self._doc_norms[doc_idx]
            if d_norm == 0.0:
                continue
            dot = _dot_product(q_vec, doc_vec)
            sim = dot / (q_norm * d_norm)
            if sim > 0.0:
                results.append((doc_idx, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]


def _dot_product(a: Dict[int, float], b: Dict[int, float]) -> float:
    """Sparse dot product of two vectors represented as dicts."""
    # Iterate over the smaller dict for efficiency
    if len(a) > len(b):
        a, b = b, a
    total = 0.0
    for idx, val in a.items():
        if idx in b:
            total += val * b[idx]
    return total


def _l2_norm(vec: Dict[int, float]) -> float:
    """L2 norm of a sparse vector."""
    return math.sqrt(sum(v * v for v in vec.values()))


# =============================================================================
# NODE TEXT EXTRACTION
# =============================================================================

def _node_text(node: GraphNode) -> str:
    """
    Concatenate all searchable text fields of a node into a single string.

    Fields included: name, description, tags, and select metadata values.
    """
    parts: List[str] = [node.name]
    if node.description:
        parts.append(node.description)
    if node.tags:
        parts.extend(node.tags)
    # Include select metadata string values (e.g., http_method, framework)
    for key in ("http_method", "http_path", "framework", "orm", "table_name",
                "trigger", "prefix"):
        val = node.metadata.get(key)
        if val and isinstance(val, str):
            parts.append(val)
    return " ".join(parts)


def _node_tokens(node: GraphNode) -> List[str]:
    """Tokenize all searchable text fields of a node."""
    return tokenize(_node_text(node))


# =============================================================================
# SEARCH INDEX
# =============================================================================

class SearchIndex:
    """
    Hybrid search index over a :class:`GraphStore`.

    Combines three search strategies, merging and deduplicating results:

    1. **Exact match** -- case-insensitive substring match on node *name*.
       Scores 1.0.
    2. **Keyword match** -- tokenized query words matched against all node
       text fields.  Score is the fraction of query tokens found.
    3. **TF-IDF semantic** -- cosine similarity on TF-IDF vectors built from
       all node text.  Score is the raw cosine similarity (0-1).

    The index is built lazily on the first call to :meth:`search` if
    :meth:`build_index` has not been called yet.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self._tfidf_matrix: Optional[_TfidfEngine] = None
        self._node_ids: List[str] = []
        self._node_token_cache: Dict[str, List[str]] = {}
        self._built = False

    # --------------------------------------------------------------------- #
    # Index lifecycle
    # --------------------------------------------------------------------- #

    def build_index(self) -> None:
        """
        Build (or rebuild) the TF-IDF index from all nodes in the store.

        Call this after bulk mutations to keep the index fresh.
        """
        nodes = self.store.get_all_nodes()
        self._node_ids = [n.id for n in nodes]
        corpus: List[List[str]] = []
        self._node_token_cache.clear()

        for node in nodes:
            tokens = _node_tokens(node)
            corpus.append(tokens)
            self._node_token_cache[node.id] = tokens

        engine = _TfidfEngine()
        engine.fit(corpus)
        self._tfidf_matrix = engine
        self._built = True

    def _ensure_index(self) -> None:
        """Build the index lazily if it hasn't been built yet."""
        if not self._built:
            self.build_index()

    # --------------------------------------------------------------------- #
    # Public search API
    # --------------------------------------------------------------------- #

    def search(
        self,
        query: str,
        limit: int = 10,
        type_filter: Optional[NodeType] = None,
        tier_filter: Optional[Tier] = None,
    ) -> List[SearchResult]:
        """
        Hybrid search: exact match -> keyword -> TF-IDF semantic.

        Results from all three strategies are merged.  If the same node
        appears in multiple result sets the highest score wins.

        Args:
            query:       Free-text search query.
            limit:       Maximum number of results to return.
            type_filter: Optional -- only return nodes of this type.
            tier_filter: Optional -- only return nodes in this tier.

        Returns:
            List of :class:`SearchResult` sorted by score descending.
        """
        self._ensure_index()

        if not query or not query.strip():
            return []

        # Collect results from all strategies
        exact = self._exact_search(query)
        keyword = self._keyword_search(query)
        semantic = self._tfidf_search(query, limit=limit * 3)

        # Merge: deduplicate by node id, keeping the highest score
        best: Dict[str, SearchResult] = {}
        for result in exact + keyword + semantic:
            nid = result.node.id
            if nid not in best or result.score > best[nid].score:
                best[nid] = result

        # Apply filters
        results = list(best.values())
        if type_filter is not None:
            results = [r for r in results if r.node.type == type_filter]
        if tier_filter is not None:
            results = [r for r in results if r.node.tier == tier_filter]

        # Sort by score descending, then by name for stability
        results.sort(key=lambda r: (-r.score, r.node.name))
        return results[:limit]

    # --------------------------------------------------------------------- #
    # Search strategies
    # --------------------------------------------------------------------- #

    def _exact_search(self, query: str) -> List[SearchResult]:
        """
        Exact case-insensitive substring match on node name.

        A hit on the name scores 1.0.  A hit only on the description
        scores 0.9.
        """
        q_lower = query.lower().strip()
        results: List[SearchResult] = []

        for node in self.store.get_all_nodes():
            if q_lower in node.name.lower():
                results.append(SearchResult(
                    node=node,
                    score=1.0,
                    match_type="exact",
                    matched_field="name",
                ))
            elif node.description and q_lower in node.description.lower():
                results.append(SearchResult(
                    node=node,
                    score=0.9,
                    match_type="exact",
                    matched_field="description",
                ))
        return results

    def _keyword_search(self, query: str) -> List[SearchResult]:
        """
        Keyword matching: tokenize the query and check how many tokens
        appear in each node's text.

        Score = matched_tokens / total_query_tokens  (0.0 to ~0.85, capped
        below exact match).
        """
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        query_set = set(query_tokens)
        results: List[SearchResult] = []

        for node in self.store.get_all_nodes():
            node_tokens = self._node_token_cache.get(node.id)
            if node_tokens is None:
                node_tokens = _node_tokens(node)
            node_token_set = set(node_tokens)

            matched = query_set & node_token_set
            if not matched:
                continue

            raw_score = len(matched) / len(query_set)
            # Cap keyword score at 0.85 so exact matches always rank higher
            score = min(raw_score * 0.85, 0.85)

            # Determine which field contributed the match
            matched_field = _best_matched_field(node, matched)

            results.append(SearchResult(
                node=node,
                score=score,
                match_type="keyword",
                matched_field=matched_field,
            ))
        return results

    def _tfidf_search(self, query: str, limit: int = 30) -> List[SearchResult]:
        """
        TF-IDF cosine similarity search.

        Score is the raw cosine similarity scaled to [0, 0.80] so that
        keyword and exact matches rank above purely semantic matches when
        scores are close.
        """
        if self._tfidf_matrix is None:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        raw_results = self._tfidf_matrix.query(query_tokens, limit=limit)

        results: List[SearchResult] = []
        for doc_idx, cosine_sim in raw_results:
            if doc_idx >= len(self._node_ids):
                continue
            node_id = self._node_ids[doc_idx]
            node = self.store.get_node(node_id)
            if node is None:
                continue

            # Scale cosine similarity into [0, 0.80]
            score = min(cosine_sim * 0.80, 0.80)

            results.append(SearchResult(
                node=node,
                score=score,
                match_type="semantic",
                matched_field="description",
            ))
        return results


# =============================================================================
# HELPERS
# =============================================================================

def _best_matched_field(node: GraphNode, matched_tokens: Set[str]) -> str:
    """
    Determine which node field contributed the most matched tokens.

    Returns one of: "name", "description", "tags", "metadata".
    """
    name_tokens = set(tokenize(node.name))
    if matched_tokens & name_tokens:
        return "name"

    if node.description:
        desc_tokens = set(tokenize(node.description))
        if matched_tokens & desc_tokens:
            return "description"

    if node.tags:
        tag_tokens = set(tokenize(" ".join(node.tags)))
        if matched_tokens & tag_tokens:
            return "tags"

    return "metadata"


# =============================================================================
# CLI / DEMO
# =============================================================================

if __name__ == "__main__":
    from ontology import Provenance

    print("=" * 70)
    print("  Story 5.3 -- Vector Search Demo")
    print("=" * 70)

    # --- Build a small demo graph ------------------------------------------
    store = GraphStore()

    demo_nodes = [
        GraphNode(
            id="service:order-api",
            type=NodeType.SERVICE,
            name="Order API",
            description="REST API for order management and checkout flow",
            tags=["critical-path"],
            metadata={"framework": "express"},
        ),
        GraphNode(
            id="function:process-payment",
            type=NodeType.FUNCTION,
            name="processPayment",
            description="Handles payment processing via Stripe integration",
            tags=["critical-path", "sensitive-data"],
            metadata={"framework": "express"},
        ),
        GraphNode(
            id="endpoint:post-orders",
            type=NodeType.ENDPOINT,
            name="POST /orders",
            description="Creates a new order, validates inventory, and initiates payment",
            metadata={"http_method": "POST", "http_path": "/api/v1/orders"},
        ),
        GraphNode(
            id="collection:orders",
            type=NodeType.COLLECTION,
            name="orders",
            description="Orders table storing all customer orders with status tracking",
            metadata={"engine": "postgres", "table_name": "orders"},
        ),
        GraphNode(
            id="collection:payments",
            type=NodeType.COLLECTION,
            name="payments",
            description="Payment records linked to orders with Stripe transaction IDs",
            metadata={"engine": "postgres", "table_name": "payments"},
        ),
        GraphNode(
            id="function:validate-inventory",
            type=NodeType.FUNCTION,
            name="validateInventory",
            description="Checks product availability and reserves stock for an order",
            tags=["utility"],
        ),
        GraphNode(
            id="function:send-order-email",
            type=NodeType.FUNCTION,
            name="sendOrderConfirmationEmail",
            description="Sends order confirmation email to customer after successful checkout",
            tags=["notification"],
        ),
        GraphNode(
            id="data-model:user",
            type=NodeType.DATA_MODEL,
            name="User",
            description="User entity with authentication credentials and profile information",
            tags=["auth-required"],
            metadata={"orm": "prisma"},
        ),
        GraphNode(
            id="middleware:auth",
            type=NodeType.MIDDLEWARE,
            name="authMiddleware",
            description="JWT authentication middleware that validates bearer tokens",
            tags=["auth-required", "rate-limited"],
        ),
        GraphNode(
            id="event:order-created",
            type=NodeType.EVENT,
            name="ORDER_CREATED",
            description="Domain event emitted when a new order is successfully placed",
        ),
        GraphNode(
            id="external-api:stripe",
            type=NodeType.EXTERNAL_API,
            name="Stripe API",
            description="Payment processing gateway for credit card and subscription billing",
        ),
        GraphNode(
            id="queue:email-queue",
            type=NodeType.QUEUE,
            name="email-queue",
            description="Message queue for asynchronous email delivery",
        ),
    ]

    for node in demo_nodes:
        store.add_node(node)

    print(f"\nLoaded {len(demo_nodes)} demo nodes into GraphStore.")

    # --- Build search index ------------------------------------------------
    index = SearchIndex(store)
    index.build_index()
    print("TF-IDF index built.\n")

    # --- Run demo queries --------------------------------------------------
    queries = [
        "payment",
        "find payment code",
        "order checkout",
        "email notification",
        "authentication JWT",
        "stripe",
        "inventory",
        "processPayment",
    ]

    for q in queries:
        print(f"--- Query: \"{q}\" ---")
        results = index.search(q, limit=5)
        if not results:
            print("  (no results)")
        for r in results:
            print(f"  {r.score:.3f}  [{r.match_type:<8}]  [{r.matched_field:<12}]  "
                  f"{r.node.type.value:<14}  {r.node.name}")
        print()

    # --- Filtered search ---------------------------------------------------
    print("--- Filtered: type=Function, query='payment' ---")
    results = index.search("payment", type_filter=NodeType.FUNCTION, limit=5)
    for r in results:
        print(f"  {r.score:.3f}  [{r.match_type:<8}]  {r.node.name}")
    print()

    print("--- Filtered: tier=MICRO, query='order' ---")
    results = index.search("order", tier_filter=Tier.MICRO, limit=5)
    for r in results:
        print(f"  {r.score:.3f}  [{r.match_type:<8}]  {r.node.name}")
    print()

    # --- Tokenizer demo ----------------------------------------------------
    print("--- Tokenizer demo ---")
    test_strings = [
        "processPayment",
        "validate_inventory_stock",
        "POST /api/v1/orders",
        "JWT authentication middleware",
    ]
    for s in test_strings:
        print(f"  {s!r:40s} -> {tokenize(s)}")

    print("\nDone.")
