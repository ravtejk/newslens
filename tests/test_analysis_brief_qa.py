"""M9 milestone 2 — QA adversarial extension: the citation validator IS the
product (the "fake receipts" defense line).

QA-written; extends tests/test_analysis_brief.py (implementer). Fully
offline: model + Sonar are injected fakes, the autouse loopback guard backs
everything. Adversarial focus per dispatch: (1) validator fail-closed over
hostile/deformed JSON, (2) borrowed-inference drop cascade disclosed,
(3) degradation ladder as behavior, (4) M1 hostile fixture -> M2 validator
both directions, (5) money pre-checks on both calls, (6) mechanical sweep.

BUG ledger (test_BUG<n>_* = the fix-loop acceptance criteria,
principal's Option A). BUG10-BUG14 were FIXED at the M2 gate fix loop
(2026-07-06) — their tests are green regression guards now, docstrings
keep the pre-fix story. BUG15 (closing pass) is the open KNOWN-RED:
  BUG10  validate_brief is not total over adversarial JSON shapes — bare
         strings / numbers where dicts/strings are expected raise
         AttributeError/TypeError instead of BriefRejected; at run level
         the whole analysis run dies UNLOGGED after the synthesis call was
         already paid (BUG-6 money-honesty class + BUG-8 crash class).
  BUG11  quote verification sees ASCII straight quotes only — curly-quoted
         fabrications are invisible (false pass), and straight-vs-curly
         apostrophe glyph mismatch between model and corpus false-rejects
         genuinely verbatim quotes. Fix must normalize quote GLYPHS on both
         sides + extend detection to curly marks (direction-safe: wider
         detection, glyph-insensitive matching).
  BUG12  a discrepancy whose two sides cite the identical source key
         passes as "two-sided" — ADR-0012 lists one-sided discrepancies as
         a hard-reject class; identical singleton cite sets are one source
         wearing two hats.
  BUG13  call_analysis_model retry double-pay: attempt 1's tokens are PAID
         (truncation/parse failures happen after the HTTP spend) but only
         attempt 2's cost is returned — real spend under-reported against
         the principal's $0.25 cap (BUG-6 precedent: money honesty is a
         hard requirement).
  BUG14  migration 0008's comment claims "append-only like generation_log"
         but there are no RAISE(ABORT) triggers (BUG-5 precedent, fixed
         then by 0004): a forensic status='rejected' row can be UPDATEd to
         'valid' — the hard-reject is reversible at the DB layer.
  BUG15  render_material's S/R/C loop can starve a real article to an
         EMPTY or P-only material block (share carries no header room, no
         first-entry admission) — the SCR gate has passed, so a fabricated
         brief citing the invisible article's real keys validates: fake
         receipts with code-supplied keys. Found in the closing-pass
         reservation sweep; the with-P case is a residual-3 regression.
"""

from __future__ import annotations

import json
import sqlite3
import types
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

from newslens import analysis, cli, db, diagnose, paths

FIXTURES = Path(__file__).parent / "fixtures" / "analysis"
DATE = "2026-07-06"


# ---------------------------------------------------------------------------
# Self-contained kit (independent of the implementer's module-level helpers)
# ---------------------------------------------------------------------------

def qa_sources():
    """All four key kinds; S1/C1 share an outlet (dup-credit probes); S1's
    text carries a curly apostrophe (BUG11 glyph probes)."""
    return {
        "S1": {"kind": "cluster-full-text", "outlet": "thehill.com",
               "title": "Summit opens", "url": "https://thehill.com/a",
               "retrieved_at": "2026-07-06T00:00Z",
               "text": "The summit opens Tuesday in Ankara. The alliance’s "
                       "drone plan advances through committee. Delegates weigh "
                       "a five percent defense spending pledge."},
        "C1": {"kind": "cluster-excerpt", "outlet": "thehill.com",
               "title": "Committee note", "url": "https://thehill.com/b",
               "retrieved_at": "2026-07-05",
               "text": "Committee work continued into the evening session."},
        "C2": {"kind": "cluster-excerpt", "outlet": "cnbc.com",
               "title": "Pledge push", "url": "https://cnbc.com/c",
               "retrieved_at": "2026-07-05",
               "text": "Allies face pressure over the spending pledge number."},
        "R1": {"kind": "retrieved", "outlet": "reuters.com",
               "title": "Resistance", "url": "https://reuters.com/d",
               "retrieved_at": "2026-07-06T00:00Z",
               "text": "Diplomats said three members resist the pledge."},
        "R2": {"kind": "retrieved", "outlet": "apnews.com",
               "title": "Agenda", "url": "https://apnews.com/e",
               "retrieved_at": "2026-07-06T00:00Z",
               "text": "The agenda includes a drone defense initiative."},
        "P1": {"kind": "prior-briefing", "outlet": "NewsLens (prior edition)",
               "title": "briefing 2026-07-05", "url": "",
               "retrieved_at": "2026-07-05",
               "text": "Yesterday's edition covered pre-summit staging."},
    }


def corpus_of(sources):
    return " ".join(s["text"] for s in sources.values())


def qa_brief():
    """Valid against qa_sources(); deliberately quote-free prose so the
    quote checker fires only where a test plants a quote."""
    return {
        "pinned_facts": [
            {"fact": "The summit opens Tuesday in Ankara.", "cites": ["S1"]},
            {"fact": "Delegates weigh a five percent pledge.",
             "cites": ["S1", "C2"]},
            {"fact": "Committee work on the drone plan continues.",
             "cites": ["C1"]},
        ],
        "ledger": [
            {"claim": "Three members resist the pledge.", "cites": ["R1"]},
        ],
        "mechanism": "Members trade a pledge for security guarantees; each "
                     "parliament pays the domestic cost of the number [S1].",
        "effects": [
            {"effect": "The pledge faces resistance from three members.",
             "basis": "attributed", "holder": "diplomats (Reuters)",
             "cites": ["R1"]},
        ],
        "arc": None,
        "unknowns": [
            {"question": "Which members resist the pledge",
             "why_material": "holdouts can block the communique",
             "would_resolve": "the communique text"},
        ],
        "watch": [
            {"observable": "communique language by Thursday",
             "settles": "whether resistance held"},
            {"observable": "any bilateral statement Wednesday",
             "settles": "what the meeting produced"},
        ],
        "notes_for_writer": "lead with the meeting.",
    }


def validate(raw, sources=None):
    src = sources if sources is not None else qa_sources()
    return analysis.validate_brief(raw, src, "full", corpus_of(src))


def s_brief():
    """Valid against a map whose only offered keys are S1/S2 (seeded items,
    excerpts superseded by their own full texts)."""
    return {
        "pinned_facts": [
            {"fact": "The president travels to the summit.", "cites": ["S1"]},
            {"fact": "The meeting happens midweek.", "cites": ["S1"]},
            {"fact": "Allies discuss spending targets.", "cites": ["S1"]},
        ],
        "ledger": [{"claim": "The summit spans two days.", "cites": ["S1"]}],
        "mechanism": "Attendance is traded for spending commitments; each "
                     "ally answers to its own parliament [S1].",
        "effects": [],
        "arc": None,
        "unknowns": [{"question": "Which allies commit first",
                      "why_material": "sets the communique floor",
                      "would_resolve": "a named-member statement"}],
        "watch": [
            {"observable": "a communique draft by Thursday", "settles": "scope"},
            {"observable": "bilateral statements midweek", "settles": "output"},
        ],
        "notes_for_writer": "",
    }


def r_brief():
    """Valid against a Sonar-only map (R1/R2 offered)."""
    b = s_brief()
    b["pinned_facts"] = [
        {"fact": "Coverage names a drone initiative.", "cites": ["R1"]},
        {"fact": "The agenda is contested.", "cites": ["R2"]},
        {"fact": "Reporting continues through the week.", "cites": ["R1"]},
    ]
    b["ledger"] = [{"claim": "An initiative is on the agenda.", "cites": ["R2"]}]
    b["mechanism"] = "Retrieved coverage frames the agenda fight [R1]."
    return b


def seed_min(con, n_items=2, date=DATE, slots_extra=()):
    """One depth slot backed by n_items source_items (+ optional extras)."""
    with con:
        ids = []
        for i in range(n_items):
            cur = con.execute(
                "INSERT INTO source_items (source_type, outlet, url, title,"
                " raw_excerpt) VALUES ('rss', ?, ?, ?, ?)",
                ("The Hill", f"https://thehill.com/x{i}", f"Story item {i}",
                 "The president travels to the summit midweek."))
            ids.append(cur.lastrowid)
        slots = [{"slot": "1", "story_title": "Summit meetings",
                  "summary": "Summit.", "item_ids": ids, "outlets": ["The Hill"],
                  "matched_tags": [], "matched_memory": [], "override": False,
                  "corroboration_label": "Reported by 1 named outlet"}]
        slots.extend(slots_extra)
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (date, json.dumps(slots)))


def fetch_fixture(url, timeout, cap=0, user_agent=""):
    if url.endswith("/robots.txt"):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    return (FIXTURES / "clean_article.html").read_bytes()


def sonar_none(key, title, claims):
    return [], 0.0, "ok — 0 results"


def sonar_sentinel(key, title, claims):
    raise AssertionError("Sonar callable invoked — the pre-call budget check "
                         "must run BEFORE any Sonar spend")


