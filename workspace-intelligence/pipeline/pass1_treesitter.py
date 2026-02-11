"""
Workspace Intelligence Layer - Pass 1: Tree-sitter AST Extraction

FREE pass (no LLM calls). Extracts structural information from source files:
- File nodes with language and line count
- Function/Method nodes with async detection
- Class nodes (as DataModel for ORM-like, TypeDef for interfaces)
- Import relationships between files

Uses tree-sitter for robust, language-agnostic parsing.
"""

import hashlib
import os
from pathlib import Path
from typing import List, Optional, Dict, Any, Set
import logging

# Try to import tree-sitter with graceful degradation
try:
    from tree_sitter import Language, Parser, Node as TSNode
    from tree_sitter_languages import get_language, get_parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    try:
        from tree_sitter import Language, Parser, Node as TSNode
        # Fallback to individual language packages
        import tree_sitter_python
        import tree_sitter_javascript
        import tree_sitter_typescript
        TREE_SITTER_AVAILABLE = True
        USE_INDIVIDUAL_PARSERS = True
    except ImportError:
        TREE_SITTER_AVAILABLE = False
        USE_INDIVIDUAL_PARSERS = False

import sys
from pathlib import Path as PathlibPath

# Add parent directory to path for imports
sys.path.insert(0, str(PathlibPath(__file__).parent.parent))

from ontology import (
    GraphNode, GraphEdge, NodeType, EdgeType, Provenance, SourceLocation
)
from graph_store import GraphStore

logger = logging.getLogger(__name__)


# Language configuration
SUPPORTED_LANGUAGES = {
    "python": {
        "extensions": [".py"],
        "class_types": ["class_definition"],
        "function_types": ["function_definition"],
        "import_types": ["import_statement", "import_from_statement"],
        "async_keywords": ["async"],
    },
    "javascript": {
        "extensions": [".js", ".jsx"],
        "class_types": ["class_declaration"],
        "function_types": ["function_declaration", "arrow_function", "function_expression"],
        "import_types": ["import_statement"],
        "async_keywords": ["async"],
    },
    "typescript": {
        "extensions": [".ts", ".tsx"],
        "class_types": ["class_declaration", "interface_declaration"],
        "function_types": ["function_declaration", "arrow_function", "function_expression", "method_definition"],
        "import_types": ["import_statement"],
        "async_keywords": ["async"],
    },
}


