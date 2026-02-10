"""
Workspace Intelligence - Graph Viewer HTTP Server

Web UI with folder browsing, scanning, graph visualization,
and real-time updates via Server-Sent Events (SSE).

Usage:
    python viewer/server.py
    python viewer/server.py --port 9090
    python viewer/server.py --watch /path/to/project --graph graphs/project_graph.json
"""

import http.server
import json
import argparse
import webbrowser
import threading
import subprocess
import sys
import os
import time
import queue
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VIEWER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = VIEWER_DIR.parent
GRAPHS_DIR = PROJECT_ROOT / "graphs"

# Ensure graphs directory exists
GRAPHS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# SSE (Server-Sent Events) broadcast for live updates
# ---------------------------------------------------------------------------
_sse_clients: list = []  # list of queue.Queue, one per connected client
_sse_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Active Processes and Watchers
# ---------------------------------------------------------------------------
_active_scans = {}     # folder_path -> subprocess.Popen
_active_watchers = {}  # folder_path -> GraphWatcher instance
_scans_lock = threading.Lock()
_watchers_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Folder Picker State (for async native dialog)
# ---------------------------------------------------------------------------
_picker_result = None  # Stores result from tkinter folder picker
_picker_lock = threading.Lock()


def broadcast_sse(event_type: str, data: dict) -> None:
    """Send an event to all connected SSE clients."""
    msg = f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


def _register_sse_client() -> "queue.Queue[str]":
    """Register a new SSE client and return its message queue."""
    q: queue.Queue[str] = queue.Queue(maxsize=50)
    with _sse_lock:
        _sse_clients.append(q)
    return q


def _unregister_sse_client(q: "queue.Queue[str]") -> None:
    """Remove a disconnected SSE client."""
    with _sse_lock:
        if q in _sse_clients:
            _sse_clients.remove(q)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_existing_graphs():
    """Find all .json graph files in the graphs directory."""
    graphs = []
    seen = set()
    # Check graphs/ directory (primary location)
    for f in sorted(GRAPHS_DIR.glob("*.json")):
        graphs.append({"name": f.stem, "path": str(f), "size": f.stat().st_size})
        seen.add(f.stem)
    # Also check project root for any not yet copied
    for f in sorted(PROJECT_ROOT.glob("*graph*.json")):
        if f.stem not in seen:
            graphs.append({"name": f.stem, "path": str(f), "size": f.stat().st_size})
    return graphs


def _browse_directory(path_str):
    """List directories and files in a path for the folder browser."""
    try:
        p = Path(path_str).resolve()
        if not p.exists():
            return {"error": f"Path not found: {path_str}"}

        items = []
        if p.parent != p:  # not root
            items.append({"name": "..", "path": str(p.parent), "type": "parent"})

        for entry in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith(".") and entry.name not in (".env.example",):
                continue
            if entry.name in ("node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build"):
                continue
            if entry.is_dir():
                items.append({"name": entry.name, "path": str(entry), "type": "folder"})
            elif entry.suffix in (".json", ".js", ".ts", ".py", ".go", ".rs", ".java", ".cs"):
                items.append({"name": entry.name, "path": str(entry), "type": "file"})

        return {"current": str(p), "items": items}
    except PermissionError:
        return {"error": f"Permission denied: {path_str}"}
    except Exception as e:
        return {"error": str(e)}


