"""NL-118 P0-P2 — the writer-pipeline material fixes.

Every test here is BORN RED against 93e64c9 except where a docstring names it
a carried invariant. The case they all serve is the cockroach story, editions
2026-07-25 (brief 47) and 2026-07-26 (brief 50), whose corpora are frozen in
tests/fixtures/analysis/brief{47,50}_corpus.json (exported read-only from the
founder DB; `analysis_retrieval` carries no-update and no-delete triggers, so
those rows are the exact material the analyst was handed).

Zero model calls, zero network, zero metered anything: the analyst seam is
injected in every test that reaches it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from newslens import analysis, config, db

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "analysis"


def corpus(brief_id: int) -> dict:
    return json.loads((FIXTURES / f"brief{brief_id}_corpus.json").read_text(
        encoding="utf-8"))


def sources_of(doc: dict) -> dict:
    return {r["key"]: {k: r[k] for k in
                       ("kind", "outlet", "title", "url", "retrieved_at", "text")}
            for r in doc["rows"]}


def rendered_len(material: str, key: str) -> int:
    """Chars of a key's TEXT inside the assembled material block."""
    marker = f"--- [{key}] "
    start = material.find(marker)
    if start < 0:
        return 0
    body = material.index("\n", start) + 1
    nxt = material.find("\n\n--- [", body)
    return len(material[body:nxt if nxt >= 0 else len(material)])


# ---------------------------------------------------------------------------
# 8. The frozen fixtures themselves
# ---------------------------------------------------------------------------

def test_frozen_corpora_are_present_and_checksum_to_their_export():
    """CARRIED INVARIANT by construction (born green once the fixtures exist —
    they ARE the deliverable this pins). It guards the eval's foundation: a
    fixture edited later would silently move every measurement above it."""
    for brief_id, rows in ((47, 19), (50, 18)):
        doc = corpus(brief_id)
        assert len(doc["rows"]) == rows
        blob = json.dumps(doc["rows"], ensure_ascii=False,
                          sort_keys=True).encode("utf-8")
        assert hashlib.sha256(blob).hexdigest() == doc["rows_sha256"]
    fifty = corpus(50)
    # the properties that make brief 50 the pinned quality case
    assert sum(len(r["text"]) for r in fifty["rows"]) == 23473
    joined = " ".join(r["text"] for r in fifty["rows"]).lower()
    assert "at least 20 student deaths" in joined       # held human-stakes fact
    assert "20 july" in joined or "july 20" in joined   # held absolute date
    # the verified HOLE — the successor is not in this corpus, which is what
    # makes D11 a live fabrication trap rather than a spelling check
    for probe in ("succeed", "successor", "new education minister",
                  "named as", "sworn"):
        assert probe not in joined, probe


# ---------------------------------------------------------------------------
# 1. Water-fill allocator
# ---------------------------------------------------------------------------

def test_water_fill_is_max_min_fair_and_spends_the_whole_budget():
    """The allocator's contract in one line: short demands are satisfied in
    full and their surplus RE-FLOWS to the long ones."""
    assert analysis._water_fill([10, 10, 10], 3000) == [10, 10, 10]
    assert sum(analysis._water_fill([5000, 5000, 5000], 3000)) == 3000
    got = analysis._water_fill([50, 5000, 5000], 3000)
    assert got[0] == 50 and got[1] == got[2] == 1475   # surplus re-flowed
    assert analysis._water_fill([], 100) == []
    assert analysis._water_fill([100], 0) == [0]


def test_a_stub_no_longer_holds_a_full_share_of_the_material_budget():
    """BORN RED. The shipped allocator gave every source share =
    max(1200, remainder // n): a 303-char stub reserved ~1,371 chars and spent
    303 of them, and the long article was cut at that same 1,371 with the
    surplus evaporating."""
    src = {"S1": {"kind": "cluster-full-text", "outlet": "bbc.com",
                  "title": "long", "url": "https://bbc.com/a",
                  "retrieved_at": "", "text": "A" * 9000},
           "R1": {"kind": "retrieved", "outlet": "stub.com", "title": "stub",
                  "url": "https://stub.com/a", "retrieved_at": "",
                  "text": "B" * 303}}
    out = analysis.render_material(src, budget_chars=6000)
    assert rendered_len(out, "R1") == 303          # took what it needed
    assert rendered_len(out, "S1") > 5000          # and gave back the rest
    assert len(out) <= 6000


def test_brief50_regression_the_three_facts_the_model_never_received():
    """BORN RED, on the real frozen corpus. Each probe is a fact the case file
    proved was HELD and never rendered: the death toll (PBS char 1,537), the
    CJP's compensation commitment (BBC char ~5,937), both past the 1,371-char
    cut the shipped allocator made."""
    src = sources_of(corpus(50))
    out = analysis.render_material(src, budget_chars=24_000)
    assert rendered_len(out, "S1") == 6622        # BBC, whole
    assert rendered_len(out, "S2") == 5721        # PBS, whole
    assert rendered_len(out, "P2") > 0            # no longer dropped outright
    for probe in ("at least 20 student deaths", "continue to fight",
                  "compensation"):
        assert probe in out, probe
    assert len(out) <= 24_000


def test_brief47_regression_the_budget_is_actually_spent():
    """Five full texts, 39,636 held chars: the shipped allocator rendered
    11,290 and left 10,789 budget chars unspent."""
    src = sources_of(corpus(47))
    out = analysis.render_material(src, budget_chars=24_000)
    assert len(out) > 23_000 and len(out) <= 24_000
    for key in ("S1", "S2", "S3", "S4", "S5"):
        assert rendered_len(out, key) > 3000, key


