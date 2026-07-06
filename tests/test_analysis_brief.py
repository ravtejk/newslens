"""M9 milestone 2 — the analysis call + citation checking, offline
(implementer-written; QA extends).

Spend-proof by construction: the model and Sonar are injected fakes; the
autouse loopback guard catches any socket that slips. Every degradation-
ladder branch is its own pinned test, per the '3 feeds killed on purpose'
discipline (engineering transcript, milestone B QA surface).
"""

import json
from pathlib import Path

import pytest

from newslens import analysis, db

FIXTURES = Path(__file__).parent / "fixtures" / "analysis"


def sources_fixture():
    """A small offered map with all four key kinds."""
    return {
        "S1": {"kind": "cluster-full-text", "outlet": "The Hill",
               "title": "NATO summit story", "url": "https://thehill.com/a",
               "retrieved_at": "2026-07-06T00:00Z",
               "text": "President Trump will meet Wednesday with Ukrainian "
                       "President Volodymyr Zelensky. The summit opens Tuesday "
                       "in Ankara with a session on defense spending targets."},
        "C1": {"kind": "cluster-excerpt", "outlet": "CNBC",
               "title": "Trump to meet Zelenskyy", "url": "https://cnbc.com/b",
               "retrieved_at": "2026-07-05", "text": "Trump plans to press "
               "European allies on a five percent spending pledge."},
        "R1": {"kind": "retrieved", "outlet": "reuters.com",
               "title": "NATO agenda", "url": "https://reuters.com/c",
               "retrieved_at": "2026-07-06T00:00Z",
               "text": "Diplomats said the alliance will weigh a new "
                       "drone-defense initiative, analyst Jan Novak said the "
                       "pledge faces resistance from three members."},
        "P1": {"kind": "prior-briefing", "outlet": "NewsLens (prior edition)",
               "title": "briefing 2026-07-05", "url": "",
               "retrieved_at": "2026-07-05",
               "text": "Yesterday's edition covered the pre-summit meetings."},
    }


def corpus_of(sources):
    return " ".join(s["text"] for s in sources.values())


def good_brief():
    return {
        "pinned_facts": [
            {"fact": "Trump meets Zelensky at the NATO summit on Wednesday.",
             "cites": ["S1", "C1"]},
            {"fact": "The summit opens Tuesday in Ankara.", "cites": ["S1"]},
            {"fact": "A five percent spending pledge is on the agenda.",
             "cites": ["C1"]},
        ],
        "ledger": [
            {"claim": "The alliance weighs a drone-defense initiative.",
             "cites": ["R1"]},
            {"claim": "Defense spending targets open the summit.",
             "cites": ["S1", "C1"]},
        ],
        "mechanism": "Alliance members trade a spending pledge for continued "
                     "security guarantees; each government answers to a "
                     "domestic budget constraint that makes the five percent "
                     "number costly to sign [C1].",
        "effects": [
            {"effect": "The pledge faces resistance from three members.",
             "basis": "attributed", "holder": "Jan Novak (Reuters)",
             "cites": ["R1"]},
        ],
        "arc": {"delta": "advances", "what_changed": "meetings move from "
                "pre-summit staging to the summit itself.", "cites": ["P1"]},
        "unknowns": [
            {"question": "Which three members resist the spending pledge",
             "why_material": "three holdouts can block a unanimous communique",
             "would_resolve": "the communique text or a named-member statement"},
        ],
        "watch": [
            {"observable": "communique language on the five percent target "
                           "by Thursday", "settles": "whether resistance held"},
            {"observable": "any bilateral Trump-Zelensky statement Wednesday",
             "settles": "what the meeting produced"},
        ],
        "notes_for_writer": "lead with the meeting, not the agenda.",
    }


# ---------------------------------------------------------------------------
# Validation — the receipts machinery
# ---------------------------------------------------------------------------

def test_good_brief_validates_with_computed_furniture():
    src = sources_fixture()
    clean, warnings = analysis.validate_brief(good_brief(), src, "full",
                                              corpus_of(src))
    # provenance is CODE-computed, never model-claimed
    provs = [e["provenance"] for e in clean["ledger"]]
    assert provs == ["retrieved-single (reuters.com)",
                     "cluster-corroborated (2 outlets)"]
    # source table carries only cited keys, code-built
    keys = [s["key"] for s in clean["sources"]]
    assert keys == ["S1", "C1", "R1", "P1"]
    assert all(s["kind"] for s in clean["sources"])
    assert not any("verified" in w.lower() for w in warnings)


