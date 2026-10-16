"""Offline tests for build_dashboard.py  -  run:  python test_build_dashboard.py"""
import json
import re

import build_dashboard as bd

DATA = {
    "generated_at": "2026-10-11T12:00:00+00:00",
    "profile": {"roles": ["Software Engineer"], "max_exp": 2, "days": 3, "include_internships": True},
    "results": [
        {"company": "Evil", "role": "Software Engineer", "status": "found", "scanned": 5, "jobs": [{
            "title": "</script><img src=x onerror=alert(1)> Engineer", "company": "x", "location": "Pune",
            "url": "javascript:alert(1)", "source": "test", "posted": "2026-10-11T10:00:00+00:00",
            "date_kind": "published", "description": "d", "job_type": "full-time", "match": "exact",
            "experience": "fit", "note": "", "age_text": "2h ago (published)"}]},
        {"company": "Down", "role": "Software Engineer", "status": "unreachable", "reason": "HTTP 403", "jobs": []},
        {"company": "Quiet", "role": "Software Engineer", "status": "no_role", "scanned": 9, "jobs": []},
    ],
}


def extract_payload(html):
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "data block missing"
    return m.group(1)


def test_payload_roundtrips_and_cannot_break_out():
    html = bd.build_html(DATA)
    payload = extract_payload(html)
    assert "</script" not in payload.lower()                 # job titles cannot close the tag
    assert json.loads(payload) == DATA                       # nothing lost or altered
    assert html.lower().count("</script>") == 2              # only the template's own two


def test_template_is_complete():
    html = bd.build_html(DATA)
    assert "__DATA__" not in html
    for needle in ("Could not check", "textContent", "replaceChildren", "https?:"):
        assert needle in html


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("All dashboard tests passed.")