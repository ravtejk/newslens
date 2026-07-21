"""NL-17-M1 increment A — QA adversarial pass (QA-owned; the attacks the
implementer's own file does not carry).

Contract sources: product-4 adjudication 2026-07-16 (two rungs entity +
STORYLINE, storyline = the ongoing story never the headline string, Kass's
disclosure-in-words clause); DECISIONS 2026-07-17 "THE STORYLINE CORRECTION";
TRACKER NL-17 (a)-(d) — this increment is a REPORT, not state.

Attack surfaces pinned here:
  * ONE-RESOLUTION-PER-CALL (B3 D1/D5/D6): a lane/model flap mid-call, an
    armed-fallback DISARM mid-call, a flap mid-RUN, and a binary that VANISHES
    mid-run can never fork the lane the bytes ride from the lane the ledger
    records.
  * MONEY HONESTY: a billed attempt whose envelope is malformed (usage present,
    choices absent) still lands in cost_sink; a truncated attempt is billed;
    the shadow arithmetic is the Haiku table exactly ($1/$5 per Mtok);
    subscription charges $0, the api override and the labeled fall bill shadow.
  * _validate boundary: confidence enum, headline-string altitude, non-string /
    whitespace fields, non-object JSON.
  * READ-ONLY / REPORT-NOT-STATE: dry-run leaves the entire sandbox tree
    byte-stable (recursive, not top-level names) and attempts ZERO socket
    operations; a --run leaves the DB FILE byte-identical (stronger than row
    counts); refusal paths write nothing.
  * SPEND SURFACE OF THE FOLLOWED-SET PREDICATE (the B3-era §F junk-sweep
    precedent): a dismissed thread never draws a transport call, and the legacy
    'dismissed'/'stale' statuses are physically foreclosed by the 0006 CHECK.
  * FAIL-LOUD: an unregistered lane dies loud (never a silent wrong-vendor
    call); a cap breach skips with disclosure and ZERO transport.
  * TRANSPORT END-TO-END: the resolver rides the real anthropic subscription
    provider against a RECORDING shim (no llm.chat monkeypatch) — proving the
    stub-shim guard holds for this seat's spawn path and the envelope mapping
    feeds the validator/ledger correctly.

Offline by construction under the autouse conftest sandbox; $0.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat as stat_mod
import textwrap
from pathlib import Path

import pytest

from newslens import db, follow_altitude as fa, llm, paths, ranking


# --------------------------------------------------------------------------
# helpers (deliberately duplicated from the implementer file — QA's fixtures
# must not inherit a defect in the thing under audit)
# --------------------------------------------------------------------------

def _envelope(content: str, finish: str = "stop", pt: int = 1200, ct: int = 40,
              with_choices: bool = True) -> "llm.LaneResponse":
    raw = {
        "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "cache_creation_tokens": 0},
    }
    if with_choices:
        raw["choices"] = [{"message": {"content": content},
                           "finish_reason": finish}]
    return llm.LaneResponse(
        content=content, usage=llm.Usage(prompt_tokens=pt, completion_tokens=ct),
        finish_reason=finish, raw=raw)


def _pick(altitude="entity", primary="Volkswagen",
          disclosure="Following Volkswagen — the company, not just this story.",
          confidence="high") -> str:
    return json.dumps({"altitude": altitude, "primary_entity": primary,
                       "disclosure": disclosure, "confidence": confidence})


def _seed_db(rows):
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


def _tree_state(root: Path):
    """Recursive byte-level fingerprint of a directory tree: every path with
    size+mtime_ns+sha256. Catches writes INTO existing subdirs, which a
    top-level name listing cannot (the v7-M2 in-place-rewrite lesson)."""
    state = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            st = p.stat()
            state[str(p)] = (st.st_size, st.st_mtime_ns, h)
        elif p.is_dir():
            state[str(p)] = "dir"
    return state


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_shim(dir_path: Path, result_content: str, self_destruct: bool = False,
               inp: int = 1000, out: int = 50) -> Path:
    """A minimal recording `claude` shim: answers --version stdin-free,
    otherwise records argv+stdin to rec-<n>.json in its own dir and prints a
    canned subscription-lane success envelope whose `result` is
    `result_content`. Optionally deletes itself after one call (the D5
    binary-vanish flap)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    src = textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys, os, json
        if '--version' in sys.argv[1:]:
            print('2.1.212 (NL17 QA recording shim)'); sys.exit(0)
        DIR = {dir!r}
        cnt = os.path.join(DIR, 'calls.count')
        n = 1
        if os.path.exists(cnt):
            with open(cnt) as f:
                n = int(f.read().strip() or 0) + 1
        with open(cnt, 'w') as f:
            f.write(str(n))
        data = sys.stdin.read()
        with open(os.path.join(DIR, 'rec-%d.json' % n), 'w') as f:
            json.dump({{'argv': sys.argv[1:], 'stdin': data, 'pid': os.getpid()}}, f)
        if {self_destruct!r}:
            try:
                os.remove(sys.argv[0])
            except OSError:
                pass
        print(json.dumps({{'type': 'result', 'subtype': 'success',
                           'is_error': False, 'result': {content!r},
                           'session_id': 'nl17-qa', 'total_cost_usd': 0.0,
                           'usage': {{'input_tokens': {inp}, 'output_tokens': {out},
                                      'cache_read_input_tokens': 0}}}}))
        """).format(dir=str(dir_path), content=result_content,
                    self_destruct=self_destruct, inp=inp, out=out)
    shim = dir_path / "claude"
    shim.write_text(src)
    shim.chmod(shim.stat().st_mode | stat_mod.S_IXUSR)
    return shim


# --------------------------------------------------------------------------
# hermeticity sentinels: the two NEW scrub vars are actually scrubbed
# --------------------------------------------------------------------------

def test_new_seat_env_overrides_are_scrubbed_in_suite():
    """conftest gained NEWSLENS_LANE_FOLLOW_ALTITUDE and
    NEWSLENS_MODEL_FOLLOW_ALTITUDE in SCRUBBED_ENV_VARS — the D2-hermeticity
    class. If either is visible here, an ambient shell export could silently
    re-lane/re-model every resolver test (proven to bite for the writer seat,
    QA 2026-07-17). The out-of-process hostile-ambient proof is run by QA in
    the pass itself; this sentinel keeps the scrub list from regressing."""
    assert "NEWSLENS_LANE_FOLLOW_ALTITUDE" not in os.environ
    assert "NEWSLENS_MODEL_FOLLOW_ALTITUDE" not in os.environ
    import conftest as _cft
    assert "NEWSLENS_LANE_FOLLOW_ALTITUDE" in _cft.SCRUBBED_ENV_VARS
    assert "NEWSLENS_MODEL_FOLLOW_ALTITUDE" in _cft.SCRUBBED_ENV_VARS


def test_step_prefix_map_never_routes_to_the_resolver_seat():
    """Structural half of the implementer's seat_for_step pin: no generate step
    prefix maps TO follow_altitude, so the resolver seat is unreachable from
    generate.call_llm by construction (a REPORT seat, never an edition step)."""
    assert "follow_altitude" not in {seat for _, seat in llm._STEP_PREFIX_SEAT}


# --------------------------------------------------------------------------
# one-resolution-per-call: the B3 D1/D5/D6 flap family
# --------------------------------------------------------------------------

def test_mid_call_lane_and_model_flap_cannot_fork_transport_or_ledger(monkeypatch):
    """D1/D6 for the resolver: attempt 1 fails validation AND the per-seat lane
    + model env flip mid-call. The seat was resolved ONCE — attempt 2's bytes
    and BOTH ledger rows must stay on the original api/Haiku resolution (the
    RESOLVER LANE FIX default). The mid-call flap tries to yank the lane to
    SUBSCRIPTION and the model to fable-5; a fork to either is the B3-D6 breach."""
    seen_cfgs = []

    def chat(req):
        seen_cfgs.append((req.cfg.lane, req.cfg.model, req.cfg.seat))
        if len(seen_cfgs) == 1:
            # the flap lands between the two transport attempts — flip AWAY from
            # the api default, so a re-resolution would be visibly detectable.
            monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "subscription")
            monkeypatch.setenv("NEWSLENS_MODEL_FOLLOW_ALTITUDE", "claude-fable-5")
            return _envelope("not json")
        return _envelope(_pick())

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen"), cost_sink=sink)
    assert res.attempts == 2
    assert seen_cfgs == [("api", "claude-haiku-4-5", "follow_altitude")] * 2
    assert [e["lane"] for e in sink] == ["api", "api"]
    assert [e["model"] for e in sink] == ["claude-haiku-4-5"] * 2
    assert all(e["usd_charged"] == e["usd_shadow"] > 0 for e in sink)   # api bills
    assert res.lane == "api"


def test_armed_fall_is_labeled_on_every_row_and_survives_midcall_disarm(monkeypatch):
    """D5: binary dead at the gate + NEWSLENS_LANE_FALLBACK=api armed -> ONE
    labeled fall. Attempt 1 then fails validation and the fallback var is
    DISARMED mid-call. The captured resolution must carry attempt 2 and label
    EVERY cost row 'api(fallback:subscription_unavailable)' — never a bare
    'api' hiding real API spend, never a re-gate back to a dead lane."""
    # RESOLVER LANE FIX: the armed subscription->api fall only exists when the seat
    # STARTS on subscription; force it (api is the default now) so this D5 test
    # exercises the labeled fall, not a plain api resolution.
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "subscription")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/nonexistent/claude-nl17-qa")
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    calls = []

    def chat(req):
        calls.append(req.cfg.lane)
        if len(calls) == 1:
            monkeypatch.delenv("NEWSLENS_LANE_FALLBACK", raising=False)
            return _envelope("not json")
        return _envelope(_pick())

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen"), cost_sink=sink)
    assert calls == ["api", "api"]
    assert res.attempts == 2
    label = "api(fallback:subscription_unavailable)"
    assert [e["lane"] for e in sink] == [label, label]
    # the fallen lane BILLS: charged == shadow > 0 on both rows
    assert all(e["usd_charged"] == e["usd_shadow"] > 0 for e in sink)
    assert res.lane == label


def test_falsifier_run_rides_one_resolution_across_all_threads(monkeypatch):
    """D6 at run scope: the --run gate resolves ONCE; a per-thread env flap
    between transport calls must not re-lane later threads. Every cost row and
    the report's seat block stay on the run-scoped resolution."""
    _seed_db([("Alpha", "active"), ("Beta", "active"), ("Gamma", "active")])
    lanes_seen = []

    def chat(req):
        lanes_seen.append(req.cfg.lane)
        # hostile: flip the per-seat lane var between threads
        monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE",
                           "subscription" if len(lanes_seen) % 2 else "api")
        title = req.prompt.split("THREAD TITLE:", 1)[1].splitlines()[0].strip()
        return _envelope(_pick(primary=title,
                               disclosure=f"Following {title} — the company."))

    monkeypatch.setattr(llm, "chat", chat)
    rc = fa.main(["--run"])
    assert rc == 0
    # RESOLVER LANE FIX: the --run gate resolves ONCE on the api default; the
    # hostile mid-run flap toward subscription must not re-lane later threads.
    assert lanes_seen == ["api"] * 3
    report = json.loads(
        (paths.DATA_DIR / "follow_altitude" / ranking.local_today()
         / "report.json").read_text())
    assert report["seat"]["lane"] == "api"
    assert {e["lane"] for e in report["cost_attempts"]} == {"api"}
    # api lane bills: charged total == shadow total > 0 (was $0 on subscription)
    assert report["usd_charged_total"] == report["usd_shadow_total"] > 0


