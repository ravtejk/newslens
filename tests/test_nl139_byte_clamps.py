"""NL-139 — THE BYTE CLAMPS (NL-133 gate ruling R-B), lockstep with NL-138.

NL-133 turned `analysis.PROMPT_MARGIN_CHARS` from an allowance into a PROOF
BOUND by capping cluster SHAPE (MAX_CLUSTER_ITEMS=48 + the matched_memory
de-dup). The gate stated the residue in the same breath: the bound is
conditional on FIELD LENGTHS, and three owners could still break it —

  (a) `_sonar_verify`   vendor titles/snippets/urls: count-clamped to 8, NOT
                        byte-clamped. Recorded breach: 8 titles averaging >=366
                        chars pierce cap 48 by 7 chars.
  (b) memory            topic inserts carried no length clamp at any door.
  (c) ingest            feed titles / feed article URL hosts.

This batch closes (a) and (b). (c) remains, is named in analysis.py, and is the
honest reason the bound is still stated as conditional.

EVERY worst case below is CONSTRUCTED FROM THE SHIPPED CONSTANTS and rendered
through the REAL constructors — the same rule NL-133's pin set, for the same
reason (NL-118's 4.33-char surviving margin, NL-130's two-addresses lesson).
The clamps are not hand-frozen numbers either: their derivation is in
research/2026-08-02--nl138-build.md and the arithmetic that justifies each one
is re-executed here, from the constants, on every run.

BORN-RED CLASS (ENGINEERING.md:122) — CORRECTED IN FIX LOOP 1 (QA F-4). The
original header claimed the three BREACH pins were behaviour-red at 38141a3.
They are not: the worst-case harness reads `analysis.SONAR_MAX_RESULTS` before
any assertion runs, so at HEAD all three die on AttributeError for the absent
clamp constants, BEFORE they can demonstrate the breach. Every pin in this
file is therefore a **NEW-SURFACE red** — red at HEAD on a missing symbol, not
on behaviour — and each says so in its own docstring.

The breaches themselves are real and independently re-derived (mine, and QA's
own construction, agreeing to the digit); what was wrong was the CURRENCY the
claim was paid in, and the 2026-07-18 law exists precisely so that stays
honest. Reordering the pins to assert the breach before touching a clamp
symbol was considered and rejected: it would require hand-freezing the result
count and field maxima as literals, which is the other rule this file is
built on ("every worst case CONSTRUCTED FROM THE SHIPPED CONSTANTS"). The
behaviour currency for this batch is carried by the memory-DOOR pins here and
by tests/test_nl139_fixloop1_caller_perimeter.py, which are genuine behaviour
reds.

The HEAD-run fail list is in the build record.
"""
import pytest

from newslens import analysis, memory, paths, ranking


# --------------------------------------------------------------------------
# The worst case, built from constants (extends NL-133's construction)
# --------------------------------------------------------------------------
# INGEST's all-time observed maxima — swept read-only from the founder DB
# (11,198 source_items / 175 cluster records) on 2026-08-02. These stay
# observations rather than constants because ingest is the ONE residue owner
# this batch does not close: they are the bound's stated precondition.
TITLE_MAX = 183      # max(len(source_items.title))
OUTLET_MAX = 41      # max(len(source_items.outlet)) — sources.yaml, his own
HOST_MAX = 27        # max url host length


