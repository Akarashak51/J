"""
triage_companies.py  -  STEP 7
Sort a big list of companies (like your link_check_results.csv) into buckets, and find out
which ones the job agent can actually search.

For each company it looks for evidence of the hiring portal:
  1. the link itself (e.g. the link already points to myworkdayjobs.com)
  2. the careers page (fetches it and looks for Workday / Greenhouse / Lever / other portal links)
  3. slug probing (tries likely names on the Greenhouse and Lever public APIs)

Buckets in the report:
  ready          supported portal found AND the connection test passed   -> goes into ready.json
  guess          a Greenhouse/Lever board with a matching name or jobs, but not certain -> check by hand
  other_ats      a portal the agent cannot read yet (Ashby, SmartRecruiters, Keka, ...) -> shows which adapters to build
  needs_attention  supported portal found but incomplete (e.g. Workday link without its site name)
  check_failed   supported portal found but the connection test failed
  custom_alive   page loads, no known portal found (custom career site)
  blocked        site refuses scripts (bot protection) and nothing else was found
  dead           link is broken / domain gone / timed out -> needs a new link

Usage:
  python triage_companies.py --limit 30            (try a small batch first)
  python triage_companies.py                       (all rows; safe to stop and restart)
  python triage_companies.py --offline             (no internet: uses only the links + old status codes)
  python triage_companies.py --merge companies.json   (add the 'ready' ones to your company list)
"""

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import add_company as ac
import job_agent as ja

SUPPORTED = ("workday", "greenhouse", "lever", "ashby")

# Portals the agent cannot read yet. Counting them tells us which adapter to build next.
UNSUPPORTED_PATTERNS = [
    ("smartrecruiters", r"smartrecruiters\.com/[A-Za-z0-9_.-]+"),
    ("icims", r"[a-z0-9-]+\.icims\.com"),
    ("taleo", r"[a-z0-9-]+\.taleo\.net"),
    ("successfactors", r"[a-z0-9.-]*(?:successfactors|sapsf)\.(?:com|eu)"),
    ("oracle-cloud", r"[a-z0-9.-]+\.oraclecloud\.com/hcmUI"),
    ("darwinbox", r"[a-z0-9-]+\.darwinbox\.(?:in|com)"),
    ("keka", r"[a-z0-9-]+\.keka\.com"),
    ("zoho-recruit", r"[a-z0-9-]+\.zohorecruit\.(?:com|in|eu)"),
    ("workable", r"apply\.workable\.com/[A-Za-z0-9_.-]+"),
    ("bamboohr", r"[a-z0-9-]+\.bamboohr\.com"),
    ("recruitee", r"[a-z0-9-]+\.recruitee\.com"),
    ("freshteam", r"[a-z0-9-]+\.freshteam\.com"),
    ("jobvite", r"jobs\.jobvite\.com/[A-Za-z0-9_.-]+"),
    ("personio", r"[a-z0-9-]+\.jobs\.personio\.(?:de|com)"),
    ("teamtailor", r"[a-z0-9-]+\.teamtailor\.com"),
    ("pinpoint", r"[a-z0-9-]+\.pinpointhq\.com"),
]

WORKDAY_RE = re.compile(r"https?://[a-z0-9-]+\.wd\d{1,3}\.myworkdayjobs\.com[^\s\"'<>\\)]*", re.I)
GREENHOUSE_RE = re.compile(
    r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)
ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)", re.I)
LEVER_RE = re.compile(r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)", re.I)
BAD_SLUGS = {"embed", "js", "jobs", "v1", "api", "assets", "vanity", "static", "login"}
BAD_WORKDAY_SITES = {"wday", "assets", "login", "sitemap.xml", "favicon.ico"}

STOP_WORDS = {"india", "indian", "technologies", "technology", "tech", "pvt", "ltd", "limited", "inc",
              "llc", "labs", "lab", "group", "solutions", "systems", "software", "services", "the", "and",
              "private", "corp", "corporation", "company", "co", "global", "digital"}