# --------------------------------------------------------------------------
# money honesty: billed-but-malformed, truncation billing, exact arithmetic
# --------------------------------------------------------------------------

def test_billed_attempt_with_no_choices_still_lands_in_sink(monkeypatch):
    """The money-before-validation law at its sharpest: an envelope that
    returned USAGE (billed) but no choices at all (shape failure downstream of
    the sink append) must still be in the money record — then the corrected
    retry runs and the total is BOTH attempts."""
    replies = [
        _envelope("ignored", with_choices=False),   # billed, malformed
        _envelope(_pick()),
    ]
    monkeypatch.setattr(llm, "chat", lambda req: replies.pop(0))
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen"), cost_sink=sink)
    assert res.attempts == 2
    assert len(sink) == 2
    assert [e["attempt"] for e in sink] == [1, 2]
    # every row carries the full lane/shadow ledger key set + the legacy usd
    for e in sink:
        for key in ("seat", "attempt", "prompt_tokens", "completion_tokens",
                    "usd", "model", "lane", "usd_shadow", "usd_charged",
                    "cache_read_tokens", "cache_creation_tokens"):
            assert key in e, key
        assert e["usd"] == e["usd_charged"]          # legacy usd == charged


def test_truncated_attempt_is_billed(monkeypatch):
    """finish_reason=length is a corrected retry — but the truncated attempt
    consumed tokens and must be in the sink (the implementer's truncation test
    does not look at the money record)."""
    replies = [_envelope(_pick(), finish="length"), _envelope(_pick())]
    monkeypatch.setattr(llm, "chat", lambda req: replies.pop(0))
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(1, "VW"), cost_sink=sink)
    assert res.attempts == 2 and len(sink) == 2


