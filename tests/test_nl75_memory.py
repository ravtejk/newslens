"""NL-75 memory_core: rung (a) writer context, the poisoned-antecedent rule,
the supersession read side, and the expiry register.

These are the load-bearing reds. On 9c3078b: writer_thread_context /
has_predating_antecedent / the watch_items helpers do not exist; the
supersession read side does not exist. Each is the red the wiring only it flips.
"""

from __future__ import annotations

from newslens import memory_core


def _thread(con, topic):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')", (topic,))
    return con.execute("SELECT id FROM memory WHERE topic = ?", (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, slot=1, signif="", verdict="advances"):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json) VALUES (?, ?, ?, ?, ?, ?, '[\"S1\"]')",
        (tid, date, slot, verdict, what, signif))
    return con.execute("SELECT id FROM thread_deltas ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _state(con, tid, as_of, text):
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text) VALUES (?, ?, ?)",
        (tid, as_of, text))


# --- rung (a): the ledger reaches the writer -------------------------------

def test_writer_thread_context_carries_dated_deltas_and_state(migrated_con):
    con = migrated_con
    tid = _thread(con, "Strait of Hormuz")
    _delta(con, tid, "2026-07-05", "Iran offered special transit terms amid US fee objections",
           signif="the contest was over the terms of passage")
    _delta(con, tid, "2026-07-10", "Iran closed the strait after both sides traded strikes",
           signif="a war over passage itself")
    _state(con, tid, "2026-07-10",
           "The strait standoff escalated from a fee dispute (Jul 5) to closure (Jul 10).")
    block = memory_core.writer_thread_context(con, "Strait of Hormuz", before_date="2026-07-14")
    assert "Jul 5" in block and "Jul 10" in block          # dates load-bearing
    assert "closed the strait" in block.lower()
    assert "standing state" in block.lower()
    assert "escalated from a fee dispute" in block.lower()  # the state text


def test_writer_thread_context_is_empty_for_a_day_one_thread(migrated_con):
    _thread(migrated_con, "Fresh Thread")
    assert memory_core.writer_thread_context(
        migrated_con, "Fresh Thread", before_date="2026-07-14") == ""


def test_writer_thread_context_excludes_todays_own_delta(migrated_con):
    con = migrated_con
    tid = _thread(con, "T")
    _delta(con, tid, "2026-07-14", "today's own turn — written after generation")
    # strict before_date: the writer never sees the edition's own delta fed back
    assert memory_core.writer_thread_context(con, "T", before_date="2026-07-14") == ""


def test_writer_thread_context_notes_absent_state_without_implying_one(migrated_con):
    con = migrated_con
    tid = _thread(con, "T")
    _delta(con, tid, "2026-07-05", "the first turn")   # ledger but no state row
    block = memory_core.writer_thread_context(con, "T", before_date="2026-07-14")
    assert "none on record yet" in block.lower()


# --- the poisoned-antecedent rule (HSR finding 1, BINDING) -----------------

def test_poisoned_antecedent_todays_backfill_does_not_license_repetition(migrated_con):
    con = migrated_con
    tid = _thread(con, "Strait of Hormuz")
    # The ONLY ledger row is dated == the edition (the 07-14 same-day backfill
    # echoing edition-day source diction). It must NOT establish the antecedent.
    _delta(con, tid, "2026-07-14", "U.S. reinstated a naval blockade of the strait")
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, "2026-07-14") is False


def test_a_predating_ledger_row_licenses_the_repetition(migrated_con):
    con = migrated_con
    tid = _thread(con, "Strait of Hormuz")
    _delta(con, tid, "2026-07-05", "U.S. imposed a naval blockade of the strait")
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, "2026-07-14") is True


def test_antecedent_requires_the_subject_not_just_any_history(migrated_con):
    con = migrated_con
    tid = _thread(con, "Strait of Hormuz")
    _delta(con, tid, "2026-07-05", "Iran offered special transit terms")   # no blockade
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, "2026-07-14") is False


def test_superseded_predating_row_does_not_license_the_repetition(migrated_con):
    con = migrated_con
    tid = _thread(con, "Strait of Hormuz")
    a = _delta(con, tid, "2026-07-05", "U.S. imposed a naval blockade")
    # the correcting row does NOT mention the subject — so once A is superseded,
    # the record holds no live antecedent for "blockade"
    b = _delta(con, tid, "2026-07-10", "the dispute stayed a fee negotiation")
    con.execute("INSERT INTO thread_delta_supersessions (delta_id, superseded_by)"
                " VALUES (?, ?)", (a, b))
    con.commit()
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, "2026-07-14") is False


# --- supersession read side (Rook's gate) ----------------------------------

def test_superseded_delta_is_excluded_from_state_regeneration(migrated_con):
    con = migrated_con
    tid = _thread(con, "T")
    a = _delta(con, tid, "2026-07-05", "WRONG fact about a blockade")
    b = _delta(con, tid, "2026-07-14", "the corrected fact")
    con.execute("INSERT INTO thread_delta_supersessions (delta_id, superseded_by)"
                " VALUES (?, ?)", (a, b))
    con.commit()
    captured = {}

    def fake_chat(key, prompt):
        captured["prompt"] = prompt
        return {"state": "The corrected fact holds (Jul 14)."}, 0.001

    res = memory_core.rewrite_state(
        con, tid, "T", "2026-07-14", None, "k",
        "topic={topic} date={date}\nledger={ledger}", remaining_usd=1.0, chat=fake_chat)
    assert res.outcome == "written"
    assert "WRONG fact" not in captured["prompt"]   # superseded row never reached the prompt
    assert "corrected fact" in captured["prompt"]


