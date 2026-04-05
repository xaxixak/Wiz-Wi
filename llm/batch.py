"""
Anthropic Batch API for Cost-Efficient Bulk Analysis
=====================================================

Batch API integration for submitting bulk analysis requests at 50% reduced cost.
Used for initial full-index of projects where all files are analyzed at once.

The Batch API allows submitting up to 10,000 requests in a single batch, with
results available for download once processing completes. This is ideal for
the initial scan phase where we don't need real-time results.

API Flow:
1. Submit batch: POST /v1/messages/batches with JSONL requests
2. Poll status: GET /v1/messages/batches/{batch_id} until processing_status = "ended"
3. Download results: GET the results_url JSONL file
4. Parse results: Match custom_id to extract responses or errors
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
import httpx


logger = logging.getLogger(__name__)


@dataclass
class BatchRequest:
    """A single request in a batch."""
    custom_id: str          # Unique ID for matching results, e.g. "file:project:path.py"
    model: str
    messages: List[dict]
    system: str = None
    tools: List[dict] = None
    max_tokens: int = 4096
    temperature: float = 0.0

    def to_batch_format(self) -> dict:
        """Convert to Anthropic Batch API request format."""
        params: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": self.messages,
        }

        if self.system:
            params["system"] = self.system

        if self.tools:
            params["tools"] = self.tools

        return {
            "custom_id": self.custom_id,
            "params": params
        }


@dataclass
class BatchResult:
    """Result from a completed batch request."""
    custom_id: str
    success: bool
    response: dict = None    # Full API response if success
    error: str = None        # Error message if failed


class BatchProcessor:
    """
    Anthropic Batch API for cost-efficient bulk analysis.

    The Batch API provides 50% cost savings compared to real-time API calls,
    making it ideal for initial full-index operations where we analyze all
    files in a project at once.

    Usage:
        processor = BatchProcessor()
        requests = [BatchRequest(...), ...]
        results = await processor.run_batch(requests)

        for result in results:
            if result.success:
                # Process response
                pass
            else:
                # Handle error
                pass
    """

    # API Configuration
    API_BASE = "https://api.anthropic.com/v1/messages/batches"
    API_VERSION = "2023-06-01"
    BATCH_BETA_HEADER = "message-batches-2024-09-24"

    # Batch limits
    MAX_REQUESTS_PER_BATCH = 10_000

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize with API key (from param or ANTHROPIC_API_KEY env var).

        Args:
            api_key: Anthropic API key. If None, reads from ANTHROPIC_API_KEY env var.

        Raises:
            ValueError: If no API key is provided or found in environment.
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No API key provided. Pass api_key parameter or set "
                "ANTHROPIC_API_KEY environment variable."
            )

        # HTTP client (created on demand)
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx async client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for batch API requests."""
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "anthropic-beta": self.BATCH_BETA_HEADER,
            "content-type": "application/json",
        }

    async def submit_batch(self, requests: List[BatchRequest]) -> str:
        """
        Submit a batch of requests.

        Steps:
        1. Convert requests to JSONL format
        2. POST to /v1/messages/batches with the JSONL file
        3. Return batch_id

        Args:
            requests: List of BatchRequest objects (max 10,000 per batch)

        Returns:
            batch_id: Unique identifier for tracking the batch

        Raises:
            ValueError: If requests list is empty or exceeds max size
            httpx.HTTPStatusError: On API errors
        """
        if not requests:
            raise ValueError("Requests list cannot be empty")

        if len(requests) > self.MAX_REQUESTS_PER_BATCH:
            raise ValueError(
                f"Batch size {len(requests)} exceeds maximum of "
                f"{self.MAX_REQUESTS_PER_BATCH} requests per batch"
            )

        # Convert requests to batch format
        batch_requests = [req.to_batch_format() for req in requests]

        # Build request payload
        payload = {
            "requests": batch_requests
        }

        client = self._get_client()

        logger.info(f"Submitting batch with {len(requests)} requests")

        # Submit batch
        response = await client.post(
            self.API_BASE,
            json=payload,
            headers=self._get_headers(),
        )
        response.raise_for_status()

        result = response.json()
        batch_id = result["id"]

        logger.info(f"Batch submitted successfully: {batch_id}")
        logger.info(f"Processing status: {result.get('processing_status')}")

        return batch_id

    async def poll_batch(
        self,
        batch_id: str,
        poll_interval: int = 30,
        timeout: int = 3600
    ) -> str:
        """
        Poll batch status until complete.

        Args:
            batch_id: Batch identifier from submit_batch
            poll_interval: Seconds to wait between polls (default: 30)
            timeout: Maximum seconds to wait before raising error (default: 3600)

        Returns:
            Final status: "ended"

        Raises:
            TimeoutError: If batch doesn't complete within timeout
            httpx.HTTPStatusError: On API errors
            RuntimeError: If batch fails or is canceled
        """
        client = self._get_client()
        url = f"{self.API_BASE}/{batch_id}"

        logger.info(f"Polling batch {batch_id} (interval: {poll_interval}s, timeout: {timeout}s)")

        elapsed = 0

        while elapsed < timeout:
            # Get batch status
            response = await client.get(url, headers=self._get_headers())
            response.raise_for_status()

            result = response.json()
            processing_status = result.get("processing_status")
            request_counts = result.get("request_counts", {})

            # Log progress
            total = request_counts.get("total", 0)
            succeeded = request_counts.get("succeeded", 0)
            errored = request_counts.get("errored", 0)
            expired = request_counts.get("expired", 0)
            canceled = request_counts.get("canceled", 0)
            processing = request_counts.get("processing", 0)

            completed = succeeded + errored + expired + canceled

            logger.info(
                f"Batch {batch_id}: {completed}/{total} complete "
                f"(succeeded: {succeeded}, errored: {errored}, processing: {processing})"
            )

            # Check if processing is complete
            if processing_status == "ended":
                logger.info(f"Batch {batch_id} completed")
                return "ended"

            # Check for failure states
            if processing_status in ("canceled", "failed"):
                raise RuntimeError(
                    f"Batch {batch_id} {processing_status}: "
                    f"{succeeded}/{total} succeeded, {errored} errored"
                )

            # Wait before next poll
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Timeout reached
        raise TimeoutError(
            f"Batch {batch_id} did not complete within {timeout}s timeout. "
            f"Last status: {processing_status}"
        )

    async def get_results(self, batch_id: str) -> List[BatchResult]:
        """
        Download and parse batch results.

        Fetches results_url JSONL, parses each line into BatchResult.

        Args:
            batch_id: Batch identifier

        Returns:
            List of BatchResult objects, one per request

        Raises:
            httpx.HTTPStatusError: On API errors
            ValueError: If results_url is not available
        """
        client = self._get_client()
        url = f"{self.API_BASE}/{batch_id}"

        logger.info(f"Fetching results for batch {batch_id}")

        # Get batch metadata including results_url
        response = await client.get(url, headers=self._get_headers())
        response.raise_for_status()

        result = response.json()
        results_url = result.get("results_url")

        if not results_url:
            raise ValueError(
                f"No results_url available for batch {batch_id}. "
                f"Processing status: {result.get('processing_status')}"
            )

        # Download results JSONL
        logger.info(f"Downloading results from {results_url}")
        response = await client.get(results_url)
        response.raise_for_status()

        # Parse JSONL results
        results = []
        lines = response.text.strip().split('\n')

        for line in lines:
            if not line.strip():
                continue

            try:
                entry = json.loads(line)
                custom_id = entry["custom_id"]
                result_data = entry["result"]
                result_type = result_data["type"]

                if result_type == "succeeded":
                    # Success: extract message response
                    results.append(BatchResult(
                        custom_id=custom_id,
                        success=True,
                        response=result_data["message"]
                    ))
                elif result_type == "errored":
                    # Error: extract error details
                    error_info = result_data.get("error", {})
                    error_msg = error_info.get("message", "Unknown error")
                    results.append(BatchResult(
                        custom_id=custom_id,
                        success=False,
                        error=error_msg
                    ))
                else:
                    # Unexpected type
                    results.append(BatchResult(
                        custom_id=custom_id,
                        success=False,
                        error=f"Unexpected result type: {result_type}"
                    ))

            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse result line: {line[:100]}... Error: {e}")
                continue

        logger.info(
            f"Parsed {len(results)} results for batch {batch_id} "
            f"({sum(1 for r in results if r.success)} succeeded)"
        )

        return results

    async def run_batch(
        self,
        requests: List[BatchRequest],
        poll_interval: int = 30,
        timeout: int = 3600
    ) -> List[BatchResult]:
        """
        Convenience: submit + poll + get_results in one call.

        Args:
            requests: List of BatchRequest objects
            poll_interval: Seconds between status polls (default: 30)
            timeout: Maximum seconds to wait for completion (default: 3600)

        Returns:
            List of BatchResult objects

        Raises:
            ValueError: If requests list is invalid
            TimeoutError: If batch doesn't complete in time
            httpx.HTTPStatusError: On API errors
        """
        # Submit the batch
        batch_id = await self.submit_batch(requests)

        try:
            # Poll until complete
            await self.poll_batch(batch_id, poll_interval=poll_interval, timeout=timeout)

            # Download and parse results
            results = await self.get_results(batch_id)

            return results

        except Exception as e:
            logger.error(f"Error processing batch {batch_id}: {e}")
            raise

    def run_batch_sync(
        self,
        requests: List[BatchRequest],
        poll_interval: int = 30,
        timeout: int = 3600
    ) -> List[BatchResult]:
        """
        Sync wrapper for run_batch.

        This is a convenience method for testing and simple scripts.
        For production use, prefer the async run_batch() method.

        Args:
            Same as run_batch()

        Returns:
            Same as run_batch()
        """
        return asyncio.run(
            self.run_batch(
                requests=requests,
                poll_interval=poll_interval,
                timeout=timeout
            )
        )

    async def __aenter__(self):
        """Async context manager support."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager cleanup."""
        await self.close()

    def __enter__(self):
        """Context manager support (sync)."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager cleanup (sync)."""
        if self._client is not None:
            asyncio.run(self.close())
