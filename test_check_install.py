"""Tests for check_install.py  -  run:  python test_check_install.py"""
import contextlib
import io
import os

import check_install as ci


def test_current_files_pass_and_old_versions_are_caught():
    here = os.path.dirname(os.path.abspath(__file__))
    cwd = os.getcwd()
    os.chdir(here)
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ci.main()
        assert code == 0 and "EVERYTHING IS UP TO DATE" in out.getvalue()
        import job_agent
        real = job_agent.__version__
        job_agent.__version__ = "0.2.4"                       # pretend the user still has the old file
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = ci.main()
            assert code == 1 and "REPLACE THESE FILES" in out.getvalue() and "[OLD] job_agent.py is v0.2.4" in out.getvalue()
        finally:
            job_agent.__version__ = real
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    test_current_files_pass_and_old_versions_are_caught()
    print("PASS test_current_files_pass_and_old_versions_are_caught")
    print("All install-check tests passed.")