def _worst_source_map(n_items, *, sonar_title, sonar_host, topic, n_priors=None,
                      n_sonar=None):
    """The most expensive source map `n_items` in ONE cluster can render.

    Worst on every axis at once: every key full-text, every title/outlet at its
    all-time maximum, plus the Sonar results `_sonar_verify` admits and the
    prior-briefing keys. `sonar_host` selects the BRANCH — equal to the cluster
    host means one maximal sibling list; a longer one means the R keys form a
    second maximal list of their own. Both are rendered; the binding one is
    whichever measures larger, which is the only honest way to read a
    quadratic.
    """
    if n_priors is None:
        n_priors = memory.CONTEXT_CAP
    if n_sonar is None:
        n_sonar = analysis.SONAR_MAX_RESULTS
    host = "h" * (HOST_MAX - 4) + ".com"
    shost = "h" * (sonar_host - 4) + ".com"
    title, outlet = "T" * TITLE_MAX, "O" * OUTLET_MAX
    items = [{"outlet": outlet, "url": "https://%s/%d" % (host, i),
              "title": title, "raw_excerpt": "x" * 4000,
              "fetched_at": "2026-08-02T00:00Z", "published_at": "2026-08-02",
              "source_name": outlet, "tier": "full"} for i in range(n_items)]
    records = [analysis.FetchRecord(
        url=it["url"], source_name=outlet, tier="full", outcome=analysis.OK,
        attempted=True, title=title, text="x" * 4000) for it in items]
    sonar = [{"url": "https://%s/s%d" % (shost, i), "title": "T" * sonar_title,
              "date": "2026-08-02", "snippet": "s" * 4000}
             for i in range(n_sonar)]
    priors = [{"date": "2026-08-02", "text": "p" * 4000, "thread": "M" * topic}
              for _ in range(n_priors)]
    return analysis.build_source_map(records, items, sonar, priors)


def _worst_prompt_chars(n_items, *, sonar_title, sonar_host, topic, **kw):
    """That map, rendered into the real analysis prompt. Returns
    (prompt_chars, bound_chars) — the two numbers rung 1's guarantee compares."""
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    sources = _worst_source_map(n_items, sonar_title=sonar_title,
                                sonar_host=sonar_host, topic=topic, **kw)
    prompt = analysis._render_prompt(template, {
        "word_budget": str(analysis.word_budget_for("full")),
        "tier": "medium", "date": "2026-08-02", "slot": "1",
        "story_title": "S" * 300, "story_summary": "U" * 400,
        "memory_context": "\n".join(["M" * topic] * memory.CONTEXT_CAP),
        "source_map": analysis.render_source_map(sources),
        "material": analysis.render_material(sources)})
    return len(prompt), analysis.brief_bound_chars(template)


def _at_the_clamps(n_items=None, **over):
    """The post-clamp worst case: every clamped field AT its clamp, ingest at
    its observed maxima, worst over BOTH sibling-list branches."""
    if n_items is None:
        n_items = ranking.MAX_CLUSTER_ITEMS
    kw = dict(sonar_title=analysis.SONAR_TITLE_MAX_CHARS,
              topic=memory.TOPIC_MAX_CHARS)
    kw.update(over)
    same, bound = _worst_prompt_chars(n_items, sonar_host=HOST_MAX, **kw)
    own, _ = _worst_prompt_chars(n_items,
                                 sonar_host=analysis.SONAR_HOST_MAX_CHARS, **kw)
    return max(same, own), bound


# --------------------------------------------------------------------------
# 1. THE RECORDED BREACHES — reproduced, then closed
# --------------------------------------------------------------------------

def test_the_recorded_sonar_title_breach_is_closed_by_the_clamp():
    """(a), the gate's own recorded specimen: 8 vendor titles averaging >=366
    chars pierce the bound at cap 48 by 7 chars.

    NEW-SURFACE pin (labelled, ENGINEERING.md:122 — corrected in fix loop 1,
    QA F-4): at 38141a3 this dies on the absent `clamp_sonar_results` /
    `SONAR_MAX_RESULTS`, BEFORE the breach assertion runs. It is NOT behaviour
    currency. The breach it measures is real and re-derived independently, and
    the assertion below still fails if a future edit makes the unclamped shape
    stop breaching — but HEAD never gets that far.
    """
    hostile = [{"url": "https://h.example/%d" % i, "title": "T" * 366,
                "date": "2026-08-02", "snippet": "s" * 4000}
               for i in range(analysis.SONAR_MAX_RESULTS)]
    kept, truncated, _ = analysis.clamp_sonar_results(hostile)
    longest = max(len(r["title"]) for r in kept)
    assert truncated == analysis.SONAR_MAX_RESULTS
    assert longest == analysis.SONAR_TITLE_MAX_CHARS

    unclamped, bound = _worst_prompt_chars(
        ranking.MAX_CLUSTER_ITEMS, sonar_title=366, sonar_host=HOST_MAX,
        topic=memory.TOPIC_MAX_CHARS)
    assert unclamped > bound, (
        "the recorded breach no longer reproduces (%d vs %d) — the derivation "
        "moved underneath this pin, so RE-DERIVE rather than deleting it"
        % (unclamped, bound))

    clamped, bound = _worst_prompt_chars(
        ranking.MAX_CLUSTER_ITEMS, sonar_title=longest, sonar_host=HOST_MAX,
        topic=memory.TOPIC_MAX_CHARS)
    assert clamped <= bound, (
        "clamped vendor titles still outrun the bound: %d vs %d"
        % (clamped, bound))


