"""
Workspace Intelligence Layer - Pass 2b: Behavioral Connection Extraction
=========================================================================

FREE pass (no LLM calls). Runs AFTER Pass 2 (patterns).

Analyzes source code to discover OPERATIONAL edges between existing graph nodes:
  - CALLS: Function -> Function (direct invocation)
  - READS_DB: Function/File -> DataModel (database reads)
  - WRITES_DB: Function/File -> DataModel (database writes)
  - EMITS_EVENT: Function/File -> Event (event emission)
  - CONSUMES_EVENT: Function/File -> Event (event listening)
  - ENQUEUES: Function/File -> Queue (job enqueue)
  - DEQUEUES: AsyncHandler/File -> Queue (job processing)
  - CACHE_READ: Function/File -> CacheKey (cache gets)
  - CACHE_WRITE: Function/File -> CacheKey (cache sets)
  - CALLS_API: Function/File -> ExternalAPI (external API calls)

Design:
  - Phase 1 (Gather): Read each source file, extract behavioral signals
  - Phase 2 (Resolve): Match signals against graph nodes to create edges
  - Creates missing target nodes (Event, Queue, ExternalAPI, CacheKey)
    if they don't already exist
"""

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Set, Tuple, Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ontology import (
    GraphNode, GraphEdge, NodeType, EdgeType, Provenance, SourceLocation,
)
from graph_store import GraphStore

logger = logging.getLogger("workspace-intelligence")


# =========================================================================
# DATA STRUCTURES
# =========================================================================

@dataclass
class Signal:
    """A behavioral pattern detected in source code."""
    kind: str           # db_read, db_write, event_emit, event_listen, enqueue,
                        # dequeue, cache_read, cache_write, api_call, fn_call
    file_path: str      # File path (posix)
    line: int           # Line number
    target: str         # Target name (model name, event name, function name, etc.)
    extra: Dict[str, Any] = field(default_factory=dict)


# =========================================================================
# REGEX PATTERNS (pre-compiled)
# =========================================================================

# --- DB operations ---
_DB_READ_RE = re.compile(
    r'(?P<model>[A-Z][a-zA-Z0-9]+)\.'
    r'(?:find|findOne|findById|findByIdAndUpdate|aggregate|countDocuments|count|distinct|'
    r'get|all|filter|select|query|where|first|last|objects)\s*\('
)
_DB_WRITE_RE = re.compile(
    r'(?P<model>[A-Z][a-zA-Z0-9]+)\.'
    r'(?:create|insertMany|insertOne|save|updateOne|updateMany|deleteOne|deleteMany|'
    r'findOneAndUpdate|findByIdAndUpdate|findOneAndDelete|findByIdAndDelete|'
    r'findOneAndRemove|remove|bulkWrite|replaceOne)\s*\('
)
_DB_NEW_RE = re.compile(r'new\s+(?P<model>[A-Z][a-zA-Z0-9]+)\s*\(')
_DB_SAVE_RE = re.compile(r'(?:await\s+)?(?P<var>\w+)\.save\s*\(')

# --- Events ---
_EVENT_EMIT_RE = re.compile(
    r'(?:emitEvent|\.emit|eventBus\.emit)\s*\(\s*[\'"](?P<event>[A-Z][A-Z_0-9]+)[\'"]'
)
_EVENT_LISTEN_RE = re.compile(
    r'(?:onEvent|\.on|eventBus\.on|addEventListener)\s*\(\s*[\'"](?P<event>[A-Z][A-Z_0-9]+)[\'"]'
)

# --- Queues ---
_ENQUEUE_RE = re.compile(
    r'(?:enqueueJob|\.add|queue\.add)\s*\(\s*[\'"](?P<job>[a-zA-Z0-9_\-]+)[\'"]'
)
_DEQUEUE_RE = re.compile(
    r'new\s+Worker\s*\(\s*[\'"](?P<queue>[a-zA-Z0-9_\-]+)[\'"]'
)