def test_shadow_arithmetic_is_the_haiku_table_exactly(monkeypatch):
    """usd_shadow must be pt/1e6*$1.00 + ct/1e6*$5.00 to the cent — the seat
    table, not an estimate. Subscription (forced via the escape hatch): charged 0.
    api (the RESOLVER LANE FIX default, here carrying a model arm): charged ==
    shadow (same table — the model override never re-prices)."""
    pt, ct = 200_000, 40_000            # -> 0.2 + 0.2 = $0.40 exactly
    monkeypatch.setattr(llm, "chat", lambda req: _envelope(_pick(), pt=pt, ct=ct))
    # the $0 path is now the SUBSCRIPTION escape hatch (api is the default).
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "subscription")
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(1, "VW"), cost_sink=sink)
    assert res.lane == "subscription"
    assert sink[0]["usd_shadow"] == pytest.approx(0.40)
    assert sink[0]["usd_charged"] == 0.0 and res.usd_charged == 0.0

    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "api")
    monkeypatch.setenv("NEWSLENS_MODEL_FOLLOW_ALTITUDE", "claude-fable-5")
    sink2 = []
    res2 = fa.resolve_altitude(fa.ThreadInput(1, "VW"), cost_sink=sink2)
    assert res2.lane == "api"
    assert sink2[0]["model"] == "claude-fable-5"     # the arm is recorded...
    assert sink2[0]["usd_shadow"] == pytest.approx(0.40)   # ...priced at the SEAT
    assert sink2[0]["usd_charged"] == pytest.approx(0.40)


