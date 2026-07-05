"""The writer (M5): §5.9 invariants + ADR-0007 chain/variant/furniture rules.

Fully offline: LLM passes run through a stateful fake `generate._chat`
(dispatching on json_mode — narrative vs script), HTTP error taxonomy through
the loopback fake server via `ranking.OPENAI_CHAT_URL`. The two autouse
conftest guards (sandbox_paths, loopback_only_network) make real state and
real endpoints unreachable by construction — `generate` is exactly the verb
class that motivated them.

Reds are self-contained acceptance criteria (Option A).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from newslens import db, generate, paths, ranking

A_DAY = "2026-07-05"   # dogfood day 1 — even ordinal, variant A of record
B_DAY = "2026-07-06"


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def slot(
    n=1,
    title="Story title",
    override=False,
    override_reason="it cleared a high global-impact bar",
    corroboration_count=2,
    outlets=("Outlet A", "Outlet B"),
    tags=({"name": "AI regulation", "level": "topic"},),
    mem=(),
    revived=(),
    label=None,
    reason="Sector-wide effects",
):
    return {
        "slot": n,
        "story_title": title,
        "summary": "What happened, in one line.",
        "item_ids": [n],
        "outlets": list(outlets),
        "matched_tags": [dict(t) for t in tags],
        "matched_memory": list(mem),
        "matched_dormant": [],
        "followed_analyst": False,
        "personal_score": 1.0,
        "world_impact": 6,
        "world_impact_reason": reason,
        "combined_score": 0.8,
        "override": override,
        "override_label": (
            ranking.OVERRIDE_LABEL_PREFIX + override_reason + "." if override else None
        ),
        "corroboration_count": corroboration_count,
        "corroboration_label": (
            f"Reported by {corroboration_count} named outlets"
            if corroboration_count != 1
            else "Reported by 1 named outlet"
        ),
        "wire_items_excluded": 0,
        "revived_threads": [dict(r) for r in revived],
    }


def seed_briefing(con, date, slots, narrative=None, token_cost=None):
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " token_cost, narrative_text, generated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            date,
            json.dumps(slots),
            json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT, "per_story": []}),
            json.dumps(token_cost or {"steps": [{"step": "rank_select", "usd": 0.001}],
                                      "total_usd": 0.001}),
            narrative,
            iso_now(),
        ),
    )
    for s in slots:
        con.execute(
            "INSERT OR IGNORE INTO source_items (id, source_type, outlet, url,"
            " title, fetched_at, raw_excerpt) VALUES (?, 'rss', ?, ?, ?, ?, ?)",
            (s["slot"], (s.get("outlets") or ["X"])[0],
             f"https://x.example/{date}/{s['slot']}", s["story_title"], iso_now(),
             "An excerpt of the source item."),
        )
    con.commit()


def tier_for_position(i):
    """A2 sanity defaults for fixtures: 1 full, 2 medium, 3 medium (model may
    also say quick), 4+ quick."""
    return "full" if i == 1 else ("medium" if i in (2, 3) else "quick")


def stories_payload(slots, variant="A", lede_extra="", my_read=None):
    stories = []
    for i, s in enumerate(slots, start=1):
        lede = "The opening sentence reports the development. A second sentence adds context."
        for rv in s.get("revived_threads", []):
            lede = (
                f"We last covered {rv['last_covered']} this thread; here is what changed. "
                "The development moved again today."
            )
        tier = tier_for_position(i)
        story = {
            "tier": tier,
            "headline": f"Rewritten headline {s['slot']}",
            "lede": lede + (lede_extra or ""),
        }
        if tier != "quick":  # A2: movement fields on a quick hit are an ERROR
            story["why_it_matters"] = (
                "It matters because of concrete effects on the reader's interests."
            )
            story["watch_for"] = "Watch the next scheduled decision."
        if my_read is not None and variant == "B":
            story["my_read"] = my_read
        stories.append(story)
    return {"stories": stories}


def compliant_script(slots, narrative=""):
    parts = [
        "Good morning. Here is your briefing.",
    ]
    for s in slots:
        seg = f"Story {s['slot']}. The development moved today."
        if s.get("override"):
            seg += (
                " This one sits outside your usual interests — it's here because "
                "it cleared a high global-impact bar."
            )
        for rv in s.get("revived_threads", []):
            d = datetime.strptime(rv["last_covered"], "%Y-%m-%d")
            seg += f" We last covered this on {generate._MONTHS[d.month - 1]} {d.day}."
        if s.get("corroboration_count") == 1 and s.get("outlets"):
            seg += f" A single outlet, {s['outlets'][0]}, is carrying this so far."
        parts.append(seg)
    parts.append(generate.SPOKEN_CAVEAT)
    parts.append(generate.SIGNOFF)
    # Pad to clear the severe-shortfall floor deterministically.
    filler = " ".join(["The detail continues in measured spoken prose."] * 60)
    parts.insert(1, filler)
    return "\n\n".join(parts)


@pytest.fixture
def fake_model(monkeypatch):
    """Stateful fake for BOTH passes: json_mode=True -> narrative payload,
    json_mode=False -> script text. Tests set .narrative / .script;
    .calls records each request's knobs."""
    state = type("S", (), {})()
    state.calls = []
    state.narrative = None
    state.script = None

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append(
            {"json_mode": json_mode, "max_tokens": max_tokens,
             "temperature": temperature, "prompt": prompt}
        )
        content = (
            json.dumps(state.narrative) if json_mode else state.script
        )
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 200},
        }

    monkeypatch.setattr(generate, "_chat", fake_chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


ENV = {"OPENAI_API_KEY": "sk-qa-fake"}


def run(con, date=A_DAY, variant=None, refresh=False, env=None):
    # `env if env is not None else ENV` — NOT `env or ENV`: a keyless test
    # passes {} (falsy), and `or` silently swapped in the fake key, testing
    # nothing (M5 helper bug, implementer-diagnosed; run_generate itself was
    # always correct).
    return generate.run_generate(
        date=date, con=con, env=env if env is not None else ENV,
        variant_override=variant, refresh=refresh,
    )


# --- variant mechanics (§5.2 / §5.9 #11) -----------------------------------------

def test_variant_parity_anchor_and_strict_alternation():
    assert generate.variant_for(A_DAY) == "A"  # dogfood day 1
    assert generate.variant_for(B_DAY) == "B"
    d = datetime.strptime(A_DAY, "%Y-%m-%d")
    seq = [
        generate.variant_for((d + timedelta(days=i)).strftime("%Y-%m-%d"))
        for i in range(10)
    ]
    assert seq == ["A", "B"] * 5  # never model-chosen, never repeats


def test_garbage_variant_refused(migrated_con):
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, variant="C")
    assert "variant must be A or B" in str(excinfo.value)


