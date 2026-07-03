"""Discovery seam contract (src/newslens/discovery.py; ADR-0003 §6).

The seam is COLD (no key granted) and must stay cold in tests: skip states are
verified to build no request via the socket-level guard, and the with-key
paths run only against the local fake server.

KNOWN-RED: test_BUG3_* documents that a malformed prompt template (stray
brace / unknown placeholder — the file is principal-editable) crashes the
whole ingest run instead of degrading to RSS-only like every other discovery
failure.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from newslens import config, db, discovery, ingest, paths

NOW = "2026-07-03T09:00:00.000Z"
KEY_ENV = {"PERPLEXITY_API_KEY": "pplx-qa-fake-key"}


def cfg_with_interests():
    return config.SourcesConfig(
        sources=[config.Source(name="Outlet A", rss_url="https://a.invalid/f.xml")],
        interests_broad=["technology"],
        interests_granular=["AI regulation"],
    )


def sonar_payload(n_results=2, answer="ANSWER-TEXT-SENTINEL-9f3a"):
    return {
        "id": "qa",
        "model": "sonar",
        "usage": {"total_tokens": 850},
        "choices": [{"message": {"role": "assistant", "content": answer}}],
        "search_results": [
            {
                "title": f"Discovered {i}",
                "url": f"https://elsewhere.example/story-{i}",
                "date": "2026-07-03",
            }
            for i in range(n_results)
        ],
    }


def sonar_rows(con):
    return con.execute(
        "SELECT outlet, url, title, raw_excerpt, published_at, source_type,"
        " wire_syndication_flag FROM source_items WHERE source_type = 'sonar'"
        " ORDER BY id"
    ).fetchall()


# --- skip states: no request is ever built ---------------------------------------

def test_keyless_skips_with_zero_network(migrated_con, no_network):
    status = discovery.run_discovery(migrated_con, cfg_with_interests(), env={}, now_iso=NOW)
    assert status.startswith("skipped — PERPLEXITY_API_KEY not set")
    assert no_network == []


def test_interest_less_skips_with_zero_network(migrated_con, no_network):
    cfg = config.SourcesConfig(
        sources=[config.Source(name="A", rss_url="https://a.invalid/f.xml")]
    )
    status = discovery.run_discovery(migrated_con, cfg, env=KEY_ENV, now_iso=NOW)
    assert status.startswith("skipped — no interests configured")
    assert no_network == []


def test_budget_guard_fires_before_any_request(migrated_con, no_network):
    env = dict(KEY_ENV, BUDGET_CAP_USD_PER_RUN="0.0001")  # below any real estimate
    status = discovery.run_discovery(migrated_con, cfg_with_interests(), env=env, now_iso=NOW)
    assert status.startswith("aborted — estimated discovery cost")
    assert "exceeds" in status and "RSS-only run" in status
    assert no_network == []


def test_missing_prompt_file_degrades_with_zero_network(
    migrated_con, no_network, monkeypatch, tmp_path
):
    monkeypatch.setattr(paths, "PROMPTS_DIR", tmp_path / "empty")
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW
    )
    assert status.startswith("failed — cannot read prompts/discovery_query.txt")
    assert no_network == []


def test_BUG3_malformed_prompt_template_must_degrade_not_crash(
    migrated_con, no_network, monkeypatch, tmp_path
):
    """KNOWN-RED (BUG-3): prompts are principal-editable files. A stray
    `{typo}` placeholder currently raises KeyError out of run_discovery,
    which run_ingest does NOT catch — the WHOLE ingest run dies with a
    traceback after RSS already succeeded, instead of degrading to RSS-only
    like every other discovery failure (ADR-0003 §6 'the run degrades to
    RSS-only and says so')."""
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / discovery.PROMPT_FILE).write_text(
        "Today {today_utc}; outlets {outlets}; interests {interests}; {typo}",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "PROMPTS_DIR", pdir)
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW
    )  # must not raise
    assert status.startswith("failed")
    assert no_network == []


# --- prompt construction -----------------------------------------------------------

def test_build_prompt_renders_outlets_interests_and_utc_date():
    prompt = discovery.build_prompt(cfg_with_interests())
    assert "Outlet A" in prompt
    assert "technology" in prompt and "AI regulation" in prompt
    assert "{" not in prompt  # every placeholder rendered


def test_estimate_cost_is_conservative_and_tiny_at_default_budget():
    prompt = discovery.build_prompt(cfg_with_interests())
    est = discovery.estimate_cost_usd(prompt)
    assert 0 < est < config.DEFAULT_BUDGET_CAP_USD_PER_RUN  # default cap never blocks v1


# --- retry discipline: one call, at most one retry, never on 4xx --------------------

def _http_error(code):
    return urllib.error.HTTPError("https://api.fake/x", code, "boom", {}, None)


def test_5xx_gets_exactly_one_retry_then_raises(monkeypatch):
    calls = {"n": 0}

    def always_500(key, body, timeout):
        calls["n"] += 1
        raise _http_error(500)

    monkeypatch.setattr(discovery, "_post_once", always_500)
    with pytest.raises(urllib.error.HTTPError):
        discovery.call_sonar("k", "p")
    assert calls["n"] == 2


