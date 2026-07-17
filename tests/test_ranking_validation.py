"""Ranking LLM seam: hard validation + retry/expense discipline (ADR-0004 §10;
ranking.py structured-output block; the 2026-07-04 live finding).

Everything runs against the local fake server, or pure functions — no real
endpoint, no spend, and a real key in .env changes nothing (tests pass env
dicts explicitly / the fixture sets a fake ANTHROPIC_API_KEY).

B2 fake migration (QA, 2026-07-16): the rank seat rides the Claude API lane
(claude-haiku-4-5) now, and the anthropic provider reads its OWN endpoint
(llm.ANTHROPIC_MESSAGES_URL) + credential (ANTHROPIC_API_KEY) — the historical
ranking.OPENAI_CHAT_URL patch no longer reaches it. The `llm` fixture therefore
redirects BOTH endpoint names at the loopback fake, and transport bodies are
anthropic-shaped (conftest.anthropic_envelope / anthropic error envelopes).
Every test's CONTRACT is unchanged: same retry counts, same fail-fast classes,
same disclosure assertions. Where the ERROR STRINGS misdirect on this lane
("OpenAI rejected the key ... platform.openai.com" for a failure that happened
at api.anthropic.com), the tests pin current behavior and NAME the misdirection
— characterized for the gate, deliberately not fixed in a QA pass.

KNOWN-RED: test_BUG6_* — ADR-0004 §6 says failed runs log to ranking_runs
too, but pre-call failures (budget abort) currently log nothing.
"""

from __future__ import annotations

import json
import time
import urllib.error

import pytest

from conftest import anthropic_envelope
from newslens import config, llm as llm_mod, ranking

DATE = "2026-07-04"
TAGS = {"AI regulation": "topic", "economy": "domain"}
MEMORY = ["chip export controls"]
KNOWN_IDS = {1, 2, 3, 4}


def cluster(
    ids,
    title="A story",
    summary="What happened.",
    tags=None,
    memory=None,
    impact=5,
    reason="Because it matters.",
):
    return {
        "story_title": title,
        "summary": summary,
        "item_ids": ids,
        "matched_tags": tags or [],
        "matched_memory": memory or [],
        "world_impact": impact,
        "world_impact_reason": reason,
    }


def envelope(payload, prompt_tokens=1000, completion_tokens=200):
    return json.dumps(
        {
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }
    ).encode("utf-8")


def validate(payload):
    return ranking.validate_payload(payload, KNOWN_IDS, TAGS, MEMORY)


# --- validate_payload hostility -------------------------------------------------

def test_valid_payload_passes_and_truncates():
    out = validate(
        {"clusters": [cluster([1, 2], title="T" * 400, summary="S" * 500,
                              tags=[{"name": "AI regulation", "level": "topic"}])]}
    )
    assert len(out) == 1
    assert len(out[0]["story_title"]) == 300
    assert len(out[0]["summary"]) == 400
    assert out[0]["matched_tags"] == [{"name": "AI regulation", "level": "topic"}]


@pytest.mark.parametrize("payload", ["not a dict", {"no_clusters": []}, {"clusters": "x"}])
def test_wrong_shape_is_rejected_outright(payload):
    with pytest.raises(ValueError) as excinfo:
        validate(payload)
    assert "clusters" in str(excinfo.value)


def test_far_too_many_clusters_refused():
    with pytest.raises(ValueError) as excinfo:
        validate({"clusters": [cluster([1]) for _ in range(25)]})
    assert "far over" in str(excinfo.value)


def test_invented_item_ids_rejected():
    with pytest.raises(ValueError) as excinfo:
        validate({"clusters": [cluster([1, 99])]})
    assert "invented item_ids [99]" in str(excinfo.value)


def test_cross_cluster_item_reuse_rejected_the_live_finding_class():
    """The 2026-07-04 live failure class: the model put items 514/797 in two
    clusters; the partition rule must reject the whole payload, naming ids."""
    with pytest.raises(ValueError) as excinfo:
        validate({"clusters": [cluster([1, 2]), cluster([2, 3], title="Other")]})
    assert "item_ids [2] already used by another cluster" in str(excinfo.value)


