"""
Anthropic API Client for Code Analysis
========================================

Direct HTTP client for the Anthropic Messages API with features required for
the Workspace Intelligence Layer's Pass 3 semantic analysis.

Features:
- Async HTTP client using httpx
- Prompt caching for system prompts
- Tool use support for structured output
- Exponential backoff with jitter on rate limits
- Token and cost tracking
"""

import asyncio
import os
import random
from typing import List, Dict, Any, Optional
import httpx


class LLMClient:
    """
    Anthropic API client for semantic code analysis.

    This client wraps the Anthropic Messages API with features optimized for
    the code intelligence pipeline:
    - Prompt caching to reduce costs on repeated system prompts
    - Tool use for structured extraction
    - Retry logic for rate limits and overload
    - Cost tracking across analysis sessions
    """

    # API Configuration
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    # Cost per 1M tokens (in USD)
    PRICING = {
        "claude-sonnet-4-5-20250929": {
            "input": 3.00,
            "output": 15.00,
            "cache_write": 3.75,
            "cache_read": 0.30,
        },
        "claude-haiku-4-5-20251001": {
            "input": 0.80,
            "output": 4.00,
            "cache_write": 1.00,
            "cache_read": 0.08,
        },
        "claude-opus-4-6": {
            "input": 15.00,
            "output": 75.00,
            "cache_write": 18.75,
            "cache_read": 1.50,
        },
    }

    # Retry configuration
    MAX_RETRIES = 4
    BASE_DELAY = 1.0  # seconds
    MAX_DELAY = 8.0   # seconds
    JITTER_FACTOR = 0.5  # +/- 50% jitter

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "claude-sonnet-4-5-20250929"
    ):
        """
        Initialize the LLM client.

        Args:
            api_key: Anthropic API key. If None, reads from ANTHROPIC_API_KEY env var.
            default_model: Default model to use for analysis calls.

        Raises:
            ValueError: If no API key is provided or found in environment.
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No API key provided. Pass api_key parameter or set "
                "ANTHROPIC_API_KEY environment variable."
            )

        self.default_model = default_model

        # Cost tracking
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cache_creation_tokens = 0
        self._total_cache_read_tokens = 0
        self._total_cost = 0.0

        # HTTP client (created on demand)
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx async client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int,
        cache_read_tokens: int,
    ) -> float:
        """
        Calculate the cost of an API call based on token usage.

        Args:
            model: Model name used for the call
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cache_creation_tokens: Number of tokens written to cache
            cache_read_tokens: Number of tokens read from cache

        Returns:
            Cost in USD
        """
        pricing = self.PRICING.get(model)
        if not pricing:
            # Unknown model, use Sonnet pricing as fallback
            pricing = self.PRICING["claude-sonnet-4-5-20250929"]

        cost = (
            (input_tokens / 1_000_000) * pricing["input"]
            + (output_tokens / 1_000_000) * pricing["output"]
            + (cache_creation_tokens / 1_000_000) * pricing["cache_write"]
            + (cache_read_tokens / 1_000_000) * pricing["cache_read"]
        )
        return cost

    def _update_cost_tracking(
        self,
        model: str,
        usage: Dict[str, int]
    ):
        """
        Update internal cost tracking with usage from a response.

        Args:
            model: Model name used for the call
            usage: Usage dict from API response
        """
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
        cache_read_tokens = usage.get("cache_read_input_tokens", 0)

        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._total_cache_creation_tokens += cache_creation_tokens
        self._total_cache_read_tokens += cache_read_tokens

        cost = self._calculate_cost(
            model,
            input_tokens,
            output_tokens,
            cache_creation_tokens,
            cache_read_tokens,
        )
        self._total_cost += cost

    def _add_jitter(self, delay: float) -> float:
        """Add random jitter to delay time."""
        jitter = delay * self.JITTER_FACTOR * (2 * random.random() - 1)
        return delay + jitter

    async def _make_request(
        self,
        payload: Dict[str, Any],
        cache_system: bool = False,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request to the Anthropic API with retry logic.

        Args:
            payload: Request body
            cache_system: Whether to enable prompt caching

        Returns:
            Response JSON

        Raises:
            httpx.HTTPStatusError: On non-retryable errors
            Exception: On max retries exceeded
        """
        client = self._get_client()

        # Build headers
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        # Add prompt caching header if enabled
        if cache_system:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        # Retry loop
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = await client.post(
                    self.API_URL,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code

                # Retry on rate limit (429) or overloaded (529)
                if status_code in (429, 529) and attempt < self.MAX_RETRIES:
                    delay = min(self.BASE_DELAY * (2 ** attempt), self.MAX_DELAY)
                    delay = self._add_jitter(delay)

                    await asyncio.sleep(delay)
                    continue

                # Non-retryable error or max retries exceeded
                raise

    async def analyze(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        cache_system: bool = True,
    ) -> Dict[str, Any]:
        """
        Send a message to the Anthropic Messages API for code analysis.

        This method supports the full Anthropic Messages API with optimizations
        for code analysis:
        - Prompt caching on system prompts (cache_system=True)
        - Tool use for structured output extraction
        - Automatic retry on rate limits with exponential backoff
        - Cost tracking

        Args:
            messages: List of message dicts with "role" and "content"
            model: Model to use (defaults to client's default_model)
            tools: List of tool definitions for structured output
            system: System prompt (will be cached if cache_system=True)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0 = deterministic)
            cache_system: Whether to cache the system prompt

        Returns:
            API response dict containing:
                - id: Response ID
                - model: Model used
                - role: "assistant"
                - content: List of content blocks (text and/or tool_use)
                - stop_reason: Why generation stopped
                - usage: Token usage stats

        Raises:
            ValueError: On invalid parameters
            httpx.HTTPStatusError: On API errors
        """
        if not messages:
            raise ValueError("messages cannot be empty")

        model = model or self.default_model

        # Build request payload
        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        # Add system prompt with optional caching
        if system:
            if cache_system:
                # Add cache control to enable prompt caching
                payload["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
            else:
                payload["system"] = system

        # Add tools if provided
        if tools:
            payload["tools"] = tools

        # Make the API request with retry logic
        response = await self._make_request(payload, cache_system=cache_system)

        # Update cost tracking
        if "usage" in response:
            self._update_cost_tracking(model, response["usage"])

        return response

    def get_cost_summary(self) -> Dict[str, Any]:
        """
        Get a summary of token usage and estimated costs.

        Returns:
            Dict with keys:
                - total_input_tokens: Total input tokens across all calls
                - total_output_tokens: Total output tokens
                - total_cache_creation_tokens: Total tokens written to cache
                - total_cache_read_tokens: Total tokens read from cache
                - total_cost_usd: Total estimated cost in USD
                - cache_savings_usd: Estimated savings from cache hits
        """
        # Calculate what we would have paid without caching
        cost_without_cache = (
            (self._total_cache_read_tokens / 1_000_000)
            * self.PRICING[self.default_model]["input"]
        )

        # Actual cost of cache reads
        cost_with_cache = (
            (self._total_cache_read_tokens / 1_000_000)
            * self.PRICING[self.default_model]["cache_read"]
        )

        cache_savings = cost_without_cache - cost_with_cache

        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cache_creation_tokens": self._total_cache_creation_tokens,
            "total_cache_read_tokens": self._total_cache_read_tokens,
            "total_cost_usd": round(self._total_cost, 4),
            "cache_savings_usd": round(cache_savings, 4),
        }

    def analyze_sync(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        cache_system: bool = True,
    ) -> Dict[str, Any]:
        """
        Synchronous wrapper for analyze().

        This is a convenience method for testing and simple scripts.
        For production use, prefer the async analyze() method.

        Args:
            Same as analyze()

        Returns:
            Same as analyze()
        """
        return asyncio.run(
            self.analyze(
                messages=messages,
                model=model,
                tools=tools,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                cache_system=cache_system,
            )
        )

    def __enter__(self):
        """Context manager support (sync)."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager cleanup (sync)."""
        if self._client is not None:
            asyncio.run(self.close())

    async def __aenter__(self):
        """Async context manager support."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager cleanup."""
        await self.close()
