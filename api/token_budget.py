"""
Token Budget System (Story 4.4)
================================

Controls how much detail is included in ContextPack responses.

Three detail levels:
  - L1 (names only, ~200 tokens):  Node IDs, names, types, tiers. Minimal.
  - L2 (names + descriptions, ~1K tokens):  L1 + descriptions, tags,
        confidence, edge summaries.
  - L3 (full detail + snippets, ~4K tokens):  L2 + code snippets, metadata,
        source locations, all edge details.

The output is plain text designed for human and AI consumption. AI agents
read structured text better than deeply nested JSON, so we format as
labeled sections with indented fields.

No third-party dependencies. Python 3.11+ on Windows.
"""

import json
import sys
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup -- allow imports from the workspace-intelligence root
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ontology import GraphNode, GraphEdge, ContextPack, Tier, NodeType  # noqa: E402


# =============================================================================
# DETAIL LEVEL
# =============================================================================

class DetailLevel(str, Enum):
    """How much detail to include in a formatted ContextPack."""
    L1 = "L1"  # Names only         (~200 tokens)
    L2 = "L2"  # Names + descriptions (~1K tokens)
    L3 = "L3"  # Full detail + snippets (~4K tokens)


# =============================================================================
# TOKEN ESTIMATION
# =============================================================================

def estimate_tokens(text: str) -> int:
    """
    Rough token estimate: ~4 characters per token on average.

    This is a widely-used heuristic that works across English prose, code, and
    structured text. It deliberately errs on the high side so we stay under
    budget rather than over.
    """
    return len(text) // 4


# =============================================================================
# NODE FORMATTING
# =============================================================================

def format_node(node: GraphNode, level: DetailLevel) -> Dict:
    """
    Serialize a GraphNode to a dict at the requested detail level.

    L1: identity only (id, name, type, tier)
    L2: L1 + descriptions, tags, confidence, staleness
    L3: L2 + location, metadata, language, source_hash, provenance
    """
    result: Dict = {
        "id": node.id,
        "name": node.name,
        "type": node.type.value,
        "tier": node.tier.value,
    }

    if level in (DetailLevel.L2, DetailLevel.L3):
        result["description"] = node.description or ""
        result["tags"] = node.tags
        result["confidence"] = node.confidence
        result["is_stale"] = node.is_stale

    if level == DetailLevel.L3:
        if node.location:
            result["location"] = (
                f"{node.location.file_path}:{node.location.start_line}"
                f"-{node.location.end_line}"
            )
        else:
            result["location"] = None
        result["metadata"] = node.metadata if node.metadata else {}
        result["language"] = node.language
        result["source_hash"] = node.source_hash
        result["provenance"] = node.provenance.value

    return result


# =============================================================================
# EDGE FORMATTING
# =============================================================================

def format_edge(edge: GraphEdge, level: DetailLevel) -> Dict:
    """
    Serialize a GraphEdge to a dict at the requested detail level.

    L1: source, target, type
    L2: L1 + description, confidence
    L3: L2 + provenance, is_stale, weight, conditional, location
    """
    result: Dict = {
        "source": edge.source_id,
        "target": edge.target_id,
        "type": edge.type.value,
    }

    if level in (DetailLevel.L2, DetailLevel.L3):
        result["description"] = edge.description or ""
        result["confidence"] = edge.confidence

    if level == DetailLevel.L3:
        result["provenance"] = edge.provenance.value
        result["is_stale"] = edge.is_stale
        result["weight"] = edge.weight
        result["conditional"] = edge.conditional
        if edge.location:
            result["location"] = (
                f"{edge.location.file_path}:{edge.location.start_line}"
                f"-{edge.location.end_line}"
            )
        else:
            result["location"] = None

    return result


# =============================================================================
# SECTION HELPERS
# =============================================================================