BLOCKED_STATUSES = {401, 403, 406, 429, 503, 202}
HOST_GATE = threading.BoundedSemaphore(4)       # at most 4 simultaneous calls to the shared API hosts
GATE_DELAY = 0.15


# ----------------------------------------------------------------------------
# 1. Spot a hiring portal in a link or in page HTML
# ----------------------------------------------------------------------------

def find_ats(text: str) -> list[dict]:
    """All portal hits in the text, in the order they appear. Each hit:
       {"ats", "entry_url" (supported & complete) | None, "complete": bool}"""
    hits = []
    for m in WORKDAY_RE.finditer(text):
        url = re.split(r"[&?#]", m.group(0))[0].rstrip(".,;")
        d = ja.detect_from_url(url)
        if d is None:
            hits.append((m.start(), {"ats": "workday", "entry_url": None, "complete": False}))
        elif d["site"].lower() in BAD_WORKDAY_SITES:     # e.g. an API address: Workday, but site name unknown
            hits.append((m.start(), {"ats": "workday", "entry_url": None, "complete": False}))
        else:
            hits.append((m.start(), {"ats": "workday", "complete": True,
                                     "entry_url": f"https://{d['host']}/{d['site']}"}))
    for m in GREENHOUSE_RE.finditer(text):
        slug = m.group(2)
        if slug.lower() in BAD_SLUGS:
            continue
        hits.append((m.start(), {"ats": "greenhouse", "complete": True,
                                 "entry_url": f"https://boards.greenhouse.io/{slug}"}))
    for m in LEVER_RE.finditer(text):
        slug = m.group(1)
        if slug.lower() in BAD_SLUGS:
            continue
        hits.append((m.start(), {"ats": "lever", "complete": True,
                                 "entry_url": f"https://jobs.lever.co/{slug}"}))
    for m in ASHBY_RE.finditer(text):
        slug = m.group(1)
        if slug.lower() in BAD_SLUGS:
            continue
        hits.append((m.start(), {"ats": "ashby", "complete": True,
                                 "entry_url": f"https://jobs.ashbyhq.com/{slug}"}))
    for name, pattern in UNSUPPORTED_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            hits.append((m.start(), {"ats": name, "entry_url": None, "complete": False}))
            break
    hits.sort(key=lambda h: h[0])
    seen, out = set(), []
    for _, hit in hits:
        key = (hit["ats"], hit["entry_url"])
        if key not in seen:
            seen.add(key)
            out.append(hit)
    return out


def choose(hits: list[dict]) -> dict | None:
    """Best hit: supported & complete first, then an incomplete Workday, then any other portal."""
    for hit in hits:
        if hit["ats"] in SUPPORTED and hit["complete"]:
            return hit
    for hit in hits:
        if hit["ats"] == "workday":
            return hit
    return hits[0] if hits else None


# ----------------------------------------------------------------------------
# 2. Fetch a careers page (honest user agent; no tricks around bot protection)
# ----------------------------------------------------------------------------

