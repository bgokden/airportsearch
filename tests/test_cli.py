"""Tests for the airportsearch command-line interface."""
import io
from contextlib import redirect_stdout, redirect_stderr

from airportsearch.__main__ import main


def test_cli_basic_query():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["frankfurt", "-k", "3"])
    out = buf.getvalue()
    assert rc == 0
    assert "FRA" in out
    assert len(out.strip().splitlines()) <= 3


def test_cli_multiword_query():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["charles", "de", "gaulle"])
    assert rc == 0
    assert "CDG" in buf.getvalue()


def test_cli_no_results_returns_nonzero():
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(["zzzzxxxqqq", "wwwvvvuuu", "--cutoff", "90"])
    assert rc == 1
    assert "No airports found" in err.getvalue()
