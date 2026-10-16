"""Offline tests for triage_companies.py  -  run:  python test_triage_companies.py"""
import csv
import json
import os
import tempfile

import add_company as ac
import job_agent as ja
import triage_companies as tr

tr.GATE_DELAY = 0


def row(name, url, final="", status="200", note="OK"):
    return {"company_name": name, "original_url": url, "final_url": final, "status_code": status, "note": note}


def test_find_ats_in_html():
    html = ('<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/jobs">Jobs</a>'
            '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
            '<a href="https://jobs.lever.co/acme-inc">x</a> <a href="https://jobs.ashbyhq.com/acme">y</a>')
    hits = tr.find_ats(html)
    assert [h["ats"] for h in hits] == ["workday", "greenhouse", "lever", "ashby"]
    assert hits[0]["entry_url"] == "https://acme.wd5.myworkdayjobs.com/AcmeCareers"
    assert hits[1]["entry_url"] == "https://boards.greenhouse.io/acme"
    assert hits[2]["entry_url"] == "https://jobs.lever.co/acme-inc"
    assert tr.choose(hits)["ats"] == "workday"


def test_ashby_is_now_supported_in_triage():
    hit = tr.find_ats('<a href="https://jobs.ashbyhq.com/openai">Careers</a>')[0]
    assert hit["ats"] == "ashby" and hit["entry_url"] == "https://jobs.ashbyhq.com/openai" and hit["complete"]
    ac.check = lambda entry: (True, "reachable")
    tr.fetch_best = lambda url, final="": {"ok": True, "status": 200, "final": url, "error": "",
                                           "text": '<a href="https://jobs.ashbyhq.com/zeta">x</a>'}
    r = tr.triage_one(row("Zeta", "https://zeta.com/careers"))
    assert r["bucket"] == "ready" and r["ats"] == "ashby"


def test_retry_other_rechecks_only_other_ats():
    rows = [row("A", "https://a.com"), row("B", "https://b.com")]
    calls = []
    real = tr.triage_one
    tr.triage_one = lambda r, probe=True, offline=False: (calls.append(r["company_name"]) or
        {"company": r["company_name"], "original_url": r["original_url"], "bucket": "ready", "ats": "", "entry_url": "",
         "evidence": "", "detail": ""})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            json.dump({"https://a.com": {"company": "A", "original_url": "https://a.com", "bucket": "other_ats"},
                       "https://b.com": {"company": "B", "original_url": "https://b.com", "bucket": "custom_alive"}},
                      open(cache, "w"))
            tr.run(rows, cache, retry_other=True, progress=lambda *_: None)
    finally:
        tr.triage_one = real
    assert calls == ["A"]


def test_find_ats_ignores_noise_and_flags_incomplete_workday():
    assert tr.find_ats('<script src="https://boards.greenhouse.io/embed/job_board/js"></script>') == []
    bare = tr.find_ats("https://adobe.wd5.myworkdayjobs.com/")
    assert bare[0]["ats"] == "workday" and bare[0]["complete"] is False
    assert tr.find_ats("https://x.wd1.myworkdayjobs.com/wday/cxs/x/y")[0]["complete"] is False
    assert tr.find_ats("nothing here") == []


def test_slug_candidates_and_name_matching():
    c = tr.slug_candidates("Acko Insurance", "https://www.acko.com/careers")
    assert c[0] == "acko" and "ackoinsurance" in c and len(c) <= 4
    assert tr.slug_candidates("1mg (Tata Digital)", "https://www.1mg.com/careers")[0] == "1mg"
    assert tr.slug_candidates("Zomato", "https://www.zomato.co.in/x")[0] == "zomato"


