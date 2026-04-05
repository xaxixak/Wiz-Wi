"""WI Viewer Server - Fixed and Simplified"""
import http.server
import json
import time
import webbrowser
import threading
import subprocess
import sys
import queue
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VIEWER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = VIEWER_DIR.parent
GRAPHS_DIR = PROJECT_ROOT / "graphs"
GRAPHS_DIR.mkdir(exist_ok=True)

# SSE (Server-Sent Events) for live updates
_sse_clients = []
_sse_lock = threading.Lock()
_watcher_instances = {}  # Track running watcher instances by graph_path

# Folder picker state (async pattern)
_picker_lock = threading.Lock()
_picker_result = None  # None = idle, "waiting" = dialog open, dict = result ready

# Running scan process (for abort)
_scan_process = None
_scan_lock = threading.Lock()

# Import watcher and intelligence
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(VIEWER_DIR))  # so `from adapters import` works
from incremental.watcher import GraphWatcher
from intelligence import GraphIntelligence
from adapters import list_all_sources, load_graph as adapter_load_graph

# Intelligence cache: {graph_path: (mtime, metrics)}
_intel_cache: dict = {}
_intel_cache_lock = threading.Lock()

def broadcast_sse(event_type, data):
    """Broadcast an event to all SSE clients."""
    message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with _sse_lock:
        dead_clients = []
        for client in _sse_clients:
            try:
                client.put(message)
            except Exception:
                dead_clients.append(client)
        for dc in dead_clients:
            _sse_clients.remove(dc)

def find_graphs():
    """Find all graph JSON files"""
    return [
        {"name": f.stem, "path": str(f), "size": f.stat().st_size}
        for f in sorted(GRAPHS_DIR.glob("*.json"))
    ]

class ViewerHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Single page: both / and /view serve the graph viewer
        if path == "/" or path == "/view":
            self.path = "/index.html"
            return super().do_GET()

        # API: List graphs
        elif path == "/api/graphs":
            graphs = find_graphs()
            body = json.dumps(graphs).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        # API: Load specific graph (enriched via adapter)
        elif path == "/api/graph":
            graph_path = params.get("path", [None])[0]
            if not graph_path:
                graphs = find_graphs()
                graph_path = graphs[0]["path"] if graphs else None

            if not graph_path or not Path(graph_path).is_file():
                self.send_error(404, "Graph not found")
                return

            # Route through WI adapter for enrichment (tags, categories, timestamps)
            try:
                graph_stem = Path(graph_path).stem
                graph = adapter_load_graph(f"wi:{graph_stem}")
                body = json.dumps(graph.to_dict()).encode("utf-8")
            except Exception:
                # Fallback to raw file read
                with open(graph_path, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # API: Intelligence metrics
        elif path == "/api/intelligence":
            graph_path = params.get("path", [None])[0]
            if not graph_path:
                graphs = find_graphs()
                graph_path = graphs[0]["path"] if graphs else None

            if not graph_path or not Path(graph_path).is_file():
                self.send_error(404, "Graph not found")
                return

            # Cache by path + mtime
            try:
                mtime = Path(graph_path).stat().st_mtime
                with _intel_cache_lock:
                    cached = _intel_cache.get(graph_path)
                    if cached and cached[0] == mtime:
                        metrics = cached[1]
                    else:
                        gi = GraphIntelligence(graph_path)
                        metrics = gi.all_metrics()
                        _intel_cache[graph_path] = (mtime, metrics)
            except Exception as e:
                result = {"error": str(e)}
                body = json.dumps(result).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return

            body = json.dumps(metrics).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        # API: SSE for live updates
        elif path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            client_queue = queue.Queue()
            with _sse_lock:
                _sse_clients.append(client_queue)

            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            try:
                while True:
                    try:
                        msg = client_queue.get(timeout=1)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        # Send heartbeat every 1 second to keep connection alive
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _sse_lock:
                    if client_queue in _sse_clients:
                        _sse_clients.remove(client_queue)

        # API: Start async Windows folder picker
        elif path == "/api/pick-folder":
            global _picker_result
            with _picker_lock:
                _picker_result = "waiting"

            def _run_picker():
                global _picker_result
                try:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes('-topmost', True)
                    folder_path = filedialog.askdirectory(title="Select Folder to Scan")
                    root.destroy()
                    with _picker_lock:
                        if folder_path:
                            _picker_result = {"success": True, "path": folder_path}
                        else:
                            _picker_result = {"cancelled": True}
                except Exception as e:
                    with _picker_lock:
                        _picker_result = {"error": str(e)}

            threading.Thread(target=_run_picker, daemon=True).start()

            result = {"status": "started"}
            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        # API: Poll for folder picker result
        elif path == "/api/pick-folder-result":
            with _picker_lock:
                if _picker_result == "waiting":
                    result = {"status": "waiting"}
                elif _picker_result is None:
                    result = {"error": "No picker started"}
                else:
                    result = _picker_result
                    _picker_result = None  # Reset after reading

            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        # API: List all graph sources (universal adapter)
        elif path == "/api/graph-sources":
            try:
                sources = [s.to_dict() for s in list_all_sources()]
            except Exception as e:
                sources = []
                print(f"[WARN] graph-sources error: {e}")
            body = json.dumps(sources).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        # API: Load graph from universal adapter
        elif path == "/api/graph-source":
            source_id = params.get("id", [None])[0]
            if not source_id:
                self.send_error(400, "Missing ?id= parameter")
                return
            try:
                graph = adapter_load_graph(source_id)
                body = json.dumps(graph.to_dict()).encode("utf-8")
                self.send_response(200)
            except ValueError as e:
                body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(404)
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        # Favicon
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()

        # Static files
        else:
            return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/scan":
            global _scan_process
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(body) if body else {}
            folder = data.get("folder", "")
            passes = data.get("passes", ["scan", "treesitter", "patterns", "connections"])
            watch_enabled = data.get("watchEnabled", False)
            debounce_ms = data.get("debounceMs", 800)

            if not folder or not Path(folder).is_dir():
                result = {"error": "Invalid folder path"}
            else:
                try:
                    graph_name = Path(folder).name.lower() + "_graph.json"
                    graph_path = GRAPHS_DIR / graph_name

                    cmd = [
                        sys.executable,
                        str(PROJECT_ROOT / "pipeline" / "orchestrator.py"),
                        str(folder),
                        "-o", str(graph_path),
                        "--passes",
                    ] + passes

                    t0 = time.time()
                    with _scan_lock:
                        _scan_process = subprocess.Popen(
                            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        )
                    proc = _scan_process
                    stdout, stderr = proc.communicate(timeout=300)
                    duration_ms = (time.time() - t0) * 1000
                    with _scan_lock:
                        _scan_process = None

                    if proc.returncode == 0:
                        result = {
                            "success": True,
                            "graph_path": str(graph_path),
                            "duration_ms": round(duration_ms),
                            "output": stdout[:2000] if stdout else "",
                        }
                        # Auto-start watcher if watch mode enabled
                        if watch_enabled:
                            try:
                                gp_str = str(graph_path)
                                if gp_str in _watcher_instances:
                                    _watcher_instances[gp_str].stop()
                                    del _watcher_instances[gp_str]

                                def on_graph_update(event):
                                    update_data = {
                                        "type": "graph-update",
                                        "changed_files": event.changed_files,
                                        "nodes_affected": event.nodes_affected,
                                        "nodes_added": event.nodes_added,
                                        "nodes_removed": event.nodes_removed,
                                        "nodes_stale": getattr(event, 'nodes_stale', 0),
                                        "duration_ms": event.duration_ms,
                                        "graph_path": event.graph_path,
                                    }
                                    broadcast_sse("graph-update", update_data)

                                watcher = GraphWatcher(
                                    workspace_path=Path(folder),
                                    graph_path=graph_path,
                                    on_update=on_graph_update,
                                    debounce_ms=debounce_ms,
                                )
                                threading.Thread(target=watcher.start, daemon=True).start()
                                _watcher_instances[gp_str] = watcher
                                result["watcher_started"] = True
                                print(f"[LIVE] Auto-started watcher for {folder}", flush=True)
                            except Exception as e:
                                result["watcher_error"] = str(e)
                    else:
                        result = {"error": f"Scan failed: {stderr}"}
                except subprocess.TimeoutExpired:
                    with _scan_lock:
                        if _scan_process:
                            _scan_process.kill()
                            _scan_process = None
                    result = {"error": "Scan timed out (300s)"}
                except Exception as e:
                    with _scan_lock:
                        _scan_process = None
                    result = {"error": str(e)}

            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/scan-abort":
            with _scan_lock:
                if _scan_process and _scan_process.poll() is None:
                    _scan_process.kill()
                    _scan_process = None
                    result = {"success": True, "message": "Scan aborted"}
                else:
                    result = {"success": False, "message": "No scan running"}

            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/runtime-event":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            data = json.loads(body) if body else {}
            broadcast_sse("runtime-activity", data)

            response = {"received": True}
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/watch-start":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(body) if body else {}
            folder_path = data.get("folder_path", "")
            graph_path = data.get("graph_path", "")

            if not folder_path or not graph_path:
                result = {"error": "Missing folder_path or graph_path"}
            else:
                try:
                    # Stop existing watcher for this graph if one is running
                    if graph_path in _watcher_instances:
                        _watcher_instances[graph_path].stop()
                        del _watcher_instances[graph_path]

                    import traceback
                    import logging
                    logging.basicConfig(level=logging.INFO)

                    print(f"\n[LIVE] Attempting to start watcher...", flush=True)
                    print(f"[LIVE]   Folder: {folder_path}", flush=True)
                    print(f"[LIVE]   Graph: {graph_path}", flush=True)

                    # Create callback to broadcast updates via SSE
                    def on_graph_update(event):
                        update_data = {
                            "type": "graph-update",
                            "changed_files": event.changed_files,
                            "nodes_affected": event.nodes_affected,
                            "nodes_added": event.nodes_added,
                            "nodes_removed": event.nodes_removed,
                            "nodes_stale": getattr(event, 'nodes_stale', 0),
                            "duration_ms": event.duration_ms,
                            "graph_path": event.graph_path,
                        }
                        broadcast_sse("graph-update", update_data)
                        print(f"\n[LIVE] Graph updated: {event.nodes_affected} nodes affected, {event.duration_ms:.0f}ms\n", flush=True)

                    # Start watcher in background thread
                    print(f"[LIVE] Creating GraphWatcher instance...", flush=True)
                    watcher = GraphWatcher(
                        workspace_path=Path(folder_path),
                        graph_path=Path(graph_path),
                        on_update=on_graph_update,
                        debounce_ms=800
                    )

                    # Start watcher in background thread so HTTP response returns immediately
                    def start_watcher_async():
                        try:
                            print(f"[LIVE] Background thread: calling watcher.start()...", flush=True)
                            watcher.start()
                            print(f"\n[LIVE] Background thread: Watcher started successfully for {folder_path}\n", flush=True)
                        except Exception as e:
                            import traceback
                            error_details = traceback.format_exc()
                            print(f"\n[LIVE] ERROR: Background thread: Watcher failed to start:\n{error_details}\n", flush=True)

                    threading.Thread(target=start_watcher_async, daemon=True).start()

                    _watcher_instances[graph_path] = watcher
                    result = {"success": True, "message": "Watcher starting in background"}
                    print(f"[LIVE] HTTP response sent, watcher starting in background...", flush=True)

                except Exception as e:
                    import traceback
                    error_details = traceback.format_exc()
                    result = {"error": str(e)}
                    print(f"\n[LIVE] ERROR starting watcher:", flush=True)
                    print(f"{error_details}\n", flush=True)

            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/agent-activity":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            data = json.loads(body) if body else {}
            broadcast_sse("agent-activity", data)

            response = {"received": True}
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/watch-stop":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(body) if body else {}
            graph_path = data.get("graph_path", "")

            if graph_path in _watcher_instances:
                watcher = _watcher_instances[graph_path]
                watcher.stop()
                del _watcher_instances[graph_path]
                result = {"success": True, "message": "Watcher stopped"}
                print(f"[LIVE] Stopped watching: {graph_path}")
            else:
                result = {"success": False, "message": "No watcher running"}

            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        # Suppress SSE heartbeat spam
        if args and isinstance(args[0], str) and "/api/events" in args[0]:
            return
        super().log_message(format, *args)

if __name__ == "__main__":
    PORT = 8080
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), ViewerHandler)
    url = f"http://127.0.0.1:{PORT}"

    print(f"\nWI Viewer running: {url}")
    print(f"Graphs found: {len(find_graphs())}")
    print("Press Ctrl+C to stop\n")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