# --------------------------------------------------------------------------
# _validate boundary: the edges the implementer's file leaves open
# --------------------------------------------------------------------------

@pytest.mark.parametrize("confidence", ["certain", "", "HIGH", 3, None])
def test_validate_rejects_bad_confidence(confidence):
    payload = {"altitude": "entity", "primary_entity": "VW",
               "disclosure": "Following VW — the company."}
    if confidence is not None:
        payload["confidence"] = confidence
    with pytest.raises(ValueError):
        fa._validate(json.dumps(payload))


def test_validate_rejects_the_headline_string_as_altitude():
    """The literal banned failure mode from the round: the headline string is
    never an altitude."""
    with pytest.raises(ValueError):
        fa._validate(_pick(altitude="Volkswagen plans significant job cuts"))


@pytest.mark.parametrize("payload", [
    "[]",                                   # JSON array, not an object
    '"entity"',                             # bare string
    json.dumps({"altitude": "entity", "primary_entity": 42,
                "disclosure": "d", "confidence": "high"}),
    json.dumps({"altitude": "entity", "primary_entity": "  ",
                "disclosure": "d", "confidence": "high"}),
    json.dumps({"altitude": "entity", "primary_entity": "VW",
                "disclosure": 42, "confidence": "high"}),
])
def test_validate_rejects_malformed_shapes(payload):
    with pytest.raises(ValueError):
        fa._validate(payload)