def test_strict_name_match_accepts_same_company():
    m = tr.strict_name_match
    assert m("Stripe", "Stripe, Inc.") and m("Rubrik", "Rubrik Job Board") and m("HackerRank", "HackerRank Careers")
    assert m("Bill.com", "BILL") and m("Honeycomb", "Honeycomb.io") and m("Foundit", "Foundit (Monster)")
    assert m("Rocket Lab", "Rocket Lab Corporation") and m("Gusto", "Gusto, Inc.")
    assert m("Graviton Research Capital", "Graviton Research Capital LLP")


def test_strict_name_match_rejects_the_real_mistakes_from_the_first_scan():
    m = tr.strict_name_match
    for company, board in [("Wise (TransferWise)", "Wise Worksite Field Sales"),
                           ("BlueStone", "Bluestone Physician Services"),
                           ("Apollo.io", "Apollo Education Systems"),
                           ("Bethesda", "Bethesda Physical Therapy"),
                           ("McAfee", "McAfee Heating and Air Conditioning"),
                           ("Orca Security", "ORCA Service Technologies"),
                           ("Remote.com", "General Assembly Remote Jobs"),
                           ("Air India", "Air"), ("Pine Labs", "Pine"),
                           ("TCS", "Thornbury Community Services"), ("SAS Institute", "Superior Alarm Systems")]:
        assert not m(company, board), (company, board)


def test_triage_flows():
    ac.check = lambda entry: (True, "reachable")
    # 1. link already points to a supported portal
    r = tr.triage_one(row("Fractal", "https://fractal.ai/careers", "https://fractal.wd1.myworkdayjobs.com/Careers"))
    assert r["bucket"] == "ready" and r["evidence"] == "link" and r["ats"] == "workday"
    # 2. portal found inside the page
    tr.fetch_best = lambda url, final="": {"ok": True, "status": 200, "final": url, "error": "",
                                           "text": '<a href="https://jobs.lever.co/zeta">Apply</a>'}
    r = tr.triage_one(row("Zeta", "https://zeta.com/careers"))
    assert r["bucket"] == "ready" and r["evidence"] == "page" and r["entry_url"] == "https://jobs.lever.co/zeta"
    # 3. unsupported portal
    tr.fetch_best = lambda url, final="": {"ok": True, "status": 200, "final": url, "error": "",
                                           "text": '<a href="https://acme.keka.com/careers">Apply</a>'}
    r = tr.triage_one(row("Acme", "https://acme.com/careers"))
    assert r["bucket"] == "other_ats" and r["ats"] == "keka"
    # 4. bare Workday link
    r = tr.triage_one(row("Adobe", "https://adobe.wd5.myworkdayjobs.com", status="406"))
    assert r["bucket"] == "needs_attention"
    # 5. check fails -> check_failed, not silently ready
    ac.check = lambda entry: (False, "HTTP 403")
    r = tr.triage_one(row("Fractal", "https://fractal.ai/careers", "https://fractal.wd1.myworkdayjobs.com/Careers"))
    assert r["bucket"] == "check_failed" and "403" in r["detail"]


def test_probe_confirmed_guess_and_none():
    ac.check = lambda entry: (True, "ok")
    tr.fetch_best = lambda url, final="": {"ok": True, "status": 200, "final": url, "error": "", "text": "<html></html>"}

    def fake_get(url):
        if "boards-api.greenhouse.io/v1/boards/acko" in url:
            return {"name": "Acko"}
        if "boards-api.greenhouse.io/v1/boards/other" in url:
            return {"name": "Totally Different Co"}
        if "api.lever.co/v0/postings/leverco" in url:
            return [{"text": "Engineer"}]
        raise ja.PortalNotFound(url)

    ja.http_get_json = fake_get
    r = tr.triage_one(row("Acko", "https://www.acko.com/careers"))
    assert r["bucket"] == "ready" and r["evidence"] == "probe-confirmed"
    r = tr.triage_one(row("Acko Insurance", "https://www.acko.com/careers"))
    assert r["bucket"] == "guess"                                   # partial name = needs your eyes
    r = tr.triage_one(row("Other", "https://other.com/careers"))
    assert r["bucket"] == "guess" and "really Other" in r["detail"]
    r = tr.triage_one(row("Leverco", "https://leverco.com/careers"))
    assert r["bucket"] == "guess" and r["ats"] == "lever"
    r = tr.triage_one(row("Nobody", "https://nobody.com/careers"))
    assert r["bucket"] == "custom_alive"
    r = tr.triage_one(row("Nobody", "https://nobody.com/careers"), probe=False)
    assert r["bucket"] == "custom_alive"