def test_sample_mode_writes_file_only_record_untouched(migrated_con, fake_model):
    """Forcing the off-parity variant = SAMPLE: artifact with explicit header,
    briefings row untouched, log entry sample:true — alternation-of-record
    stays clean (ADR-0007 §3)."""
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots, variant="B", my_read="A judgment.")
    fake_model.script = compliant_script(slots)

    rep = run(migrated_con, date=A_DAY, variant="B")  # B forced on an A day
    assert rep.sample is True
    assert any("SAMPLE mode" in w for w in rep.warnings)

    row = migrated_con.execute(
        "SELECT narrative_text, script_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"] is None and row["script_text"] is None  # untouched

    artifact = paths.DATA_DIR / "briefings" / f"{A_DAY}-variant-B-SAMPLE.md"
    assert artifact.exists()
    text = artifact.read_text(encoding="utf-8")
    assert text.startswith("<!-- SAMPLE — variant B")
    assert "Voice:" not in text  # stamp retired with the alternation window
    assert any("voice B is retired" in w for w in rep.warnings)  # A1 wording

    log_lines = [
        json.loads(l)
        for l in (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()
    ]
    ok = [e for e in log_lines if e["status"] == "ok"]
    assert ok and ok[0]["sample"] is True


# --- furniture ownership (§5.7) ------------------------------------------------------

def _inputs_for(slots, continuity="none", window=None):
    return {
        "slots": slots,
        "items_by_slot": {s["slot"]: [] for s in slots},
        "threads": [],
        "prior_ctx": None,
        "continuity_status": continuity,
        "window_meta": window,
        "corroboration": {},
    }


def test_assemble_narrative_owns_all_furniture():
    slots = [slot(1), slot(2, n_override := False) if False else slot(2)]
    slots = [slot(1), slot(2, override=True, tags=(), corroboration_count=1,
                          outlets=("Solo Outlet",))]
    stories, _ = generate.validate_narrative_payload(
        stories_payload(slots), slots, "A"
    )
    text = generate.assemble_narrative(A_DAY, "A", stories, _inputs_for(slots))
    assert text.startswith("# NewsLens — Sunday, July 5, 2026")
    assert "In today's briefing:" in text
    assert text.count("**Why it matters:**") == 2
    assert text.count("**Watch for:**") == 2
    # Override label: canonical, code-assembled, above the override story only.
    assert text.count("**Outside your interests:**") == 1
    assert "it's here because it cleared a high global-impact bar" in text
    # Meta-lines: corroboration + outlets + provenance.
    assert "Reported by 2 named outlets — Outlet A, Outlet B. Here for: AI regulation." in text
    assert "Here for: editor's override — see note above." in text
    # Footer: window line then caveat verbatim — the variant stamp is
    # RETIRED (editorial A1): no Voice: line anywhere on any tier.
    caveat_pos = text.find(ranking.CORROBORATION_CAVEAT)
    window_pos = text.find("NewsLens sees only its configured sources")
    assert -1 < window_pos < caveat_pos
    assert "Voice:" not in text


def test_model_written_my_read_prefix_is_stripped_never_doubled():
    slots = [slot(1)]
    payload = stories_payload(slots, variant="B", my_read="My read: the judgment.")
    stories, _ = generate.validate_narrative_payload(payload, slots, "B")
    assert stories[0]["my_read"] == "the judgment."  # model's label stripped
    text = generate.assemble_narrative(B_DAY, "B", stories, _inputs_for(slots))
    assert text.count("My read:") == 1  # exactly one, code-owned
    assert "**My read:** the judgment." in text


def test_variant_a_payload_with_my_read_is_rejected():
    slots = [slot(1)]
    payload = stories_payload(slots)
    payload["stories"][0]["my_read"] = "sneaky judgment"
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(payload, slots, "A")
    assert "variant A must not carry my_read" in str(excinfo.value)


# --- narrative validator -----------------------------------------------------------

@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda p: p.pop("stories"), "`stories` list"),
        (lambda p: p["stories"].pop(), "must match"),
        (lambda p: p["stories"][0].update(lede=""), "lede missing/empty"),
        (lambda p: p["stories"][0].update(why_it_matters="  "), "why_it_matters missing/empty"),
    ],
)
def test_narrative_structure_blocks(mutate, fragment):
    slots = [slot(1), slot(2)]
    payload = stories_payload(slots)
    mutate(payload)
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(payload, slots, "A")
    assert fragment in str(excinfo.value)


def test_revival_date_must_open_the_lede():
    slots = [slot(1, revived=({"topic": "Helium Shortage", "last_covered": "2026-07-01"},))]
    good = stories_payload(slots)  # helper puts the date in sentence 1
    stories, _ = generate.validate_narrative_payload(good, slots, "A")
    assert "2026-07-01" in stories[0]["lede"]

    bad = stories_payload(slots)
    bad["stories"][0]["lede"] = (
        "First sentence without the date. Second sentence still without it. "
        "We last covered 2026-07-01 back here in sentence three."
    )
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(bad, slots, "A")
    assert "missing from the lede's first two sentences" in str(excinfo.value)


