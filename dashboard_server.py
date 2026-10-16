"""
dashboard_server.py  -  STEP 5
Local web dashboard with a "Refresh now" button that re-runs the job search.

Setup (once):   pip install flask
Run:            python dashboard_server.py
Then open:      http://127.0.0.1:5000

Options:
  --port 5000        port to listen on
  --workers 4        parallel portal searches
  --auto-hours 6     also refresh automatically every N hours (0 = off, default)
"""

import argparse
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, send_file

import build_dashboard as bd
import run_batch as rb

MIN_GAP_SECONDS = 60       # ignore refresh clicks that arrive right after a finished run


def create_app(companies_path="companies.json", profile_path="profile.json",
               results_path="results.json", workers=4, runner=None, min_gap=MIN_GAP_SECONDS):
    app = Flask(__name__)
    lock = threading.Lock()
    state = {"running": False, "started_at": None, "finished_at": None, "error": None}
    run = runner or (lambda: rb.refresh(companies_path, profile_path, results_path, workers))

    def _worker():
        error = None
        try:
            run()
        except Exception as e:                       # report it on the page, keep the server alive
            error = f"{type(e).__name__}: {e}"
        with lock:
            state.update(running=False, finished_at=time.time(), error=error)

    def trigger() -> dict:
        """Start a background run unless one is already running or one just finished."""
        with lock:
            if state["running"]:
                return {"started": False, "reason": "already running"}
            if state["finished_at"] and time.time() - state["finished_at"] < min_gap:
                return {"started": False, "reason": "a run just finished"}
            state.update(running=True, started_at=time.time(), error=None)
        threading.Thread(target=_worker, daemon=True).start()
        return {"started": True}

    def load_data() -> dict:
        try:
            return json.loads(Path(results_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            try:
                profile = {**rb.DEFAULT_PROFILE, **rb.load_json(profile_path)}
            except (OSError, json.JSONDecodeError):
                profile = dict(rb.DEFAULT_PROFILE)
            return {"generated_at": datetime.now(timezone.utc).isoformat(),
                    "profile": profile, "results": [], "never_run": True}

    @app.get("/")
    def index():
        return Response(bd.build_html(load_data(), live=True), mimetype="text/html")

    @app.get("/status")
    def status():
        with lock:
            return jsonify(dict(state))

    @app.post("/refresh")
    def refresh():
        return jsonify(trigger())

    @app.get("/results.json")
    def results_file():
        if not Path(results_path).exists():
            return jsonify({"error": "no results yet"}), 404
        return send_file(Path(results_path).resolve(), mimetype="application/json")

    app.trigger = trigger                              # type: ignore[attr-defined]
    return app


def main() -> int:
    p = argparse.ArgumentParser(description="Local job dashboard server")
    p.add_argument("--companies", default="companies.json")
    p.add_argument("--profile", default="profile.json")
    p.add_argument("--results", default="results.json")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--auto-hours", type=float, default=0, help="auto-refresh every N hours (0 = off)")
    args = p.parse_args()

    app = create_app(args.companies, args.profile, args.results, args.workers)

    if args.auto_hours > 0:
        def loop():
            while True:
                time.sleep(args.auto_hours * 3600)
                app.trigger()                          # type: ignore[attr-defined]
        threading.Thread(target=loop, daemon=True).start()

    print(f"Dashboard: http://127.0.0.1:{args.port}   (Ctrl+C to stop)")
    # 127.0.0.1 only: this tool is private to your computer, not exposed to your network.
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())