def test_timeline_keeps_superseded_rows_but_marks_them(migrated_con):
    con = migrated_con
    tid = _thread(con, "T")
    a = _delta(con, tid, "2026-07-05", "wrong")
    b = _delta(con, tid, "2026-07-14", "right")
    con.execute("INSERT INTO thread_delta_supersessions (delta_id, superseded_by)"
                " VALUES (?, ?)", (a, b))
    con.commit()
    by_date = {r["date"]: r for r in memory_core.timeline_rows(con, tid)}
    assert by_date["2026-07-05"]["superseded_by"] == b   # struck, not dropped
    assert by_date["2026-07-14"]["superseded_by"] is None


# --- the expiry register ----------------------------------------------------

def test_parse_due_date_resolves_human_and_iso_forms():
    assert memory_core.parse_due_date(
        "Switzerland talks on July 12 will indicate whether diplomacy holds",
        "2026-07-14") == "2026-07-12"
    assert memory_core.parse_due_date("the vote lands 2026-07-20", "2026-07-14") == "2026-07-20"
    assert memory_core.parse_due_date("whether the blockade holds", "2026-07-14") is None


def test_persist_watch_items_writes_open_promises_scoped_to_threads(migrated_con):
    con = migrated_con
    tid = _thread(con, "Strait of Hormuz")
    con.execute("INSERT INTO briefings (date) VALUES ('2026-07-10')")
    bid = con.execute("SELECT id FROM briefings WHERE date = '2026-07-10'").fetchone()["id"]
    slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz"]},
             {"slot": 2, "matched_memory": []}]
    stories = [{"watch_for": "The Switzerland talks on July 12 will indicate whether channels hold"},
               {"watch_for": "Watch whether the coalition vote clears"}]
    n = memory_core.persist_watch_items(con, "2026-07-10", bid, stories, slots)
    assert n == 2
    row = con.execute("SELECT thread_id, due_date, observable FROM watch_items"
                      " WHERE slot = 1").fetchone()
    assert row["thread_id"] == tid
    assert row["due_date"] == "2026-07-12"
    # idempotent: re-persisting the same edition writes nothing more
    assert memory_core.persist_watch_items(con, "2026-07-10", bid, stories, slots) == 0


def test_expired_unconverted_finds_past_due_open_items(migrated_con):
    con = migrated_con
    tid = _thread(con, "Strait of Hormuz")
    con.execute("INSERT INTO watch_items (thread_id, edition_date, kind, observable, due_date)"
                " VALUES (?, '2026-07-10', 'open', 'Switzerland talks July 12', '2026-07-12')", (tid,))
    con.execute("INSERT INTO watch_items (thread_id, edition_date, kind, observable, due_date)"
                " VALUES (?, '2026-07-10', 'open', 'a still-future check', '2026-07-20')", (tid,))
    con.commit()
    exp = memory_core.expired_unconverted_watch_items(con, "Strait of Hormuz", "2026-07-14")
    assert [e["due_date"] for e in exp] == ["2026-07-12"]   # only the past-due one


def test_conversion_row_closes_the_open_item(migrated_con):
    con = migrated_con
    tid = _thread(con, "Strait of Hormuz")
    con.execute("INSERT INTO watch_items (thread_id, edition_date, kind, observable, due_date)"
                " VALUES (?, '2026-07-10', 'open', 'Switzerland talks July 12', '2026-07-12')", (tid,))
    con.commit()
    item = memory_core.expired_unconverted_watch_items(con, "Strait of Hormuz", "2026-07-14")[0]
    memory_core.record_watch_conversion(con, item, "2026-07-14", None, "unanswered",
                                        "none of today's outlets mention the talks")
    # once converted, it is no longer an outstanding debt
    assert memory_core.expired_unconverted_watch_items(con, "Strait of Hormuz", "2026-07-14") == []


def test_classify_conversion_three_outcomes_and_the_silent_drop():
    obs = "The Switzerland talks on July 12 will indicate whether diplomacy holds"
    resolved = "The Switzerland talks collapsed on the 12th after Iran walked out."
    unanswered = "The Switzerland talks this briefing flagged have come and gone without a mention."
    superseded = "The Switzerland talks were overtaken by Tuesday's blockade before they could convene."
    dropped = "Oil prices climbed and the coalition vote slipped."   # never references the talks
    assert memory_core.classify_conversion(obs, resolved) == "resolved"
    assert memory_core.classify_conversion(obs, unanswered) == "unanswered"
    assert memory_core.classify_conversion(obs, superseded) == "superseded"
    assert memory_core.classify_conversion(obs, dropped) is None


def test_prior_for_slot_date_label_reads_the_live_ledger(migrated_con):
    """Gate FIX-2: the P-material's date label must come from the newest LIVE
    delta — a superseded newest row date-labels a block that omits its text."""
    from test_nl75_qa import _thread, _delta
    con = migrated_con
    tid = _thread(con, topic="Fix2 Thread")
    _delta(con, tid, "2026-07-05", "Old live fact about the corridor")
    _delta(con, tid, "2026-07-10", "WRONG-FACT later corrected away")
    con.commit()
    rows = con.execute(
        "SELECT id FROM thread_deltas WHERE thread_id=? ORDER BY id", (tid,)
    ).fetchall()
    con.execute(
        "INSERT INTO thread_delta_supersessions (delta_id, superseded_by)"
        " VALUES (?, ?)", (rows[1]["id"], rows[0]["id"]))
    con.commit()
    from newslens import memory_core
    scoped = memory_core.prior_for_slot(
        con, "2026-07-14", {"matched_memory": ["Fix2 Thread"]},
        generic_prior=[])
    assert scoped and scoped[0]["date"] == "2026-07-05"
