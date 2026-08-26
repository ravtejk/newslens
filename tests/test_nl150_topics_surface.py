"""NL-150 — the topics surface, both halves of the 2026-08-25 batch.

HALF 1, THE RE-ADD PARITY BUG (his 08-12 report). The reported mechanism was
"the add path validates against catalog vocabulary". It does not: `topic_add`
consults no catalog at all (server.py, and `_bad_name` is its only name gate).
The real gate is on the OFFER side and it is client-enforced:

    the Topics combobox is rendered suggest_only=True (server._render_following)
      -> webui's suggestSubmit no-ops any value that is not an offered suggestion
      -> so the ONLY addable names are whatever `_topic_suggestions` returns
      -> which was (latest edition's matched_tags) MINUS followed
      -> and matched_tags is validated against `tag_levels`, built in
         ranking.py from cfg.interests_broad + cfg.interests_granular

so matched_tags is a SUBSET of the followed vocabulary by construction. Subtract
followed and the offer is empty in steady state — the module's own docstring
flagged exactly that ("in steady state this is empty ... a real 'topics to
discover' add-source is the skeleton-catalog work"). The skeleton-catalog work
SHIPPED as NL-116 (catalog.py, ratified 2026-07-28) and was never wired to this
surface. These tests wire the flagged fix and pin the acceptance: anything
removable is re-addable.

HALF 2, THE PROMPT KILL (ruled 2026-08-24, DECISIONS "THE SLATE RULED" item 5).
The level question dies; the level is INFERRED from the catalog by the same rule
the Commissioning already writes with (`catalog.LEVEL_TO_EDITOR_LEVEL`, applied
at commissioning.py's write step), with off-catalog defaulting to full weight.
The yaml semantics is untouched: broad still means interests.broad.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from newslens import catalog, config, db, server, webui


def _con():
    db.migrate()
    return db.connect()


def _cfg(broad=(), granular=()):
    return SimpleNamespace(interests_broad=list(broad),
                           interests_granular=list(granular),
                           sources=[], followed_analyst_sources=[])


def _sugg(con, cfg):
    return {o["v"] for o in server._topic_suggestions(con, cfg)}


# ===========================================================================
# HALF 1 — re-add parity
# ===========================================================================

def test_medicaid_is_on_catalog_not_off_it():
    """The bug report's premise, corrected on the record: Medicaid is IN the
    catalog at topic level (templates/topic-catalog.yaml, under Health
    Systems), so the failure was never about off-catalog vocabulary."""
    cat = catalog.load()
    assert cat.level_of("Medicaid") == catalog.TOPIC
    assert cat.canonical("medicaid") == "Medicaid"


def test_removed_catalog_topic_is_offered_again(tmp_paths):
    """THE ACCEPTANCE. A followed catalog topic that is removed comes back as
    an offer, so the suggestions-only combobox can re-add it. Red before this
    batch: the offer was scoped to the latest edition's matched tags, and a
    topic nobody is following can no longer be matched by anything."""
    con = _con()
    try:
        followed = _cfg(granular=["Medicaid", "Drug Pricing"])
        assert "Medicaid" not in _sugg(con, followed)   # held -> not offered
        after_removal = _cfg(granular=["Drug Pricing"])
        assert "Medicaid" in _sugg(con, after_removal)  # released -> offered
    finally:
        con.close()


def test_every_catalog_name_is_offered_when_unfollowed(tmp_paths):
    """Parity as a PROPERTY, not one example: with nothing followed and no
    edition on file, every one of the catalog's names is addable. This is also
    the fix for the flagged empty-in-steady-state defect — the box had nothing
    to offer a reader who had not just deleted something."""
    con = _con()
    try:
        offered = _sugg(con, _cfg())
        missing = [n for n in catalog.load().names() if n not in offered]
        assert not missing, f"catalog names not offered: {missing}"
    finally:
        con.close()


def test_offer_still_excludes_what_is_already_followed(tmp_paths):
    """The exclusion survives the widening, at BOTH levels and case-blind —
    the NL-11 seam-4 contract."""
    con = _con()
    try:
        offered = _sugg(con, _cfg(broad=["public health"],
                                  granular=["MEDICAID"]))
        assert "Public Health" not in offered
        assert "Medicaid" not in offered
        assert "Drug Pricing" in offered      # sibling, unfollowed -> still offered
    finally:
        con.close()


def test_live_matched_tags_still_ride_alongside_the_catalog(tmp_paths):
    """The latest-edition leg is WIDENED, not replaced: an off-catalog name the
    latest edition matched is still offered, which is the only re-add route an
    off-catalog topic has. Old-edition-only tags still do not resurface (NL-68
    item 12's ruled behaviour)."""
    con = _con()
    try:
        with con:
            con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                        ("2026-07-01", json.dumps([{"slot": "1", "matched_tags":
                            [{"name": "Old Off-Catalog Tag"}]}])))
            con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                        ("2026-07-14", json.dumps([{"slot": "1", "matched_tags":
                            [{"name": "NYC Subway"}]}])))
        offered = _sugg(con, _cfg())
        assert "NYC Subway" in offered              # latest edition -> live
        assert "Old Off-Catalog Tag" not in offered  # old-only -> not resurfaced
        assert "Medicaid" in offered                 # ...and the catalog rides too
    finally:
        con.close()


