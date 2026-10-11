"""Offline tests for run_batch.py  -  run:  python test_run_batch.py"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import job_agent as ja
import run_batch as rb

NOW = datetime.now(timezone.utc)


def fake_job(title, hours_ago=5):
    return ja.Job(title=title, company="x", location="Pune", url="https://x/1", source="test",
                  posted=NOW - timedelta(hours=hours_ago), date_kind="published",
                  description="0-2 years of experience", match="exact", experience="fit")


def fake_search_company(slug, role, max_exp, days, include_internships=True):
    if slug == "broken":
        raise RuntimeError("boom")
    if slug == "quiet":
        return {"company": slug, "status": "no_role", "scanned": 10, "jobs": []}
    return {"company": slug, "status": "found", "scanned": 10, "jobs": [fake_job(role)]}


def fake_search_url(url, role, max_exp, days, include_internships=True):
    return {"company": url, "status": "unreachable", "reason": "portal type not recognised", "jobs": []}


def test_batch_runs_every_company_and_role():
    ja.search_company, ja.search_url = fake_search_company, fake_search_url
    companies = [{"name": "A", "slug": "a"}, {"name": "Q", "slug": "quiet"},
                 {"name": "Bad", "slug": "broken"}, {"name": "U", "url": "https://example.com"},
                 {"name": "Empty"}]
    profile = {**rb.DEFAULT_PROFILE, "roles": ["Software Engineer", "Embedded Engineer"]}
    results = rb.run_batch(companies, profile, workers=3)
    assert len(results) == 10                                   # 5 companies x 2 roles
    by = {(r["company"], r["role"]): r for r in results}
    assert by[("A", "Embedded Engineer")]["status"] == "found"
    assert by[("Q", "Software Engineer")]["status"] == "no_role"
    assert by[("Bad", "Software Engineer")]["status"] == "unreachable"   # exception contained
    assert "boom" in by[("Bad", "Software Engineer")]["reason"]
    assert "slug" in by[("Empty", "Software Engineer")]["reason"]
    assert by[("U", "Software Engineer")]["status"] == "unreachable"


def test_results_json_is_valid():
    ja.search_company, ja.search_url = fake_search_company, fake_search_url
    profile = dict(rb.DEFAULT_PROFILE)
    results = rb.run_batch([{"name": "A", "slug": "a"}], profile)
    data = rb.serialise(results, profile)
    text = json.dumps(data)                                     # must be serialisable
    back = json.loads(text)
    job = back["results"][0]["jobs"][0]
    assert job["title"] == "Software Engineer" and job["posted"] and job["age_text"]
    assert back["profile"]["days"] == 3 and "generated_at" in back


def test_sample_config_files_are_valid():
    here = os.path.dirname(os.path.abspath(__file__))
    comps = rb.load_json(os.path.join(here, "companies.json"))
    prof = rb.load_json(os.path.join(here, "profile.json"))
    assert all(("slug" in c) or ("url" in c) for c in comps)
    assert prof["roles"] and prof["max_exp"] >= 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("All batch tests passed.")