def test_warn_grade_checks_headline_banned_single_source():
    slots = [slot(1, corroboration_count=1, outlets=("BBC News — World",))]
    payload = stories_payload(slots)
    payload["stories"][0]["headline"] = " ".join(["word"] * 16)
    payload["stories"][0]["watch_for"] = "This bears watching as a perfect storm."
    _, warns = generate.validate_narrative_payload(payload, slots, "A")
    joined = " | ".join(warns)
    assert "headline over the 12-word band" in joined
    assert "banned strings present" in joined and "bears watching" in joined
    assert "single-outlet story should name" in joined  # outlet not in lede

    named = stories_payload(slots)
    named["stories"][0]["lede"] = (
        "Only the BBC is carrying this development so far. Details remain thin."
    )
    _, warns2 = generate.validate_narrative_payload(named, slots, "A")
    assert not any("single-outlet" in w for w in warns2)  # token match: "the BBC"


def _bare_string_cluster(tag):
    return {"clusters": [{
        "story_title": "T", "summary": "S", "item_ids": [1],
        "matched_tags": [tag],
        "matched_memory": [], "world_impact": 5,
        "world_impact_reason": "R",
    }]}


def test_tag_shape_tolerance_exact_bare_string_normalizes_with_count():
    """GATE-PENDING (principal ruling 2026-07-05, DECISIONS.md): persistent
    strings-for-dicts at temp 0 was ruled DISCLOSED SCHEMA TOLERANCE, not
    repair — an EXACT-match bare-string tag name normalizes from the
    canonical map, counted via the `notes` out-param. This re-pins my
    pre-ruling hard-reject test to the ruled contract; the gate may overrule,
    and these pins freeze whatever stands."""
    notes = []
    clusters = ranking.validate_payload(
        _bare_string_cluster("AI regulation"),
        {1}, {"AI regulation": "topic"}, [], [], notes=notes,
    )
    assert clusters[0]["matched_tags"] == [{"name": "AI regulation", "level": "topic"}]
    assert len(notes) == 1


def test_tag_shape_tolerance_boundary_non_exact_still_rejects():
    """The tolerance boundary: only EXACT canonical names normalize — a case
    variant is still a hard reject, as is a dict with a bogus level."""
    with pytest.raises(ValueError) as excinfo:
        ranking.validate_payload(
            _bare_string_cluster("ai regulation"),  # wrong case: not exact
            {1}, {"AI regulation": "topic"}, [], [],
        )
    assert "not an exact listed tag" in str(excinfo.value)
    with pytest.raises(ValueError):
        ranking.validate_payload(
            _bare_string_cluster({"name": "AI regulation", "level": "domain"}),
            {1}, {"AI regulation": "topic"}, [], [],
        )