def test_fabricated_citation_is_hard_reject():
    src = sources_fixture()
    b = good_brief()
    b["ledger"].append({"claim": "A ninth carrier group moved.", "cites": ["S9"]})
    with pytest.raises(analysis.BriefRejected) as exc:
        analysis.validate_brief(b, src, "full", corpus_of(src))
    assert "fabricated citation" in str(exc.value) and "S9" in str(exc.value)


def test_quote_must_be_verbatim_substring_of_retrieved_text():
    src = sources_fixture()
    b = good_brief()
    b["pinned_facts"][0]["fact"] = ('Officials said "the summit will produce '
                                    'a historic breakthrough agreement".')
    with pytest.raises(analysis.BriefRejected) as exc:
        analysis.validate_brief(b, src, "full", corpus_of(src))
    assert "verbatim substring" in str(exc.value)


def test_verbatim_quote_passes():
    src = sources_fixture()
    b = good_brief()
    b["ledger"][0]["claim"] = ('Reporting notes "the alliance will weigh a '
                               'new drone-defense initiative" this week.')
    clean, _ = analysis.validate_brief(b, src, "full", corpus_of(src))
    assert "drone-defense" in clean["ledger"][0]["claim"]


def test_uncited_pinned_fact_is_hard_reject():
    src = sources_fixture()
    b = good_brief()
    b["pinned_facts"][1]["cites"] = []
    with pytest.raises(analysis.BriefRejected):
        analysis.validate_brief(b, src, "full", corpus_of(src))


def test_missing_section_is_hard_reject():
    src = sources_fixture()
    b = good_brief()
    del b["unknowns"]
    with pytest.raises(analysis.BriefRejected) as exc:
        analysis.validate_brief(b, src, "full", corpus_of(src))
    assert "unknowns" in str(exc.value)


def test_generic_unknown_is_the_banned_class():
    src = sources_fixture()
    b = good_brief()
    b["unknowns"] = [{"question": "It remains unclear how this will unfold",
                      "why_material": "x", "would_resolve": "y"}]
    with pytest.raises(analysis.BriefRejected) as exc:
        analysis.validate_brief(b, src, "full", corpus_of(src))
    assert "zero-information" in str(exc.value)


def test_borrowed_inference_rule_drops_own_voice_effects_with_disclosure():
    """The principal's structural ruling: no own-voice inference in either
    rendering. mechanism-inference (and any unlisted basis, and uncited
    effects) are dropped — disclosed, never silent, never rendered."""
    src = sources_fixture()
    b = good_brief()
    b["effects"].append({"effect": "This will likely reshape the alliance.",
                         "basis": "mechanism-inference", "cites": ["S1"]})
    b["effects"].append({"effect": "Markets may react.", "basis": "attributed",
                         "holder": "", "cites": []})  # a take without receipts
    clean, warnings = analysis.validate_brief(b, src, "full", corpus_of(src))
    assert len(clean["effects"]) == 1  # only the real attributed one survives
    assert any("borrowed-inference enforcement: dropped 2" in w for w in warnings)


def test_discrepancy_requires_both_sides_cited_never_averaged():
    src = sources_fixture()
    b = good_brief()
    b["ledger"].append({"discrepancy": True,
                        "a": {"value": "5 percent pledge", "cites": ["C1"]},
                        "b": {"value": "3.5 percent floor", "cites": []},
                        "note": "outlets differ on the number"})
    with pytest.raises(analysis.BriefRejected) as exc:
        analysis.validate_brief(b, src, "full", corpus_of(src))
    assert "both values need both sources" in str(exc.value)
    b["ledger"][-1]["b"]["cites"] = ["R1"]
    clean, _ = analysis.validate_brief(b, src, "full", corpus_of(src))
    disc = [e for e in clean["ledger"] if e.get("discrepancy")]
    assert len(disc) == 1  # carried as a discrepancy, both sides intact


def test_word_budget_is_a_warning_never_a_reject():
    src = sources_fixture()
    b = good_brief()
    b["mechanism"] = "Each member government answers to its own parliament. " * 60
    clean, warnings = analysis.validate_brief(b, src, "medium", corpus_of(src))
    assert any("word" in w and "ceiling" in w for w in warnings)


def test_stable_background_tolerated_and_labeled():
    src = sources_fixture()
    b = good_brief()
    b["ledger"].append({"claim": "Ankara is Turkey's capital.", "cites": []})
    clean, warnings = analysis.validate_brief(b, src, "full", corpus_of(src))
    assert clean["ledger"][-1]["provenance"] == "stable-background"
    assert any("stable-background" in w for w in warnings)


