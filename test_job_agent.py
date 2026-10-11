"""Offline tests for job_agent.py  -  run:  python test_job_agent.py"""
from datetime import datetime, timedelta, timezone

import job_agent as ja

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def mk(title, desc="", hours_ago=10, job_type="full-time"):
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


def test_experience_rejects_in_filter():
    jobs = [mk("Software Engineer", desc="Requires 4+ years of experience", hours_ago=2)]
    assert ja.filter_jobs(jobs, "Software Engineer", 2, 3, now=NOW) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("All tests passed.")
