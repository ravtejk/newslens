"""NL-17-M1 increment A — the altitude resolver + the falsifier instrument.

Born-red where they pin law (dispatch 2026-07-17):
  * TWO RUNGS ONLY / no-new-vocabulary: ALTITUDES == (entity, storyline) and the
    validator REJECTS any third rung — a widened vocabulary flips these red.
  * KASS'S CLAUSE: a pick with no disclosure line is rejected (a default the
    reader taps once must NAME the altitude in words, or it is silent inference).
  * CORRECTED RETRY (rank/generate law): a malformed answer takes ONE corrected
    retry that echoes the exact failure; every billed attempt lands in cost_sink.
  * ZERO SELECTION-WEIGHT TOUCH / read-only: the falsifier opens the record via
    db.connect_readonly ONLY (db.connect is a tripwire), and a --run adds ZERO
    rows to memory / thread_state / thread_deltas — the output is a REPORT, never
    edition state or a selection weight (NL-17 acceptance (a)-(d) untouched).
  * FAIL-LOUD: an unavailable subscription lane at --run dies with a named fix
    before any transport.
  * SANDBOX DISCIPLINE: the instrument runs in-process under the autouse
    conftest sandbox (scrub_env / sandbox_paths / loopback_only_network /
    real_state_tripwire) — zero live calls, $0, real state never touched.

Offline by construction: llm.chat is monkeypatched to canned responses; no
network, no subprocess, no real key.
"""

from __future__ import annotations

import json

import pytest

from newslens import db, follow_altitude as fa, llm, paths, ranking


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _fake_response(content: str, finish: str = "stop",
                   pt: int = 1200, ct: int = 40) -> "llm.LaneResponse":
    """A LaneResponse with the OpenAI-shaped .raw the resolver parses."""
    raw = {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "cache_creation_tokens": 0},
    }
    return llm.LaneResponse(
        content=content, usage=llm.Usage(prompt_tokens=pt, completion_tokens=ct),
        finish_reason=finish, raw=raw)


