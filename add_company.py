"""
add_company.py  -  STEP 6
Add a company to companies.json from its careers page link (or slug), after checking it works.

Usage:
  python add_company.py "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
  python add_company.py "https://boards.greenhouse.io/stripe" --name Stripe
  python add_company.py "https://jobs.lever.co/spotify"
  python add_company.py stripe                 (a bare slug is tried on Greenhouse, Lever and Ashby)
  python add_company.py <link> --no-check      (add without testing the connection)

How to get the link: open the company's careers page, go to its job search page,
and copy the address from your browser's address bar.
"""

import argparse
import json
import os
import sys
from urllib.parse import urlparse

import job_agent as ja


def parse_target(text: str, name: str | None = None) -> dict:
    """Turn what the user typed into a companies.json entry."""
    text = text.strip()
    if "/" in text or "." in text:                       # looks like a link
        url = text if "://" in text else "https://" + text
        detected = ja.detect_from_url(url)
        if detected:
            guess = detected.get("tenant") or detected.get("slug") or ""
        else:
            guess = urlparse(url).netloc.replace("www.", "").split(".")[0]
        return {"name": name or guess.replace("-", " ").title(), "url": url}
    slug = text.lower()
    return {"name": name or slug.replace("-", " ").title(), "slug": slug}


def check(entry: dict) -> tuple[bool, str]:
    """Try the portal once. Returns (works, message)."""
    try:
        if entry.get("url"):
            d = ja.detect_from_url(entry["url"])
            if d is None:
                return False, ("portal not recognised. Supported links: Workday (myworkdayjobs.com), "
                               "Greenhouse (boards.greenhouse.io), Lever (jobs.lever.co), Ashby (jobs.ashbyhq.com)")
            if d["ats"] == "workday":
                base = f"https://{d['host']}/wday/cxs/{d['tenant']}/{d['site']}"
                data = ja.http_post_json(f"{base}/jobs", {
                    "appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
                return True, f"Workday site reachable, about {data.get('total', '?')} postings"
            jobs = ja.ADAPTERS[d["ats"]](d["slug"])
            return True, f"{d['ats'].title()} board reachable, {len(jobs)} postings"
        slug = entry["slug"]
        found, errors = [], []
        for portal, fetch in ja.ADAPTERS.items():
            try:
                found.append(f"{portal.title()} ({len(fetch(slug))} postings)")
            except ja.PortalNotFound:
                continue
            except ja.PortalError as e:
                errors.append(f"{portal}: {e}")
        if found:
            return True, "found on " + " and ".join(found)
        if errors:
            return False, "could not reach it: " + "; ".join(errors)
        return False, ("slug not found on Greenhouse, Lever or Ashby. If the company uses Workday "
                       "or another portal, paste its careers page link instead")
    except ja.PortalNotFound:
        return False, "careers site not found - check the link"
    except ja.PortalError as e:
        return False, f"could not reach it: {e}"


def _key(entry: dict) -> str:
    """Identity of a company, so a link and a slug for the same board count as one."""
    if entry.get("url"):
        d = ja.detect_from_url(entry["url"])
        if d and d["ats"] == "workday":
            return f"workday:{d['host']}/{d['site']}".lower()
        if d:
            return f"slug:{d['slug']}".lower()
        return entry["url"].lower().rstrip("/")
    return f"slug:{entry.get('slug', '')}".lower()


def add_entry(path: str, entry: dict) -> str:
    """Append to companies.json. Returns 'added' or 'duplicate'. Raises ValueError on a broken file."""
    companies = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                companies = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} is not valid JSON ({e}). Fix or delete it first.") from e
        if not isinstance(companies, list):
            raise ValueError(f"{path} must contain a list of companies.")
    if any(_key(c) == _key(entry) for c in companies):
        return "duplicate"
    companies.append(entry)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(companies, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return "added"


def main() -> int:
    p = argparse.ArgumentParser(description="Add a company to companies.json")
    p.add_argument("target", help="careers page link, or a company slug like 'stripe'")
    p.add_argument("--name", help="display name (default: guessed from the link)")
    p.add_argument("--companies", default="companies.json")
    p.add_argument("--no-check", action="store_true", help="add without testing the connection")
    args = p.parse_args()

    entry = parse_target(args.target, args.name)
    if not args.no_check:
        ok, message = check(entry)
        print(("OK: " if ok else "NOT ADDED: ") + message)
        if not ok:
            print("Use --no-check to add it anyway.")
            return 1
    try:
        result = add_entry(args.companies, entry)
    except ValueError as e:
        print(f"NOT ADDED: {e}")
        return 1
    if result == "duplicate":
        print(f"Already in {args.companies}: {entry['name']}")
        return 0
    print(f"Added {entry['name']} to {args.companies}. Click 'Refresh now' (or run run_batch.py) to search it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())