def test_the_sonar_host_is_inside_the_label_budget_and_dropped_past_the_dns_max():
    """(a), the branch the title clamp does NOT cover — RE-DERIVED IN NL-142
    FIX LOOP 2, because the answer changed.

    WHAT THIS PIN USED TO SAY. The URL's PATH never renders, but its HOST does
    — twice per R line plus once per sibling line — so Sonar keys holding a
    maximal host of their own formed a SECOND maximal sibling list at 16 chars
    of prompt per char of host, and a 330-char host breached the bound by 623.
    That was true, and it was the reason SONAR_HOST_MAX_CHARS was DERIVED
    against the bound.

    WHAT THE MACHINE DOES NOW. The same 16-chars-per-char cost is what let a
    Sonar result on the CLUSTER'S OWN host carry the bound past its ceiling
    through a door nobody had enumerated (gate F-G0: 81,972 vs 78,621). The
    close put R-key LABELS inside `MAP_LABEL_BUDGET_CHARS`, and the budget
    does not care how long the host it truncates was — so the vendor host has
    NO ceiling against this bound any more, in either direction. Raising it
    buys the prompt nothing; the 330-char breach does not reproduce and MUST
    NOT be re-asserted.

    THE CLAMP STAYS, ON ITS OTHER LEG. NL-139 gave two reasons and only one of
    them was arithmetic: 253 is the DNS maximum hostname length (RFC
    1035/1123), so a URL whose host exceeds it is not a hostname, and admitting
    it would mint an R key carrying a FABRICATED OUTLET IDENTITY — one that
    `_outlet_id`, `outlet_index` and the reader-facing "retrieved-single (%s)"
    provenance string would all then quote. That leg is untouched by F-G0 and
    is what this constant now rests on. A future re-derivation that finds no
    bound-ceiling here must not read that as licence to raise it.

    BEHAVIOUR-RED at the pre-fix tree, measured: the render is sensitive to
    the vendor host there (74,556 / 75,148 / 78,012 / 137,964 at hosts
    37 / 74 / 253 / 4,000), so the insensitivity assert fails on the first
    pair."""
    flat = []
    for host in (37, 74, analysis.SONAR_HOST_MAX_CHARS,
                 analysis.SONAR_HOST_MAX_CHARS + 77, 4_000):
        prompt, bound = _worst_prompt_chars(
            ranking.MAX_CLUSTER_ITEMS,
            sonar_title=analysis.SONAR_TITLE_MAX_CHARS, sonar_host=host,
            topic=memory.TOPIC_MAX_CHARS)
        flat.append((host, prompt))
    assert len({p for _h, p in flat}) == 1, (
        "the analyst prompt is sensitive to the vendor host again (%r) — a "
        "render door stopped applying the label budget to R keys, which is "
        "gate finding F-G0" % (flat,))
    assert flat[0][1] <= bound, (
        "the vendor-host branch renders %d chars against a %d-char bound"
        % (flat[0][1], bound))

    # The other leg, unchanged: a host that is not a hostname is refused.
    over = analysis.SONAR_HOST_MAX_CHARS + 77          # 330 at today's value
    bad = [{"url": "https://%s.example/x" % ("h" * over), "title": "t",
            "snippet": "s"}]
    kept, _, dropped = analysis.clamp_sonar_results(bad)
    assert (kept, dropped) == ([], 1), (
        "a result whose host is not a hostname was admitted: %r" % (kept,))


