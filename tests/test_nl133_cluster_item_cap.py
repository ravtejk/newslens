"""NL-133 — the per-cluster item cap that turns PROMPT_MARGIN_CHARS from an
allowance into a bound.

Chartered by the NL-130 gate (ruling R-B / commit constraint C-4: "the
structural end of the quadratic-source-map class"). The class: `render_source_map`
names every same-outlet sibling on every key's own line, so the map is O(n^2) in
same-outlet key count, and nothing upstream bounded how many items one cluster
could claim out of the 550-item pool. A validation-passing payload could
therefore drive the analysis prompt past the bound rung 1 prices it against,
which re-opens the pay-then-skip sliver the 2026-08-01 ordering ruling closed.

Every worst case below is CONSTRUCTED FROM THE SHIPPED CONSTANTS and rendered
through the REAL constructors — no hand-frozen char counts. That is deliberate:
the drift class this batch inherits (NL-118's 4.33-char surviving margin, NL-130's
two-addresses lesson) is exactly what a pinned literal reproduces.
"""
import pytest

from newslens import analysis, memory, paths, ranking


# --------------------------------------------------------------------------
# The worst case, built from constants
# --------------------------------------------------------------------------
# All-time observed field maxima, swept read-only from the founder DB
# (11,198 source_items / 175 cluster records) on 2026-08-02. They are the
# bound's stated PRECONDITION, not decoration: the cap bounds cluster SHAPE,
# these bound per-key LENGTH, and the margin is a ceiling only under both.
#
# NL-139 (2026-08-02, gate ruling R-B) converted two of those preconditions
# into ENFORCED clamps, so this pin's worst case now reads them from the
# shipped constants instead of from an observation: `analysis.
# SONAR_TITLE_MAX_CHARS` for vendor titles and `memory.TOPIC_MAX_CHARS` for
# thread names. The numbers below are LARGER than the observations they
# replace (200 > 183, 80 > 64) — the worst case measured here therefore grew,
# on purpose, and is now a bound rather than a bet. INGEST's maxima stay
# observations, because ingest is the one residue owner still unclamped.
TITLE_MAX = 183      # max(len(source_items.title)) — INGEST, still unclamped
OUTLET_MAX = 41      # max(len(source_items.outlet)) — his own sources.yaml
HOST_MAX = 27        # max url host length — INGEST, still unclamped
SONAR_TITLE = analysis.SONAR_TITLE_MAX_CHARS   # NL-139: enforced, not observed
TOPIC_MAX = memory.TOPIC_MAX_CHARS             # NL-139: enforced, not observed


def _worst_source_map(n_items, n_priors=None):
    """The most expensive source map `n_items` in ONE cluster can render.

    Worst on every axis at once: every key full-text (the longest `kind`
    string), every key on ONE host (maximal sibling lists — the quadratic),
    every title/outlet at its all-time maximum, plus the Sonar results
    `_sonar_verify` admits landing on that SAME host (so they join the sibling
    list rather than starting their own), plus the prior-briefing keys.

    NL-139: the Sonar titles and the thread names are now AT THEIR CLAMPS
    rather than at an observed maximum, and the count is read from
    `analysis.SONAR_MAX_RESULTS` rather than typed as 8. The other branch of
    the sibling quadratic — Sonar keys holding a maximal host of their OWN,
    which is the binding one once titles are clamped — is measured in
    tests/test_nl139_byte_clamps.py, where the host clamp that closes it lives.
    """
    if n_priors is None:
        n_priors = memory.CONTEXT_CAP
    host = "h" * (HOST_MAX - 4) + ".com"
    title, outlet = "T" * TITLE_MAX, "O" * OUTLET_MAX
    items = [{"outlet": outlet, "url": "https://%s/%d" % (host, i),
              "title": title, "raw_excerpt": "x" * 4000,
              "fetched_at": "2026-08-02T00:00Z", "published_at": "2026-08-02",
              "source_name": outlet, "tier": "full"} for i in range(n_items)]
    records = [analysis.FetchRecord(
        url=it["url"], source_name=outlet, tier="full", outcome=analysis.OK,
        attempted=True, title=title, text="x" * 4000) for it in items]
    sonar = [{"url": "https://%s/s%d" % (host, i), "title": "T" * SONAR_TITLE,
              "date": "2026-08-02", "snippet": "s" * 4000}
             for i in range(analysis.SONAR_MAX_RESULTS)]
    priors = [{"date": "2026-08-02", "text": "p" * 4000, "thread": "M" * TOPIC_MAX}
              for _ in range(n_priors)]
    return analysis.build_source_map(records, items, sonar, priors)


