"""
Workspace Intelligence Layer - Pass 2: Pattern Matching
=========================================================

Regex-based pattern detection for architectural elements that cannot be reliably
extracted by tree-sitter alone.

This pass is FREE (no LLM calls) and runs AFTER Pass 1 (tree-sitter).

Detects:
  - HTTP endpoints (Express, Flask, FastAPI, Django, Spring)
  - Data models (Mongoose, SQLAlchemy, Prisma, Django ORM)
  - Events (EventEmitter, event decorators)
  - Middleware (Express, NestJS, Django)
  - Queue operations (Bull, BullMQ, Celery)
  - Environment variables
  - Cache operations (Redis, generic cache)
  - Scheduled tasks (cron, decorators)

Design:
  - Pattern definitions stored as dataclass instances for extensibility
  - Each pattern creates appropriate NodeType with metadata
  - Creates DEFINES edge from File node to new node
  - Node IDs follow consistent format: {type}:{project}:{identifier}
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import hashlib

from ontology import (
    GraphNode, GraphEdge, NodeType, EdgeType, Provenance,
    SourceLocation, MetadataKey, WellKnownTag,
)
from graph_store import GraphStore


# =============================================================================
# PATTERN DEFINITION
# =============================================================================

@dataclass
class Pattern:
    """
    Definition of a regex pattern for detecting architectural elements.

    Attributes:
        name: Human-readable pattern name
        regex: Compiled regex pattern
        node_type: Type of node to create when pattern matches
        language: Programming language this pattern applies to (None = all)
        metadata_extractor: Function to extract metadata from match
        tags: Tags to apply to created nodes
        description_template: Template for node description
    """
    name: str
    regex: re.Pattern
    node_type: NodeType
    language: Optional[str] = None
    metadata_extractor: Optional[callable] = None
    tags: List[str] = None
    description_template: str = ""

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


# =============================================================================
# METADATA EXTRACTORS
# =============================================================================

def extract_http_endpoint_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract metadata from HTTP endpoint patterns."""
    groups = match.groupdict()
    method = groups.get('method', '').upper()
    path = groups.get('path', '').strip('\'"')

    return {
        MetadataKey.HTTP_METHOD: method,
        MetadataKey.HTTP_PATH: path,
    }


def extract_express_route_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract Express.js route metadata."""
    groups = match.groupdict()
    method = groups.get('method', 'USE').upper()

    # Extract path from the line
    path_match = re.search(r'[\'"]([^\'"]+)[\'"]', line[match.start():])
    path = path_match.group(1) if path_match else '/'

    return {
        MetadataKey.HTTP_METHOD: method,
        MetadataKey.HTTP_PATH: path,
        MetadataKey.FRAMEWORK: 'express',
    }


def extract_flask_route_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract Flask route metadata."""
    groups = match.groupdict()

    # For @app.route decorator
    if 'path' in groups:
        path = groups['path'].strip('\'"')
        # Try to find methods in decorator
        methods_match = re.search(r'methods\s*=\s*\[([^\]]+)\]', line)
        method = 'GET'  # Default
        if methods_match:
            methods_str = methods_match.group(1)
            method = methods_str.strip('\'"').split(',')[0].strip('\'"').upper()
    else:
        # For @app.get, @app.post, etc.
        method = groups.get('method', 'GET').upper()
        path_match = re.search(r'[\'"]([^\'"]+)[\'"]', line[match.start():])
        path = path_match.group(1) if path_match else '/'

    return {
        MetadataKey.HTTP_METHOD: method,
        MetadataKey.HTTP_PATH: path,
        MetadataKey.FRAMEWORK: 'flask',
    }


def extract_fastapi_route_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract FastAPI route metadata."""
    groups = match.groupdict()
    method = groups.get('method', 'GET').upper()

    path_match = re.search(r'[\'"]([^\'"]+)[\'"]', line[match.start():])
    path = path_match.group(1) if path_match else '/'

    return {
        MetadataKey.HTTP_METHOD: method,
        MetadataKey.HTTP_PATH: path,
        MetadataKey.FRAMEWORK: 'fastapi',
    }


def extract_spring_mapping_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract Spring @*Mapping metadata."""
    groups = match.groupdict()
    method = groups.get('method', 'REQUEST').replace('Mapping', '').upper()
    if method == 'REQUEST':
        method = 'GET'  # Default

    path_match = re.search(r'[\'"]([^\'"]+)[\'"]', line[match.start():])
    path = path_match.group(1) if path_match else '/'

    return {
        MetadataKey.HTTP_METHOD: method,
        MetadataKey.HTTP_PATH: path,
        MetadataKey.FRAMEWORK: 'spring',
    }


