import pytest

from amcrawler.source import fetch_catalogue, fetch_uncovered, make_client

pytestmark = pytest.mark.live


def test_live_uncovered_and_catalogue():
    with make_client() as client:
        assert len(fetch_uncovered(client)) >= 1
        assert len(fetch_catalogue(client)) >= 500