def test_water_fill_keeps_the_BUG15_floors_and_the_P_reservation():
    """CARRIED INVARIANT (born green, and it must stay green): the material
    block is never empty of article text while a fetched article exists, and
    the prior-briefing slice is still reserved before the S/R/C spend."""
    single = {"S1": {"kind": "cluster-full-text", "outlet": "a.com",
                     "title": "t", "url": "", "retrieved_at": "",
                     "text": "A" * 30_000}}
    out = analysis.render_material(single, budget_chars=24_000)
    assert "[S1]" in out and "A" * 1200 in out
    with_p = dict(single)
    with_p["P1"] = {"kind": "prior-briefing", "outlet": "NewsLens",
                    "title": "p1", "url": "", "retrieved_at": "2026-07-25",
                    "text": "B" * 4000}
    out2 = analysis.render_material(with_p, budget_chars=24_000)
    assert "[S1]" in out2 and "A" * 1200 in out2 and "[P1]" in out2


def test_a_truncated_source_is_dropped_not_shredded():
    """MATERIAL_MIN_SHARE: 60 long sources against a small budget must not
    produce sixty slivers — the tail is dropped, the head is readable."""
    src = {f"S{i}": {"kind": "cluster-full-text", "outlet": f"o{i}.com",
                     "title": f"t{i}", "url": "", "retrieved_at": "",
                     "text": "x" * 5000} for i in range(1, 61)}
    out = analysis.render_material(src, budget_chars=24_000)
    lens = [rendered_len(out, f"S{i}") for i in range(1, 61)]
    assert min(x for x in lens if x) >= analysis.MATERIAL_MIN_SHARE
    assert "[S1]" in out                      # priority order kept the head


# ---------------------------------------------------------------------------
# 2. Dateline annotation (never a corpus rewrite)
# ---------------------------------------------------------------------------

def test_material_header_carries_the_publication_date():
    """BORN RED. `source_items.published_at` stopped at the DB: the model has
    never seen when anything was published."""
    src = {"S1": {"kind": "cluster-full-text", "outlet": "BBC News",
                  "title": "Protesters march", "url": "https://bbc.com/a",
                  "retrieved_at": "2026-07-26T09:00Z",
                  "published_at": "2026-07-20T11:03:00Z",
                  "text": "Police used force on 20 July."}}
    out = analysis.render_material(src)
    assert "--- [S1] BBC News — Protesters march (published 2026-07-20) ---" in out


def test_retrieved_at_is_never_passed_off_as_a_publication_date():
    """The dateline must degrade to ABSENT, never to today. `retrieved_at` is
    when WE fetched, which is today for every article key — stamping it as
    "published" would be a fabricated dateline wearing our own header."""
    src = {"S1": {"kind": "cluster-full-text", "outlet": "BBC News",
                  "title": "t", "url": "", "retrieved_at": "2026-07-26T09:00Z",
                  "text": "body"}}
    assert "published" not in analysis.render_material(src)
    # the one legitimate exception: our own prior edition
    prior = {"P1": {"kind": "prior-briefing", "outlet": "NewsLens",
                    "title": "briefing 2026-07-25", "url": "",
                    "retrieved_at": "2026-07-25", "text": "body"}}
    assert "(published 2026-07-25)" in analysis.render_material(prior)


def test_the_corpus_text_itself_is_never_rewritten():
    """CARRIED INVARIANT (born green at 93e64c9, and the reason this batch
    annotates instead of rewriting). Rook's rail, mechanically: `check_quotes`
    validates model quotes as
    verbatim substrings of the corpus WE assemble. If the dateline were
    injected into the TEXT rather than the header, the model could quote our
    own edit and pass the fabrication check."""
    text = "The crackdown happened Monday, police said."
    src = {"S1": {"kind": "cluster-full-text", "outlet": "BBC", "title": "t",
                  "url": "", "retrieved_at": "", "published_at": "2026-07-20",
                  "text": text}}
    out = analysis.render_material(src)
    body = out.split("---\n", 1)[1]
    assert body == text                       # byte-identical, no annotation
    assert "Monday, July 20" not in out


def test_build_source_map_joins_published_at_onto_the_fetched_article():
    rec = analysis.FetchRecord(url="https://bbc.com/a", source_name="BBC News",
                               tier="full", outcome=analysis.OK, chars=10,
                               title="Protest", text="body text")
    items = [{"outlet": "BBC News", "url": "https://bbc.com/a",
              "title": "Protest", "raw_excerpt": "x",
              "fetched_at": "2026-07-26T09:00Z",
              "published_at": "2026-07-20T11:00:00Z"}]
    src = analysis.build_source_map([rec], items, [], [])
    assert src["S1"]["published_at"] == "2026-07-20T11:00:00Z"
    assert "(published 2026-07-20)" in analysis.render_material(src)


# ---------------------------------------------------------------------------
# 3. X4 — the prior-briefing renderer
# ---------------------------------------------------------------------------

def test_X4_brief50s_prior_record_reaches_the_model_with_its_facts():
    """BORN RED, the X4 receipt. `narrative_text[:4000]` cut the 16,582-char
    2026-07-25 edition at its FIRST story, so the cockroach thread's slot-2
    record never travelled: compensat@5,140, Wangchuk@5,683, reform@7,254,
    "dropping police cases"@5,095 — all four past the cut, and instr()=0 in
    all four persisted P rows across briefs 47 and 50."""
    doc = corpus(50)
    slot = {"story_title": doc["story"]["title"],
            "summary": doc["story"]["summary"],
            "matched_memory": doc["story"]["matched_memory"]}
    before = (doc["prior_narratives"][0]["text"] or "")[:4000]
    for probe in ("compensat", "Wangchuk", "reform", "dropping police cases"):
        assert probe.lower() not in before.lower(), f"{probe} was never lost"

    after = analysis.prior_material_for_story(doc["prior_narratives"], slot)
    p1 = after[0]
    assert p1["matched"] is True
    assert p1["text"].startswith("**India's Gen Z 'cockroach' movement")
    for probe in ("compensat", "Wangchuk", "reform", "dropping police cases"):
        assert probe.lower() in p1["text"].lower(), probe