def _worst_prompt_chars(n_items, n_priors=None):
    """That map, rendered into the real analysis prompt. Returns
    (prompt_chars, bound_chars) — the two numbers rung 1's guarantee compares."""
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    sources = _worst_source_map(n_items, n_priors)
    prompt = analysis._render_prompt(template, {
        "word_budget": str(analysis.word_budget_for("full")),
        "tier": "medium", "date": "2026-08-02", "slot": "1",
        # validate_payload's own hard caps on the two model-authored scalars
        "story_title": "S" * 300, "story_summary": "U" * 400,
        "memory_context": "\n".join(["M" * TOPIC_MAX] * memory.CONTEXT_CAP),
        "source_map": analysis.render_source_map(sources),
        "material": analysis.render_material(sources)})
    return len(prompt), analysis.brief_bound_chars(template)


def _payload(item_ids, matched_memory=None):
    return {"clusters": [{
        "story_title": "One big story", "summary": "s", "item_ids": list(item_ids),
        "matched_tags": [], "matched_memory": list(matched_memory or []),
        "world_impact": 5, "world_impact_reason": "r"}]}


# --------------------------------------------------------------------------
# 1. The bound itself — born red on BEHAVIOUR at f0ad0bc
# --------------------------------------------------------------------------

def test_a_validated_cluster_can_never_outrun_the_analysis_prompt_bound():
    """THE class-killer. A payload the ranker ACCEPTS must not be able to build
    a prompt the analyst's own affordability bound cannot pay for.

    63 same-outlet items is a validation-passing payload today (item_ids need
    only be known, non-empty and unreused — pool cap 550), and the NL-130 QA
    pass constructed exactly this shape. BORN RED at f0ad0bc on behaviour, not
    on a missing symbol: HEAD returns all 63 ids and the prompt they build
    measures ~91k against a ~78.6k bound.
    """
    ids = list(range(1, 64))
    kept = ranking.validate_payload(
        _payload(ids), set(ids), {}, [])[0]["item_ids"]
    prompt_chars, bound_chars = _worst_prompt_chars(len(kept))
    assert prompt_chars <= bound_chars, (
        "a validated %d-item cluster renders a %d-char prompt against a %d-char "
        "bound (over by %d) — rung 1 would under-price the brief and the "
        "ORDERING INVARIANT VIOLATED tripwire would fire"
        % (len(kept), prompt_chars, bound_chars, prompt_chars - bound_chars))


def test_the_duplicate_thread_route_into_the_same_quadratic_is_closed():
    """The SECOND route, found while deriving the bound and closed with it.

    Every matched_memory entry becomes a prior-briefing key, and all P keys
    share one outlet identity — so a repeated (perfectly valid) topic name
    inflates the same sibling list the item cap bounds. Nothing capped the
    LIST, only its vocabulary. BORN RED at f0ad0bc on behaviour: 80 repeats of
    one listed topic survive validation and render a ~46k-char source map with
    ZERO cluster items.
    """
    topics = ["ai regulation"]
    cluster = ranking.validate_payload(
        _payload([1], ["ai regulation"] * 80), {1}, {}, topics)[0]
    prompt_chars, bound_chars = _worst_prompt_chars(
        0, n_priors=len(cluster["matched_memory"]))
    assert prompt_chars <= bound_chars, (
        "%d matched_memory entries survived validation and render a %d-char "
        "prompt against a %d-char bound"
        % (len(cluster["matched_memory"]), prompt_chars, bound_chars))
    assert cluster["matched_memory"] == topics, (
        "de-duplication must keep first-mention order and the vocabulary "
        "itself, not merely shorten the list: %r" % (cluster["matched_memory"],))


