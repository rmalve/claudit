"""
Dashboard Launcher

Starts the FastAPI backend and the React/Vite frontend dev server, then opens
the browser at the port Vite *actually* bound (Vite auto-increments when its
preferred port is taken, so the requested port and the real port can differ).

Usage:
    python dashboard/start.py

Env overrides:
    DASHBOARD_API_PORT       backend port (default 8001; keep vite.config.js proxy in sync)
    DASHBOARD_FRONTEND_PORT  preferred frontend port (default 5180; Vite
                             auto-increments from here if it's taken)
"""

import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
REPO_ROOT = DASHBOARD_DIR.parent
FRONTEND_DIR = DASHBOARD_DIR / "frontend"

# Backend port. Off 8000 to avoid colliding with other local FastAPI apps
# (e.g. an onboarded project's own webapp). Must match vite.config.js proxy.
API_HOST = "0.0.0.0"
API_PORT = int(os.environ.get("DASHBOARD_API_PORT", "8001"))

# Preferred frontend port. Defaults off the common 5173 so the launcher doesn't
# fight other local Vite apps. Vite auto-increments if this is taken; we detect
# the real bound port from Vite's output below and open the browser there.
FRONTEND_PORT = int(os.environ.get("DASHBOARD_FRONTEND_PORT", "5180"))

# Matches Vite's "Local: http://localhost:5180/" line (ignores the Network line,
# which advertises a LAN IP rather than localhost/127.0.0.1).
_LOCAL_URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1):(\d{2,5})")

# Hard cap on how long to wait for Vite's URL before falling back to the
# preferred port. Vite normally prints within a couple of seconds.
_URL_WAIT_SECONDS = 12


def main():
    print("Starting Audit Dashboard...")
    print(f"  Backend:  http://localhost:{API_PORT}")
    print(f"  Frontend: launching (preferred port {FRONTEND_PORT}; Vite may pick the next free one)")
    print()

    # Start FastAPI backend (output inherits this terminal).
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "dashboard.api.main:app",
         "--host", API_HOST, "--port", str(API_PORT)],
        cwd=str(REPO_ROOT),
    )

    # Start Vite dev server with stdout captured so we can learn the real port.
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    frontend_proc = subprocess.Popen(
        [npm_cmd, "run", "dev", "--", "--port", str(FRONTEND_PORT)],
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    opened = threading.Event()

    def _open(url):
        # Open exactly once, whichever trigger fires first.
        if not opened.is_set():
            opened.set()
            print(f"\nOpening dashboard at {url}\n")
            webbrowser.open(url)

    def _watch_frontend():
        # Echo Vite output through to the user AND sniff out the bound port.
        stream = frontend_proc.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, ""):
            sys.stdout.write(raw)
            sys.stdout.flush()
            if not opened.is_set():
                m = _LOCAL_URL_RE.search(raw)
                if m:
                    _open(f"http://localhost:{m.group(1)}")
        stream.close()

    def _fallback():
        # Last resort: if Vite never printed a detectable Local URL, open the
        # preferred port. No-op if the watcher already opened the real one.
        time.sleep(_URL_WAIT_SECONDS)
        if not opened.is_set():
            print(f"\n(Vite URL not detected; opening preferred port {FRONTEND_PORT})\n")
            _open(f"http://localhost:{FRONTEND_PORT}")

    threading.Thread(target=_watch_frontend, daemon=True).start()
    threading.Thread(target=_fallback, daemon=True).start()

    print("Dashboard is running. Press Ctrl+C to stop both servers.")
    print()

    try:
        api_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        api_proc.terminate()
        frontend_proc.terminate()
        api_proc.wait()
        frontend_proc.wait()
        print("Dashboard stopped.")


if __name__ == "__main__":
    main()