def test_X4_an_edition_that_did_not_carry_the_story_says_so():
    """The 2026-07-24 and 2026-07-23 editions genuinely have no cockroach
    section (verified: 'cockroach' appears nowhere in either narrative). The
    honest degrade is the edition INDEX plus a disclosure on the key's own
    title — not 4,000 characters about oil prices standing in as this
    thread's record."""
    doc = corpus(47)
    slot = {"story_title": doc["story"]["title"], "summary": "",
            "matched_memory": []}
    after = analysis.prior_material_for_story(doc["prior_narratives"], slot)
    assert [p["matched"] for p in after] == [False, False]
    assert all(len(p["text"]) <= 800 for p in after)
    src = analysis.build_source_map([], [], [], after)
    assert "NO SECTION OF THAT EDITION NAMES THIS STORY" in src["P1"]["title"]
    assert "NO SECTION OF THAT EDITION NAMES THIS STORY" in \
        analysis.render_material(src)


def test_the_X4_degrade_directive_never_reaches_a_reader_renderable_field():
    """BORN RED (NL-118 QA finding 2 — containment, now OWNED).

    QA proved the all-caps directive persists: `source_table` copied the P
    key's title verbatim, so citing an unmatched prior-briefing key put
    "NO SECTION OF THAT EDITION NAMES THIS STORY" into `brief["sources"][]
    .title`, which is a reader-renderable field. It stayed off screens only
    because server.py's NL-58 branch rewrites prior-briefing titles wholesale
    for its own unrelated reason — a cross-file accident this batch neither
    owns nor tests, and one that any new exporter would not replicate.

    The pin is a SWEEP, not a spot-check: the directive must appear nowhere in
    the persisted brief JSON at all. Where the fact is still needed it is
    carried in plain prose under `record_status`, and the writer channel —
    the one §5.3 says gets degradation directives — re-attaches the loud form.
    """
    doc = corpus(47)
    slot = {"story_title": doc["story"]["title"], "summary": "",
            "matched_memory": []}
    priors = analysis.prior_material_for_story(doc["prior_narratives"], slot)
    assert [p["matched"] for p in priors] == [False, False]
    src = analysis.build_source_map([], [], [], priors)
    # the PROMPT side is unchanged: the analyst is still told, loudly
    assert analysis.DEGRADE_NO_SECTION in src["P1"]["title"]

    b = _brief([{"observable": "Whether the party registers", "cites": ["P1"]}])
    b["pinned_facts"] = [{"fact": "The prior edition is on file.",
                          "cites": ["P1"]}]
    b["mechanism"] = "Actors trade costs [P1]."
    clean, _ = analysis.validate_brief(b, src, "medium",
                                       " ".join(s["text"] for s in src.values()))
    assert [s["key"] for s in clean["sources"]] == ["P1"], "P1 must be cited"

    blob = json.dumps(clean, ensure_ascii=False)
    assert analysis.DEGRADE_NO_SECTION not in blob, (
        "the model-input directive reached the persisted brief: "
        + blob[:400])
    row = clean["sources"][0]
    assert row["title"] == "briefing 2026-07-24"
    assert row["record_status"] == analysis.DEGRADE_RECORD_STATUS
    assert row["record_status"].islower() or not row["record_status"].isupper()

    # and the writer's own channel still carries the directive
    assert analysis.DEGRADE_NO_SECTION in analysis.render_writer_view(clean)


def test_an_undegraded_source_row_gains_no_new_field():
    """CARRIED INVARIANT. `record_status` is additive and conditional: an
    ordinary row must keep exactly the six keys every persisted brief in the
    DB already has, or the containment fix becomes a schema change."""
    src = _map_for()
    clean, _ = analysis.validate_brief(
        _brief([{"observable": "Whether the party registers", "cites": ["R1"]}]),
        src, "medium", "Some retrieved body text.")
    for row in clean["sources"]:
        assert set(row) == {"key", "outlet", "title", "url", "retrieved_at",
                            "kind"}, row


def test_X4_section_matching_needs_a_headline_hit_not_a_stray_word():
    """The false-positive rail: 'forces' inside an army story must not make
    that story this thread's prior record."""
    narrative = ("# NewsLens — Thursday\n\nIn today's briefing:\n- a\n\n---\n"
                 "**Zelensky ousts army commander in a widening shake-up**\n\n"
                 "Ukrainian forces regrouped after the education of new "
                 "recruits stalled.\n")
    slot = {"story_title": "India's Gen Z Movement Forces Education "
                           "Minister's Resignation"}
    out = analysis.prior_material_for_story([{"date": "2026-07-23",
                                              "text": narrative}], slot)
    assert out[0]["matched"] is False


def test_prior_briefing_material_no_longer_truncates_at_the_read(migrated_con):
    """The stage-level read hands back WHOLE editions now; the cut to a
    story happens per-slot, where the story is known."""
    con = migrated_con
    with con:
        con.execute("INSERT INTO briefings (date, story_slots, narrative_text)"
                    " VALUES ('2026-07-25', '[]', ?)", ("z" * 9000,))
    got = analysis._prior_briefing_material(con, "2026-07-26")
    assert len(got[0]["text"]) == 9000


# ---------------------------------------------------------------------------
# 4. Outlet counts at prompt-build
# ---------------------------------------------------------------------------

def test_source_map_carries_outlet_multiplicity_and_a_distinct_total():
    """BORN RED. The '(1 outlet)' label existed only at RENDER time
    (server.py:_facts_outlet_count), computed from the finished brief — so the
    model that wrote the claims never saw it."""
    src = sources_of(corpus(50))
    rendered = analysis.render_source_map(src)
    assert "DISTINCT OUTLETS IN THIS MAP:" in rendered
    # NPR reaches this map twice: as a feed excerpt (C4) and as a Sonar result
    # (R4, host npr.org). One newsroom, one outlet — counting it twice would
    # inflate the corroboration the model is about to modulate against.
    assert analysis._outlet_id(src["C4"]) == analysis._outlet_id(src["R4"])
    assert "SAME OUTLET as" in rendered
    idx = analysis.outlet_index(src)
    assert sorted(idx["npr.org"]) == ["C4", "R4"]
    assert f"DISTINCT OUTLETS IN THIS MAP: {len(idx)}." in rendered