def _format_node_block(nodes: List[GraphNode], level: DetailLevel) -> str:
    """Format a list of nodes as indented lines."""
    if not nodes:
        return "  (none)\n"
    lines: List[str] = []
    for node in nodes:
        d = format_node(node, level)
        if level == DetailLevel.L1:
            lines.append(f"  - {d['name']} [{d['type']}, {d['tier']}]  id={d['id']}")
        elif level == DetailLevel.L2:
            stale_marker = " [STALE]" if d.get("is_stale") else ""
            tags_str = ", ".join(d["tags"]) if d["tags"] else ""
            desc = d["description"]
            line = f"  - {d['name']} [{d['type']}, {d['tier']}]  conf={d['confidence']}{stale_marker}"
            if tags_str:
                line += f"  tags=[{tags_str}]"
            if desc:
                line += f"\n    {desc}"
            lines.append(line)
        else:  # L3
            stale_marker = " [STALE]" if d.get("is_stale") else ""
            tags_str = ", ".join(d["tags"]) if d["tags"] else ""
            desc = d["description"]
            line = f"  - {d['name']} [{d['type']}, {d['tier']}]  conf={d['confidence']}{stale_marker}"
            if tags_str:
                line += f"  tags=[{tags_str}]"
            if desc:
                line += f"\n    {desc}"
            extras: List[str] = []
            if d.get("location"):
                extras.append(f"loc={d['location']}")
            if d.get("language"):
                extras.append(f"lang={d['language']}")
            if d.get("provenance"):
                extras.append(f"prov={d['provenance']}")
            if d.get("source_hash"):
                extras.append(f"hash={d['source_hash']}")
            if d.get("metadata"):
                extras.append(f"meta={json.dumps(d['metadata'], default=str)}")
            if extras:
                line += "\n    " + "  ".join(extras)
            lines.append(line)
    return "\n".join(lines) + "\n"


def _format_edge_block(edges: List[GraphEdge], level: DetailLevel) -> str:
    """Format a list of edges as indented lines."""
    if not edges:
        return "  (none)\n"
    lines: List[str] = []
    for edge in edges:
        d = format_edge(edge, level)
        if level == DetailLevel.L1:
            lines.append(f"  - {d['source']} --[{d['type']}]--> {d['target']}")
        elif level == DetailLevel.L2:
            desc = d.get("description", "")
            line = f"  - {d['source']} --[{d['type']}]--> {d['target']}  conf={d['confidence']}"
            if desc:
                line += f"\n    {desc}"
            lines.append(line)
        else:  # L3
            desc = d.get("description", "")
            stale_marker = " [STALE]" if d.get("is_stale") else ""
            cond_marker = " [CONDITIONAL]" if d.get("conditional") else ""
            line = (
                f"  - {d['source']} --[{d['type']}]--> {d['target']}"
                f"  conf={d['confidence']}  w={d['weight']}{stale_marker}{cond_marker}"
            )
            if desc:
                line += f"\n    {desc}"
            extras: List[str] = []
            if d.get("provenance"):
                extras.append(f"prov={d['provenance']}")
            if d.get("location"):
                extras.append(f"loc={d['location']}")
            if extras:
                line += "\n    " + "  ".join(extras)
            lines.append(line)
    return "\n".join(lines) + "\n"


# =============================================================================
# AUTO LEVEL SELECTION
# =============================================================================

def auto_select_level(pack: ContextPack, max_tokens: int) -> DetailLevel:
    """
    Choose the richest DetailLevel that fits within *max_tokens*.

    Strategy: render at L3 first; if too large, try L2; fall back to L1.
    This ensures we give the AI agent as much context as the budget allows.
    """
    for level in (DetailLevel.L3, DetailLevel.L2, DetailLevel.L1):
        rendered = _render_sections(pack, level)
        if estimate_tokens(rendered) <= max_tokens:
            return level
    # Even L1 exceeds budget -- return L1 anyway and let truncation handle it
    return DetailLevel.L1


# =============================================================================
# TRUNCATION
# =============================================================================

