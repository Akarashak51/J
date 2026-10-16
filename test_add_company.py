"""Offline tests for add_company.py  -  run:  python test_add_company.py"""
import json
import os
import tempfile

import job_agent as ja
import add_company as ac


def test_parse_target():
    e = ac.parse_target("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite")
    assert e == {"name": "Nvidia", "url": "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"}
    assert ac.parse_target("boards.greenhouse.io/stripe") == {"name": "Stripe", "url": "https://boards.greenhouse.io/stripe"}
    assert ac.parse_target("Stripe") == {"name": "Stripe", "slug": "stripe"}
    assert ac.parse_target("my-company", "My Co") == {"name": "My Co", "slug": "my-company"}
    assert ac.parse_target("https://www.example.com/careers")["name"] == "Example"


def test_check_workday_greenhouse_lever_and_unknown():
    ja.http_post_json = lambda url, payload: {"total": 132, "jobPostings": []}
    ok, msg = ac.check({"url": "https://x.wd5.myworkdayjobs.com/Site"})
    assert ok and "132" in msg
    ja.ADAPTERS["greenhouse"] = lambda slug: [1, 2, 3]
    ok, msg = ac.check({"url": "https://boards.greenhouse.io/stripe"})
    assert ok and "3 postings" in msg
    ok, msg = ac.check({"url": "https://example.com/careers"})
    assert not ok and "not recognised" in msg


def test_check_slug_cases():
    def missing(slug):
        raise ja.PortalNotFound(slug)

    def down(slug):
        raise ja.PortalError("HTTP 500")

    ja.ADAPTERS["greenhouse"], ja.ADAPTERS["lever"] = (lambda s: [1] * 5), missing
    ok, msg = ac.check({"slug": "a"})
    assert ok and "Greenhouse (5 postings)" in msg
    ja.ADAPTERS["greenhouse"], ja.ADAPTERS["lever"] = missing, missing
    ok, msg = ac.check({"slug": "a"})
    assert not ok and "paste its careers page link" in msg
    ja.ADAPTERS["greenhouse"], ja.ADAPTERS["lever"] = down, missing
    ok, msg = ac.check({"slug": "a"})
    assert not ok and "HTTP 500" in msg


def test_link_and_slug_for_same_company_are_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "companies.json")
        assert ac.add_entry(path, {"name": "Stripe", "url": "https://boards.greenhouse.io/stripe"}) == "added"
        assert ac.add_entry(path, {"name": "Stripe", "slug": "stripe"}) == "duplicate"
        assert ac.add_entry(path, {"name": "N", "url": "https://nvidia.wd5.myworkdayjobs.com/en-US/Site"}) == "added"
        assert ac.add_entry(path, {"name": "N", "url": "https://nvidia.wd5.myworkdayjobs.com/Site"}) == "duplicate"
        assert len(json.load(open(path))) == 2


def test_add_entry_dedupes_and_survives_bad_files():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "companies.json")
        assert ac.add_entry(path, {"name": "A", "slug": "a"}) == "added"          # file created
        assert ac.add_entry(path, {"name": "A again", "slug": "A"}) == "duplicate"
        assert ac.add_entry(path, {"name": "N", "url": "https://x.com/s/"}) == "added"
        assert ac.add_entry(path, {"name": "N2", "url": "https://X.com/s"}) == "duplicate"
        assert len(json.load(open(path))) == 2
        assert not os.path.exists(path + ".tmp")
        open(path, "w").write("{ not json")
        try:
            ac.add_entry(path, {"name": "B", "slug": "b"})
            raise AssertionError("should have refused")
        except ValueError as e:
            assert "not valid JSON" in str(e)
        assert open(path).read() == "{ not json"                                  # broken file left untouched


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("All add-company tests passed.")
