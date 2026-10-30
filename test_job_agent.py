"""Offline tests for job_agent.py  -  run:  python test_job_agent.py"""
from datetime import datetime, timedelta, timezone

import job_agent as ja

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def mk(title, desc="", hours_ago: int | None = 10, job_type="full-time"):
    posted = None if hours_ago is None else NOW - timedelta(hours=hours_ago)
    return ja.Job(title=title, company="x", location="", url="u", source="test",
                  posted=posted, date_kind="published" if posted else "unknown",
                  description=desc, job_type=job_type)


def test_title_matching():
    q = "Software Engineer"
    assert ja.match_title("Software Engineer", q) == "exact"
    assert ja.match_title("Software Engineer, Backend", q) == "exact"
    assert ja.match_title("Full Stack Developer", q) == "related"
    assert ja.match_title("Software Developer", q) == "related"
    assert ja.match_title("SDE 1", q) == "related"
    assert ja.match_title("Senior Software Engineer", q) == ""
    assert ja.match_title("Marketing Manager", q) == ""
    assert ja.match_title("Staff Software Engineer", q) == ""
    # word boundary: "sde" must not match inside another word
    assert ja.match_title("Wisdeman Analyst", q) == ""


def test_families_do_not_leak_into_each_other():
    for title in ("Hardware Engineer", "Firmware Engineer", "IoT Engineer", "Embedded Engineer"):
        assert ja.match_title(title, "Software Engineer") == "", title
    assert ja.match_title("Embedded Software Engineer", "Software Engineer") == "exact"
    assert ja.match_title("Embedded Software Engineer", "Embedded Engineer") == "related"
    assert ja.match_title("Full Stack Developer", "Embedded Engineer") == ""


def test_embedded_family():
    assert ja.match_title("Firmware Engineer", "Embedded Engineer") == "related"
    assert ja.match_title("IoT Engineer", "Embedded Engineer") == "related"


def test_experience():
    f = lambda t, d: ja.classify_experience(t, d, 2)
    assert f("Software Engineer", "3+ years of experience required") == "reject"
    assert f("Software Engineer", "5 years experience in Python") == "reject"
    assert f("Software Engineer", "0-2 years of experience") == "fit"
    assert f("Software Engineer", "1 year of experience preferred") == "fit"
    assert f("Software Engineer", "We love building things") == "unspecified"
    assert f("Software Engineer - New Grad", "") == "fit"
    assert f("Software Engineer Intern", "") == "fit"
    assert f("Senior Software Engineer", "") == "reject"
    # company blurb must not trigger rejection
    assert f("Software Engineer", "We have been in business for 20 years.") == "unspecified"


def test_freshness_and_ranking():
    jobs = [
        mk("Software Engineer", hours_ago=100),               # too old -> dropped
        mk("Software Engineer", hours_ago=20),                # exact, fresh
        mk("Full Stack Developer", hours_ago=5),              # related, fresher
        mk("Software Developer", hours_ago=None),             # related, date unknown -> kept
        mk("Software Engineer Intern", hours_ago=30),         # internship
    ]
    out = ja.filter_jobs(jobs, "Software Engineer", 2, 3, now=NOW)
    titles = [j.title for j in out]
    assert len(out) == 4
    assert out[0].match == "exact"                            # exact before related
    assert titles[-1] == "Software Developer"                 # unknown date last in its group
    assert any(j.job_type == "internship" for j in out)


def test_no_internships_flag():
    jobs = [mk("Software Engineer Intern", hours_ago=5), mk("Software Engineer", hours_ago=5)]
    out = ja.filter_jobs(jobs, "Software Engineer", 2, 3, include_internships=False, now=NOW)
    assert [j.title for j in out] == ["Software Engineer"]


def test_experience_long_phrasing():
    f = lambda d: ja.classify_experience("Software Engineer", d, 2)
    assert f("4+ years of professional software development experience required") == "reject"
    assert f("Minimum of 4 years of hands-on experience with C++") == "reject"
    assert f("Experience: 5+ years in distributed systems") == "reject"
    assert f("Bachelor's degree and 4+ years of work experience") == "reject"


NVIDIA_JD = (
    "NVIDIA has been transforming computer graphics for more than 25 years. "
    "What You'll Be Doing: Build workflow definitions. "
    "What We Need To See: BS/MS in Computer Science, or equivalent experience. "
    "4+ years developing production software, or equivalent demonstrated scope. "
    "Strong programming in Go, Python, C++. "
    "Ways To Stand Out From The Crowd: 1 year of Rust. Applications accepted until October 12.")