class _Chat:
    """Stateful llm.chat stand-in: records each request's user prompt, returns
    the next scripted (content, finish)."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.systems = []
        self.calls = 0

    def __call__(self, req):
        self.calls += 1
        self.prompts.append(req.prompt)
        self.systems.append(req.system)
        content, finish = self.replies.pop(0)
        return _fake_response(content, finish)


def _pick(altitude="entity", primary="Volkswagen",
          disclosure="Following Volkswagen — the company, not just this story.",
          confidence="high") -> str:
    return json.dumps({"altitude": altitude, "primary_entity": primary,
                       "disclosure": disclosure, "confidence": confidence})


def _seed_db(rows):
    """Migrate + seed the SANDBOX DB (paths.DB_PATH, redirected by conftest)
    with memory rows. rows: list of (topic, status)."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    now = "2026-07-01T00:00:00.000Z"
    try:
        for topic, status in rows:
            con.execute(
                "INSERT INTO memory (topic, status, status_changed_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (topic, status, now, now, now))
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------
# seat registration (the seam law)
# --------------------------------------------------------------------------

def test_seat_registered_haiku_subscription_default():
    cfg = llm.SEATS["follow_altitude"]
    assert cfg.model == "claude-haiku-4-5"
    assert cfg.provider == "anthropic"
    assert cfg.lane == "subscription"          # matches the Haiku family + mandate
    assert cfg.thinking is None and cfg.effort is None   # mechanical, not reasoning


def test_seat_is_not_a_generate_step():
    """follow_altitude is a resolver seat, never a `generate` edition step — it
    must not be routable through seat_for_step (a silent default there would bill
    the Opus writer). Pins the deliberate exclusion from _STEP_PREFIX_SEAT."""
    with pytest.raises(ValueError):
        llm.seat_for_step("follow_altitude")


def test_effective_seat_resolves_under_the_stub_bin():
    # conftest points NEWSLENS_CLAUDE_BIN at the canned stub, so the
    # subscription-default seat gates cleanly with no real binary.
    cfg, reason = llm.effective_seat("follow_altitude")
    assert cfg.seat == "follow_altitude" and cfg.lane == "subscription"
    assert reason is None


# --------------------------------------------------------------------------
# no-new-vocabulary: two rungs only (BORN-RED)
# --------------------------------------------------------------------------

def test_altitudes_are_exactly_two_rungs():
    # v1 rungs = entity + storyline (principal ruling 2026-07-17, DECISIONS
    # "THE STORYLINE CORRECTION"); industry/region stay deferred to NL-17/18.
    assert fa.ALTITUDES == ("entity", "storyline")


@pytest.mark.parametrize("bad", ["region", "industry", "topic", "company", ""])
def test_validate_rejects_third_rung(bad):
    # 'industry' is now REJECTED exactly like 'region' — the deferred rungs.
    with pytest.raises(ValueError):
        fa._validate(_pick(altitude=bad))


def test_validate_accepts_storyline():
    """The flip's positive half: 'storyline' is now a first-class rung (the
    thread/topic tier — the ongoing story at proper altitude)."""
    parsed = fa._validate(_pick(
        altitude="storyline", primary="Volkswagen",
        disclosure="Following the Volkswagen job-cuts story — the ongoing "
                   "story, not just this article."))
    assert parsed["altitude"] == "storyline"
    assert parsed["primary_entity"] == "Volkswagen"


def test_resolver_rejects_persistent_third_rung(monkeypatch):
    """A model that keeps answering 'industry' (a rung v1 does NOT ship — the
    storyline correction deferred it) fails after one corrected retry — the
    no-new-vocabulary law, end to end."""
    chat = _Chat([(_pick(altitude="industry"), "stop"),
                  (_pick(altitude="industry"), "stop")])
    monkeypatch.setattr(llm, "chat", chat)
    with pytest.raises(fa.AltitudeError):
        fa.resolve_altitude(fa.ThreadInput(1, "Some industry thread"))
    assert chat.calls == 2
    # the corrected retry echoed the exact rule that failed
    assert "two rungs only" in chat.prompts[1]


# --------------------------------------------------------------------------
# Kass's clause: the disclosure must name the altitude in words (BORN-RED)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("disclosure", ["", "   ", None])
def test_validate_requires_disclosure(disclosure):
    payload = {"altitude": "entity", "primary_entity": "Volkswagen",
               "confidence": "high"}
    if disclosure is not None:
        payload["disclosure"] = disclosure
    with pytest.raises(ValueError):
        fa._validate(json.dumps(payload))


def test_resolver_rejects_missing_disclosure(monkeypatch):
    chat = _Chat([(_pick(disclosure=""), "stop"), (_pick(disclosure=""), "stop")])
    monkeypatch.setattr(llm, "chat", chat)
    with pytest.raises(fa.AltitudeError):
        fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen"))
    assert "disclosure" in chat.prompts[1]


# --------------------------------------------------------------------------
# the happy path + the corrected-retry law + money honesty
# --------------------------------------------------------------------------

def test_resolver_parses_entity_pick(monkeypatch):
    chat = _Chat([(_pick(), "stop")])
    monkeypatch.setattr(llm, "chat", chat)
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(22, "Volkswagen"), cost_sink=sink)
    assert res.altitude == "entity"
    assert res.primary_entity == "Volkswagen"
    assert res.disclosure.startswith("Following Volkswagen")
    assert res.confidence == "high"
    assert res.attempts == 1
    # the stable law rides the system prefix; the title rides the user prompt
    assert "THREAD TITLE: Volkswagen" in chat.prompts[0]
    assert chat.systems[0] and "altitude" in chat.systems[0].lower()
    # cost_sink carries the full shadow-ledger keys (subscription lane: $0
    # charged, Haiku-priced shadow > 0)
    assert len(sink) == 1
    assert sink[0]["usd_shadow"] > 0
    assert sink[0]["usd_charged"] == 0.0
    assert sink[0]["lane"] == "subscription"
    assert sink[0]["model"] == "claude-haiku-4-5"


def test_resolver_one_corrected_retry_records_both_attempts(monkeypatch):
    """Malformed then valid: attempts==2, the retry is CORRECTED (echoes the
    failure, anchored to the original), and BOTH billed attempts are in the sink
    (a malformed attempt that still billed is never lost)."""
    chat = _Chat([("not json at all", "stop"), (_pick(), "stop")])
    monkeypatch.setattr(llm, "chat", chat)
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen"), cost_sink=sink)
    assert res.attempts == 2
    assert chat.calls == 2
    assert len(sink) == 2                     # money honesty: both attempts logged
    assert fa._RETRY_CORRECTION_PREFIX in chat.prompts[1]
    assert chat.prompts[1].startswith("THREAD TITLE:")   # anchored to the original


def test_truncation_is_a_corrected_retry(monkeypatch):
    chat = _Chat([(_pick(), "length"), (_pick(), "stop")])
    monkeypatch.setattr(llm, "chat", chat)
    res = fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen"))
    assert res.attempts == 2
    assert "truncated" in chat.prompts[1]


def test_api_override_charges_the_shadow(monkeypatch):
    """NEWSLENS_LANE_FOLLOW_ALTITUDE=api -> usd_charged == usd_shadow (the api
    lane bills); the subscription default charges $0. Pins the lane/shadow
    ledger correctness for the new seat."""
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "api")
    chat = _Chat([(_pick(), "stop")])
    monkeypatch.setattr(llm, "chat", chat)
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen"), cost_sink=sink)
    assert res.lane == "api"
    assert sink[0]["usd_charged"] == sink[0]["usd_shadow"] > 0