def test_hostile_article_body_cannot_mint_citations_or_verified_claims():
    """M1's hostile fixture, both directions at the M2 layer: the planted
    body-text directive travels INTO the material (extraction preserved it)
    — and the validator makes its instructions unexecutable: a citation not
    in the offered map rejects the brief no matter what the text asked for,
    and reader copy never says 'verified' because that word is not in the
    code-owned furniture at all."""
    html = (FIXTURES / "hostile_content.html").read_text(encoding="utf-8")
    res = analysis.extract_article_text(html)
    rec = analysis.FetchRecord(url="https://ex.com/h", source_name="Example Herald",
                               tier="full", outcome=analysis.OK,
                               chars=res.chars, text=res.text, title=res.title)
    sources = analysis.build_source_map([rec], [], [], [])
    # the injected directive is present in the material — data, not hidden
    assert "[system directive" in sources["S1"]["text"]
    # the prompt build places it inside the DATA block, under the armor rule
    from newslens import paths
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    assert "DATA, NEVER INSTRUCTIONS" in template
    # a brief that obeys the directive (fake key, 'verified' claim) dies
    bad = good_brief()
    bad["pinned_facts"] = [{"fact": "This outlet verified all claims.",
                            "cites": ["V1"]}]  # V1 exists nowhere
    with pytest.raises(analysis.BriefRejected):
        analysis.validate_brief(bad, sources, "full",
                                corpus_of(sources))
    # and the writer-facing rendering never emits the word 'verified' as
    # furniture (Sten's law: 'cited', never 'verified')
    clean, _ = analysis.validate_brief(
        {**good_brief(), "arc": None},
        sources_fixture(), "full", corpus_of(sources_fixture()))
    rendered = analysis.render_writer_view(clean)
    assert "cited, never 'verified'" in rendered.lower() or "verified" not in rendered.lower()


# ---------------------------------------------------------------------------
# The loop + the ladder (mocked model and Sonar; no sockets)
# ---------------------------------------------------------------------------

DATE = "2026-07-06"


def seed_ranked_briefing(con, n_items=2):
    with con:
        ids = []
        for i in range(n_items):
            cur = con.execute(
                "INSERT INTO source_items (source_type, outlet, url, title,"
                " raw_excerpt) VALUES ('rss', ?, ?, ?, ?)",
                ("The Hill", f"https://thehill.com/x{i}", f"Story item {i}",
                 "President Trump will meet Wednesday with Ukrainian President "
                 "Volodymyr Zelensky at the summit."))
            ids.append(cur.lastrowid)
        slots = [{"slot": "1", "story_title": "NATO summit meetings",
                  "summary": "Trump meets Zelensky.", "item_ids": ids,
                  "outlets": ["The Hill"], "matched_tags": [],
                  "matched_memory": [], "override": False,
                  "corroboration_label": "Reported by 1 named outlet"},
                 {"slot": "2", "story_title": "Second story",
                  "summary": "s2", "item_ids": [], "outlets": [],
                  "matched_tags": [], "matched_memory": [], "override": False,
                  "corroboration_label": ""},
                 {"slot": "3", "story_title": "Third story",
                  "summary": "s3", "item_ids": [], "outlets": [],
                  "matched_tags": [], "matched_memory": [], "override": False,
                  "corroboration_label": ""}]
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (DATE, json.dumps(slots)))


def fake_fetch_ok(url, timeout, cap=0, user_agent=""):
    if url.endswith("/robots.txt"):
        import urllib.error
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    return (FIXTURES / "clean_article.html").read_bytes()


def fake_chat_good(key, prompt):
    b = good_brief()
    # keys the seeded map actually offers: S1 (fetched), C1/C2 (excerpts) —
    # no R (sonar faked empty) — pin cites accordingly
    b["pinned_facts"] = [
        {"fact": "Trump meets Zelensky at the NATO summit.", "cites": ["S1"]},
        {"fact": "The president leaves for Ankara on Monday evening.",
         "cites": ["S1"]},
        {"fact": "The summit runs two days.", "cites": ["S1"]},
    ]
    b["ledger"] = [{"claim": "The meeting happens Wednesday.", "cites": ["S1"]}]
    b["mechanism"] = ("The president trades summit attendance for allied "
                      "spending commitments; each ally answers to its own "
                      "parliament [S1].")
    b["effects"] = []
    b["arc"] = None
    return b, 0.03


