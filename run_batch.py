"""
run_batch.py  -  STEP 3
Run job_agent over many companies and many roles in one go.

Files used:
  profile.json    your roles, experience limit, freshness window
  companies.json  the companies to check (a slug OR a careers page link each)
Output:
  results.json    everything found, with a status per company/role (the dashboard will read this)

Usage:
  python run_batch.py
  python run_batch.py --companies companies.json --profile profile.json --out results.json --workers 4
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone

import job_agent as ja

DEFAULT_PROFILE = {"roles": ["Software Engineer"], "max_exp": 2, "days": 3, "include_internships": True}


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def job_to_dict(job: ja.Job) -> dict:
    d = asdict(job)
    d["posted"] = job.posted.isoformat() if job.posted else None
    d["age_text"] = ja.age_text(job)
    return d


def run_one(entry: dict, role: str, profile: dict) -> dict:
    """Search one company for one role. Never raises: problems become an 'unreachable' result."""
    name = entry.get("name") or entry.get("slug") or entry.get("url") or "?"
    try:
        kwargs = dict(role=role,
                      max_exp=profile.get("max_exp", 2),
                      days=profile.get("days", 3),
                      include_internships=profile.get("include_internships", True))
        if entry.get("url"):
            res = ja.search_url(entry["url"], **kwargs)
        elif entry.get("slug"):
            res = ja.search_company(entry["slug"], **kwargs)
        else:
            res = {"status": "unreachable", "reason": "entry needs a 'slug' or a 'url'", "jobs": []}
    except Exception as e:  # one bad company must not stop the whole run
        res = {"status": "unreachable", "reason": f"unexpected error: {type(e).__name__}: {e}", "jobs": []}
    res["company"] = name
    res["role"] = role
    return res


def run_batch(companies: list[dict], profile: dict, workers: int = 4) -> list[dict]:
    pairs = [(c, r) for r in profile["roles"] for c in companies]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda cr: run_one(cr[0], cr[1], profile), pairs))


def serialise(results: list[dict], profile: dict) -> dict:
    out = []
    for r in results:
        item = {k: v for k, v in r.items() if k != "jobs"}
        item["jobs"] = [job_to_dict(j) for j in r["jobs"]]
        out.append(item)
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "profile": profile, "results": out}


def print_summary(results: list[dict], profile: dict) -> None:
    for role in profile["roles"]:
        rows = [r for r in results if r["role"] == role]
        jobs = [(r["company"], j) for r in rows for j in r["jobs"]]
        print(f"\n=== {role}: {len(jobs)} job(s) in the last {profile['days']} days ===")
        for company, j in jobs:
            exp = j.experience + (", description not read" if j.experience == "unspecified" and not j.description else "")
            print(f"  * {company}: {j.title}  [{j.match}] [{j.job_type}] [exp: {exp}]")
            print(f"      {j.location or 'location n/a'} | {ja.age_text(j)}")
            print(f"      {j.url}")
            if j.note:
                print(f"      note: {j.note}")
        quiet = [r["company"] for r in rows if r["status"] == "no_role"]
        broken = [(r["company"], r.get("reason", "")) for r in rows if r["status"] == "unreachable"]
        if quiet:
            print(f"  [-] reached, nothing matching: {', '.join(quiet)}")
        for company, reason in broken:
            print(f"  [X] COULD NOT CHECK {company}: {reason}")


def main() -> int:
    p = argparse.ArgumentParser(description="Batch job search over many companies and roles")
    p.add_argument("--companies", default="companies.json")
    p.add_argument("--profile", default="profile.json")
    p.add_argument("--out", default="results.json")
    p.add_argument("--workers", type=int, default=4, help="parallel searches (default 4)")
    args = p.parse_args()

    try:
        companies = load_json(args.companies)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cannot read {args.companies}: {e}")
        return 1
    try:
        profile = {**DEFAULT_PROFILE, **load_json(args.profile)}
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cannot read {args.profile} ({e}); using defaults: {DEFAULT_PROFILE}")
        profile = dict(DEFAULT_PROFILE)

    print(f"job_agent v{ja.__version__} | {len(companies)} companies x {len(profile['roles'])} role(s) "
          f"| last {profile['days']} days | max {profile['max_exp']} yrs experience")
    results = run_batch(companies, profile, args.workers)
    print_summary(results, profile)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(serialise(results, profile), f, indent=2, ensure_ascii=False)
    print(f"\nSaved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