def test_releveled_and_unknown_tags_rejected():
    with pytest.raises(ValueError) as excinfo:
        validate(
            {"clusters": [cluster(
                [1],
                tags=[{"name": "AI regulation", "level": "domain"},  # re-leveled
                      {"name": "made-up tag", "level": "topic"}],    # not listed
            )]}
        )
    msg = str(excinfo.value)
    assert msg.count("not an exact listed tag") == 2


def test_unknown_memory_thread_rejected():
    with pytest.raises(ValueError) as excinfo:
        validate({"clusters": [cluster([1], memory=["a thread we never provided"])]})
    assert "not in the provided threads" in str(excinfo.value)


@pytest.mark.parametrize("impact", [11, -1, True, "7"])
def test_out_of_range_or_non_numeric_world_impact_rejected(impact):
    with pytest.raises(ValueError) as excinfo:
        validate({"clusters": [cluster([1], impact=impact)]})
    assert "world_impact must be a number 0-10" in str(excinfo.value)


def test_boundary_impacts_accepted_and_floats_rounded():
    out = validate(
        {"clusters": [cluster([1], impact=0), cluster([2], impact=10),
                      cluster([3], impact=7.6)]}
    )
    assert [c["world_impact"] for c in out] == [0, 10, 8]


@pytest.mark.parametrize(
    "field, value, fragment",
    [
        ("story_title", "", "story_title missing/empty"),
        ("summary", "  ", "summary missing/empty"),
        ("world_impact_reason", "", "world_impact_reason missing/empty"),
        ("item_ids", [], "non-empty list of integers"),
        ("item_ids", ["1"], "non-empty list of integers"),
    ],
)
def test_missing_or_empty_required_fields_rejected(field, value, fragment):
    c = cluster([1])
    c[field] = value
    with pytest.raises(ValueError) as excinfo:
        validate({"clusters": [c]})
    assert fragment in str(excinfo.value)


def test_all_problems_reported_not_just_the_first():
    """A retry/report is only actionable if EVERY problem is named."""
    bad = {
        "clusters": [
            cluster([1, 99], reason=""),                       # invented id + empty reason
            cluster([1], tags=[{"name": "nope", "level": "topic"}]),  # dupe id + bad tag
        ]
    }
    with pytest.raises(ValueError) as excinfo:
        validate(bad)
    msg = str(excinfo.value)
    for fragment in (
        "invented item_ids [99]",
        "world_impact_reason missing/empty",
        "item_ids [1] already used by another cluster",
        "not an exact listed tag",
    ):
        assert fragment in msg, f"missing {fragment!r} in {msg!r}"


# --- HTTP error helpers ------------------------------------------------------------

def _http_error(code, body=b"", headers=None):
    import email.message
    import io

    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    return urllib.error.HTTPError(
        "https://api.fake/v1/chat/completions", code, "err", hdrs, io.BytesIO(body)
    )


def test_http_error_detail_extracts_code_and_message():
    exc = _http_error(
        429, json.dumps({"error": {"code": "insufficient_quota", "message": "No credits"}}).encode()
    )
    assert ranking._http_error_detail(exc) == "insufficient_quota: No credits"


def test_http_error_detail_tolerates_non_json_bodies():
    assert ranking._http_error_detail(_http_error(500, b"<html>oops</html>")) == ""


@pytest.mark.parametrize(
    "header, expected", [("3.5", 3.5), ("999", 20.0), ("soon", 10.0), (None, 10.0)]
)
def test_retry_after_seconds_parses_caps_and_defaults(header, expected):
    headers = {"Retry-After": header} if header is not None else {}
    assert ranking._retry_after_seconds(_http_error(429, b"", headers)) == expected


# --- call_llm_validated: retry + expense discipline (offline fake server) ------------

