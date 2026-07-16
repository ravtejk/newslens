"""NL-75 — the REFRESH-path liveness guard (2026-07-16 live-contact loop).

The existing NL-75 e2e liveness proof (test_nl75_qa.py) runs
`run_generate(refresh=False)`. The 2026-07-16 16:04Z regenerate went out the
REFRESH path (ingest->rank->narrative) and showed the three symptoms of a dead
rung-a seam (no dated MEMORY block, 0 watch_items, no forward-claim warnings).

DIAGNOSIS (this file's reason to exist): the enrichment and the expiry register
sit on the SHARED input seam — `load_briefing_inputs` (generate.py:2041) is
called AFTER the `if refresh:` block, so refresh and --no-refresh build writer
inputs through the SAME constructor; there is no second path. This guard proves
it on the refresh path directly, and the bite-proof proves the guard bites:
sever the seam and the 16:04 symptom shape reappears.

Offline by construction (conftest sandbox + loopback guard; fake `_chat`,
faked ingest/rank).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from newslens import db, generate, memory_core, paths, ranking
from newslens import ingest as ingest_mod

EDITION = "2026-07-16"
PRIOR = "2026-07-10"
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _thread(con, topic="Strait of Hormuz"):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')", (topic,))
    return con.execute("SELECT id FROM memory WHERE topic=?", (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, signif="", slot=1):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, ?, '[\"S1\"]')",
        (tid, date, slot, what, signif))


def _state(con, tid, as_of, text):
    con.execute("INSERT INTO thread_state (thread_id, as_of_date, state_text)"
                " VALUES (?, ?, ?)", (tid, as_of, text))


def _open_watch(con, tid, raised_on, observable, due):
    con.execute("INSERT INTO watch_items (thread_id, edition_date, kind,"
                " observable, due_date) VALUES (?, ?, 'open', ?, ?)",
                (tid, raised_on, observable, due))
    con.commit()


def _slot(n, mem=()):
    return {
        "slot": n, "story_title": f"Story {n}",
        "summary": "What happened, in one line.", "item_ids": [n],
        "outlets": ["Outlet A", "Outlet B"],
        "matched_tags": [{"name": "AI regulation", "level": "topic"}],
        "matched_memory": list(mem), "matched_dormant": [],
        "followed_analyst": False, "personal_score": 1.0, "world_impact": 6,
        "world_impact_reason": "Sector-wide effects", "combined_score": 0.8,
        "override": False, "override_label": None, "corroboration_count": 2,
        "corroboration_label": "Reported by 2 named outlets",
        "wire_items_excluded": 0, "revived_threads": [],
    }


def _seed_edition(con, date, slots):
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " token_cost, generated_at) VALUES (?, ?, ?, ?, ?)",
        (date, json.dumps(slots),
         json.dumps({"standing_caveat": "", "per_story": []}),
         json.dumps({"steps": [{"step": "rank_select", "usd": 0.001}],
                     "total_usd": 0.001}), iso_now()))
    for s in slots:
        con.execute(
            "INSERT OR IGNORE INTO source_items (id, source_type, outlet, url,"
            " title, fetched_at, raw_excerpt) VALUES (?, 'rss', ?, ?, ?, ?, ?)",
            (s["slot"], s["outlets"][0], f"https://x.example/{date}/{s['slot']}",
             s["story_title"], iso_now(), "An excerpt of the source item."))
    con.commit()


def _tier(i):
    return "full" if i == 1 else ("medium" if i in (2, 3) else "quick")


def _payload(slots, overrides=None):
    overrides = overrides or {}
    stories = []
    for i, s in enumerate(slots, start=1):
        story = {
            "tier": _tier(i),
            "headline": f"Rewritten headline {s['slot']}",
            "lede": ("The opening sentence reports the development. "
                     "A second sentence adds context."),
            "why_it_matters": ("It matters because of concrete effects on the "
                               "reader's interests."),
            "watch_for": "Watch the next scheduled decision.",
            "why_label": generate.WHY_FRAMINGS[(i - 1) % len(generate.WHY_FRAMINGS)],
            "watch_label": generate.WATCH_FRAMINGS[(i - 1) % len(generate.WATCH_FRAMINGS)],
        }
        story.update(overrides.get(i, {}))
        stories.append(story)
    return {"stories": stories}


def _script(slots):
    parts = ["Good morning. Here is your briefing."]
    for s in slots:
        parts.append(f"Story {s['slot']}. The development moved today.")
    parts.append(generate.SPOKEN_CAVEAT)
    parts.append(generate.SIGNOFF)
    slot_budget = (generate.SCRIPT_OPEN_WORDS + generate.SCRIPT_OUTRO_WORDS
                   + sum(generate.script_segment(int(s["slot"])) for s in slots))
    body_words = sum(len(p.split()) for p in parts)
    need = int(slot_budget * 0.85) - body_words
    if need > 0:
        parts.insert(1, " ".join(
            ["The detail continues in measured spoken prose."] * (need // 7 + 1)))
    return "\n\n".join(parts)


@pytest.fixture
def fake_model(monkeypatch):
    state = type("S", (), {})()
    state.calls = []
    state.narrative = None
    state.script = None

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append({"json_mode": json_mode, "prompt": prompt})
        content = json.dumps(state.narrative) if json_mode else state.script
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", fake_chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


def _fake_ingest(con=None, env=None, **kw):
    r = type("R", (), {})()
    r.succeeded, r.attempted, r.items_new = ["A"], 1, 3
    r.discovery_status = "skipped"
    r.degradation_message = None
    return r


def _seed_hormuz_priors(con):
    """The thread-10 shape: genuine dated priors + a standing state as_of a
    date BEFORE the edition, plus the expired Switzerland watch-for the prior
    edition raised (the accountability debt this edition must convert)."""
    tid = _thread(con)
    _delta(con, tid, "2026-07-05",
           "Iran offered special transit terms amid US fee objections",
           "the contest was over the terms of passage")
    _delta(con, tid, PRIOR, "Iran closed the strait after both sides traded strikes",
           "a war over passage itself")
    _state(con, tid, PRIOR,
           "The strait standoff escalated from a fee dispute (Jul 5) to closure (Jul 10).")
    _open_watch(con, tid, PRIOR,
                "The next round of U.S.-Iran talks in Switzerland on July 12",
                "2026-07-12")
    con.commit()
    return tid


def test_refresh_path_carries_rung_a_watch_register_and_forward_claim(
        migrated_con, fake_model, monkeypatch):
    """The refresh chain (ingest->rank->narrative) delivers the FULL NL-75
    contract — identical to the --no-refresh e2e — because both share
    load_briefing_inputs. FAITHFUL to 16:04: a PRIOR briefing row for the
    edition already exists (a pre-plumbing generate had created it) and the
    refresh re-ranks over it."""
    con = migrated_con
    _seed_hormuz_priors(con)
    slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
    # a prior briefing row for the SAME edition (the pre-plumbing 06:29 run)
    _seed_edition(con, EDITION, slots)

    def fake_rank(date=None, con=None, env=None, **kw):
        # the real refresh rank re-persists slots INTO the existing row
        con.execute("UPDATE briefings SET story_slots=? WHERE date=?",
                    (json.dumps(slots), date))
        con.commit()
        r = type("R", (), {})()
        r.warnings = []
        return r

    monkeypatch.setattr(ingest_mod, "run_ingest", _fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)
    # slot 1 ships a STALE forward date (rule i: not future-relative) and no
    # slot's body converts the expired Switzerland debt (rule ii).
    fake_model.narrative = _payload(slots, {
        1: {"watch_for": "Watch the coalition vote on July 8."}})
    fake_model.script = _script(slots)

    rep = generate.run_generate(date=EDITION, con=con, env=ENV, refresh=True)
    assert rep.sample is False

    # (1) rung (a): the dated MEMORY block reached the writer on the REFRESH path
    n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    assert "MEMORY — the record for thread 'Strait of Hormuz'" in n_prompt
    assert "Jul 5" in n_prompt and "Jul 10" in n_prompt
    assert "EXPIRED WATCH-FOR you flagged on 2026-07-10" in n_prompt

    # (2) the expiry register persisted this edition's promises
    opens = con.execute(
        "SELECT slot FROM watch_items WHERE kind='open' AND edition_date=?"
        " ORDER BY slot", (EDITION,)).fetchall()
    assert len(opens) == 3

    # (3) forward-claim wiring ran on the refresh path and surfaced findings
    joined = " | ".join(rep.warnings)
    assert "not future-relative" in joined       # rule i (stale 07-08 date)
    assert "NOT converted" in joined             # rule ii (expired debt dropped)


def test_refresh_path_bite_proof_dead_seam_reproduces_the_1604_symptoms(
        migrated_con, fake_model, monkeypatch):
    """BITE PROOF: sever the shared seam (rung-a silent + watch register a
    no-op) and the exact 16:04 symptom shape reappears on the refresh path —
    no MEMORY block, 0 watch_items. Proves the guard above is load-bearing."""
    con = migrated_con
    _seed_hormuz_priors(con)
    slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
    _seed_edition(con, EDITION, slots)

    def fake_rank(date=None, con=None, env=None, **kw):
        con.execute("UPDATE briefings SET story_slots=? WHERE date=?",
                    (json.dumps(slots), date))
        con.commit()
        r = type("R", (), {})()
        r.warnings = []
        return r

    monkeypatch.setattr(memory_core, "writer_thread_context", lambda *a, **k: "")
    monkeypatch.setattr(memory_core, "persist_watch_items", lambda *a, **k: 0)
    monkeypatch.setattr(ingest_mod, "run_ingest", _fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)
    fake_model.narrative = _payload(slots)
    fake_model.script = _script(slots)

    generate.run_generate(date=EDITION, con=con, env=ENV, refresh=True)
    n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    opens = con.execute("SELECT 1 FROM watch_items WHERE kind='open'"
                        " AND edition_date=?", (EDITION,)).fetchall()
    assert "MEMORY — the record for thread 'Strait of Hormuz'" not in n_prompt
    assert len(opens) == 0


def test_refresh_path_own_connection_cli_shape(fake_model, monkeypatch):
    """The REAL CLI shape: `run_generate(con=None)` opens its OWN connection
    via db.connect() (own_con=True) — the exact path `newslens generate` takes.
    Eliminates the injected-con variable: the register still persists and the
    MEMORY block still reaches the writer when the run owns its connection."""
    db_path = paths.DB_PATH
    db.migrate(db_path=db_path)
    con = db.connect(db_path)
    try:
        _seed_hormuz_priors(con)
        slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
        _seed_edition(con, EDITION, slots)
    finally:
        con.close()

    def fake_rank(date=None, con=None, env=None, **kw):
        con.execute("UPDATE briefings SET story_slots=? WHERE date=?",
                    (json.dumps(slots), date))
        con.commit()
        r = type("R", (), {})()
        r.warnings = []
        return r

    monkeypatch.setattr(ingest_mod, "run_ingest", _fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)
    fake_model.narrative = _payload(slots)
    fake_model.script = _script(slots)

    generate.run_generate(date=EDITION, con=None, env=ENV, refresh=True)

    n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    assert "MEMORY — the record for thread 'Strait of Hormuz'" in n_prompt
    check = db.connect(db_path)
    try:
        opens = check.execute(
            "SELECT slot FROM watch_items WHERE kind='open' AND edition_date=?",
            (EDITION,)).fetchall()
    finally:
        check.close()
    assert len(opens) == 3