def test_real_nvidia_posting_is_rejected():
    assert ja.min_years_required(NVIDIA_JD) == 4
    assert ja.classify_experience("Software Engineer, Workflow Systems", NVIDIA_JD, 2) == "reject"
    assert ja.classify_experience("Software Engineer, Workflow Systems", NVIDIA_JD, 4) != "reject"


def test_company_history_is_not_a_requirement():
    jd = "We have grown for more than 25 years and offer a 4 year vesting plan after the first year."
    assert ja.classify_experience("Software Engineer", jd, 2) == "unspecified"
    jd2 = "Over 5 years of experience building services is required."
    assert ja.classify_experience("Software Engineer", jd2, 2) == "reject"


def test_workday_detail_failure_is_reported():
    ja.REQUEST_DELAY = 0

    def fake_post(url, payload):
        if payload["offset"] > 0:
            return {"jobPostings": []}
        return {"jobPostings": [{"title": "Software Engineer", "externalPath": "/job/A/X_R1",
                                 "postedOn": "Posted Today", "locationsText": "A"}]}

    def failing_get(url):
        raise ja.PortalError("HTTP 403 from " + url)

    ja.http_post_json, ja.http_get_json = fake_post, failing_get
    jobs = ja.fetch_workday("x.wd1.myworkdayjobs.com", "x", "Site", "Software Engineer", 3)
    assert len(jobs) == 1 and "HTTP 403" in jobs[0].note and jobs[0].description == ""

    ja.http_get_json = lambda url: {"jobPostingInfo": {"title": "x"}}
    jobs = ja.fetch_workday("x.wd1.myworkdayjobs.com", "x", "Site", "Software Engineer", 3)
    assert "no jobDescription" in jobs[0].note


def test_internal_is_not_internship():
    assert not ja.is_intern("Software Engineer, Internal Identity")
    assert not ja.is_intern("International Software Engineer")
    assert ja.is_intern("Software Engineer Intern")
    assert ja.is_intern("Summer Internship 2027")
    out = ja.filter_jobs([mk("Software Engineer, Internal Tools", hours_ago=5)],
                         "Software Engineer", 2, 3, now=NOW)
    assert out[0].job_type == "full-time"


def test_experience_rejects_in_filter():
    jobs = [mk("Software Engineer", desc="Requires 4+ years of experience", hours_ago=2)]
    assert ja.filter_jobs(jobs, "Software Engineer", 2, 3, now=NOW) == []