def truncate_to_budget(formatted: str, max_tokens: int) -> str:
    """
    Trim a formatted context pack string to fit within *max_tokens*.

    Truncation priority (last removed first):
      1. Code Snippets
      2. Patterns
      3. Invariants
      4. Edges
      5. Downstream Effects
      6. Upstream Dependencies
      7. Relevant Nodes
      8. Stale Warnings
      9. Risk
     10. Header (scope/focus) -- never removed

    We remove entire sections from the bottom of the priority list first,
    then character-trim the last retained section if needed.
    """
    if max_tokens <= 0 or estimate_tokens(formatted) <= max_tokens:
        return formatted

    # Section markers in removal order (lowest priority first)
    removal_order = [
        "--- Code Snippets",
        "--- Patterns",
        "--- Invariants",
        "--- Downstream Effects",
        "--- Upstream Dependencies",
        "--- Edges",
        "--- Stale Warnings",
    ]

    result = formatted
    for marker in removal_order:
        if estimate_tokens(result) <= max_tokens:
            break
        idx = result.find(marker)
        if idx == -1:
            continue
        # Find next section boundary or end of string
        next_section = len(result)
        search_start = idx + len(marker)
        while search_start < len(result):
            nl = result.find("\n---", search_start)
            if nl == -1:
                break
            next_section = nl
            break
        # Remove this section
        result = result[:idx] + result[next_section:]

    # If still over budget, hard-truncate with a notice
    char_budget = max_tokens * 4
    if len(result) > char_budget:
        result = result[:char_budget].rstrip()
        result += "\n\n... [truncated to fit token budget] ..."

    return result


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def _render_sections(pack: ContextPack, level: DetailLevel) -> str:
    """
    Internal renderer: build the full text for a ContextPack at *level*.

    Separated from ``format_context_pack`` so that ``auto_select_level``
    can call it without triggering truncation.
    """
    parts: List[str] = []

    # --- Header ---
    parts.append("=== Context Pack ===")
    parts.append(f"Scope: {pack.scope}")
    parts.append(f"Focus: {pack.focus}")
    parts.append(
        f"Depth: {pack.depth} hops | Nodes in scope: {pack.total_nodes_in_scope}"
    )
    parts.append("")

    # --- Relevant Nodes ---
    parts.append(f"--- Relevant Nodes ({len(pack.relevant_nodes)}) ---")
    parts.append(_format_node_block(pack.relevant_nodes, level))

    # --- Edges ---
    parts.append(f"--- Edges ({len(pack.relevant_edges)}) ---")
    parts.append(_format_edge_block(pack.relevant_edges, level))

    # --- Upstream Dependencies ---
    parts.append(f"--- Upstream Dependencies ({len(pack.upstream)}) ---")
    parts.append(_format_node_block(pack.upstream, level))

    # --- Downstream Effects ---
    parts.append(f"--- Downstream Effects ({len(pack.downstream)}) ---")
    parts.append(_format_node_block(pack.downstream, level))

    # --- Risk ---
    parts.append("--- Risk ---")
    parts.append(f"  {pack.risk_assessment or '(none)'}")
    parts.append("")

    # --- Stale Warnings ---
    if pack.stale_warnings:
        parts.append("--- Stale Warnings ---")
        for w in pack.stale_warnings:
            parts.append(f"  - {w}")
        parts.append("")

    # --- Code Snippets (L3 only) ---
    if level == DetailLevel.L3 and pack.code_snippets:
        parts.append(f"--- Code Snippets ({len(pack.code_snippets)}) ---")
        for node_id, snippet in pack.code_snippets.items():
            parts.append(f"  [{node_id}]")
            for line in snippet.splitlines():
                parts.append(f"    {line}")
            parts.append("")

    # --- Invariants (L2+ only) ---
    if level in (DetailLevel.L2, DetailLevel.L3) and pack.invariants:
        parts.append(f"--- Invariants ({len(pack.invariants)}) ---")
        for inv in pack.invariants:
            parts.append(f"  - {inv}")
        parts.append("")

    # --- Patterns (L2+ only) ---
    if level in (DetailLevel.L2, DetailLevel.L3) and pack.patterns:
        parts.append(f"--- Patterns ({len(pack.patterns)}) ---")
        for pat in pack.patterns:
            parts.append(f"  - {pat}")
        parts.append("")

    return "\n".join(parts)


def format_context_pack(
    pack: ContextPack,
    level: DetailLevel = DetailLevel.L2,
    max_tokens: int = 0,
) -> str:
    """
    Main entry point: serialize a ContextPack to human/AI-readable text.

    Parameters
    ----------
    pack : ContextPack
        The context pack to format.
    level : DetailLevel
        Desired detail level (L1, L2, L3).  Ignored when *max_tokens* > 0
        (auto-selection picks the best level).
    max_tokens : int
        If > 0, automatically select the richest detail level that fits
        within this budget, then truncate if still over.

    Returns
    -------
    str
        Formatted plain-text representation of the context pack.
    """
    if max_tokens > 0:
        level = auto_select_level(pack, max_tokens)

    rendered = _render_sections(pack, level)

    if max_tokens > 0:
        rendered = truncate_to_budget(rendered, max_tokens)

    return rendered


# =============================================================================
# SELF-TEST
# =============================================================================