def test_a_broken_catalog_leaves_the_offer_standing_not_a_500(tmp_paths,
                                                              monkeypatch):
    """FIX-1 (gate R-1, 2026-08-25). The catalog leg reads a data file, and a
    data file can be broken. The degrade was BUILT — `_topic_suggestions`
    catches CatalogError and falls back to the edition-only offer — but it was
    UNPINNED, and an unpinned degrade on this surface is the closed-door class
    this batch exists to kill wearing a second face: a broken catalog would
    500 the whole Following page, which is readable in every other respect.

    The pin: catalog.load raises, one off-catalog tag on the latest edition,
    and the offer is that edition leg ALONE — the catalog's 66 names gone, no
    exception out (a re-raising degrade errors this test rather than failing
    it, which is the bite below).

    MUTATION-PROVEN, not born-red: the degrade was already built, so this pin
    is green on the batch bytes it was written against. Its currency is the
    bite — MUT-D1, the mutation that measured 80/80 GREEN at the 2026-08-25
    gate and named this hole. Transcribed from the run that observed it (the
    `except catalog.CatalogError as exc:` arm of _topic_suggestions replaced
    with a bare `raise`, planted in a copy, executed artifact asserted):

        E       newslens.catalog.CatalogError: synthetic
        FAILED …::test_a_broken_catalog_leaves_the_offer_standing_not_a_500
        1 failed, 85 passed         (the four batch files)
    """
    def boom(*a, **k):
        raise catalog.CatalogError("synthetic")
    monkeypatch.setattr(catalog, "load", boom)
    con = _con()
    try:
        with con:
            con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                        ("2026-07-14", json.dumps([{"slot": "1", "matched_tags":
                            [{"name": "NYC Subway"}]}])))
        assert _sugg(con, _cfg()) == {"NYC Subway"}
    finally:
        con.close()


def test_the_following_page_renders_the_offer_it_computes(tmp_paths):
    """FIX-5 (gate R-7, 2026-08-25). The handoff at _render_following —
    `_topic_suggestions(con, cfg)` into the topics combobox — had no test
    binding it: the existing render pin (test_nl68_batch_qa.py) asserts the
    suggest-only MARKERS, not the payload, so the wiring's only witnesses were
    live browser walks. A hand that dropped the argument would ship a
    permanently empty box — the pre-fix closed door, restored — with a green
    suite.

    The pin reads the rendered box's own JSON payload and holds it to two
    things: it IS what the function computed, and it carries an unfollowed
    catalog name (so an empty payload cannot pass as "correct by identity").

    MUTATION-PROVEN. BITE (the `_topic_suggestions(con, cfg)` argument at the
    _render_suggest("topic", …) call replaced with `[]`, planted in a copy,
    executed artifact asserted) — transcribed from the run:

        E           AssertionError: assert [] == [{'l': 'Afric...search'}, ...]
        E             Right contains 66 more items, first extra item:
                      {'l': 'Africa', 'v': 'Africa'}
        FAILED …::test_the_following_page_renders_the_offer_it_computes
        1 failed, 85 passed         (the four batch files)
    """
    con = _con()
    try:
        cfg = config.load_sources()
        expected = server._topic_suggestions(con, cfg)
        html = server._render_following(con)
        payload = json.loads(html.split('data-kind="topic"')[1]
                                 .split('class="suggest-data">')[1]
                                 .split("</script>")[0])
        assert payload == expected
        assert "Medicaid" in {o["v"] for o in payload}
    finally:
        con.close()


