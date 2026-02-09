"""
Workspace Intelligence Layer - AST-guided Code Chunker

Splits source files into chunks at natural AST boundaries for LLM analysis.
Uses tree-sitter to preserve function/class boundaries and maintain context.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Set
import logging

# Try to import tree-sitter with graceful degradation
try:
    from tree_sitter import Parser, Node as TSNode
    from tree_sitter_languages import get_language
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

logger = logging.getLogger(__name__)


# Language configuration
LANGUAGE_CONFIG = {
    "python": {
        "extensions": [".py"],
        "class_types": ["class_definition"],
        "function_types": ["function_definition"],
        "method_types": ["function_definition"],  # Methods are also function_definition inside class
        "import_types": ["import_statement", "import_from_statement"],
    },
    "javascript": {
        "extensions": [".js", ".jsx"],
        "class_types": ["class_declaration"],
        "function_types": ["function_declaration", "arrow_function", "function_expression"],
        "method_types": ["method_definition"],
        "import_types": ["import_statement"],
        "export_types": ["export_statement"],
    },
    "typescript": {
        "extensions": [".ts", ".tsx"],
        "class_types": ["class_declaration", "interface_declaration"],
        "function_types": ["function_declaration", "arrow_function", "function_expression"],
        "method_types": ["method_definition"],
        "import_types": ["import_statement"],
        "export_types": ["export_statement"],
    },
}


@dataclass
class CodeChunk:
    """A chunk of code at a natural AST boundary."""
    content: str              # The actual code
    start_line: int           # Start line in original file
    end_line: int             # End line in original file
    token_estimate: int       # Rough token count (~4 chars per token)
    entity_names: List[str] = field(default_factory=list)  # Function/class names in this chunk
    imports_context: str = ""  # Import statements prepended for context
    chunk_type: str = "module_level"  # "function", "class", "module_level", "merged"

    def __post_init__(self):
        """Ensure token estimate is calculated if not provided."""
        if self.token_estimate == 0:
            self.token_estimate = len(self.content) // 4


class Chunker:
    """AST-guided code chunker for LLM analysis."""

    def __init__(self, max_tokens: int = 8000, min_tokens: int = 200):
        """
        Args:
            max_tokens: Maximum tokens per chunk (default 8000 for ~32K context).
            min_tokens: Minimum tokens — smaller chunks get merged with neighbors.
        """
        self.max_tokens = max_tokens
        self.min_tokens = min_tokens
        self.parsers: Dict[str, Parser] = {}
        self._init_parsers()

    def _init_parsers(self):
        """Initialize tree-sitter parsers for supported languages."""
        if not TREE_SITTER_AVAILABLE:
            logger.warning("tree-sitter not available. Will use fallback line-based chunking.")
            return

        for lang in ["python", "javascript", "typescript"]:
            try:
                language = get_language(lang)
                parser = Parser()
                parser.set_language(language)
                self.parsers[lang] = parser
                logger.debug(f"Initialized parser for {lang}")
            except Exception as e:
                logger.warning(f"Could not load parser for {lang}: {e}")

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimation: ~4 chars per token for code."""
        return len(text) // 4

    def _node_text(self, node: TSNode, source_bytes: bytes) -> str:
        """Extract text content from a tree-sitter node."""
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')

    def _get_line_text(self, lines: List[str], start_line: int, end_line: int) -> str:
        """Get text from specific line range (1-indexed)."""
        # Convert to 0-indexed
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        return '\n'.join(lines[start_idx:end_idx])

    def _extract_imports(self, tree: TSNode, source_bytes: bytes, language: str) -> str:
        """
        Extract all import statements from the file as a string.
        These will be prepended to every chunk for context.
        """
        import_types = LANGUAGE_CONFIG[language]["import_types"]
        import_nodes = []

        def traverse(node: TSNode):
            if node.type in import_types:
                import_nodes.append(node)
            for child in node.children:
                traverse(child)

        traverse(tree.root_node)

        # Extract import text and deduplicate
        imports = []
        seen = set()
        for node in import_nodes:
            import_text = self._node_text(node, source_bytes).strip()
            if import_text not in seen:
                imports.append(import_text)
                seen.add(import_text)

        return '\n'.join(imports)

    def _extract_entity_name(self, node: TSNode, source_bytes: bytes) -> Optional[str]:
        """Extract function/class name from a node."""
        for child in node.children:
            if child.type in ['identifier', 'type_identifier', 'property_identifier']:
                return self._node_text(child, source_bytes)
        return None

    def _find_top_level_entities(
        self,
        tree: TSNode,
        source_bytes: bytes,
        language: str
    ) -> List[Tuple[TSNode, str, str]]:
        """
        Find all top-level classes and functions.
        Returns list of (node, entity_type, entity_name) tuples.
        entity_type is "class" or "function".
        """
        config = LANGUAGE_CONFIG[language]
        class_types = config["class_types"]
        function_types = config["function_types"]

        entities = []

        def traverse_top_level(node: TSNode, depth: int = 0):
            # Only look at direct children of root or module
            if node.type in class_types:
                name = self._extract_entity_name(node, source_bytes)
                if name:
                    entities.append((node, "class", name))
                    # Don't traverse into class (we handle classes separately)
                    return
            elif node.type in function_types and depth <= 1:
                # Only top-level functions, not nested ones
                name = self._extract_entity_name(node, source_bytes)
                if name:
                    entities.append((node, "function", name))
                    return

            # Continue traversing for top-level entities only
            if depth <= 1:
                for child in node.children:
                    traverse_top_level(child, depth + 1)

        traverse_top_level(tree.root_node)

        # Sort by start position
        entities.sort(key=lambda x: x[0].start_point[0])
        return entities

    def _extract_class_methods(
        self,
        class_node: TSNode,
        source_bytes: bytes,
        language: str
    ) -> List[Tuple[TSNode, str]]:
        """
        Extract method nodes from a class.
        Returns list of (method_node, method_name) tuples.
        """
        config = LANGUAGE_CONFIG[language]
        method_types = config.get("method_types", config["function_types"])

        methods = []

        def traverse(node: TSNode):
            if node.type in method_types and node != class_node:
                name = self._extract_entity_name(node, source_bytes)
                if name:
                    methods.append((node, name))
            else:
                for child in node.children:
                    traverse(child)

        for child in class_node.children:
            traverse(child)

        return methods

    def _get_class_signature(self, class_node: TSNode, source_bytes: bytes) -> str:
        """
        Extract class signature (declaration line with inheritance).
        """
        # Find the line containing the class declaration
        lines = self._node_text(class_node, source_bytes).split('\n')

        # Get first few lines that contain the class declaration
        # (handles multi-line inheritance)
        signature_lines = []
        for line in lines:
            signature_lines.append(line)
            # Stop after we find the opening brace or colon
            if '{' in line or ':' in line:
                break

        return '\n'.join(signature_lines)

    def _split_large_class(
        self,
        class_node: TSNode,
        class_name: str,
        source_bytes: bytes,
        lines: List[str],
        language: str,
        imports_context: str
    ) -> List[CodeChunk]:
        """
        Split a large class into method-level chunks.
        Each chunk includes the class signature + one method.
        """
        chunks = []
        methods = self._extract_class_methods(class_node, source_bytes, language)

        if not methods:
            # Class has no methods, treat as single chunk
            content = self._node_text(class_node, source_bytes)
            return [CodeChunk(
                content=content,
                start_line=class_node.start_point[0] + 1,
                end_line=class_node.end_point[0] + 1,
                token_estimate=self.estimate_tokens(content),
                entity_names=[class_name],
                imports_context=imports_context,
                chunk_type="class"
            )]

        # Get class signature
        class_signature = self._get_class_signature(class_node, source_bytes)

        # Create chunk for each method
        for method_node, method_name in methods:
            method_content = self._node_text(method_node, source_bytes)

            # Combine class signature + method
            full_content = f"{class_signature}\n    # ... other methods omitted ...\n\n{method_content}"

            chunks.append(CodeChunk(
                content=full_content,
                start_line=method_node.start_point[0] + 1,
                end_line=method_node.end_point[0] + 1,
                token_estimate=self.estimate_tokens(full_content),
                entity_names=[class_name, method_name],
                imports_context=imports_context,
                chunk_type="function"  # Method is a function
            ))

        return chunks

    def _create_chunks_from_entities(
        self,
        entities: List[Tuple[TSNode, str, str]],
        source_bytes: bytes,
        lines: List[str],
        language: str,
        imports_context: str
    ) -> List[CodeChunk]:
        """
        Create chunks from top-level entities.
        Handles module-level code between entities.
        """
        chunks = []
        last_end_line = 0

        for node, entity_type, entity_name in entities:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1

            # Check for module-level code before this entity
            if start_line > last_end_line + 1:
                module_code = self._get_line_text(lines, last_end_line + 1, start_line - 1).strip()
                if module_code:
                    chunks.append(CodeChunk(
                        content=module_code,
                        start_line=last_end_line + 1,
                        end_line=start_line - 1,
                        token_estimate=self.estimate_tokens(module_code),
                        entity_names=[],
                        imports_context=imports_context,
                        chunk_type="module_level"
                    ))

            # Create chunk for this entity
            content = self._node_text(node, source_bytes)
            token_estimate = self.estimate_tokens(content)

            # Handle large classes by splitting into methods
            if entity_type == "class" and token_estimate > self.max_tokens:
                class_chunks = self._split_large_class(
                    node, entity_name, source_bytes, lines, language, imports_context
                )
                chunks.extend(class_chunks)
            else:
                # Regular chunk
                chunks.append(CodeChunk(
                    content=content,
                    start_line=start_line,
                    end_line=end_line,
                    token_estimate=token_estimate,
                    entity_names=[entity_name],
                    imports_context=imports_context,
                    chunk_type=entity_type
                ))

            last_end_line = end_line

        # Handle trailing module-level code
        if last_end_line < len(lines):
            module_code = self._get_line_text(lines, last_end_line + 1, len(lines)).strip()
            if module_code:
                chunks.append(CodeChunk(
                    content=module_code,
                    start_line=last_end_line + 1,
                    end_line=len(lines),
                    token_estimate=self.estimate_tokens(module_code),
                    entity_names=[],
                    imports_context=imports_context,
                    chunk_type="module_level"
                ))

        return chunks

    def _merge_small_chunks(self, chunks: List[CodeChunk]) -> List[CodeChunk]:
        """
        Merge adjacent small chunks (both < min_tokens) into larger chunks.
        """
        if not chunks:
            return chunks

        merged = []
        i = 0

        while i < len(chunks):
            current = chunks[i]

            # Check if current chunk is small
            if current.token_estimate < self.min_tokens:
                # Try to merge with next chunk if it's also small
                if i + 1 < len(chunks) and chunks[i + 1].token_estimate < self.min_tokens:
                    next_chunk = chunks[i + 1]

                    # Merge the two chunks
                    merged_content = current.content + "\n\n" + next_chunk.content
                    merged_entities = current.entity_names + next_chunk.entity_names

                    merged_chunk = CodeChunk(
                        content=merged_content,
                        start_line=current.start_line,
                        end_line=next_chunk.end_line,
                        token_estimate=self.estimate_tokens(merged_content),
                        entity_names=merged_entities,
                        imports_context=current.imports_context,
                        chunk_type="merged"
                    )

                    merged.append(merged_chunk)
                    i += 2  # Skip both chunks
                    continue

            # Keep chunk as-is
            merged.append(current)
            i += 1

        return merged

    def _chunk_with_ast(
        self,
        content: str,
        language: str,
        file_path: str = "<stdin>"
    ) -> List[CodeChunk]:
        """
        Chunk content using AST parsing.
        """
        if language not in self.parsers:
            logger.warning(f"No parser available for {language}, using fallback")
            return self._chunk_fallback(content, file_path)

        source_bytes = content.encode('utf-8')
        lines = content.split('\n')

        try:
            parser = self.parsers[language]
            tree = parser.parse(source_bytes)
        except Exception as e:
            logger.error(f"Parse error for {file_path}: {e}, using fallback")
            return self._chunk_fallback(content, file_path)

        # Extract imports for context
        imports_context = self._extract_imports(tree, source_bytes, language)

        # Find top-level entities
        entities = self._find_top_level_entities(tree, source_bytes, language)

        if not entities:
            # No entities found, return entire file as one chunk
            logger.debug(f"No entities found in {file_path}, returning as single chunk")
            return [CodeChunk(
                content=content,
                start_line=1,
                end_line=len(lines),
                token_estimate=self.estimate_tokens(content),
                entity_names=[],
                imports_context=imports_context,
                chunk_type="module_level"
            )]

        # Create chunks from entities
        chunks = self._create_chunks_from_entities(
            entities, source_bytes, lines, language, imports_context
        )

        # Merge small adjacent chunks
        chunks = self._merge_small_chunks(chunks)

        return chunks

    def _chunk_fallback(self, content: str, file_path: str = "<stdin>") -> List[CodeChunk]:
        """
        Fallback chunking strategy: simple line-based splitting.
        Used when AST parsing fails or is unavailable.
        """
        lines = content.split('\n')
        chunks = []

        # Calculate lines per chunk based on max_tokens
        chars_per_chunk = self.max_tokens * 4
        avg_chars_per_line = len(content) / len(lines) if lines else 80
        lines_per_chunk = max(1, int(chars_per_chunk / avg_chars_per_line))

        for i in range(0, len(lines), lines_per_chunk):
            chunk_lines = lines[i:i + lines_per_chunk]
            chunk_content = '\n'.join(chunk_lines)

            chunks.append(CodeChunk(
                content=chunk_content,
                start_line=i + 1,
                end_line=min(i + lines_per_chunk, len(lines)),
                token_estimate=self.estimate_tokens(chunk_content),
                entity_names=[],
                imports_context="",
                chunk_type="module_level"
            ))

        logger.debug(f"Fallback chunking for {file_path}: {len(chunks)} chunks")
        return chunks

    def _detect_language(self, file_path: Path) -> Optional[str]:
        """Detect language from file extension."""
        suffix = file_path.suffix.lower()
        for lang, config in LANGUAGE_CONFIG.items():
            if suffix in config["extensions"]:
                return lang
        return None

    def chunk_file(self, file_path: Path, language: Optional[str] = None) -> List[CodeChunk]:
        """
        Split a file into chunks at natural AST boundaries.

        Strategy:
        1. Parse file with tree-sitter
        2. Extract top-level function/class definitions as chunk boundaries
        3. Module-level code (between functions) becomes its own chunk
        4. Import statements extracted and prepended to every chunk as context
        5. Small adjacent chunks (< min_tokens) merged together
        6. Large functions/classes (> max_tokens) split at method boundaries

        Returns list of CodeChunk objects.
        """
        # Auto-detect language if not provided
        if language is None:
            language = self._detect_language(file_path)

        if language is None:
            logger.warning(f"Could not detect language for {file_path}, using fallback")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                return self._chunk_fallback(content, str(file_path))
            except Exception as e:
                logger.error(f"Error reading file {file_path}: {e}")
                return []

        # Read file content
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return []

        return self._chunk_with_ast(content, language, str(file_path))

    def chunk_content(
        self,
        content: str,
        language: str,
        file_path: str = "<stdin>"
    ) -> List[CodeChunk]:
        """Chunk from a string instead of a file path."""
        if not TREE_SITTER_AVAILABLE or language not in LANGUAGE_CONFIG:
            logger.warning(f"Unsupported language '{language}', using fallback")
            return self._chunk_fallback(content, file_path)

        return self._chunk_with_ast(content, language, file_path)