def extract_django_path_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract Django path() metadata."""
    groups = match.groupdict()
    path = groups.get('path', '').strip('\'"')

    return {
        MetadataKey.HTTP_PATH: path,
        MetadataKey.FRAMEWORK: 'django',
    }


def extract_mongoose_model_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract Mongoose model metadata."""
    groups = match.groupdict()
    model_name = groups.get('model', '').strip('\'"')

    return {
        MetadataKey.TABLE_NAME: model_name,
        MetadataKey.ORM: 'mongoose',
        MetadataKey.ENGINE: 'mongodb',
    }


def extract_sqlalchemy_model_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract SQLAlchemy model metadata."""
    groups = match.groupdict()
    class_name = groups.get('name', '')

    # Table name is usually lowercase plural
    table_name = class_name.lower() + 's'

    return {
        MetadataKey.TABLE_NAME: table_name,
        MetadataKey.ORM: 'sqlalchemy',
    }


def extract_prisma_model_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract Prisma model metadata."""
    groups = match.groupdict()
    model_name = groups.get('name', '')

    return {
        MetadataKey.TABLE_NAME: model_name,
        MetadataKey.ORM: 'prisma',
    }


def extract_django_model_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract Django model metadata."""
    groups = match.groupdict()
    class_name = groups.get('name', '')

    # Table name is usually app_modelname
    table_name = class_name.lower()

    return {
        MetadataKey.TABLE_NAME: table_name,
        MetadataKey.ORM: 'django',
    }


def extract_event_emit_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract event emit metadata."""
    groups = match.groupdict()
    event_name = groups.get('event', '').strip('\'"')

    return {
        'event_name': event_name,
    }


def extract_event_listener_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract event listener metadata."""
    groups = match.groupdict()
    event_name = groups.get('event', '').strip('\'"')

    return {
        'event_name': event_name,
    }


def extract_queue_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract queue metadata."""
    groups = match.groupdict()
    queue_name = groups.get('queue', '').strip('\'"')

    return {
        'queue_name': queue_name,
    }


def extract_processor_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract queue processor metadata."""
    groups = match.groupdict()
    queue_name = groups.get('queue', '').strip('\'"')

    return {
        'queue_name': queue_name,
        MetadataKey.TRIGGER: 'queue',
    }


def extract_celery_task_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract Celery task metadata."""
    return {
        MetadataKey.TRIGGER: 'queue',
        MetadataKey.FRAMEWORK: 'celery',
    }


def extract_cron_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract cron schedule metadata."""
    groups = match.groupdict()
    schedule = groups.get('schedule', '').strip('\'"')

    return {
        MetadataKey.TRIGGER: 'cron',
        MetadataKey.SCHEDULE: schedule,
    }


def extract_schedule_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract schedule metadata."""
    return {
        MetadataKey.TRIGGER: 'schedule',
    }


def extract_env_var_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract environment variable metadata."""
    groups = match.groupdict()
    var_name = groups.get('var', '').strip('\'"')

    return {
        'var_name': var_name,
    }


def extract_cache_key_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract cache key metadata."""
    groups = match.groupdict()
    key = groups.get('key', '').strip('\'"')

    return {
        'cache_key': key,
    }


def extract_middleware_metadata(match: re.Match, line: str) -> Dict[str, Any]:
    """Extract middleware metadata."""
    return {
        MetadataKey.FRAMEWORK: 'express',
    }


# =============================================================================
# PATTERN REGISTRY
# =============================================================================