def test_validate_strips_whitespace_on_accept():
    parsed = fa._validate(json.dumps({
        "altitude": "storyline", "primary_entity": "  Volkswagen  ",
        "disclosure": "  Following the VW job-cuts story — the ongoing story.  ",
        "confidence": "low"}))
    assert parsed["primary_entity"] == "Volkswagen"
    assert parsed["disclosure"].startswith("Following")


def test_prompt_file_teaches_exactly_the_two_rungs():
    """Prompts are code: the versioned law file must offer entity + storyline
    and explicitly forbid inventing a third rung (the prompt-side half of the
    no-new-vocabulary tripwire; the validator is the enforcement half)."""
    law = (paths.PROMPTS_DIR / "follow_altitude.txt").read_text(encoding="utf-8")
    assert '"entity"' in law and '"storyline"' in law
    assert "only two rungs" in law                       # wrap-proof anchor
    assert 'no "industry", no "region"' in law           # deferred rungs forbidden
    # Kass's clause survives the M1b compact re-land (2026-07-18): the disclosure
    # still NAMES the altitude in words — the tailed-sentence phrasing became the
    # compact qualifier grammar, but "in words" is the invariant.
    assert "names the altitude in words" in law          # Kass's clause
    assert fa._system_law() == law                       # the file IS the system law


# --------------------------------------------------------------------------
# read-only / report-not-state: recursive write-proof, socket-proof, byte-proof
# --------------------------------------------------------------------------

def test_dryrun_is_byte_stable_and_socket_silent(tmp_paths, no_network, capsys):
    """Dry-run law, mechanically: the ENTIRE sandbox tree (recursive sha256,
    not top-level names — a write into an existing subdir must be caught) is
    byte-identical across the run, and ZERO socket operations are attempted
    (no_network records and refuses everything including loopback)."""
    _seed_db([("Volkswagen", "active"), ("Fed policy", "dormant")])
    before = _tree_state(tmp_paths)
    rc = fa.main([])
    assert rc == 0
    assert _tree_state(tmp_paths) == before
    assert no_network == []                  # not one DNS lookup or connect
    assert "DRY RUN" in capsys.readouterr().out


def test_run_leaves_the_db_file_byte_identical(monkeypatch):
    """--run's read-only law at byte grade: connect_readonly means the DB file
    is not merely row-stable but BYTE-identical (no touched-and-rolled-back
    writes, no WAL side effects against the record)."""
    _seed_db([("Volkswagen", "active"), ("Fed policy", "dormant")])
    db_hash_before = _sha(paths.DB_PATH)
    monkeypatch.setattr(llm, "chat", lambda req: _envelope(_pick()))
    rc = fa.main(["--run"])
    assert rc == 0
    assert _sha(paths.DB_PATH) == db_hash_before
    # and the report landed OUTSIDE the DB, under the sandbox data dir
    report = (paths.DATA_DIR / "follow_altitude" / ranking.local_today()
              / "report.json")
    assert report.is_file()


def test_run_honors_out_flag_and_writes_nothing_under_data_dir(monkeypatch, tmp_paths):
    _seed_db([("Volkswagen", "active")])
    monkeypatch.setattr(llm, "chat", lambda req: _envelope(_pick()))
    alt_out = tmp_paths / "elsewhere"
    rc = fa.main(["--out", str(alt_out), "--run"])
    assert rc == 0
    assert (alt_out / ranking.local_today() / "report.json").is_file()
    assert not (paths.DATA_DIR / "follow_altitude").exists()


def test_empty_followed_set_short_circuits_before_any_run_machinery(monkeypatch, capsys):
    """Zero followed threads: even --run exits 0 with the friendly note, no
    transport, no report artifact (nothing to falsify is not a failure)."""
    _seed_db([])
    monkeypatch.setattr(llm, "chat",
                        lambda req: pytest.fail("transport on empty set"))
    rc = fa.main(["--run"])
    assert rc == 0
    assert "no followed threads" in capsys.readouterr().out
    assert not (paths.DATA_DIR / "follow_altitude").exists()