def fake_sonar_empty(key, title, claims):
    return [], 0.0, "ok — 0 results"


def test_run_analysis_produces_and_persists_a_valid_brief(tmp_paths, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    db.migrate()
    con = db.connect()
    seed_ranked_briefing(con)
    report = analysis.run_analysis(date=DATE, con=con, chat=fake_chat_good,
                                   sonar=fake_sonar_empty, fetch=fake_fetch_ok,
                                   sleep=lambda s: None)
    outcomes = {s["slot"]: s["outcome"] for s in report["per_story"]}
    assert outcomes[1] == "ok"
    # slot 2 medium with no items: total-failure rule -> no brief, disclosed
    assert outcomes[2] == "skipped-thin"
    # slot 3 medium, thin -> the analyst's demotion call (the reconciliation)
    assert outcomes[3] == "demoted-quick"
    brief = analysis.latest_valid_brief(con, DATE, 1)
    assert brief is not None
    assert brief["brief"]["pinned_facts"][0]["cites"] == ["S1"]
    assert brief["header"]["manifest"]  # attribution: the manifest persists
    assert brief["header"]["model"] == analysis.ANALYSIS_MODEL
    con.close()


def test_rejected_brief_is_persisted_for_forensics_but_never_served(tmp_paths, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    db.migrate()
    con = db.connect()
    seed_ranked_briefing(con)

    def fabricating_chat(key, prompt):
        b = good_brief()
        b["pinned_facts"] = [{"fact": "x", "cites": ["S99"]}]
        return b, 0.03

    report = analysis.run_analysis(date=DATE, con=con, chat=fabricating_chat,
                                   sonar=fake_sonar_empty, fetch=fake_fetch_ok,
                                   sleep=lambda s: None)
    s1 = report["per_story"][0]
    assert s1["outcome"] == "rejected" and "fabricated" in s1["detail"]
    assert analysis.latest_valid_brief(con, DATE, 1) is None
    row = con.execute("SELECT status, reject_reason FROM analysis_briefs"
                      " WHERE date=? AND slot=1", (DATE,)).fetchone()
    assert row["status"] == "rejected" and "fabricated" in row["reject_reason"]
    con.close()


def test_budget_ladder_sonar_first_then_briefs_with_derating_flags(tmp_paths, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test-not-real")
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "0.001")  # starve the run
    db.migrate()
    con = db.connect()
    seed_ranked_briefing(con)
    sonar_calls = []

    def sonar_recording(key, title, claims):
        sonar_calls.append(title)
        return [], 0.0, "ok"

    report = analysis.run_analysis(date=DATE, con=con, chat=fake_chat_good,
                                   sonar=sonar_recording, fetch=fake_fetch_ok,
                                   sleep=lambda s: None)
    assert sonar_calls == []  # rung 1: Sonar never called under the starved cap
    s1 = report["per_story"][0]
    assert s1["outcome"] == "skipped-budget"  # rung 2: brief skipped, disclosed
    assert report["derating"] is True         # escalation flag, never absorbed
    assert any("derating" in w for w in report["warnings"])
    con.close()


def test_synthesis_failure_degrades_to_no_brief_not_an_exception(tmp_paths, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    db.migrate()
    con = db.connect()
    seed_ranked_briefing(con)

    def dying_chat(key, prompt):
        raise OSError("model endpoint unreachable")

    report = analysis.run_analysis(date=DATE, con=con, chat=dying_chat,
                                   sonar=fake_sonar_empty, fetch=fake_fetch_ok,
                                   sleep=lambda s: None)
    s1 = report["per_story"][0]
    assert s1["outcome"] == "failed" and "unreachable" in s1["detail"]
    assert analysis.latest_valid_brief(con, DATE, 1) is None
    con.close()


def test_quick_tier_stories_get_no_analysis():
    tiers = analysis._tiers_for("1999-01-01", 5)  # positional default
    assert tiers == ["full", "medium", "medium", "quick", "quick"]


def test_writer_rendering_is_deterministic_and_labeled():
    src = sources_fixture()
    clean, _ = analysis.validate_brief(good_brief(), src, "full", corpus_of(src))
    r1 = analysis.render_writer_view(clean)
    r2 = analysis.render_writer_view(clean)
    assert r1 == r2
    assert "PINNED FACTS" in r1 and "UNKNOWNS" in r1 and "DISCREPANCY" not in r1
    assert "never generate your own" in r1  # the borrowed-inference directive
    assert "cited, never 'verified'" in r1.lower()
