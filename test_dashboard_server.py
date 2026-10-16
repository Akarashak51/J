"""Offline tests for dashboard_server.py  -  run:  python test_dashboard_server.py"""
import json
import os
import tempfile
import time

try:
    import flask  # noqa: F401
except ImportError:
    raise SystemExit("Flask is not installed. Run:  pip install flask")

import dashboard_server as ds

FAKE = {"generated_at": "2026-10-11T12:00:00+00:00",
        "profile": {"roles": ["Software Engineer"], "max_exp": 2, "days": 3, "include_internships": True},
        "results": []}


def wait_done(client, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get("/status").get_json()
        if not s["running"]:
            return s
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def make(tmp, runner, min_gap=0):
    path = os.path.join(tmp, "results.json")
    return ds.create_app(results_path=path, runner=lambda: runner(path), min_gap=min_gap), path


def test_first_visit_shows_never_run_and_refresh_button():
    with tempfile.TemporaryDirectory() as tmp:
        app, _ = make(tmp, lambda p: None)
        html = app.test_client().get("/").get_data(as_text=True)
        assert "Refresh now" in html and '"never_run": true' in html
        assert app.test_client().get("/results.json").status_code == 404


def test_refresh_runs_in_background_and_updates_page():
    with tempfile.TemporaryDirectory() as tmp:
        def runner(path):
            time.sleep(0.1)
            json.dump(FAKE, open(path, "w"))
        app, _ = make(tmp, runner)
        c = app.test_client()
        assert c.post("/refresh").get_json() == {"started": True}
        assert c.get("/status").get_json()["running"] is True            # visible while running
        assert c.post("/refresh").get_json()["started"] is False         # no double runs
        s = wait_done(c)
        assert s["error"] is None
        html = c.get("/").get_data(as_text=True)
        assert "\"never_run\": true" not in html and "2026-10-11T12:00:00" in html
        assert c.get("/results.json").status_code == 200


def test_failed_run_is_reported_and_server_survives():
    with tempfile.TemporaryDirectory() as tmp:
        def runner(path):
            raise RuntimeError("portal exploded")
        app, _ = make(tmp, runner)
        c = app.test_client()
        c.post("/refresh")
        s = wait_done(c)
        assert "portal exploded" in s["error"]
        assert c.get("/").status_code == 200


def test_rapid_second_click_is_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        app, _ = make(tmp, lambda p: json.dump(FAKE, open(p, "w")), min_gap=60)
        c = app.test_client()
        assert c.post("/refresh").get_json()["started"] is True
        wait_done(c)
        assert c.post("/refresh").get_json() == {"started": False, "reason": "a run just finished"}


def test_refresh_requires_post():
    with tempfile.TemporaryDirectory() as tmp:
        app, _ = make(tmp, lambda p: None)
        assert app.test_client().get("/refresh").status_code == 405


def test_real_refresh_function_writes_valid_file():
    import job_agent as ja
    import run_batch as rb
    with tempfile.TemporaryDirectory() as tmp:
        cpath, ppath, rpath = (os.path.join(tmp, n) for n in ("c.json", "p.json", "r.json"))
        json.dump([{"name": "A", "slug": "a"}], open(cpath, "w"))
        json.dump({"roles": ["Software Engineer"]}, open(ppath, "w"))
        ja.search_company = lambda slug, role, max_exp, days, include_internships=True: {
            "company": slug, "status": "no_role", "scanned": 3, "jobs": []}
        data = rb.refresh(cpath, ppath, rpath)
        assert json.load(open(rpath))["results"][0]["status"] == "no_role"
        assert not os.path.exists(rpath + ".tmp") and data["profile"]["days"] == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("All server tests passed.")