@pytest.fixture
def llm(fake_api, monkeypatch):
    # Both lanes point at the loopback fake: the openai seam (historical) AND
    # the anthropic provider's own module-level endpoint — B2's rank seat rides
    # the latter and ignores LaneRequest.url. The anthropic credential is the
    # fake server's good_key so the x-api-key auth succeeds unless a test
    # routes an explicit error.
    monkeypatch.setattr(
        ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    monkeypatch.setattr(
        llm_mod, "ANTHROPIC_MESSAGES_URL", fake_api.base_url + "/v1/messages"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    # B3: rank DEFAULTS to the claude -p subscription lane now. These tests
    # exercise the anthropic API PROVIDER (its request bytes, retry law, cost) —
    # the registered api FALL-OVER lane — so pin rank to it. Transport routes to
    # the loopback /v1/messages above; the subscription subprocess is never hit.
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    fake_api.sleeps = sleeps
    return fake_api


def anthropic_error(err_type: str, message: str) -> bytes:
    """An anthropic-shaped error body (the twin of the OpenAI {'error': {...}}
    bodies these tests historically served). ranking._http_error_detail reads
    error.code-or-TYPE + error.message off it, so the detail line becomes e.g.
    'authentication_error: bad key'."""
    return json.dumps(
        {"type": "error", "error": {"type": err_type, "message": message}}
    ).encode("utf-8")


def _posts(fake_api):
    return [r for r in fake_api.recorded if r["method"] == "POST"]


def call(key="sk-qa-fake"):
    return ranking.call_llm_validated(key, "prompt", KNOWN_IDS, TAGS, MEMORY)


def test_success_returns_clusters_and_usage(llm):
    llm.add_route(
        "/v1/messages", status=200,
        body=anthropic_envelope({"clusters": [cluster([1])]}),
        content_type="application/json",
    )
    clusters, usage = call()
    # usage is the OpenAI-shaped dict the anthropic provider synthesises:
    # input_tokens=1000 must surface as prompt_tokens=1000 (the ledger reader).
    assert len(clusters) == 1 and usage["prompt_tokens"] == 1000
    assert len(_posts(llm)) == 1


def test_401_fails_immediately_naming_the_key(llm):
    """Contract unchanged on the Claude lane: auth failure = immediate
    RankingError, zero retries, zero sleeps, body detail surfaced.

    FIX B (gate, landed): the rank seat is anthropic, so a 401 now names the
    RIGHT console — 'Anthropic rejected the key ... regenerate at
    console.anthropic.com/settings/keys and update .env' — instead of sending
    the principal to rotate the wrong (OpenAI) key. The openai arm is unchanged
    (the rollback path; pinned by test_generate.test_call_llm_401_names_the_key)."""
    llm.add_route(
        "/v1/messages", status=401,
        body=anthropic_error("authentication_error", "invalid x-api-key"),
        content_type="application/json",
    )
    with pytest.raises(ranking.RankingError) as excinfo:
        call()
    msg = str(excinfo.value)
    assert "Anthropic rejected the key" in msg
    assert "console.anthropic.com/settings/keys" in msg   # the RIGHT console
    assert "platform.openai.com" not in msg               # no longer misdirects
    assert "authentication_error" in msg  # anthropic detail surfaced
    assert len(_posts(llm)) == 1  # never retried
    assert llm.sleeps == []


def test_429_insufficient_quota_fails_immediately_with_billing_hint(llm):
    """The quota fast-fail ARM, pinned: any 429 whose body detail carries
    'insufficient_quota' fails immediately with the billing hint — retrying
    spends nothing and fixes nothing. NOTE (B2 QA): 'insufficient_quota' is an
    OpenAI error code; the anthropic API signals credit exhaustion as HTTP 400
    'credit balance is too low' instead (characterized in
    test_anthropic_credit_exhaustion_400_fails_immediately below), so on this
    lane the arm is reachable only via this synthetic body. Kept: it pins the
    arm's behavior and the doctor-blind-spot honesty line."""
    llm.add_route(
        "/v1/messages", status=429,
        body=anthropic_error("rate_limit_error",
                             "insufficient_quota: You exceeded your current quota"),
        content_type="application/json",
    )
    with pytest.raises(ranking.RankingError) as excinfo:
        call()
    msg = str(excinfo.value)
    assert "add credits / check billing" in msg
    assert "cannot catch this" in msg  # names the doctor's blind spot honestly
    assert len(_posts(llm)) == 1  # retrying spends nothing and fixes nothing
    assert llm.sleeps == []


def test_anthropic_credit_exhaustion_400_fails_immediately(llm):
    """FIX C (gate, landed) — the REAL anthropic quota-exhaustion shape
    (HTTP 400 invalid_request_error, 'credit balance is too low') now takes a
    DEDICATED billing arm ahead of the generic 4xx arm: immediate RankingError,
    no retry, no sleep, and an actionable message that names the anthropic
    billing console and keeps the doctor-blind-spot honesty line. The openai
    insufficient_quota arm (a different wire code) is unchanged."""
    llm.add_route(
        "/v1/messages", status=400,
        body=anthropic_error("invalid_request_error",
                             "Your credit balance is too low to access the Anthropic API."),
        content_type="application/json",
    )
    with pytest.raises(ranking.RankingError) as excinfo:
        call()
    msg = str(excinfo.value)
    assert "Anthropic account has no available credit" in msg
    assert "add credits at console.anthropic.com billing" in msg
    assert "cannot catch this" in msg                  # doctor blind-spot honesty
    assert "credit balance is too low" in msg          # the actionable detail surfaces
    assert "OpenAI" not in msg                          # no longer misdirects
    assert len(_posts(llm)) == 1  # non-retryable: never retried
    assert llm.sleeps == []


def test_transient_429_retries_once_honoring_retry_after(llm):
    llm.add_route(
        "/v1/messages", status=429,
        body=anthropic_error("rate_limit_error", "Rate limit"),
        content_type="application/json",
        headers={"Retry-After": "0"},
    )
    with pytest.raises(ranking.RankingError) as excinfo:
        call()
    assert "failed after one retry" in str(excinfo.value)
    assert "rate limited" in str(excinfo.value)
    assert len(_posts(llm)) == 2  # exactly one retry
    assert llm.sleeps == [0.0]   # honored the header, capped semantics tested above


def test_5xx_retries_once_then_visible_error(llm):
    llm.add_route("/v1/messages", status=503,
                  body=anthropic_error("api_error", "down"),
                  content_type="application/json")
    with pytest.raises(ranking.RankingError) as excinfo:
        call()
    assert "failed after one retry" in str(excinfo.value)
    assert len(_posts(llm)) == 2


def test_529_overloaded_is_retryable_like_5xx(llm):
    """B2 adversarial: anthropic's 529 overloaded_error is its load-shed shape
    (a code OpenAI never sent). It must ride the >=500 transient arm — one
    retry, then a visible error naming the status + detail — never the
    non-retryable 4xx raise."""
    llm.add_route("/v1/messages", status=529,
                  body=anthropic_error("overloaded_error", "Overloaded"),
                  content_type="application/json")
    with pytest.raises(ranking.RankingError) as excinfo:
        call()
    msg = str(excinfo.value)
    assert "failed after one retry" in msg
    assert "HTTP 529" in msg and "overloaded_error" in msg
    assert len(_posts(llm)) == 2


def test_duplicate_ids_payload_repairs_and_succeeds_with_disclosure(llm):
    """M3 fix loop 1 flips the 2026-07-04 live class from reject-and-retry to
    DISCLOSED deterministic repair: keep each item's first cluster, drop later
    duplicates, count every drop — one call, no retry burned."""
    llm.add_route(
        "/v1/messages", status=200,
        body=anthropic_envelope({"clusters": [cluster([1, 2]), cluster([2, 3], title="B")]}),
        content_type="application/json",
    )
    repairs = {}
    clusters, usage = ranking.call_llm_validated(
        "sk-qa-fake", "prompt", KNOWN_IDS, TAGS, MEMORY, repairs=repairs
    )
    assert [c["item_ids"] for c in clusters] == [[1, 2], [3]]  # first cluster kept item 2
    assert repairs["repaired"] == 1
    assert repairs["dropped"] == [{"item_id": 2, "dropped_from": "B"}]
    assert repairs["clusters_emptied"] == []
    assert len(_posts(llm)) == 1  # repaired, not retried


def test_cluster_emptied_by_repair_is_dropped_whole_and_disclosed(llm):
    llm.add_route(
        "/v1/messages", status=200,
        body=anthropic_envelope({"clusters": [cluster([1, 2]), cluster([2, 1], title="Echo")]}),
        content_type="application/json",
    )
    repairs = {}
    clusters, _ = ranking.call_llm_validated(
        "sk-qa-fake", "prompt", KNOWN_IDS, TAGS, MEMORY, repairs=repairs
    )
    assert len(clusters) == 1  # Echo lost both ids and was removed
    assert repairs["repaired"] == 2
    assert repairs["clusters_emptied"] == ["Echo"]


@pytest.mark.parametrize(
    "bad_cluster, fragment",
    [
        (cluster([1, 99]), "invented item_ids"),
        (cluster([1], tags=[{"name": "AI regulation", "level": "domain"}]),
         "not an exact listed tag"),
        (cluster([1], impact=11), "world_impact must be a number 0-10"),
        (cluster([1], reason=""), "world_impact_reason missing/empty"),
    ],
)
def test_repair_scope_other_violation_classes_still_hard_reject_end_to_end(
    llm, bad_cluster, fragment
):
    """The repair is scoped to duplicate assignment ONLY — every other
    violation class still walks the reject -> one retry -> visible-error
    path, with the validator's diagnosis in the message."""
    llm.add_route(
        "/v1/messages", status=200,
        body=anthropic_envelope({"clusters": [bad_cluster]}),
        content_type="application/json",
    )
    with pytest.raises(ranking.RankingError) as excinfo:
        call()
    msg = str(excinfo.value)
    assert "failed after one retry" in msg
    assert "malformed LLM output" in msg
    assert fragment in msg
    assert len(_posts(llm)) == 2


def test_repair_duplicate_ids_unit_semantics():
    payload = {
        "clusters": [
            cluster([1, 2], title="First"),
            cluster([2, 3], title="Second"),
            cluster(["x"], title="NotInts"),  # not this repair's class
        ]
    }
    fixed, info = ranking.repair_duplicate_ids(payload)
    assert [c["item_ids"] for c in fixed["clusters"]] == [[1, 2], [3], ["x"]]
    assert info["repaired"] == 1
    # No duplicates -> payload passes through identically, repaired == 0.
    clean = {"clusters": [cluster([1]), cluster([2], title="B")]}
    same, info2 = ranking.repair_duplicate_ids(clean)
    assert same is clean and info2 == {"repaired": 0}
    # Unparseable shapes pass through for the validator's usual diagnosis.
    garbage, info3 = ranking.repair_duplicate_ids("not a dict")
    assert garbage == "not a dict" and info3 == {"repaired": 0}


def test_unreachable_endpoint_retries_once_then_fails(llm, fake_api, monkeypatch):
    # B2: the DEAD endpoint must be the one the rank seat actually posts to —
    # the anthropic module URL. (Pre-migration this patched only the openai
    # seam, which the Claude lane ignores; the test then passed by hitting the
    # loopback DNS guard instead of the dead port — green for the wrong reason.)
    monkeypatch.setattr(llm_mod, "ANTHROPIC_MESSAGES_URL", fake_api.dead_url("/v1/messages"))
    with pytest.raises(ranking.RankingError) as excinfo:
        call()
    assert "failed after one retry" in str(excinfo.value)


# --- run_rank guards: spend-proofing + instrumentation --------------------------------

def seed_items(con, n=3):
    now = ranking.datetime.now(ranking.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for i in range(1, n + 1):
        con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title, fetched_at)"
            " VALUES (?, 'rss', ?, ?, ?, ?)",
            (i, f"Outlet {i}", f"https://o{i}.example/{i}", f"Story {i}", now),
        )
    con.commit()


def rank_cfg():
    return config.SourcesConfig(
        sources=[config.Source(name="Outlet 1", rss_url="https://o1.example/f")],
        interests_broad=["economy"],
        interests_granular=["AI regulation"],
    )


def test_keyless_rank_refuses_before_any_request(migrated_con, llm):
    seed_items(migrated_con)
    with pytest.raises(ranking.RankingError) as excinfo:
        ranking.run_rank(date=DATE, con=migrated_con, cfg=rank_cfg(), env={})
    assert "OPENAI_API_KEY not set" in str(excinfo.value)
    assert "no keyless mode" in str(excinfo.value)
    assert _posts(llm) == []  # spend-proof: no request was ever built


def test_no_interests_refuses_before_any_request(migrated_con, llm):
    seed_items(migrated_con)
    cfg = config.SourcesConfig(
        sources=[config.Source(name="Outlet 1", rss_url="https://o1.example/f")]
    )
    with pytest.raises(ranking.RankingError) as excinfo:
        ranking.run_rank(
            date=DATE, con=migrated_con, cfg=cfg, env={"OPENAI_API_KEY": "sk-x"}
        )
    assert "no interests configured" in str(excinfo.value)
    assert _posts(llm) == []


def test_no_items_in_window_is_a_named_refusal_and_logs_a_failed_run(
    migrated_con, llm
):
    with pytest.raises(ranking.RankingError) as excinfo:
        ranking.run_rank(
            date=DATE, con=migrated_con, cfg=rank_cfg(), env={"OPENAI_API_KEY": "sk-x"}
        )
    assert "no ingested items inside the candidate window" in str(excinfo.value)
    assert _posts(llm) == []
    # M3 fix loop 1: post-connection refusals are instrumentation too.
    rows = migrated_con.execute(
        "SELECT meta FROM ranking_runs WHERE date = ?", (DATE,)
    ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["meta"])["status"] == "failed"


def test_budget_cap_aborts_before_the_call(migrated_con, llm):
    seed_items(migrated_con)
    with pytest.raises(ranking.RankingError) as excinfo:
        ranking.run_rank(
            date=DATE, con=migrated_con, cfg=rank_cfg(),
            env={"OPENAI_API_KEY": "sk-x", "BUDGET_CAP_USD_PER_RUN": "0.0001"},
        )
    assert "exceeds BUDGET_CAP_USD_PER_RUN" in str(excinfo.value)
    assert _posts(llm) == []  # the guard fired BEFORE any money could move


def test_BUG6_budget_abort_must_log_a_ranking_runs_row(migrated_con, llm):
    """KNOWN-RED (BUG-6): ADR-0004 §6 — 'Failed runs log too (status=failed)'.
    Pre-call failures (budget abort) currently log NOTHING, so the day-14
    readout cannot see that runs were dying on a misconfigured cap.
    Implementer call: log pre-call RankingErrors once the date is known, or
    take a narrowed contract back through review."""
    seed_items(migrated_con)
    with pytest.raises(ranking.RankingError):
        ranking.run_rank(
            date=DATE, con=migrated_con, cfg=rank_cfg(),
            env={"OPENAI_API_KEY": "sk-x", "BUDGET_CAP_USD_PER_RUN": "0.0001"},
        )
    rows = migrated_con.execute(
        "SELECT meta FROM ranking_runs WHERE date = ?", (DATE,)
    ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["meta"])["status"] == "failed"


def test_llm_failure_logs_a_failed_ranking_runs_row(migrated_con, llm):
    """The live 2026-07-04 behavior, pinned: an LLM/validation failure raises
    RankingError AND leaves a status=failed instrumentation row."""
    seed_items(migrated_con)
    # B2: route the 503 at the endpoint the rank seat actually posts to (the
    # pre-migration /chat/completions route was dead code on this lane — the
    # test stayed green only via the loopback guard's connection failure).
    llm.add_route("/v1/messages", status=503,
                  body=anthropic_error("api_error", "down"),
                  content_type="application/json")
    with pytest.raises(ranking.RankingError):
        ranking.run_rank(
            date=DATE, con=migrated_con, cfg=rank_cfg(),
            env={"OPENAI_API_KEY": "sk-x"},
        )
    rows = migrated_con.execute(
        "SELECT meta FROM ranking_runs WHERE date = ?", (DATE,)
    ).fetchall()
    assert len(rows) == 1
    meta = json.loads(rows[0]["meta"])
    assert meta["status"] == "failed"
    assert "failed after one retry" in meta["error"]
    # And no briefing row was written for the failed run:
    assert migrated_con.execute(
        "SELECT COUNT(*) FROM briefings WHERE date = ?", (DATE,)
    ).fetchone()[0] == 0


def test_repaired_run_succeeds_end_to_end_with_full_disclosure(migrated_con, llm):
    """The M3 fix-loop contract end to end: a duplicate-assignment payload
    (with one cluster fully emptied) repairs, ranks, persists — and the
    repair is disclosed BOTH as a visible run warning AND in
    ranking_runs.meta.repairs. Never silent."""
    seed_items(migrated_con)
    payload = {
        "clusters": [
            cluster([1, 2], title="Keeper",
                    tags=[{"name": "AI regulation", "level": "topic"}]),
            cluster([2, 3], title="Partial"),
            cluster([3, 1], title="Ghost"),  # loses both ids -> emptied
        ]
    }
    llm.add_route(
        "/v1/messages", status=200, body=anthropic_envelope(payload),
        content_type="application/json",
    )
    report = ranking.run_rank(
        date=DATE, con=migrated_con, cfg=rank_cfg(), env={"OPENAI_API_KEY": "sk-x"}
    )
    assert len(_posts(llm)) == 1  # repaired on the first attempt, no retry
    assert {s.story_title for s in report.slots} <= {"Keeper", "Partial"}

    repair_warnings = [w for w in report.warnings if "clustering repair" in w]
    assert len(repair_warnings) == 1
    assert "3 duplicate item assignment(s) dropped" in repair_warnings[0]
    assert "1 cluster(s) emptied and removed" in repair_warnings[0]
    assert "ranking_runs.meta.repairs" in repair_warnings[0]

    run = migrated_con.execute(
        "SELECT meta FROM ranking_runs WHERE date = ?", (DATE,)
    ).fetchone()
    meta = json.loads(run["meta"])
    assert meta["status"] == "ok"
    assert meta["repairs"]["repaired"] == 3
    assert meta["repairs"]["clusters_emptied"] == ["Ghost"]
    # The briefing row landed despite the repair:
    assert migrated_con.execute(
        "SELECT COUNT(*) FROM briefings WHERE date = ?", (DATE,)
    ).fetchone()[0] == 1


# --- render-failure class (BUG-3 carryover pins, ranking side) --------------------------

@pytest.mark.parametrize(
    "template, exc_name",
    [
        ("Window {window_desc}; broken {items_block.nope}", "AttributeError"),
        ("Cap {max_clusters[0]} is not subscriptable", "TypeError"),
        ("Unknown {placeholder_that_does_not_exist}", "KeyError"),
    ],
)
def test_prompt_render_errors_are_named_ranking_failures_not_crashes(
    migrated_con, llm, monkeypatch, tmp_path, template, exc_name
):
    from newslens import paths

    seed_items(migrated_con)
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / ranking.PROMPT_FILE).write_text(template, encoding="utf-8")
    monkeypatch.setattr(paths, "PROMPTS_DIR", pdir)
    with pytest.raises(ranking.RankingError) as excinfo:
        ranking.run_rank(
            date=DATE, con=migrated_con, cfg=rank_cfg(), env={"OPENAI_API_KEY": "sk-x"}
        )
    msg = str(excinfo.value)
    assert "did not render" in msg and exc_name in msg
    assert _posts(llm) == []  # failed before any request