# --- Cache ---
_CACHE_READ_RE = re.compile(
    r'(?:getCache|redis\.get|cache\.get|client\.get|redisClient\.get)\s*\('
)
_CACHE_WRITE_RE = re.compile(
    r'(?:setCache|redis\.set|cache\.set|client\.set|redisClient\.set|'
    r'redis\.del|cache\.del|client\.del|deleteCache)\s*\('
)

# --- External APIs ---
_STRIPE_RE = re.compile(r'stripe\.(?P<resource>\w+)\.(?P<method>\w+)\s*\(')
_FETCH_RE = re.compile(r'(?:fetch|axios\.(?:get|post|put|delete|patch)|httpx?\.\w+)\s*\(')

# --- Function calls ---
_FN_CALL_RE = re.compile(r'(?:await\s+)?(?P<fn>[a-zA-Z_]\w+)\s*\(')

# --- Variable-to-model tracking ---
_VAR_CTOR_RE = re.compile(
    r'(?:const|let|var)\s+(?P<var>\w+)\s*=\s*(?:await\s+)?new\s+(?P<model>[A-Z][a-zA-Z0-9]+)\s*\('
)

# Skip these names when detecting function calls
_SKIP_FN = {
    'require', 'import', 'export', 'module', 'console', 'log', 'warn', 'error',
    'info', 'debug', 'trace', 'assert',
    'if', 'for', 'while', 'switch', 'catch', 'return', 'throw', 'typeof',
    'delete', 'void', 'new', 'class', 'function', 'async', 'await',
    'parseInt', 'parseFloat', 'JSON', 'String', 'Number', 'Array', 'Object',
    'Boolean', 'Date', 'Math', 'Error', 'Promise', 'Map', 'Set', 'RegExp',
    'Symbol', 'Buffer', 'Uint8Array', 'Int32Array',
    'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
    'res', 'req', 'next', 'err', 'self', 'this', 'super',
    'describe', 'it', 'test', 'expect', 'beforeEach', 'afterEach',
    'push', 'pop', 'map', 'filter', 'reduce', 'forEach', 'find', 'some',
    'every', 'includes', 'indexOf', 'slice', 'splice', 'concat', 'join',
    'split', 'trim', 'replace', 'match', 'search', 'toString', 'valueOf',
    'keys', 'values', 'entries', 'assign', 'freeze', 'parse', 'stringify',
    'then', 'catch', 'finally', 'resolve', 'reject', 'all', 'race',
    'send', 'status', 'json', 'render', 'redirect',
    'use', 'get', 'post', 'put', 'delete', 'patch',
    'listen', 'close', 'connect', 'disconnect', 'on', 'emit', 'once',
    'print', 'len', 'range', 'enumerate', 'zip', 'isinstance', 'type',
    'str', 'int', 'float', 'list', 'dict', 'set', 'tuple', 'bool',
    'hasattr', 'getattr', 'setattr', 'property', 'staticmethod',
    'classmethod', 'abstractmethod', 'dataclass',
    'round', 'abs', 'min', 'max', 'sum', 'sorted', 'reversed',
}

# Skip these as model names (they look like models but aren't)
_SKIP_MODELS = {
    'Error', 'TypeError', 'RangeError', 'SyntaxError', 'ReferenceError',
    'Promise', 'Map', 'Set', 'Date', 'RegExp', 'Buffer', 'URL',
    'EventEmitter', 'Schema', 'Router', 'Queue', 'Worker',
    'Request', 'Response', 'Stream', 'Transform',
    'ObjectId', 'Types',
}


# =========================================================================
# CONNECTION PASS
# =========================================================================

