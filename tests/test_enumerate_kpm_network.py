"""The 2026-09-28 scheduled run: a busy server must read as a busy server.

That run met a burst of 503s on consecutive search pages and then a dropped connection
(`http.client.RemoteDisconnected`, which is not a URLError), and crashed. Before that, a
page that failed was skipped with a warning and the partial URL set was compared against
the manifest, so an upstream outage would have read as the corpus going stale. And the
Socrata dataset the 2016-2018 names come from is gone from data.oregon.gov.

`urllib.request.urlopen` is the external boundary and is faked; retry, give-up,
FAILED bookkeeping and main()'s refusal run for real. Backoff waits are zeroed.
"""
from __future__ import annotations

import http.client
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import enumerate_kpm as ek  # noqa: E402

URL = "https://www.oregonlegislature.gov/lfo/APPR/APPR_X_2020.pdf"


class _Resp:
    def __init__(self, body=b"", status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


def _http(code):
    return urllib.error.HTTPError(URL, code, "x", {}, None)


@pytest.fixture
def urlopen(monkeypatch):
    """Install a scripted urlopen; returns the list of calls made."""
    monkeypatch.setattr(ek, "RETRY_WAITS", (0, 0, 0))
    monkeypatch.setattr(ek, "FAILED", [])
    calls: list[str] = []

    def script(*outcomes):
        it = iter(outcomes)

        def fake(req, timeout):
            calls.append(req.full_url)
            o = next(it)
            if isinstance(o, BaseException):
                raise o
            return o
        monkeypatch.setattr(ek.urllib.request, "urlopen", fake)
        return calls
    return script


class TestGet:
    def test_a_dropped_connection_is_retried(self, urlopen):
        calls = urlopen(http.client.RemoteDisconnected("gone"), _Resp(b"xml"))
        assert ek.get(URL) == b"xml" and len(calls) == 2

    def test_a_503_is_retried(self, urlopen):
        calls = urlopen(_http(503), _http(503), _Resp(b"x"))
        assert ek.get(URL) == b"x" and len(calls) == 3

    def test_a_404_is_an_answer_and_is_not_retried(self, urlopen):
        calls = urlopen(_http(404))
        with pytest.raises(urllib.error.HTTPError):
            ek.get(URL)
        assert len(calls) == 1

    def test_it_gives_up_after_the_last_retry(self, urlopen):
        calls = urlopen(*[_http(503)] * 4)
        with pytest.raises(urllib.error.HTTPError):
            ek.get(URL)
        assert len(calls) == 4


class TestVerify:
    def test_a_persistent_503_is_recorded_as_failed_not_as_unreachable(self, urlopen):
        urlopen(*[_http(503)] * 4)
        assert ek.verify(URL)[0] == 503 and len(ek.FAILED) == 1

    def test_a_404_is_unreachable_and_not_a_failure(self, urlopen):
        urlopen(_http(404))
        assert ek.verify(URL)[0] == 404 and ek.FAILED == []

    def test_a_dropped_connection_is_retried_then_reads_the_pdf(self, urlopen):
        urlopen(http.client.RemoteDisconnected("x"),
                _Resp(headers={"Content-Type": "application/pdf", "Content-Length": "9"}))
        assert ek.verify(URL) == (200, "application/pdf", 9) and ek.FAILED == []


def test_a_search_page_that_never_answers_is_recorded(urlopen, monkeypatch):
    monkeypatch.setattr(ek, "QUERY_TERMS", ["APPR"])
    monkeypatch.setattr(ek, "YEARS", [])
    urlopen(*[http.client.RemoteDisconnected("x")] * 4)
    ek.sweep_rss()
    assert len(ek.FAILED) == 1 and "start=1" in ek.FAILED[0]


@pytest.mark.parametrize("argv", [["--check"], []])
def test_an_incomplete_run_neither_compares_nor_writes(argv, monkeypatch, capsys, tmp_path):
    manifest = tmp_path / "source-manifest.yml"
    manifest.write_text("sources: []\n")
    monkeypatch.setattr(ek, "MANIFEST", manifest)
    monkeypatch.setattr(ek, "FAILED", [])
    monkeypatch.setattr(ek, "build", lambda skip_sweep=False: (ek.FAILED.append("x"), {})[1])
    monkeypatch.setattr(sys, "argv", ["enumerate_kpm.py", *argv])
    assert ek.main() == 1
    err = capsys.readouterr().err
    assert "neither current nor stale" in err and "out of date" not in err
    assert manifest.read_text() == "sources: []\n"


class TestSocrata:
    def test_a_missing_dataset_falls_back_to_the_archived_copy(self, urlopen, capsys):
        urlopen(_http(404))
        rows = ek.socrata_rows()
        assert len(rows) == 238
        assert "archived copy" in capsys.readouterr().out

    def test_the_archive_holds_what_the_docstring_says_the_dataset_held(self):
        rows = json.loads(ek.SOCRATA_ARCHIVE.read_text(encoding="utf-8"))
        years = sorted({r["year"] for r in rows})
        assert len(rows) == 238 and years == ["2016", "2017", "2018"]

    def test_the_live_dataset_wins_when_it_answers(self, urlopen):
        urlopen(_Resp(json.dumps([{"year": "2016"}]).encode()))
        assert ek.socrata_rows() == [{"year": "2016"}]

    def test_any_other_error_is_not_papered_over_by_the_archive(self, urlopen):
        urlopen(*[_http(503)] * 4)
        with pytest.raises(urllib.error.HTTPError):
            ek.socrata_rows()