PATTERNS: List[Pattern] = [
    # -------------------------------------------------------------------------
    # HTTP ENDPOINTS
    # -------------------------------------------------------------------------

    # Express.js: app.get('/path', ...), router.post('/path', ...)
    Pattern(
        name="express_route",
        regex=re.compile(r'(?:app|router)\.(?P<method>get|post|put|delete|patch|use)\s*\('),
        node_type=NodeType.ENDPOINT,
        language="javascript",
        metadata_extractor=extract_express_route_metadata,
        tags=[],
        description_template="Express.js {method} endpoint: {path}",
    ),

    # Flask: @app.route('/path', methods=['GET'])
    Pattern(
        name="flask_route_decorator",
        regex=re.compile(r'@(?:app|router|blueprint)\.route\s*\(\s*[\'"](?P<path>[^\'"]+)[\'"]'),
        node_type=NodeType.ENDPOINT,
        language="python",
        metadata_extractor=extract_flask_route_metadata,
        tags=[],
        description_template="Flask endpoint: {path}",
    ),

    # Flask: @app.get('/path'), @app.post('/path')
    Pattern(
        name="flask_method_decorator",
        regex=re.compile(r'@(?:app|router|blueprint)\.(?P<method>get|post|put|delete|patch)\s*\('),
        node_type=NodeType.ENDPOINT,
        language="python",
        metadata_extractor=extract_flask_route_metadata,
        tags=[],
        description_template="Flask {method} endpoint: {path}",
    ),

    # FastAPI: @app.get('/path'), @router.post('/path')
    Pattern(
        name="fastapi_route",
        regex=re.compile(r'@(?:app|router)\.(?P<method>get|post|put|delete|patch)\s*\('),
        node_type=NodeType.ENDPOINT,
        language="python",
        metadata_extractor=extract_fastapi_route_metadata,
        tags=[],
        description_template="FastAPI {method} endpoint: {path}",
    ),

    # Django: path('path/', view_func)
    Pattern(
        name="django_path",
        regex=re.compile(r'path\s*\(\s*[\'"](?P<path>[^\'"]+)[\'"]'),
        node_type=NodeType.ENDPOINT,
        language="python",
        metadata_extractor=extract_django_path_metadata,
        tags=[],
        description_template="Django endpoint: {path}",
    ),

    # Spring: @GetMapping, @PostMapping, @RequestMapping
    Pattern(
        name="spring_mapping",
        regex=re.compile(r'@(?P<method>Get|Post|Put|Delete|Patch|Request)Mapping\s*\('),
        node_type=NodeType.ENDPOINT,
        language="java",
        metadata_extractor=extract_spring_mapping_metadata,
        tags=[],
        description_template="Spring {method} endpoint: {path}",
    ),

    # -------------------------------------------------------------------------
    # DATA MODELS
    # -------------------------------------------------------------------------

    # Mongoose: mongoose.model('ModelName', schema)
    Pattern(
        name="mongoose_model",
        regex=re.compile(r'mongoose\.model\s*\(\s*[\'"](?P<model>[^\'"]+)[\'"]'),
        node_type=NodeType.DATA_MODEL,
        language="javascript",
        metadata_extractor=extract_mongoose_model_metadata,
        tags=[],
        description_template="Mongoose model: {model}",
    ),

    # Mongoose: new Schema({...})
    Pattern(
        name="mongoose_schema",
        regex=re.compile(r'new\s+(?:mongoose\.)?Schema\s*\('),
        node_type=NodeType.DATA_MODEL,
        language="javascript",
        metadata_extractor=lambda m, l: {MetadataKey.ORM: 'mongoose'},
        tags=[],
        description_template="Mongoose schema definition",
    ),

    # SQLAlchemy: class Model(Base):, class Model(db.Model):
    Pattern(
        name="sqlalchemy_model",
        regex=re.compile(r'class\s+(?P<name>\w+)\s*\(\s*(?:.*\.)?(Base|Model)\s*\)'),
        node_type=NodeType.DATA_MODEL,
        language="python",
        metadata_extractor=extract_sqlalchemy_model_metadata,
        tags=[],
        description_template="SQLAlchemy model: {name}",
    ),

    # Prisma: model ModelName {
    Pattern(
        name="prisma_model",
        regex=re.compile(r'model\s+(?P<name>\w+)\s*\{'),
        node_type=NodeType.DATA_MODEL,
        language="prisma",
        metadata_extractor=extract_prisma_model_metadata,
        tags=[],
        description_template="Prisma model: {name}",
    ),

    # Django: class Model(models.Model):
    Pattern(
        name="django_model",
        regex=re.compile(r'class\s+(?P<name>\w+)\s*\(\s*models\.Model\s*\)'),
        node_type=NodeType.DATA_MODEL,
        language="python",
        metadata_extractor=extract_django_model_metadata,
        tags=[],
        description_template="Django model: {name}",
    ),

    # -------------------------------------------------------------------------
    # EVENTS
    # -------------------------------------------------------------------------

    # Event emit: emit('EVENT_NAME', ...), eventEmitter.emit('EVENT_NAME')
    Pattern(
        name="event_emit",
        regex=re.compile(r'(?:\.emit|emit)\s*\(\s*[\'"](?P<event>[^\'"]+)[\'"]'),
        node_type=NodeType.EVENT,
        language=None,
        metadata_extractor=extract_event_emit_metadata,
        tags=[],
        description_template="Event: {event}",
    ),

    # Event listener: on('EVENT_NAME', ...), addEventListener('EVENT_NAME')
    Pattern(
        name="event_listener",
        regex=re.compile(r'(?:\.on|on|addEventListener)\s*\(\s*[\'"](?P<event>[^\'"]+)[\'"]'),
        node_type=NodeType.EVENT,
        language=None,
        metadata_extractor=extract_event_listener_metadata,
        tags=[],
        description_template="Event: {event}",
    ),

    # EventPattern decorator: @EventPattern('EVENT_NAME')
    Pattern(
        name="event_pattern_decorator",
        regex=re.compile(r'@EventPattern\s*\(\s*[\'"](?P<event>[^\'"]+)[\'"]'),
        node_type=NodeType.EVENT,
        language="javascript",
        metadata_extractor=extract_event_listener_metadata,
        tags=[],
        description_template="Event pattern: {event}",
    ),

    # -------------------------------------------------------------------------
    # MIDDLEWARE
    # -------------------------------------------------------------------------

    # Express middleware: app.use(...), router.use(...)
    Pattern(
        name="express_middleware",
        regex=re.compile(r'(?:app|router)\.use\s*\('),
        node_type=NodeType.MIDDLEWARE,
        language="javascript",
        metadata_extractor=extract_middleware_metadata,
        tags=[],
        description_template="Express middleware",
    ),

    # NestJS guards: @UseGuards(...)
    Pattern(
        name="nestjs_guard",
        regex=re.compile(r'@UseGuards\s*\('),
        node_type=NodeType.MIDDLEWARE,
        language="javascript",
        metadata_extractor=lambda m, l: {MetadataKey.FRAMEWORK: 'nestjs'},
        tags=[WellKnownTag.AUTH_REQUIRED],
        description_template="NestJS guard middleware",
    ),

    # NestJS interceptors: @UseInterceptors(...)
    Pattern(
        name="nestjs_interceptor",
        regex=re.compile(r'@UseInterceptors\s*\('),
        node_type=NodeType.MIDDLEWARE,
        language="javascript",
        metadata_extractor=lambda m, l: {MetadataKey.FRAMEWORK: 'nestjs'},
        tags=[],
        description_template="NestJS interceptor middleware",
    ),

    # Django middleware: @middleware decorator or MIDDLEWARE setting
    Pattern(
        name="django_middleware",
        regex=re.compile(r'@middleware|MIDDLEWARE\s*='),
        node_type=NodeType.MIDDLEWARE,
        language="python",
        metadata_extractor=lambda m, l: {MetadataKey.FRAMEWORK: 'django'},
        tags=[],
        description_template="Django middleware",
    ),

    # -------------------------------------------------------------------------
    # QUEUES & ASYNC
    # -------------------------------------------------------------------------

    # Bull/BullMQ: new Queue('queue-name')
    Pattern(
        name="bull_queue",
        regex=re.compile(r'new\s+(?:Bull\.)?Queue\s*\(\s*[\'"](?P<queue>[^\'"]+)[\'"]'),
        node_type=NodeType.QUEUE,
        language="javascript",
        metadata_extractor=extract_queue_metadata,
        tags=[],
        description_template="Bull queue: {queue}",
    ),

    # Bull processor: @Process('queue-name')
    Pattern(
        name="bull_processor",
        regex=re.compile(r'@Process\s*\(\s*[\'"](?P<queue>[^\'"]+)[\'"]'),
        node_type=NodeType.ASYNC_HANDLER,
        language="javascript",
        metadata_extractor=extract_processor_metadata,
        tags=[],
        description_template="Queue processor: {queue}",
    ),

    # Bull processor decorator: @Processor('queue-name')
    Pattern(
        name="bull_processor_decorator",
        regex=re.compile(r'@Processor\s*\(\s*[\'"](?P<queue>[^\'"]+)[\'"]'),
        node_type=NodeType.ASYNC_HANDLER,
        language="javascript",
        metadata_extractor=extract_processor_metadata,
        tags=[],
        description_template="Queue processor: {queue}",
    ),

    # Celery task: @celery.task, @shared_task
    Pattern(
        name="celery_task",
        regex=re.compile(r'@(?:celery\.task|shared_task|task)\s*(?:\(|$)'),
        node_type=NodeType.ASYNC_HANDLER,
        language="python",
        metadata_extractor=extract_celery_task_metadata,
        tags=[],
        description_template="Celery task",
    ),

    # Cron decorator: @Cron('schedule')
    Pattern(
        name="cron_decorator",
        regex=re.compile(r'@Cron\s*\(\s*[\'"](?P<schedule>[^\'"]+)[\'"]'),
        node_type=NodeType.ASYNC_HANDLER,
        language="javascript",
        metadata_extractor=extract_cron_metadata,
        tags=[],
        description_template="Cron job: {schedule}",
    ),

    # Schedule library: schedule.every(...)
    Pattern(
        name="schedule_every",
        regex=re.compile(r'schedule\.every\s*\('),
        node_type=NodeType.ASYNC_HANDLER,
        language="python",
        metadata_extractor=extract_schedule_metadata,
        tags=[],
        description_template="Scheduled task",
    ),

    # -------------------------------------------------------------------------
    # ENVIRONMENT VARIABLES
    # -------------------------------------------------------------------------

    # Node.js: process.env.VAR_NAME
    Pattern(
        name="nodejs_env_var",
        regex=re.compile(r'process\.env\.(?P<var>\w+)'),
        node_type=NodeType.ENV_VAR,
        language="javascript",
        metadata_extractor=extract_env_var_metadata,
        tags=[],
        description_template="Environment variable: {var}",
    ),

    # Python: os.environ['VAR_NAME'], os.getenv('VAR_NAME')
    Pattern(
        name="python_env_var",
        regex=re.compile(r'os\.(?:environ\[[\'"](?P<var>[^\'"]+)[\'"]\]|getenv\s*\(\s*[\'"](?P<var2>[^\'"]+)[\'"])'),
        node_type=NodeType.ENV_VAR,
        language="python",
        metadata_extractor=lambda m, l: {'var_name': m.group('var') or m.group('var2')},
        tags=[],
        description_template="Environment variable: {var}",
    ),

    # -------------------------------------------------------------------------
    # CACHE
    # -------------------------------------------------------------------------

    # Redis: redis.get('key'), cache.get('key')
    Pattern(
        name="cache_get",
        regex=re.compile(r'(?:redis|cache)\.get\s*\(\s*[\'"](?P<key>[^\'"]+)[\'"]'),
        node_type=NodeType.CACHE_KEY,
        language=None,
        metadata_extractor=extract_cache_key_metadata,
        tags=[],
        description_template="Cache key: {key}",
    ),

    # Redis: redis.set('key'), cache.set('key')
    Pattern(
        name="cache_set",
        regex=re.compile(r'(?:redis|cache)\.set\s*\(\s*[\'"](?P<key>[^\'"]+)[\'"]'),
        node_type=NodeType.CACHE_KEY,
        language=None,
        metadata_extractor=extract_cache_key_metadata,
        tags=[],
        description_template="Cache key: {key}",
    ),

    # @Cacheable decorator
    Pattern(
        name="cacheable_decorator",
        regex=re.compile(r'@Cacheable\s*\('),
        node_type=NodeType.CACHE_KEY,
        language=None,
        metadata_extractor=lambda m, l: {},
        tags=[],
        description_template="Cacheable method",
    ),
]