def test_outlet_annotation_rides_build_source_map():
    rec = analysis.FetchRecord(url="https://npr.org/a", source_name="NPR",
                               tier="full", outcome=analysis.OK, chars=4,
                               title="t", text="body")
    src = analysis.build_source_map(
        [rec], [], [{"url": "https://npr.org/b", "title": "u",
                     "snippet": "snip"}], [])
    assert src["S1"]["outlet_id"] == "npr.org"
    assert src["S1"]["outlet_keys"] == ["S1", "R1"]


# ---------------------------------------------------------------------------
# 5. The `watch` citation slot
# ---------------------------------------------------------------------------

def _map_for(keys=("S1", "R1")):
    return {k: {"kind": "cluster-full-text", "outlet": f"{k}.com",
                "title": k, "url": f"https://{k}.com/a",
                "retrieved_at": "", "text": "Some retrieved body text."}
            for k in keys}


def _brief(watch):
    return {"pinned_facts": [{"fact": "A thing happened today.",
                              "cites": ["S1"]}],
            "ledger": [], "mechanism": "Actors trade costs [S1].",
            "effects": [], "arc": None,
            "unknowns": [{"question": "Who signs the order",
                          "why_material": "the signature starts the clock",
                          "would_resolve": "the gazette notification"}],
            "watch": watch, "notes_for_writer": ""}


def test_an_uncited_watch_item_is_dropped_and_disclosed():
    """BORN RED (EC-1). 102/102 watch items across the whole DB were uncited
    because the field had no citation slot at all."""
    src = _map_for()
    clean, warnings = analysis.validate_brief(
        _brief([{"observable": "Whether the party registers", "settles": "x"},
                {"observable": "Any filing by organisers", "settles": "y",
                 "cites": ["R1"], "basis": "mechanical"}]),
        src, "medium", "Some retrieved body text.")
    assert [w["observable"] for w in clean["watch"]] == ["Any filing by organisers"]
    assert clean["watch"][0]["cites"] == ["R1"]
    assert any("watch citation enforcement" in w for w in warnings)


def test_a_watch_cite_must_be_in_the_manifest_and_reaches_the_source_table():
    src = _map_for()
    with pytest.raises(analysis.BriefRejected):
        analysis.validate_brief(
            _brief([{"observable": "x", "cites": ["S9"]}]), src, "medium",
            "Some retrieved body text.")
    clean, _ = analysis.validate_brief(
        _brief([{"observable": "Whether the party registers", "cites": ["R1"]}]),
        src, "medium", "Some retrieved body text.")
    assert "R1" in [s["key"] for s in clean["sources"]]


def test_the_shipped_brief50_output_FAILS_the_watch_rule():
    """BORN RED on real output, which is the whole point of the rule: every
    watch item the pipeline actually shipped on 2026-07-26 is uncited."""
    shipped = corpus(50)["original_brief"]
    assert shipped["watch"], "fixture lost its watch array"
    assert all(not w.get("cites") for w in shipped["watch"])
    src = sources_of(corpus(50))
    clean, warnings = analysis.validate_brief(
        _brief(shipped["watch"]), src, "medium",
        " ".join(s["text"] for s in src.values()))
    assert clean["watch"] == []
    assert any("dropped 3 uncited watch item" in w for w in warnings)


# ---------------------------------------------------------------------------
# 5a. The single-quote hole in the verbatim check (found in the fix leg)
# ---------------------------------------------------------------------------

# Verbatim from the re-run this batch's acceptance number is scored on. The
# quotation is REAL — it is Al Jazeera's own headline on key C5 — but it is
# not in any source's BODY text, so the body-only corpus called it invented
# while the single-quote blind spot meant nothing ever asked.
HEADLINE_QUOTE_FACT = (
    "Parents of students who died by suicide after the leak say the "
    "appointment 'can't bring my dead daughter back,' per Al Jazeera.")
AL_JAZEERA_HEADLINE = "New education minister can't bring my dead daughter back"


def test_single_quoted_material_is_checked_and_now_REJECTED_when_unmarked():
    """CONSCIOUSLY FLIPPED (Spec-1 promotion, content round 2026-07-31, HIGH
    stakes; landed in NL-118 content-leg batch B).

    WAS: `test_single_quoted_material_is_checked_and_disclosed_not_silently_
    passed`, which pinned DISCLOSE-not-reject and asserted the brief survived.

    That pin was always explicitly provisional — its own docstring said
    "whether a verbatim rule admits brackets and elisions is the content
    round's call", and the round has now made it. The scanner's original find
    stands unchanged and is still asserted here: `_QUOTE_RE` matched only the
    double-quote family, so the cardinal promise was never enforced on the
    quotations the model actually writes (7 spans seen where the scanner sees
    92, replayed read-only over the founder DB).

    What changed is the PENALTY, and only because the canon now separates the
    two populations the old policy could not tell apart. Hard-rejecting all 92
    would have destroyed 5 of 43 real briefs — but every one of those five
    carries a standard editorial MARK (`maintain[s]`, `but... advancing`), and
    marked spans are now lawful. What is left is unmarked wording alteration,
    which is the thing the promise was about. Founder-DB replay under the
    promoted policy: 0 briefs destroyed.
    """
    doc = corpus(50)
    src = sources_of(doc)
    body = analysis.verbatim_corpus(src)
    invented = ("Officials say the review is 'finished before the monsoon "
                "session ends' per the ministry.")
    assert "monsoon session ends" not in body

    b = _brief([{"observable": "Whether the party registers", "cites": ["S1"]}])
    b["pinned_facts"] = [{"fact": invented, "cites": ["S1"]}]
    with pytest.raises(analysis.BriefQuoteUnmarked) as exc:
        analysis.validate_brief(b, src, "medium", body,
                                briefing_date=doc["date"])
    # the span is still SEEN by the single-quote scanner — the find that
    # produced this test is what makes the rejection possible at all
    assert "finished before the monsoon" in str(exc.value)
    assert "pinned fact 1" in str(exc.value)
    assert isinstance(exc.value, analysis.BriefRejected)


