"""NL-103 remediation batch — ratified-register conformance pins.

The register (`design/TAXONOMY-PROPOSAL.md`, RATIFIED 2026-07-26) is binding
copy law, so its rulings get pinned like any other contract. This file covers
the rows this batch landed — the ones whose contract is a STRING or an aria
name, where a silent re-word is exactly the regression nobody notices:

  * row 4  — the cap refusal carries no env-var name (§3 global law)
  * row 5  — the Delete control is ink, never --danger (color is never the channel)
  * row 8  — the note explainer states the two facts and stops
  * row 10 — ONE writer-lookup string, refusal class, no roadmap promise
  * row 11 — a receipt never takes a pronoun object
  * row 16 — "interest" is dead; topic everywhere
  * row 17 — backs: bare visible destination + "Back to <destination>" aria name,
             the accessible name CONTAINING the visible label (WCAG 2.5.3)
  * row 18 — the state panels lost the pipeline tour, kept the facts and the act
  * row 20 — empty states name their class unless a head is programmatically theirs
  * row 21 — RETIRED-NOT-RENDERED marks every retired constant, and no marked
             constant is rendered by any surface
  * FIX-2  — the reader-reachable refusal class at BOTH `d.error` injection
             sites (topics + writers), folded in by the gate: every returned
             message is refusal-form and leaks no config path, filename or
             internal vocabulary — plus the writer receipt's success detail,
             which is live reader copy through row 11

Rows 7 and 9 are pinned where their originals live (test_nl68_batch
::test_topics_interface_hint_is_removed and test_server
::test_ride23_error_panel_wording_in_both_failure_positions) — the re-pin belongs
next to the assertion it replaces, not in a second place that can drift from it.

In-process render only; the autouse sandbox (conftest) redirects DATA_DIR/
DB_PATH and starts sources.yaml in the synthetic zero-source template state, so
the empty-state rows below are the template's honest output, never real config.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from newslens import config, labels, paths, server, webui

from test_v7_m2 import DATE, _brief, _con, _mem
from test_server import replica                            # noqa: F401


BACKS = (labels.BACK_TO_TODAY, labels.BACK_TO_EDITION,
         labels.BACK_TO_ARCHIVE, labels.THREAD_BACK)


# --- Row 17: the back affordances -----------------------------------------------


def test_row17_visible_back_labels_are_bare_destinations():
    """B8: the "← Back to today's edition" family DIES — the visible label is the
    destination and nothing else (the mockup's form)."""
    assert labels.BACK_TO_TODAY == "← Today"
    assert labels.BACK_TO_EDITION == "← This edition"
    assert labels.BACK_TO_ARCHIVE == "← Archive"
    assert labels.THREAD_BACK == "← Following"


def test_row17_aria_names_the_destination_and_contains_the_visible_label():
    """§3 aria law: the accessible name names the target and CONTAINS the visible
    label verbatim (WCAG 2.5.3 label-in-name) — every back, no exceptions."""
    for label in BACKS:
        html = server._back_link(label, "noop()")
        visible = label.lstrip("←").strip()
        aria = re.search(r'aria-label="([^"]+)"', html).group(1)
        assert aria == f"Back to {visible}", html
        assert visible in aria, (visible, aria)   # containment, case-exact
        assert html.endswith(f">{label}</a>")     # and the arrow stays visible


def test_row17_a_degenerate_label_cannot_render_a_nameless_link():
    """Gate FIX-6 (QA-4): the derivation's one unguarded input. An empty or
    arrow-only label produced `aria-label="Back to "` — a link with no
    accessible name, which is precisely what deriving the name was meant to
    make impossible. The loop above iterates the four real constants, so only
    this case can catch a degenerate RE-PIN."""
    for label in ("", "←", "← "):
        with pytest.raises(ValueError):
            server._back_link(label, "noop()")


def test_row17_aria_follows_a_re_pin(monkeypatch):
    """The derivation exists so the label table stays the ONE place: re-pin the
    label and the accessible name moves with it (gate FIX-2's contract, extended
    to the aria name)."""
    monkeypatch.setattr(labels, "BACK_TO_TODAY", "← ZZ-DEST")
    con = _con()
    html = server._render_deep_view("story-0", "H",
                                    {"header": {}, "brief": _brief()},
                                    DATE, con=con)
    con.close()
    assert "← ZZ-DEST" in html
    assert 'aria-label="Back to ZZ-DEST"' in html


def test_row17_thread_page_back_renders_both_halves():
    con = _con()
    tid = _mem(con, "A thread")
    mrow = con.execute("SELECT * FROM memory WHERE id = ?", (tid,)).fetchone()
    html = server._render_thread_page(con, mrow)
    con.close()
    assert labels.THREAD_BACK in html
    assert 'aria-label="Back to Following"' in html


def test_row17_deep_view_back_renders_both_halves():
    con = _con()
    html = server._render_deep_view("story-0", "H",
                                    {"header": {}, "brief": _brief()},
                                    DATE, con=con)
    con.close()
    assert labels.BACK_TO_TODAY in html
    assert 'aria-label="Back to Today"' in html


def test_row17_archive_edition_back_renders_both_halves():
    """Both branches of the edition fragment — the real edition and the
    unavailable-date panel — go through the same back affordance."""
    con = _con()
    frag, date_read = server.build_edition_fragment(con, "2020-01-02")
    con.close()
    assert date_read is None                       # the no-such-edition branch
    assert labels.BACK_TO_ARCHIVE in frag
    assert 'aria-label="Back to Archive"' in frag


# --- Row 4: no env-var name in reader copy ---------------------------------------


def test_row4_cap_refusal_carries_no_env_var_name():
    """§3 global law: reader copy never contains an env-var name — doctor/SETUP
    own it. (The route's machine-parseable `detail` field still carries the name
    for diagnostics; it is not reader copy and no surface renders it.)"""
    assert "BUDGET_CAP_USD_PER_RUN" not in labels.FOLLOW_CAP_REFUSAL
    # Gate FIX-7: typographic apostrophes — the rendered document's convention.
    assert labels.FOLLOW_CAP_REFUSAL == (
        "Couldn’t choose a broader follow — it costs more than this run’s "
        "budget allows. The follow stands — this story.")
    assert "'" not in labels.FOLLOW_CAP_REFUSAL


def test_row4_no_env_var_name_reaches_a_rendered_page():
    """CARRIED INVARIANT (born GREEN — it passes against HEAD too, and is
    labeled rather than claimed as born-red). It held before this batch only
    because the cap refusal never reaches a page at all: the client's
    `ok === false` branch reverts the card to resting and renders no reason.
    The pin exists for the day someone closes that gap — the reason may land on
    screen, the env-var name may not."""
    con = _con()
    page, _ = server.build_page(con)
    con.close()
    assert "BUDGET_CAP_USD_PER_RUN" not in page


# --- Row 5: the Delete control is ink, never danger -------------------------------


def test_row5_delete_control_carries_no_danger_color():
    """C7: --danger is not a control color. The confirm's grammar carries the
    weight; color is never the channel."""
    card = webui.POPUPS.split('id="popup-delete-confirm"')[1]
    assert "var(--danger)" not in card
    assert ">Delete</button>" in card              # the control itself survives
    assert "delete-action:hover" not in webui.CSS  # and its danger hover is gone
    # Gate FIX-4 (QA-3): and the NON-destructive Remove verb is not louder than
    # the destructive one — the inversion this batch would otherwise have shipped.
    assert "token-remove:hover { color: var(--ink); }" in webui.CSS


# --- Rows 8 / 10 / 11 / 16: the popup copy ---------------------------------------


def test_row8_note_explainer_states_the_two_facts():
    assert ("This note shapes future editions. It never appears in them."
            in webui.POPUPS)
    assert "shapes how future editions frame this story" not in webui.POPUPS


def test_row16_interest_is_dead_topic_everywhere():
    """Row 16 (A5): the reader-facing noun is `topic`, never `interest`.

    NL-150 (2026-08-25): the sentence this used to quote — "Add this as a broad
    topic or a specific one?" — was killed by the 2026-08-24 ruling, so the pin
    moves to what still renders on the same card. Row 16's own law is unchanged
    and now has MORE bite, not less: `interest` is dead across the whole popup
    set, and the surviving add-topic copy is checked for it directly."""
    assert "Add this as a broad topic or a specific one?" not in webui.POPUPS   # killed
    assert "broad interest" not in webui.POPUPS
    assert "interest" not in webui.POPUPS.lower()      # row 16, product-wide noun
    card = webui.POPUPS.split('id="popup-add-topic"')[1].split("</div>\n</div>")[0]
    assert "Add topic" in card                          # the class noun survives


def test_row10_one_writer_lookup_string_refusal_class():
    """C7a: two wordings and a roadmap promise collapse into ONE refusal-class
    string, shown when the reader actually hits the limit."""
    # Gate FIX-7: the apostrophe is typographic, escaped for the JS string.
    one = ("Name lookup isn\\u2019t available yet \\u2014 paste a link to "
           "their feed.")
    assert "Name-only lookup is coming" not in webui.POPUPS
    assert "Name-only lookup is coming" not in webui.JS
    assert webui.JS.count(one) == 1
    assert one not in webui.POPUPS                 # the static note stays dead


def test_row11_receipt_fallback_is_the_class_noun():
    """C7b: a receipt never takes a pronoun object."""
    assert "(name || 'them')" not in webui.JS
    assert "(name || 'the feed')" in webui.JS


# --- Row 18: the state panels ------------------------------------------------------


def test_row18_empty_panels_keep_the_facts_and_lose_the_pipeline_tour():
    con = _con()
    page, _ = server.build_page(con)
    con.close()
    # Gate FIX-3: the duration is "about half an hour" — six of six logged runs
    # put "a couple of minutes" an order of magnitude out.
    assert "No edition has been generated. Generating one takes about half an" in page
    assert "a couple of minutes" not in page
    assert "picks the stories" not in page
    assert "records the episode" not in page
    assert "Generate today’s edition" in page      # the act stays


def test_row18_generating_panel_keeps_status_and_loses_the_stage_tour(monkeypatch):
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    server.GEN_JOB.state = "running"
    con = _con()
    page, _ = server.build_page(con)
    con.close()
    assert "Generating today’s edition…" in page
    assert 'id="gen-live"' in page                 # the live status pointer's target
    assert "the page refreshes itself when it’s ready" in page
    assert "Fetching your sources, ranking, writing" not in page


# --- Row 20: empty states name their class ------------------------------------------


def test_row20_empty_states_name_their_class():
    """Five sites. Four name the class in-string (no programmatic head above
    them); the Archive one keeps `Nothing yet` because its own page-title h1 is
    the immediately preceding sibling."""
    con = _con()
    page, _ = server.build_page(con)
    con.close()
    assert "No threads yet" in page                # Following → Threads
    assert "No broad topics yet" in page           # Following → Topics (Broad)
    assert "No specific topics yet" in page        # Following → Topics (Specific)
    assert "No writers yet" in page                # Following → Writers
    assert "No edition yet" in page                # Settings → Today's edition
    # Gate FIX-5 (QA-2): LOCATE the two licensed bare "Nothing yet"s, then count.
    # Counting alone passed silently if a new bare one appeared while a sanctioned
    # one vanished — the exact regression the tripwire exists to catch.
    today_panel = re.search(r'<div class="state-panel">\s*<h2>Nothing yet</h2>.*?</div>',
                            page, re.S)
    assert today_panel and "No edition has been generated." in today_panel.group(0)
    assert ('<h1 class="page-title">Archive</h1><p class="empty-note">Nothing yet</p>'
            in page)
    assert page.count("Nothing yet") == 2      # and nowhere else


# --- FIX-2: the reader-reachable refusal class ------------------------------------------
#
# Both popup status elements render the API's `error` verbatim (webui.py's
# addTopic / addWriter), so every False-return string from these two add paths
# is reader copy and binds to §3's refusal rule: what did NOT happen, in
# reader-world terms, with the honest next act — never a config path, a
# filename, or internal vocabulary. The success detail is reader copy too: it
# rides row 11's receipt.

NO_SOURCES = ("sources:\n  - name: Example\n    rss_url: https://e.invalid/f\n"
              "interests:\n  broad:\n    - alpha\n  granular:\n    - beta\n")
NO_INTERESTS = "sources:\n  - name: Example\n    rss_url: https://e.invalid/f\n"


def test_fix2_both_popup_statuses_announce():
    """§3's refusal-loud floor: the two elements that render `d.error` are live
    regions, so a refusal reaches a screen-reader user at all. The writer one
    carries the follow receipt too."""
    for pid in ("add-topic-status", "add-writer-status"):
        el = re.search(r'<p class="popup-status[^"]*" id="%s"[^>]*>' % pid,
                       webui.POPUPS)
        assert el, pid
        assert 'aria-live="polite"' in el.group(0), el.group(0)


def test_fix2_topic_refusals_are_register_form(replica):
    ok, msg = server.topic_add("economy", "broad")            # already present
    assert not ok
    assert msg == "Didn’t add it — economy is already in your broad topics."

    paths.SOURCES_FILE.write_text("# just comments\n", encoding="utf-8")
    ok, msg = server.topic_add("anything", "broad")           # no section
    assert not ok
    assert msg == ("Didn’t add it — your sources file has no section for "
                   "broad topics.")


def test_fix2_writer_refusals_are_register_form(replica):
    ok, msg = server.writer_add("Somebody", "ftp://nope")
    assert not ok
    assert msg == ("Didn’t follow — that doesn’t look like a link. Feed links "
                   "start with http:// or https://.")

    ok, msg = server.writer_add("The Hill", "https://other.invalid/feed")
    assert not ok
    assert msg == "Didn’t follow — The Hill is already in your sources."

    paths.SOURCES_FILE.write_text(NO_INTERESTS, encoding="utf-8")
    ok, msg = server.writer_add("New Writer", "https://new.invalid/feed")
    assert not ok
    assert msg == ("Didn’t follow — your sources file is missing its topics "
                   "section.")


def test_fix2_bad_name_refusals_are_register_form(replica):
    for name, expected in (
            ("bad: colon", "Nothing was added — a name can’t contain ':'."),
            ("two\nlines", "Nothing was added — a name can’t contain line breaks."),
            ("# comment", "Nothing was added — a name can’t start with '#'.")):
        ok, msg = server.topic_add(name, "broad")
        assert not ok and msg == expected, (name, msg)


def test_fix2_revert_refusal_names_no_internals(replica, monkeypatch):
    """Both revert branches — raise and problems-state — say the same true
    thing. The exception text and the problems list stay out of reader copy;
    doctor prints the diagnostics."""
    expected = ("Nothing was saved — that change would have broken your "
                "sources file.")

    def boom():
        raise config.SourcesParseError("synthetic parse explosion")

    monkeypatch.setattr(config, "load_sources", boom)
    ok, msg = server.topic_add("Perfectly Fine Topic", "broad")
    assert not ok and msg == expected

    def problematic():
        cfg = config.SourcesConfig()
        cfg.problems.append("synthetic problem: `tier` must be one of ...")
        return cfg

    monkeypatch.setattr(config, "load_sources", problematic)
    ok, msg = server.topic_add("Another Fine Topic", "broad")
    assert not ok and msg == expected


def test_fix2_writer_success_detail_is_the_receipt_tail(replica):
    """Gate-found: this string is NOT internal. The handler returns it as
    `detail` and row 11's receipt renders `Following <name> — <detail>`."""
    ok, msg = server.writer_add("A New Writer", "https://new.invalid/feed")
    assert ok, msg
    assert msg == "added to your sources"
    assert "Following " + "A New Writer" + " — " + msg == \
        "Following A New Writer — added to your sources"


def test_fix2_no_add_path_message_leaks_internals(replica, monkeypatch):
    """The class sweep. Force every reachable failure on both add paths and
    prove the whole returned set is clean — a new leak in a branch nobody
    pinned individually still trips this."""
    banned = ("interests", "sources.yaml", "granular", "anchor", "pool",
              "(it changes", "(that's a comment)", "(that’s a comment)")
    msgs = []
    msgs.append(server.topic_add("economy", "broad")[1])
    msgs.append(server.topic_add("bad: colon", "broad")[1])
    msgs.append(server.topic_add("# comment", "specific")[1])
    msgs.append(server.writer_add("Somebody", "ftp://nope")[1])
    msgs.append(server.writer_add("The Hill", "https://x.invalid/f")[1])
    msgs.append(server.writer_add("Fresh Name", "https://fresh.invalid/f")[1])
    paths.SOURCES_FILE.write_text(NO_INTERESTS, encoding="utf-8")
    msgs.append(server.writer_add("New Writer", "https://new.invalid/feed")[1])
    paths.SOURCES_FILE.write_text("# just comments\n", encoding="utf-8")
    msgs.append(server.topic_add("anything", "broad")[1])

    for msg in msgs:
        low = msg.lower()
        for token in banned:
            assert token.lower() not in low, (token, msg)
        # and every one of them is a sentence, not a fragment
        assert msg[0].isupper() or msg.startswith("added"), msg


# --- Row 21: the retired-constant sweep marker ----------------------------------------


def _marked_retired() -> set:
    src = Path(labels.__file__).read_text(encoding="utf-8")
    return {m.group(1) for m in
            re.finditer(r"^([A-Z0-9_]+)\s*=.*#\s*RETIRED-NOT-RENDERED",
                        src, re.M)}


def test_row21_retired_constants_are_marked_and_rendered_nowhere():
    """C5: retired-but-kept names carry the sweep marker so a vocabulary pass
    skips them — and the marker's claim is enforced: no rendering module may
    reference a marked constant."""
    marked = _marked_retired()
    # NL-17-M1c added seven: the states his 07-25 rulings killed (the settling
    # status ①, the ask lead + its option row ④), the apology pair NL-103 row 3
    # killed with its composed whole, the reasonless switch line the R-WRITE
    # frame supersedes, and the cap refusal that retires from reader copy under
    # the content pass's §5.1 Arm A. Every one is KEPT (the ruled strings stay
    # on record, nothing imports a dangling name) and rendered NOWHERE — which
    # the loop below is what actually enforces.
    # NL-17 M1 joins FOLLOW_NARROW + FOLLOW_RUNG_THIS_STORY to the set: the
    # qualifier and rung seats of "this story", buried by amendment (i). Kept
    # under the same retired-but-named convention, rendered nowhere — which the
    # loop below is what actually enforces.
    # Fix loop 2b adds the worded-fallback pair — the "wider story" / "the
    # company" switch that died whole with the offer class (RECONVENE-2, ruling
    # (b)). Same convention: kept named, rendered nowhere, which the loop below
    # is what actually enforces.
    # NL-117 (mockup-v13 PASSED 2026-08-24) adds WHY_RELATED_TO: flag ② ruled the
    # reason line NAME-LED, which retires the STEM, and this constant's only
    # consumer was that stem. Its sibling WHY_CHOSEN_BECAUSE is NOT retired — it
    # still leads the markdown edition's §5.7 override line and the rank CLI,
    # neither of which this increment touched.
    assert marked == {"KICKER_LEAD", "FOLLOW_STORY_ACTIVE",
                      "FOLLOW_NARROW", "FOLLOW_RUNG_THIS_STORY",
                      "FOLLOW_ALT_FALLBACK_ENTITY", "FOLLOW_ALT_FALLBACK_STORYLINE",
                      "FOLLOW_STORY_CONFIRM",
                      "FOLLOW_RESOLVING", "FOLLOW_LOW_LEAD",
                      "FOLLOW_JUST_THIS_STORY_OPTION",
                      "FOLLOW_DEGRADE_LEAD", "FOLLOW_DEGRADE_UPGRADE",
                      "FOLLOW_DEGRADE_COMMITTED", "FOLLOW_SWITCH_FAILED",
                      "FOLLOW_CAP_REFUSAL", "WHY_RELATED_TO"}, marked
    for mod in (server, webui):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for name in marked:
            assert name not in src, (mod.__name__, name)