def test_the_memory_topic_breach_is_closed_by_the_clamp():
    """(b). A topic is the most leveraged string in the analysis prompt: 30
    chars of prompt per char of topic (CONTEXT_CAP memory_context lines plus
    one prior-briefing P-key title per thread, both capped at 15). The bound
    breaks at a 113-char topic.

    NEW-SURFACE pin (labelled — fix loop 1, QA F-4): at 38141a3 the absent
    `SONAR_TITLE_MAX_CHARS` / `TOPIC_MAX_CHARS` raise before the breach
    assertion runs. Symbol-red, not behaviour-red. (That a 113-char topic was
    insertable at every door on HEAD is true and was measured; this test is
    simply not the thing that proves it.)"""
    breaching_topic = 113
    over, bound = _worst_prompt_chars(
        ranking.MAX_CLUSTER_ITEMS, sonar_title=analysis.SONAR_TITLE_MAX_CHARS,
        sonar_host=HOST_MAX, topic=breaching_topic)
    assert over > bound, (
        "a %d-char topic no longer breaches (%d vs %d) — re-derive"
        % (breaching_topic, over, bound))
    assert memory.TOPIC_MAX_CHARS < breaching_topic, (
        "the clamp is at or past the breach point — it bounds nothing")

    stored, was_cut = memory.clamp_topic("M" * breaching_topic)
    assert was_cut and len(stored) == memory.TOPIC_MAX_CHARS

    under, bound = _worst_prompt_chars(
        ranking.MAX_CLUSTER_ITEMS, sonar_title=analysis.SONAR_TITLE_MAX_CHARS,
        sonar_host=HOST_MAX, topic=len(stored))
    assert under <= bound


# --------------------------------------------------------------------------
# 2. THE EXTENDED ARITHMETIC — NL-133's coupling, now over the clamped fields
# --------------------------------------------------------------------------

def test_the_post_clamp_worst_case_fits_and_the_clamps_are_load_bearing():
    """The NL-133 arithmetic pin, extended to the clamped worst case. Derived
    entirely from shipped constants, both directions pinned:

      * AT the clamps, worst over both sibling-list branches, the prompt fits.
      * ONE STEP PAST either clamp it does not. That second half is what makes
        the clamp VALUES load-bearing rather than decorative — the same
        argument NL-133's +2 arm makes for MAX_CLUSTER_ITEMS.

    A future edit that raises a clamp, raises MAX_CLUSTER_ITEMS, or lowers
    PROMPT_MARGIN_CHARS lands here first.

    BORN RED at 38141a3 on the absent symbols (SONAR_* / TOPIC_MAX_CHARS)."""
    worst, bound = _at_the_clamps()
    assert worst <= bound, (
        "the clamped worst case renders %d chars against a %d-char bound (over "
        "by %d). Lower a clamp or raise analysis.PROMPT_MARGIN_CHARS — and note "
        "the margin is a MONEY guard: raising it raises brief_bound_usd, so "
        "more slots skip under exhaustion." % (worst, bound, worst - bound))

    # Load-bearing, clamp by clamp. The step sizes are the measured marginal
    # costs (8 / 16 / 30 chars of prompt per char of field), so one step is
    # deliberately larger than one character: a clamp with only single-digit
    # headroom would be a knife-edge, and the pin should say so before a
    # future re-derivation does.
    title_over, _ = _at_the_clamps(
        sonar_title=analysis.SONAR_TITLE_MAX_CHARS + 200)
    topic_over, _ = _at_the_clamps(topic=memory.TOPIC_MAX_CHARS + 40)
    assert title_over > bound, (
        "the vendor title clamp is no longer the binding constraint — the "
        "margin grew, so RE-DERIVE it instead of leaving the product "
        "truncating headlines it no longer needs to")
    assert topic_over > bound, (
        "the topic clamp is no longer binding — re-derive it rather than "
        "leaving the product shortening thread names it need not shorten")


