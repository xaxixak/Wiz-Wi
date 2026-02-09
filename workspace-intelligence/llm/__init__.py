"""
LLM Client Module
==================

Anthropic API wrapper for the Workspace Intelligence Layer's code analysis pipeline.

This module provides:
- LLMClient: Real-time API calls with prompt caching and tool use
- BatchProcessor: Batch API for cost-efficient bulk analysis (50% cheaper)

Real-time API (LLMClient):
- Prompt caching
- Tool use for structured output
- Exponential backoff with jitter
- Cost tracking

Batch API (BatchProcessor):
- 50% cost reduction for bulk analysis
- Submit up to 10,000 requests per batch
- Poll for completion and download results
- Ideal for initial full-index operations

Usage:
    # Real-time API
    from llm import LLMClient

    client = LLMClient()
    result = await client.analyze(
        messages=[{"role": "user", "content": "Analyze this code..."}],
        tools=[...]
    )

    # Batch API
    from llm import BatchProcessor, BatchRequest

    processor = BatchProcessor()
    requests = [BatchRequest(...), ...]
    results = await processor.run_batch(requests)
"""

from llm.client import LLMClient
from llm.batch import BatchProcessor, BatchRequest, BatchResult

__all__ = ["LLMClient", "BatchProcessor", "BatchRequest", "BatchResult"]
