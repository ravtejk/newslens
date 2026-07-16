"""NL-75 rung (a) — the ledger reaches the WRITER, end to end.

THE load-bearing red (HSR-shaped, dispatch): a fixture edition whose matched
thread has ledger history must produce a writer PROMPT carrying dated deltas.
RED on 9c3078b — build_narrative_prompt built the threads block from topic+note
only (generate.py:578); thread_state / thread_deltas never reached the writer.
Offline: no model call — the assertion is on the assembled prompt string.
"""

from __future__ import annotations

import json

from newslens import db, generate

DATE = "2026-07-14"


def _seed_hormuz_edition(con):
    con.execute("INSERT INTO memory (topic, status) VALUES ('Strait of Hormuz', 'active')")
    tid = con.execute(
        "SELECT id FROM memory WHERE topic = 'Strait of Hormuz'").fetchone()["id"]
    for date, what, sig in [
        ("2026-07-05", "Iran offered special transit terms amid US fee objections",
         "the contest was over the terms of passage"),
        ("2026-07-10", "Iran closed the strait after both sides traded strikes",
         "a war over passage itself"),
    ]:
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
            " what_happened, significance, cites_json) VALUES (?,?,1,'advances',?,?,'[\"S1\"]')",
            (tid, date, what, sig))
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text) VALUES (?, '2026-07-10',"
        " 'The strait standoff escalated from a fee dispute (Jul 5) to closure (Jul 10).')", (tid,))
    slots = [
        {"slot": "1", "story_title": "US blockades the strait", "summary": "blockade",
         "item_ids": [], "outlets": ["Reuters"], "matched_tags": [],
         "matched_memory": ["Strait of Hormuz"], "override": False,
         "corroboration_label": "Reported by 9 named outlets"},
        {"slot": "2", "story_title": "S2", "summary": "s2", "item_ids": [],
         "outlets": ["The Hill"], "matched_tags": [], "matched_memory": [],
         "override": False, "corroboration_label": "x"},
        {"slot": "3", "story_title": "S3", "summary": "s3", "item_ids": [],
         "outlets": ["The Hill"], "matched_tags": [], "matched_memory": [],
         "override": False, "corroboration_label": "x"},
    ]
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                (DATE, json.dumps(slots)))
    con.commit()
    return tid


def _prompt(con):
    inputs = generate.load_briefing_inputs(con, DATE)
    inputs["briefs_by_slot"] = {}
    inputs["analyst_slot3_tier"] = None
    return generate.build_narrative_prompt(DATE, "A", inputs)


def test_rung_a_writer_prompt_carries_dated_deltas(tmp_paths):
    db.migrate()
    con = db.connect()
    try:
        _seed_hormuz_edition(con)
        prompt = _prompt(con)
        assert "MEMORY — the record for thread 'Strait of Hormuz'" in prompt
        assert "Jul 5" in prompt and "Jul 10" in prompt          # dates load-bearing
        assert "closed the strait" in prompt.lower()             # a dated delta's text
        assert "escalated from a fee dispute" in prompt.lower()  # the standing state
    finally:
        con.close()


def test_rung_a_variant_b_also_carries_the_ledger(tmp_paths):
    """The story block is shared by both variants — B inherits rung (a)."""
    db.migrate()
    con = db.connect()
    try:
        _seed_hormuz_edition(con)
        inputs = generate.load_briefing_inputs(con, DATE)
        inputs["briefs_by_slot"] = {}
        inputs["analyst_slot3_tier"] = None
        prompt_b = generate.build_narrative_prompt(DATE, "B", inputs)
        assert "Jul 10" in prompt_b and "closed the strait" in prompt_b.lower()
    finally:
        con.close()


def test_rung_a_two_clocks_no_reader_history_leaks(tmp_paths):
    """The two-clocks law: EDITION history in prose, the reader's history never.
    The memory block must steer against 'you read/opened this on ...'."""
    db.migrate()
    con = db.connect()
    try:
        _seed_hormuz_edition(con)
        prompt = _prompt(con)
        # the injected MEMORY block steers to edition history, never the reader's
        assert "edition history only; never the reader's history" in prompt.lower()
        # and the block itself carries no reader-timestamp phrasing (the prompt
        # TEMPLATE legitimately bans "you read/skipped" — that ban isn't a leak)
        mem = prompt[prompt.index("MEMORY — the record for thread"):]
        mem = mem[:mem.index("corroboration:")].lower()
        for leak in ("you read", "you opened", "you last", "you viewed", "you saw"):
            assert leak not in mem
    finally:
        con.close()


def test_rung_a_day_one_thread_adds_no_memory_furniture(tmp_paths):
    db.migrate()
    con = db.connect()
    try:
        con.execute("INSERT INTO memory (topic, status) VALUES ('Fresh Topic', 'active')")
        slots = [{"slot": "1", "story_title": "T", "summary": "s", "item_ids": [],
                  "outlets": ["The Hill"], "matched_tags": [],
                  "matched_memory": ["Fresh Topic"], "override": False,
                  "corroboration_label": "x"},
                 {"slot": "2", "story_title": "S2", "summary": "s2", "item_ids": [],
                  "outlets": ["The Hill"], "matched_tags": [], "matched_memory": [],
                  "override": False, "corroboration_label": "x"},
                 {"slot": "3", "story_title": "S3", "summary": "s3", "item_ids": [],
                  "outlets": ["The Hill"], "matched_tags": [], "matched_memory": [],
                  "override": False, "corroboration_label": "x"}]
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (DATE, json.dumps(slots)))
        con.commit()
        prompt = _prompt(con)
        assert "MEMORY — the record for thread" not in prompt   # nothing to show
    finally:
        con.close()