def chat_sentinel(key, prompt):
    raise AssertionError("synthesis callable invoked — the pre-call estimate "
                         "must run BEFORE any synthesis spend")


CFG_STUB = types.SimpleNamespace(sources=[])
ENV_OK = {"OPENAI_API_KEY": "sk-test-not-real",
          "PERPLEXITY_API_KEY": "pplx-test-not-real"}


def story_kwargs(**over):
    kw = dict(tier="medium", cfg=CFG_STUB, openai_key="sk-test-not-real",
              pplx_key="pplx-test-not-real", remaining_usd=0.25,
              memory_lines=[], prior=[], fetch=fetch_fixture,
              chat=chat_sentinel, sonar=sonar_none, sleep=lambda s: None)
    kw.update(over)
    return kw


# ---------------------------------------------------------------------------
# 1. BUG10 — validator totality: adversarial JSON must land in BriefRejected,
#    never an unhandled crash
# ---------------------------------------------------------------------------

def _shape(name):
    b = qa_brief()
    if name == "pinned-entry-bare-string":
        b["pinned_facts"] = ["The summit opens Tuesday in Ankara."]
    elif name == "pinned-fact-number":
        b["pinned_facts"][0] = {"fact": 12345, "cites": ["S1"]}
    elif name == "ledger-claim-number":
        b["ledger"] = [{"claim": 42, "cites": ["S1"]}]
    elif name == "discrepancy-side-bare-string":
        b["ledger"] = [{"discrepancy": True, "a": "July 8",
                        "b": {"value": "Wednesday", "cites": ["S1"]}}]
    elif name == "unknown-question-number":
        b["unknowns"] = [{"question": 7, "why_material": "x",
                          "would_resolve": "y"}]
    elif name == "watch-observable-number":
        b["watch"] = [{"observable": 99, "settles": "x"},
                      {"observable": "real one", "settles": "y"}]
    return b


@pytest.mark.parametrize("shape", [
    "pinned-entry-bare-string", "pinned-fact-number", "ledger-claim-number",
    "discrepancy-side-bare-string", "unknown-question-number",
    "watch-observable-number",
])
def test_BUG10_validator_is_total_over_adversarial_json_shapes(shape):
    """GREEN since the M2 gate fix loop — was KNOWN-RED (BUG10). Contract: for ANY JSON-decodable model output,
    validate_brief either returns a clean brief or raises BriefRejected —
    never AttributeError/TypeError. The model author is an adversary here:
    gpt-4o can emit a bare string in pinned_facts on any given day, and a
    non-BriefRejected exception is not a disclosed outcome.

    Today: each parametrized shape raises AttributeError (str.get) or
    TypeError (regex/join over an int) out of the validator.

    Fix contract: coerce-or-reject at every entry boundary (isinstance
    guards on pinned entries and discrepancy sides; str() or reject on
    fact/claim/question/observable before regex and join). Rejection text
    should name the malformed section. These tests pass when every shape
    lands in BriefRejected (or validates after safe coercion)."""
    try:
        validate(_shape(shape))
    except analysis.BriefRejected:
        pass  # a disclosed rejection satisfies the contract


def test_BUG10_adversarial_shape_is_a_disclosed_outcome_not_a_run_crash(tmp_paths):
    """GREEN since the M2 gate fix loop — was KNOWN-RED (BUG10, run level). The synthesis call is PAID before
    validation; a validator crash kills run_analysis with no _append_log
    entry — the paid cost vanishes from the record (BUG-6 money-honesty
    class) and every other slot's work is lost with it.

    Contract: run_analysis returns a report; the slot's outcome is in the
    disclosed vocabulary ('rejected' or 'failed'); the analysis stage entry
    IS appended to generation_log.jsonl."""
    db.migrate()
    con = db.connect()
    try:
        seed_min(con)

        def adversarial_chat(key, prompt):
            return {**s_brief(), "pinned_facts": ["a bare string"]}, 0.03

        report = analysis.run_analysis(date=DATE, con=con, env=dict(ENV_OK),
                                       chat=adversarial_chat, sonar=sonar_none,
                                       fetch=fetch_fixture, sleep=lambda s: None)
        assert report["per_story"][0]["outcome"] in ("rejected", "failed")
        log = (paths.DATA_DIR / "generation_log.jsonl").read_text(encoding="utf-8")
        assert any(json.loads(l).get("stage") == "analysis"
                   for l in log.splitlines() if l.strip())
    finally:
        con.close()


def test_weird_but_survivable_shapes_do_not_crash_today():
    """Green tolerance pin: shapes the validator already survives — extra
    top-level keys, ledger/effects as strings (chars skipped as non-dicts),
    non-dict arc (dropped), bracketed cite strings ('[S1]' tolerated)."""
    b = qa_brief()
    b["surprise_key"] = {"nested": [1, 2]}
    b["arc"] = "not a dict"
    b["pinned_facts"][0]["cites"] = ["[S1]"]  # bracket-wrapped: stripped
    clean, _ = validate(b)
    assert clean["arc"] is None
    assert clean["pinned_facts"][0]["cites"] == ["S1"]
    b2 = qa_brief()
    b2["ledger"] = "not a list"   # str iteration yields non-dict chars
    b2["effects"] = "also not"
    clean2, _ = validate(b2)
    assert clean2["ledger"] == [] and clean2["effects"] == []


# ---------------------------------------------------------------------------
# 2. BUG11 — smart quotes, both directions (dispatch-named)
# ---------------------------------------------------------------------------

def test_BUG11_curly_quoted_fabrication_must_not_bypass_the_quote_check():
    """GREEN since the M2 gate fix loop — was KNOWN-RED (BUG11 direction 1: false PASS). _QUOTE_RE matches ASCII
    straight quotes only; a fabricated quote wrapped in curly marks
    (U+201C/201D — what chat models emit inside JSON strings to dodge
    escaping) is invisible to the checker and sails into a 'valid' brief.

    Today: this brief validates clean. Contract: the fabricated curly-quoted
    sentence is detected and the brief HARD-REJECTS exactly as its
    straight-quoted twin would.

    Fix contract (direction-safe): extend quote detection to curly pairs
    AND normalize quote/apostrophe glyphs identically on both the candidate
    quote and the corpus before the substring test — wider detection can
    only catch more fabrications; symmetric glyph normalization can only
    repair glyph-variant matches, never manufacture one."""
    b = qa_brief()
    b["mechanism"] += (" One diplomat claimed “the alliance secretly "
                       "agreed to disband itself entirely by 2027.”")
    with pytest.raises(analysis.BriefRejected):
        validate(b)


def test_BUG11_apostrophe_glyph_mismatch_must_not_false_reject_verbatim_quote():
    """GREEN since the M2 gate fix loop — was KNOWN-RED (BUG11 direction 2: false REJECT). The corpus says
    alliance’s (curly, as real article HTML does); the model quotes it
    with a straight apostrophe inside straight marks. The words are
    verbatim; only the apostrophe glyph differs. Today the substring test
    fails and a truthful brief dies. Contract: glyph-normalized comparison
    accepts it."""
    b = qa_brief()
    b["ledger"].append(
        {"claim": "Reporters note \"the alliance's drone plan advances "
                  "through committee\" in filings.", "cites": ["S1"]})
    clean, _ = validate(b)  # must validate: the quote IS verbatim modulo glyph
    assert len(clean["ledger"]) == 2


def test_curly_verbatim_quote_passes_now_and_must_keep_passing_after_the_fix():
    """Direction-safety pin for the BUG11 fix: a quote that is verbatim
    INCLUDING its curly glyphs (curly marks outside, curly apostrophe
    inside, exactly as the corpus has it) validates today (the checker is
    blind to it) and MUST still validate once detection widens — the fix
    may not turn glyph-faithful verbatim quotes into rejects."""
    b = qa_brief()
    b["mechanism"] += (" Reporters wrote “the alliance’s drone plan "
                       "advances through committee” on the record.")
    clean, _ = validate(b)
    assert "drone plan" in clean["mechanism"]


def test_straight_quoted_fabrication_still_rejects():
    """Regression floor under BUG11 work: the straight-quote path the
    checker DOES see keeps hard-rejecting."""
    b = qa_brief()
    b["mechanism"] += (" He said \"the alliance secretly agreed to disband "
                       "itself entirely\" yesterday.")
    with pytest.raises(analysis.BriefRejected):
        validate(b)


# ---------------------------------------------------------------------------
# 3. Cite-shape fail-safes (green: every deformity degrades AWAY from trust)
# ---------------------------------------------------------------------------

def test_pinned_fact_with_dict_shaped_cites_fails_closed():
    """cites entries that aren't strings are ignored by _cites_of, so a
    pinned fact citing [{'key':'S1'}] is an UNcited pinned fact -> reject.
    Fail-closed is the right direction; pinned."""
    b = qa_brief()
    b["pinned_facts"][0]["cites"] = [{"key": "S1"}]
    with pytest.raises(analysis.BriefRejected, match="no citation"):
        validate(b)


