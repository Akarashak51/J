"""
check_install.py
Checks that every file in this folder is the CURRENT version, and tells you which ones to replace.

Usage:  python check_install.py
"""

import importlib
import inspect
import json
import os
import sys
import time

EXPECTED_JOB_AGENT = (0, 3, 2)

# (module/file, what it must contain, what that feature is)
CHECKS = [
    ("job_agent", "fetch_ashby", "Ashby support"),
    ("job_agent", "requires_phd", "PhD rule and the fixed experience rule"),
    ("job_agent", "fetch_board_cached", "download-once board cache"),
    ("run_batch", "refresh", "refresh button support"),
    ("run_batch", "run_batch:progress", "progress counter"),
    ("build_dashboard", "build_html:live", "live dashboard toolbar"),
    ("dashboard_server", "create_app", "dashboard server"),
    ("add_company", "add_entry", "add_company helper"),
    ("triage_companies", "strict_name_match", "strict company-name matching"),
    ("triage_companies", "audit_cache", "--audit"),
    ("triage_companies", "apply_review", "--apply-review"),
    ("triage_companies", "enrich_guesses", "--enrich-review"),
    ("triage_companies", "read_csv_rows", "keeps your review answers safe and tolerates Excel locks"),
]


def version_tuple(text: str):
    return tuple(int(x) for x in text.split("."))


def main() -> int:
    sys.path.insert(0, os.getcwd())
    problems, loaded = [], {}
    for module in sorted({m for m, _, _ in CHECKS}):
        try:
            loaded[module] = importlib.import_module(module)
        except Exception as e:
            problems.append(f"{module}.py: cannot be loaded ({type(e).__name__}: {e})")
    ja = loaded.get("job_agent")
    if ja:
        version = getattr(ja, "__version__", "0")
        ok = version_tuple(version) >= EXPECTED_JOB_AGENT
        print(f"[{'OK' if ok else 'OLD'}] job_agent.py is v{version} (need v{'.'.join(map(str, EXPECTED_JOB_AGENT))} or newer)")
        if not ok:
            problems.append("job_agent.py is an old version")
    for module, feature, label in CHECKS:
        mod = loaded.get(module)
        if mod is None:
            continue
        name, _, param = feature.partition(":")
        target = getattr(mod, name, None)
        ok = target is not None and (not param or param in inspect.signature(target).parameters)
        print(f"[{'OK' if ok else 'MISSING'}] {module}.py - {label}")
        if not ok:
            problems.append(f"{module}.py is old (missing: {label})")

    print()
    for path, what in (("companies.json", "company list"), ("profile.json", "search settings")):
        try:
            data = json.load(open(path, encoding="utf-8"))
            detail = f"{len(data)} companies" if isinstance(data, list) else f"roles: {data.get('roles')}"
            print(f"[OK] {path} - {detail}")
        except (OSError, json.JSONDecodeError) as e:
            print(f"[!!] {path} - cannot read the {what}: {e}")
    try:
        age_h = (time.time() - os.path.getmtime("results.json")) / 3600
        print(f"[OK] results.json - last updated {age_h:.1f} hours ago" + ("  (stale: click Refresh now)" if age_h > 24 else ""))
    except OSError:
        print("[--] results.json - not created yet (click Refresh now)")

    print()
    if problems:
        print("REPLACE THESE FILES with the latest copies from the chat:")
        for problem in sorted({p.split(" ")[0] for p in problems}):
            print("  -", problem)
        return 1
    print("EVERYTHING IS UP TO DATE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())