def test_the_double_quoted_hard_reject_is_untouched_by_the_disclosure_split():
    """CARRIED INVARIANT. The family that has always been fatal stays fatal —
    the single-quote work must not soften the check that already worked.
    Label note (gate rider R4): the INVARIANT carries — proven at HEAD via
    the old check_quotes API — but this file is structurally red at HEAD (it
    calls verbatim_corpus, which HEAD lacks); the proof-class is behavioral
    carry, not file transplant."""
    doc = corpus(50)
    src = sources_of(doc)
    body = analysis.verbatim_corpus(src)
    b = _brief([{"observable": "Whether the party registers", "cites": ["S1"]}])
    b["pinned_facts"] = [{"fact": 'Officials say the review is "finished '
                                  'before the monsoon session ends" per them.',
                          "cites": ["S1"]}]
    with pytest.raises(analysis.BriefRejected) as exc:
        analysis.validate_brief(b, src, "medium", body,
                                briefing_date=doc["date"])
    assert "not a verbatim substring" in str(exc.value)


def test_a_quote_taken_from_a_source_HEADLINE_is_not_called_a_fabrication():
    """BORN RED, and the reason the single-quote fix cannot ship alone.

    `render_material` puts each source's headline in its material header, so
    the model is SHOWN titles and quotes from them. Al Jazeera's C5 headline
    is itself a quotation and brief 50 used it, cited correctly. The corpus
    `check_quotes` ran against was body text only — so the moment the
    single-quote hole closes, that legitimate quotation becomes a rejected
    brief. `verbatim_corpus` is the other half of the fix: the material a
    quote may be checked against is everything we retrieved AND showed.
    """
    doc = corpus(50)
    src = sources_of(doc)
    bodies = " ".join(s["text"] for s in src.values())
    assert "daughter" not in bodies.lower(), "not a body-text quote — that is the point"
    assert any(AL_JAZEERA_HEADLINE in (s["title"] or "").replace("’", "'")
               for s in src.values()), "fixture lost the C5 headline"

    b = _brief([{"observable": "Whether the party registers", "cites": ["S1"]}])
    b["pinned_facts"] = [{"fact": HEADLINE_QUOTE_FACT, "cites": ["C5"]}]
    clean, _ = analysis.validate_brief(
        b, src, "medium", analysis.verbatim_corpus(src),
        briefing_date=doc["date"])
    assert clean["pinned_facts"], "a real headline quotation was rejected"


def test_our_own_prior_edition_titles_stay_out_of_the_verbatim_corpus():
    """Rook's rail, held while widening. Outlet headlines are the outlet's
    words; a prior-briefing key's title is OURS, and letting the model quote
    NewsLens's own machine text back as 'retrieved material' is precisely the
    self-quotation the rail forbids — the X4 degrade directive most of all."""
    src = {"S1": {"kind": "cluster-full-text", "outlet": "bbc.com",
                  "title": "Minister quits over exam leak", "url": "u",
                  "retrieved_at": "", "text": "Body text here."},
           "P1": {"kind": "prior-briefing", "outlet": "NewsLens (prior edition)",
                  "title": "briefing 2026-07-24" + analysis.DEGRADE_TITLE_SUFFIX,
                  "url": "", "retrieved_at": "2026-07-24",
                  "text": "Prior edition body."}}
    body = analysis.verbatim_corpus(src)
    assert "Minister quits over exam leak" in body        # outlet title: in
    assert "briefing 2026-07-24" not in body              # our title: out
    assert analysis.DEGRADE_NO_SECTION not in body
    assert "Prior edition body." in body                  # its TEXT still counts


def test_the_shipped_brief50_carried_quotes_that_nothing_checked():
    """BORN RED on real shipped output. The 2026-07-26 brief quotes four
    times; the old extractor saw zero of them. Faithful or not was never the
    question the validator got to ask."""
    doc = corpus(50)
    b = doc["original_brief"]
    prose = " ".join([p["fact"] for p in b["pinned_facts"]]
                     + [e.get("claim", "") or "" for e in b.get("ledger") or []]
                     + [e.get("effect", "") or ""
                        for e in b.get("effects") or []])
    spans = analysis._quoted_spans(prose, analysis.QUOTE_MIN_CHARS)
    assert len(spans) >= 3, spans
    assert any("dead daughter back" in q for q in spans), spans
    assert analysis._QUOTE_RE.findall(prose) == [], \
        "the old regex is supposed to be blind here — that was the defect"


@pytest.mark.parametrize("text,spans", [
    ("Parents say it 'can't bring my dead daughter back,' per AJ.",
     ["can't bring my dead daughter back,"]),
    ('She said "the report is finished" on Friday.',
     ["the report is finished"]),
    ("He called them ‘anti-national forces’ in the speech.",
     ["anti-national forces"]),
    ("The parents' group met the students' union about Modi's plan.", []),
    ("No quotation marks appear in this sentence at all.", []),
])
def test_quoted_spans_reads_both_families_without_tripping_on_apostrophes(
        text, spans):
    """The scanner's contract, spelled out. The possessive row is the one that
    makes a naive single-quote regex unusable, and the contraction row is the
    one that makes it WRONG rather than merely noisy."""
    assert analysis._quoted_spans(text) == spans


# ---------------------------------------------------------------------------
# 5b. The quote check's boundary punctuation (NL-118 QA finding 3)
# ---------------------------------------------------------------------------

# The exact fragment the 2026-07-30 redraft probe lost a whole brief on, and
# the exact PBS (S2) sentence it came from — both verbatim.
REDRAFT_LOST_QUOTE = "damaged permanently,"
PBS_SOURCE_LINE = ("the aura surrounding Mr. Modi has been damaged "
                   "permanently.")