def test_detect_from_url():
    d = ja.detect_from_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/jobs")
    assert d == {"ats": "workday", "host": "nvidia.wd5.myworkdayjobs.com",
                 "tenant": "nvidia", "site": "NVIDIAExternalCareerSite"}
    d = ja.detect_from_url("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
    assert d is not None and d["site"] == "NVIDIAExternalCareerSite"
    assert ja.detect_from_url("https://boards.greenhouse.io/stripe") == {"ats": "greenhouse", "slug": "stripe"}
    assert ja.detect_from_url("https://jobs.lever.co/spotify/abc") == {"ats": "lever", "slug": "spotify"}
    assert ja.detect_from_url("https://example.com/careers") is None


def test_parse_workday_posted():
    assert ja.parse_workday_posted("Posted Today") == 0
    assert ja.parse_workday_posted("Posted Yesterday") == 1
    assert ja.parse_workday_posted("Posted 3 Days Ago") == 3
    assert ja.parse_workday_posted("Posted 30+ Days Ago") == 30
    assert ja.parse_workday_posted("") is None


def test_workday_fetch_with_mock():
    ja.REQUEST_DELAY = 0
    sent = []

    def fake_post(url, payload):
        sent.append(payload["searchText"])
        if payload["offset"] > 0:
            return {"jobPostings": []}
        return {"jobPostings": [
            {"title": "Software Engineer", "externalPath": "/job/Pune/SE_R1", "postedOn": "Posted Today", "locationsText": "Pune"},
            {"title": "Software Engineer", "externalPath": "/job/Pune/SE_R2", "postedOn": "Posted 10 Days Ago", "locationsText": "Pune"},
            {"title": "Senior Software Engineer", "externalPath": "/job/Pune/SE_R3", "postedOn": "Posted Today", "locationsText": "Pune"},
            {"title": "Software Developer", "externalPath": "/job/Pune/SD_R4", "postedOn": "Posted 2 Days Ago", "locationsText": "Pune"},
        ]}

    def fake_get(url):
        return {"jobPostingInfo": {"jobDescription": "<p>0-2 years of experience</p>"}}

    ja.http_post_json, ja.http_get_json = fake_post, fake_get
    jobs = ja.fetch_workday("x.wd1.myworkdayjobs.com", "x", "Site", "Software Engineer", 3)
    titles = sorted(j.title for j in jobs)
    assert titles == ["Software Developer", "Software Engineer"]      # old + senior dropped
    assert all(j.description.startswith("0-2 years") for j in jobs)
    assert all(j.url.startswith("https://x.wd1.myworkdayjobs.com/Site/job/") for j in jobs)
    assert jobs.seen == 4                                             # all postings counted, not just matches
    assert len(sent) > 1                                              # related terms searched too
    final = ja.filter_jobs(jobs, "Software Engineer", 2, 3)
    assert len(final) == 2 and all(j.experience == "fit" for j in final)


def test_ashby_adapter_and_url_detection():
    assert ja.detect_from_url("https://jobs.ashbyhq.com/openai") == {"ats": "ashby", "slug": "openai"}
    assert ja.detect_from_url("https://jobs.ashbyhq.com/openai/abc-123")["slug"] == "openai"
    payload = {"apiVersion": "1", "jobs": [
        {"title": "Software Engineer", "location": "Bengaluru", "isListed": True, "employmentType": "FullTime",
         "publishedAt": (NOW - timedelta(hours=5)).isoformat(), "jobUrl": "https://jobs.ashbyhq.com/x/1",
         "descriptionPlain": "0-2 years of experience"},
        {"title": "Software Engineer Intern", "location": "Remote", "isListed": True, "employmentType": "Intern",
         "publishedAt": (NOW - timedelta(hours=5)).isoformat(), "jobUrl": "https://jobs.ashbyhq.com/x/2",
         "descriptionHtml": "<p>Summer role</p>"},
        {"title": "Hidden Role", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/x/3"},
    ]}
    ja.http_get_json = lambda url: payload
    jobs = ja.fetch_ashby("x")
    assert [j.title for j in jobs] == ["Software Engineer", "Software Engineer Intern"]      # unlisted skipped
    assert jobs[1].job_type == "internship" and jobs[1].description == "Summer role"
    assert jobs[0].source == "ashby" and jobs[0].posted is not None
    out = ja.filter_jobs(jobs, "Software Engineer", 2, 3, now=NOW)
    assert len(out) == 2 and "ashby" in ja.ADAPTERS


STRIPE_JD = ("Who you are We are looking for someone who meets the minimum requirements to be considered for the role. "
             "If you meet these requirements, you are encouraged to apply. The preferred qualifications are a bonus, "
             "not a requirement. Minimum requirements 4+ years of experience in building, shipping, and maintaining "
             "backend software in a production environment Proficiency in Java or Ruby "
             "Preferred qualifications 2 years of Rust")
FIGMA_JD = ("We would love to hear from you if you have: Current enrollment as a 4th or 5th year senior PhD student "
            "pursuing a PhD in Computer Graphics, Media Processing, or a related field. This is a 12 or 16-week internship.")
NVIDIA_PERF_JD = ("NVIDIA has been transforming computer graphics for more than 25 years. What we need to see: BS or MS "
                  "degree in Computer Engineering, or related degree (or equivalent experience). 2+ years of relevant "
                  "computer architecture or SW development experience.")
NVIDIA_FAE_JD = ("What we need to see: BS or MS in Electrical Engineering or Computer Science or equivalent experience "
                 "2+ years of work-related experience in a high-tech electronics industry in an embedded design role")


def test_real_postings_from_the_first_dashboard_run():
    f = lambda title, jd: ja.classify_experience(title, jd, 2)
    assert ja.min_years_required(STRIPE_JD) == 4                          # 'preferred qualifications are a bonus' no longer hides it
    assert f("Software Engineer, Storage Integrations", STRIPE_JD) == "reject"
    assert f("PhD Intern, Software Engineer - Graphics (2027)", FIGMA_JD) == "reject"
    assert f("System Software Engineer - Performance", NVIDIA_PERF_JD) == "fit"
    assert f("Field Applications Engineer, Embedded Systems - NALA", NVIDIA_FAE_JD) == "fit"
    assert ja.min_years_required(NVIDIA_PERF_JD) == 2 and ja.min_years_required(NVIDIA_FAE_JD) == 2


def test_phd_rule_is_not_too_greedy():
    assert not ja.requires_phd("Software Engineer", "0-2 years of experience. PhD preferred.")
    assert ja.requires_phd("Research Intern (Ph.D.)", "")
    assert ja.requires_phd("Intern", "Open to a PhD student in machine learning")
    assert ja.classify_experience("Software Engineer", "0-2 years of experience. PhD preferred.", 2) == "fit"


def test_preferred_years_are_ignored_but_required_years_use_the_largest():
    assert ja.min_years_required("Minimum requirements 1 year of Python and 3 years of backend experience") == 3
    assert ja.min_years_required("Basic qualifications 1+ years of Java. Preferred qualifications 5+ years of Go") == 1
    assert ja.min_years_required("Ways to stand out: 4 years of Rust") is None


def test_exact_means_the_whole_role_phrase_is_in_the_title():
    assert ja.match_title("Embedded Engineer II", "Embedded Engineer") == "exact"
    assert ja.match_title("Field Applications Engineer, Embedded Systems - NALA", "Embedded Engineer") == "related"
    assert ja.match_title("Software Engineer, Storage Integrations", "Software Engineer") == "exact"


def test_kept_jobs_record_the_years_they_ask_for():
    j = mk("Software Engineer", desc=NVIDIA_PERF_JD, hours_ago=5)
    out = ja.filter_jobs([j], "Software Engineer", 2, 3, now=NOW)
    assert out[0].min_years == 2
    j2 = mk("Software Engineer", desc="We love building things", hours_ago=5)
    assert ja.filter_jobs([j2], "Software Engineer", 2, 3, now=NOW)[0].min_years is None


def test_filter_does_not_modify_shared_jobs():
    shared = mk("Software Engineer", desc="0-2 years of experience", hours_ago=5)
    ja.filter_jobs([shared], "Software Engineer", 2, 3, now=NOW)
    assert shared.match == "" and shared.experience == "" and shared.min_years is None   # the original is untouched
    first = ja.filter_jobs([shared], "Software Engineer", 2, 3, now=NOW)[0]
    second = ja.filter_jobs([shared], "Embedded Engineer", 2, 3, now=NOW)
    assert first.match == "exact" and second == []                                       # roles cannot overwrite each other


def test_board_is_downloaded_once_for_several_roles():
    calls = []

    def fresh(title):                    # dated relative to the REAL clock, because search_url uses the real clock
        return ja.Job(title=title, company="acme", location="", url="u", source="test",
                      posted=datetime.now(timezone.utc) - timedelta(hours=5), date_kind="published",
                      description="0-2 years of experience")

    def fake_board(slug):
        calls.append(slug)
        return [fresh("Software Engineer"), fresh("Embedded Engineer")]

    saved = dict(ja.ADAPTERS)
    ja.ADAPTERS.clear()
    ja.ADAPTERS["greenhouse"] = fake_board
    try:
        ja.clear_board_cache()
        a = ja.search_url("https://boards.greenhouse.io/acme", "Software Engineer", 2, 3)
        b = ja.search_url("https://boards.greenhouse.io/acme", "Embedded Engineer", 2, 3)
        assert calls == ["acme"]                                                          # one download for two roles
        assert [j.title for j in a["jobs"]] == ["Software Engineer"] and [j.title for j in b["jobs"]] == ["Embedded Engineer"]
        ja.clear_board_cache()
        ja.search_url("https://boards.greenhouse.io/acme", "Software Engineer", 2, 3)
        assert calls == ["acme", "acme"]                                                  # a new batch downloads again
    finally:
        ja.ADAPTERS.clear()
        ja.ADAPTERS.update(saved)
        ja.clear_board_cache()


def test_failed_board_is_remembered_within_a_batch():
    calls = []

    def broken(slug):
        calls.append(slug)
        raise ja.PortalError("HTTP 500")

    saved = dict(ja.ADAPTERS)
    ja.ADAPTERS.clear()
    ja.ADAPTERS["lever"] = broken
    try:
        ja.clear_board_cache()
        for role in ("Software Engineer", "Embedded Engineer"):
            res = ja.search_url("https://jobs.lever.co/acme", role, 2, 3)
            assert res["status"] == "unreachable" and "HTTP 500" in res["reason"]
        assert calls == ["acme"]
    finally:
        ja.ADAPTERS.clear()
        ja.ADAPTERS.update(saved)
        ja.clear_board_cache()


def test_relative_date_boundary():
    j = mk("Software Engineer", hours_ago=72)
    j.date_kind = "relative"
    j.posted = NOW - timedelta(days=3)
    assert len(ja.filter_jobs([j], "Software Engineer", 2, 3, now=NOW)) == 1
    j.posted = NOW - timedelta(days=4)
    assert ja.filter_jobs([j], "Software Engineer", 2, 3, now=NOW) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("All tests passed.")