class TreeSitterPass:
    """
    Pass 1 of the intelligence pipeline: Tree-sitter based AST extraction.

    Creates:
    - FILE nodes
    - FUNCTION nodes (methods and functions)
    - DATA_MODEL nodes (ORM entities, Mongoose schemas, etc.)
    - TYPE_DEF nodes (interfaces, type aliases, abstract classes)
    - IMPORTS edges between files
    - DEFINES edges from files to their contents
    """

    def __init__(self, store: GraphStore):
        """
        Initialize the TreeSitter pass.

        Args:
            store: GraphStore instance to add nodes and edges to
        """
        self.store = store
        self.parsers: Dict[str, Any] = {}
        self._init_parsers()

    def _init_parsers(self):
        """Initialize tree-sitter parsers for supported languages."""
        if not TREE_SITTER_AVAILABLE:
            logger.warning("tree-sitter not available. AST extraction will be disabled.")
            return

        try:
            # Use tree-sitter-languages (unified approach)
            for lang in ["python", "javascript", "typescript"]:
                try:
                    # Get language and create parser
                    language = get_language(lang)
                    parser = Parser()
                    parser.set_language(language)
                    self.parsers[lang] = parser
                    logger.info(f"Initialized tree-sitter parser for {lang}")
                except Exception as e:
                    logger.warning(f"Could not load tree-sitter parser for {lang}: {e}")
        except Exception as e:
            logger.error(f"Error initializing tree-sitter parsers: {e}")

    def _compute_source_hash(self, content: str) -> str:
        """Compute SHA256 hash of source content for staleness detection."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _detect_language(self, file_path: Path) -> Optional[str]:
        """Detect language from file extension."""
        suffix = file_path.suffix.lower()
        for lang, config in SUPPORTED_LANGUAGES.items():
            if suffix in config["extensions"]:
                return lang
        return None

    def _node_text(self, node: 'TSNode', source_bytes: bytes) -> str:
        """Extract text content from a tree-sitter node."""
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')

    def _is_async_function(self, node: 'TSNode', source_bytes: bytes) -> bool:
        """Check if a function node is async."""
        # Check for 'async' keyword before function definition
        text = self._node_text(node, source_bytes)
        return text.strip().startswith('async ')

    def _is_orm_class(self, node: 'TSNode', source_bytes: bytes) -> bool:
        """
        Heuristic to detect if a class is an ORM model/schema.

        Checks for common ORM patterns:
        - Inherits from Model, Schema, Document, Entity, Base
        - Has 'schema', 'model', 'entity' in name
        """
        text = self._node_text(node, source_bytes).lower()

        # Common ORM base class names
        orm_keywords = [
            'model', 'schema', 'document', 'entity', 'base',
            'mongoose', 'sequelize', 'typeorm', 'sqlalchemy',
        ]

        for keyword in orm_keywords:
            if keyword in text:
                return True

        return False

    def _is_interface_or_abstract(self, node: 'TSNode', source_bytes: bytes) -> bool:
        """
        Check if a class/type is an interface or abstract type definition.

        For TypeScript: check node type is 'interface_declaration'
        For Python: check for ABC inheritance or 'abstract' keyword
        """
        if node.type == 'interface_declaration':
            return True

        text = self._node_text(node, source_bytes).lower()
        if 'abstract' in text or 'abc' in text or 'protocol' in text:
            return True

        return False

    def _extract_class_name(self, node: 'TSNode', source_bytes: bytes) -> Optional[str]:
        """Extract class/interface name from a class node."""
        for child in node.children:
            if child.type in ['identifier', 'type_identifier']:
                return self._node_text(child, source_bytes)
        return None

    def _extract_function_name(self, node: 'TSNode', source_bytes: bytes) -> Optional[str]:
        """Extract function name from a function node."""
        for child in node.children:
            if child.type in ['identifier', 'property_identifier']:
                return self._node_text(child, source_bytes)
        return None

    def _extract_imports(self, tree: 'TSNode', source_bytes: bytes, language: str) -> List[str]:
        """
        Extract import paths from the AST.

        Returns list of imported module/file paths.
        Handles both ES6 imports and CommonJS require().
        """
        imports = []
        import_types = SUPPORTED_LANGUAGES[language]["import_types"]
        import re as re_mod

        def traverse(node: 'TSNode'):
            # ES6: import x from 'path'
            if node.type in import_types:
                text = self._node_text(node, source_bytes)
                matches = re_mod.findall(r'["\']([^"\']+)["\']', text)
                imports.extend(matches)

            # CommonJS: require('path') or require("path")
            if node.type == "call_expression":
                text = self._node_text(node, source_bytes)
                req_match = re_mod.match(r'require\s*\(\s*["\']([^"\']+)["\']\s*\)', text)
                if req_match:
                    imports.append(req_match.group(1))

            for child in node.children:
                traverse(child)

        traverse(tree.root_node)
        return imports

    def _create_file_node(
        self,
        file_path: Path,
        project_id: str,
        language: str,
        source_content: str,
        line_count: int,
    ) -> GraphNode:
        """Create a FILE node."""
        relative_path = file_path.as_posix()
        node_id = f"file:{project_id}:{relative_path}"

        source_hash = self._compute_source_hash(source_content)

        return GraphNode(
            id=node_id,
            type=NodeType.FILE,
            name=file_path.name,
            description=f"Source file: {relative_path}",
            location=SourceLocation(
                file_path=relative_path,
                start_line=1,
                end_line=line_count,
            ),
            provenance=Provenance.SCANNER,
            confidence=1.0,
            source_hash=source_hash,
            language=language,
            metadata={
                "line_count": line_count,
                "relative_path": relative_path,
            },
        )

    def _create_function_node(
        self,
        function_name: str,
        file_path: Path,
        project_id: str,
        language: str,
        start_line: int,
        end_line: int,
        is_async: bool,
    ) -> GraphNode:
        """Create a FUNCTION node."""
        relative_path = file_path.as_posix()
        node_id = f"function:{project_id}:{relative_path}:{function_name}"

        return GraphNode(
            id=node_id,
            type=NodeType.FUNCTION,
            name=function_name,
            description=f"Function: {function_name}",
            location=SourceLocation(
                file_path=relative_path,
                start_line=start_line,
                end_line=end_line,
            ),
            provenance=Provenance.SCANNER,
            confidence=1.0,
            language=language,
            metadata={
                "is_async": is_async,
            },
        )

    def _create_class_node(
        self,
        class_name: str,
        file_path: Path,
        project_id: str,
        language: str,
        start_line: int,
        end_line: int,
        is_orm: bool,
        is_interface: bool,
    ) -> GraphNode:
        """Create a DATA_MODEL or TYPE_DEF node."""
        relative_path = file_path.as_posix()

        # Choose node type based on classification
        if is_interface:
            node_type = NodeType.TYPE_DEF
            node_id = f"typedef:{project_id}:{relative_path}:{class_name}"
        else:
            node_type = NodeType.DATA_MODEL
            node_id = f"datamodel:{project_id}:{relative_path}:{class_name}"

        return GraphNode(
            id=node_id,
            type=node_type,
            name=class_name,
            description=f"{'Interface' if is_interface else 'Data model'}: {class_name}",
            location=SourceLocation(
                file_path=relative_path,
                start_line=start_line,
                end_line=end_line,
            ),
            provenance=Provenance.SCANNER,
            confidence=1.0 if (is_orm or is_interface) else 0.8,  # Lower confidence for non-ORM classes
            language=language,
            metadata={
                "is_orm": is_orm,
                "is_interface": is_interface,
            },
        )

    def _create_defines_edge(self, file_node: GraphNode, defined_node: GraphNode) -> GraphEdge:
        """Create a DEFINES edge from file to defined entity."""
        return GraphEdge(
            source_id=file_node.id,
            target_id=defined_node.id,
            type=EdgeType.DEFINES,
            provenance=Provenance.SCANNER,
            confidence=1.0,
        )

    def _create_imports_edge(
        self,
        source_file_node: GraphNode,
        target_file_path: str,
        project_id: str,
    ) -> GraphEdge:
        """Create an IMPORTS edge between files."""
        # Construct target file node ID
        target_node_id = f"file:{project_id}:{target_file_path}"

        return GraphEdge(
            source_id=source_file_node.id,
            target_id=target_node_id,
            type=EdgeType.IMPORTS,
            provenance=Provenance.SCANNER,
            confidence=1.0,
        )

    def process_file(self, file_path: Path, project_id: str, language: Optional[str] = None) -> List[GraphNode]:
        """
        Parse a single file and extract nodes.

        Args:
            file_path: Path to the source file
            project_id: Project identifier for node IDs
            language: Language override (auto-detected if None)

        Returns:
            List of created GraphNode objects
        """
        if not TREE_SITTER_AVAILABLE:
            logger.warning("tree-sitter not available, skipping file: %s", file_path)
            return []

        # Detect language
        if language is None:
            language = self._detect_language(file_path)

        if language is None:
            logger.debug("Unsupported file type: %s", file_path)
            return []

        if language not in self.parsers:
            logger.warning("No parser available for language: %s", language)
            return []

        # Read file content
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source_content = f.read()
        except Exception as e:
            logger.error("Error reading file %s: %s", file_path, e)
            return []

        source_bytes = source_content.encode('utf-8')
        line_count = source_content.count('\n') + 1

        # Parse with tree-sitter
        try:
            parser = self.parsers[language]
            tree = parser.parse(source_bytes)
        except Exception as e:
            logger.error("Error parsing file %s: %s", file_path, e)
            return []

        created_nodes = []

        # Create FILE node
        file_node = self._create_file_node(
            file_path=file_path,
            project_id=project_id,
            language=language,
            source_content=source_content,
            line_count=line_count,
        )
        self.store.add_node(file_node)
        created_nodes.append(file_node)

        # Extract language config
        lang_config = SUPPORTED_LANGUAGES[language]
        class_types = lang_config["class_types"]
        function_types = lang_config["function_types"]

        # Traverse AST and extract nodes
        def traverse(node: 'TSNode'):
            # Extract classes
            if node.type in class_types:
                class_name = self._extract_class_name(node, source_bytes)
                if class_name:
                    is_orm = self._is_orm_class(node, source_bytes)
                    is_interface = self._is_interface_or_abstract(node, source_bytes)

                    class_node = self._create_class_node(
                        class_name=class_name,
                        file_path=file_path,
                        project_id=project_id,
                        language=language,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        is_orm=is_orm,
                        is_interface=is_interface,
                    )
                    self.store.add_node(class_node)
                    created_nodes.append(class_node)

                    # Create DEFINES edge
                    defines_edge = self._create_defines_edge(file_node, class_node)
                    self.store.add_edge(defines_edge, validate=False)

            # Extract functions
            elif node.type in function_types:
                function_name = self._extract_function_name(node, source_bytes)
                if function_name:
                    is_async = self._is_async_function(node, source_bytes)

                    function_node = self._create_function_node(
                        function_name=function_name,
                        file_path=file_path,
                        project_id=project_id,
                        language=language,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        is_async=is_async,
                    )
                    self.store.add_node(function_node)
                    created_nodes.append(function_node)

                    # Create DEFINES edge
                    defines_edge = self._create_defines_edge(file_node, function_node)
                    self.store.add_edge(defines_edge, validate=False)

            # Recursively traverse children
            for child in node.children:
                traverse(child)

        traverse(tree.root_node)

        # Extract imports and create IMPORTS edges
        imports = self._extract_imports(tree, source_bytes, language)
        for import_path in imports:
            # Resolve relative imports to absolute paths if possible
            # For now, use as-is (Pass 2 can do more sophisticated resolution)
            try:
                imports_edge = self._create_imports_edge(
                    source_file_node=file_node,
                    target_file_path=import_path,
                    project_id=project_id,
                )
                self.store.add_edge(imports_edge, validate=False)
            except Exception as e:
                logger.debug("Could not create import edge for %s: %s", import_path, e)

        logger.info("Processed file: %s (%d nodes extracted)", file_path, len(created_nodes))
        return created_nodes

    def process_directory(
        self,
        dir_path: Path,
        project_id: str,
        language: Optional[str] = None,
        exclude_patterns: Optional[Set[str]] = None,
    ) -> List[GraphNode]:
        """
        Process all supported files in a directory recursively.

        Args:
            dir_path: Directory path to scan
            project_id: Project identifier for node IDs
            language: Language filter (None = auto-detect all supported languages)
            exclude_patterns: Set of patterns to exclude (e.g., {'node_modules', '__pycache__', '.git'})

        Returns:
            List of all created GraphNode objects
        """
        if exclude_patterns is None:
            exclude_patterns = {
                'node_modules', '__pycache__', '.git', '.venv', 'venv',
                'dist', 'build', '.next', '.cache', 'coverage',
            }

        all_created_nodes = []

        # Determine which extensions to look for
        if language:
            if language not in SUPPORTED_LANGUAGES:
                logger.error("Unsupported language: %s", language)
                return []
            extensions = SUPPORTED_LANGUAGES[language]["extensions"]
        else:
            # All supported extensions
            extensions = []
            for lang_config in SUPPORTED_LANGUAGES.values():
                extensions.extend(lang_config["extensions"])

        # Walk directory tree
        for root_str, dirs, files in os.walk(dir_path):
            root = Path(root_str)

            # Filter out excluded directories
            dirs[:] = [d for d in dirs if d not in exclude_patterns]

            for file_name in files:
                file_path = root / file_name

                # Check if file has supported extension
                if file_path.suffix.lower() in extensions:
                    try:
                        nodes = self.process_file(
                            file_path=file_path,
                            project_id=project_id,
                            language=language,
                        )
                        all_created_nodes.extend(nodes)
                    except Exception as e:
                        logger.error("Error processing file %s: %s", file_path, e)

        logger.info(
            "Processed directory: %s (%d files, %d nodes)",
            dir_path,
            len(all_created_nodes),
            sum(1 for n in all_created_nodes if n.type == NodeType.FILE),
        )

        return all_created_nodes


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def scan_project(
    project_path: Path,
    project_id: str,
    store: GraphStore,
    language: Optional[str] = None,
) -> List[GraphNode]:
    """
    Convenience function to scan an entire project.

    Args:
        project_path: Root directory of the project
        project_id: Unique identifier for the project
        store: GraphStore to populate
        language: Language filter (None = auto-detect all)

    Returns:
        List of all created nodes
    """
    pass1 = TreeSitterPass(store)
    return pass1.process_directory(
        dir_path=project_path,
        project_id=project_id,
        language=language,
    )