# --- run 28 (2026-07-14): the fabricated arithmetic id-lattice --------------------
# Live RANK failure: at temp 0 gpt-4o abandoned transcription of the 550-line
# item list and emitted 12 clusters of 12 evenly-spaced 3-digit ids (383-613,
# step ~20) — NONE present in the real 3679-4228 window. Pure fabrication, not a
# near-miss. These pin (a) that the closed-vocab guard hard-rejects the exact
# shape and (b) the safety property that makes it work: fabrications land
# OUTSIDE the sparse real id set. A dense 1..N id remap would break (b).

# The real window the failed run saw, and the exact lattice the model emitted.
_RUN28_WINDOW_IDS = set(range(3679, 4229))          # 550 real ids, all 4-digit
_RUN28_LATTICE = [                                  # 12 clusters x 12 ids, step ~20
    list(range(base, base + 12 * 20, 20)) for base in (395, 386, 387, 383, 388, 389,
                                                       390, 391, 392, 393, 394, 396)
]


def test_run28_fabricated_id_lattice_is_hard_rejected():
    payload = {"clusters": [cluster(ids, title=f"c{n}") for n, ids in enumerate(_RUN28_LATTICE)]}
    with pytest.raises(ValueError) as excinfo:
        ranking.validate_payload(payload, _RUN28_WINDOW_IDS, TAGS, MEMORY)
    msg = str(excinfo.value)
    # Every fabricated cluster is named as invented — no row can be written.
    assert msg.count("invented item_ids") == 12