def _quoting_brief(quote: str) -> dict:
    b = _brief([{"observable": "Whether the party registers", "cites": ["S1"]}])
    b["effects"] = [{"effect": f'A PBS guest holds the aura around Modi is '
                               f'"{quote}" after the resignation.',
                     "basis": "attributed", "holder": "a PBS guest",
                     "cites": ["S2"]}]
    return b


def test_a_real_quote_is_not_rejected_for_the_comma_style_pulls_inside_it():
    """BORN RED (NL-118 QA finding 3). One brief in eight was lost — not to an
    invented quote, but to American comma placement.

    PBS (S2) carries "...the aura surrounding Mr. Modi has been damaged
    permanently." The ceiling redraft re-quoted it as the shorter fragment
    "damaged permanently," — comma inside the closing mark, standard style —
    and `check_quotes` rejected the ENTIRE brief via BriefRejected because it
    normalised whitespace, case and curly glyphs (BUG11) but not boundary
    punctuation. Nothing was fabricated: the interior text is verbatim S2.

    The repair is candidate-side only, so it is direction-safe by BUG11's own
    argument, and it discloses itself in warnings.
    """
    doc = corpus(50)
    src = sources_of(doc)
    body = " ".join(s["text"] for s in src.values())
    assert PBS_SOURCE_LINE in body, "fixture lost the S2 sentence under test"
    assert REDRAFT_LOST_QUOTE not in body, \
        "the fragment must NOT be a plain substring, or there is nothing to fix"

    clean, warnings = analysis.validate_brief(
        _quoting_brief(REDRAFT_LOST_QUOTE), src, "medium", body,
        briefing_date=doc["date"])
    assert clean["effects"], "the effect carrying the real quote was lost"
    assert any("boundary punctuation" in w for w in warnings), warnings


@pytest.mark.parametrize("quote,kept", [
    ("damaged permanently,", True),      # comma inside — the observed loss
    ("damaged permanently.", True),      # period inside — same class
    ("damaged permanently…", True),      # ellipsis truncation
    ("damaged permanently", True),       # clean, matched before the fix too
    ("damaged eternally,", False),       # interior word invented -> still dead
    ("damaged, permanently", False),     # INTERIOR comma is a real edit
    ("aura surrounding Mr Modi has", False),   # interior "Mr." -> "Mr", refused
])
def test_the_boundary_tolerance_cannot_launder_an_interior_edit(quote, kept):
    """The direction-safety claim, made falsifiable. Trimming the candidate's
    EDGES can only remove false rejections; anything altered INSIDE the quote
    — a swapped word, a comma moved into the middle, a dropped honorific full
    stop — must still cost the brief. The `Mr.`->`Mr` row is QA's own case,
    flagged as observed and deliberately NOT normalised."""
    doc = corpus(50)
    src = sources_of(doc)
    body = " ".join(s["text"] for s in src.values())
    if kept:
        clean, _ = analysis.validate_brief(
            _quoting_brief(quote), src, "medium", body,
            briefing_date=doc["date"])
        assert clean["effects"]
    else:
        with pytest.raises(analysis.BriefRejected) as exc:
            analysis.validate_brief(_quoting_brief(quote), src, "medium", body,
                                    briefing_date=doc["date"])
        assert "not a verbatim substring" in str(exc.value)


# ---------------------------------------------------------------------------
# 6. The 542-word budget made real  (WAS: "the 400-word ceiling", then "the
#    450-word ceiling" — re-based by Spec-4(c) step 0, batch B, when `arc`
#    entered the counted number, and re-based again by the NL-118 ratification
#    of 2026-08-01, clause (i), which moved medium 450 -> 542 as throughput.
#    Ceiling 650 and band 731 DERIVE from it; neither factor has ever moved.)
#
#    EVERY multiplier in this section is calibrated to the ceiling and the
#    band, so all of them moved with the numbers. Left alone they would have
#    gone quiet rather than red: 80 reps = 580 words, which used to trip the
#    540 ceiling and now sits comfortably under 650.
# ---------------------------------------------------------------------------

def test_over_budget_warns_and_past_the_ceiling_raises():
    """RE-ANCHORED, not weakened (batch B item 1; again at the NL-118
    ratification): same brief, same raise, the re-based numbers.
    WAS: `budget == 400 and ceiling == 480`, then `450`/`540` at x80."""
    src = _map_for()
    b = _brief([{"observable": "Whether the party registers", "cites": ["S1"]}])
    # 20 baseline + 7/rep => 699 words, past the 650 ceiling. WAS: x80 = 580.
    b["mechanism"] = "Each ministry answers to its own committee. " * 97
    with pytest.raises(analysis.BriefOverCeiling) as over:
        analysis.validate_brief(b, src, "medium", "Some retrieved body text.")
    assert over.value.budget == 542 and over.value.ceiling == 650
    assert isinstance(over.value, analysis.BriefRejected)


def test_analyze_story_redrafts_once_and_ships_the_shorter_draft(tmp_paths):
    """BORN RED (EC-9's teeth). The ceiling used to warn, and the warning was
    not even persisted — brief 50 ran 636 words against 400 and shipped."""
    con = _seed(tmp_paths)
    calls = []

    def chat(key, prompt):
        calls.append(prompt)
        b = _brief([{"observable": "Whether the party registers",
                     "cites": ["S1"]}])
        if len(calls) == 1:
            # RE-BASED (batch B item 1): x70 = 510 words, which SHIPS under the
            # step-0 ceiling of 540. x80 = 580 still trips it. WAS: 70.
            # RE-BASED AGAIN (NL-118 clause (i)): x80 = 580 now SHIPS under the
            # 650 ceiling, so the first draft would never have been over and no
            # redraft would have been bought — the test would have failed on
            # the call count rather than proving anything. x97 = 699 trips 650.
            b["mechanism"] = "Each ministry answers to its own committee. " * 97
        return b, 0.0, 0.0

    sa = analysis.analyze_story(con, "2026-07-26", 1, _slot(), "medium",
                                config.SourcesConfig(), "", "", 10.0, [], [],
                                fetch=_fetch, chat=chat,
                                sonar=lambda *a: ([], 0.0, "ok — 0 results"),
                                sleep=lambda s: None)
    assert sa.outcome == "ok"
    assert len(calls) == 2                       # exactly one redraft
    assert "REDRAFT — YOUR PREVIOUS ATTEMPT WAS OVER THE CEILING." in calls[1]
    assert "do not remove specifics" in calls[1].lower()
    assert any("length redraft applied" in w for w in sa.warnings)