# --------------------------------------------------------------------------
# read-only DB reader
# --------------------------------------------------------------------------

def test_followed_threads_excludes_dismissed():
    _seed_db([("Volkswagen", "active"), ("Fed policy", "dormant"),
              ("redistrictinga", "dismissed_user")])
    con = db.connect_readonly(paths.DB_PATH)
    try:
        rows = fa.followed_threads(con)
    finally:
        con.close()
    topics = [r["topic"] for r in rows]
    assert "Volkswagen" in topics and "Fed policy" in topics
    assert "redistrictinga" not in topics       # dismissed = unfollowed


# --------------------------------------------------------------------------
# the falsifier instrument
# --------------------------------------------------------------------------

def test_dryrun_makes_zero_calls_and_zero_writes(monkeypatch, capsys):
    _seed_db([("Volkswagen", "active"), ("Fed policy", "dormant"),
              ("redistrictinga", "dismissed_user")])
    # tripwires: no LLM call, no writer connection in dry-run
    monkeypatch.setattr(llm, "chat",
                        lambda req: pytest.fail("llm.chat called in dry-run"))
    monkeypatch.setattr(db, "connect",
                        lambda *a, **k: pytest.fail("db.connect (writer) called"))
    before = sorted(p.name for p in (paths.DATA_DIR).iterdir())
    rc = fa.main([])
    after = sorted(p.name for p in (paths.DATA_DIR).iterdir())
    assert rc == 0
    assert before == after                      # zero writes (no follow_altitude/)
    out = capsys.readouterr().out
    assert "followed threads: 2" in out         # dismissed excluded from the count
    assert "DRY RUN" in out
    assert "subscription" in out                # the resolved lane is disclosed


def test_dryrun_refuses_absent_record(capsys):
    # no DB seeded -> connect_readonly raises -> clean refusal, no traceback
    rc = fa.main([])
    assert rc == 1
    assert "refused" in capsys.readouterr().err


def test_run_resolves_followed_threads_and_writes_report(monkeypatch, capsys):
    _seed_db([("Volkswagen", "active"), ("Fed policy", "dormant"),
              ("redistrictinga", "dismissed_user")])

    def picker(req):
        # echo the title as the primary entity so the report is legible
        title = req.prompt.split("THREAD TITLE:", 1)[1].splitlines()[0].strip()
        return _fake_response(_pick(primary=title,
                                    disclosure=f"Following {title} — the company."))
    monkeypatch.setattr(llm, "chat", picker)
    # ZERO-SELECTION-WEIGHT / read-only: the writer connection must never open
    monkeypatch.setattr(db, "connect",
                        lambda *a, **k: pytest.fail("db.connect (writer) called"))

    rc = fa.main(["--run"])
    assert rc == 0

    report_dir = paths.DATA_DIR / "follow_altitude" / ranking.local_today()
    report = json.loads((report_dir / "report.json").read_text())
    assert report["resolved"] == 2 and report["failed"] == 0
    assert report["followed_total"] == 2
    names = {r["primary_entity"] for r in report["results"]}
    assert names == {"Volkswagen", "Fed policy"}      # dismissed excluded
    assert report["usd_charged_total"] == 0.0         # subscription lane


def test_run_adds_zero_rows_to_the_record(monkeypatch):
    """The resolver's output is a REPORT, not state: a --run mutates NO ledger
    table. Read-only by construction (connect_readonly) — this pins it even if a
    future edit tried to write."""
    _seed_db([("Volkswagen", "active"), ("Fed policy", "dormant")])

    def counts():
        con = db.connect_readonly(paths.DB_PATH)
        try:
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("memory", "thread_state", "thread_deltas",
                              "thread_baselines")}
        finally:
            con.close()

    before = counts()
    monkeypatch.setattr(llm, "chat", lambda req: _fake_response(_pick()))
    fa.main(["--run"])
    assert counts() == before                   # zero new rows anywhere


def test_run_fails_loud_when_subscription_lane_unavailable(monkeypatch, capsys):
    """The subscription binary won't resolve -> --run dies at the gate with a
    named fix, BEFORE any transport (fail-loud, one gate upfront)."""
    _seed_db([("Volkswagen", "active")])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN",
                       str(paths.DATA_DIR / "nonexistent-claude"))
    monkeypatch.setattr(llm, "chat",
                        lambda req: pytest.fail("transport reached despite dead lane"))
    rc = fa.main(["--run"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "refused" in err and "claude" in err