def test_remove_then_readd_round_trips_at_the_same_level(tmp_paths):
    """End to end through the shipped doors, both levels: remove a domain and a
    topic, confirm each is offered again, re-add each with NO level argument
    (what the killed prompt used to supply), and land back in the group it came
    from. Level-preserving round trip is the acceptance's real shape — a
    Medicaid that returns at half weight would be a different bug."""
    path = config.paths.SOURCES_FILE
    path.write_text(
        "sources:\n"
        "  - name: Example\n"
        "    rss_url: https://example.invalid/feed\n"
        "interests:\n"
        "  broad:\n"
        "    - Public Health\n"
        "  granular:\n"
        "    - Medicaid\n", encoding="utf-8")
    con = _con()
    try:
        for name, group in (("Medicaid", "granular"), ("Public Health", "broad")):
            ok, msg = server.topic_remove(name)
            assert ok, msg
            cfg = config.load_sources()
            assert name in _sugg(con, cfg), f"{name} not re-offered"
            ok, msg = server.topic_add(name)          # no level: inferred
            assert ok, msg
            back = config.load_sources()
            assert name in getattr(back, f"interests_{group}"), \
                f"{name} came back at the wrong level"
    finally:
        con.close()


# ===========================================================================
# HALF 2 — the prompt kill
# ===========================================================================

def test_the_level_question_is_gone_from_the_popup():
    """The ruled kill, on the surface that carried it. The two rung buttons go
    with it — they ARE the question in button form."""
    assert "broad topic or a specific one" not in webui.POPUPS
    assert "Add as broad" not in webui.POPUPS
    assert "Add as specific" not in webui.POPUPS


def test_the_popup_no_longer_speaks_the_dead_rung_words():
    """DECISIONS 2026-07-28 §2 made broad/granular/specific DEAD as reader
    words; test_stage0_c1_vocabulary.py flagged this popup as the known live
    violation it did not own. The kill closes it.

    Source COMMENTS are stripped before the check — the law is about what a
    reader can read, and the comment explaining the kill necessarily names the
    words it killed. With them stripped this still bites the shipped bytes:
    both rung buttons and the question sentence were rendered text."""
    card = webui.POPUPS.split('id="popup-add-topic"')[1].split("</div>\n</div>")[0]
    rendered = re.sub(r"<!--.*?-->", "", card, flags=re.S).lower()
    for dead in ("broad", "granular", "specific"):
        assert dead not in rendered, f"dead rung word {dead!r} still on the card"


def test_the_add_popup_keeps_its_refusal_surface():
    """The ASK dies, the popup does not: it is the only element that renders a
    topic-add refusal, and NL-103 FIX-2 (gate 2026-07-26) made those refusals
    reader-facing and loud. Killing the card would silently drop them."""
    status = re.search(r"<p[^>]*id=\"add-topic-status\"[^>]*>", webui.POPUPS)
    assert status, "the refusal element is gone"
    assert 'aria-live="polite"' in status.group(0)   # still announces
    assert "addTopic()" in webui.POPUPS              # one act, no level argument


def test_client_sends_no_level():
    """The wiring proof: the JS add call carries name only. A client-side level
    would be a second copy of the catalog's split, which catalog.py exists to
    prevent."""
    fn = webui.JS.split("function addTopic(")[1].split("\nfunction ")[0]
    assert "level:" not in fn
    assert "'/api/topic/add'" in fn


def test_level_is_inferred_from_the_catalog(tmp_paths):
    """The ruled inference, all three arms: a catalog DOMAIN name infers broad,
    a catalog TOPIC name infers specific, and an off-catalog name defaults to
    specific/full weight. Same rule and same map the Commissioning writes with
    (catalog.LEVEL_TO_EDITOR_LEVEL)."""
    assert server._inferred_level("Public Health") == "broad"
    assert server._inferred_level("public health") == "broad"   # case-blind
    assert server._inferred_level("Medicaid") == "specific"
    assert server._inferred_level("NYC Subway") == "specific"   # off-catalog
    assert server._inferred_level("") == "specific"