def _second_over_run(tmp_paths, reps):
    con = _seed(tmp_paths)
    calls = []

    def chat(key, prompt):
        calls.append(prompt)
        b = _brief([{"observable": "Whether it registers", "cites": ["S1"]}])
        b["mechanism"] = "Each ministry answers to its own committee. " * reps
        return b, 0.0, 0.0

    sa = analysis.analyze_story(con, "2026-07-26", 1, _slot(), "medium",
                                config.SourcesConfig(), "", "", 10.0, [], [],
                                fetch=_fetch, chat=chat,
                                sonar=lambda *a: ([], 0.0, "ok — 0 results"),
                                sleep=lambda s: None)
    return sa, calls


def test_a_second_over_run_INSIDE_the_band_ships_disclosed_not_a_third_call(
        tmp_paths):
    """CONSCIOUSLY FLIPPED (Spec-4(c) step 2, batch B item 5).

    WAS: `test_a_second_over_run_is_a_disclosed_rejection_not_a_third_call`,
    which asserted `outcome == "rejected"` for every second over-run.

    Step 2 is the surviving loss mode, and it exists because the redraft does
    not reliably shrink: measured on batch B's own iteration-1 runs, draft 603
    -> redraft 725 and 622 -> 646. The old path threw away the shorter draft it
    already had and lost the story. Now the SHORTER draft ships when it is
    within budget x 1.35, WITH a disclosure.

    The property that mattered in the old test is unchanged and still asserted
    here: exactly TWO calls. Bounded-by-construction was never the part Step 2
    touched."""
    # RE-BASED (NL-118 clause (i)): x97 = 699 words, over the 650 ceiling and
    # inside the 731 band — the same position in the band that x80's 580 held
    # against the old 540/607 pair. WAS: x80.
    sa, calls = _second_over_run(tmp_paths, 97)          # 699 words, band 731
    assert sa.outcome == "ok"
    assert len(calls) == 2, "step 2 must not buy a third call"
    band_notes = [w for w in sa.warnings if "DISCLOSURE BAND" in w]
    assert len(band_notes) == 1, sa.warnings
    assert "shipped WITH this disclosure" in band_notes[0]


def test_step2_keeps_the_FIRST_draft_when_the_redraft_comes_back_LONGER(
        tmp_paths):
    """The case Step 2 was actually written for, and the one the sibling tests
    do not reach because their two drafts are identical.

    Observed live on batch B's iteration-1 rescore, twice: draft 603 words ->
    redraft 725, and draft 622 -> redraft 646. The model is instructed to come
    in shorter and does the opposite. Step 2 must keep the FIRST draft in that
    case — "take the shorter draft" is not "take the redraft"."""
    con = _seed(tmp_paths)
    calls = []

    def chat(key, prompt):
        calls.append(prompt)
        b = _brief([{"observable": "Whether it registers", "cites": ["S1"]}])
        # draft 1 inside the band (699w); the "redraft" comes back far LONGER.
        # RE-BASED (NL-118 clause (i)): draft 1 x80 -> x97 so it still lands
        # over the 650 ceiling and inside the 731 band. The redraft's x130 is
        # UNCHANGED — it only has to come back longer than draft 1, and 929
        # words clears 699 exactly as it cleared 580.
        b["mechanism"] = ("Each ministry answers to its own committee. "
                          * (97 if len(calls) == 1 else 130))
        return b, 0.0, 0.0

    sa = analysis.analyze_story(con, "2026-07-26", 1, _slot(), "medium",
                                config.SourcesConfig(), "", "", 10.0, [], [],
                                fetch=_fetch, chat=chat,
                                sonar=lambda *a: ([], 0.0, "ok — 0 results"),
                                sleep=lambda s: None)
    assert sa.outcome == "ok", sa.detail
    assert len(calls) == 2
    band = [w for w in sa.warnings if "DISCLOSURE BAND" in w]
    assert len(band) == 1, sa.warnings
    # the SHORTER (first) draft is what shipped
    shipped = analysis._prose_words(
        sa.brief["pinned_facts"], sa.brief["ledger"], sa.brief["mechanism"],
        sa.brief["effects"], sa.brief["unknowns"], sa.brief["watch"],
        sa.brief.get("arc"))
    assert shipped <= int(542 * 1.35), shipped      # the 731 band (was 607)
    # the disclosure names the number that actually shipped — not the redraft's
    assert f"the shorter ({shipped})" in band[0], band[0]
    assert "and 929 words" in band[0], "the longer redraft is disclosed too"


def test_past_the_band_it_is_still_a_disclosed_rejection_not_a_third_call(
        tmp_paths):
    """CARRIED INVARIANT. The band is bounded: beyond budget x 1.35 the
    existing disclosed rejection is exactly as it was, and still on two calls.
    Without this pin, step 2 would read as 'the ceiling is now 1.35'.

    RE-BASED (NL-118 clause (i)): x100 = 720 words used to sit past the 607
    band; against the 731 band it now falls INSIDE, so the untouched fixture
    would have inverted this test's meaning — it would have shipped disclosed
    and reported the bound as broken. x121 = 867 restores 'past the band' with
    the same proportional overshoot x100 had."""
    sa, calls = _second_over_run(tmp_paths, 121)         # ~867 words > band 731
    assert sa.outcome == "rejected"
    assert len(calls) == 2
    assert "after length redraft" in sa.detail
    assert "disclosure band" in sa.detail