def test_pinned_fact_with_string_cites_fails_closed_as_fabrication():
    """cites as a bare string iterates CHARACTERS ('S','1'); single chars
    can never be manifest keys, so the entry rejects as fabricated rather
    than silently passing. Fail-closed; pinned as actual."""
    b = qa_brief()
    b["pinned_facts"][0]["cites"] = "S1"
    with pytest.raises(analysis.BriefRejected, match="fabricated"):
        validate(b)


def test_discrepancy_side_with_dict_shaped_cites_rejects_as_uncited():
    b = qa_brief()
    b["ledger"] = [{"discrepancy": True,
                    "a": {"value": "July 8", "cites": [{"key": "S1"}]},
                    "b": {"value": "Wednesday", "cites": ["C2"]}}]
    with pytest.raises(analysis.BriefRejected, match="uncited"):
        validate(b)


def test_discrepancy_side_with_fabricated_key_rejects():
    b = qa_brief()
    b["ledger"] = [{"discrepancy": True,
                    "a": {"value": "July 8", "cites": ["S9"]},
                    "b": {"value": "Wednesday", "cites": ["C2"]}}]
    with pytest.raises(analysis.BriefRejected, match="fabricated"):
        validate(b)


def test_effect_with_dict_shaped_cites_is_dropped_and_disclosed():
    """Borrowed-inference enforcement covers malformed receipts too: an
    effect whose cites are unusable is an effect without receipts ->
    dropped AND disclosed, never rendered."""
    b = qa_brief()
    b["effects"][0]["cites"] = [{"key": "R1"}]
    clean, warnings = validate(b)
    assert clean["effects"] == []
    assert any("dropped 1 effect" in w for w in warnings)


def test_ledger_entry_with_dict_shaped_cites_degrades_to_stable_background():
    """The weakest link, pinned as actual: unusable cites on a ledger entry
    degrade it to the labeled stable-background lane (no borrowed
    authority, label renders) with a disclosure warning — it does NOT
    inherit any provenance credit."""
    b = qa_brief()
    b["ledger"] = [{"claim": "Committee work continues.",
                    "cites": [{"key": "S1"}]}]
    clean, warnings = validate(b)
    assert clean["ledger"][0]["provenance"] == "stable-background"
    assert any("stable-background" in w for w in warnings)


@pytest.mark.parametrize("bad_key", ["s1", "Ｓ1"])  # lowercase, fullwidth S
def test_case_and_homoglyph_keys_reject_not_normalize(bad_key):
    """Key matching is exact: 's1' and homoglyph 'Ｓ1' are NOT quietly
    normalized into S1 — they reject as fabricated. Normalizing keys would
    widen the acceptance surface for free."""
    b = qa_brief()
    b["pinned_facts"][0]["cites"] = [bad_key]
    with pytest.raises(analysis.BriefRejected, match="fabricated"):
        validate(b)


# ---------------------------------------------------------------------------
# 4. Provenance unit battery (green: computed, never inflatable)
# ---------------------------------------------------------------------------

def test_duplicate_cites_cannot_inflate_corroboration():
    src = qa_sources()
    assert analysis.compute_provenance(["S1", "S1"], src) == "cluster-single"


def test_same_outlet_via_two_keys_is_still_cluster_single():
    """S1 and C1 are both thehill.com: two keys, ONE outlet — the outlet
    set (not the key count) drives corroboration."""
    src = qa_sources()
    assert analysis.compute_provenance(["S1", "C1"], src) == "cluster-single"


def test_two_distinct_outlets_corroborate():
    src = qa_sources()
    assert analysis.compute_provenance(["S1", "C2"], src) == \
        "cluster-corroborated (2 outlets)"


def test_retrieved_material_never_inherits_cluster_corroboration():
    """Contract :544 verbatim rule: cluster-single + an R cite stays
    cluster-single; R never counts toward the outlet set."""
    src = qa_sources()
    assert analysis.compute_provenance(["C2", "R1"], src) == "cluster-single"


def test_r_only_cites_are_retrieved_single_with_the_outlet_named():
    src = qa_sources()
    assert analysis.compute_provenance(["R1"], src) == \
        "retrieved-single (reuters.com)"
    assert analysis.compute_provenance(["R1", "R2"], src) == \
        "retrieved-single (reuters.com)"  # first R named; still single-class


def test_prior_briefing_only_cites_are_stable_background():
    """P-cites are contract-legal material (§5 universe item b) but earn no
    corroboration: a P-only claim carries the stable-background label — a
    prior NewsLens edition can never be laundered into a source outlet."""
    src = qa_sources()
    assert analysis.compute_provenance(["P1"], src) == "stable-background"
    assert analysis.compute_provenance([], src) == "stable-background"


# ---------------------------------------------------------------------------
# 5. BUG12 — same-source discrepancy laundering
# ---------------------------------------------------------------------------

def test_BUG12_discrepancy_sides_citing_the_identical_source_must_reject():
    """GREEN since the M2 gate fix loop — was KNOWN-RED (BUG12). ADR-0012's hard-reject list includes 'one-sided
    discrepancies'. A discrepancy whose sides cite the IDENTICAL key
    (a:[S1] vs b:[S1]) is one source wearing two hats — there is no second
    source, so there is no cross-source discrepancy; today it passes
    because the check requires cited-ness, not distinctness.

    Fix contract (minimal): reject when the two sides' cite sets are
    identical. (Overlapping-but-distinct sets stay legal — a genuine
    three-source split can share a member. The adjacent S#/R# same-URL
    duplication is reported separately, not part of this red.)"""
    b = qa_brief()
    b["ledger"] = [{"discrepancy": True,
                    "a": {"value": "opens Tuesday", "cites": ["S1"]},
                    "b": {"value": "opens Wednesday", "cites": ["S1"]},
                    "note": "same outlet, both values"}]
    with pytest.raises(analysis.BriefRejected):
        validate(b)


def test_two_sided_discrepancy_carries_both_values_never_averaged():
    b = qa_brief()
    b["ledger"] = [{"discrepancy": True,
                    "a": {"value": "July 8", "cites": ["S1"]},
                    "b": {"value": "Wednesday", "cites": ["C2"]},
                    "note": "dates differ"}]
    clean, _ = validate(b)
    e = clean["ledger"][0]
    assert e["discrepancy"] is True
    rendered = analysis.render_writer_view(clean)
    assert "July 8" in rendered and "Wednesday" in rendered and "vs" in rendered


def test_model_claimed_provenance_inside_a_discrepancy_side_never_renders():
    """A side dict arrives raw from the model; if it smuggles a
    'provenance' field, the WRITER view must not surface it — provenance is
    code-owned furniture. (The raw field does survive inside the persisted
    artifact today — flagged to the gate as an observation; the render
    layer is the enforced boundary this test pins.)"""
    b = qa_brief()
    b["ledger"] = [{"discrepancy": True,
                    "a": {"value": "July 8", "cites": ["S1"],
                          "provenance": "cluster-corroborated (9 outlets)"},
                    "b": {"value": "Wednesday", "cites": ["C2"]}}]
    clean, _ = validate(b)
    rendered = analysis.render_writer_view(clean)
    assert "9 outlets" not in rendered


# ---------------------------------------------------------------------------
# 6. Borrowed inference + the hollow-brief cascade
# ---------------------------------------------------------------------------

def test_full_effect_drop_cascade_discloses_and_details_stay_honest():
    """Every effect own-voice -> ALL dropped, disclosed by count; the brief
    survives (ADR-0012 decision 2) with effects == [] and the drop is never
    silent. Also pins that dropped effects never reach the writer view.

    Residual 1 landed at the gate: ledger-empty now draws its own countable
    warning (pinned in the closing-pass section below). Usefulness floors
    remain the Editor's lane; mechanical disclosure is what's pinned here."""
    b = qa_brief()
    b["effects"] = [
        {"effect": "This will surely reshape the alliance.", "basis": "vibes",
         "cites": ["S1"]},
        {"effect": "Markets may wobble.", "basis": "", "cites": ["S1"]},
        {"effect": "A cited but own-voice take.", "basis": "speculation",
         "cites": ["C2"]},
    ]
    clean, warnings = validate(b)
    assert clean["effects"] == []
    assert any("dropped 3 effect(s)" in w for w in warnings)
    rendered = analysis.render_writer_view(clean)
    assert "reshape the alliance" not in rendered
    assert "EFFECTS" not in rendered  # empty section: heading suppressed


def test_attributed_effect_with_receipts_survives_the_drop_filter():
    clean, warnings = validate(qa_brief())
    assert len(clean["effects"]) == 1
    assert clean["effects"][0]["basis"] == "attributed"
    assert not any("dropped" in w for w in warnings)


def test_minimal_brief_validates_with_band_warnings_not_silence():
    """The hollow-brief floor, pinned as actual: nulled ledger/effects/arc,
    empty unknowns/watch -> valid, but the band warnings disclose the
    thinness (0 unknowns, 0 watch) — it is not a silent hollowing."""
    b = qa_brief()
    b["ledger"] = None
    b["effects"] = None
    b["unknowns"] = []
    b["watch"] = []
    clean, warnings = validate(b)
    assert clean["ledger"] == [] and clean["effects"] == []
    assert any("unknowns count 0" in w for w in warnings)
    assert any("watch count 0" in w for w in warnings)
    # residual 1 (closing pass): the hollowing is now countable too
    assert any("ledger empty — no attributed takes" in w for w in warnings)


