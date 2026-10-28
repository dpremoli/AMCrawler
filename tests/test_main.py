import pytest

from amcrawler import __main__ as cli
from amcrawler.store import SnapshotInfo
from datetime import datetime, timezone


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("AMC_DB_PATH", str(tmp_path / "cli.db"))


class _Client:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _snap(ok, error=None):
    return SnapshotInfo(id=7, taken_at=datetime.now(timezone.utc), ok=ok,
                        error=error, departure_count=0)


@pytest.mark.parametrize("argv", [[], ["bogus"], ["crawl", "extra"]])
def test_bad_usage_exits_2(argv):
    assert cli.main(argv) == 2


def test_crawl_success(monkeypatch, capsys):
    monkeypatch.setattr(cli.source, "make_client", lambda: _Client())
    monkeypatch.setattr(cli.crawl, "run_crawl", lambda store, c, now: _snap(True))
    assert cli.main(["crawl"]) == 0
    assert "snapshot 7" in capsys.readouterr().out


def test_crawl_not_ok_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(cli.source, "make_client", lambda: _Client())
    monkeypatch.setattr(cli.crawl, "run_crawl", lambda store, c, now: _snap(False, "boom"))
    assert cli.main(["crawl"]) == 1
    assert "boom" in capsys.readouterr().err


@pytest.mark.parametrize("status,code", [("sent", 0), ("empty", 0), ("baseline", 0),
                                         ("failed", 1), ("stale", 1)])
def test_digest_exit_codes(monkeypatch, capsys, status, code):
    monkeypatch.setattr(cli.digest, "run_digest", lambda *a: status)
    assert cli.main(["digest"]) == code
    assert status in capsys.readouterr().out


@pytest.mark.parametrize("status,reason", [("paused", "paused"),
                                           ("not_send_day", "not a send day")])
def test_digest_skip_prints_reason_and_exits_0(monkeypatch, capsys, status, reason):
    monkeypatch.setattr(cli.digest, "run_digest", lambda *a: status)
    assert cli.main(["digest"]) == 0
    assert f"digest skipped: {reason}" in capsys.readouterr().out