def test_the_trim_costs_the_ranker_no_outlet_and_no_corroboration():
    """WHICH items survive is the design call the NL-130 gate left to this
    batch. The rule: round-robin across outlets, model order inside each.

    The shape that makes the naive head-slice wrong — 59 items from one outlet
    and ONE item from a second, the lone item sorted last by the model. Under
    "keep the first 48" that outlet disappears and `corroborate`'s distinct-
    outlet count (the trust label the reader sees) silently drops by one.
    NEW-SURFACE pin (labelled, per ENGINEERING.md:122): at f0ad0bc this fails
    on the absent `item_outlets` parameter, BEFORE reaching the bound assert —
    so it is NOT behaviour currency, even though HEAD does also breach the
    bound on this shape. The class-killer above carries that proof.
    """
    ids = list(range(1, 61))
    outlets = {i: "Reuters" for i in ids}
    outlets[60] = "AP"                      # the lone second-outlet item, LAST
    kept = ranking.validate_payload(
        _payload(ids), set(ids), {}, [], item_outlets=outlets)[0]["item_ids"]
    prompt_chars, bound_chars = _worst_prompt_chars(len(kept))
    assert prompt_chars <= bound_chars, (
        "%d items survived — %d-char prompt vs %d-char bound"
        % (len(kept), prompt_chars, bound_chars))
    assert 60 in kept, (
        "the lone AP item was dropped — the trim cost the cluster an outlet, "
        "which costs corroboration: kept %d items, outlets %r"
        % (len(kept), sorted({outlets[i] for i in kept})))
    assert {outlets[i] for i in kept} == {outlets[i] for i in ids}, (
        "distinct-outlet set changed across the trim — `corroborate` counts "
        "exactly this set, so the published trust label would move")
    assert kept == sorted(kept), "the trim must not reorder the model's list"


def test_the_trim_is_disclosed_the_same_way_every_other_repair_is():
    """Honest degradation means the run SAYS SO. The trim rides the disclosure
    channel `repair_duplicate_ids` established: an out-param record, folded
    into `repairs`, surfaced as a run warning AND persisted in
    ranking_runs.meta.repairs.

    NEW-SURFACE pin (labelled, per ENGINEERING.md:122): at f0ad0bc this fails
    on the absent `truncations` parameter, not on behaviour — there is no trim
    at HEAD to disclose. Its bite is proven by mutation, not by the HEAD run.
    """
    ids = list(range(1, 61))
    outlets = {i: ("Reuters" if i % 2 else "AP") for i in ids}
    trims = []
    ranking.validate_payload(_payload(ids), set(ids), {}, [],
                             item_outlets=outlets, truncations=trims)
    assert len(trims) == 1, "an over-cap trim must produce exactly one record"
    rec = trims[0]
    assert rec["kept"] == ranking.MAX_CLUSTER_ITEMS
    assert rec["kept"] + rec["dropped"] == len(ids)
    assert rec["outlets_before"] == rec["outlets_after"] == 2, (
        "the record must carry the diversity claim as a MEASUREMENT of this "
        "trim, not as an assertion: %r" % (rec,))
    assert rec["cluster"] == "One big story"

    # An at-cap cluster is silent: disclosure is for events, not for weather.
    quiet = []
    ranking.validate_payload(
        _payload(ids[:ranking.MAX_CLUSTER_ITEMS]), set(ids), {}, [],
        item_outlets=outlets, truncations=quiet)
    assert quiet == []


def test_raising_the_cap_without_the_margin_breaks_this_test_first():
    """The cap -> bound COUPLING, derived from the shipped constants so a future
    edit cannot quietly re-open the class.

    `MAX_CLUSTER_ITEMS` and `PROMPT_MARGIN_CHARS` live in different modules and
    are only related by this arithmetic; nothing else in the tree would notice
    the two drifting apart. Both directions are pinned — raising the cap, or
    lowering the margin, lands here. The +2 arm is what makes the cap's VALUE
    load-bearing rather than decorative: it proves the number was chosen
    against the bound and not picked for looking round.

    NEW-SURFACE pin (labelled): at f0ad0bc this fails on the absent
    `MAX_CLUSTER_ITEMS`, not on behaviour.
    """
    at_cap, bound = _worst_prompt_chars(ranking.MAX_CLUSTER_ITEMS)
    assert at_cap <= bound, (
        "MAX_CLUSTER_ITEMS=%d renders %d chars against a %d-char bound (over by "
        "%d). Either lower the cap or raise analysis.PROMPT_MARGIN_CHARS — and "
        "note the margin is a MONEY guard: raising it raises brief_bound_usd, "
        "so more slots skip under exhaustion."
        % (ranking.MAX_CLUSTER_ITEMS, at_cap, bound, at_cap - bound))

    # ...and the cap is genuinely AT the edge — two more items breach. This is
    # what makes the value load-bearing rather than round. (`prompt - bound` is
    # invariant to template length: both sides carry len(template), so a
    # principal edit to analysis_brief.txt cannot move this arm.)
    over, _ = _worst_prompt_chars(ranking.MAX_CLUSTER_ITEMS + 2)
    assert over > bound, (
        "the cap is no longer the binding constraint (%d items still fit in %d "
        "chars) — the margin grew, so RE-DERIVE the cap instead of leaving the "
        "product truncating clusters it no longer needs to"
        % (ranking.MAX_CLUSTER_ITEMS + 2, bound))