def test_the_editor_prompt_no_longer_pushes_the_lead_longer_with_teeth():
    """The contradiction the case file put on the record: the analysis
    contract rejects past budget +20% while `editor_pass.txt` held 'never
    below ~450', 'target ~640', 'must stay the single longest', and 'an edit
    that does it is discarded'."""
    from newslens import paths
    text = (paths.PROMPTS_DIR / "editor_pass.txt").read_text(encoding="utf-8")
    assert "never cuts the LEAD below ~450 words" not in text
    assert "below its 450 floor is a tier violation" not in text
    assert "NO LENGTH FLOOR" in text
    assert "short is a PASS" in text
    assert "single longest one" in text          # the ORDERING rule survives


# ---------------------------------------------------------------------------
# 7. gap_report second synthesis pass (default OFF)
# ---------------------------------------------------------------------------

def _slot():
    return {"slot": "1", "story_title": "Cockroach protests force a minister out",
            "summary": "s", "item_ids": [1], "outlets": ["The Hill"],
            "matched_tags": [], "matched_memory": [], "override": False}


def _fetch(url, timeout, cap=0, user_agent=""):
    import urllib.error
    if url.endswith("/robots.txt"):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    return (b"<html><body><article>" + b"<p>Retrieved body text here.</p>" * 60
            + b"</article></body></html>")


def _seed(tmp_paths):
    db.migrate()
    con = db.connect()
    with con:
        con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title,"
            " raw_excerpt, published_at) VALUES (1, 'rss', 'The Hill',"
            " 'https://thehill.com/x', 'Story', 'excerpt', '2026-07-20')")
        con.execute("INSERT INTO briefings (date, story_slots) VALUES"
                    " ('2026-07-26', ?)", (json.dumps([_slot()]),))
    return con


def test_the_second_pass_is_OFF_by_default(tmp_paths):
    """Default-off proved by CALL COUNT, not by reading the flag."""
    con = _seed(tmp_paths)
    calls = []

    def chat(key, prompt):
        calls.append(prompt)
        return _brief([{"observable": "Whether it registers", "cites": ["S1"]}]), 0.0, 0.0

    sa = analysis.analyze_story(con, "2026-07-26", 1, _slot(), "medium",
                                config.SourcesConfig(), "", "", 10.0, [], [],
                                fetch=_fetch, chat=chat,
                                sonar=lambda *a: ([], 0.0, "ok — 0 results"),
                                sleep=lambda s: None)
    assert sa.outcome == "ok" and len(calls) == 1
    assert config.SourcesConfig().gap_report_second_pass is False


def test_the_flag_is_a_sources_yaml_setting_not_a_new_env_var(tmp_paths):
    cfg = config.load_sources(_yaml_with_setting("gap_report_second_pass: true"))
    assert cfg.problems == [] and cfg.gap_report_second_pass is True
    cfg_off = config.load_sources(_yaml_with_setting("tts_engine: kokoro"))
    assert cfg_off.gap_report_second_pass is False
    bad = config.load_sources(_yaml_with_setting("gap_report_second_pass: yes please"))
    assert any("gap_report_second_pass" in p for p in bad.problems)


def _yaml_with_setting(line: str) -> Path:
    import tempfile
    body = ("sources:\n  - name: The Hill\n    rss_url: https://thehill.com/f\n"
            "    tier: full\ninterests:\n  broad: [politics]\n"
            f"settings:\n  {line}\n")
    fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8")
    fh.write(body)
    fh.close()
    return Path(fh.name)


def test_the_second_pass_emits_a_gap_report_and_makes_no_retrieval_call(tmp_paths):
    con = _seed(tmp_paths)
    calls = []
    sonar_calls = []

    def chat(key, prompt):
        calls.append(prompt)
        b = _brief([{"observable": "Whether it registers", "cites": ["S1"]}])
        if len(calls) == 2:
            b["gap_report"] = [{"question": "Who signs the order",
                                "answered": False, "cites": [],
                                "note": "no key in this map names the signatory"}]
        return b, 0.0, 0.0

    def sonar(*a):
        sonar_calls.append(a)
        return [], 0.0, "ok — 0 results"

    sa = analysis.analyze_story(con, "2026-07-26", 1, _slot(), "medium",
                                config.SourcesConfig(), "", "", 10.0, [], [],
                                fetch=_fetch, chat=chat, sonar=sonar,
                                sleep=lambda s: None, second_pass=True)
    assert sa.outcome == "ok" and len(calls) == 2
    assert "SECOND PASS — same story, SAME MATERIAL, no new sources." in calls[1]
    assert len(sonar_calls) == 1          # pass 1's rung only; pass 2 adds none
    assert any("gap-report second pass applied" in w for w in sa.warnings)
    row = con.execute("SELECT brief_json FROM analysis_briefs"
                      " ORDER BY id DESC LIMIT 1").fetchone()
    header = json.loads(row["brief_json"])["header"]
    assert header["gap_report"][0]["answered"] is False


def test_an_unanswered_question_that_vanishes_keeps_the_pass_one_brief(tmp_paths):
    """The engineering round's non-negotiable: without this the pass cannot
    distinguish 'not chased' from 'chased and absent', and A1's 'veteran
    leader' x4 reproduces."""
    con = _seed(tmp_paths)
    calls = []

    def chat(key, prompt):
        calls.append(prompt)
        b = _brief([{"observable": "Whether it registers", "cites": ["S1"]}])
        if len(calls) == 2:
            b["unknowns"] = [{"question": "Something else entirely",
                              "why_material": "it bites", "would_resolve": "a filing"}]
            b["gap_report"] = [{"question": "Who signs the order",
                                "answered": False, "cites": [], "note": "absent"}]
        return b, 0.0, 0.0

    sa = analysis.analyze_story(con, "2026-07-26", 1, _slot(), "medium",
                                config.SourcesConfig(), "", "", 10.0, [], [],
                                fetch=_fetch, chat=chat,
                                sonar=lambda *a: ([], 0.0, "ok — 0 results"),
                                sleep=lambda s: None, second_pass=True)
    assert sa.outcome == "ok"
    assert sa.brief["unknowns"][0]["question"] == "Who signs the order"
    assert any("vanished from unknowns" in w for w in sa.warnings)
