"""
job_agent.py  -  STEP 1
Fresher-focused job finder for company career portals.

Supports : Greenhouse, Lever  (public JSON APIs, standard library only)
Rules    : 1. Title must match (exact or related role family)
           2. Experience must fit a fresher / 0-2 years (unclear = kept, tagged)
           3. Posted within the last N days (default 3); unknown date = kept, tagged

Usage:
  python job_agent.py --company stripe --role "Software Engineer"
  python job_agent.py --company stripe --company figma --role "Software Engineer" --days 3
  python job_agent.py --company stripe --role "Embedded Engineer" --json
"""

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

USER_AGENT = "Mozilla/5.0 (JobAgent/0.1; personal fresher job search)"
TIMEOUT = 15

# --------------------------------------------------------------------------
# 1. Unified job schema
# --------------------------------------------------------------------------

@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str                 # "greenhouse" | "lever"
    posted: datetime | None     # timezone-aware UTC, or None if unknown
    date_kind: str              # "published" | "updated" | "unknown"
    description: str            # plain text
    job_type: str = "full-time"  # "full-time" | "internship"
    match: str = ""             # "exact" | "related"
    experience: str = ""        # "fit" | "unspecified"


# --------------------------------------------------------------------------
# 2. Title matching (role families)
# --------------------------------------------------------------------------

ROLE_FAMILIES = {
    "software": [
        "software engineer", "software developer", "sde", "sde 1", "sde i",
        "full stack developer", "full stack engineer", "fullstack developer",
        "backend developer", "backend engineer", "frontend developer",
        "frontend engineer", "application developer", "associate software engineer",
        "member of technical staff", "programmer analyst", "web developer",
    ],
    "embedded": [
        "embedded engineer", "embedded software engineer", "firmware engineer",
        "iot engineer", "hardware engineer", "electronics engineer",
        "graduate engineer trainee", "embedded developer", "systems engineer",
    ],
    "data": [
        "data analyst", "data scientist", "data engineer", "machine learning engineer",
        "ml engineer", "ai engineer", "business analyst",
    ],
}

SENIORITY_WORDS = [
    "senior", "sr", "lead", "principal", "staff", "manager", "director",
    "head", "vp", "architect", "distinguished",
]


def norm(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9+ ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def has_seniority(title_n: str) -> bool:
    return any(has_phrase(title_n, w) for w in SENIORITY_WORDS)


def families_for_query(query_n: str) -> list[str]:
    """Families whose aliases overlap with the user's query."""
    found = []
    for fam, aliases in ROLE_FAMILIES.items():
        if any(has_phrase(query_n, a) or has_phrase(a, query_n) for a in aliases):
            found.append(fam)
    return found


def match_title(title: str, query: str) -> str:
    """Return 'exact', 'related' or '' (no match)."""
    t, q = norm(title), norm(query)
    if not t or not q:
        return ""
    if has_seniority(t) and not has_seniority(q):
        return ""
    q_tokens = q.split()
    if all(has_phrase(t, tok) for tok in q_tokens):
        return "exact"
    for fam in families_for_query(q):
        if any(has_phrase(t, a) for a in ROLE_FAMILIES[fam]):
            return "related"
    return ""


# --------------------------------------------------------------------------
# 3. Experience classifier
# --------------------------------------------------------------------------

FRESHER_KEYWORDS = [
    "fresher", "freshers", "entry level", "new grad", "new graduate",
    "graduate", "trainee", "intern", "internship", "campus", "early career",
    "0 2 years", "0 1 years", "0 to 2 years",
]

YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)"
    r"[^.\n]{0,40}?experience"
    r"|experience[^.\n]{0,30}?(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)


def min_years_required(text: str) -> int | None:
    """Lowest 'N years experience' figure mentioned, or None."""
    lows = []
    for m in YEARS_RE.finditer(text):
        nums = [g for g in m.groups() if g]
        if nums:
            lows.append(int(nums[0]))
    return min(lows) if lows else None


def classify_experience(title: str, description: str, max_exp: int) -> str:
    """Return 'fit', 'unspecified' or 'reject'."""
    title_n, desc_n = norm(title), norm(description)
    if has_seniority(title_n):
        return "reject"
    years = min_years_required(f"{title}. {description}")
    if years is not None and years > max_exp:
        # Explicit demand beyond the limit wins, unless the title itself says fresher.
        if not any(has_phrase(title_n, k) for k in FRESHER_KEYWORDS):
            return "reject"
    if any(has_phrase(title_n, k) for k in FRESHER_KEYWORDS):
        return "fit"
    if years is not None and years <= max_exp:
        return "fit"
    if any(has_phrase(desc_n, k) for k in ("fresher", "freshers", "entry level", "new grad", "new graduate", "early career")):
        return "fit"
    return "unspecified"


# --------------------------------------------------------------------------
# 4. Portal adapters
# --------------------------------------------------------------------------

class PortalNotFound(Exception):
    """Slug does not exist on this ATS (HTTP 404)."""


class PortalError(Exception):
    """Network failure, timeout, blocked, bad response."""