def test_gate_refusal_writes_no_artifact(monkeypatch, capsys):
    """--run with a dead subscription binary refuses BEFORE the report dir is
    created — a refused run leaves zero trace on disk."""
    _seed_db([("Volkswagen", "active")])
    # RESOLVER LANE FIX: force subscription so the dead-binary gate fires (api,
    # the new default, needs no binary and would not refuse here).
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "subscription")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/nonexistent/claude-nl17-qa")
    rc = fa.main(["--run"])
    assert rc == 1
    assert "refused" in capsys.readouterr().err
    assert not (paths.DATA_DIR / "follow_altitude").exists()


# --------------------------------------------------------------------------
# the followed-set predicate as a SPEND surface (§F junk-sweep precedent)
# --------------------------------------------------------------------------

def test_dismissed_threads_never_draw_a_transport_call(monkeypatch):
    """The B3-era §F breach class, pinned at the spend surface: a
    dismissed_user thread must never reach transport — not merely be absent
    from the report. Exactly one call, and its prompt names only the live
    thread."""
    _seed_db([("Volkswagen", "active"), ("redistrictinga", "dismissed_user")])
    prompts = []

    def chat(req):
        prompts.append(req.prompt)
        return _envelope(_pick())

    monkeypatch.setattr(llm, "chat", chat)
    rc = fa.main(["--run"])
    assert rc == 0
    assert len(prompts) == 1
    assert "THREAD TITLE: Volkswagen" in prompts[0]
    assert "redistrictinga" not in prompts[0]


@pytest.mark.parametrize("legacy", ["dismissed", "stale"])
def test_legacy_statuses_are_physically_foreclosed_by_schema(legacy):
    """Migration 0006 rebuilt memory with CHECK (status IN
    ('active','dormant','dismissed_user')): the legacy vocabulary cannot even
    be INSERTED, so the predicate's exclusion set is closed — no un-migrated
    status can leak into the followed set on a migrated DB."""
    _seed_db([])
    con = db.connect(paths.DB_PATH)
    try:
        with pytest.raises(db.sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO memory (topic, status, status_changed_at, "
                "created_at, updated_at) VALUES ('X', ?, 't', 't', 't')",
                (legacy,))
    finally:
        con.close()


# --------------------------------------------------------------------------
# fail-loud: unregistered lane, cap breach
# --------------------------------------------------------------------------

def test_unregistered_lane_dies_loud_never_a_wrong_vendor_call(monkeypatch):
    """NEWSLENS_LANE_FOLLOW_ALTITUDE=bogus: the gate must die LaneUnavailable
    before any transport — never fall through to some other registered
    provider (the silent wrong-vendor call the seam law exists to prevent)."""
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "bogus")
    monkeypatch.setattr(llm, "chat",
                        lambda req: pytest.fail("transport on unregistered lane"))
    with pytest.raises(llm.LaneUnavailable):
        fa.resolve_altitude(fa.ThreadInput(1, "VW"))


def test_cap_breach_skips_with_disclosure_and_zero_transport(monkeypatch, capsys):
    """A cap smaller than one thread's estimate: every thread SKIPs with the
    arithmetic disclosed, --run reaches zero transport, and the honest report
    says resolved 0 (exit 1 — an instrument that resolved nothing must not
    look like a verdict input)."""
    _seed_db([("Volkswagen", "active"), ("Fed policy", "active")])
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "0.0000001")
    monkeypatch.setattr(llm, "chat",
                        lambda req: pytest.fail("transport despite cap breach"))
    rc = fa.main(["--run"])
    out = capsys.readouterr().out
    assert rc == 1
    assert out.count("SKIP") == 2 and "would exceed" in out
    report = json.loads(
        (paths.DATA_DIR / "follow_altitude" / ranking.local_today()
         / "report.json").read_text())
    assert report["resolved"] == 0 and report["cost_attempts"] == []


# --------------------------------------------------------------------------
# records correctness: no pre-flip wording survives in the shipped sources
# --------------------------------------------------------------------------