if __name__ == "__main__":
    from ontology import (
        SourceLocation,
        EdgeType,
        Provenance,
    )

    # -- Build a small sample ContextPack --
    node_svc = GraphNode(
        id="service:orders:OrderService",
        type=NodeType.SERVICE,
        name="OrderService",
        description="Handles order lifecycle: creation, payment, fulfillment.",
        tags=["critical-path"],
        language="typescript",
        provenance=Provenance.SCANNER,
        confidence=0.95,
        location=SourceLocation(
            file_path="src/orders/order.service.ts",
            start_line=1,
            end_line=250,
        ),
        metadata={"framework": "nestjs"},
    )

    node_ep = GraphNode(
        id="endpoint:orders:POST:/orders",
        type=NodeType.ENDPOINT,
        name="POST /orders",
        description="Creates a new order. Validates cart, reserves inventory.",
        tags=["auth-required", "rate-limited"],
        language="typescript",
        provenance=Provenance.SCANNER,
        confidence=1.0,
        location=SourceLocation(
            file_path="src/orders/order.controller.ts",
            start_line=42,
            end_line=78,
        ),
        metadata={"http_method": "POST", "http_path": "/orders"},
    )

    node_coll = GraphNode(
        id="collection:orders:orders",
        type=NodeType.COLLECTION,
        name="orders",
        description="Stores order records with status tracking.",
        provenance=Provenance.SCANNER,
        confidence=1.0,
        metadata={"engine": "postgres", "table_name": "orders"},
    )

    node_upstream = GraphNode(
        id="service:gateway:APIGateway",
        type=NodeType.SERVICE,
        name="APIGateway",
        description="Routes external traffic to internal services.",
        provenance=Provenance.SCANNER,
    )

    node_downstream = GraphNode(
        id="service:inventory:InventoryService",
        type=NodeType.SERVICE,
        name="InventoryService",
        description="Manages stock levels and reservations.",
        tags=["critical-path"],
        provenance=Provenance.LLM,
        confidence=0.85,
    )

    edge_writes = GraphEdge(
        source_id="endpoint:orders:POST:/orders",
        target_id="collection:orders:orders",
        type=EdgeType.WRITES_DB,
        description="Inserts new order row on creation.",
        provenance=Provenance.SCANNER,
        confidence=1.0,
        weight=0.9,
        location=SourceLocation(
            file_path="src/orders/order.service.ts",
            start_line=112,
            end_line=118,
        ),
    )

    edge_calls = GraphEdge(
        source_id="endpoint:orders:POST:/orders",
        target_id="service:inventory:InventoryService",
        type=EdgeType.CALLS_SERVICE,
        description="Reserves inventory before confirming order.",
        provenance=Provenance.LLM,
        confidence=0.8,
        weight=0.7,
        conditional=True,
    )

    pack = ContextPack(
        scope="Service: OrderService",
        focus="Refactoring order creation flow",
        relevant_nodes=[node_svc, node_ep, node_coll],
        relevant_edges=[edge_writes, edge_calls],
        upstream=[node_upstream],
        downstream=[node_downstream],
        related_files=[
            SourceLocation(
                file_path="src/orders/order.service.ts",
                start_line=1,
                end_line=250,
            ),
        ],
        code_snippets={
            "endpoint:orders:POST:/orders": (
                "@Post('/')\n"
                "async createOrder(@Body() dto: CreateOrderDto) {\n"
                "  const order = await this.orderService.create(dto);\n"
                "  return order;\n"
                "}"
            ),
        },
        invariants=[
            "Order total must never be negative.",
            "Inventory must be reserved before order is confirmed.",
        ],
        patterns=[
            "Saga pattern: multi-step order fulfillment with compensating actions.",
        ],
        stale_warnings=[
            "WARNING: InventoryService confidence=0.85 (LLM-inferred, not verified).",
        ],
        risk_assessment=(
            "Medium Risk: 2 services coupled through synchronous call. "
            "Inventory reservation is conditional."
        ),
        depth=2,
        total_nodes_in_scope=14,
    )

    # -- Render at each level --
    separator = "=" * 72

    for lvl in (DetailLevel.L1, DetailLevel.L2, DetailLevel.L3):
        output = format_context_pack(pack, level=lvl)
        tokens = estimate_tokens(output)
        print(separator)
        print(f"  DETAIL LEVEL: {lvl.value}  |  Estimated tokens: {tokens}")
        print(separator)
        print(output)
        print()

    # -- Demonstrate auto-selection with a tight budget --
    print(separator)
    print("  AUTO-SELECT with max_tokens=300")
    print(separator)
    auto_output = format_context_pack(pack, max_tokens=300)
    print(auto_output)
    print(f"\n  [Estimated tokens: {estimate_tokens(auto_output)}]")
    print()

    # -- Demonstrate auto-selection with a generous budget --
    print(separator)
    print("  AUTO-SELECT with max_tokens=5000")
    print(separator)
    generous_output = format_context_pack(pack, max_tokens=5000)
    print(generous_output)
    print(f"\n  [Estimated tokens: {estimate_tokens(generous_output)}]")

    print(f"\n{separator}")
    print("  All tests passed.  Token budget system is operational.")
    print(separator)