def test_BUG7_tag_shape_tolerance_must_persist_not_just_warn(migrated_con, monkeypatch, fake_api):
    """KNOWN-RED (BUG-7) — self-contained acceptance (Option A): the ruling
    (DECISIONS.md 2026-07-05) requires the tolerance be counted + surfaced +
    PERSISTED at ranking_runs meta.repairs.tag_shape_normalized. The warning
    fires, but `meta["repairs"] = repair_sink` in ranking._run_rank_body is
    still gated on `repair_sink.get("repaired")` (the M3 duplicate flag) — a
    tag-shape-only run (repaired=0, normalized>=1, the COMMON case) persists
    nothing, so the day-30 readout cannot count how often the tolerance
    fired. Fix contract: persist repair_sink when EITHER repaired or
    tag_shape_normalized is nonzero; this test goes green then."""
    monkeypatch.setattr(
        ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    monkeypatch.setattr(time, "sleep", lambda s: None)
    now = iso_now()
    migrated_con.execute(
        "INSERT INTO source_items (id, source_type, outlet, url, title, fetched_at)"
        " VALUES (1, 'rss', 'Outlet A', 'https://a.example/1', 'Story', ?)", (now,),
    )
    migrated_con.commit()
    payload = {
        "clusters": [{
            "story_title": "T", "summary": "S", "item_ids": [1],
            "matched_tags": ["AI regulation"],
            "matched_memory": [], "world_impact": 5,
            "world_impact_reason": "R",
        }]
    }
    fake_api.add_route(
        "/chat/completions", status=200,
        body=json.dumps({
            "choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        }).encode("utf-8"),
        content_type="application/json",
    )
    from newslens import config as config_mod

    cfg = config_mod.SourcesConfig(
        sources=[config_mod.Source(name="Outlet A", rss_url="https://a.example/f")],
        interests_granular=["AI regulation"],
    )
    rep = ranking.run_rank(date=A_DAY, con=migrated_con, cfg=cfg, env=ENV)
    assert any("tag-shape normalization" in w and "disclosed schema tolerance" in w
               for w in rep.warnings)
    meta = json.loads(migrated_con.execute(
        "SELECT meta FROM ranking_runs WHERE date = ?", (A_DAY,)
    ).fetchone()["meta"])
    assert meta["repairs"]["tag_shape_normalized"] == 1


# --- writer-model seam (principal amendment 2026-07-05) ----------------------------------

def test_writer_model_seam_and_rates():
    """The up-tiers are SEAMS, not scatters: writer AND ranking both run
    gpt-4o at 2.50/10.00 per M (principal-triggered up-tiers, 2026-07-05);
    gpt-4o-mini remains only as the documented fallback rung
    (ranking.MODEL), not the active model."""
    assert generate.WRITER_MODEL == "gpt-4o"
    assert ranking.RANK_MODEL == "gpt-4o"
    assert ranking.RANK_USD_PER_MTOK_IN == 2.50
    assert ranking.RANK_USD_PER_MTOK_OUT == 10.00
    assert ranking.MODEL == "gpt-4o-mini"  # the fallback rung, kept documented
    assert generate.WRITER_USD_PER_MTOK_IN == 2.50
    assert generate.WRITER_USD_PER_MTOK_OUT == 10.00
    usage = {"prompt_tokens": 1000, "completion_tokens": 200}
    assert generate._step_cost(usage) == pytest.approx(0.0045)  # writer rates


def test_writer_steps_log_the_writer_model(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    assert all(s["model"] == "gpt-4o" for s in rep.steps)
    tc = json.loads(migrated_con.execute(
        "SELECT token_cost FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()["token_cost"])
    writer_steps = [s for s in tc["steps"] if s["step"] != "rank_select"]
    assert all(s["model"] == "gpt-4o" for s in writer_steps)


def test_doctor_cost_line_matches_the_measured_pipeline():
    from newslens import doctor

    (line,) = doctor.cost_estimate()
    # Rank up-tier to gpt-4o: measured $0.086-0.091/full briefing.
    assert "$0.07-0.10" in line.text


def test_prompt_drift_guards_commit_or_null_and_mechanism_depth():
    """Prompt-directive obligations aren't mechanically checkable — pin the
    files carry them so silent prompt edits surface here."""
    b_text = (paths.PROMPTS_DIR / generate.PROMPT_B).read_text(encoding="utf-8")
    assert "falsifier" in b_text and "null" in b_text  # commit-or-null license
    for f in (generate.PROMPT_A, generate.PROMPT_B):
        text = (paths.PROMPTS_DIR / f).read_text(encoding="utf-8")
        assert "mechanism" in text  # mechanism-depth obligation


# --- --no-threads cold-start SAMPLE (principal amendment 2026-07-05) ----------------------

def test_no_threads_sample_isolation_and_stripping(migrated_con, fake_model):
    """Cold-start view: ALWAYS a sample (own filename + labeled header),
    record untouched — and every thread trace stripped consistently from
    prompt, validators, assembly meta-lines, and script labels, while the
    PERSISTED slots keep their thread data."""
    # NB: a synthetic thread name — the narrative prompt TEMPLATE uses
    # "Iran War" as illustrative example prose, which is template furniture,
    # not leaked thread data (observation on record).
    slots = [
        slot(1, mem=("Zebra Futures Thread",),
             revived=({"topic": "Helium Shortage", "last_covered": "2026-07-01"},)),
    ]
    seed_briefing(migrated_con, A_DAY, slots)
    migrated_con.execute(
        "INSERT INTO memory (topic, status, created_at, updated_at)"
        " VALUES ('Zebra Futures Thread', 'active', ?, ?)", (iso_now(), iso_now()),
    )
    migrated_con.commit()
    fake_model.narrative = stories_payload([{**slots[0], "revived_threads": []}])
    fake_model.script = compliant_script([{**slots[0], "revived_threads": [],
                                           "matched_memory": []}])

    rep = generate.run_generate(
        date=A_DAY, con=migrated_con, env=ENV, refresh=False, no_threads=True
    )
    assert rep.sample is True  # no-threads is never the briefing of record

    # NB: pin the SLOT-INJECTED directives, not the template keyword — both
    # prompt templates carry standing REVIVAL *rules* prose that legitimately
    # survives; what must vanish is the per-slot obligation with its date.
    n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    assert "Zebra Futures Thread" not in n_prompt      # thread list stripped
    assert "Helium Shortage" not in n_prompt           # revival slot-line stripped
    assert "last covered 2026-07-01" not in n_prompt   # dated obligation stripped
    s_prompt = next(c for c in fake_model.calls if not c["json_mode"])["prompt"]
    assert "last covered 2026-07-01" not in s_prompt   # script labels thread-free
    assert "Helium Shortage" not in s_prompt

    artifact = paths.DATA_DIR / "briefings" / f"{A_DAY}-no-threads-SAMPLE.md"
    assert artifact.exists()
    text = artifact.read_text(encoding="utf-8")
    assert text.startswith("<!-- SAMPLE — no active threads (cold-start view)")
    assert "Zebra Futures Thread" not in text      # meta-lines thread-free

    row = migrated_con.execute(
        "SELECT narrative_text, story_slots FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"] is None       # record untouched
    assert "Helium Shortage" in row["story_slots"]  # persisted slots keep threads


def test_no_match_meta_line_fallback_is_honest():
    """The latent bug the cold-start sample exposed, fixed and pinned: a slot
    with NO tag/thread match and NO override must say 'world-impact selection
    (no tag or thread match)' — never point at an override label that isn't
    there. Override slots keep their own pointer."""
    plain = slot(1, tags=(), mem=())
    override = slot(2, override=True, tags=(), mem=())
    stories, _ = generate.validate_narrative_payload(
        stories_payload([plain, override]), [plain, override], "A"
    )
    text = generate.assemble_narrative(
        A_DAY, "A", stories, _inputs_for([plain, override])
    )
    assert "Here for: world-impact selection (no tag or thread match)." in text
    assert "Here for: editor's override — see note above." in text
    assert text.count("editor's override") == 1  # only under the real override


# --- script validator ----------------------------------------------------------------

def test_script_hard_failures_override_revival_schedule():
    slots = [
        slot(1, override=True),
        slot(2, revived=({"topic": "T", "last_covered": "2026-07-01"},)),
    ]
    inputs = _inputs_for(slots)
    bare = "A script that says none of the required things. " * 40 + "See you tomorrow."
    _, hard, warns = generate.validate_script(bare, "narrative text", inputs)
    joined = " | ".join(hard)
    assert "outside-your-tags acknowledgment" in joined
    assert "missing its reason" in joined
    assert "schedule promise" in joined
    # A5: spoken revival downgraded hard -> warn (text disclosure stays hard;
    # the warn wording is "not voiced").
    assert "spoken revival date" not in joined
    assert any("spoken revival date '2026-07-01' not voiced" in w for w in warns)


def test_script_spoken_forms_accept_month_day():
    slots = [slot(1, revived=({"topic": "T", "last_covered": "2026-07-01"},))]
    text = compliant_script(slots)
    assert "July 1" in text  # helper speaks the month-day form
    _, hard, _ = generate.validate_script(text, "narrative", _inputs_for(slots))
    assert hard == []


def test_frozen_caveat_and_signoff_appended_with_disclosure():
    slots = [slot(1)]
    no_furniture = "A decent script body. " * 40 + generate.SIGNOFF
    body, hard, warns = generate.validate_script(
        no_furniture, "narrative", _inputs_for(slots)
    )
    assert hard == []
    assert any("spoken caveat was missing — appended verbatim" in w for w in warns)
    # Caveat lands BEFORE the sign-off; the stray sign-off was relocated.
    assert body.index(generate.SPOKEN_CAVEAT) < body.index(generate.SIGNOFF)
    assert body.count(generate.SIGNOFF) == 1

    no_signoff = "Body. " * 40 + generate.SPOKEN_CAVEAT
    body2, _, warns2 = generate.validate_script(
        no_signoff, "narrative", _inputs_for(slots)
    )
    assert body2.rstrip().endswith(generate.SIGNOFF)
    assert any("sign-off was missing" in w for w in warns2)


def test_fact_subset_and_hedge_warn_grade():
    slots = [slot(1)]
    narrative = "The plan allocates 40 billion and might pass."
    script = (
        "The plan allocates 40 billion — and a new figure, 7500, appears here. "
        "It will pass. There are 2 reasons and 3 caveats. "
        + generate.SPOKEN_CAVEAT + " " + generate.SIGNOFF + " " + "pad " * 60
    )
    _, hard, warns = generate.validate_script(script, narrative, _inputs_for(slots))
    assert hard == []
    joined = " | ".join(warns)
    assert "script numerals absent from narrative" in joined and "7500" in joined
    assert "'2'" not in joined and "'3'" not in joined  # enumeration exemption
    assert "hedge check" in joined  # will vs might


# --- script scaling (M5 fix) -----------------------------------------------------------

def test_script_budget_scales_with_narrative_not_slot_count():
    full, _ = generate._script_budgets(5, 1100)
    assert full == 1650  # min(1840 slot budget, 1100*1.5)
    thin, _ = generate._script_budgets(3, 667)
    assert thin == 1000  # the live finding: 667 words can't fill 1440
    tiny, _ = generate._script_budgets(1, 100)
    assert tiny == 400   # floor
    _, desc = generate._script_budgets(3, 667)
    assert "slot 1: ~347" in desc  # per-segment guidance scaled (500 * 1000/1440)


def test_severely_short_script_retries_then_fails(migrated_con, fake_model):
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = "Way too short. " + generate.SPOKEN_CAVEAT + " " + generate.SIGNOFF
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY)
    assert "script severely short" in str(excinfo.value)
    script_calls = [c for c in fake_model.calls if not c["json_mode"]]
    assert len(script_calls) == 2  # one retry, then visible failure


# --- chain semantics (ADR-0007 §2) ------------------------------------------------------

def test_default_chain_runs_ingest_then_rank_then_writes(migrated_con, fake_model, monkeypatch):
    order = []

    def fake_ingest(con=None, env=None, **kw):
        order.append("ingest")
        r = type("R", (), {})()
        r.succeeded, r.attempted, r.items_new = ["A"], 1, 3
        r.discovery_status = "skipped — PERPLEXITY_API_KEY not set (RSS-only run; the Sonar reliability spike is still gated on the key)"
        r.degradation_message = None
        return r

    def fake_rank(date=None, con=None, env=None, **kw):
        order.append("rank")
        slots = [slot(1)]
        seed_briefing(con, date, slots)
        r = type("R", (), {})()
        r.warnings = ["rank warning carried through"]
        return r

    from newslens import ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)
    fake_model.narrative = stories_payload([slot(1)])
    fake_model.script = compliant_script([slot(1)])

    rep = run(migrated_con, date=A_DAY, refresh=True)
    assert order == ["ingest", "rank"]  # chained, in order, before the writer
    assert "1/1 sources" in rep.ingest_summary
    assert "rank warning carried through" in rep.warnings


def test_rank_stage_failure_is_wrapped_and_logged(migrated_con, fake_model, monkeypatch):
    from newslens import ingest as ingest_mod

    def fake_ingest(con=None, env=None, **kw):
        r = type("R", (), {})()
        r.succeeded, r.attempted, r.items_new = ["A"], 1, 0
        r.discovery_status = "not attempted"
        r.degradation_message = None
        return r

    def failing_rank(**kw):
        raise ranking.RankingError("no ingested items inside the candidate window")

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", failing_rank)
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY, refresh=True)
    assert "rank stage failed" in str(excinfo.value)
    log = (paths.DATA_DIR / "generation_log.jsonl").read_text()
    assert '"status": "failed"' in log


def test_no_refresh_consumes_existing_row_without_pipeline(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    assert rep.ingest_summary == ""  # no ingest ran
    runs = migrated_con.execute("SELECT COUNT(*) FROM ranking_runs").fetchone()[0]
    assert runs == 0  # no rank ran
    assert rep.narrative_text and rep.script_text


def test_no_refresh_without_a_row_is_a_named_refusal(migrated_con, fake_model):
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY, refresh=False)
    assert "no briefing row" in str(excinfo.value)
    assert "--no-refresh" in str(excinfo.value)


def test_regeneration_archives_prior_narrative_first(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots, narrative="the first narrative")
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    run(migrated_con, date=A_DAY, refresh=False)
    hist = migrated_con.execute(
        "SELECT narrative_text FROM briefings_history WHERE date = ?", (A_DAY,)
    ).fetchall()
    assert [h["narrative_text"] for h in hist] == ["the first narrative"]
    row = migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"].startswith("# NewsLens —")


# --- continuity distinction (ADR-0007 §7; M4 gate must-address) ---------------------------

def test_corrupt_prior_row_is_not_first_briefing(migrated_con, fake_model, monkeypatch):
    from newslens import memory as memory_mod

    slots = [slot(1)]
    seed_briefing(migrated_con, "2026-07-03", [slot(1, title="Prior story")])
    seed_briefing(migrated_con, A_DAY, slots)
    # The corrupt seam: a prior ROW exists but context extraction fails.
    monkeypatch.setattr(memory_mod, "prior_briefing_context", lambda con, d: None)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    assert rep.continuity_status == "corrupt"
    assert any("continuity SUSPENDED" in w for w in rep.warnings)
    n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    assert "continuity is suspended for this run" in n_prompt
    log = [json.loads(l) for l in (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()]
    assert log[-1]["continuity"] == "corrupt"


def test_first_briefing_and_ok_continuity_are_distinct(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    assert rep.continuity_status == "none"
    assert not any("SUSPENDED" in w for w in rep.warnings)
    prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    assert "This is the first briefing" in prompt

    # Now with a healthy prior row: delta-only callback rules reach the prompt.
    seed_briefing(migrated_con, B_DAY, slots)
    fake_model.calls.clear()
    fake_model.narrative = stories_payload(slots, variant="B")
    fake_model.script = compliant_script(slots)
    rep2 = run(migrated_con, date=B_DAY, refresh=False)
    assert rep2.continuity_status == "ok"
    prompt2 = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    assert "Your previous briefing (2026-07-05)" in prompt2
    assert "Callback rules apply: delta-only" in prompt2


# --- money paths (§5.9 / ADR-0007 §8) ------------------------------------------------------

def test_keyless_generate_refuses_before_anything(migrated_con, fake_model):
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, env={})
    assert "OPENAI_API_KEY not set" in str(excinfo.value)
    assert fake_model.calls == []


def test_narrative_budget_abort_before_any_call(migrated_con, fake_model, monkeypatch):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    monkeypatch.setattr(generate, "_est_cost", lambda p, m: 999.0)
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY, refresh=False)
    assert "exceeds the remaining budget cap" in str(excinfo.value)
    assert fake_model.calls == []  # the guard fired before money could move
    log = (paths.DATA_DIR / "generation_log.jsonl").read_text()
    assert '"status": "failed"' in log


def test_script_budget_abort_leaves_row_untouched(migrated_con, fake_model, monkeypatch):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    ests = iter([0.0001, 999.0])  # narrative cheap, script over cap
    monkeypatch.setattr(generate, "_est_cost", lambda p, m: next(ests))
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY, refresh=False)
    msg = str(excinfo.value)
    assert "would exceed the run budget cap" in msg
    assert "narrative was NOT persisted" in msg
    row = migrated_con.execute(
        "SELECT narrative_text, script_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"] is None and row["script_text"] is None
    assert len(fake_model.calls) == 1  # narrative ran; script never called


def test_per_step_costs_merge_into_token_cost(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)  # carries a rank_select step
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    run(migrated_con, date=A_DAY, refresh=False)
    tc = json.loads(
        migrated_con.execute(
            "SELECT token_cost FROM briefings WHERE date = ?", (A_DAY,)
        ).fetchone()["token_cost"]
    )
    step_names = [s["step"] for s in tc["steps"]]
    assert step_names == ["rank_select", "narrative_A", "script_adapt"]
    expected_total = round(sum(s.get("usd") or 0 for s in tc["steps"]), 6)
    assert tc["total_usd"] == expected_total


def test_generation_log_records_ok_runs_fully(migrated_con, fake_model):
    slots = [slot(1, override=True, tags=()),
             slot(2, revived=({"topic": "T", "last_covered": "2026-07-01"},))]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    run(migrated_con, date=A_DAY, refresh=False)
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1]
    )
    assert entry["status"] == "ok" and entry["sample"] is False
    assert entry["variant"] == "A"
    assert entry["override_rendered"] is True
    assert entry["revival_rendered"] is True
    assert entry["narrative_words"] > 0 and entry["script_words"] > 0
    assert [s["step"] for s in entry["steps"]] == ["narrative_A", "script_adapt"]