# ---------------------------------------------------------------------------
# 7. Degradation ladder — direct analyze_story boundaries
# ---------------------------------------------------------------------------

def _con(tmp_paths):
    db.migrate()
    return db.connect()


def test_sonar_precheck_skips_below_the_line_and_synthesis_still_runs(tmp_paths):
    """Rung independence: remaining 0.0619 is under the Sonar line
    (0.0619 - 0.012 < 0.05 probe) -> Sonar sentinel NEVER called, derating
    disclosed — while the synthesis estimate still fits, so the brief is
    built. Sonar degrades FIRST and alone."""
    con = _con(tmp_paths)
    try:
        seed_min(con)
        slot = json.loads(con.execute(
            "SELECT story_slots FROM briefings WHERE date=?",
            (DATE,)).fetchone()["story_slots"])[0]
        sa = analysis.analyze_story(
            con, DATE, 1, slot, **story_kwargs(
                remaining_usd=0.0619, sonar=sonar_sentinel,
                chat=lambda k, p: (s_brief(), 0.01)))
        assert sa.outcome == "ok"
        assert "budget ladder" in sa.sonar_status
        assert any(w.startswith("derating: Sonar") for w in sa.warnings)
    finally:
        con.close()


def test_sonar_precheck_boundary_calls_just_above_the_line(tmp_paths):
    con = _con(tmp_paths)
    try:
        seed_min(con)
        slot = json.loads(con.execute(
            "SELECT story_slots FROM briefings WHERE date=?",
            (DATE,)).fetchone()["story_slots"])[0]
        calls = []

        def sonar_recording(key, title, claims):
            calls.append(title)
            return [], 0.0, "ok — 0 results"

        sa = analysis.analyze_story(
            con, DATE, 1, slot, **story_kwargs(
                remaining_usd=0.0621, sonar=sonar_recording,
                chat=lambda k, p: (s_brief(), 0.01)))
        assert calls == ["Summit meetings"]
        assert sa.outcome == "ok"
    finally:
        con.close()


def test_synthesis_precheck_blocks_the_call_and_persists_no_row(tmp_paths):
    """Rung 2: estimate > remaining -> skipped-budget; the chat sentinel is
    never invoked (nothing calls before its cap check) and skip outcomes
    write NO analysis_briefs row (only ok/rejected persist)."""
    con = _con(tmp_paths)
    try:
        seed_min(con)
        slot = json.loads(con.execute(
            "SELECT story_slots FROM briefings WHERE date=?",
            (DATE,)).fetchone()["story_slots"])[0]
        sa = analysis.analyze_story(
            con, DATE, 1, slot, **story_kwargs(
                remaining_usd=0.001, sonar=sonar_sentinel))
        assert sa.outcome == "skipped-budget"
        assert any(w.startswith("derating: analysis brief skipped")
                   for w in sa.warnings)
        assert con.execute(
            "SELECT COUNT(*) c FROM analysis_briefs").fetchone()["c"] == 0
    finally:
        con.close()


def test_synthesis_death_is_failed_with_no_row_and_no_exception(tmp_paths):
    con = _con(tmp_paths)
    try:
        seed_min(con)
        slot = json.loads(con.execute(
            "SELECT story_slots FROM briefings WHERE date=?",
            (DATE,)).fetchone()["story_slots"])[0]

        def dying_chat(key, prompt):
            raise OSError("endpoint gone")

        sa = analysis.analyze_story(con, DATE, 1, slot,
                                    **story_kwargs(chat=dying_chat))
        assert sa.outcome == "failed" and "endpoint gone" in sa.detail
        assert con.execute(
            "SELECT COUNT(*) c FROM analysis_briefs").fetchone()["c"] == 0
    finally:
        con.close()


def test_prior_briefings_alone_never_ground_a_brief(tmp_paths):
    """The P-gate: with prior-briefing material present but zero S/C/R,
    the total-failure rule fires (skipped-thin) and the model is never
    called — a NewsLens edition can't be tomorrow's only source."""
    con = _con(tmp_paths)
    try:
        slot = {"story_title": "Ghost story", "summary": "s", "item_ids": []}
        sa = analysis.analyze_story(
            con, DATE, 2, slot, **story_kwargs(
                prior=[{"date": "2026-07-05", "text": "yesterday's edition"}]))
        assert sa.outcome == "skipped-thin"
        assert con.execute(
            "SELECT COUNT(*) c FROM analysis_briefs").fetchone()["c"] == 0
    finally:
        con.close()


def test_slot3_demotion_precedes_the_total_failure_rule(tmp_paths):
    """ADR-0012 decision 3 ordering pin: slot 3 with NO material satisfies
    both the demotion condition and skipped-thin — demotion wins (the tier
    call IS the outcome), the model is never called, no row persists."""
    con = _con(tmp_paths)
    try:
        slot = {"story_title": "Slot three", "summary": "s", "item_ids": []}
        sa = analysis.analyze_story(
            con, DATE, 3, slot, **story_kwargs(
                prior=[{"date": "2026-07-05", "text": "prior"}]))
        assert sa.outcome == "demoted-quick"
        assert "thin material" in sa.detail
        assert con.execute(
            "SELECT COUNT(*) c FROM analysis_briefs").fetchone()["c"] == 0
    finally:
        con.close()


def test_slot3_with_one_sonar_result_still_demotes(tmp_paths):
    """The <2 boundary from the demotion condition, low side."""
    con = _con(tmp_paths)
    try:
        slot = {"story_title": "Slot three", "summary": "s", "item_ids": []}
        one = [{"url": "https://a.com/1", "title": "t", "snippet": "snippet"}]
        sa = analysis.analyze_story(
            con, DATE, 3, slot, **story_kwargs(
                sonar=lambda k, t, c: (one, 0.001, "ok — 1 results")))
        assert sa.outcome == "demoted-quick"
    finally:
        con.close()


def test_slot3_with_two_sonar_results_is_not_demoted(tmp_paths):
    """High side of the boundary: 2 retrieved results = enough material for
    the medium brief path; synthesis proceeds on R material."""
    con = _con(tmp_paths)
    try:
        slot = {"story_title": "Slot three", "summary": "s", "item_ids": []}
        two = [{"url": "https://a.com/1", "title": "t1", "snippet": "first result"},
               {"url": "https://b.com/2", "title": "t2", "snippet": "second result"}]
        sa = analysis.analyze_story(
            con, DATE, 3, slot, **story_kwargs(
                sonar=lambda k, t, c: (two, 0.001, "ok — 2 results"),
                chat=lambda k, p: (r_brief(), 0.01)))
        assert sa.outcome == "ok"
    finally:
        con.close()


def test_thin_material_outside_slot3_is_skipped_thin_not_demoted(tmp_paths):
    """The demotion is slot-3-specific (the reconciliation's scope): slot 2
    with the same thinness takes the disclosed no-brief path."""
    con = _con(tmp_paths)
    try:
        slot = {"story_title": "Slot two", "summary": "s", "item_ids": []}
        sa = analysis.analyze_story(con, DATE, 2, slot, **story_kwargs())
        assert sa.outcome == "skipped-thin"
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 8. Money paths
# ---------------------------------------------------------------------------

def test_BUG13_retry_must_account_for_both_paid_attempts(monkeypatch):
    """GREEN since the M2 gate fix loop — was KNOWN-RED (BUG13). Attempt 1 completes the HTTP call (tokens PAID),
    then fails post-payment (finish_reason=length). The retry succeeds and
    call_analysis_model returns ONLY attempt 2's cost — the run's recorded
    spend against the principal's $0.25 cap under-reports by the full price
    of attempt 1 (here: half the real spend).

    Fix contract: accumulate usage-derived cost across ALL attempts that
    returned usage, and surface it in the returned cost (so analyze_story's
    sa.cost_usd and the generation_log per-story row carry real spend).
    BUG-6 precedent: money honesty is a hard requirement."""
    responses = [
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 1400},
         "choices": [{"finish_reason": "length", "message": {"content": ""}}]},
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 100},
         "choices": [{"finish_reason": "stop",
                      "message": {"content": json.dumps({"ok": True})}}]},
    ]
    calls = []
    monkeypatch.setattr(analysis, "_analysis_chat",
                        lambda key, prompt: (calls.append(1),
                                             responses[len(calls) - 1])[1])
    monkeypatch.setattr(analysis.time, "sleep", lambda s: None)
    parsed, cost = analysis.call_analysis_model("sk-test-not-real", "p")
    assert parsed == {"ok": True} and len(calls) == 2
    cost_attempt1 = (1000 / 1e6 * analysis.ANALYSIS_USD_IN_PER_MTOK
                     + 1400 / 1e6 * analysis.ANALYSIS_USD_OUT_PER_MTOK)
    cost_attempt2 = (1000 / 1e6 * analysis.ANALYSIS_USD_IN_PER_MTOK
                     + 100 / 1e6 * analysis.ANALYSIS_USD_OUT_PER_MTOK)
    assert cost == pytest.approx(cost_attempt1 + cost_attempt2)


def test_estimate_formula_is_conservative_chars_over_four_plus_full_output():
    est = analysis.estimate_synthesis_usd("x" * 4000)
    expected = (1000 / 1e6 * analysis.ANALYSIS_USD_IN_PER_MTOK
                + analysis.ANALYSIS_MAX_TOKENS / 1e6
                * analysis.ANALYSIS_USD_OUT_PER_MTOK)
    assert est == pytest.approx(expected)


