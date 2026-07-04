"""net.py: the shared fetch seam (M2 review carryovers 5-7; M2 QA observation 3).

One opener, one 308 story, one UA, one byte cap — shared by ingest and the
doctor so a feed cannot behave differently between them.
"""

from __future__ import annotations

import pytest

from newslens import config, ingest, net

from conftest import make_rss


def test_fetch_bytes_enforces_the_byte_cap_loudly(fake_api):
    url = fake_api.add_route("/big.xml", body=b"x" * 1001)
    with pytest.raises(ValueError) as excinfo:
        net.fetch_bytes(url, timeout=5, cap=1000)
    assert "exceeds the 1000-byte feed size cap" in str(excinfo.value)


def test_fetch_bytes_at_exactly_the_cap_is_fine(fake_api):
    url = fake_api.add_route("/fits.xml", body=b"y" * 1000)
    assert net.fetch_bytes(url, timeout=5, cap=1000) == b"y" * 1000


def test_default_cap_is_4mb():
    assert net.MAX_FEED_BYTES == 4_000_000


def test_fetch_bytes_follows_308_via_the_shared_handler(fake_api):
    fake_api.add_route("/moved8", status=308, location="/dest8.xml")
    dest = make_rss([{"title": "T", "url": "https://x.example/1"}])
    fake_api.add_route("/dest8.xml", body=dest)
    assert net.fetch_bytes(fake_api.base_url + "/moved8", timeout=5) == dest


def test_head_bytes_returns_prefix_and_status(fake_api):
    body = make_rss([{"title": "T", "url": "https://x.example/1"}])
    url = fake_api.add_route("/head.xml", body=body)
    head, status = net.head_bytes(url, timeout=5, n=16)
    assert status == 200
    assert head == body[:16]


def test_ingest_and_net_share_one_user_agent():
    assert ingest.USER_AGENT == net.USER_AGENT


def test_oversize_feed_is_a_visible_per_source_failure(
    migrated_con, fake_api, monkeypatch
):
    """End-to-end: a feed over the cap degrades that source loudly instead of
    reading unbounded (M2 QA observation 3, now structural)."""
    real_fetch = net.fetch_bytes

    def tiny_cap_fetch(url, timeout, cap=None, user_agent=net.USER_AGENT):
        return real_fetch(url, timeout=timeout, cap=100, user_agent=user_agent)

    monkeypatch.setattr(net, "fetch_bytes", tiny_cap_fetch)
    url = fake_api.add_route(
        "/fat.xml", body=make_rss([{"title": "T" * 80, "url": "https://x.example/1"}])
    )
    cfg = config.SourcesConfig(sources=[config.Source(name="Fat Feed", rss_url=url)])
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert list(report.failed) == ["Fat Feed"]
    assert "100-byte feed size cap" in report.failed["Fat Feed"]
    assert report.degradation_message.startswith("1 of 1 sources unavailable")