def test_5xx_then_success_returns_the_retry_payload(monkeypatch):
    calls = {"n": 0}

    def flaky(key, body, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(503)
        return {"ok": True}

    monkeypatch.setattr(discovery, "_post_once", flaky)
    assert discovery.call_sonar("k", "p") == {"ok": True}
    assert calls["n"] == 2


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429])
def test_4xx_is_never_retried(monkeypatch, code):
    calls = {"n": 0}

    def client_error(key, body, timeout):
        calls["n"] += 1
        raise _http_error(code)

    monkeypatch.setattr(discovery, "_post_once", client_error)
    with pytest.raises(urllib.error.HTTPError):
        discovery.call_sonar("k", "p")
    assert calls["n"] == 1  # a bad request doesn't get better by asking twice


def test_timeout_gets_exactly_one_retry(monkeypatch):
    calls = {"n": 0}

    def times_out(key, body, timeout):
        calls["n"] += 1
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(discovery, "_post_once", times_out)
    with pytest.raises(urllib.error.URLError):
        discovery.call_sonar("k", "p")
    assert calls["n"] == 2


def test_integration_5xx_sends_exactly_two_posts(migrated_con, fake_api, monkeypatch):
    monkeypatch.setattr(
        discovery, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    fake_api.add_route("/chat/completions", status=500, body=b'{"error": "down"}')
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW
    )
    assert status.startswith("failed — Sonar call unsuccessful after one retry")
    assert "degraded to RSS-only" in status
    posts = [r for r in fake_api.recorded if r["method"] == "POST"]
    assert len(posts) == 2


def test_integration_401_sends_exactly_one_post(migrated_con, fake_api, monkeypatch):
    monkeypatch.setattr(
        discovery, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    # No route: default handler 401s any bearer that isn't the fake good key.
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW
    )
    assert status.startswith("failed — Sonar call unsuccessful")
    posts = [r for r in fake_api.recorded if r["method"] == "POST"]
    assert len(posts) == 1


# --- storage: search_results only, nothing fabricated --------------------------------

def test_success_stores_search_results_capped_with_no_fabricated_excerpts(
    migrated_con, fake_api, monkeypatch
):
    monkeypatch.setattr(
        discovery, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    payload = sonar_payload(n_results=10)  # over the 8-item cap
    payload["search_results"].append({"title": "no url, must be skipped"})
    fake_api.add_route(
        "/chat/completions",
        status=200,
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW
    )
    assert status.startswith("ok — 1 Sonar call, 8 discovered item(s) stored")
    stored = sonar_rows(migrated_con)
    assert len(stored) == discovery.MAX_DISCOVERY_ITEMS == 8
    for row in stored:
        assert row["source_type"] == "sonar"
        assert row["raw_excerpt"] is None  # we do not fabricate source content
        assert row["outlet"] == "elsewhere.example"  # netloc attribution
        assert row["published_at"] == "2026-07-03"
        assert row["wire_syndication_flag"] == 0
    # The generated answer text is NEVER persisted anywhere in the database.
    hits = migrated_con.execute(
        "SELECT COUNT(*) FROM source_items WHERE title LIKE '%SENTINEL%'"
        " OR raw_excerpt LIKE '%SENTINEL%' OR outlet LIKE '%SENTINEL%'"
    ).fetchone()[0]
    assert hits == 0


def test_discovery_defers_to_rss_rows_already_present_today(
    migrated_con, fake_api, monkeypatch
):
    monkeypatch.setattr(
        discovery, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    src = config.Source(name="RSS Outlet", rss_url="https://a.invalid/f.xml")
    ingest.upsert_item(
        migrated_con,
        src,
        ingest.ParsedItem(
            url="https://elsewhere.example/story-0",
            title="RSS got here first",
            published_at=None,
            excerpt="real feed excerpt",
        ),
        NOW,
    )
    migrated_con.commit()
    fake_api.add_route(
        "/chat/completions",
        status=200,
        body=json.dumps(sonar_payload(n_results=2)).encode("utf-8"),
        content_type="application/json",
    )
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW
    )
    assert "1 discovered item(s) stored" in status  # story-0 skipped, story-1 stored
    row = migrated_con.execute(
        "SELECT source_type, title, raw_excerpt FROM source_items WHERE url = ?",
        ("https://elsewhere.example/story-0",),
    ).fetchone()
    assert row["source_type"] == "rss"  # the RSS snapshot was not overwritten
    assert row["title"] == "RSS got here first"


def test_discovery_rows_are_idempotent_per_day_on_rerun(
    migrated_con, fake_api, monkeypatch
):
    monkeypatch.setattr(
        discovery, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    fake_api.add_route(
        "/chat/completions",
        status=200,
        body=json.dumps(sonar_payload(n_results=3)).encode("utf-8"),
        content_type="application/json",
    )
    discovery.run_discovery(migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW)
    status2 = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW
    )
    assert "0 discovered item(s) stored" in status2
    assert len(sonar_rows(migrated_con)) == 3  # no duplicates on same-day re-run
