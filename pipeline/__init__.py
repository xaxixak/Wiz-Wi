"""
Workspace Intelligence Layer - Pipeline Package

Multi-pass pipeline for building the workspace intelligence graph.

Pass 0: Workspace scanner + bridge (FREE) — project discovery
Pass 1: Tree-sitter AST extraction (FREE) — File/Function/Class nodes
Pass 2: Regex pattern matching (FREE) — endpoints, models, events, etc.
Pass 2b: Behavioral connections (FREE) — CALLS, READS_DB, EMITS_EVENT, etc.
Pass 3: LLM semantic enrichment (PAID) — operational edges, file classification
Pass 4: Validation & scoring (FREE) — future
"""

from .pass1_treesitter import TreeSitterPass
from .pass2_patterns import PatternPass
from .pass2b_connections import ConnectionPass
from .pass3_llm import LLMPass
from .orchestrator import run_pipeline, PipelineResult, print_summary

__all__ = [
    "TreeSitterPass",
    "PatternPass",
    "ConnectionPass",
    "LLMPass",
    "run_pipeline",
    "PipelineResult",
    "print_summary",
]