def test_no_stale_pre_flip_rung_wording_in_shipped_sources():
    """BORN RED (QA defect pin, 2026-07-17). THE STORYLINE CORRECTION exists
    because a wrong rung word ('industry') propagated through the records into
    a dispatch. Two comments still carry the pre-flip wording and WILL be the
    next reader's copy source:

      * src/newslens/llm.py, SEATS follow_altitude comment: says the seat
        picks "entity|industry" — the code picks entity|storyline.
      * tests/test_nl17_m1_altitude.py module docstring: says
        "ALTITUDES == (entity, industry)" — the assertion pins
        ("entity", "storyline").

    FIX CONTRACT (flips this green): correct both comments to storyline
    wording ('entity|storyline' / '(entity, storyline)'). No behavior change —
    this is a records fix; the storyline correction's own origin story is why
    comment-level rung wording is treated as load-bearing here."""
    llm_src = Path(llm.__file__).read_text(encoding="utf-8")
    assert "entity|industry" not in llm_src, (
        "llm.py SEATS comment still describes the pre-flip 'entity|industry' "
        "contract; the ruled contract is entity|storyline")
    impl_tests = (Path(__file__).parent / "test_nl17_m1_altitude.py"
                  ).read_text(encoding="utf-8")
    assert "(entity, industry)" not in impl_tests, (
        "the implementer test file's docstring still claims ALTITUDES == "
        "(entity, industry); the pinned tuple is (entity, storyline)")


# --------------------------------------------------------------------------
# transport end-to-end: the real subscription provider on a recording shim
# --------------------------------------------------------------------------

def test_resolver_rides_the_real_subscription_transport_via_shim(
        monkeypatch, tmp_paths):
    """NO llm.chat monkeypatch: the resolver's call goes through the actual
    anthropic:subscription provider, which must spawn the RECORDING shim (never
    the real ~/.local/bin/claude — the conftest stub-shim guard extended to
    this seat's spawn path, proven by the rec file), parse the envelope through
    the validator, and bill $0 with a Haiku-priced shadow."""
    shim_dir = tmp_paths / "shim"
    _make_shim(shim_dir, _pick(
        altitude="storyline", primary="Volkswagen",
        disclosure="Following the Volkswagen job-cuts story — the ongoing "
                   "story, not just this article."))
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(shim_dir / "claude"))
    # RESOLVER LANE FIX: this test is ABOUT the subscription spawn path, so force
    # the (now fallback) subscription lane — the api default would never spawn a shim.
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "subscription")
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(22, "Volkswagen job cuts"),
                              cost_sink=sink)
    assert res.altitude == "storyline"
    assert res.primary_entity == "Volkswagen"
    assert res.attempts == 1
    rec = json.loads((shim_dir / "rec-1.json").read_text())
    assert "THREAD TITLE: Volkswagen job cuts" in rec["stdin"]
    assert sink[0]["lane"] == "subscription"
    assert sink[0]["usd_charged"] == 0.0
    assert sink[0]["usd_shadow"] > 0


def test_falsifier_survives_binary_vanish_mid_run_without_forking(
        monkeypatch, tmp_paths, capsys):
    """The D5 vanish at RUN scope: the shim answers thread 1 then deletes
    itself. Thread 2's transport fails, is DISCLOSED, and the run continues to
    an honest partial report — with every recorded cost row still on the one
    run-scoped subscription resolution (no re-gate, no fork, no silent
    lane change mid-run)."""
    _seed_db([("Alpha", "active"), ("Beta", "active")])
    shim_dir = tmp_paths / "shim"
    _make_shim(shim_dir, _pick(primary="Alpha",
                               disclosure="Following Alpha — the company."),
               self_destruct=True)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(shim_dir / "claude"))
    # RESOLVER LANE FIX: force the (now fallback) subscription lane — this run-scope
    # vanish test is about the claude -p spawn path and its subscription ledger.
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "subscription")
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    rc = fa.main(["--run"])
    assert rc == 0                                    # partial success is honest
    report = json.loads(
        (paths.DATA_DIR / "follow_altitude" / ranking.local_today()
         / "report.json").read_text())
    assert report["resolved"] == 1 and report["failed"] == 1
    assert report["failures"][0]["topic"] == "Beta"
    assert {e["lane"] for e in report["cost_attempts"]} == {"subscription"}
    assert "FAILED" in capsys.readouterr().err