# =============================================================================
# PATTERN PASS
# =============================================================================

class PatternPass:
    """
    Pass 2 of the pipeline: Regex-based pattern matching.

    Scans source files for architectural patterns that are difficult or
    impossible to extract reliably via tree-sitter AST parsing.

    This pass is FREE (no LLM calls).
    """

    def __init__(self, store: GraphStore):
        """
        Initialize pattern pass.

        Args:
            store: GraphStore instance to add discovered nodes and edges to.
        """
        self.store = store
        self.patterns = PATTERNS

    def process_file(
        self,
        file_path: Path,
        project_id: str,
        language: str,
    ) -> List[GraphNode]:
        """
        Scan a single file for pattern matches.

        Args:
            file_path: Path to the source file
            project_id: Project identifier for node IDs
            language: Programming language of the file

        Returns:
            List of created nodes
        """
        if not file_path.exists():
            return []

        created_nodes = []

        # Read file content
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            return []

        lines = content.split('\n')

        # Get the File node for this file (should exist from Pass 1)
        file_id = f"file:{project_id}:{file_path.as_posix()}"
        file_node = self.store.get_node(file_id)

        # Scan each line with each pattern
        for line_num, line in enumerate(lines, start=1):
            for pattern in self.patterns:
                # Skip if pattern is language-specific and doesn't match
                if pattern.language and pattern.language != language:
                    continue

                # Try to match pattern
                match = pattern.regex.search(line)
                if not match:
                    continue

                # Extract metadata
                metadata = {}
                if pattern.metadata_extractor:
                    try:
                        metadata = pattern.metadata_extractor(match, line)
                    except Exception as e:
                        print(f"Error extracting metadata in {file_path}:{line_num}: {e}")
                        metadata = {}

                # Create node
                node = self._create_node_from_match(
                    pattern=pattern,
                    file_path=file_path,
                    line_num=line_num,
                    line=line,
                    project_id=project_id,
                    language=language,
                    metadata=metadata,
                )

                if node:
                    # Add node to store
                    self.store.add_node(node)
                    created_nodes.append(node)

                    # Create DEFINES edge from File to this node
                    if file_node:
                        edge = GraphEdge(
                            source_id=file_id,
                            target_id=node.id,
                            type=EdgeType.DEFINES,
                            provenance=Provenance.SCANNER,
                            confidence=0.9,  # Pattern matching has high confidence
                            location=SourceLocation(
                                file_path=file_path.as_posix(),
                                start_line=line_num,
                                end_line=line_num,
                            ),
                        )
                        self.store.add_edge(edge)

        return created_nodes

    def process_directory(
        self,
        dir_path: Path,
        project_id: str,
        language: str = None,
    ) -> List[GraphNode]:
        """
        Recursively scan a directory for pattern matches.

        Args:
            dir_path: Directory to scan
            project_id: Project identifier for node IDs
            language: Programming language (if None, auto-detect from extension)

        Returns:
            List of all created nodes
        """
        if not dir_path.exists() or not dir_path.is_dir():
            return []

        all_nodes = []

        # Language extensions map
        lang_extensions = {
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'javascript',
            '.tsx': 'typescript',
            '.py': 'python',
            '.java': 'java',
            '.prisma': 'prisma',
            '.go': 'go',
            '.rb': 'ruby',
            '.php': 'php',
        }

        # Scan all source files
        for file_path in dir_path.rglob('*'):
            if not file_path.is_file():
                continue

            # Skip common non-source directories
            if any(part in file_path.parts for part in ['node_modules', '.git', '__pycache__', 'venv', 'dist', 'build']):
                continue

            # Determine language
            file_lang = language
            if not file_lang:
                ext = file_path.suffix.lower()
                file_lang = lang_extensions.get(ext, 'unknown')

            if file_lang == 'unknown':
                continue

            # Process file
            nodes = self.process_file(file_path, project_id, file_lang)
            all_nodes.extend(nodes)

        return all_nodes

    def _create_node_from_match(
        self,
        pattern: Pattern,
        file_path: Path,
        line_num: int,
        line: str,
        project_id: str,
        language: str,
        metadata: Dict[str, Any],
    ) -> Optional[GraphNode]:
        """
        Create a GraphNode from a pattern match.

        Args:
            pattern: The matched pattern
            file_path: Source file path
            line_num: Line number of match
            line: The matched line content
            project_id: Project identifier
            language: Programming language
            metadata: Extracted metadata

        Returns:
            GraphNode or None if node couldn't be created
        """
        # Generate node ID based on type
        node_id = self._generate_node_id(
            node_type=pattern.node_type,
            project_id=project_id,
            file_path=file_path,
            metadata=metadata,
        )

        # Check if node already exists (avoid duplicates)
        existing = self.store.get_node(node_id)
        if existing:
            return None

        # Generate name
        name = self._generate_node_name(pattern.node_type, metadata, file_path)

        # Generate description
        description = self._generate_description(pattern, metadata)

        # Create source location
        location = SourceLocation(
            file_path=file_path.as_posix(),
            start_line=line_num,
            end_line=line_num,
        )

        # Create source hash
        source_hash = hashlib.sha256(line.encode('utf-8')).hexdigest()[:16]

        # Create node
        node = GraphNode(
            id=node_id,
            type=pattern.node_type,
            name=name,
            description=description,
            location=location,
            provenance=Provenance.SCANNER,
            confidence=0.9,  # Pattern matching is quite reliable
            source_hash=source_hash,
            language=language,
            tags=pattern.tags.copy(),
            metadata=metadata,
        )

        return node

    def _generate_node_id(
        self,
        node_type: NodeType,
        project_id: str,
        file_path: Path,
        metadata: Dict[str, Any],
    ) -> str:
        """
        Generate a unique node ID.

        Format varies by node type:
        - endpoint:{project}:{method}:{path}
        - event:{project}:{event_name}
        - queue:{project}:{queue_name}
        - model:{project}:{table_name}
        - env_var:{project}:{var_name}
        - cache_key:{project}:{key}
        """
        type_lower = node_type.value.lower()

        if node_type == NodeType.ENDPOINT:
            method = metadata.get(MetadataKey.HTTP_METHOD, 'UNKNOWN')
            path = metadata.get(MetadataKey.HTTP_PATH, '/unknown')
            return f"endpoint:{project_id}:{method}:{path}"

        elif node_type == NodeType.EVENT:
            event_name = metadata.get('event_name', 'unknown')
            return f"event:{project_id}:{event_name}"

        elif node_type == NodeType.QUEUE:
            queue_name = metadata.get('queue_name', 'unknown')
            return f"queue:{project_id}:{queue_name}"

        elif node_type == NodeType.DATA_MODEL:
            table_name = metadata.get(MetadataKey.TABLE_NAME, file_path.stem)
            return f"model:{project_id}:{table_name}"

        elif node_type == NodeType.ENV_VAR:
            var_name = metadata.get('var_name', 'unknown')
            return f"env_var:{project_id}:{var_name}"

        elif node_type == NodeType.CACHE_KEY:
            key = metadata.get('cache_key', 'unknown')
            return f"cache_key:{project_id}:{key}"

        elif node_type == NodeType.ASYNC_HANDLER:
            queue_name = metadata.get('queue_name', '')
            schedule = metadata.get(MetadataKey.SCHEDULE, '')
            identifier = queue_name or schedule or file_path.stem
            return f"async_handler:{project_id}:{identifier}"

        elif node_type == NodeType.MIDDLEWARE:
            # Use file and line for middleware since they're often anonymous
            return f"middleware:{project_id}:{file_path.stem}"

        else:
            # Fallback
            return f"{type_lower}:{project_id}:{file_path.stem}"

    def _generate_node_name(
        self,
        node_type: NodeType,
        metadata: Dict[str, Any],
        file_path: Path,
    ) -> str:
        """Generate a human-readable name for the node."""
        if node_type == NodeType.ENDPOINT:
            method = metadata.get(MetadataKey.HTTP_METHOD, 'GET')
            path = metadata.get(MetadataKey.HTTP_PATH, '/')
            return f"{method} {path}"

        elif node_type == NodeType.EVENT:
            return metadata.get('event_name', 'Unknown Event')

        elif node_type == NodeType.QUEUE:
            return metadata.get('queue_name', 'Unknown Queue')

        elif node_type == NodeType.DATA_MODEL:
            return metadata.get(MetadataKey.TABLE_NAME, file_path.stem)

        elif node_type == NodeType.ENV_VAR:
            return metadata.get('var_name', 'Unknown Variable')

        elif node_type == NodeType.CACHE_KEY:
            return metadata.get('cache_key', 'Unknown Cache Key')

        elif node_type == NodeType.ASYNC_HANDLER:
            queue = metadata.get('queue_name', '')
            schedule = metadata.get(MetadataKey.SCHEDULE, '')
            if queue:
                return f"Queue Handler: {queue}"
            elif schedule:
                return f"Cron: {schedule}"
            else:
                return f"Async Handler"

        elif node_type == NodeType.MIDDLEWARE:
            framework = metadata.get(MetadataKey.FRAMEWORK, '')
            if framework:
                return f"{framework.capitalize()} Middleware"
            return "Middleware"

        else:
            return f"{node_type.value}"

    def _generate_description(
        self,
        pattern: Pattern,
        metadata: Dict[str, Any],
    ) -> str:
        """Generate a description from pattern template and metadata."""
        if not pattern.description_template:
            return ""

        try:
            # Try to format with metadata
            return pattern.description_template.format(**metadata)
        except (KeyError, ValueError):
            # If formatting fails, return template as-is
            return pattern.description_template