def test_the_cap_still_binds_over_the_clamped_fields():
    """NL-133's cap-binds invariant, RE-MEASURED at the new field values. Two
    more items past the cap must still breach after the clamps: if the clamps
    had bought so much headroom that the cap stopped binding, the honest
    response would be to re-derive the cap, not to enjoy the slack silently.

    NEW-SURFACE pin (labelled, per ENGINEERING.md:122): at 38141a3 this fails
    on the absent clamp constants, BEFORE reaching either assert — so it is
    NOT behaviour currency. The cap's own binding is NL-133's proof and is
    carried, not re-claimed here."""
    worst, bound = _at_the_clamps()
    over, _ = _at_the_clamps(ranking.MAX_CLUSTER_ITEMS + 2)
    assert worst <= bound < over


def test_the_clamps_never_bite_a_shape_this_product_has_ever_produced():
    """The floor half of every clamp's design (NL-133's rule: floor from
    observed data, ceiling from the bound, land inside both).

    BORN RED at 38141a3 on the absent symbols."""
    # No title this product has ever ingested (all-time max 183) is truncated.
    assert analysis.SONAR_TITLE_MAX_CHARS > TITLE_MAX
    # No hostname can exceed the DNS limit, so no real result is dropped.
    assert analysis.SONAR_HOST_MAX_CHARS == 253
    # The all-time longest thread name in the founder's memory table is 64.
    assert memory.TOPIC_MAX_CHARS > 64
    # And the snippet clamp cannot cost the prompt one rendered char, because
    # render_material water-fills the whole block into the material budget.
    assert analysis.SONAR_SNIPPET_MAX_CHARS >= analysis.MATERIAL_BUDGET_CHARS


# --------------------------------------------------------------------------
# 3. THE CLAMP'S OWN SHAPE + ITS DISCLOSURE
# --------------------------------------------------------------------------

def test_the_count_clamp_applies_before_the_byte_clamp():
    """Order is a design call, stated in `clamp_sonar_results`: a vendor
    cannot spend our per-result budget on results 9..N, and cannot push a good
    result out of the window by padding earlier ones with junk hosts. A
    dropped result costs a slot rather than promoting the next one — the
    conservative direction for a bound.

    BORN RED at 38141a3 on the absent symbol."""
    n = analysis.SONAR_MAX_RESULTS
    payload = ([{"url": "https://%s.example/x" % ("h" * 400), "title": "bad",
                 "snippet": "s"}] * n
               + [{"url": "https://good.example/%d" % i, "title": "good",
                   "snippet": "s"} for i in range(5)])
    kept, _, dropped = analysis.clamp_sonar_results(payload)
    assert dropped == n and kept == [], (
        "an out-of-window result was promoted into the window by the drop: %r"
        % (kept,))


def test_the_clamp_truncates_titles_and_snippets_and_keeps_the_rest():
    """BORN RED at 38141a3 on the absent symbol."""
    res = [{"url": "https://ok.example/a", "title": "T" * 999,
            "snippet": "s" * 99_999, "date": "2026-08-02"}]
    kept, truncated, dropped = analysis.clamp_sonar_results(res)
    assert (truncated, dropped) == (1, 0)
    assert len(kept[0]["title"]) == analysis.SONAR_TITLE_MAX_CHARS
    assert len(kept[0]["snippet"]) == analysis.SONAR_SNIPPET_MAX_CHARS
    # Fields the clamp does not own survive untouched — a byte clamp must not
    # quietly become a schema filter.
    assert kept[0]["date"] == "2026-08-02"
    assert kept[0]["url"] == "https://ok.example/a"