def _run_scan(folder_path, passes=None, watch_enabled=False, debounce_ms=800):
    """Run the pipeline on a folder. Returns the graph JSON path."""
    folder = Path(folder_path).resolve()
    if not folder.is_dir():
        return {"error": f"Not a directory: {folder_path}"}

    graph_name = folder.name.lower().replace(" ", "-")
    output_path = GRAPHS_DIR / f"{graph_name}_graph.json"

    cli_path = PROJECT_ROOT / "cli.py"
    cmd = [sys.executable, str(cli_path), "index", str(folder), "-o", str(output_path)]

    # Add --passes flag if specified
    if passes:
        # Filter and join passes
        passes_str = ",".join(passes)
        cmd.extend(["--passes", passes_str])

    try:
        start_time = time.time()

        # Use Popen so we can track and cancel the process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
        )

        # Store process for cancellation
        with _scans_lock:
            _active_scans[str(folder)] = process

        try:
            stdout, stderr = process.communicate(timeout=120)
            duration_ms = int((time.time() - start_time) * 1000)

            if process.returncode == 0:
                # Parse output for stats (optional)
                output_text = stdout + stderr

                return {
                    "success": True,
                    "graph_path": str(output_path),
                    "graph_name": graph_name,
                    "output": output_text,
                    "duration_ms": duration_ms,
                    "watch_config": {"enabled": watch_enabled, "debounce_ms": debounce_ms} if watch_enabled else None,
                }
            else:
                return {"error": f"Scan failed:\n{stderr}\n{stdout}"}
        finally:
            with _scans_lock:
                _active_scans.pop(str(folder), None)

    except subprocess.TimeoutExpired:
        with _scans_lock:
            proc = _active_scans.pop(str(folder), None)
            if proc:
                proc.kill()
        return {"error": "Scan timed out (>120s). Try a smaller folder."}
    except Exception as e:
        with _scans_lock:
            _active_scans.pop(str(folder), None)
        return {"error": str(e)}


