"""
Workspace Intelligence - Graph Viewer HTTP Server

Web UI with folder browsing, scanning, and graph visualization.
No command line needed -- do everything from the browser.

Usage:
    python viewer/server.py
    python viewer/server.py --port 9090
"""

import http.server
import json
import argparse
import webbrowser
import threading
import subprocess
import sys
import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VIEWER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = VIEWER_DIR.parent
GRAPHS_DIR = PROJECT_ROOT / "graphs"

# Ensure graphs directory exists
GRAPHS_DIR.mkdir(exist_ok=True)


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


def _run_scan(folder_path):
    """Run the pipeline on a folder. Returns the graph JSON path."""
    folder = Path(folder_path).resolve()
    if not folder.is_dir():
        return {"error": f"Not a directory: {folder_path}"}

    graph_name = folder.name.lower().replace(" ", "-")
    output_path = GRAPHS_DIR / f"{graph_name}_graph.json"

    cli_path = PROJECT_ROOT / "cli.py"
    cmd = [sys.executable, str(cli_path), "index", str(folder), "-o", str(output_path)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            return {
                "success": True,
                "graph_path": str(output_path),
                "graph_name": graph_name,
                "output": result.stdout + result.stderr,
            }
        else:
            return {"error": f"Scan failed:\n{result.stderr}\n{result.stdout}"}
    except subprocess.TimeoutExpired:
        return {"error": "Scan timed out (>120s). Try a smaller folder."}
    except Exception as e:
        return {"error": str(e)}


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
                result = _run_scan(folder)
                self._send_json(result)
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
                super().log_message(format, *args)

    return ViewerHandler


def main():
    parser = argparse.ArgumentParser(description="Workspace Intelligence Viewer")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    # Move any existing graph files to graphs/
    for f in PROJECT_ROOT.glob("*_graph.json"):
        dest = GRAPHS_DIR / f.name
        if not dest.exists():
            import shutil
            shutil.copy2(f, dest)

    handler_class = make_handler()
    server = http.server.HTTPServer(("127.0.0.1", args.port), handler_class)

    url = f"http://127.0.0.1:{args.port}"
    print(f"Workspace Intelligence Viewer")
    print(f"Open in browser: {url}")
    print(f"Press Ctrl+C to stop.\n")

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