def test_a_short_honest_result_passes_through_byte_identical():
    """The clamp must be invisible on real data. CARRIED-INVARIANT in spirit
    but born RED at 38141a3 on the absent symbol (labelled, per
    ENGINEERING.md:122 — it is a new surface, not behaviour HEAD had)."""
    res = [{"url": "https://npr.org/story", "title": "A normal headline",
            "snippet": "A normal snippet.", "date": "2026-08-02"}]
    kept, truncated, dropped = analysis.clamp_sonar_results(res)
    assert (truncated, dropped) == (0, 0)
    assert kept == res


def test_the_drop_and_the_truncation_are_disclosed_on_the_status_line():
    """Honest degradation means the run SAYS SO — on the channel this function
    already owns (`sa.sonar_status`, persisted on the brief header and
    rendered in the run report). The DROP is named because a verification
    source went missing; truncated titles are named as a COUNT, because the
    shortened string is model-facing map furniture and per-title warnings
    would be vendor noise in a channel the reader's edition inherits.

    BORN RED at 38141a3: HEAD's status is 'ok — N results', full stop."""
    from newslens import discovery

    payload = {"usage": {"total_tokens": 100}, "search_results": [
        {"url": "https://ok.example/a", "title": "T" * 999, "snippet": "s"},
        {"url": "https://%s.example/b" % ("h" * 400), "title": "t",
         "snippet": "s"}]}
    calls = []

    def fake_call_sonar(key, prompt):
        calls.append(key)
        return payload

    old = discovery.call_sonar
    discovery.call_sonar = fake_call_sonar
    try:
        results, _cost, status = analysis._sonar_verify(
            "pplx-fake", "A story", ["a claim"])
    finally:
        discovery.call_sonar = old

    assert calls, "the pin never reached the call — it proves nothing"
    assert len(results) == 1
    assert "1 results" in status
    assert "dropped" in status and str(analysis.SONAR_HOST_MAX_CHARS) in status
    assert "truncated" in status and str(analysis.SONAR_TITLE_MAX_CHARS) in status


# --------------------------------------------------------------------------
# 4. THE MEMORY DOORS — all four of them
# --------------------------------------------------------------------------

LONG = "Global oil and shipping disruption in the Strait of Hormuz and beyond"
LONG_113 = (LONG + " and further afield still today")[:113].ljust(113, "x")


def test_the_verb_door_clamps_and_names_the_outcome(migrated_con):
    """`add_thread` is the CLI/UI verb surface. The clamp runs FIRST — ahead
    of lift_tombstone and the lower(topic) lookup — so the row it creates and
    the row a second call finds are the same row. The proof is idempotence:
    at HEAD a second add of the same long name would find nothing (the clamp
    did not exist), and with a clamp applied at the INSERT instead it would
    insert a SECOND row.

    BORN RED at 38141a3: the outcome vocabulary has no 'added-truncated'."""
    out = memory.add_thread(migrated_con, LONG_113)
    assert out == "added-truncated"

    stored = [r["topic"] for r in migrated_con.execute(
        "SELECT topic FROM memory").fetchall()]
    assert len(stored) == 1
    assert len(stored[0]) <= memory.TOPIC_MAX_CHARS

    again = memory.add_thread(migrated_con, LONG_113)
    assert again == "already-active", (
        "the clamped name did not resolve to its own row — the clamp is on "
        "the wrong side of the lookup")
    assert migrated_con.execute(
        "SELECT count(*) c FROM memory").fetchone()["c"] == 1


