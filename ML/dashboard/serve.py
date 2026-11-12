"""Serve the local dashboard.

    python ML/dashboard/serve.py            # http://localhost:8765, opens the browser
    python ML/dashboard/serve.py --port 9000 --no-open

Static files come from this folder; /data/ maps to data/dashboard/app/ (built by
ML/src/build_dashboard_data.py).
"""
from __future__ import annotations

import argparse
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "data" / "dashboard" / "app"


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            target = (DATA / clean[len("/data/"):]).resolve()
            return str(target) if DATA in target.parents else str(DATA / "missing")
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass


def main() -> None:
    global DATA
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--data", type=Path, default=DATA, help="folder with meta.json, grid.bin, dem.bin and the series files")
    args = parser.parse_args()
    DATA = args.data.resolve()
    if not (DATA / "meta.json").exists():
        raise SystemExit(f"No data in {DATA}. Run: python ML/src/build_dashboard_data.py")
    handler = lambda *a, **k: Handler(*a, directory=str(HERE), **k)  # noqa: E731
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://localhost:{args.port}/"
    print(f"Dashboard at {url}  (Ctrl+C to stop)")
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