def test_inference_agrees_with_the_commissionings_own_map():
    """No second rule: the inference IS LEVEL_TO_EDITOR_LEVEL over the catalog,
    so the two doors cannot drift into disagreeing about a name."""
    cat = catalog.load()
    for name in cat.names():
        assert server._inferred_level(name) == \
            catalog.LEVEL_TO_EDITOR_LEVEL[cat.level_of(name)]


def test_an_unreadable_catalog_does_not_take_the_add_door_down(tmp_paths, monkeypatch):
    """The inference reads a data file, and a data file can be broken. A
    catalog that will not load must not turn every add into a refusal — it
    falls back to the ruled default (full weight) and grades itself to the
    operator's terminal, the house pattern for this class."""
    def boom(*a, **k):
        raise catalog.CatalogError("synthetic")
    monkeypatch.setattr(catalog, "load", boom)
    assert server._inferred_level("Public Health") == "specific"


def test_explicit_level_still_wins(tmp_paths):
    """The Commissioning passes a level it derived itself
    (commissioning.py's write step) — that call site must keep working
    unchanged, and an explicit level must never be silently re-inferred."""
    path = config.paths.SOURCES_FILE
    path.write_text(
        "sources:\n"
        "  - name: Example\n"
        "    rss_url: https://example.invalid/feed\n"
        "interests:\n"
        "  broad:\n"
        "  granular:\n", encoding="utf-8")
    # "Medicaid" infers specific; forcing broad must put it in broad.
    ok, msg = server.topic_add("Medicaid", "broad")
    assert ok, msg
    assert "Medicaid" in config.load_sources().interests_broad


def test_api_add_accepts_a_bodyless_level(tmp_paths):
    """The route contract the client now relies on: level absent is lawful and
    infers, rather than refusing with the old 'level must be broad or
    specific'."""
    path = config.paths.SOURCES_FILE
    path.write_text(
        "sources:\n"
        "  - name: Example\n"
        "    rss_url: https://example.invalid/feed\n"
        "interests:\n"
        "  broad:\n"
        "  granular:\n", encoding="utf-8")
    ok, msg = server.topic_add("Public Health")
    assert ok, msg
    cfg = config.load_sources()
    assert "Public Health" in cfg.interests_broad
    assert "Public Health" not in cfg.interests_granular


# ===========================================================================
# HALF 2b — the write door speaks the catalog's spelling (FIX-4, gate R-3)
# ===========================================================================
# catalog.py:89-91 states the contract in its own words: "What gets WRITTEN to
# sources.yaml is always this, never the bytes the client sent." The
# Commissioning door honours it (commissioning.py's write step resolves first);
# topic_add did not — it wrote the POSTED bytes, so a reader who TYPED a
# suggestion instead of clicking it (webui.suggestSubmit matches case-blind and
# then posts the reader's own casing) minted a second spelling of a catalog
# name in his file. The gate ruled the mechanism rather than the docstring:
# canonicalize here, off-catalog verbatim, broken catalog verbatim.

def _empty_interests_yaml():
    config.paths.SOURCES_FILE.write_text(
        "sources:\n"
        "  - name: Example\n"
        "    rss_url: https://example.invalid/feed\n"
        "interests:\n"
        "  broad:\n"
        "  granular:\n", encoding="utf-8")
    return config.paths.SOURCES_FILE