def _start_watcher(folder_path, graph_path, passes=None, debounce_ms=800):
    """Start a GraphWatcher for the given folder."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from incremental.watcher import GraphWatcher

        folder = Path(folder_path).resolve()
        graph = Path(graph_path).resolve()

        if not folder.is_dir():
            return {"error": f"Folder not found: {folder_path}"}

        if not graph.is_file():
            return {"error": f"Graph file not found: {graph_path}"}

        # Check if watcher already running for this folder
        with _watchers_lock:
            if str(folder) in _active_watchers:
                return {"error": "Watcher already active for this folder"}

        # Filter passes (exclude 'scan' since watcher only handles incremental)
        if passes:
            passes = [p for p in passes if p != "scan"]
        else:
            passes = ["treesitter", "patterns", "connections"]

        def on_graph_update(event):
            broadcast_sse("graph-updated", {
                "changed_files": event.changed_files,
                "nodes_affected": event.nodes_affected,
                "nodes_stale": event.nodes_stale,
                "nodes_added": event.nodes_added,
                "nodes_removed": event.nodes_removed,
                "edges_after": event.edges_after,
                "duration_ms": event.duration_ms,
                "graph_path": event.graph_path,
            })

        watcher = GraphWatcher(
            workspace_path=folder,
            graph_path=graph,
            on_update=on_graph_update,
            debounce_ms=debounce_ms,
            passes=passes,
        )

        watcher.start()

        with _watchers_lock:
            _active_watchers[str(folder)] = watcher

        return {
            "success": True,
            "watch_id": str(folder),
            "message": f"Watcher started for {folder.name}",
        }

    except ImportError as e:
        return {"error": f"Could not start watcher: {e}. Install watchdog: pip install watchdog"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

def make_handler():
    """Create the request handler class."""

    class ViewerHandler(http.server.SimpleHTTPRequestHandler):

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            params = parse_qs(parsed.query)

            if path == "/":
                self._serve_file("home.html", "text/html")
            elif path == "/view":
                self._serve_file("index.html", "text/html")
            elif path == "/api/graph":
                graph_path = params.get("path", [None])[0]
                self._serve_graph(graph_path)
            elif path == "/api/graphs":
                self._send_json(_find_existing_graphs())
            elif path == "/api/browse":
                dir_path = params.get("path", [self._default_browse_path()])[0]
                self._send_json(_browse_directory(dir_path))
            elif path == "/api/events":
                self._serve_sse()
            elif path == "/api/pick-folder":
                self._start_folder_picker()
            elif path == "/api/pick-folder-result":
                self._get_picker_result()
            elif path == "/favicon.ico":
                # Return a simple 1x1 transparent icon to suppress 404
                self.send_response(204)
                self.end_headers()
            else:
                super().do_GET()

        def do_POST(self):
            parsed = urlparse(self.path)

            if parsed.path == "/api/scan":
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8")
                data = json.loads(body) if body else {}
                folder = data.get("folder", "")
                if not folder:
                    self._send_json({"error": "No folder specified"})
                    return

                # Parse configuration from request
                passes = data.get("passes", None)
                watch_enabled = data.get("watchEnabled", False)
                debounce_ms = data.get("debounceMs", 800)

                result = _run_scan(folder, passes=passes, watch_enabled=watch_enabled, debounce_ms=debounce_ms)

                # Start watcher if scan succeeded and watch mode enabled
                if result.get("success") and watch_enabled:
                    graph_path = result.get("graph_path")
                    watch_result = _start_watcher(folder, graph_path, passes, debounce_ms)
                    if not watch_result.get("success"):
                        result["watch_error"] = watch_result.get("error")

                self._send_json(result)

            elif parsed.path == "/api/scan-abort":
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
                data = json.loads(body) if body else {}
                folder = data.get("folder", "")

                with _scans_lock:
                    if folder and folder in _active_scans:
                        process = _active_scans[folder]
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        _active_scans.pop(folder, None)
                        self._send_json({"success": True, "message": "Scan cancelled"})
                    elif _active_scans:
                        # Cancel any active scan if folder not specified
                        folder_path, process = next(iter(_active_scans.items()))
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        _active_scans.pop(folder_path, None)
                        self._send_json({"success": True, "message": "Scan cancelled"})
                    else:
                        self._send_json({"error": "No active scan to cancel"})

            elif parsed.path == "/api/watch-start":
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8")
                data = json.loads(body) if body else {}
                folder = data.get("folder", "")
                graph_path = data.get("graph_path", "")
                debounce_ms = data.get("debounce_ms", 800)
                passes = data.get("passes", ["treesitter", "patterns", "connections"])

                if not folder or not graph_path:
                    self._send_json({"error": "folder and graph_path required"})
                    return

                result = _start_watcher(folder, graph_path, passes, debounce_ms)
                self._send_json(result)

            elif parsed.path == "/api/watch-stop":
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
                data = json.loads(body) if body else {}
                folder = data.get("folder", "")
                graph_path = data.get("graph_path", "")

                # Try to find watcher by folder or graph_path
                with _watchers_lock:
                    watcher_key = None
                    if folder and folder in _active_watchers:
                        watcher_key = folder
                    elif graph_path:
                        # Find by graph path
                        for key, watcher in _active_watchers.items():
                            if str(watcher.graph_path) == graph_path:
                                watcher_key = key
                                break

                    if watcher_key:
                        watcher = _active_watchers[watcher_key]
                        watcher.stop()
                        _active_watchers.pop(watcher_key, None)
                        self._send_json({"success": True, "message": "Watcher stopped"})
                    else:
                        self._send_json({"error": "No active watcher found"})

            else:
                self._send_error(404, "Not found")

        def _default_browse_path(self):
            """Default path for folder browser."""
            # Start at the parent of workspace-intelligence
            return str(PROJECT_ROOT.parent)

        def _serve_graph(self, graph_path_str):
            if not graph_path_str:
                # Try to find any existing graph
                graphs = _find_existing_graphs()
                if graphs:
                    graph_path_str = graphs[0]["path"]
                else:
                    self._send_json({"nodes": [], "edges": []})
                    return

            gp = Path(graph_path_str)
            if not gp.is_file():
                self._send_json({"nodes": [], "edges": []})
                return

            try:
                raw = gp.read_text(encoding="utf-8")
                data = json.loads(raw)
                self._send_json(data)
            except Exception as e:
                self._send_error(500, f"Error reading graph: {e}")

        def _start_folder_picker(self):
            """Launch native Windows folder picker in background thread."""
            global _picker_result

            def show_picker():
                global _picker_result
                try:
                    import tkinter as tk
                    from tkinter import filedialog

                    root = tk.Tk()
                    root.withdraw()
                    root.attributes('-topmost', True)

                    folder_path = filedialog.askdirectory(
                        title="Select Folder to Scan",
                        mustexist=True
                    )

                    root.destroy()

                    with _picker_lock:
                        if folder_path:
                            _picker_result = {"success": True, "path": folder_path}
                        else:
                            _picker_result = {"success": False, "cancelled": True}

                except Exception as e:
                    with _picker_lock:
                        _picker_result = {"success": False, "error": str(e)}

            # Reset result and start picker in background
            with _picker_lock:
                _picker_result = None

            picker_thread = threading.Thread(target=show_picker, daemon=True)
            picker_thread.start()

            self._send_json({"status": "started"})

        def _get_picker_result(self):
            """Poll for folder picker result."""
            global _picker_result

            with _picker_lock:
                if _picker_result is None:
                    self._send_json({"status": "waiting"})
                else:
                    result = _picker_result.copy()
                    _picker_result = None  # Clear result
                    self._send_json(result)

        def _serve_sse(self):
            """Handle SSE connection — keeps the connection open and streams events."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            # Send initial heartbeat
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
            except Exception:
                return

            client_q = _register_sse_client()
            try:
                while True:
                    try:
                        msg = client_q.get(timeout=15)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        # Send heartbeat to keep connection alive
                        try:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                        except Exception:
                            break
            except Exception:
                pass
            finally:
                _unregister_sse_client(client_q)

        def _serve_file(self, filename, content_type):
            filepath = VIEWER_DIR / filename
            if not filepath.is_file():
                self._send_error(404, f"File not found: {filename}")
                return
            content = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, data):
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, code, message):
            body = json.dumps({"error": message}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            if "/api/" in (args[0] if args else ""):
                # Skip SSE heartbeat noise
                if "/api/events" not in (args[0] if args else ""):
                    super().log_message(format, *args)

    return ViewerHandler


def main():
    parser = argparse.ArgumentParser(description="Workspace Intelligence Viewer")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    parser.add_argument("--watch", type=str, default=None,
                        help="Watch a workspace directory for live updates")
    parser.add_argument("--graph", type=str, default=None,
                        help="Graph file to update when watching")
    args = parser.parse_args()

    # Move any existing graph files to graphs/
    for f in PROJECT_ROOT.glob("*_graph.json"):
        dest = GRAPHS_DIR / f.name
        if not dest.exists():
            import shutil
            shutil.copy2(f, dest)

    # Start file watcher if --watch is specified
    watcher = None
    if args.watch:
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from incremental.watcher import GraphWatcher

            graph_path = Path(args.graph) if args.graph else (
                GRAPHS_DIR / f"{Path(args.watch).name.lower()}_graph.json"
            )

            def on_graph_update(event):
                broadcast_sse("graph-updated", {
                    "changed_files": event.changed_files,
                    "nodes_affected": event.nodes_affected,
                    "nodes_stale": event.nodes_stale,
                    "nodes_added": event.nodes_added,
                    "nodes_removed": event.nodes_removed,
                    "edges_after": event.edges_after,
                    "duration_ms": event.duration_ms,
                    "graph_path": event.graph_path,
                })

            watcher = GraphWatcher(
                workspace_path=Path(args.watch),
                graph_path=graph_path,
                on_update=on_graph_update,
            )
            watcher.start()
            print(f"File watcher active: {args.watch}")
            print(f"Graph auto-updates:  {graph_path}")
        except ImportError as e:
            print(f"WARNING: Could not start watcher: {e}")
            print("  Install watchdog: pip install watchdog")

    handler_class = make_handler()
    # Use ThreadingHTTPServer so concurrent requests (loadGraphs + browse + SSE) don't block
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler_class)

    url = f"http://127.0.0.1:{args.port}"
    print(f"\nWorkspace Intelligence Viewer")
    print(f"Open in browser: {url}")
    if watcher:
        print(f"Live updates:    ENABLED (SSE)")
    print(f"Press Ctrl+C to stop.\n")

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        if watcher:
            watcher.stop()
        server.shutdown()
        print("Stopped.")


if __name__ == "__main__":
    main()