def test_the_follow_from_story_door_resolves_its_own_row(migrated_con):
    """The LOAD-BEARING door. `add_thread_at_altitude` keys a thread on a
    HEADLINE — the input most likely to be long — then looks the row up by
    lower(topic) to get its id.

    BORN RED at 38141a3 in the most useful way available: with a clamp in
    add_thread and none here, this raises TypeError ('NoneType' is not
    subscriptable) because the lookup misses the row the add just created.
    That failure mode is why the clamp is a shared helper called at every
    door rather than one buried in add_thread."""
    outcome, thread_id = memory.add_thread_at_altitude(
        migrated_con, LONG_113, altitude="narrow", source="seed",
        origin_story=LONG_113)
    assert outcome == "added-truncated"
    assert isinstance(thread_id, int)
    row = migrated_con.execute(
        "SELECT topic FROM memory WHERE id = ?", (thread_id,)).fetchone()
    assert len(row["topic"]) <= memory.TOPIC_MAX_CHARS


def test_the_file_door_clamps_before_the_diff_and_discloses(migrated_con):
    """memory.md is the third door. The clamp lands in `parse_file`, BEFORE
    the casefold key `plan_import` diffs on — otherwise every re-sync would
    re-insert the same thread, because the file's long name would never match
    the stored short one.

    And the shortening is DISCLOSED, unlike the vendor clamps: a thread name
    is the reader's own words, rendered back to him in memory.md and the
    Following spine, so storing 80 characters of an 84-character name he wrote
    is the product renaming his thread.

    BORN RED at 38141a3: parse_file returns the full-length topic and
    SyncResult has no truncated_topics."""
    entries = memory.parse_file(
        "# Threads\n\n## Active threads\n- %s\n" % LONG_113)
    assert len(entries) == 1
    assert len(entries[0]["topic"]) <= memory.TOPIC_MAX_CHARS
    assert entries[0]["truncated_from"] == LONG_113

    plan = memory.plan_import(migrated_con, entries)
    assert len(plan.inserts) == 1
    assert len(plan.truncated) == 1
    assert str(memory.TOPIC_MAX_CHARS) in plan.truncated[0]
    # A truncation is not a PENDING EDIT: counting it as one would flip a
    # clean bootstrap into a stale refusal on a file that agrees with the DB.
    empty = memory.plan_import(migrated_con, [])
    assert empty.is_empty


def test_a_re_sync_of_a_long_file_name_is_idempotent(migrated_con):
    """The regression this ordering exists to prevent, driven end to end
    through the plan: the second import must find the row the first created.

    BORN RED at 38141a3 on the absent 'truncated_from' key."""
    text = "# Threads\n\n## Active threads\n- %s\n" % LONG_113
    plan1 = memory.plan_import(migrated_con, memory.parse_file(text))
    result = memory.SyncResult()
    memory._apply_plan(migrated_con, plan1, result)
    assert len(result.added) == 1

    plan2 = memory.plan_import(migrated_con, memory.parse_file(text))
    assert plan2.inserts == [], (
        "the same file line inserted a second row — the clamp is downstream "
        "of the diff key")
    # The disclosure still fires on the re-sync: the file still says something
    # the database does not store, whether or not an edit results.
    assert len(plan2.truncated) == 1


def test_two_long_names_that_clamp_together_are_reported_as_the_collision(
        migrated_con):
    """The clamp's own hazard, pinned: truncation can make two distinct names
    identical. That is a duplicate, and the file has to hear about it rather
    than silently getting one thread.

    BORN RED at 38141a3: neither name is clamped, so they are two ordinary
    distinct topics and no problem is raised."""
    a = LONG_113
    b = LONG_113[:memory.TOPIC_MAX_CHARS] + " but a different tail entirely"
    with pytest.raises(memory.MemorySyncError) as exc:
        memory.parse_file(
            "# Threads\n\n## Active threads\n- %s\n- %s\n" % (a, b))
    assert "duplicate topic" in str(exc.value)
    assert "shortened" in str(exc.value)


def test_a_normal_thread_name_is_untouched_at_every_door(migrated_con):
    """The floor, at the doors. CARRIED-INVARIANT for the behaviour (a short
    name has always round-tripped) but born RED at 38141a3 on the absent
    `clamp_topic` symbol — labelled per ENGINEERING.md:122."""
    name = "Strait of Hormuz shipping"
    assert memory.clamp_topic(name) == (name, False)
    assert memory.add_thread(migrated_con, name) == "added"
    entries = memory.parse_file(
        "# Threads\n\n## Active threads\n- %s\n" % name)
    assert entries[0]["topic"] == name
    assert "truncated_from" not in entries[0]