def test_a_typed_catalog_name_is_written_in_the_catalogs_own_spelling(tmp_paths):
    """ARM 1 — the ruled mechanism. The reader types "medicaid" and picks
    nothing; the file gets "Medicaid". Asserted on the FILE's bytes, because
    the contract is about what is written, not about what a loader can
    normalise back (it cannot: sources.yaml is his hand-editable file and the
    spellings in it are what he reads).

    BORN RED on the pre-fix batch bytes (a copy at server.py sha 712e4e1b…,
    PYTHONPATH + newslens.__file__ asserted inside it) — transcribed:

        E       AssertionError: assert '    - Medicaid\\n' in 'sources:\\n
                  - name: Example\\n    rss_url: https://example.invalid/feed
                  \\ninterests:\\n  broad:\\n  granular:\\n    - medicaid\\n'
        FAILED …::test_a_typed_catalog_name_is_written_in_the_catalogs_own_spelling
        1 failed, 40 passed
    """
    path = _empty_interests_yaml()
    ok, msg = server.topic_add("medicaid")
    assert ok, msg
    text = path.read_text(encoding="utf-8")
    assert "    - Medicaid\n" in text
    assert "medicaid" not in text                       # no second spelling
    assert config.load_sources().interests_granular == ["Medicaid"]


def test_an_off_catalog_name_is_written_exactly_as_typed(tmp_paths):
    """ARM 2 — canonicalisation is a CATALOG lookup, not a title-caser. A name
    the catalog does not carry is the reader's own vocabulary and is written
    byte-for-byte as they typed it (the off-catalog full-weight default from
    _inferred_level is unchanged).

    CARRIED INVARIANT, born GREEN — the pre-fix door wrote everything verbatim,
    so this arm states what must SURVIVE the fix, not what it changes. Its
    currency is therefore the bite, MUT-T (the off-catalog branch's
    `return name, "specific"` widened to `return name.title(), "specific"`,
    planted in a copy, executed artifact asserted) — transcribed:

        E       AssertionError: assert '    - nyc subway\\n' in '…granular:
                  \\n    - Nyc Subway\\n'
        E       AssertionError: assert 'Didn’t add i...broad topics.' ==
                  'Didn’t add i...broad topics.'
        E         - Didn’t add it — economy is already in your broad topics.
        E         + Didn’t add it — Economy is already in your broad topics.
        FAILED …::test_an_off_catalog_name_is_written_exactly_as_typed
        FAILED …test_nl103_register_conformance.py::
                  test_fix2_topic_refusals_are_register_form
        2 failed, 84 passed         (the four batch files)

    The SECOND red is worth reading, because it is the live coupling this arm
    sits next to: `topic_add`'s NL-103 FIX-2 refusals interpolate the name, so
    the spelling this function returns is the spelling the READER is refused
    with. Off-catalog (nl103's "economy") that is their own bytes, which is
    what its pin holds. For a CATALOG name it is now the catalog's — "Didn't
    add it — Medicaid is already in your specific topics" where the pre-fix
    door would have echoed "medicaid" back. The template is untouched; only
    the interpolated name moved, and it moved onto the product's own spelling.
    """
    path = _empty_interests_yaml()
    ok, msg = server.topic_add("nyc subway")
    assert ok, msg
    assert "    - nyc subway\n" in path.read_text(encoding="utf-8")
    assert config.load_sources().interests_granular == ["nyc subway"]


def test_a_broken_catalog_still_writes_the_typed_name(tmp_paths, monkeypatch):
    """ARM 3 — degrade, never a new closed door. The spelling is a nicety; the
    reader's act is not. A catalog that will not load writes what they typed
    and grades itself to the operator's terminal, the same house pattern
    _inferred_level and _topic_suggestions already use for this error.

    CARRIED INVARIANT, born GREEN, for the same reason as ARM 2 — and the arm
    that matters most, because the fix is what put a catalog READ on this
    door's path at all. Bite, MUT-F (the write-identity `except
    catalog.CatalogError` arm replaced with a bare `raise`, so a broken data
    file mints a new closed door on the add path) — transcribed:

        E       newslens.catalog.CatalogError: synthetic
        FAILED …::test_an_unreadable_catalog_does_not_take_the_add_door_down
        FAILED …::test_a_broken_catalog_still_writes_the_typed_name
        2 failed, 84 passed         (the four batch files)
    """
    def boom(*a, **k):
        raise catalog.CatalogError("synthetic")
    monkeypatch.setattr(catalog, "load", boom)
    path = _empty_interests_yaml()
    ok, msg = server.topic_add("medicaid")
    assert ok, msg
    assert "    - medicaid\n" in path.read_text(encoding="utf-8")
    assert config.load_sources().interests_granular == ["medicaid"]
