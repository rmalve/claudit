"""
Dashboard Launcher

Starts both the FastAPI backend and React frontend dev server,
then opens the dashboard in the default browser.

Usage:
    python dashboard/start.py
"""

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
REPO_ROOT = DASHBOARD_DIR.parent
FRONTEND_DIR = DASHBOARD_DIR / "frontend"

API_HOST = "0.0.0.0"
API_PORT = 8000
FRONTEND_PORT = 5173


def main():
    print("Starting Audit Dashboard...")
    print(f"  Backend:  http://localhost:{API_PORT}")
    print(f"  Frontend: http://localhost:{FRONTEND_PORT}")
    print()

    # Start FastAPI backend
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "dashboard.api.main:app",
         "--host", API_HOST, "--port", str(API_PORT)],
        cwd=str(REPO_ROOT),
    )

    # Start Vite dev server
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    frontend_proc = subprocess.Popen(
        [npm_cmd, "run", "dev", "--", "--port", str(FRONTEND_PORT)],
        cwd=str(FRONTEND_DIR),
    )

    # Wait for frontend to be ready, then open browser
    time.sleep(4)
    webbrowser.open(f"http://localhost:{FRONTEND_PORT}")

    print()
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