def test_blocked_and_dead_live_results():
    ja.http_get_json = lambda url: (_ for _ in ()).throw(ja.PortalNotFound(url))
    tr.fetch_best = lambda url, final="": {"ok": False, "status": 403, "final": url, "text": "", "error": "HTTP 403"}
    assert tr.triage_one(row("A", "https://a.com/careers"))["bucket"] == "blocked"
    tr.fetch_best = lambda url, final="": {"ok": False, "status": None, "final": url, "text": "", "error": "URLError"}
    assert tr.triage_one(row("B", "https://b.com/careers"))["bucket"] == "dead"
    tr.fetch_best = lambda url, final="": {"ok": False, "status": 404, "final": url, "text": "", "error": "HTTP 404"}
    assert tr.triage_one(row("C", "https://c.com/careers"))["bucket"] == "dead"


def test_offline_mode_uses_old_status_codes():
    b = lambda s, n="": tr.triage_one(row("X", "https://x.com/careers", status=s, note=n), offline=True)["bucket"]
    assert b("200") == "custom_alive" and b("403") == "blocked" and b("404") == "dead"
    assert b("ERROR", "NameResolutionError") == "dead"
    r = tr.triage_one(row("Fractal", "https://fractal.ai/careers", "https://fractal.wd1.myworkdayjobs.com/Careers"),
                      offline=True)
    assert r["bucket"] == "ready_unverified"


def test_run_is_resumable_and_outputs_are_written():
    rows = [row("A", "https://a.com/careers", status="200"), row("B", "https://b.com/careers", status="404")]
    calls = []
    real = tr.triage_one

    def counting(r, probe=True, offline=False):
        calls.append(r["company_name"])
        return real(r, probe, offline)

    with tempfile.TemporaryDirectory() as tmp:
        cache = os.path.join(tmp, "cache.json")
        ja.http_get_json = lambda url: (_ for _ in ()).throw(ja.PortalNotFound(url))
        tr.fetch_best = lambda url, final="": {"ok": True, "status": 200, "final": url, "error": "", "text": ""}
        tr.triage_one = counting
        try:
            first = tr.run(rows, cache, workers=2, progress=lambda *_: None)
            assert sorted(calls) == ["A", "B"] and len(first) == 2
            calls.clear()
            tr.run(rows, cache, workers=2, progress=lambda *_: None)
            assert calls == []                                          # everything came from the cache
        finally:
            tr.triage_one = real
        out = tr.write_outputs(first + [{"company": "R", "bucket": "ready", "ats": "lever", "entry_url": "https://jobs.lever.co/r",
                                         "evidence": "page", "detail": "", "original_url": "u"}], tmp)
        assert json.load(open(os.path.join(tmp, "ready.json"))) == [{"name": "R", "url": "https://jobs.lever.co/r"}]
        assert os.path.exists(os.path.join(tmp, "report.csv")) and out["ready"]


def test_workday_link_stops_at_ampersand():
    hit = tr.find_ats('<a href="https://genpact.wd108.myworkdayjobs.com/External_Careers&amp;x=1">Jobs</a>')[0]
    assert hit["entry_url"] == "https://genpact.wd108.myworkdayjobs.com/External_Careers"
    hit = tr.find_ats("https://x.wd5.myworkdayjobs.com/Site?source=abc#top")[0]
    assert hit["entry_url"] == "https://x.wd5.myworkdayjobs.com/Site"