class ConnectionPass:
    """
    Pass 2b: Behavioral connection extraction (FREE, no LLM).

    Reads source files and discovers operational edges between nodes
    using regex heuristics.
    """

    def __init__(self, store: GraphStore):
        self.store = store
        # Indices built from existing graph
        self._fn_index: Dict[str, List[GraphNode]] = {}
        self._model_index: Dict[str, GraphNode] = {}
        self._event_index: Dict[str, GraphNode] = {}
        self._queue_index: Dict[str, GraphNode] = {}
        self._cache_index: Dict[str, GraphNode] = {}
        self._api_index: Dict[str, GraphNode] = {}
        # File → scopes (function line ranges)
        self._scopes: Dict[str, List[Tuple[int, int, str]]] = {}  # file → [(start, end, node_id)]
        # Dedup set
        self._seen_edges: Set[str] = set()
        # Counters
        self.signals_found = 0
        self.edges_created = 0
        self.nodes_created = 0

    # -----------------------------------------------------------------
    # INDEX BUILDING
    # -----------------------------------------------------------------

    def _build_indices(self):
        """Build lookup indices from the existing graph (after passes 0-2)."""
        for node in self.store.get_all_nodes():
            nt = node.type

            if nt in (NodeType.FUNCTION, NodeType.ASYNC_HANDLER):
                self._fn_index.setdefault(node.name, []).append(node)
                if node.location:
                    fp = node.location.file_path
                    self._scopes.setdefault(fp, []).append(
                        (node.location.start_line, node.location.end_line, node.id)
                    )

            elif nt == NodeType.ENDPOINT:
                if node.location:
                    fp = node.location.file_path
                    self._scopes.setdefault(fp, []).append(
                        (node.location.start_line, node.location.start_line + 50, node.id)
                    )

            elif nt == NodeType.DATA_MODEL:
                self._model_index[node.name] = node
                table = node.metadata.get('table_name', '')
                if table:
                    self._model_index[table] = node

            elif nt == NodeType.COLLECTION:
                self._model_index[node.name] = node

            elif nt == NodeType.EVENT:
                self._event_index[node.name] = node

            elif nt == NodeType.QUEUE:
                self._queue_index[node.name] = node

            elif nt == NodeType.CACHE_KEY:
                self._cache_index[node.name] = node

            elif nt == NodeType.EXTERNAL_API:
                self._api_index[node.name] = node

        # Sort scopes by start line for lookup
        for fp in self._scopes:
            self._scopes[fp].sort()

    # -----------------------------------------------------------------
    # SCOPE RESOLUTION
    # -----------------------------------------------------------------

    def _find_source(self, file_path: str, line: int) -> Optional[str]:
        """Find the enclosing function/endpoint node, or fall back to file node."""
        scopes = self._scopes.get(file_path, [])
        best = None
        best_span = 999999
        for start, end, nid in scopes:
            if start <= line <= end:
                span = end - start
                if span < best_span:
                    best = nid
                    best_span = span
        if best:
            return best

        # Fall back to File node
        for node in self.store.get_all_nodes():
            if node.type == NodeType.FILE and node.location:
                if node.location.file_path == file_path:
                    return node.id
        return None

    # -----------------------------------------------------------------
    # TARGET RESOLUTION (find or create)
    # -----------------------------------------------------------------

    def _resolve_model(self, name: str) -> Optional[str]:
        """Find a DataModel/Collection by name (case-insensitive)."""
        if name in self._model_index:
            return self._model_index[name].id
        lo = name.lower()
        for k, v in self._model_index.items():
            if k.lower() == lo:
                return v.id
        return None

    def _resolve_or_create_event(self, name: str, project_id: str, sig: Signal) -> str:
        if name in self._event_index:
            return self._event_index[name].id
        nid = f"event:{project_id}:{name}"
        node = GraphNode(
            id=nid, type=NodeType.EVENT, name=name,
            description=f"Domain event: {name}",
            location=SourceLocation(file_path=sig.file_path, start_line=sig.line, end_line=sig.line),
            provenance=Provenance.SCANNER, confidence=0.85,
            metadata={"event_name": name, "discovered_by": "pass2b"},
        )
        self.store.add_node(node)
        self._event_index[name] = node
        self.nodes_created += 1
        return nid

    def _resolve_or_create_queue(self, name: str, project_id: str, sig: Signal) -> str:
        if name in self._queue_index:
            return self._queue_index[name].id
        nid = f"queue:{project_id}:{name}"
        node = GraphNode(
            id=nid, type=NodeType.QUEUE, name=name,
            description=f"Job queue: {name}",
            location=SourceLocation(file_path=sig.file_path, start_line=sig.line, end_line=sig.line),
            provenance=Provenance.SCANNER, confidence=0.85,
            metadata={"queue_name": name, "discovered_by": "pass2b"},
        )
        self.store.add_node(node)
        self._queue_index[name] = node
        self.nodes_created += 1
        return nid

    def _resolve_or_create_cache(self, name: str, project_id: str, sig: Signal) -> str:
        if name in self._cache_index:
            return self._cache_index[name].id
        nid = f"cache_key:{project_id}:{name}"
        node = GraphNode(
            id=nid, type=NodeType.CACHE_KEY, name=name,
            description=f"Cache key: {name}",
            location=SourceLocation(file_path=sig.file_path, start_line=sig.line, end_line=sig.line),
            provenance=Provenance.SCANNER, confidence=0.8,
            metadata={"cache_key": name, "discovered_by": "pass2b"},
        )
        self.store.add_node(node)
        self._cache_index[name] = node
        self.nodes_created += 1
        return nid

    def _resolve_or_create_api(self, name: str, project_id: str, sig: Signal) -> str:
        if name in self._api_index:
            return self._api_index[name].id
        display = name.capitalize()
        nid = f"externalapi:{project_id}:{name}"
        node = GraphNode(
            id=nid, type=NodeType.EXTERNAL_API, name=display,
            description=f"External API: {display}",
            location=SourceLocation(file_path=sig.file_path, start_line=sig.line, end_line=sig.line),
            provenance=Provenance.SCANNER, confidence=0.8,
            metadata={"api_name": name, "discovered_by": "pass2b"},
        )
        self.store.add_node(node)
        self._api_index[name] = node
        self.nodes_created += 1
        return nid

    def _resolve_function(self, name: str, source_id: str) -> Optional[str]:
        """Find a function by name, preferring cross-file matches."""
        candidates = self._fn_index.get(name)
        if not candidates:
            return None
        if len(candidates) == 1:
            cid = candidates[0].id
            return cid if cid != source_id else None

        # Prefer cross-file
        src_node = self.store.get_node(source_id)
        src_file = src_node.location.file_path if src_node and src_node.location else ""
        for c in candidates:
            if c.location and c.location.file_path != src_file:
                return c.id
        return candidates[0].id if candidates[0].id != source_id else None

    # -----------------------------------------------------------------
    # EDGE CREATION
    # -----------------------------------------------------------------

    def _add_edge(self, src: str, tgt: str, etype: EdgeType,
                  desc: str, conf: float, fp: str, ln: int):
        """Add an edge, deduplicating by src+tgt+type."""
        key = f"{src}|{tgt}|{etype.value}"
        if key in self._seen_edges:
            return
        self._seen_edges.add(key)

        edge = GraphEdge(
            source_id=src, target_id=tgt, type=etype,
            description=desc, provenance=Provenance.SCANNER,
            confidence=conf,
            location=SourceLocation(file_path=fp, start_line=ln, end_line=ln),
            metadata={"discovered_by": "pass2b"},
        )
        self.store.add_edge(edge, validate=False)
        self.edges_created += 1

    # -----------------------------------------------------------------
    # FILE SCANNING (Phase 1 + Phase 2 combined per file)
    # -----------------------------------------------------------------

    def _scan_file(self, file_path: Path, project_id: str, language: str):
        """Scan a file for behavioral patterns and create edges immediately."""
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception:
            return

        fp = file_path.as_posix()
        lines = content.split('\n')

        # Build model import set for this file
        known_models = self._detect_file_models(content)

        # Track var→model for .save() resolution
        var_model: Dict[str, str] = {}

        for ln, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith('//') or stripped.startswith('#'):
                continue

            # --- Variable → Model tracking ---
            m = _VAR_CTOR_RE.search(line)
            if m:
                model = m.group('model')
                if model in known_models:
                    var_model[m.group('var')] = model

            # --- DB Reads ---
            m = _DB_READ_RE.search(line)
            if m:
                model = m.group('model')
                if model in known_models:
                    src = self._find_source(fp, ln)
                    tgt = self._resolve_model(model)
                    if src and tgt:
                        self._add_edge(src, tgt, EdgeType.READS_DB,
                                       f"Reads {model}", 0.85, fp, ln)
                        self.signals_found += 1

            # --- DB Writes ---
            m = _DB_WRITE_RE.search(line)
            if m:
                model = m.group('model')
                if model in known_models:
                    src = self._find_source(fp, ln)
                    tgt = self._resolve_model(model)
                    if src and tgt:
                        self._add_edge(src, tgt, EdgeType.WRITES_DB,
                                       f"Writes {model}", 0.85, fp, ln)
                        self.signals_found += 1

            # --- new Model() → WRITES_DB ---
            m = _DB_NEW_RE.search(line)
            if m:
                model = m.group('model')
                if model in known_models and model not in _SKIP_MODELS:
                    src = self._find_source(fp, ln)
                    tgt = self._resolve_model(model)
                    if src and tgt:
                        self._add_edge(src, tgt, EdgeType.WRITES_DB,
                                       f"Creates {model}", 0.85, fp, ln)
                        self.signals_found += 1

            # --- .save() → WRITES_DB ---
            m = _DB_SAVE_RE.search(line)
            if m:
                var = m.group('var')
                if var in var_model:
                    model = var_model[var]
                    src = self._find_source(fp, ln)
                    tgt = self._resolve_model(model)
                    if src and tgt:
                        self._add_edge(src, tgt, EdgeType.WRITES_DB,
                                       f"Saves {model}", 0.85, fp, ln)
                        self.signals_found += 1

            # --- Event Emit ---
            m = _EVENT_EMIT_RE.search(line)
            if m:
                evt = m.group('event')
                src = self._find_source(fp, ln)
                if src:
                    tgt = self._resolve_or_create_event(evt, project_id,
                              Signal('event_emit', fp, ln, evt))
                    self._add_edge(src, tgt, EdgeType.EMITS_EVENT,
                                   f"Emits {evt}", 0.9, fp, ln)
                    self.signals_found += 1

            # --- Event Listen ---
            m = _EVENT_LISTEN_RE.search(line)
            if m:
                evt = m.group('event')
                src = self._find_source(fp, ln)
                if src:
                    tgt = self._resolve_or_create_event(evt, project_id,
                              Signal('event_listen', fp, ln, evt))
                    self._add_edge(src, tgt, EdgeType.CONSUMES_EVENT,
                                   f"Listens to {evt}", 0.9, fp, ln)
                    self.signals_found += 1

            # --- Enqueue ---
            m = _ENQUEUE_RE.search(line)
            if m:
                job = m.group('job')
                src = self._find_source(fp, ln)
                if src:
                    tgt = self._resolve_or_create_queue(job, project_id,
                              Signal('enqueue', fp, ln, job))
                    self._add_edge(src, tgt, EdgeType.ENQUEUES,
                                   f"Enqueues {job}", 0.9, fp, ln)
                    self.signals_found += 1

            # --- Dequeue (Worker) ---
            m = _DEQUEUE_RE.search(line)
            if m:
                queue = m.group('queue')
                src = self._find_source(fp, ln)
                if src:
                    tgt = self._resolve_or_create_queue(queue, project_id,
                              Signal('dequeue', fp, ln, queue))
                    self._add_edge(src, tgt, EdgeType.DEQUEUES,
                                   f"Processes {queue}", 0.9, fp, ln)
                    self.signals_found += 1

            # --- Cache Read ---
            m = _CACHE_READ_RE.search(line)
            if m:
                src = self._find_source(fp, ln)
                if src:
                    key = self._extract_cache_key(line)
                    tgt = self._resolve_or_create_cache(key, project_id,
                              Signal('cache_read', fp, ln, key))
                    self._add_edge(src, tgt, EdgeType.CACHE_READ,
                                   f"Cache read: {key}", 0.8, fp, ln)
                    self.signals_found += 1

            # --- Cache Write ---
            m = _CACHE_WRITE_RE.search(line)
            if m:
                src = self._find_source(fp, ln)
                if src:
                    key = self._extract_cache_key(line)
                    tgt = self._resolve_or_create_cache(key, project_id,
                              Signal('cache_write', fp, ln, key))
                    self._add_edge(src, tgt, EdgeType.CACHE_WRITE,
                                   f"Cache write: {key}", 0.8, fp, ln)
                    self.signals_found += 1

            # --- Stripe API ---
            m = _STRIPE_RE.search(line)
            if m:
                src = self._find_source(fp, ln)
                if src:
                    tgt = self._resolve_or_create_api('stripe', project_id,
                              Signal('api_call', fp, ln, 'stripe'))
                    resource = m.group('resource')
                    method = m.group('method')
                    self._add_edge(src, tgt, EdgeType.CALLS_API,
                                   f"Stripe {resource}.{method}", 0.9, fp, ln)
                    self.signals_found += 1

            # --- fetch/axios ---
            elif _FETCH_RE.search(line):
                src = self._find_source(fp, ln)
                if src:
                    tgt = self._resolve_or_create_api('http', project_id,
                              Signal('api_call', fp, ln, 'http'))
                    self._add_edge(src, tgt, EdgeType.CALLS_API,
                                   "HTTP request", 0.7, fp, ln)
                    self.signals_found += 1

            # --- Function calls (cross-file) ---
            for m in _FN_CALL_RE.finditer(line):
                fn = m.group('fn')
                if fn in _SKIP_FN or fn in _SKIP_MODELS:
                    continue
                if fn in known_models:
                    continue  # Already handled as DB operation
                if fn not in self._fn_index:
                    continue  # Unknown function

                src = self._find_source(fp, ln)
                if not src:
                    continue
                tgt = self._resolve_function(fn, src)
                if tgt:
                    self._add_edge(src, tgt, EdgeType.CALLS,
                                   f"Calls {fn}()", 0.75, fp, ln)
                    self.signals_found += 1

    # -----------------------------------------------------------------
    # HELPERS
    # -----------------------------------------------------------------

    def _detect_file_models(self, content: str) -> Set[str]:
        """Detect which model names are available in this file."""
        models = set()
        # require('../models/ModelName')
        for m in re.finditer(r"require\s*\(['\"]([^'\"]*(?:models?|schema)[/\\](\w+))['\"]", content):
            models.add(m.group(2))
        # import X from '../models/X'
        for m in re.finditer(r"import\s+(\w+)\s+from\s+['\"]([^'\"]*(?:models?|schema)[/\\]\w+)['\"]", content):
            models.add(m.group(1))
        # const { X } = require('../models/X')
        for m in re.finditer(r"(?:const|let|var)\s+(\w+)\s*=\s*require\s*\(['\"]([^'\"]*(?:models?|schema)[/\\](\w+))['\"]", content):
            models.add(m.group(1))
        # Also match any names that exist in our model index
        for name in self._model_index:
            if name in content:
                models.add(name)
        return models

    def _extract_cache_key(self, line: str) -> str:
        """Extract a cache key pattern from a line."""
        # Try to find a string argument
        m = re.search(r"['\"]([^'\"]+)['\"]", line)
        if m:
            key = m.group(1)
            # Normalize template expressions
            key = re.sub(r'\$\{[^}]+\}', '{id}', key)
            return key
        return "dynamic"

    # -----------------------------------------------------------------
    # PUBLIC API
    # -----------------------------------------------------------------

    def process_all(self, source_files: List[Tuple[Path, str, str]]) -> Dict[str, int]:
        """
        Main entry point: scan all files and create behavioral edges.

        Args:
            source_files: List of (file_path, project_id, language) tuples.

        Returns:
            Summary dict with counts.
        """
        self._build_indices()

        for file_path, project_id, language in source_files:
            self._scan_file(file_path, project_id, language)

        return {
            "signals": self.signals_found,
            "edges_created": self.edges_created,
            "nodes_created": self.nodes_created,
        }