def test_run28_fabrication_lands_outside_the_real_vocabulary():
    """The guard's power is that a fabricated id is OUTSIDE the sparse real id
    set, so it rejects. A future 'compress ids to 1..N' change would put this
    same lattice INSIDE the vocabulary and silently mis-attribute it — this
    test fails loudly if the id vocabulary is ever densified under the model."""
    fabricated = {i for ids in _RUN28_LATTICE for i in ids}
    assert fabricated.isdisjoint(_RUN28_WINDOW_IDS)


# --- the CORRECTED retry: not a byte-identical re-POST (run 28 fix) ----------------

def _resp(payload, finish_reason=None):
    """A parsed /chat/completions response as _post_chat returns it (a dict,
    not bytes) — for monkeypatching _post_chat to sequence attempts."""
    choice = {"message": {"content": json.dumps(payload)}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_malformed_retry_carries_a_correction_and_recovers(monkeypatch):
    """Attempt 1 fabricates -> validation fails; the retry must send a DIFFERENT
    prompt (carrying RETRY_CORRECTION), which recovers. Proves the retry is a
    real second draw, not the guaranteed-identical re-POST that burned run 28's
    second ~$0.025."""
    sent = []
    responses = [
        _resp({"clusters": [cluster([9999])]}),          # invented id -> rejected
        _resp({"clusters": [cluster([1, 2])]}),          # valid -> recovers
    ]
    monkeypatch.setattr(ranking, "_post_chat",
                        lambda key, prompt: (sent.append(prompt), responses.pop(0))[1])
    clusters, usage = ranking.call_llm_validated("sk-x", "BASE-PROMPT", KNOWN_IDS, TAGS, MEMORY)
    assert [c["item_ids"] for c in clusters] == [[1, 2]]   # recovered on the retry
    assert len(sent) == 2
    assert sent[0] == "BASE-PROMPT"                        # attempt 1 unchanged
    assert sent[1] != sent[0]                              # retry is NOT identical
    assert "CORRECTION" in sent[1] and sent[1].startswith("BASE-PROMPT")


def test_transport_retry_re_sends_the_original_prompt_unchanged(monkeypatch):
    """Scope guard: a 5xx/transport retry is NOT the model's fault, so its retry
    must re-send the original prompt with no correction appended (the correction
    is malformed-output only)."""
    sent = []
    calls = {"n": 0}

    def fake_post(key, prompt):
        sent.append(prompt)
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("u", 503, "down", {}, None)
        return _resp({"clusters": [cluster([1])]})

    monkeypatch.setattr(ranking, "_post_chat", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    clusters, _ = ranking.call_llm_validated("sk-x", "BASE-PROMPT", KNOWN_IDS, TAGS, MEMORY)
    assert len(sent) == 2 and sent[0] == sent[1] == "BASE-PROMPT"
    assert all("CORRECTION" not in p for p in sent)