def http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PortalNotFound(url) from e
        raise PortalError(f"HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise PortalError(f"{type(e).__name__}: {e}") from e


def strip_html(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_greenhouse(slug: str) -> list[Job]:
    data = http_get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    jobs = []
    for j in data.get("jobs", []):
        published = parse_iso(j.get("first_published"))
        updated = parse_iso(j.get("updated_at"))
        posted = published or updated
        kind = "published" if published else ("updated" if updated else "unknown")
        jobs.append(Job(
            title=j.get("title", ""),
            company=slug,
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            source="greenhouse",
            posted=posted,
            date_kind=kind,
            description=strip_html(j.get("content", "")),
        ))
    return jobs


def fetch_lever(slug: str) -> list[Job]:
    data = None
    for host in ("api.lever.co", "api.eu.lever.co"):
        try:
            data = http_get_json(f"https://{host}/v0/postings/{slug}?mode=json")
            break
        except PortalNotFound:
            continue
    if data is None:
        raise PortalNotFound(slug)
    jobs = []
    for j in data:
        created = j.get("createdAt")
        posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None
        cats = j.get("categories") or {}
        job = Job(
            title=j.get("text", ""),
            company=slug,
            location=cats.get("location", "") or "",
            url=j.get("hostedUrl", ""),
            source="lever",
            posted=posted,
            date_kind="published" if posted else "unknown",
            description=j.get("descriptionPlain") or strip_html(j.get("description", "")),
        )
        if "intern" in (cats.get("commitment", "") or "").lower():
            job.job_type = "internship"
        jobs.append(job)
    return jobs


ADAPTERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever}


# --------------------------------------------------------------------------
# 5. Filtering + ranking
# --------------------------------------------------------------------------

def filter_jobs(jobs: list[Job], role: str, max_exp: int, days: int,
                include_internships: bool = True, now: datetime | None = None) -> list[Job]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    kept = []
    for job in jobs:
        m = match_title(job.title, role)
        if not m:
            continue
        if "intern" in norm(job.title):
            job.job_type = "internship"
        if job.job_type == "internship" and not include_internships:
            continue
        exp = classify_experience(job.title, job.description, max_exp)
        if exp == "reject":
            continue
        if job.posted is not None and job.posted < cutoff:
            continue
        job.match, job.experience = m, exp
        kept.append(job)

    far_past = datetime.min.replace(tzinfo=timezone.utc)
    kept.sort(key=lambda j: (
        j.match != "exact",                       # exact first
        j.posted is None,                         # known dates before unknown
        -(j.posted or far_past).timestamp(),      # newest first
    ))
    return kept


# --------------------------------------------------------------------------
# 6. Search one company -> status + jobs
# --------------------------------------------------------------------------

def search_company(slug: str, role: str, max_exp: int, days: int,
                   include_internships: bool = True) -> dict:
    all_jobs, reached, errors = [], False, []
    for name, fetch in ADAPTERS.items():
        try:
            all_jobs.extend(fetch(slug))
            reached = True
        except PortalNotFound:
            continue
        except PortalError as e:
            errors.append(f"{name}: {e}")
    if not reached:
        reason = "; ".join(errors) if errors else "not found on Greenhouse or Lever (may use Workday or a custom portal)"
        return {"company": slug, "status": "unreachable", "reason": reason, "jobs": []}
    matches = filter_jobs(all_jobs, role, max_exp, days, include_internships)
    status = "found" if matches else "no_role"
    return {"company": slug, "status": status, "scanned": len(all_jobs), "jobs": matches}


# --------------------------------------------------------------------------
# 7. Output
# --------------------------------------------------------------------------

def age_text(job: Job) -> str:
    if job.posted is None:
        return "date unknown"
    hours = (datetime.now(timezone.utc) - job.posted).total_seconds() / 3600
    label = f"{int(hours)}h ago" if hours < 48 else f"{int(hours // 24)}d ago"
    return f"{label} ({job.date_kind})"


def print_report(result: dict, role: str, days: int) -> None:
    co = result["company"]
    if result["status"] == "unreachable":
        print(f"\n[X] {co}: could not reach portal - {result['reason']}")
        return
    if result["status"] == "no_role":
        print(f"\n[-] {co}: portal reached ({result['scanned']} jobs scanned), "
              f"no '{role}' role posted in the last {days} days")
        return
    print(f"\n[OK] {co}: {len(result['jobs'])} match(es) from {result['scanned']} jobs scanned")
    for j in result["jobs"]:
        print(f"   * {j.title}  [{j.match}] [{j.job_type}] [exp: {j.experience}]")
        print(f"     {j.location or 'location n/a'} | {age_text(j)} | {j.source}")
        print(f"     {j.url}")


def main() -> int:
    p = argparse.ArgumentParser(description="Fresher-focused career portal job finder")
    p.add_argument("--company", action="append", required=True, help="company slug (repeatable)")
    p.add_argument("--role", required=True, help='e.g. "Software Engineer"')
    p.add_argument("--max-exp", type=int, default=2, help="max years of experience (default 2)")
    p.add_argument("--days", type=int, default=3, help="only jobs posted within N days (default 3)")
    p.add_argument("--no-internships", action="store_true")
    p.add_argument("--json", action="store_true", help="print JSON instead of a report")
    args = p.parse_args()

    results = [search_company(c, args.role, args.max_exp, args.days, not args.no_internships)
               for c in args.company]
    if args.json:
        def default(o):
            return o.isoformat() if isinstance(o, datetime) else asdict(o)
        print(json.dumps(results, default=default, indent=2))
    else:
        for r in results:
            print_report(r, args.role, args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