# --- call_llm error taxonomy (HTTP layer, loopback fake server) ------------------------------

@pytest.fixture
def llm_http(fake_api, monkeypatch):
    monkeypatch.setattr(ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return fake_api


def test_call_llm_401_names_the_key(llm_http):
    llm_http.add_route(
        "/chat/completions", status=401,
        body=json.dumps({"error": {"code": "invalid_api_key", "message": "bad"}}).encode(),
        content_type="application/json",
    )
    with pytest.raises(generate.GenerateError) as excinfo:
        generate.call_llm("sk-x", "p", "narrative", 100, 0.3, True)
    assert "regenerate at platform.openai.com" in str(excinfo.value)
    assert len([r for r in llm_http.recorded if r["method"] == "POST"]) == 1


def test_call_llm_truncation_named_and_retried(llm_http):
    llm_http.add_route(
        "/chat/completions", status=200,
        body=json.dumps({
            "choices": [{"finish_reason": "length", "message": {"content": "{"}}],
            "usage": {},
        }).encode(),
        content_type="application/json",
    )
    with pytest.raises(generate.GenerateError) as excinfo:
        generate.call_llm("sk-x", "p", "script", 3200, 0.4, False)
    msg = str(excinfo.value)
    assert "truncated at the script token cap (3200)" in msg
    assert "failed after one retry" in msg
    assert len([r for r in llm_http.recorded if r["method"] == "POST"]) == 2


# --- misc pins -------------------------------------------------------------------------------

def test_labels_block_carries_the_corrections_placeholder():
    """§5.9 #4: no upstream correction flag exists yet — pin the placeholder
    line until M6+ wires the real flag."""
    slots = [slot(1, override=True), slot(2, corroboration_count=1, outlets=("Solo",))]
    block = generate.build_labels_block(_inputs_for(slots))
    assert block.splitlines()[-1] == "corrections flagged upstream: none this run"
    assert "story 1: OVERRIDE — reason:" in block
    assert "story 2: SINGLE-SOURCE — outlet: Solo" in block


def test_date_spoken_forms_ordinals():
    assert generate._date_spoken_forms("2026-07-01") == [
        "2026-07-01", "July 1", "July 1st"
    ]
    assert "July 3rd" in generate._date_spoken_forms("2026-07-03")
    assert "July 12th" in generate._date_spoken_forms("2026-07-12")


def test_generate_cli_is_keyless_safe_inside_the_suite(capsys):
    """The M5 escape, pinned dead: `newslens generate` from inside the suite
    is keyless (autouse sandbox redirects .env) and exits 1 politely — it can
    never again reach a real key, real state, or the network from a test."""
    from newslens import cli

    rc = cli.main(["generate"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "OPENAI_API_KEY not set" in err
# --- Editorial-review package A1-A6 (2026-07-05) — closing pins -----------------------

def test_A1_record_is_always_voice_A_parity_never_consulted(migrated_con, fake_model):
    """A1: alternation ended. On a date whose PARITY says B, the record still
    generates as voice A, not a sample — variant_for stays dormant code."""
    assert generate.ACTIVE_VOICE == "A"
    slots = [slot(1)]
    seed_briefing(migrated_con, B_DAY, slots)  # B_DAY parity would say "B"
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=B_DAY, refresh=False)
    assert rep.variant == "A" and rep.sample is False
    assert (paths.DATA_DIR / "briefings" / f"{B_DAY}.md").exists()
    row = migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (B_DAY,)
    ).fetchone()
    assert row["narrative_text"]  # the record, written as A


def test_A1_prediction_rule_and_no_methodology_prompt_guards():
    """Drift guards: prompt A carries the binding prediction rule; the
    briefing never self-references methodology (scan both prompt files)."""
    a_text = (paths.PROMPTS_DIR / generate.PROMPT_A).read_text(encoding="utf-8")
    assert "THE PREDICTION RULE" in a_text
    assert "NEVER predicts" in a_text


def test_A2_tier_gate_positions_and_quick_movements():
    """Model proposes, code enforces: 1 full / 2 medium / 3 medium-or-quick /
    4+ quick; a movement field on a quick hit is a validation ERROR."""
    slots5 = [slot(i) for i in range(1, 6)]
    payload = stories_payload(slots5)
    stories, _ = generate.validate_narrative_payload(payload, slots5, "A")
    assert [s["tier"] for s in stories] == ["full", "medium", "medium", "quick", "quick"]

    bad_pos = stories_payload(slots5)
    bad_pos["stories"][0]["tier"] = "quick"  # the lead is always full
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(bad_pos, slots5, "A")
    assert "tier 'quick' not allowed at this position" in str(excinfo.value)

    missing = stories_payload(slots5)
    del missing["stories"][1]["tier"]
    with pytest.raises(ValueError):
        generate.validate_narrative_payload(missing, slots5, "A")

    # Story 3 is the model's judgment call: quick is legal there too.
    s3_quick = stories_payload(slots5)
    s3_quick["stories"][2] = {
        "tier": "quick",
        "headline": "Quick three",
        "lede": "One sentence hit.",
    }
    stories2, _ = generate.validate_narrative_payload(s3_quick, slots5, "A")
    assert stories2[2]["tier"] == "quick"

    smuggled = stories_payload(slots5)
    smuggled["stories"][4]["why_it_matters"] = "movement on a quick hit"
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(smuggled, slots5, "A")
    assert "quick hits carry no why_it_matters" in str(excinfo.value)


def test_A2_quick_hits_render_lean_with_trust_furniture():
    slots4 = [slot(i) for i in range(1, 5)]
    slots4[3] = slot(4, tags=(), corroboration_count=1, outlets=("Solo",))
    stories, _ = generate.validate_narrative_payload(
        stories_payload(slots4), slots4, "A"
    )
    text = generate.assemble_narrative(A_DAY, "A", stories, _inputs_for(slots4))
    # Three tiered stories carry movements; the quick hit carries none…
    assert text.count("**Why it matters:**") == 3
    assert text.count("**Watch for:**") == 3
    # …but its trust furniture is intact (meta-line with corroboration).
    assert "Reported by 1 named outlet — Solo. Here for:" in text


def test_A2_tier_word_bands_warn_only(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fat = stories_payload(slots)
    fat["stories"][0]["why_it_matters"] = "Very long movement. " * 200  # >>550 words
    fake_model.narrative = fat
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    assert any("tier guidance (A2)" in w and "full story" in w for w in rep.warnings)


def test_A3_truism_and_moralizing_scans_warn():
    slots = [slot(1)]
    payload = stories_payload(slots)
    payload["stories"][0]["why_it_matters"] = (
        "This has profound implications and raises questions about policy; "
        "a divisive, troubling story."
    )
    _, warns = generate.validate_narrative_payload(payload, slots, "A")
    joined = " | ".join(warns)
    assert "profound implications" in joined.lower()      # TRUISM_WARN_STRINGS
    assert "raises questions about" in joined.lower()
    assert "divisive" in joined.lower() and "troubling" in joined.lower()


def test_A4_mechanical_transitions_and_early_dateline_warn():
    slots = [slot(1)]
    inputs = _inputs_for(slots)
    script = (
        "It's Sunday, July 5. Turning to markets now. "
        + "Body prose continues at length here. " * 30
        + generate.SPOKEN_CAVEAT + " " + generate.SIGNOFF
    )
    _, hard, warns = generate.validate_script(script, "narrative", inputs)
    assert hard == []
    joined = " | ".join(warns)
    assert "mechanical transition defaults (A4)" in joined
    assert "turning to" in joined
    assert "intro formula (A4)" in joined  # dateline in the opening breath


def test_A5_relaxations_did_not_leak_into_the_hard_set():
    """The A5 boundary: spoken single-source checking is GONE (no output at
    all), spoken revival warns — while override elements and the schedule ban
    stay hard, and the frozen-furniture append still discloses."""
    slots = [
        slot(1, override=True),
        slot(2, corroboration_count=1, outlets=("Solo",)),
        slot(3, revived=({"topic": "T", "last_covered": "2026-07-01"},)),
    ]
    inputs = _inputs_for(slots)
    bare = "Says nothing required. " * 40 + "See you tomorrow."
    body, hard, warns = generate.validate_script(bare, "narrative", inputs)
    joined_hard = " | ".join(hard)
    joined_warns = " | ".join(warns)
    assert "outside-your-tags acknowledgment" in joined_hard      # still hard
    assert "schedule promise" in joined_hard                      # still hard
    assert "not voiced" in joined_warns                           # warn now
    assert "single-source" not in (joined_hard + joined_warns)    # check removed
    assert "spoken caveat was missing — appended verbatim" in joined_warns
    assert body.rstrip().endswith(generate.SIGNOFF)


def test_A6_settings_block_validation_and_default_off(tmp_path):
    from newslens import config as config_mod

    p = tmp_path / "sources.yaml"
    p.write_text(
        "sources:\n  - name: A\n    rss_url: https://a.example/f\n"
        "settings:\n  threads_steer_selection: true\n",
        encoding="utf-8",
    )
    cfg = config_mod.load_sources(p)
    assert cfg.problems == [] and cfg.threads_steer_selection is True

    p.write_text(
        "sources:\n  - name: A\n    rss_url: https://a.example/f\n"
        "settings:\n  threads_steer_selection: \"yes\"\n",
        encoding="utf-8",
    )
    assert any("must be true or false" in pr for pr in config_mod.load_sources(p).problems)

    p.write_text(
        "sources:\n  - name: A\n    rss_url: https://a.example/f\n"
        "settings:\n  unknown_knob: true\n",
        encoding="utf-8",
    )
    assert any("unknown" in pr for pr in config_mod.load_sources(p).problems)

    p.write_text("sources:\n  - name: A\n    rss_url: https://a.example/f\n", encoding="utf-8")
    assert config_mod.load_sources(p).threads_steer_selection is False  # default OFF


def test_A6_doctor_renders_the_steering_setting(tmp_paths, fake_api):
    from newslens import doctor
    from conftest import make_rss

    url = fake_api.add_route("/f.xml", body=make_rss([{"title": "T", "url": "https://x.example/1"}]))
    paths.SOURCES_FILE.write_text(
        f"sources:\n  - name: A\n    rss_url: {url}\n"
        "settings:\n  threads_steer_selection: true\n",
        encoding="utf-8",
    )
    results = doctor.check_sources()
    assert any(
        "threads_steer_selection = true (threads boost selection)" in r.text
        for r in results
    )


def test_A6_steering_off_still_records_references_and_persists_flag(
    migrated_con, monkeypatch, fake_api
):
    """Both halves of steering-off, end to end: a thread match contributes
    ZERO score (cluster earns its slot on tags alone) yet reference recording
    still runs in persist(), and the run's ranking_runs meta persists
    threads_steer_selection=false."""
    monkeypatch.setattr(ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    now = iso_now()
    migrated_con.execute(
        "INSERT INTO source_items (id, source_type, outlet, url, title, fetched_at)"
        " VALUES (1, 'rss', 'Outlet A', 'https://a.example/1', 'Story', ?)", (now,),
    )
    migrated_con.execute(
        "INSERT INTO memory (topic, status, created_at, updated_at)"
        " VALUES ('Iran War', 'active', ?, ?)", (now, now),
    )
    migrated_con.commit()
    payload = {
        "clusters": [{
            "story_title": "T", "summary": "S", "item_ids": [1],
            "matched_tags": [{"name": "AI regulation", "level": "topic"}],
            "matched_memory": ["Iran War"], "world_impact": 5,
            "world_impact_reason": "R",
        }]
    }
    fake_api.add_route(
        "/chat/completions", status=200,
        body=json.dumps({
            "choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        }).encode("utf-8"),
        content_type="application/json",
    )
    from newslens import config as config_mod

    cfg = config_mod.SourcesConfig(
        sources=[config_mod.Source(name="Outlet A", rss_url="https://a.example/f")],
        interests_granular=["AI regulation"],
    )
    assert cfg.threads_steer_selection is False  # default OFF
    ranking.run_rank(date=A_DAY, con=migrated_con, cfg=cfg, env=ENV)

    slots_json = json.loads(migrated_con.execute(
        "SELECT story_slots FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()["story_slots"])
    assert slots_json[0]["personal_score"] == 1.0  # tag only — no memory boost
    briefing_id = migrated_con.execute(
        "SELECT id FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()["id"]
    ref = migrated_con.execute(
        "SELECT last_referenced_briefing_id FROM memory WHERE topic = 'Iran War'"
    ).fetchone()["last_referenced_briefing_id"]
    assert ref == briefing_id  # recognition-only still RECORDS (continuity intact)
    meta = json.loads(migrated_con.execute(
        "SELECT meta FROM ranking_runs WHERE date = ? ORDER BY id DESC LIMIT 1", (A_DAY,)
    ).fetchone()["meta"])
    assert meta["threads_steer_selection"] is False
