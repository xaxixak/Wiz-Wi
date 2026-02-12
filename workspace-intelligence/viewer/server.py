"""WI Viewer Server - Fixed and Simplified"""
import http.server
import json
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
_watcher_instances = {}  # Track running watcher instances by graph_path

# Import watcher
sys.path.insert(0, str(PROJECT_ROOT))
from incremental.watcher import GraphWatcher

def broadcast_sse(event_type, data):
    """Broadcast an event to all SSE clients."""
    message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    dead_clients = []
    for client in _sse_clients:
        try:
            client.put(message)
        except:
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

        # Serve main viewer
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

        # API: Load specific graph
        elif path == "/api/graph":
            graph_path = params.get("path", [None])[0]
            if not graph_path:
                graphs = find_graphs()
                graph_path = graphs[0]["path"] if graphs else None

            if not graph_path or not Path(graph_path).is_file():
                self.send_error(404, "Graph not found")
                return

            with open(graph_path, "r", encoding="utf-8") as f:
                body = f.read().encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
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
            _sse_clients.append(client_queue)

            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            try:
                while True:
                    msg = client_queue.get(timeout=30)
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
            except:
                pass
            finally:
                if client_queue in _sse_clients:
                    _sse_clients.remove(client_queue)

        # API: Open Windows folder picker
        elif path == "/api/pick-folder":
            try:
                import tkinter as tk
                from tkinter import filedialog

                # Create hidden root window
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)

                # Open folder picker dialog
                folder_path = filedialog.askdirectory(title="Select Folder to Scan")
                root.destroy()

                result = {"folder": folder_path} if folder_path else {"error": "No folder selected"}
                body = json.dumps(result).encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                error_result = {"error": str(e)}
                body = json.dumps(error_result).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
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
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(body) if body else {}
            folder = data.get("folder", "")

            if not folder or not Path(folder).is_dir():
                result = {"error": "Invalid folder path"}
            else:
                try:
                    # Run orchestrator to scan
                    graph_name = Path(folder).name.lower() + "_graph.json"
                    graph_path = GRAPHS_DIR / graph_name

                    cmd = [
                        sys.executable,
                        str(PROJECT_ROOT / "pipeline" / "orchestrator.py"),
                        str(folder),
                        "-o", str(graph_path)
                    ]

                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

                    if proc.returncode == 0:
                        result = {"success": True, "graph_path": str(graph_path)}
                    else:
                        result = {"error": f"Scan failed: {proc.stderr}"}
                except Exception as e:
                    result = {"error": str(e)}

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
