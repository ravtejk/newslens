"""NL-75 THE FORWARD-CLAIM RULES — generation-side validation (Content council
2026-07-16). Rule iii (repetition antecedent, poisoned-antecedent hardened),
rule i (future-relative watch-for), rule ii (expiry conversion). Born-red:
these functions do not exist on 9c3078b.
"""

from __future__ import annotations

from newslens import generate


def _thread(con, topic="Strait of Hormuz"):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')", (topic,))
    return con.execute("SELECT id FROM memory WHERE topic = ?", (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, signif=""):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, 1, 'advances', ?, ?, '[\"S1\"]')", (tid, date, what, signif))


# --- rule iii: repetition antecedent, poisoned-antecedent hardened ----------

def test_poisoned_antecedent_reinstated_is_flagged(migrated_con):
    """The exact HSR failure: the ONLY ledger row is the 07-14 same-day backfill
    ('reinstated a naval blockade' — source-echo). It must NOT license the word
    in the 07-14 edition; the check flags an unattributed 'reinstated'."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-14", "U.S. reinstated a naval blockade of the strait")
    stories = [{"headline": "US blockades the strait",
                "lede": "The United States reinstated a naval blockade of the strait Tuesday.",
                "why_it_matters": "Oil prices surged."}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    findings = generate.repetition_antecedent_findings(con, stories, slots, "2026-07-14")
    assert any("reinstated" in f.lower() for f in findings)


def test_predating_antecedent_licenses_the_word(migrated_con):
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-05", "U.S. imposed a naval blockade of the strait")
    stories = [{"headline": "Blockade back",
                "lede": "The United States reinstated a naval blockade of the strait Tuesday.",
                "why_it_matters": "x"}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    assert generate.repetition_antecedent_findings(con, stories, slots, "2026-07-14") == []


def test_source_attributed_repetition_is_legal(migrated_con):
    """Content rule iii middle state: no ledger antecedent, but the word ships
    ATTRIBUTED ('a step today's reports call "reinstated"') — legal."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-14", "U.S. reinstated a naval blockade")  # poisoned, same-day
    stories = [{"headline": "Blockade",
                "lede": 'The U.S. blockaded the strait — a step today\'s reports call "reinstated," '
                        "though no earlier blockade appears in this record.",
                "why_it_matters": "x"}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    assert generate.repetition_antecedent_findings(con, stories, slots, "2026-07-14") == []


# --- rule i: future-relative watch-for --------------------------------------

def test_stale_watch_for_date_is_flagged():
    stories = [{"watch_for": "The Switzerland talks on July 12 will indicate whether channels hold"}]
    slots = [{"slot": "1"}]
    findings = generate.future_relative_watch_findings(stories, slots, "2026-07-14")
    assert any("2026-07-12" in f for f in findings)


def test_future_watch_for_date_is_clean():
    stories = [{"watch_for": "The coalition vote on July 20 will settle the question"}]
    slots = [{"slot": "1"}]
    assert generate.future_relative_watch_findings(stories, slots, "2026-07-14") == []


# --- rule ii: expiry conversion (the three outcomes) ------------------------

_EXPIRED = {"observable": "The Switzerland talks on July 12 will indicate whether channels hold",
            "due_date": "2026-07-12", "edition_date": "2026-07-10"}


def test_unconverted_expired_watch_is_flagged():
    stories = [{"lede": "Oil prices climbed.", "why_it_matters": "x",
                "watch_for": "Watch the coalition vote."}]
    slots = [{"slot": "1", "expired_watch": [_EXPIRED]}]
    findings = generate.expiry_conversion_findings(stories, slots)
    assert any("NOT converted" in f for f in findings)


def test_resolved_conversion_is_clean():
    stories = [{"lede": "The Switzerland talks collapsed on the 12th after Iran walked out.",
                "why_it_matters": "x", "watch_for": "Watch the next round."}]
    slots = [{"slot": "1", "expired_watch": [_EXPIRED]}]
    assert generate.expiry_conversion_findings(stories, slots) == []


def test_unanswered_conversion_is_clean():
    stories = [{"lede": "The Switzerland talks this briefing flagged have come and gone "
                        "without a mention in today's reporting.",
                "why_it_matters": "x", "watch_for": "x"}]
    slots = [{"slot": "1", "expired_watch": [_EXPIRED]}]
    assert generate.expiry_conversion_findings(stories, slots) == []


def test_superseded_conversion_is_clean():
    stories = [{"lede": "The Switzerland talks were overtaken by Tuesday's blockade before "
                        "they could convene.",
                "why_it_matters": "x", "watch_for": "x"}]
    slots = [{"slot": "1", "expired_watch": [_EXPIRED]}]
    assert generate.expiry_conversion_findings(stories, slots) == []


def test_forward_claim_findings_orchestrator_runs_all_three(migrated_con):
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-14", "U.S. reinstated a naval blockade")   # poisoned
    # rule iii: unattributed 'reinstated'; rule i: a stale July 8 date;
    # rule ii: the expired Switzerland item is never mentioned (silent drop).
    stories = [{"headline": "Blockade", "lede": "The U.S. reinstated a naval blockade.",
                "why_it_matters": "Oil surged.",
                "watch_for": "The coalition vote on July 8 will settle the question."}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"],
              "expired_watch": [_EXPIRED]}]
    findings = generate.forward_claim_findings(con, stories, slots, "2026-07-14")
    joined = " | ".join(findings)
    assert "reinstated" in joined.lower()      # rule iii
    assert "2026-07-08" in joined              # rule i
    assert "NOT converted" in joined           # rule ii


# --- Gate FIX-1 pins (milestone review): the fallback's self-licensing leak ----

def _gate_fix1_setup(con, prior_what):
    from test_nl75_qa import _thread, _delta
    tid = _thread(con, topic="Suez Canal")
    _delta(con, tid, "2026-07-05", prior_what)
    con.commit()
    return tid


def test_sentence_final_again_never_licenses_off_against(migrated_con):
    """Gate FIX-1: 'again' ending a sentence must not become its own subject —
    substring matching would license it off any prior row carrying 'against'
    (ubiquitous in conflict coverage)."""
    from newslens import generate
    _gate_fix1_setup(migrated_con, "Protests against the toll regime")
    stories = [{"headline": "Canal", "lede": "The canal is open again.",
                "why_it_matters": ""}]
    slots = [{"slot": "1", "matched_memory": ["Suez Canal"]}]
    findings = generate.repetition_antecedent_findings(
        migrated_con, stories, slots, "2026-07-14")
    assert any("again" in f for f in findings)


def test_sentence_final_again_never_licenses_off_verbatim_unrelated_prior(
        migrated_con):
    """Gate FIX-1: a prior row containing the word 'again' about a DIFFERENT
    object must not license a sentence-final 'again'."""
    from newslens import generate
    _gate_fix1_setup(migrated_con, "Inspections were delayed again at the port")
    stories = [{"headline": "Canal", "lede": "The canal is open again.",
                "why_it_matters": ""}]
    slots = [{"slot": "1", "matched_memory": ["Suez Canal"]}]
    findings = generate.repetition_antecedent_findings(
        migrated_con, stories, slots, "2026-07-14")
    assert any("again" in f for f in findings)


def test_sentence_final_resumed_never_licenses_off_different_resumption(
        migrated_con):
    """Gate FIX-1: 'Grain shipments resumed.' — the trailing match word's own
    units are excluded from the fallback subject, so a prior 'Inspections
    resumed' row cannot license it (different object entirely)."""
    from newslens import generate
    _gate_fix1_setup(migrated_con, "Inspections resumed at the northern port")
    stories = [{"headline": "Grain", "lede": "Grain shipments resumed.",
                "why_it_matters": ""}]
    slots = [{"slot": "1", "matched_memory": ["Suez Canal"]}]
    findings = generate.repetition_antecedent_findings(
        migrated_con, stories, slots, "2026-07-14")
    assert any("resumed" in f for f in findings)