@pytest.mark.parametrize("n_outlets", [1, 2, 5, 18])
def test_the_trim_is_deterministic_and_leaves_the_cluster_at_the_cap(n_outlets):
    """Same input, same output, every time — and always exactly at the cap.
    18 is the real all-time maximum distinct outlets in one cluster
    (19 on the 2026-08-02 post-ingest copy — join-time quantity; the
    parametrize value stays 18, any value <= cap exercises the property).

    NEW-SURFACE pin (labelled): fails at f0ad0bc on `MAX_CLUSTER_ITEMS`.
    """
    ids = list(range(1, 121))
    outlets = {i: "outlet-%d" % (i % n_outlets) for i in ids}
    runs = [ranking.validate_payload(_payload(ids), set(ids), {}, [],
                                     item_outlets=outlets)[0]["item_ids"]
            for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert len(runs[0]) == ranking.MAX_CLUSTER_ITEMS
    assert len({outlets[i] for i in runs[0]}) == n_outlets


def test_the_cap_never_bites_a_shape_this_product_has_ever_produced():
    """The floor half of the design. 44 items in one cluster is the all-time
    maximum across 175 cluster records (`briefings` + `briefings_history`,
    swept read-only 2026-08-02); the next-largest is 26. A cap that truncated
    real editions would be trading honesty for arithmetic.

    NEW-SURFACE pin (labelled): fails at f0ad0bc on `MAX_CLUSTER_ITEMS`.
    """
    observed_all_time_max = 44
    assert ranking.MAX_CLUSTER_ITEMS > observed_all_time_max
    ids = list(range(1, observed_all_time_max + 1))
    kept = ranking.validate_payload(
        _payload(ids), set(ids), {}, [],
        item_outlets={i: "Reuters" for i in ids})[0]["item_ids"]
    assert kept == ids, "the largest real cluster ever produced was trimmed"


def test_the_trim_never_launders_a_bad_payload_past_validation():
    """The trap in putting a repair next to a validator: an id that would have
    been DROPPED must still be REJECTED. Both rejections keep reading the
    model's full list.

    CARRIED-INVARIANT pin (born GREEN at f0ad0bc — labelled explicitly per
    ENGINEERING.md:122, and verified green by the HEAD-mirror run rather than
    assumed). It is the regression guard for this batch's most dangerous
    failure mode: both rejections read `ids` BEFORE the trim rebinds it, and a
    later refactor that moved the trim up one block would silently start
    laundering invented and re-used ids. Nothing else in the suite would notice.
    """
    known = set(range(1, 200))
    ids = list(range(1, 120)) + [9999]           # invented id, past the cap
    with pytest.raises(ValueError, match=r"invented item_ids \[9999\]"):
        ranking.validate_payload(_payload(ids), known, {}, [])

    # Cross-cluster re-use of an id THIS cluster dropped: still a violation.
    dropped_id = 119
    payload = {"clusters": [
        {"story_title": "a", "summary": "s", "item_ids": list(range(1, 120)),
         "matched_tags": [], "matched_memory": [], "world_impact": 5,
         "world_impact_reason": "r"},
        {"story_title": "b", "summary": "s", "item_ids": [dropped_id],
         "matched_tags": [], "matched_memory": [], "world_impact": 5,
         "world_impact_reason": "r"}]}
    with pytest.raises(ValueError, match=r"already used by another cluster"):
        ranking.validate_payload(payload, known, {}, [])
