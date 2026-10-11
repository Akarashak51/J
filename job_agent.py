"""
job_agent.py  -  STEP 1
Fresher-focused job finder for company career portals.

Supports : Greenhouse, Lever, Workday  (public JSON APIs, standard library only)
Rules    : 1. Title must match (exact or related role family)
           2. Experience must fit a fresher / 0-2 years (unclear = kept, tagged)
           3. Posted within the last N days (default 3); unknown date = kept, tagged

Usage:
  python job_agent.py --company stripe --role "Software Engineer"
  python job_agent.py --url "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite" --role "Software Engineer"
  python job_agent.py --company stripe --company figma --role "Software Engineer" --days 3
  python job_agent.py --company stripe --role "Embedded Engineer" --json
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

__version__ = "0.2.4"

USER_AGENT = "Mozilla/5.0 (JobAgent/0.1; personal fresher job search)"
TIMEOUT = 15
REQUEST_DELAY = 0.3   # seconds between Workday requests (be polite)

# --------------------------------------------------------------------------
# 1. Unified job schema
# --------------------------------------------------------------------------

@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str                 # "greenhouse" | "lever" | "workday"
    posted: datetime | None     # timezone-aware UTC, or None if unknown
    date_kind: str              # "published" | "updated" | "relative" | "unknown"
    description: str            # plain text
    job_type: str = "full-time"  # "full-time" | "internship"
    match: str = ""             # "exact" | "related"
    experience: str = ""        # "fit" | "unspecified"
    note: str = ""              # why something could not be read (shown in report)


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


def is_intern(text: str) -> bool:
    """Whole-word check, so 'Internal' or 'International' do not count."""
    return re.search(r"\b(intern|interns|internship|internships)\b", text.lower()) is not None


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
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
# Phrases right before a number that mean "company history / timeline", not a requirement.
NOT_A_REQUIREMENT_BEFORE = re.compile(
    r"(more than|over|for|past|last|within|every|after|next|in the)\s*$", re.IGNORECASE)
NOT_A_REQUIREMENT_AFTER = re.compile(
    r"\s*(vesting|plan|degree|program|contract|term|bond|ago|old|anniversary|warranty|lease)\b",
    re.IGNORECASE)
REQUIREMENT_HEADERS = re.compile(
    r"what we need to see|minimum qualifications|basic qualifications|qualifications|"
    r"requirements|what you.?ll need|what you need|who you are|required skills|you have",
    re.IGNORECASE)
END_OF_REQUIREMENTS = re.compile(
    r"ways to stand out|preferred qualifications|nice to have|bonus points|"
    r"what we offer|benefits|compensation|base salary", re.IGNORECASE)


def requirements_section(text: str) -> str:
    """The 'requirements' part of a posting if we can find it, else the whole text."""
    m = REQUIREMENT_HEADERS.search(text)
    if not m:
        return text
    rest = text[m.start():]
    stop = END_OF_REQUIREMENTS.search(rest, m.end() - m.start())
    return rest[:stop.start()] if stop else rest


def min_years_required(text: str) -> int | None:
    """Lowest 'N years' figure that looks like a requirement, or None."""
    section = requirements_section(text)
    lows = []
    for m in YEARS_RE.finditer(section):
        before = section[max(0, m.start() - 20):m.start()]
        after = section[m.end():m.end() + 60]
        mentions_experience = "experience" in after.lower()
        if NOT_A_REQUIREMENT_BEFORE.search(before) and not mentions_experience:
            continue                      # e.g. "for more than 25 years"
        if NOT_A_REQUIREMENT_AFTER.match(section, m.end()):
            continue                      # e.g. "4 year vesting plan", "4 year degree"
        lows.append(int(m.group(1)))
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

class JobList(list[Job]):
    """list of Job that also remembers how many postings the portal returned in total."""
    seen: int = 0


class PortalNotFound(Exception):
    """Slug does not exist on this ATS (HTTP 404)."""


class PortalError(Exception):
    """Network failure, timeout, blocked, bad response."""


def _open_json(req: urllib.request.Request, url: str):
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PortalNotFound(url) from e
        raise PortalError(f"HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise PortalError(f"{type(e).__name__}: {e}") from e


def http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    return _open_json(req, url)


def http_post_json(url: str, payload: dict):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                 "Content-Type": "application/json"})
    return _open_json(req, url)


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
        if is_intern(cats.get("commitment", "") or ""):
            job.job_type = "internship"
        jobs.append(job)
    return jobs


# ---- Workday ----------------------------------------------------------

WORKDAY_LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")


def detect_from_url(url: str) -> dict | None:
    """Work out the portal type from a careers page link."""
    u = urlparse(url.strip())
    host = u.netloc.lower()
    parts = [x for x in u.path.split("/") if x]
    if host.endswith(".myworkdayjobs.com"):
        site_parts = [x for x in parts if not WORKDAY_LOCALE_RE.match(x)]
        if not site_parts:
            return None
        return {"ats": "workday", "host": host, "tenant": host.split(".")[0], "site": site_parts[0]}
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io") and parts:
        return {"ats": "greenhouse", "slug": parts[0]}
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return {"ats": "lever", "slug": parts[0]}
    return None


def parse_workday_posted(text: str | None) -> int | None:
    """'Posted Today' -> 0, 'Posted 3 Days Ago' -> 3, '30+ Days Ago' -> 30."""
    t = (text or "").lower()
    if "today" in t:
        return 0
    if "yesterday" in t:
        return 1
    m = re.search(r"(\d+)\+?\s*days?", t)
    return int(m.group(1)) if m else None


def search_terms(role: str) -> list[str]:
    """Role itself plus a few related titles (Workday search is keyword based)."""
    terms = [role]
    for fam in families_for_query(norm(role)):
        for alias in ROLE_FAMILIES[fam]:
            if alias != norm(role) and alias not in terms and len(terms) < 4:
                terms.append(alias)
    return terms


def fetch_workday(host: str, tenant: str, site: str, role: str, days: int,
                  max_pages: int = 3, detail_cap: int = 15) -> "JobList":
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    now = datetime.now(timezone.utc)
    seen, cands = set(), []                       # cands: (job, externalPath)
    for q in search_terms(role):
        for page in range(max_pages):
            data = http_post_json(f"{base}/jobs", {
                "appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": q})
            postings = data.get("jobPostings", [])
            for item in postings:
                path = item.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                age = parse_workday_posted(item.get("postedOn"))
                posted = now - timedelta(days=age) if age is not None else None
                cands.append((Job(
                    title=item.get("title", ""), company=tenant,
                    location=item.get("locationsText", ""),
                    url=f"https://{host}/{site}{path}", source="workday",
                    posted=posted, date_kind="relative" if posted else "unknown",
                    description=""), path))
            if len(postings) < 20:
                break
            time.sleep(REQUEST_DELAY)
    # Only fetch full descriptions for jobs that already pass title + date.
    shortlist = [(j, path) for j, path in cands
                 if match_title(j.title, role)
                 and (j.posted is None or (now - j.posted).days <= days)]
    for job, path in shortlist[detail_cap:]:
        job.note = "description skipped (too many candidates)"
    for job, path in shortlist[:detail_cap]:
        time.sleep(REQUEST_DELAY)
        try:
            data = http_get_json(f"{base}{path}")
            info = data.get("jobPostingInfo", {}) or {}
            job.description = strip_html(info.get("jobDescription", ""))
            if not job.description:
                job.note = (f"detail response had no jobDescription "
                            f"(top keys: {sorted(data)}, info keys: {sorted(info)})")
        except PortalNotFound:
            job.note = f"detail fetch failed: 404 for {base}{path}"
        except PortalError as e:
            job.note = f"detail fetch failed: {e}"
    job_list = JobList(j for j, _ in shortlist)
    job_list.seen = len(seen)
    return job_list


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
        if is_intern(job.title):
            job.job_type = "internship"
        if job.job_type == "internship" and not include_internships:
            continue
        exp = classify_experience(job.title, job.description, max_exp)
        if exp == "reject":
            continue
        if job.posted is not None:
            if job.date_kind == "relative":          # e.g. Workday "Posted 3 Days Ago"
                if (now - job.posted).days > days:
                    continue
            elif job.posted < cutoff:
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

def _run_search(label: str, fetchers: list, role: str, max_exp: int, days: int,
                include_internships: bool, not_found_msg: str) -> dict:
    all_jobs, reached, errors, scanned = [], False, [], 0
    for name, fetch in fetchers:
        try:
            got = fetch()
            all_jobs.extend(got)
            scanned += getattr(got, "seen", len(got))
            reached = True
        except PortalNotFound:
            continue
        except PortalError as e:
            errors.append(f"{name}: {e}")
    if not reached:
        return {"company": label, "status": "unreachable",
                "reason": "; ".join(errors) if errors else not_found_msg, "jobs": []}
    matches = filter_jobs(all_jobs, role, max_exp, days, include_internships)
    return {"company": label, "status": "found" if matches else "no_role",
            "scanned": scanned, "jobs": matches}


def search_company(slug: str, role: str, max_exp: int, days: int,
                   include_internships: bool = True) -> dict:
    """Try Greenhouse and Lever using a company slug."""
    fetchers = [(n, (lambda f=f: f(slug))) for n, f in ADAPTERS.items()]
    return _run_search(slug, fetchers, role, max_exp, days, include_internships,
                       "not found on Greenhouse or Lever - paste its careers page link with --url instead")


def search_url(url: str, role: str, max_exp: int, days: int,
               include_internships: bool = True) -> dict:
    """Detect the portal from a careers page link, then search it."""
    d = detect_from_url(url)
    if d is None:
        return {"company": url, "status": "unreachable", "jobs": [],
                "reason": "portal type not recognised (supported: Workday, Greenhouse, Lever links)"}
    if d["ats"] == "workday":
        label = f"{d['tenant']} (workday)"
        fetchers = [("workday", lambda: fetch_workday(d["host"], d["tenant"], d["site"], role, days))]
    else:
        label = f"{d['slug']} ({d['ats']})"
        fetchers = [(d["ats"], lambda: ADAPTERS[d["ats"]](d["slug"]))]
    return _run_search(label, fetchers, role, max_exp, days, include_internships,
                       "careers site not found - check the link")


# --------------------------------------------------------------------------
# 7. Output
# --------------------------------------------------------------------------

def age_text(job: Job) -> str:
    if job.posted is None:
        return "date unknown"
    hours = (datetime.now(timezone.utc) - job.posted).total_seconds() / 3600
    label = f"{int(hours)}h ago" if hours < 48 else f"{int(hours // 24)}d ago"
    return f"{label} ({job.date_kind})"


def print_report(result: dict, role: str, days: int, verbose: bool = False) -> None:
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
        exp = j.experience
        if exp == "unspecified" and not j.description:
            exp += ", description not read"
        print(f"   * {j.title}  [{j.match}] [{j.job_type}] [exp: {exp}]")
        print(f"     {j.location or 'location n/a'} | {age_text(j)} | {j.source}")
        print(f"     {j.url}")
        if j.note:
            print(f"     note: {j.note}")
        if verbose:
            yrs = min_years_required(f"{j.title}. {j.description}")
            print(f"     debug: description {len(j.description)} chars | "
                  f"years found: {yrs if yrs is not None else 'none'}")


def main() -> int:
    p = argparse.ArgumentParser(description="Fresher-focused career portal job finder")
    p.add_argument("--company", action="append", default=[], help="company slug, e.g. stripe (repeatable)")
    p.add_argument("--url", action="append", default=[], help="careers page link: Workday/Greenhouse/Lever (repeatable)")
    p.add_argument("--role", required=True, help='e.g. "Software Engineer"')
    p.add_argument("--max-exp", type=int, default=2, help="max years of experience (default 2)")
    p.add_argument("--days", type=int, default=3, help="only jobs posted within N days (default 3)")
    p.add_argument("--no-internships", action="store_true")
    p.add_argument("--json", action="store_true", help="print JSON instead of a report")
    p.add_argument("--verbose", action="store_true", help="show why each job was classified")
    args = p.parse_args()
    if not args.company and not args.url:
        p.error("give at least one --company or --url")

    inc = not args.no_internships
    results = [search_company(c, args.role, args.max_exp, args.days, inc) for c in args.company]
    results += [search_url(u, args.role, args.max_exp, args.days, inc) for u in args.url]
    if args.json:
        def default(o):
            return o.isoformat() if isinstance(o, datetime) else asdict(o)
        print(json.dumps(results, default=default, indent=2))
    else:
        print(f"job_agent v{__version__}")
        for r in results:
            print_report(r, args.role, args.days, args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
    