"""
Workspace Intelligence Layer - Pass 3: LLM Semantic Analysis
=============================================================

Pass 3 uses LLM to discover operational edges and enrich the graph with
semantic understanding that static analysis cannot detect.

Pipeline:
  Pass 0 (File Discovery): Scans workspace for source files
  Pass 1 (Static Analysis): Extracts File, Function, DataModel, TypeDef nodes
  Pass 2 (Structural Edges): Creates IMPORTS, DEFINES, CONTAINS edges
  Pass 3 (LLM Analysis): Discovers OPERATIONAL edges (READS_DB, CALLS_API, etc.)

This pass focuses on:
  - Operational edges: READS_DB, WRITES_DB, CALLS_API, EMITS_EVENT, ENQUEUES
  - Architectural nodes: ExternalAPI, Resource, Event, Queue, CacheKey, EnvVar
  - File classification: role, tags, complexity, frameworks
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone

from graph_store import GraphStore
from llm.client import LLMClient
from llm.prompts import (
    FILE_CLASSIFICATION_TOOL,
    DISCOVER_EDGES_TOOL,
    DISCOVER_NODES_TOOL,
    build_system_prompt,
    build_classify_prompt,
    build_edge_discovery_prompt,
)
from llm.model_router import ModelRouter, AnalysisTask
from chunker import Chunker, CodeChunk
from ontology import (
    GraphNode,
    GraphEdge,
    NodeType,
    EdgeType,
    Provenance,
    SourceLocation,
)


logger = logging.getLogger(__name__)


class LLMPass:
    """Pass 3: LLM semantic analysis for operational edge discovery."""

    # File patterns to skip (tests, generated code, configs)
    SKIP_PATTERNS = [
        "test_",
        "_test.",
        ".spec.",
        ".test.",
        "__test__",
        "conftest.",
        "node_modules",
        "dist/",
        ".min.js",
        ".min.css",
        "vendor/",
        ".generated.",
        "__pycache__",
        ".pyc",
        "build/",
        "target/",
        ".git/",
    ]

    # Empty file patterns (setup.py, __init__.py often empty)
    EMPTY_FILE_NAMES = ["__init__.py", "setup.py", "conftest.py"]

    def __init__(
        self,
        store: GraphStore,
        client: LLMClient = None,
        router: ModelRouter = None,
    ):
        """
        Initialize with graph store and optional LLM client/router.

        Args:
            store: Graph store to add nodes/edges to
            client: LLM client (creates default if None)
            router: Model router (creates default if None)
        """
        self.store = store
        self._client = client
        self.router = router or ModelRouter()
        self.chunker = Chunker(max_tokens=8000, min_tokens=200)

        # Analysis summary tracking
        self._files_analyzed = 0
        self._files_skipped = 0
        self._nodes_created = 0
        self._edges_created = 0
        self._total_tokens = 0
        self._total_cost = 0.0
        self._errors: List[str] = []

    @property
    def client(self) -> LLMClient:
        """Lazy-initialize LLM client (requires API key at call time, not init time)."""
        if self._client is None:
            self._client = LLMClient()
        return self._client

    def _should_skip(self, file_path: Path) -> bool:
        """
        Check if file should be skipped (tests, generated, configs).

        Args:
            file_path: Path to the file

        Returns:
            True if file should be skipped
        """
        path_str = str(file_path).lower()

        # Check skip patterns
        for pattern in self.SKIP_PATTERNS:
            if pattern in path_str:
                logger.debug(f"Skipping {file_path}: matches pattern '{pattern}'")
                return True

        # Check if file is empty (and is a known empty file type)
        if file_path.name in self.EMPTY_FILE_NAMES:
            try:
                if file_path.stat().st_size < 50:  # Less than 50 bytes
                    logger.debug(f"Skipping {file_path}: empty {file_path.name}")
                    return True
            except Exception:
                pass

        return False

    def _parse_tool_response(self, response: dict, tool_name: str) -> Optional[dict]:
        """
        Extract tool_use input from API response content blocks.

        Args:
            response: API response from client.analyze()
            tool_name: Name of the tool to extract

        Returns:
            Tool input dict or None if not found
        """
        content = response.get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if block.get("name") == tool_name:
                    return block.get("input")
        return None

    def _resolve_edge_target(
        self, target_name: str, known_nodes: Dict[str, GraphNode]
    ) -> Optional[str]:
        """
        Try to match a target name from LLM to an existing node ID.

        Uses fuzzy matching:
        1. Exact name match
        2. Case-insensitive name match
        3. Substring match (target_name in node.name)
        4. Reverse substring match (node.name in target_name)

        Args:
            target_name: Target name from LLM response
            known_nodes: Dict of node_id -> GraphNode

        Returns:
            Matched node ID or None
        """
        target_lower = target_name.lower().strip()

        # Try exact match first
        for node_id, node in known_nodes.items():
            if node.name == target_name:
                return node_id

        # Try case-insensitive match
        for node_id, node in known_nodes.items():
            if node.name.lower() == target_lower:
                return node_id

        # Try substring match (fuzzy)
        for node_id, node in known_nodes.items():
            node_name_lower = node.name.lower()
            # Target is substring of node name
            if target_lower in node_name_lower:
                return node_id
            # Node name is substring of target
            if node_name_lower in target_lower:
                return node_id

        return None

    async def process_file(
        self, file_path: Path, project_id: str, language: str
    ) -> dict:
        """
        Analyze a single file with LLM.

        Steps:
        1. Read file content
        2. Skip if test/generated/config file (check tags, path patterns)
        3. Chunk file if > max_tokens
        4. Get known nodes from graph for edge targets
        5. Phase 1: Classify file with Haiku (FILE_CLASSIFICATION_TOOL)
        6. Phase 2: Discover edges with Sonnet (DISCOVER_EDGES_TOOL)
        7. Phase 3: Discover missed nodes with Sonnet (DISCOVER_NODES_TOOL)
        8. Parse tool_use responses and create GraphNode/GraphEdge objects
        9. Add to graph store with provenance=Provenance.LLM

        Args:
            file_path: Path to the file to analyze
            project_id: Project ID for namespacing
            language: Programming language (python, javascript, typescript)

        Returns:
            Dict with counts: {nodes_created, edges_created, tokens_used, cost, skipped}
        """
        result = {
            "nodes_created": 0,
            "edges_created": 0,
            "tokens_used": 0,
            "cost": 0.0,
            "skipped": False,
            "error": None,
        }

        # Step 2: Check if should skip
        if self._should_skip(file_path):
            result["skipped"] = True
            self._files_skipped += 1
            return result

        # Step 1: Read file content
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                file_content = f.read()
        except Exception as e:
            error_msg = f"Failed to read {file_path}: {e}"
            logger.error(error_msg)
            result["error"] = error_msg
            self._errors.append(error_msg)
            return result

        # Step 3: Chunk file if needed
        chunks = self.chunker.chunk_content(file_content, language, str(file_path))
        if not chunks:
            logger.warning(f"No chunks generated for {file_path}")
            result["skipped"] = True
            self._files_skipped += 1
            return result

        # For simplicity, analyze only the first chunk if file is chunked
        # (In production, you might want to analyze all chunks)
        chunk = chunks[0]
        content_to_analyze = chunk.content

        # Step 4: Get known nodes from graph
        all_nodes = self.store._nodes
        known_node_names = [node.name for node in all_nodes.values()]
        known_nodes_dict = {node.name: node for node in all_nodes.values()}

        try:
            # Step 5: Phase 1 - File Classification (Haiku)
            classify_model = self.router.route(AnalysisTask.FILE_CLASSIFICATION)
            classify_messages = build_classify_prompt(
                content_to_analyze, str(file_path)
            )

            classify_response = await self.client.analyze(
                messages=classify_messages,
                model=classify_model.model_id,
                tools=[FILE_CLASSIFICATION_TOOL],
                max_tokens=classify_model.max_tokens,
                temperature=classify_model.temperature,
                cache_system=False,  # Don't cache for classification (short prompts)
            )

            classification = self._parse_tool_response(
                classify_response, "classify_file"
            )

            # Step 6: Phase 2 - Edge Discovery (Sonnet)
            edge_model = self.router.route(AnalysisTask.EDGE_DISCOVERY)
            edge_system = build_system_prompt(known_node_names)
            edge_messages = build_edge_discovery_prompt(
                content_to_analyze, str(file_path), known_node_names
            )

            edge_response = await self.client.analyze(
                messages=edge_messages,
                model=edge_model.model_id,
                tools=[DISCOVER_EDGES_TOOL, DISCOVER_NODES_TOOL],
                system=edge_system,
                max_tokens=edge_model.max_tokens,
                temperature=edge_model.temperature,
                cache_system=True,  # Cache the system prompt
            )

            edges_data = self._parse_tool_response(edge_response, "discover_edges")
            nodes_data = self._parse_tool_response(edge_response, "discover_nodes")

            # Step 8 & 9: Parse responses and create graph objects

            # Create File node with classification data
            file_id = f"file:{project_id}:{file_path.name}"
            file_tags = []
            file_metadata = {}

            if classification:
                file_tags = classification.get("tags", [])
                file_metadata["file_role"] = classification.get("file_role")
                file_metadata["frameworks_detected"] = classification.get(
                    "frameworks_detected", []
                )
                file_metadata["complexity"] = classification.get("complexity")
                file_metadata["primary_responsibility"] = classification.get(
                    "primary_responsibility"
                )

            file_node = GraphNode(
                id=file_id,
                type=NodeType.FILE,
                name=file_path.name,
                description=file_metadata.get("primary_responsibility"),
                location=SourceLocation(
                    file_path=str(file_path),
                    start_line=1,
                    end_line=len(file_content.split("\n")),
                ),
                provenance=Provenance.LLM,
                confidence=0.9,
                language=language,
                tags=file_tags,
                metadata=file_metadata,
            )

            # Check if file node already exists (from Pass 1)
            existing_file = self.store.get_node(file_id)
            if not existing_file:
                self.store.add_node(file_node)
                result["nodes_created"] += 1
                self._nodes_created += 1
            else:
                # Update existing file node with LLM enrichment
                existing_file.tags.extend(
                    [tag for tag in file_tags if tag not in existing_file.tags]
                )
                existing_file.metadata.update(file_metadata)
                if file_metadata.get("primary_responsibility"):
                    existing_file.description = file_metadata["primary_responsibility"]
                self.store.add_node(existing_file)

            # Create discovered nodes
            if nodes_data and "nodes" in nodes_data:
                for node_spec in nodes_data["nodes"]:
                    node_name = node_spec.get("name")
                    node_type_str = node_spec.get("node_type")
                    node_desc = node_spec.get("description")
                    node_tags = node_spec.get("tags", [])
                    node_confidence = node_spec.get("confidence", 0.7)
                    node_line = node_spec.get("line_number")

                    # Map string to NodeType enum
                    try:
                        node_type = NodeType(node_type_str)
                    except ValueError:
                        logger.warning(
                            f"Unknown node type '{node_type_str}' for {node_name}"
                        )
                        continue

                    # Generate node ID
                    node_id = f"{node_type.value.lower()}:{project_id}:{node_name}"

                    # Check if already exists
                    if self.store.get_node(node_id):
                        continue

                    # Create location if line number provided
                    node_location = None
                    if node_line:
                        node_location = SourceLocation(
                            file_path=str(file_path),
                            start_line=node_line,
                            end_line=node_line,
                        )

                    new_node = GraphNode(
                        id=node_id,
                        type=node_type,
                        name=node_name,
                        description=node_desc,
                        location=node_location,
                        provenance=Provenance.LLM,
                        confidence=node_confidence,
                        language=language,
                        tags=node_tags,
                        parent_id=file_id,
                    )

                    self.store.add_node(new_node)
                    result["nodes_created"] += 1
                    self._nodes_created += 1

            # Create discovered edges
            if edges_data and "edges" in edges_data:
                for edge_spec in edges_data["edges"]:
                    source_name = edge_spec.get("source_name")
                    target_name = edge_spec.get("target_name")
                    edge_type_str = edge_spec.get("edge_type")
                    edge_desc = edge_spec.get("description")
                    edge_confidence = edge_spec.get("confidence", 0.7)
                    edge_conditional = edge_spec.get("conditional", False)
                    edge_line = edge_spec.get("line_number")

                    # Map string to EdgeType enum
                    try:
                        edge_type = EdgeType(edge_type_str)
                    except ValueError:
                        logger.warning(
                            f"Unknown edge type '{edge_type_str}' for {source_name} -> {target_name}"
                        )
                        continue

                    # Resolve source and target to node IDs
                    source_id = self._resolve_edge_target(source_name, all_nodes)
                    target_id = self._resolve_edge_target(target_name, all_nodes)

                    if not source_id:
                        logger.debug(
                            f"Could not resolve source '{source_name}' for edge"
                        )
                        continue

                    if not target_id:
                        logger.debug(
                            f"Could not resolve target '{target_name}' for edge"
                        )
                        continue

                    # Create location if line number provided
                    edge_location = None
                    if edge_line:
                        edge_location = SourceLocation(
                            file_path=str(file_path),
                            start_line=edge_line,
                            end_line=edge_line,
                        )

                    new_edge = GraphEdge(
                        source_id=source_id,
                        target_id=target_id,
                        type=edge_type,
                        description=edge_desc,
                        provenance=Provenance.LLM,
                        confidence=edge_confidence,
                        conditional=edge_conditional,
                        location=edge_location,
                    )

                    # Add edge (may generate validation warnings)
                    violations = self.store.add_edge(new_edge, validate=True)
                    if violations:
                        logger.debug(
                            f"Edge validation warnings for {source_name} -> {target_name}: {violations}"
                        )

                    result["edges_created"] += 1
                    self._edges_created += 1

            # Track cost and tokens
            cost_summary = self.client.get_cost_summary()
            result["tokens_used"] = (
                cost_summary["total_input_tokens"]
                + cost_summary["total_output_tokens"]
            )
            result["cost"] = cost_summary["total_cost_usd"]

            self._total_tokens = result["tokens_used"]
            self._total_cost = result["cost"]
            self._files_analyzed += 1

        except Exception as e:
            error_msg = f"LLM analysis failed for {file_path}: {e}"
            logger.error(error_msg)
            result["error"] = error_msg
            self._errors.append(error_msg)

        return result

    async def process_project(
        self,
        project_path: Path,
        project_id: str,
        language: str,
        concurrency: int = 5,
    ) -> dict:
        """
        Analyze all files in a project concurrently.

        Uses asyncio.Semaphore to limit concurrency and avoid rate limits.
        Skips files matching skip patterns.

        Args:
            project_path: Path to the project directory
            project_id: Project ID for namespacing
            language: Primary language of the project
            concurrency: Max concurrent API calls (default 5)

        Returns:
            Dict with summary: {files_analyzed, files_skipped, nodes_created,
                               edges_created, total_tokens, total_cost, errors}
        """
        # Find all source files
        file_extensions = {
            "python": [".py"],
            "javascript": [".js", ".jsx"],
            "typescript": [".ts", ".tsx"],
        }

        extensions = file_extensions.get(language, [".py", ".js", ".ts"])
        source_files = []

        for ext in extensions:
            source_files.extend(project_path.rglob(f"*{ext}"))

        logger.info(
            f"Found {len(source_files)} {language} files in {project_path}"
        )

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(concurrency)

        async def process_with_semaphore(file_path: Path):
            async with semaphore:
                return await self.process_file(file_path, project_id, language)

        # Process all files concurrently (with semaphore limiting)
        tasks = [process_with_semaphore(f) for f in source_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                error_msg = f"Exception processing {source_files[i]}: {result}"
                logger.error(error_msg)
                self._errors.append(error_msg)

        summary = self.get_analysis_summary()
        return summary

    def get_analysis_summary(self) -> dict:
        """
        Return summary: files analyzed, nodes/edges created, cost.

        Returns:
            Dict with analysis statistics
        """
        return {
            "files_analyzed": self._files_analyzed,
            "files_skipped": self._files_skipped,
            "nodes_created": self._nodes_created,
            "edges_created": self._edges_created,
            "total_tokens": self._total_tokens,
            "total_cost_usd": round(self._total_cost, 4),
            "errors": self._errors,
            "cache_savings_usd": round(
                self.client.get_cost_summary().get("cache_savings_usd", 0.0), 4
            ),
        }

    def process_file_sync(
        self, file_path: Path, project_id: str, language: str
    ) -> dict:
        """
        Synchronous wrapper for process_file().

        This is a convenience method for testing and simple scripts.
        For production use, prefer the async process_file() method.

        Args:
            file_path: Path to the file to analyze
            project_id: Project ID for namespacing
            language: Programming language

        Returns:
            Same as process_file()
        """
        return asyncio.run(self.process_file(file_path, project_id, language))

    async def close(self):
        """Close the LLM client connection."""
        await self.client.close()

    async def __aenter__(self):
        """Async context manager support."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager cleanup."""
        await self.close()