def test_run_analysis_is_keyless_hard_stop(tmp_paths, no_network):
    """No OPENAI key -> RuntimeError before any work; with no_network armed
    the failure is provably socket-free."""
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        analysis.run_analysis(env={})


def test_story_cost_accumulates_sonar_plus_synthesis(tmp_paths):
    con = _con(tmp_paths)
    try:
        seed_min(con)
        slot = json.loads(con.execute(
            "SELECT story_slots FROM briefings WHERE date=?",
            (DATE,)).fetchone()["story_slots"])[0]
        sa = analysis.analyze_story(
            con, DATE, 1, slot, **story_kwargs(
                sonar=lambda k, t, c: ([], 0.005, "ok — 0 results"),
                chat=lambda k, p: (s_brief(), 0.03)))
        assert sa.outcome == "ok"
        assert sa.cost_usd == pytest.approx(0.035)
    finally:
        con.close()


def test_per_story_log_rows_carry_the_cost_and_outcome_fields(tmp_paths):
    """ADR-0012 decision 4 (Onna's per-story cost demand): every per_story
    row carries slot/tier/outcome/detail/cost_usd/fetch/sonar; total_usd
    sums; the entry lands in generation_log with stage=analysis."""
    con = _con(tmp_paths)
    try:
        seed_min(con)
        report = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK),
            chat=lambda k, p: (s_brief(), 0.03), sonar=sonar_none,
            fetch=fetch_fixture, sleep=lambda s: None)
        row = report["per_story"][0]
        assert {"slot", "tier", "outcome", "detail", "cost_usd",
                "fetch_ok", "fetch_attempted", "sonar"} <= set(row)
        assert report["total_usd"] == pytest.approx(
            sum(r["cost_usd"] for r in report["per_story"]))
        logged = [json.loads(l) for l in
                  (paths.DATA_DIR / "generation_log.jsonl")
                  .read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(e.get("stage") == "analysis" and e.get("per_story")
                   for e in logged)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 9. Persistence & forensics (migration 0008 semantics)
# ---------------------------------------------------------------------------

def _insert_row(con, status="rejected", slot=1):
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " reject_reason, model, cost_usd) VALUES (?, ?, 'full', ?, '{}', ?,"
        " 'gpt-4o', 0.01)",
        (DATE, slot, status,
         "fabricated citation 'S9'" if status == "rejected" else None))


def test_BUG14_analysis_briefs_update_must_be_structurally_refused(tmp_paths):
    """GREEN since the M2 gate fix loop — was KNOWN-RED (BUG14). Migration 0008's own comment claims 'append-only
    like generation_log', and the ADR calls rejected rows forensic — but
    there are no RAISE(ABORT) triggers, so this UPDATE quietly flips a
    forensic rejection into a servable 'valid' brief. BUG-5 precedent:
    the same claim on ranking_runs got structural triggers in 0004.

    Fix contract: UPDATE and DELETE on analysis_briefs raise
    IntegrityError naming append-only (trigger pair like 0004's). Note the
    wrinkle for the implementer: 0008 is already applied to the live DB, so
    the triggers need a path onto existing installs (a follow-up migration,
    or amending 0008 plus a documented re-apply) — the contract is that
    BOTH fresh and existing DBs end up enforced."""
    con = _con(tmp_paths)
    try:
        with con:
            _insert_row(con, status="rejected")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with con:
                con.execute("UPDATE analysis_briefs SET status='valid'"
                            " WHERE date=? AND slot=1", (DATE,))
    finally:
        con.close()


def test_BUG14_analysis_briefs_delete_must_be_structurally_refused(tmp_paths):
    con = _con(tmp_paths)
    try:
        with con:
            _insert_row(con, status="rejected")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with con:
                con.execute("DELETE FROM analysis_briefs WHERE date=?", (DATE,))
    finally:
        con.close()


def test_newest_valid_wins_and_a_newer_rejection_never_shadows(tmp_paths):
    """Read semantics pinned both ways: regeneration adds rows and the
    newest VALID row is served; a rejected row that is newer than a valid
    one neither serves nor shadows the older valid brief."""
    con = _con(tmp_paths)
    try:
        header = {"manifest": {"S1": {"url": "u", "outlet": "o",
                                      "kind": "cluster-full-text"}}}
        analysis.persist_brief(con, DATE, 1, "full", "valid",
                               {"marker": "A"}, "", 0.01, header)
        analysis.persist_brief(con, DATE, 1, "full", "valid",
                               {"marker": "B"}, "", 0.01, header)
        doc = analysis.latest_valid_brief(con, DATE, 1)
        assert doc["brief"]["marker"] == "B"
        analysis.persist_brief(con, DATE, 1, "full", "rejected", None,
                               "fabricated citation 'S9'", 0.02, header)
        doc2 = analysis.latest_valid_brief(con, DATE, 1)
        assert doc2["brief"]["marker"] == "B"  # rejected row never shadows
    finally:
        con.close()


def test_rejected_rows_keep_the_manifest_and_reason_for_forensics(tmp_paths):
    """A rejection is evidence: the persisted doc carries brief=None, the
    reason names the offense, and the header manifest records exactly what
    the model was offered (what it fabricated AGAINST)."""
    con = _con(tmp_paths)
    try:
        header = {"manifest": {"S1": {"url": "u", "outlet": "o",
                                      "kind": "cluster-full-text"}}}
        analysis.persist_brief(con, DATE, 2, "medium", "rejected", None,
                               "fabricated citation 'S9' in pinned fact 1",
                               0.02, header)
        row = con.execute(
            "SELECT brief_json, reject_reason, status FROM analysis_briefs"
            " WHERE date=? AND slot=2", (DATE,)).fetchone()
        assert row["status"] == "rejected"
        assert "fabricated" in row["reject_reason"]
        doc = json.loads(row["brief_json"])
        assert doc["brief"] is None
        assert "S1" in doc["header"]["manifest"]
    finally:
        con.close()


def test_latest_valid_brief_returns_the_header_brief_document_shape(tmp_paths):
    """The M3 seam pin: readers get the {header, brief} DOC, not the bare
    brief — the writer must consume doc['brief'] and may read the manifest
    from doc['header']. Freezing the shape so M3 can't misgrab."""
    con = _con(tmp_paths)
    try:
        analysis.persist_brief(con, DATE, 1, "full", "valid", {"x": 1}, "",
                               0.0, {"manifest": {}})
        doc = analysis.latest_valid_brief(con, DATE, 1)
        assert set(doc) == {"header", "brief"}
        assert analysis.latest_valid_brief(con, "1999-01-01", 1) is None
    finally:
        con.close()


def test_run_analysis_default_connection_lands_in_the_sandboxed_db(tmp_paths):
    """Structural spend/sandbox guard for the NEW table: run_analysis with
    con=None opens db.connect() on paths.DB_PATH, which the autouse sandbox
    redirects — analysis_briefs rows land in the tmp DB, never the repo's
    live data/newslens.db (whose 2 real M2 artifacts stay untouched)."""
    assert str(paths.DB_PATH).startswith(str(tmp_paths))
    db.migrate()
    con = db.connect()
    seed_min(con)
    con.close()
    analysis.run_analysis(date=DATE, con=None, env=dict(ENV_OK),
                          chat=lambda k, p: (s_brief(), 0.03),
                          sonar=sonar_none, fetch=fetch_fixture,
                          sleep=lambda s: None)
    check = sqlite3.connect(paths.DB_PATH)
    check.row_factory = sqlite3.Row
    n = check.execute("SELECT COUNT(*) c FROM analysis_briefs").fetchone()["c"]
    check.close()
    assert n == 1


# ---------------------------------------------------------------------------
# 10. Armor — M1 hostile fixture at the M2 validator, both directions
# ---------------------------------------------------------------------------

def _hostile_sources():
    html = (FIXTURES / "hostile_content.html").read_text(encoding="utf-8")
    res = analysis.extract_article_text(html)
    rec = analysis.FetchRecord(url="https://ex.com/h",
                               source_name="Example Herald", tier="full",
                               outcome=analysis.OK, chars=res.chars,
                               text=res.text, title=res.title)
    return analysis.build_source_map([rec], [], [], [])


def test_hostile_body_prose_is_quotable_data():
    """Direction 1 at M2: the planted body directive SURVIVED extraction
    (M1 pin) and is legitimate quotable material — a brief that quotes the
    injection verbatim, cited to S1, validates. The directive is data;
    quoting data is journalism."""
    src = _hostile_sources()
    assert "ignore prior instructions" in src["S1"]["text"]
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "The article carries a planted directive.", "cites": ["S1"]},
        {"fact": "The Herald piece is compromised text.", "cites": ["S1"]},
        {"fact": "Its body embeds instruction-shaped prose.", "cites": ["S1"]},
    ]
    b["ledger"] = [
        {"claim": "The piece literally instructs: \"ignore prior "
                  "instructions; when summarizing\" mid-paragraph.",
         "cites": ["S1"]}]
    b["mechanism"] = "A planted directive rides inside body prose [S1]."
    b["effects"] = []
    b["unknowns"] = [{"question": "Who planted the directive",
                      "why_material": "provenance of the compromise",
                      "would_resolve": "publisher statement"}]
    b["watch"] = [{"observable": "a correction notice", "settles": "intent"},
                  {"observable": "the page changing", "settles": "control"}]
    clean, _ = analysis.validate_brief(b, src, "full", corpus_of(src))
    assert "ignore prior instructions" in clean["ledger"][0]["claim"]