def rec(company, bucket, evidence="", detail="", ats="greenhouse", url="", orig=None):
    return {"company": company, "original_url": orig or f"https://{company}.com", "bucket": bucket, "ats": ats,
            "entry_url": url, "evidence": evidence, "detail": detail}


def test_audit_demotes_only_weak_probe_matches():
    cache = {
        "a": rec("Stripe", "ready", "probe-confirmed", "Greenhouse board named 'Stripe, Inc.'", url="u1", orig="a"),
        "b": rec("McAfee", "ready", "probe-confirmed", "Greenhouse board named 'McAfee Heating and Air Conditioning'", url="u2", orig="b"),
        "c": rec("Fractal", "ready", "page", "reachable", ats="workday", url="u3", orig="c"),
        "d": rec("Foo", "custom_alive", orig="d"),
    }
    assert tr.audit_cache(cache) == 1
    assert cache["a"]["bucket"] == "ready" and cache["c"]["bucket"] == "ready" and cache["d"]["bucket"] == "custom_alive"
    assert cache["b"]["bucket"] == "guess" and cache["b"]["evidence"] == "probe-guess"
    assert tr.audit_cache(cache) == 0                                # running it again changes nothing


def test_review_roundtrip_and_deduped_outputs():
    cache = {
        "g1": rec("CRED", "guess", "probe-guess", "Lever board 'cred' has 8 jobs", ats="lever", url="https://jobs.lever.co/cred", orig="g1"),
        "g2": rec("TCS", "guess", "probe-guess", "Greenhouse board 'Thornbury Community Services' exists - is it really TCS?", url="https://boards.greenhouse.io/tcs", orig="g2"),
        "g3": rec("Maybe", "guess", "probe-guess", "x", ats="lever", url="https://jobs.lever.co/maybe", orig="g3"),
        "r1": rec("SUSE", "ready", "page", "ok", ats="workday", url="https://suse.wd3.myworkdayjobs.com/J", orig="r1"),
        "r2": rec("Rancher SUSE", "ready", "page", "ok", ats="workday", url="https://suse.wd3.myworkdayjobs.com/J", orig="r2"),
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = tr.write_outputs(list(cache.values()), tmp)
        assert [e["name"] for e in out["ready"]] == ["SUSE"]                # shared board counted once
        rows = list(csv.DictReader(open(os.path.join(tmp, "review.csv"), encoding="utf-8")))
        assert [r["company"] for r in rows] == ["CRED", "TCS", "Maybe"] and rows[0]["approve"] == ""
        assert "differs completely" in rows[1]["hint"] and "Lever" in rows[0]["hint"]
        for r in rows:
            r["approve"] = {"CRED": "yes", "TCS": "NO"}.get(r["company"], "")
        with open(os.path.join(tmp, "review.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        assert tr.apply_review(cache, os.path.join(tmp, "review.csv")) == (1, 1)
        assert cache["g1"]["bucket"] == "ready" and cache["g1"]["evidence"] == "manual"
        assert cache["g2"]["bucket"] == "rejected" and cache["g3"]["bucket"] == "guess"   # blank = still undecided
        assert tr.apply_review(cache, os.path.join(tmp, "review.csv")) == (0, 0)          # safe to apply twice


def test_read_rows_checks_columns():
    with tempfile.TemporaryDirectory() as tmp:
        good, bad = os.path.join(tmp, "g.csv"), os.path.join(tmp, "b.csv")
        open(good, "w", encoding="utf-8-sig", newline="").write("company_name,original_url\nA,https://a.com\n")
        open(bad, "w", newline="").write("name,link\nA,https://a.com\n")
        assert tr.read_rows(good)[0]["company_name"] == "A"
        try:
            tr.read_rows(bad)
            raise AssertionError("should refuse")
        except ValueError as e:
            assert "company_name" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("All triage tests passed.")