def fetch_page(url: str, timeout: int = 12, max_bytes: int = 1_500_000) -> dict:
    """Never raises. Returns {"ok", "status", "final", "text", "error"}."""
    req = urllib.request.Request(url, headers={"User-Agent": ja.USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            charset = resp.headers.get_content_charset() or "utf-8"
            return {"ok": True, "status": resp.status, "final": resp.geturl(),
                    "text": raw.decode(charset, errors="replace"), "error": ""}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "final": url, "text": "", "error": f"HTTP {e.code}"}
    except Exception as e:  # DNS, SSL, timeouts, resets ...
        return {"ok": False, "status": None, "final": url, "text": "", "error": f"{type(e).__name__}"}


def url_variants(url: str, final: str = "") -> list[str]:
    """The link itself, the same link with www toggled, and the final link from the old check."""
    out = [url]
    p = urlparse(url)
    alt_host = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
    out.append(p._replace(netloc=alt_host).geturl())
    if final and final not in out:
        out.append(final)
    return out


def fetch_best(url: str, final: str = "") -> dict:
    last = {"ok": False, "status": None, "final": url, "text": "", "error": "no attempt"}
    for variant in url_variants(url, final):
        last = fetch_page(variant)
        if last["ok"] or last["status"] in BLOCKED_STATUSES:
            break
    return last


# ----------------------------------------------------------------------------
# 3. Guess the portal name from the company name (Greenhouse / Lever APIs)
# ----------------------------------------------------------------------------

def _clean_tokens(name: str) -> list[str]:
    base = re.sub(r"\(.*?\)", " ", name.lower())
    return re.sub(r"[^a-z0-9 ]+", " ", base).split()


def slug_candidates(name: str, url: str) -> list[str]:
    cands = []
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower().split(":")[0]
    labels = [x for x in host.split(".") if x]
    if len(labels) >= 2:
        core = labels[-2]
        if core in {"co", "com", "org", "net", "ac", "gov", "edu"} and len(labels) >= 3:
            core = labels[-3]
        cands.append(core)
    tokens = _clean_tokens(name)
    core_tokens = [t for t in tokens if t not in STOP_WORDS] or tokens
    if core_tokens:
        cands += ["".join(core_tokens), "-".join(core_tokens)]
    if tokens:
        cands += ["".join(tokens), "-".join(tokens)]
    out = []
    for c in cands:
        if len(c) >= 3 and c not in out:
            out.append(c)
    return out[:4]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


NAME_NOISE = {"inc", "incorporated", "corp", "corporation", "llc", "llp", "ltd", "limited", "co",
              "company", "plc", "careers", "career", "jobs", "job", "board", "hiring"}
NAME_TLDS = {"com", "io", "ai", "net", "org"}


def _strict_tokens(name: str) -> list[str]:
    toks = [x for x in _clean_tokens(name) if x not in NAME_NOISE]
    if len(toks) > 1 and toks[-1] in NAME_TLDS:
        toks = toks[:-1]
    return toks


def strict_name_match(company: str, board_name: str) -> bool:
    """True only when the names are the same once legal/filler words are removed.
    'Stripe' = 'Stripe, Inc.' and 'Rubrik' = 'Rubrik Job Board', but 'Wise' != 'Wise Worksite Field Sales'."""
    a, b = _strict_tokens(company), _strict_tokens(board_name)
    return bool(a) and "".join(a) == "".join(b)


def _gated_get(url: str):
    with HOST_GATE:
        time.sleep(GATE_DELAY)
        return ja.http_get_json(url)


def probe_slugs(company: str, url: str) -> dict | None:
    """Try Greenhouse then Lever for each candidate slug. Returns a hit dict or None."""
    for slug in slug_candidates(company, url):
        try:
            data = _gated_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            board_name = (data or {}).get("name", "") if isinstance(data, dict) else ""
            entry_url = f"https://boards.greenhouse.io/{slug}"
            if board_name and strict_name_match(company, board_name):
                return {"ats": "greenhouse", "entry_url": entry_url, "confirmed": True,
                        "detail": f"Greenhouse board named '{board_name}'"}
            return {"ats": "greenhouse", "entry_url": entry_url, "confirmed": False,
                    "detail": f"Greenhouse board '{board_name or slug}' exists - is it really {company}?"}
        except ja.PortalNotFound:
            pass
        except ja.PortalError:
            pass
        try:
            jobs = _gated_get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            if isinstance(jobs, list) and jobs:
                return {"ats": "lever", "entry_url": f"https://jobs.lever.co/{slug}", "confirmed": False,
                        "detail": f"Lever board '{slug}' has {len(jobs)} jobs - is it really {company}?"}
        except (ja.PortalNotFound, ja.PortalError):
            pass
    return None


# ----------------------------------------------------------------------------
# 4. Triage one company
# ----------------------------------------------------------------------------

def bucket_from_old_status(status: str, note: str) -> tuple[str, str]:
    """Offline fallback: use the status code from the earlier link check."""
    s = str(status).strip()
    if s in ("200", "301", "302", "307", "308"):
        return "custom_alive", "page loaded in the earlier check (not scanned for a portal)"
    if s in ("403", "406", "429", "202", "401", "503"):
        return "blocked", "site blocks scripts (page is probably fine in a browser)"
    if s in ("404", "410"):
        return "dead", "link not found (404/410)"
    if s == "ERROR":
        n = str(note)
        if "NameResolution" in n or "getaddrinfo" in n:
            return "dead", "domain does not exist"
        if "SSL" in n:
            return "dead", "SSL certificate problem (try the link with/without www)"
        if "Timed out" in n:
            return "dead", "timed out (retry; may just be slow)"
        return "dead", "connection failed"
    return "dead", f"server problem ({s}); retry later"


def triage_one(row: dict, probe: bool = True, offline: bool = False) -> dict:
    name, url = row["company_name"], row["original_url"]
    final = row.get("final_url") or ""
    rec = {"company": name, "original_url": url, "bucket": "", "ats": "", "entry_url": "",
           "evidence": "", "detail": ""}

    hits = find_ats(f"{url} {final}")
    evidence = "link" if hits else ""
    fetched = None
    if not hits and not offline:
        fetched = fetch_best(url, final)
        if fetched["ok"]:
            hits = find_ats(f"{fetched['final']} {fetched['text']}")
            evidence = "page" if hits else ""
    chosen = choose(hits)

    if chosen and chosen["ats"] in SUPPORTED and chosen["complete"]:
        entry = {"name": name, "url": chosen["entry_url"]}
        rec.update(ats=chosen["ats"], entry_url=chosen["entry_url"], evidence=evidence)
        if offline:
            rec.update(bucket="ready_unverified", detail="portal found in the link; connection not tested")
        else:
            ok, message = ac.check(entry)
            rec.update(bucket="ready" if ok else "check_failed", detail=message)
        return rec
    if chosen and chosen["ats"] == "workday":
        rec.update(bucket="needs_attention", ats="workday", evidence=evidence,
                   detail="Workday company, but the site name is missing from the link. Open its careers "
                          "page, copy the full link and use add_company.py")
        return rec
    if chosen:
        rec.update(bucket="other_ats", ats=chosen["ats"], evidence=evidence,
                   detail=f"uses {chosen['ats']} - the agent cannot read this portal yet")
        return rec

    if probe and not offline:
        hit = probe_slugs(name, url)
        if hit:
            rec.update(bucket="ready" if hit["confirmed"] else "guess", ats=hit["ats"],
                       entry_url=hit["entry_url"], evidence="probe-confirmed" if hit["confirmed"] else "probe-guess",
                       detail=hit["detail"])
            return rec

    if offline:
        bucket, detail = bucket_from_old_status(row.get("status_code", ""), row.get("note", ""))
    elif fetched and fetched["ok"]:
        bucket, detail = "custom_alive", "page loads, no known hiring portal found (custom career site)"
    elif fetched and fetched["status"] in BLOCKED_STATUSES:
        bucket, detail = "blocked", f"site blocks scripts ({fetched['error']})"
    else:
        err = fetched["error"] if fetched else "not fetched"
        bucket, detail = "dead", f"could not load the page ({err})"
    rec.update(bucket=bucket, detail=detail)
    return rec


# ----------------------------------------------------------------------------
# 5. Run everything (resumable) and write the outputs
# ----------------------------------------------------------------------------

def read_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    need = {"company_name", "original_url"}
    if not rows or not need <= set(rows[0]):
        raise ValueError(f"{path} must have columns: company_name, original_url (found: {list(rows[0]) if rows else 'none'})")
    return rows


def load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_json_atomic(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def run(rows: list[dict], cache_path: str, workers: int = 8, probe: bool = True, offline: bool = False,
        retry_dead: bool = False, retry_other: bool = False, progress=print) -> list[dict]:
    cache = {} if offline else load_cache(cache_path)
    todo = [r for r in rows if r["original_url"] not in cache
            or (retry_dead and cache[r["original_url"]]["bucket"] in ("dead", "check_failed"))
            or (retry_other and cache[r["original_url"]]["bucket"] == "other_ats")]
    progress(f"{len(rows)} companies | {len(rows) - len(todo)} already done | {len(todo)} to check")

    def work(row):
        try:
            return triage_one(row, probe=probe, offline=offline)
        except Exception as e:  # one bad row must never stop the run
            return {"company": row["company_name"], "original_url": row["original_url"], "bucket": "dead",
                    "ats": "", "entry_url": "", "evidence": "", "detail": f"unexpected error: {type(e).__name__}: {e}"}

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(work, r) for r in todo]
        for fut in as_completed(futures):
            rec = fut.result()
            cache[rec["original_url"]] = rec
            done += 1
            if not offline and done % 50 == 0:
                save_json_atomic(cache_path, cache)
                progress(f"  ...{done}/{len(todo)}")
    if not offline:
        save_json_atomic(cache_path, cache)
    return [cache[r["original_url"]] for r in rows if r["original_url"] in cache]


def _board_name(detail: str) -> str:
    m = re.search(r"board named '(.*)'", detail) or re.search(r"board '(.*?)' exists", detail)
    return m.group(1) if m else ""


def make_hint(rec: dict) -> str:
    detail = rec.get("detail", "")
    board = _board_name(detail)
    if rec.get("ats") == "greenhouse" and board:
        a = set(_clean_tokens(rec["company"])) - STOP_WORDS
        if not a & set(_clean_tokens(board)):
            return f"board is called '{board}' - name differs completely, verify before approving"
        return f"board is called '{board}' - name only partly matches, open the link and check"
    if rec.get("ats") == "lever":
        return "Lever boards show no company name - open the link and check the jobs"
    return "open the link and check"


def sample_jobs(rec: dict, n: int = 3) -> str:
    """A few real job titles from the guessed board, so you can tell if it is the right company."""
    d = ja.detect_from_url(rec.get("entry_url", ""))
    if not d or d["ats"] == "workday":
        return ""
    try:
        with HOST_GATE:
            time.sleep(GATE_DELAY)
            jobs = ja.ADAPTERS[d["ats"]](d["slug"])
    except (ja.PortalError, ja.PortalNotFound) as e:
        return f"could not load the board: {e}"
    return " | ".join(f"{j.title} ({j.location or 'location n/a'})" for j in jobs[:n]) or "the board has no jobs"


def enrich_guesses(cache: dict, workers: int = 4) -> int:
    """Fill sample_jobs for every guess that does not have it yet. Returns how many were filled."""
    todo = [r for r in cache.values() if r.get("bucket") == "guess" and not r.get("sample_jobs")]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for rec, text in zip(todo, pool.map(sample_jobs, todo)):
            rec["sample_jobs"] = text
    return len(todo)


def audit_cache(cache: dict) -> int:
    """Re-judge old 'probe-confirmed' Greenhouse matches with the strict rule. Returns how many were demoted."""
    demoted = 0
    for rec in cache.values():
        if rec.get("bucket") == "ready" and rec.get("evidence") == "probe-confirmed":
            board = _board_name(rec.get("detail", ""))
            if not board or not strict_name_match(rec["company"], board):
                rec.update(bucket="guess", evidence="probe-guess",
                           detail=f"Greenhouse board '{board}' exists - is it really {rec['company']}?")
                demoted += 1
    return demoted


def read_csv_rows(path: str) -> list[dict]:
    """Read a CSV that Excel may have saved as UTF-8 (with or without BOM) or as Windows-1252."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(path, encoding=encoding, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: cannot read the file's text encoding - save it again as CSV (or CSV UTF-8)")


def saved_answers(review_path: str) -> dict:
    """Your yes/no answers already typed in review.csv, so rewriting the file never erases them."""
    try:
        rows = read_csv_rows(review_path)
    except (OSError, ValueError):
        return {}
    return {r["original_url"]: r["approve"].strip() for r in rows
            if r.get("original_url") and (r.get("approve") or "").strip()}


def apply_review(cache: dict, review_path: str) -> tuple[int, int]:
    """Read review.csv: approve = yes -> ready, approve = no -> rejected. Returns (approved, rejected)."""
    approved = rejected = 0
    for row in read_csv_rows(review_path):
        rec = cache.get(row.get("original_url", ""))
        answer = (row.get("approve") or "").strip().lower()
        if rec is None or rec.get("bucket") not in ("guess", "rejected"):
            continue
        if answer in ("y", "yes", "1", "true") and rec["bucket"] != "ready":
            rec.update(bucket="ready", evidence="manual", detail="approved by you in review.csv")
            approved += 1
        elif answer in ("n", "no", "0", "false") and rec["bucket"] != "rejected":
            rec.update(bucket="rejected", detail="rejected by you in review.csv")
            rejected += 1
    return approved, rejected


def _write_csv_safely(path: str, fieldnames: list, rows: list, locked: list) -> None:
    """Write a CSV. If another program (usually Excel) has it open, remember that instead of crashing."""
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    except PermissionError:
        locked.append(os.path.basename(path))


def write_outputs(records: list[dict], out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    locked: list = []
    fields = ["company", "bucket", "ats", "entry_url", "evidence", "detail", "original_url"]

    # ready.json first: it is what the merge needs, and it must not depend on any CSV being closed
    ready, seen_urls = [], set()
    for r in records:
        if r["bucket"] in ("ready", "ready_unverified") and r["entry_url"] not in seen_urls:
            seen_urls.add(r["entry_url"])                    # two companies sharing one board count once
            ready.append({"name": r["company"], "url": r["entry_url"]})
    try:
        save_json_atomic(os.path.join(out_dir, "ready.json"), ready)
    except PermissionError:
        locked.append("ready.json")

    review_path = os.path.join(out_dir, "review.csv")
    answers = saved_answers(review_path)                     # read BEFORE rewriting, so answers survive
    review_rows = [{**r, "hint": make_hint(r), "approve": answers.get(r["original_url"], "")}
                   for r in records if r["bucket"] == "guess"]
    review_fields = ["company", "ats", "entry_url", "sample_jobs", "detail", "hint", "approve", "original_url"]

    _write_csv_safely(os.path.join(out_dir, "report.csv"), fields, records, locked)
    _write_csv_safely(os.path.join(out_dir, "guesses_to_confirm.csv"), fields,
                      [r for r in records if r["bucket"] == "guess"], locked)
    _write_csv_safely(os.path.join(out_dir, "needs_new_link.csv"), fields,
                      [r for r in records if r["bucket"] == "dead"], locked)
    _write_csv_safely(review_path, review_fields, review_rows, locked)
    return {"ready": ready, "locked": locked}


def warn_if_locked(out: dict) -> None:
    if out.get("locked"):
        print("\nWARNING: could not update " + ", ".join(out["locked"]) + " because another program has it open "
              "(usually Excel). Close it and run the same command again. Nothing you typed in review.csv was lost.")


def print_summary(records: list[dict]) -> None:
    counts = Counter(r["bucket"] for r in records)
    print(f"\nResults for {len(records)} companies:")
    labels = {"ready": "ready to search", "ready_unverified": "ready (connection not tested)",
              "guess": "guess - confirm in review.csv", "rejected": "rejected by you", "other_ats": "portal not supported yet",
              "needs_attention": "supported portal, link incomplete", "check_failed": "portal found, test failed",
              "custom_alive": "custom career site", "blocked": "blocks scripts", "dead": "broken / needs new link"}
    for bucket, label in labels.items():
        if counts.get(bucket):
            print(f"  {counts[bucket]:5d}  {label}")
    others = Counter(r["ats"] for r in records if r["bucket"] == "other_ats")
    if others:
        print("\nPortals the agent cannot read yet (most common first):")
        for ats, n in others.most_common(8):
            print(f"  {n:5d}  {ats}")


def merge_ready(out: dict, companies_path: str) -> int:
    added = dup = 0
    for entry in out["ready"]:
        try:
            result = ac.add_entry(companies_path, entry)
        except ValueError as e:
            print(f"Merge stopped: {e}")
            return 1
        added += result == "added"
        dup += result == "duplicate"
    print(f"Merged into {companies_path}: {added} added, {dup} already there")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Sort a big company list into buckets")
    p.add_argument("--csv", default="link_check_results.csv")
    p.add_argument("--out", default="triage_out")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0, help="only the first N companies (to test)")
    p.add_argument("--offline", action="store_true", help="no internet: use only links and old status codes")
    p.add_argument("--no-probe", action="store_true", help="skip the Greenhouse/Lever name guessing")
    p.add_argument("--retry-dead", action="store_true", help="re-check companies that failed last time")
    p.add_argument("--retry-other", action="store_true",
                   help="re-check companies filed as 'portal not supported yet' (after a new adapter is added)")
    p.add_argument("--audit", action="store_true",
                   help="no internet: re-judge saved results with the strict name rule and rewrite the outputs")
    p.add_argument("--enrich-review", action="store_true",
                   help="add a few real job titles next to each guess in review.csv (needs internet)")
    p.add_argument("--apply-review", metavar="REVIEW_CSV",
                   help="no internet: apply your yes/no answers from review.csv")
    p.add_argument("--merge", metavar="COMPANIES_JSON", help="add the 'ready' companies to this list")
    args = p.parse_args()

    cache_path = os.path.join(args.out, "triage_cache.json")
    if args.audit or args.apply_review or args.enrich_review:
        cache = load_cache(cache_path)
        if not cache:
            print(f"No saved results at {cache_path}. Run the full triage first.")
            return 1
        if args.audit:
            print(f"Audit: {audit_cache(cache)} weak name-only match(es) moved to review.csv")
        if args.enrich_review:
            print(f"Looked up sample jobs for {enrich_guesses(cache, args.workers)} guess(es)")
        if args.apply_review:
            try:
                yes, no = apply_review(cache, args.apply_review)
            except OSError as e:
                print(f"Cannot read {args.apply_review}: {e}")
                return 1
            print(f"Review applied: {yes} approved, {no} rejected")
        save_json_atomic(cache_path, cache)
        records = list(cache.values())
        out = write_outputs(records, args.out)
        print_summary(records)
        print(f"\nFiles in {args.out}/ : report.csv, ready.json, review.csv, needs_new_link.csv")
        warn_if_locked(out)
        return merge_ready(out, args.merge) if args.merge else 0

    try:
        rows = read_rows(args.csv)
    except (OSError, ValueError) as e:
        print(f"Cannot use {args.csv}: {e}")
        return 1
    if args.limit:
        rows = rows[:args.limit]
    os.makedirs(args.out, exist_ok=True)

    records = run(rows, cache_path, args.workers, probe=not args.no_probe,
                  offline=args.offline, retry_dead=args.retry_dead,
                  retry_other=args.retry_other)
    out = write_outputs(records, args.out)
    print_summary(records)
    print(f"\nFiles in {args.out}/ : report.csv, ready.json, review.csv, guesses_to_confirm.csv, needs_new_link.csv")
    warn_if_locked(out)

    if args.merge:
        return merge_ready(out, args.merge)
    return 0


if __name__ == "__main__":
    sys.exit(main())