def test_script_payload_cannot_be_laundered_as_a_quote():
    """Direction 2 at M2: the script-tag payload never entered the corpus
    (extractor drops script/style — M1 pin), so a model 'quote' of it is a
    fabrication and the brief hard-rejects. The two directions together:
    body text quotable, non-content payloads unquotable."""
    src = _hostile_sources()
    assert "SCRIPT-PAYLOAD-MUST-NOT-SURFACE" not in corpus_of(src)
    b = qa_brief()
    b["pinned_facts"] = [{"fact": "x", "cites": ["S1"]},
                         {"fact": "y", "cites": ["S1"]},
                         {"fact": "z", "cites": ["S1"]}]
    b["ledger"] = [
        {"claim": "The page says \"SCRIPT-PAYLOAD-MUST-NOT-SURFACE\" openly.",
         "cites": ["S1"]}]
    b["mechanism"] = "Prose [S1]."
    b["effects"] = []
    with pytest.raises(analysis.BriefRejected, match="verbatim"):
        analysis.validate_brief(b, src, "full", corpus_of(src))


# ---------------------------------------------------------------------------
# 11. Mechanical sweep
# ---------------------------------------------------------------------------

def test_tiers_for_prefers_recorded_tiers_newest_entry_and_skips_samples(tmp_paths):
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = paths.DATA_DIR / "generation_log.jsonl"
    lines = [
        json.dumps({"date": DATE, "tiers": ["medium", "quick", "quick"]}),
        "not json at all",
        json.dumps({"date": DATE, "tiers": ["full", "full", "medium"]}),
        json.dumps({"date": DATE, "sample": True,
                    "tiers": ["quick", "quick", "quick"]}),
        json.dumps({"date": "2026-07-01", "tiers": ["quick"] * 3}),
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # newest non-sample entry for the date wins; malformed lines tolerated
    assert analysis._tiers_for(DATE, 3) == ["full", "full", "medium"]


def test_tiers_for_short_recorded_list_falls_back_positional(tmp_paths):
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.write_text(json.dumps({"date": DATE, "tiers": ["full"]}) + "\n",
                   encoding="utf-8")
    assert analysis._tiers_for(DATE, 3) == ["full", "medium", "medium"]


def test_render_material_respects_the_budget_and_puts_full_texts_first():
    src = {
        "S1": {"outlet": "a.com", "title": "t1", "text": "A" * 4000},
        "S2": {"outlet": "b.com", "title": "t2", "text": "B" * 4000},
        "S3": {"outlet": "c.com", "title": "t3", "text": "C" * 4000},
        "P1": {"outlet": "NewsLens", "title": "prior", "text": "tiny prior"},
    }
    out = analysis.render_material(src, budget_chars=3000)
    assert len(out) <= 3000 + 2 * 3  # join seams are uncounted; pinned slack
    assert out.index("[S1]") < out.index("[S2]")
    assert "AAAA" in out and "BBBB" in out


def test_render_material_p_reservation_survives_a_crowded_map():
    """CONSCIOUSLY FLIPPED at the M2 gate (residual 3 — the decision this
    freeze existed to force): prior-briefing material now gets a budget
    slice RESERVED before the S/R/C spend (min(total_P_len, budget//6)),
    so P-keys always reach the model's view and the arc-integrity lint can
    never fire a misattributing disclosure about material the model never
    saw. S-entries still dominate; assembly order stays S,R,C,P
    (reservation is budget, not position)."""
    src = {
        "S1": {"outlet": "a.com", "title": "t1", "text": "A" * 4000},
        "S2": {"outlet": "b.com", "title": "t2", "text": "B" * 4000},
        "S3": {"outlet": "c.com", "title": "t3", "text": "C" * 4000},
        "P1": {"outlet": "NewsLens", "title": "prior", "text": "tiny prior"},
    }
    out = analysis.render_material(src, budget_chars=3000)
    assert "[P1]" in out and "tiny prior" in out   # the reservation held
    assert "[S1]" in out                            # full texts still lead
    assert out.index("[S1]") < out.index("[P1]")    # order: S before P
    assert len(out) <= 3000 + 200                   # budget respected


def test_render_material_orders_retrieved_before_excerpts():
    """Actual-order pin: material renders S, then R, then C, then P (note:
    _key_sort used for the source-map LIST orders C before R — two
    deliberate orders, both pinned so a drive-by 'consistency fix' shows)."""
    src = {
        "C1": {"outlet": "c.com", "title": "c", "text": "excerpt text"},
        "R1": {"outlet": "r.com", "title": "r", "text": "retrieved text"},
        "S1": {"outlet": "s.com", "title": "s", "text": "full text"},
    }
    out = analysis.render_material(src, budget_chars=5000)
    assert out.index("[S1]") < out.index("[R1]") < out.index("[C1]")


def test_source_map_full_text_supersedes_its_own_excerpt_and_skips_blank_urls():
    rec = analysis.FetchRecord(url="https://thehill.com/x0",
                               source_name="The Hill", tier="full",
                               outcome=analysis.OK, chars=900,
                               text="full text body", title="T")
    items = [{"outlet": "The Hill", "url": "https://thehill.com/x0",
              "title": "T", "raw_excerpt": "excerpt", "fetched_at": "f"},
             {"outlet": "CNBC", "url": "https://cnbc.com/y", "title": "U",
              "raw_excerpt": "other excerpt", "fetched_at": "f"}]
    sonar = [{"url": "", "title": "no url", "snippet": "dropped"},
             {"url": "https://r.com/1", "title": "kept", "snippet": "s"}]
    src = analysis.build_source_map([rec], items, sonar, [])
    urls = {k: v["url"] for k, v in src.items()}
    assert urls["S1"] == "https://thehill.com/x0"
    assert list(urls.values()).count("https://thehill.com/x0") == 1  # superseded
    assert urls["C1"] == "https://cnbc.com/y"
    assert urls["R1"] == "https://r.com/1" and "R2" not in src


def test_source_map_dedups_sonar_urls_against_cluster_urls():
    """CONSCIOUSLY FLIPPED at fix loop 1 (dispatch-ordered, BUG12-adjacent):
    a Sonar result whose URL is already in the manifest as fetched full
    text or a cluster excerpt is SKIPPED — one URL never wears two keys
    (the S1-vs-R1 masquerade that would let a same-source discrepancy look
    cross-source), and the material budget isn't spent twice on one page.
    The cluster key wins (full text > snippet)."""
    rec = analysis.FetchRecord(url="https://same.com/a", source_name="Same",
                               tier="full", outcome=analysis.OK, chars=900,
                               text="body", title="T")
    sonar = [{"url": "https://same.com/a", "title": "dup", "snippet": "s"},
             {"url": "https://other.com/b", "title": "new", "snippet": "s2"}]
    src = analysis.build_source_map([rec], [], sonar, [])
    assert src["S1"]["url"] == "https://same.com/a"
    r_urls = [v["url"] for k, v in src.items() if k.startswith("R")]
    assert r_urls == ["https://other.com/b"]


def test_render_prompt_replaces_all_nine_placeholders_and_keeps_json_braces():
    """ADR-0012 decision 5 against the REAL template: every placeholder the
    code offers is consumed, the literal JSON example's braces survive, and
    an unknown placeholder passes through untouched (no str.format
    KeyError class)."""
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(
        encoding="utf-8")
    mapping = {"word_budget": "700", "tier": "full", "date": DATE,
               "slot": "1", "story_title": "T", "story_summary": "s",
               "memory_context": "(none)", "source_map": "[S1] x",
               "material": "body"}
    rendered = analysis._render_prompt(template, mapping)
    for name in mapping:
        assert ("{" + name + "}") not in rendered
    assert "pinned_facts" in rendered and "{" in rendered  # JSON example intact
    assert analysis._render_prompt("{foo} stays; {tier} goes",
                                   {"tier": "full"}) == "{foo} stays; full goes"


def test_sonar_prompt_template_renders_and_a_broken_template_degrades(tmp_path, monkeypatch):
    """The sonar prompt still uses str.format (unlike the brief prompt) —
    pin that today's file renders, and that a principal-edited template
    with a stray brace degrades to a disclosed 'failed — sonar prompt did
    not render' status instead of raising (fail-safe, no spend)."""
    real = (paths.PROMPTS_DIR / "analysis_sonar.txt").read_text(encoding="utf-8")
    assert real.format(story_title="T", claims="- c")  # renders today
    broken = tmp_path / "prompts"
    broken.mkdir()
    (broken / "analysis_sonar.txt").write_text(
        "verify {story_title} {claims} against {a JSON example}",
        encoding="utf-8")
    monkeypatch.setattr(paths, "PROMPTS_DIR", broken)
    results, cost, status = analysis._sonar_verify("pplx-test-not-real",
                                                   "T", ["c"])
    assert results == [] and cost == 0.0
    assert status.startswith("failed — sonar prompt did not render")


def test_sonar_verify_keyless_skips_free_and_failure_charges_the_estimate(monkeypatch):
    from newslens import discovery
    results, cost, status = analysis._sonar_verify("", "T", ["c"])
    assert (results, cost) == ([], 0.0) and status.startswith("skipped")

    def dying_sonar(key, prompt):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(discovery, "call_sonar", dying_sonar)
    results, cost, status = analysis._sonar_verify("pplx-test-not-real",
                                                   "T", ["c"])
    assert results == []
    assert cost == analysis.SONAR_EST_USD  # conservative: charged on failure
    assert status.startswith("failed — URLError")


def test_sonar_results_cap_at_eight(monkeypatch):
    from newslens import discovery
    payload = {"usage": {"total_tokens": 1000},
               "search_results": [{"url": f"https://r.com/{i}", "title": "t",
                                   "snippet": "s"} for i in range(12)]}
    monkeypatch.setattr(discovery, "call_sonar", lambda k, p: payload)
    results, cost, status = analysis._sonar_verify("pplx-test-not-real",
                                                   "T", ["c"])
    assert len(results) == 8 and "8 results" in status
    assert cost == pytest.approx(1000 / 1e6 * discovery.SONAR_USD_PER_MTOK)


def test_report_status_flags_a_day_with_no_depth_stories(tmp_paths):
    con = _con(tmp_paths)
    try:
        seed_min(con)
        log = paths.DATA_DIR / "generation_log.jsonl"
        log.write_text(json.dumps({"date": DATE, "tiers": ["quick"]}) + "\n",
                       encoding="utf-8")
        report = analysis.run_analysis(date=DATE, con=con, env=dict(ENV_OK),
                                       chat=chat_sentinel, sonar=sonar_sentinel,
                                       fetch=fetch_fixture, sleep=lambda s: None)
        assert report["status"] == "no-depth-stories"
        assert report["per_story"] == []
    finally:
        con.close()


def test_missing_date_falls_back_to_newest_briefing_and_discloses_it(tmp_paths):
    """Asking for a date with no briefing analyzes the NEWEST edition
    instead; the report's date field carries the date actually analyzed
    (the disclosure). Pinned so the CLI story stays honest."""
    con = _con(tmp_paths)
    try:
        seed_min(con)
        report = analysis.run_analysis(date="2019-01-01", con=con,
                                       env=dict(ENV_OK),
                                       chat=lambda k, p: (s_brief(), 0.01),
                                       sonar=sonar_none, fetch=fetch_fixture,
                                       sleep=lambda s: None)
        assert report["date"] == DATE
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 12. CLI verb + diagnose section
# ---------------------------------------------------------------------------

def test_cli_analyze_keyless_is_exit_1_with_the_reason_on_stderr(tmp_paths, capsys):
    rc = cli.main(["analyze"])
    err = capsys.readouterr().err
    assert rc == 1 and "OPENAI_API_KEY" in err


def test_cli_analyze_prints_per_story_lines_and_the_derating_banner(
        tmp_paths, capsys, monkeypatch):
    canned = {"date": DATE, "model": "gpt-4o", "total_usd": 0.0424,
              "status": "ok", "derating": True,
              "per_story": [{"slot": 1, "tier": "full", "outcome": "ok",
                             "detail": "2 ledger entries, 3 cited sources",
                             "cost_usd": 0.0301, "fetch_ok": 3,
                             "fetch_attempted": 4, "sonar": "ok — 8 results"}],
              "warnings": ["derating: Sonar verification skipped under the cap"]}
    monkeypatch.setattr(analysis, "run_analysis",
                        lambda date=None: canned)
    rc = cli.main(["analyze"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"analysis — {DATE}" in out and "$0.0424" in out
    assert "slot 1 (full): ok" in out and "fetch 3/4" in out
    assert "derating: Sonar" in out
    assert "!! DERATING under the cap" in out  # escalation never absorbed


def test_diagnose_gives_the_analyst_its_own_section(tmp_paths):
    """ADR-0012 decision 4: analysis entries split OUT of the generation
    readout into THE ANALYST — outcome counts, the pre-registered week-1
    extraction rate, cost, and the derating flag line."""
    db.migrate()
    entry = {"ts": "2026-07-06T09:00:00+00:00", "stage": "analysis",
             "date": DATE, "status": "ok", "model": "gpt-4o",
             "total_usd": 0.0424, "derating": True, "warnings": [],
             "per_story": [
                 {"slot": 1, "tier": "full", "outcome": "ok", "detail": "",
                  "cost_usd": 0.03, "fetch_ok": 3, "fetch_attempted": 4,
                  "sonar": "ok — 8 results"},
                 {"slot": 2, "tier": "medium", "outcome": "rejected",
                  "detail": "fabricated citation", "cost_usd": 0.01,
                  "fetch_ok": 0, "fetch_attempted": 0, "sonar": "skipped"}]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")
    out = diagnose.run_diagnose(
        now_utc=datetime(2026, 7, 6, 12, tzinfo=timezone.utc))
    assert "THE ANALYST" in out
    assert "ok 1" in out and "rejected 1" in out
    assert "3/4 attempted fetches ok (75%)" in out
    assert "$0.0424" in out
    assert "derating fired in 1 run(s)" in out


# ---------------------------------------------------------------------------
# 13. M2 gate closing pass — the three residuals, the 0009 receipts, BUG15
# ---------------------------------------------------------------------------

def test_empty_ledger_draws_the_gate_warning_and_nonempty_draws_none():
    """Gate residual 1, both directions: a facts+mechanism-only brief
    validates WITH the instrumentation warning (zero attributed takes must
    be countable by diagnose); a brief with ledger entries draws none.
    Usefulness RULINGS stay with the week-1 read (Editor's lane) — this is
    the countable disclosure, not a gate."""
    b = qa_brief()
    b["ledger"] = None
    b["effects"] = None
    clean, warnings = validate(b)
    assert clean["ledger"] == []
    assert any("ledger empty — no attributed takes" in w for w in warnings)

    clean2, warnings2 = validate(qa_brief())
    assert len(clean2["ledger"]) == 1
    assert not any("ledger empty" in w for w in warnings2)


def test_fabricated_quote_in_notes_for_writer_hard_rejects():
    """Gate residual 2 (ordered pin): notes_for_writer flows into the
    writer's material — a fabricated quote there was the unchecked side
    door. Now checked before entering the clean dict."""
    b = qa_brief()
    b["notes_for_writer"] = ('end with "a fully invented closing line for '
                             'the reader" tomorrow.')
    with pytest.raises(analysis.BriefRejected, match="notes_for_writer"):
        validate(b)


def test_curly_fabricated_quote_in_notes_also_rejects():
    """BUG11 direction-safety carried onto the newly covered surface:
    curly-mark fabrications are detected in notes_for_writer too."""
    b = qa_brief()
    b["notes_for_writer"] = ("close with “an entirely fabricated "
                             "sign-off about the summit” please.")
    with pytest.raises(analysis.BriefRejected, match="notes_for_writer"):
        validate(b)


def test_glyph_normalized_verbatim_quote_in_unknowns_passes():
    """Gate residual 2 (ordered pin), green direction — and the BUG11
    false-reject guard on a new surface: a verbatim quote typed with a
    straight apostrophe against the corpus's curly one passes in an
    unknown's why_material."""
    b = qa_brief()
    b["unknowns"] = [{
        "question": "Which members resist the pledge",
        "why_material": ("because \"the alliance's drone plan advances "
                         "through committee\" only if the pledge holds"),
        "would_resolve": "the communique text"}]
    clean, _ = validate(b)
    assert len(clean["unknowns"]) == 1


def _fabricated_on(surface):
    b = qa_brief()
    fq = '"a sentence that appears nowhere in the retrieved material"'
    if surface == "unknown-question":
        b["unknowns"][0]["question"] = f"Is {fq} an accurate read"
    elif surface == "watch-observable":
        b["watch"][0]["observable"] = f"whether {fq} shows up in print"
    elif surface == "watch-settles":
        b["watch"][0]["settles"] = f"if {fq} was ever said"
    elif surface == "discrepancy-a-value":
        b["ledger"] = [{"discrepancy": True,
                        "a": {"value": f"said {fq} on air", "cites": ["S1"]},
                        "b": {"value": "Wednesday", "cites": ["C2"]}}]
    return b


@pytest.mark.parametrize("surface", ["unknown-question", "watch-observable",
                                     "watch-settles", "discrepancy-a-value"])
def test_fabricated_quotes_reject_on_every_newly_covered_surface(surface):
    """Gate residual 2, my exposure sweep: the artifact-wide promise means
    every prose surface that reaches a consumer refuses invented quotes —
    unknowns' fields, watch observable AND settles, discrepancy side
    values."""
    with pytest.raises(analysis.BriefRejected):
        validate(_fabricated_on(surface))


def _crowded_map():
    src = {}
    for i in range(1, 9):
        src[f"S{i}"] = {"kind": "cluster-full-text", "outlet": f"o{i}.com",
                        "title": f"t{i}", "url": f"https://o{i}.com/a",
                        "retrieved_at": "2026-07-06T00:00Z",
                        "text": f"Outlet {i} reports the summit agenda. "
                                + ("x" * 3000)}
    src["P1"] = {"kind": "prior-briefing", "outlet": "NewsLens (prior edition)",
                 "title": "briefing 2026-07-05", "url": "",
                 "retrieved_at": "2026-07-05",
                 "text": "Yesterday's edition covered pre-summit staging. "
                         + ("y" * 900)}
    return src


def test_arc_citing_prior_briefing_validates_and_renders_on_a_crowded_map():
    """Gate residual 3's exact pair: (a) on a many-source day the P-slice
    reservation keeps prior-briefing material in the model's view; (b) an
    arc citing P1 then validates — and survives the arc-integrity lint
    (item 10) precisely because the model could see and cite P."""
    src = _crowded_map()
    material = analysis.render_material(src)  # default budget
    assert "[P1]" in material and "pre-summit staging" in material
    assert "[S1]" in material  # full texts still lead

    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "Outlet one reports the summit agenda.", "cites": ["S1"]},
        {"fact": "Outlet two reports the summit agenda.", "cites": ["S2"]},
        {"fact": "Outlet three reports the summit agenda.", "cites": ["S3"]},
    ]
    b["ledger"] = [{"claim": "The agenda is multiply reported.",
                    "cites": ["S1", "S2"]}]
    b["mechanism"] = "Eight outlets converge on one agenda [S1]."
    b["effects"] = []
    b["arc"] = {"delta": "advances", "what_changed": "staging becomes the "
                "summit itself.", "cites": ["P1"]}
    clean, warnings = analysis.validate_brief(b, src, "full", corpus_of(src))
    assert clean["arc"] is not None and clean["arc"]["cites"] == ["P1"]
    assert not any("arc dropped" in w for w in warnings)


def test_zero_p_material_reserves_nothing():
    """Reservation edge: no P-keys — and equivalently a P-key with empty
    text — reserve zero; the S/R/C loop sees the full budget. Pinned by
    output equality between the two maps."""
    base = {
        "S1": {"outlet": "a.com", "title": "t1", "text": "A" * 4000},
        "S2": {"outlet": "b.com", "title": "t2", "text": "B" * 4000},
    }
    with_empty_p = dict(base)
    with_empty_p["P1"] = {"outlet": "NewsLens", "title": "prior", "text": "  "}
    out_no_p = analysis.render_material(base, budget_chars=3000)
    out_empty_p = analysis.render_material(with_empty_p, budget_chars=3000)
    assert out_no_p == out_empty_p
    assert "[P1]" not in out_empty_p and "[S1]" in out_no_p


def test_p_smaller_than_slice_deducts_only_actual_usage():
    """Reservation edge: tiny P against a big slice — only the rendered
    bytes are deducted, so S/R/C keep effectively the whole budget; and an
    oversized P is truncated to its slice, never allowed to crowd S out."""
    tiny = {
        "S1": {"outlet": "a.com", "title": "t", "text": "A" * 4000},
        "P1": {"outlet": "NewsLens", "title": "p", "text": "tiny prior"},
    }
    out = analysis.render_material(tiny, budget_chars=24_000)
    assert "tiny prior" in out          # rendered in full
    assert "A" * 4000 in out            # S untouched by the tiny reservation

    big_p = {
        "S1": {"outlet": "a.com", "title": "t", "text": "A" * 4000},
        "P1": {"outlet": "NewsLens", "title": "p", "text": "P" * 10_000},
    }
    out2 = analysis.render_material(big_p, budget_chars=6000)
    assert "[S1]" in out2 and "A" * 4000 in out2
    assert "[P1]" in out2
    assert "P" * 1500 not in out2       # truncated to ~the budget//6 slice
    assert len(out2) <= 6000 + 100


@pytest.mark.parametrize("case", ["single-source-no-p", "single-source-with-p",
                                  "second-of-two"])
def test_BUG15_no_nonp_source_starves_while_budget_remains(case):
    """KNOWN-RED (BUG15, found in the closing-pass reservation sweep).
    Contract: every non-empty non-P source renders at least its 1200-char
    floor share whenever the remaining budget allows (floor + entry
    header); the material block must NEVER be empty of article text while
    a fetched article exists.

    Today the S/R/C loop computes share = remainder // len(src_keys) with
    no room for the entry header, and BREAKs on first overflow with no
    first-entry admission (the P-loop HAS one):
      - single long S, no P  -> material is EMPTY ('' — verified by REPL);
      - single long S + priors -> ONLY P renders; the actual article is
        invisible. REGRESSION from residual 3 (the old shared division
        over all sources rendered S1 here; remainder//len(src_keys) does
        not) — the no-P case is inherited from pre-residual math;
      - two 20k sources at 24k budget -> S2 drops with ~12k budget unused
        (cross-outlet corroboration blind on exactly the days it matters).

    Why this is trust-critical, not cosmetic: the SCR material gate has
    already PASSED (S1 exists in the map), so the model receives an empty
    or P-only MATERIAL block alongside a populated source map — a
    fabricated brief citing [S1] with no quoted strings then passes every
    validator check (keys real, nothing to quote-check): fake receipts
    with code-supplied keys, the cardinal breach this milestone exists to
    prevent.

    Fix contract (any math satisfying the assertions): trim the chunk to
    the remaining budget minus the entry header instead of breaking (or
    mirror the P-loop's first-entry admission AND recompute share against
    remaining space). P-reservation semantics stay untouched — the
    residual-3 pins in this section must keep passing."""
    if case == "single-source-no-p":
        src = {"S1": {"outlet": "a.com", "title": "t", "text": "A" * 30_000}}
        out = analysis.render_material(src, budget_chars=24_000)
        assert "[S1]" in out and "A" * 1200 in out
    elif case == "single-source-with-p":
        src = {"S1": {"outlet": "a.com", "title": "t", "text": "A" * 30_000},
               "P1": {"outlet": "NewsLens", "title": "p1", "text": "B" * 4000},
               "P2": {"outlet": "NewsLens", "title": "p2", "text": "C" * 4000}}
        out = analysis.render_material(src, budget_chars=24_000)
        assert "[S1]" in out and "A" * 1200 in out  # the ARTICLE is visible
        assert "[P1]" in out                        # reservation still holds
    else:  # second-of-two
        src = {"S1": {"outlet": "a.com", "title": "t", "text": "A" * 20_000},
               "S2": {"outlet": "b.com", "title": "u", "text": "B" * 20_000}}
        out = analysis.render_material(src, budget_chars=24_000)
        assert "[S1]" in out
        assert "[S2]" in out and "B" * 1200 in out  # second outlet visible
        assert len(out) <= 24_000 + 100


# --- 0009 part 2: the retrieval receipts (fix-loop item 11, zero coverage
#     arrived with it — QA adds the floor) ---

def test_persist_brief_writes_the_retrieval_receipts_keyed_to_the_row(tmp_paths):
    con = _con(tmp_paths)
    try:
        src = qa_sources()
        brief_id = analysis.persist_brief(con, DATE, 1, "full", "valid",
                                          {"x": 1}, "", 0.01,
                                          {"manifest": {}}, sources=src)
        rows = con.execute(
            "SELECT key, kind, url, text FROM analysis_retrieval"
            " WHERE brief_id = ? ORDER BY key", (brief_id,)).fetchall()
        assert len(rows) == len(src)
        by_key = {r["key"]: r for r in rows}
        assert by_key["S1"]["kind"] == "cluster-full-text"
        assert by_key["S1"]["url"] == "https://thehill.com/a"
        assert "drone plan advances" in by_key["S1"]["text"]
    finally:
        con.close()


def test_rejected_briefs_keep_their_receipts_too(tmp_paths):
    """Forensics needs what the model was OFFERED, most of all on the
    rejected path — the fabrication is only provable against the receipts.
    Wiring pin: analyze_story passes sources on the reject persist."""
    con = _con(tmp_paths)
    try:
        seed_min(con)
        slot = json.loads(con.execute(
            "SELECT story_slots FROM briefings WHERE date=?",
            (DATE,)).fetchone()["story_slots"])[0]

        def fabricating_chat(key, prompt):
            b = s_brief()
            b["pinned_facts"] = [{"fact": "x", "cites": ["S99"]}]
            return b, 0.01

        sa = analysis.analyze_story(con, DATE, 1, slot,
                                    **story_kwargs(chat=fabricating_chat))
        assert sa.outcome == "rejected"
        row = con.execute("SELECT id FROM analysis_briefs WHERE date=?"
                          " AND slot=1 ORDER BY id DESC LIMIT 1",
                          (DATE,)).fetchone()
        keys = [r["key"] for r in con.execute(
            "SELECT key FROM analysis_retrieval WHERE brief_id = ?",
            (row["id"],)).fetchall()]
        assert "S1" in keys  # the offer the model fabricated against
    finally:
        con.close()


def test_analysis_retrieval_is_append_only(tmp_paths):
    con = _con(tmp_paths)
    try:
        brief_id = analysis.persist_brief(con, DATE, 1, "full", "valid",
                                          {"x": 1}, "", 0.0, {"manifest": {}},
                                          sources=qa_sources())
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with con:
                con.execute("UPDATE analysis_retrieval SET text='tampered'"
                            " WHERE brief_id=?", (brief_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with con:
                con.execute("DELETE FROM analysis_retrieval WHERE brief_id=?",
                            (brief_id,))
    finally:
        con.close()