def test_the_documented_at_the_clamps_ceilings_are_the_real_ones():
    """FIX LOOP 1 (QA F-5), RE-BASED IN NL-142 FIX LOOP 2. The clamp comments
    in analysis.py and memory.py quote a CEILING and a HEADROOM for each clamp.
    This pin makes those numbers executable: it walks each CONSTANT until the
    worst case breaches and asserts the headroom the comments claim. If a
    margin, a cap or a render door moves, this fails and the comments get
    re-derived instead of quietly going stale.

    WHICH REGIME, and why it moved. "At the clamps" means every clamped field
    at its clamp — and when this pin was written the INGEST fields had no
    clamps, so it held them at their observed maxima and read 76 / 38 / 20.
    NL-142 clamped them, and the NL-142 gate then found that the vendor host
    reaches the same map line as an R-key label (F-G0). Both changes move this
    regime, so the walk now runs on NL-142's worst case — imported rather than
    restated, because "neither derivation moves underneath the other" is
    exactly the assumption F-G0 refuted. The measured headrooms fall to 13 and
    3: the vendor clamps are FOUR TIMES closer to their ceilings than this
    pin's own first version reported, which is the honest reading and the one
    a future re-derivation must budget against.

    The HOST row is retired rather than re-measured — see
    `test_the_sonar_host_is_inside_the_label_budget_and_dropped_past_the_dns_max`:
    the label budget covers R labels now, so no vendor host breaches this bound
    at any length, and asserting a ceiling that does not exist would be
    fabricating one.

    BEHAVIOUR-RED at the pre-fix tree: the worst case breaches AT the shipped
    values there (the F-G0 state), so every walk returns the constant itself
    and the measured headroom reads -1.
    """
    from test_nl142_ingest_bounds import REACHABLE_HOST, _worst

    def first_breach(module, field, span=600):
        """Walk the CONSTANT at NL-142's saturating worst case: ingest fields
        past their clamps, the cluster at its cap, the Sonar keys on the
        cluster's own host (the binding sibling branch)."""
        old = getattr(module, field)
        try:
            for v in range(old, old + span):
                setattr(module, field, v)
                worst, bound = _worst(title=100_000, host=REACHABLE_HOST,
                                      outlet=100_000)
                if worst > bound:
                    return v
        finally:
            setattr(module, field, old)
        return None

    # (module, constant, documented headroom above it) — the comments' claims.
    documented = [
        (analysis, "SONAR_TITLE_MAX_CHARS", 13),
        (memory, "TOPIC_MAX_CHARS", 3),
    ]
    for module, field, headroom in documented:
        clamp = getattr(module, field)
        breach = first_breach(module, field)
        assert breach is not None, (
            "%s: no breach found — the bound stopped depending on this clamp, "
            "so RE-DERIVE it rather than leaving it in place" % field)
        assert breach - 1 - clamp == headroom, (
            "%s clamp %d: documented headroom %d, measured %d (ceiling %d, "
            "first breach %d). Re-derive the comment in place — the shipped "
            "regime moved." % (field, clamp, headroom, breach - 1 - clamp,
                               breach - 1, breach))

    # The host has no ceiling to document any more, and that is a claim too.
    # The span deliberately covers 292 — the first breach this comment used to
    # document — so the retirement is refuted at the exact value it replaces,
    # rather than by a scan long enough to be a proof by exhaustion. The
    # unbounded half of the claim is the flatness assert in
    # `test_the_sonar_host_is_inside_the_label_budget_and_dropped_past_the_dns_max`.
    assert first_breach(analysis, "SONAR_HOST_MAX_CHARS", span=40) is None, (
        "the vendor host reaches the bound again — F-G0's door re-opened")
