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

from conftest import anthropic_envelope
from newslens import db, generate, llm, paths, ranking

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
        # NL-63 M2 amended contract: EVERY story (In Brief/quick included) is a
        # structured mini-story carrying all four prose fields and both labels.
        story["why_it_matters"] = (
            "It matters because of concrete effects on the reader's interests."
        )
        story["watch_for"] = "Watch the next scheduled decision."
        # A7: declared framings, sanctioned-menu members (varied to keep
        # the all-one-rhythm warn out of unrelated tests).
        story["why_label"] = generate.WHY_FRAMINGS[(i - 1) % len(generate.WHY_FRAMINGS)]
        story["watch_label"] = generate.WATCH_FRAMINGS[(i - 1) % len(generate.WATCH_FRAMINGS)]
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
    # Pad to a realistic episode size (floor REMOVED 2026-07-14 — no length
    # gate to clear; the degenerate backstop is far below this filler anyway).
    # NL-63 M2: the amended editions are bigger, so the script target grows
    # with them — scale the filler to ~0.85x the slot budget.
    slot_budget = (generate.SCRIPT_OPEN_WORDS + generate.SCRIPT_OUTRO_WORDS
                   + sum(generate.script_segment(int(s["slot"])) for s in slots))
    body_words = sum(len(p.split()) for p in parts)
    need = int(slot_budget * 0.85) - body_words
    if need > 0:
        filler = " ".join(["The detail continues in measured spoken prose."]
                          * (need // 7 + 1))
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
    state.editor = None      # M6: 2nd+ json-mode call = the editor pass;
    state.script = None      # None -> echo the narrative (a no-op edit)

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append(
            {"json_mode": json_mode, "max_tokens": max_tokens,
             "temperature": temperature, "prompt": prompt}
        )
        if json_mode:
            n_json_before = sum(
                1 for c in state.calls[:-1] if c["json_mode"]
            )
            payload = (
                state.narrative if n_json_before == 0
                else (state.editor if state.editor is not None else state.narrative)
            )
            content = json.dumps(payload)
        else:
            content = state.script
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
    assert text.count("**Why it matters:**") == 1   # story 1's declared framing
    assert text.count("**Why markets care:**") == 1  # story 2's (A7 render)
    assert text.count("**Watch for:**") == 1
    assert text.count("**What happens next:**") == 1
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
    # B2 fake migration: rank rides the Claude API lane — redirect the
    # anthropic endpoint + credential at the loopback fake, serve the
    # anthropic shape. Contract under test is unchanged.
    monkeypatch.setattr(
        ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    monkeypatch.setattr(
        llm, "ANTHROPIC_MESSAGES_URL", fake_api.base_url + "/v1/messages"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
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
        "/v1/messages", status=200,
        body=anthropic_envelope(payload, input_tokens=500, output_tokens=100),
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
    """Model + price are SEAMED to the seat table, not scattered. B2: the writer
    (narrative) seat stays gpt-4o at 2.50/10.00; the RANK seat flipped to the
    Claude API lane on claude-haiku-4-5 at 1.00/5.00, and ranking's module
    constants now DERIVE from llm.SEATS["rank"] (single source of truth)."""
    assert generate.WRITER_MODEL == "gpt-4o"
    assert ranking.RANK_MODEL == "claude-haiku-4-5"           # B2: Haiku
    assert ranking.RANK_MODEL == llm.SEATS["rank"].model      # derived from the seat
    assert ranking.RANK_USD_PER_MTOK_IN == 1.00
    assert ranking.RANK_USD_PER_MTOK_OUT == 5.00
    assert ranking.MODEL == "gpt-4o-mini"  # the fallback rung, kept documented
    assert generate.WRITER_USD_PER_MTOK_IN == 2.50
    assert generate.WRITER_USD_PER_MTOK_OUT == 10.00
    usage = {"prompt_tokens": 1000, "completion_tokens": 200}
    assert generate._step_cost(usage) == pytest.approx(0.0045)  # writer rates


def test_each_generate_step_logs_its_own_seat_model(migrated_con, fake_model):
    # B2: each step's ledger row names the seat that actually ran — narrative on
    # gpt-4o, script/editor on Haiku (the Claude API lane). The ledger no longer
    # forks the model that ran from the model it records.
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    seat_model = {"gpt-4o", "claude-haiku-4-5"}
    for s in rep.steps:
        assert s["model"] in seat_model, s
    by_step = {s["step"]: s["model"] for s in rep.steps}
    assert by_step.get("narrative_A", "gpt-4o") == "gpt-4o"
    for step, model in by_step.items():
        if step.startswith("script") or step.startswith("editor"):
            assert model == "claude-haiku-4-5", step
    tc = json.loads(migrated_con.execute(
        "SELECT token_cost FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()["token_cost"])
    for s in tc["steps"]:
        if s["step"].startswith("narrative"):
            assert s["model"] == "gpt-4o", s
        elif s["step"].startswith(("script", "editor")):
            assert s["model"] == "claude-haiku-4-5", s


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
    # Gate FIX-1: a ready baseline on the thread — the no-threads strip must
    # also drop the BACKGROUNDER block, or the stripped cold-start sample
    # leaks thread names through the baseline lane (ADR-0007).
    from newslens import memory_core as mc
    zebra_tid = migrated_con.execute(
        "SELECT id FROM memory WHERE topic = 'Zebra Futures Thread'"
    ).fetchone()[0]
    mc.record_baseline(migrated_con, zebra_tid, "2026-07-01", "ready",
                       backgrounder="Began in 2019.")
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
    assert "BACKGROUNDER" not in n_prompt              # gate FIX-1: baseline block stripped
    s_prompt = next(c for c in fake_model.calls if not c["json_mode"])["prompt"]
    assert "last covered 2026-07-01" not in s_prompt   # script labels thread-free
    assert "BACKGROUNDER" not in s_prompt              # gate FIX-1: baseline block stripped
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


def test_spoken_caveat_is_never_appended_signoff_still_is():
    """NL-58 ruling 2 (DECISIONS 2026-07-10): the spoken caveat is OUT of the
    podcast — a deliberate, principal-ruled contract change. validate_script no
    longer appends it and no longer warns about it; only the sign-off remains
    frozen furniture. (Was test_frozen_caveat_and_signoff_appended — flipped.)"""
    slots = [slot(1)]
    no_furniture = "A decent script body. " * 40 + generate.SIGNOFF
    body, hard, warns = generate.validate_script(
        no_furniture, "narrative", _inputs_for(slots)
    )
    assert hard == []
    # The caveat is NOT force-appended, and its absence is NOT flagged.
    assert generate.SPOKEN_CAVEAT not in body
    assert not any("spoken caveat" in w.lower() for w in warns)
    assert body.count(generate.SIGNOFF) == 1  # sign-off untouched

    # Sign-off is still frozen furniture — appended verbatim when missing.
    no_signoff = "Body. " * 40
    body2, _, warns2 = generate.validate_script(
        no_signoff, "narrative", _inputs_for(slots)
    )
    assert body2.rstrip().endswith(generate.SIGNOFF)
    assert any("sign-off was missing" in w for w in warns2)


def test_fact_subset_and_hedge_warn_grade():
    # NOTES 28b (backlog-minors batch): the blanket {2,3} exemption became
    # PRINCIPLED — enumeration numerals up to the story count are script
    # furniture; beyond the count they check like any figure. Three slots
    # here: "2" and "3" are structure ("two quick ones..."); on a 1-slot
    # day they would flag.
    slots = [slot(1), slot(2), slot(3)]
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
    assert "'2'" not in joined and "'3'" not in joined  # enumeration <= slot count
    assert "hedge check" in joined  # will vs might


def test_numeral_exemption_is_slot_bounded_not_blanket():
    """NOTES 28b: on a 1-slot day an invented '3' is a real loose numeral —
    the old blanket exemption would have hidden it."""
    slots = [slot(1)]
    narrative = "One story today."
    script = ("One story today, with 3 invented reasons. "
              + generate.SPOKEN_CAVEAT + " " + generate.SIGNOFF + " " + "pad " * 40)
    _, _, warns = generate.validate_script(script, narrative, _inputs_for(slots))
    assert any("'3'" in w for w in warns)


# --- script digest contract (principal 2026-07-14: shorter, lead-focused) --------------

def test_script_budgets_are_digest_ceilings_over_covered_stories_only():
    """THE PODCAST CONTRACT REWRITTEN: length is EMERGENT — _script_budgets
    returns per-k QUALITY CEILINGS over the COVERED stories (lead 400 + 200 each
    for up to 4 more, + open 90 + outro 70), no longer a narrative-scaled
    fullness target. A 6-7 story edition still covers only 5 (never every
    story)."""
    c2, _, k2 = generate._script_budgets(2)
    c3, _, k3 = generate._script_budgets(3)
    c5, _, k5 = generate._script_budgets(5)
    assert (k2, c2) == (2, 760)      # 90 + 70 + 400 + 200
    assert (k3, c3) == (3, 960)
    assert (k5, c5) == (5, 1360)     # 90 + 70 + 400 + 200*4
    c6, _, k6 = generate._script_budgets(6)
    c7, _, k7 = generate._script_budgets(7)
    assert (k6, c6) == (5, 1360) and (k7, c7) == (5, 1360)   # capped at 5 covered
    _, _, k1 = generate._script_budgets(1)
    assert k1 == 1                    # thin day: cover what exists
    # the per-story figures are the per-slot guides (lead the largest)
    _, desc, _ = generate._script_budgets(5)
    assert "slot 1: up to ~400" in desc and "slot 2: up to ~200" in desc
    # every guide ceiling sits under the <11-min episode ceiling (the only
    # length contract left — floor REMOVED, principal 2026-07-14 second
    # amendment); the degenerate backstop is far beneath every guide
    assert not hasattr(generate, "SCRIPT_MIN_VIABLE_WORDS")
    assert generate.SCRIPT_CEILING_WORDS == 1650
    assert c5 < generate.SCRIPT_CEILING_WORDS
    assert generate.SCRIPT_DEGENERATE_WORDS < generate._script_budgets(1)[0]


def test_script_covers_top_slots_by_rank_never_every_story():
    """Deterministic selection (principal 2026-07-14): the episode airs the
    lead + up to 4 more — the top slots by the edition's own rank order — never
    the whole edition."""
    six = [slot(i) for i in range(1, 7)]
    assert generate.script_covered_slots(_inputs_for(six)) == {1, 2, 3, 4, 5}
    two = [slot(1), slot(2)]
    assert generate.script_covered_slots(_inputs_for(two)) == {1, 2}
    one = [slot(1)]
    assert generate.script_covered_slots(_inputs_for(one)) == {1}


def test_script_prompt_states_coverage_and_emergent_ceiling():
    """Wiring (re-pinned, DECISIONS 2026-07-14 'podcast floor REMOVED'): the
    digest contract reaches the model — the covered-story instruction, the
    ceiling-only emergent length ('up to ~11 minutes', no band, no minimum),
    and 'up to' per-story ceilings (not floors)."""
    slots = [slot(i) for i in range(1, 7)]
    prompt = generate.build_script_prompt(A_DAY, "A", "The narrative body text.",
                                          _inputs_for(slots))
    assert "covers 5 stories" in prompt              # 6-story edition -> 5 covered
    assert "Cover stories 1 through 5 ONLY" in prompt
    assert "up to ~11 minutes" in " ".join(prompt.split())
    assert "4-11 minutes" not in prompt              # the band is gone
    assert "up to ~400" in prompt and "up to ~200" in prompt   # ceilings, lead deepest


def test_uncovered_story_disclosure_is_scoped_out_of_the_episode():
    """Coverage scoping (principal 2026-07-14): a mandatory spoken disclosure is
    owed only for a story the digest AIRS. An override on an uncovered lower-rank
    story is the text briefing's job, not the episode's — so it is neither fed to
    the script nor hard-flagged against a script that (correctly) never voices
    it. Without the covered scoping it WOULD hard-flag (legacy whole-edition)."""
    slots = [slot(i) for i in range(1, 6)] + [slot(6, override=True, tags=())]
    inputs = _inputs_for(slots)
    covered = generate.script_covered_slots(inputs)
    assert 6 not in covered
    labels = generate.build_labels_block(inputs, covered=covered)
    assert "story 6" not in labels                   # uncovered -> not fed to the ear
    script = ("It's Sunday, July 5. Here's what matters today. "
              + "The covered stories carry real substance today. " * 90
              + " That's your briefing.")
    _, hard, _ = generate.validate_script(script, "narrative", inputs, covered=covered)
    assert not any("story 6" in h for h in hard)     # not the episode's obligation
    _, hard_all, _ = generate.validate_script(script, "narrative", inputs)
    assert any("story 6" in h for h in hard_all)     # legacy whole-edition behavior


def test_slot_budget_lines_state_hard_targets_with_lead_primacy():
    """NL-63 M2 fix (writer under-delivered the doubled bands ~20%, lead at a
    third of target): the per-story budget lines the writer sees state HARD word
    targets — ~640 lead / ~440 full-picture / ~220 In-Brief — with the lead's
    primacy spelled out, not the old soft '~550-750' the model treated as
    optional. Steering, not a new gate."""
    lead = generate._slot_budget_line(1)
    med = generate._slot_budget_line(2)
    quick = generate._slot_budget_line(4)
    assert "640" in lead and "550" in lead      # explicit lead target + floor
    assert "LONGEST" in lead                     # primacy stated hard
    assert "440" in med and "350" in med
    assert "220" in quick and "180" in quick


def test_hard_per_story_targets_reach_the_narrative_prompt():
    """Wiring proof (steering reaches the model): the strengthened per-story
    targets are injected into the built narrative prompt, per tier."""
    slots = [slot(1), slot(2), slot(3), slot(4)]
    prompt = generate.build_narrative_prompt(A_DAY, "A", _inputs_for(slots))
    assert "TARGET ~640 words" in prompt         # lead, in its STORY 1 block
    assert "single LONGEST story" in prompt
    assert "TARGET ~440 words" in prompt          # full-picture
    assert "TARGET ~220 words" in prompt          # In Brief


def test_broken_stub_script_retries_then_fails_degenerate(migrated_con, fake_model):
    """RE-PINNED (was ...fails_viability; DECISIONS 2026-07-14 'podcast floor
    REMOVED'): the guard is now a flat brokenness backstop, NOT a length
    contract — and a genuine stub still bites it. A near-empty body
    (disclosures and truncation clear) fails SCRIPT_DEGENERATE_WORDS: one
    informed retry, then a visible, logged failure."""
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = "Way too short. " + generate.SIGNOFF   # ~5 words -> stub
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY)
    assert "script degenerate" in str(excinfo.value)
    assert "NOT a length contract" in str(excinfo.value)
    script_calls = [c for c in fake_model.calls if not c["json_mode"]]
    assert len(script_calls) == 2  # one retry, then visible failure


def test_naturally_short_digest_ships_no_false_shortfall(migrated_con, fake_model):
    """Emergent length (principal 2026-07-14, floor REMOVED same day): a
    legitimately short complete digest ships — no floor exists to fail it, no
    lower-bound 'short' warn fires, and the only length check left is the
    degenerate backstop it clears by construction."""
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY)
    assert rep.script_text and rep.script_words > generate.SCRIPT_DEGENERATE_WORDS
    assert not any("not viable" in w or "degenerate" in w or "severely short" in w
                   or ("vs ~" in w and "target" in w) for w in rep.warnings)


# --- QA extensions — NL-63 M2 live-contact fix loop (QA pass 2026-07-14) ----------------
# Adversarial pins on the rewritten podcast contract (DECISIONS 2026-07-14
# "THE PODCAST CONTRACT REWRITTEN" + "podcast contract refined"), the writer
# steering fixes, and the money-honesty fold. QA-authored; extends, never
# replaces, the implementer's contract tests above.

def test_covered_slots_rank_order_non_contiguous_ids_and_edges():
    """QA (coverage selection): the covered set is the k LOWEST slot ids
    PRESENT, so rank order survives non-contiguous ids, k=1 is legal on a
    one-story edition, and an empty edition yields an empty set — the
    max(1, ...) inside _script_coverage never invents a phantom slot
    (load_briefing_inputs refuses zero-slot editions upstream anyway).
    The 'Cover stories 1 through {last}' prompt arithmetic stays exact on
    ragged ids BECAUSE covered = the k smallest present ids: every present
    id <= last is covered, every id > last is not."""
    ragged = [slot(i) for i in (1, 3, 7, 9, 12, 15, 20)]
    covered = generate.script_covered_slots(_inputs_for(ragged))
    assert covered == {1, 3, 7, 9, 12}                       # 7 present -> top 5
    assert generate.script_covered_slots(_inputs_for([slot(3)])) == {3}
    assert generate.script_covered_slots(_inputs_for([])) == set()
    prompt = generate.build_script_prompt(A_DAY, "A", "Narrative body.",
                                          _inputs_for(ragged))
    assert "Cover stories 1 through 12 ONLY" in prompt
    assert "the remaining 2 stories are NOT" in prompt
    for n in (1, 3, 7, 9, 12):
        assert f"story {n}: corroboration for the ear" in prompt
    assert "story 15" not in prompt and "story 20" not in prompt


def test_script_prompt_feeds_exactly_the_covered_set_both_directions():
    """QA (coverage wiring, both directions): every COVERED story's ear-label
    material reaches the script prompt; NO uncovered story's does. An override
    or revival on uncovered slots 6/7 leaking into the prompt would steer the
    model toward voicing a story the episode must never mention — a contract
    break, not a nuance ('a listener never hears about a story the episode
    skips')."""
    slots = [slot(1, override=True, tags=()),
             slot(2, revived=({"topic": "T", "last_covered": "2026-07-01"},)),
             slot(3), slot(4),
             slot(5, corroboration_count=1, outlets=("Solo Outlet",)),
             slot(6, override=True, tags=()),
             slot(7, revived=({"topic": "U", "last_covered": "2026-06-20"},))]
    prompt = generate.build_script_prompt(A_DAY, "A", "Narrative body.",
                                          _inputs_for(slots))
    # direction 1: covered disclosure material arrives
    assert "story 1: OVERRIDE" in prompt
    assert "story 2: REVIVAL — say the date: last covered 2026-07-01" in prompt
    assert "story 5: SINGLE-SOURCE — outlet: Solo Outlet" in prompt
    for n in (1, 2, 3, 4, 5):
        assert f"story {n}: corroboration for the ear" in prompt
    # direction 2: uncovered stories are absent from the prompt's label/coverage
    # surfaces entirely (their text lives only inside the narrative being adapted)
    assert "story 6" not in prompt and "story 7" not in prompt
    assert "covers 5 stories" in prompt and "never cover every story" in prompt


def test_disclosure_grades_covered_bites_uncovered_silent():
    """QA (disclosure scoping, grade-accurate): with covered scoping ACTIVE, a
    COVERED story's missing override acknowledgment still HARD-flags — the
    scoping must never blanket-disable the episode's own obligations. An
    uncovered story's revival date draws neither the hard list nor even its
    warn-grade flag (the `continue` skips the whole per-slot body — the text
    briefing owns that disclosure). covered=None keeps the legacy whole-edition
    behavior for direct callers."""
    slots = [slot(1, override=True, tags=()), slot(2), slot(3), slot(4), slot(5),
             slot(6, revived=({"topic": "T", "last_covered": "2026-07-01"},))]
    inputs = _inputs_for(slots)
    covered = generate.script_covered_slots(inputs)
    assert covered == {1, 2, 3, 4, 5}
    script = ("Markets moved early and the day starts with a clear pattern "
              "worth attention. It's Sunday, July 5. Here's what matters today. "
              + "The stories carry substance in measured spoken prose. " * 80
              + generate.SIGNOFF)
    _, hard, warns = generate.validate_script(script, "narrative", inputs,
                                              covered=covered)
    assert any("story 1" in h and "override" in h for h in hard)   # covered bites
    assert not any("story 6" in h for h in hard)
    assert not any("story 6" in w for w in warns)                  # not even warn-grade
    _, hard_all, warns_all = generate.validate_script(script, "narrative", inputs)
    assert any("story 1" in h for h in hard_all)                   # legacy unchanged
    assert any("story 6" in w for w in warns_all)                  # revival = warn grade


def test_degenerate_backstop_is_flat_and_far_below_every_guide():
    """CONSCIOUS FLIP (was test_viability_floor_derivation_table_pinned_
    AS_BUILT; DECISIONS 2026-07-14 'podcast floor REMOVED'). WAS: the pending
    flat-vs-scaled floor table {1: 369, 2: 501, 3+: 600} awaiting the
    principal's ruling. NOW: the ruling landed as NEITHER — the floor is gone;
    the pending question dissolved. The only lower check is the flat,
    coverage-INDEPENDENT degenerate backstop (brokenness isn't a function of
    k), pinned here to sit far beneath every k's guide ceiling so it can never
    masquerade as a floor — and the guide-ceiling table survives unchanged."""
    ceilings = {k: generate._script_budgets(k)[0] for k in (1, 2, 3, 4, 5)}
    assert ceilings == {1: 560, 2: 760, 3: 960, 4: 1160, 5: 1360}
    assert generate.SCRIPT_DEGENERATE_WORDS == 120
    assert not hasattr(generate, "SCRIPT_MIN_VIABLE_WORDS")
    for k, c in ceilings.items():
        assert generate.SCRIPT_DEGENERATE_WORDS <= c * 0.25, (k, c)


def _digest_script(slots, total_words):
    """A structurally clean covered-digest script sized to EXACTLY total_words
    as wc() counts them: one-line hook (>60 chars, so the A4 dateline-position
    warn stays out) -> dateline -> one short segment per story (each <15 words:
    exempt from the cross-section repeat detector) -> a single unpunctuated
    filler paragraph (only one 15+-word paragraph in the body, so no pair can
    retell; one giant sentence never trips the 3-consecutive rhythm warn) ->
    verbatim sign-off."""
    parts = ["Markets moved early and the day starts with a clear pattern "
             "worth your full attention. It's Sunday, July 5. "
             "Here's what matters today."]
    for s in slots:
        parts.append(f"Story {s['slot']}. The development moved today in ways "
                     "that matter.")
    parts.append(generate.SIGNOFF)
    # Size by generate.wc — the pipeline's own counter (contractions like
    # "It's" tokenize as TWO words there, unlike str.split); "substance" is
    # exactly one wc token, so the fill is word-exact.
    need = total_words - generate.wc("\n\n".join(parts))
    assert need > 0, "fixture asked for fewer words than its own furniture"
    parts.insert(2, " ".join(["substance"] * need))
    text = "\n\n".join(parts)
    assert generate.wc(text) == total_words
    return text


def test_naturally_short_k3_digest_at_620_words_ships_clean(migrated_con, fake_model):
    """QA (the flipped guard, near the floor): a whole, disclosure-complete
    k=3 digest at 620 words — 20 over the exact 600 floor, miles under the old
    fullness targets — SHIPS with no viability failure, no shortfall warn, and
    no over-run warn. Emergent length: 620 is a CORRECT episode, not a defect
    ('however long the episode is will be how long it will be')."""
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = _digest_script(slots, 620)
    rep = run(migrated_con, date=A_DAY)
    assert rep.script_words == 620
    assert not any("not viable" in w or "severely short" in w
                   or "over the ~" in w or ("vs ~" in w and "target" in w)
                   for w in rep.warnings)


def test_overrun_warn_is_one_directional_only(migrated_con, fake_model):
    """QA (never fill — the ONLY warned direction): a k=3 digest at 1150 words
    (over the int(960*1.15)=1104 margin, still under the 1650 hard ceiling)
    SHIPS — over-run is warn-grade, never a failure — carrying exactly one
    over-run warn that names the guide, the hard ceiling, and the
    tighten-never-fill instruction. The short direction draws no warn at all
    (sibling test at 620)."""
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = _digest_script(slots, 1150)
    rep = run(migrated_con, date=A_DAY)
    assert rep.script_words == 1150
    over = [w for w in rep.warnings if "over the ~960-word guide" in w]
    assert len(over) == 1
    assert "tighten, never fill" in over[0] and "1650" in over[0]


def test_k1_short_complete_digest_ships_flat_degenerate_guard(migrated_con, fake_model):
    """CONSCIOUS FLIP — podcast floor REMOVED (principal 2026-07-14, DECISIONS
    'NewsLens — podcast floor REMOVED'; supersedes the pending thin-day
    relaxation, which dissolves with the floor).
      WAS (loop #3, b0cc572): a 1-story edition had a coverage-RELAXED viability
        floor of 369 — a 300-word single-story digest ABORTED as 'script not
        viable', only a 400-word one shipped (the 369/501/600 table).
      NOW: no length floor and no coverage relaxation. A short-but-COMPLETE
        1-story digest ships at ANY length the material earns; only genuinely
        degenerate output (below the FLAT SCRIPT_DEGENERATE_WORDS structural-
        sanity floor, coverage-independent) aborts.
    The 300-word ship is the red->green wiring proof: it FAILED on b0cc572
    (rejected at the 369 floor) and passes only once the flat guard is live."""
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    # 300 words: under the OLD 369 k=1 floor (would have aborted) — now a
    # correct, complete short episode. Ships, unwarned.
    fake_model.script = _digest_script(slots, 300)
    rep = run(migrated_con, date=A_DAY)
    assert rep.script_words == 300
    assert not any("not viable" in w or "degenerate" in w or "broken" in w
                   for w in rep.warnings)
    # genuinely degenerate: furniture wrapped around a stub, below the flat
    # brokenness floor -> aborts (one informed retry, then visible failure).
    # Coverage-independent: k=1 gets the SAME flat guard as k=5, not a fraction.
    fake_model.script = _digest_script(slots, 60)
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY)
    assert "degenerate" in str(excinfo.value)


def test_script_prompt_emergent_language_and_single_story_branch():
    """QA (steering arrival, template side): the rewritten script template's
    emergent-length and digest-discipline lines survive formatting into the
    built prompt, the dead fullness ask is gone, and the k<=1 coverage branch
    renders its single-story instruction."""
    p3 = generate.build_script_prompt(A_DAY, "A", "Narrative body.",
                                      _inputs_for([slot(1), slot(2), slot(3)]))
    assert "LENGTH is EMERGENT, never filled" in p3
    assert "CEILINGS and guides, NOT floors" in p3
    assert "one breath per COVERED story only" in p3
    # whitespace-normalized: the template wraps this sentence mid-phrase
    assert ("Do not tease, preview, or mention the stories this episode "
            "does not cover") in " ".join(p3.split())
    assert "never hears about a story the episode skips" in p3
    assert "up to ~90" in p3 and "up to ~70" in p3      # open/outro ceilings wired
    assert "aim for the FULL target" not in p3           # the dead fullness ask
    assert "covers all 3 stories" in p3                  # k == n branch
    p1 = generate.build_script_prompt(A_DAY, "A", "Narrative body.",
                                      _inputs_for([slot(1)]))
    assert "cover the LEAD only" in p1                   # k <= 1 branch


def test_amended_steering_reaches_the_sent_prompts(migrated_con, fake_model):
    """QA (prompt-REACHING at the _chat boundary, one run, all three sent
    prompts): the narrative call carries BOTH steering surfaces (the template's
    rewritten TIERED STRUCTURE — lowercase 'single longest' — and the injected
    per-slot budget lines — uppercase 'single LONGEST'), the editor call
    carries the amended 450 tier floor with the stale ~300 gone, and the script
    call carries the coverage + emergent-band contract. Offline proves ARRIVAL;
    only the live re-run proves obedience."""
    slots = [slot(i) for i in range(1, 7)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    run(migrated_con, date=A_DAY)
    json_calls = [c for c in fake_model.calls if c["json_mode"]]
    n_prompt = json_calls[0]["prompt"]
    assert "single longest story of the day" in n_prompt          # template
    assert "the lead alone carries the largest single share" in n_prompt
    assert "and never under 550" in n_prompt                      # template floor
    assert "single LONGEST story of the day" in n_prompt          # budget line
    e_prompt = json_calls[1]["prompt"]
    assert "never cuts the LEAD below ~450 words" in e_prompt
    assert "target ~640 words" in e_prompt                        # editor knows the lead target
    assert "~300" not in e_prompt                                 # stale floor gone
    s_prompt = [c for c in fake_model.calls if not c["json_mode"]][0]["prompt"]
    assert "THIS EPISODE COVERS (binding): This episode covers 5 stories" in s_prompt
    assert "(~1650 words)" in s_prompt          # ceiling-only (floor REMOVED)
    assert "up to ~11 minutes" in " ".join(s_prompt.split())
    assert "600-1650" not in s_prompt and "4-11 minutes" not in s_prompt


def test_pipeline_scopes_disclosures_to_covered_stories_LIVENESS(
        migrated_con, fake_model):
    """QA LIVENESS (BUG17 rule — the wiring, not the helpers): every other
    scoping test passes `covered` explicitly, so ONLY this test fails if
    run_generate stops passing covered_slots at either call site. A 6-story
    edition carries an override AND a revival on uncovered slot 6; the script
    voices covered stories 1-5 only and never mentions slot 6's disclosures:
    - if _validate_script loses covered=..., story 6's unvoiced override
      hard-flags on both attempts and the run ABORTS (this test goes red);
    - if the labels feed loses covered, story 6's OVERRIDE/REVIVAL labels leak
      into the sent script prompt (the prompt asserts go red).
    Bite proven by the comment-out procedure in the QA pass of 2026-07-14."""
    slots = [slot(i) for i in range(1, 6)] + [
        slot(6, override=True, tags=(),
             revived=({"topic": "T", "last_covered": "2026-07-01"},))]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = _digest_script(slots[:5], 700)   # covered stories only
    rep = run(migrated_con, date=A_DAY)                  # ships — no abort
    assert rep.script_words == 700
    s_prompt = [c for c in fake_model.calls if not c["json_mode"]][0]["prompt"]
    assert "story 5: corroboration for the ear" in s_prompt   # covered fed
    assert "story 6" not in s_prompt                          # uncovered not fed
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1])
    assert entry["status"] == "ok"


def test_cost_sink_records_every_api_reaching_attempt(monkeypatch):
    """QA (money honesty at the unit): the sink row lands BEFORE validation can
    reject — two API-reaching attempts that both fail validation leave exactly
    two billed rows behind the GenerateError, priced from the usage the API
    actually returned at the STEP'S OWN SEAT rates. B2 conscious update: the
    script step rides the Claude Haiku seat now, so the per-attempt oracle is
    llm.cost_fields(resolve_seat('script')) — Haiku 1.00/5.00 — not the writer
    _step_cost the pre-B2 version priced against (a global writer rate here
    would be the exact ledger fork B2 exists to prevent). Strengthened: the
    rows must also carry the seat's model/lane and charged-honesty keys."""
    def chat(key, prompt, max_tokens, temperature, json_mode):
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": "body"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sink = []

    def reject(content):
        raise ValueError("always rejected")

    with pytest.raises(generate.GenerateError):
        generate.call_llm("k", "p", "script", 100, 0.4, False,
                          validate=reject, cost_sink=sink)
    assert [(e["step"], e["attempt"]) for e in sink] == [("script", 1),
                                                         ("script", 2)]
    script_cfg = llm.resolve_seat("script")
    per = llm.cost_fields(script_cfg,
                          {"prompt_tokens": 1000, "completion_tokens": 500}
                          )["usd_charged"]
    assert per == pytest.approx(0.0035)  # Haiku 1.00/5.00, NOT writer 2.50/10.00
    assert all(e["usd"] == per and e["usd"] > 0 for e in sink)
    for e in sink:
        assert e["model"] == script_cfg.model == "claude-haiku-4-5"
        assert e["lane"] == "api"
        assert e["usd"] == e["usd_charged"] == e["usd_shadow"]


def test_ok_run_ledger_and_log_arithmetic_no_double_count(migrated_con, fake_model):
    """QA (money honesty, the OK path — dispatch item 5's arithmetic probe): a
    clean run bills each LLM step ONCE. The attempt ledger holds exactly one
    attempt per step; the OK log entry's steps are report.steps (the display
    breakdown — no raw 'attempt' rows); total_usd is the sum of those steps;
    and the ledger's total equals the LLM steps' total to the microdollar —
    two records, one spend, zero double-counting."""
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY)
    assert [(e["step"], e["attempt"]) for e in rep.attempt_ledger] == [
        ("narrative", 1), ("editor", 1), ("script", 1)]
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1])
    assert entry["status"] == "ok"
    assert all("attempt" not in s for s in entry["steps"])
    assert entry["total_usd"] == pytest.approx(
        round(sum(s.get("usd") or 0 for s in entry["steps"]), 6))
    llm_usd = sum(s["usd"] for s in entry["steps"]
                  if s["step"] in ("narrative_A", "editor_pass", "script_adapt"))
    assert sum(e["usd"] for e in rep.attempt_ledger) == pytest.approx(llm_usd)


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
    assert "generate the record first" in str(excinfo.value)  # M6 reword


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
    # M6 sequence: narrative -> editor -> script; abort at the SCRIPT estimate.
    # (B2: _est_cost grew a step arg — seat-priced estimates — so the stub
    # accepts it; the sequenced values keep the same meaning.)
    ests = iter([0.0001, 0.0001, 999.0])
    monkeypatch.setattr(generate, "_est_cost",
                        lambda p, m, step="narrative": next(ests))
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY, refresh=False)
    msg = str(excinfo.value)
    assert "would exceed the run budget cap" in msg
    assert "narrative was NOT persisted" in msg
    row = migrated_con.execute(
        "SELECT narrative_text, script_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"] is None and row["script_text"] is None
    assert len(fake_model.calls) == 2  # narrative + editor ran; script never called


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
    assert step_names == [
        "rank_select", "narrative_A", "editor_pass", "script_adapt"
    ]  # M6: the editor step merges between writer and script
    expected_total = round(sum(s.get("usd") or 0 for s in tc["steps"]), 6)
    assert tc["total_usd"] == expected_total


def test_failed_run_folds_accumulated_spend_into_log(migrated_con, fake_model):
    """BUG-6/32 family (NL-63 M2 obs): a run that aborts at the script step
    still spent real money — narrative, editor, and BOTH degenerate-stub script
    attempts all billed before the raise. The failed generation_log entry must
    carry that spend (total_usd + a per-attempt ledger), never a null."""
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = "Way too short. " + generate.SIGNOFF  # stub -> abort
    with pytest.raises(generate.GenerateError):
        run(migrated_con, date=A_DAY)
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1]
    )
    assert entry["status"] == "failed"
    assert entry.get("total_usd") is not None and entry["total_usd"] > 0
    steps = entry.get("steps") or []
    names = [s.get("step") for s in steps]
    assert "narrative" in names and "editor" in names
    # BOTH failed script attempts billed and are on the record (not just one)
    assert sum(1 for s in steps if s.get("step") == "script") == 2
    # the logged total is the honest sum of the per-attempt costs
    assert entry["total_usd"] == pytest.approx(
        round(sum(s.get("usd") or 0 for s in steps), 6))


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
    assert [s["step"] for s in entry["steps"]] == [
        "narrative_A", "editor_pass", "script_adapt"
    ]
    assert entry["editor"].startswith("editor: ")   # note logged (M6)
    assert entry["tiers"] == ["full", "medium"]     # per-story tiers logged
    assert entry["audio"] is None                   # engine absent in sandbox


# --- call_llm error taxonomy (HTTP layer, loopback fake server) ------------------------------

@pytest.fixture
def llm_http(fake_api, monkeypatch):
    # Both lanes at the loopback: narrative rides the writer seat (gpt-4o via
    # the openai seam url); editor/script ride the Claude Haiku seat via the
    # anthropic module endpoint (B2). Same fake server serves both shapes.
    monkeypatch.setattr(ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions")
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL", fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
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
    # B2: script rides the Claude lane — the cap-hit is stop_reason
    # 'max_tokens', which the provider must map to finish_reason 'length' for
    # call_llm's truncation guard to fire (llm._STOP_REASON_MAP's load-bearing
    # row). Same contract as pre-B2: named precisely, retried once, then fails.
    llm_http.add_route(
        "/v1/messages", status=200,
        body=anthropic_envelope("{", input_tokens=10, output_tokens=3200,
                                stop_reason="max_tokens"),
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
    """NL-63 M2 amended contract: code enforces every position — 1 full / 2
    medium / 3 medium (pinned; the demote-to-quick call is RETIRED, exactly 3
    full-picture) / 4+ quick. Every tier — quick/In Brief included — is a
    STRUCTURED story that MUST carry why_it_matters + watch_for."""
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

    # Slot 3 is PINNED to full-picture (medium) now — quick is NOT legal there
    # (a demoted slot 3 would leave only 2 full-picture stories, violating the
    # exactly-3 ruling).
    s3_quick = stories_payload(slots5)
    s3_quick["stories"][2]["tier"] = "quick"
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(s3_quick, slots5, "A")
    assert "tier 'quick' not allowed at this position" in str(excinfo.value)

    # An In Brief (quick) story MISSING its why_it_matters is an ERROR now — the
    # amended register is a structured mini-story, not a bare-lede snippet.
    stripped = stories_payload(slots5)
    del stripped["stories"][4]["why_it_matters"]
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(stripped, slots5, "A")
    assert "why_it_matters missing/empty (tier quick)" in str(excinfo.value)


def test_A2_quick_in_brief_renders_structured_with_trust_furniture():
    """NL-63 M2: the In Brief (quick) register carries all three movements now
    (the dead <=60-word snippet is gone) AND its trust furniture."""
    slots4 = [slot(i) for i in range(1, 5)]
    slots4[3] = slot(4, tags=(), corroboration_count=1, outlets=("Solo",))
    stories, _ = generate.validate_narrative_payload(
        stories_payload(slots4), slots4, "A"
    )
    text = generate.assemble_narrative(A_DAY, "A", stories, _inputs_for(slots4))
    # ALL four stories now carry a why-movement and a watch-movement (the In
    # Brief story included).
    movement_labels = sum(
        text.count(f"**{w}:**") for w in generate.WHY_FRAMINGS
    )
    watch_labels = sum(
        text.count(f"**{w}:**") for w in generate.WATCH_FRAMINGS
    )
    assert movement_labels == 4 and watch_labels == 4
    # …and the In Brief story's trust furniture is intact (meta-line).
    assert "Reported by 1 named outlet — Solo. Here for:" in text


def test_A2_tier_word_bands_warn_only(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fat = stories_payload(slots)
    # NL-63 M2: the full (lead) band is now (450, 900) — overshoot it clearly.
    fat["stories"][0]["why_it_matters"] = "Very long movement. " * 400  # >>900 words
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
    stay hard, and the sign-off is still appended. (NL-58 ruling 2: the spoken
    caveat is no longer appended — assertion flipped below.)"""
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
    assert "spoken caveat" not in joined_warns.lower()            # NL-58: removed
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
    # B2 fake migration: rank rides the Claude API lane (see test_BUG7 above).
    monkeypatch.setattr(ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions")
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL", fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
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
        "/v1/messages", status=200,
        body=anthropic_envelope(payload, input_tokens=500, output_tokens=100),
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
# --- M6: the editor pass + pipeline audio ------------------------------------------------

def _fake_audio_ok(monkeypatch, out_paths):
    from newslens import audio as audio_mod

    def fake_generate_audio(script_text, out_path, engine="kokoro", openai_key="",
                            **kw):
        out_paths.append(str(out_path))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"RIFFfake")
        return audio_mod.AudioResult(
            path=str(out_path), engine=engine, duration_s=300.0,
            gen_time_s=70.0, est_cost_usd=0.0, detail={},
        )

    monkeypatch.setattr(audio_mod, "generate_audio", fake_generate_audio)


from pathlib import Path


def test_editor_count_change_degrades_to_draft(migrated_con, fake_model):
    """Never-adds-facts guard 1: an editor payload that DROPS (or adds) a
    story fails the count guard -> retry -> DEGRADED disclosure, and the
    UNEDITED draft is what ships."""
    slots = [slot(1), slot(2)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    dropped = stories_payload(slots)
    dropped["stories"] = dropped["stories"][:1]
    fake_model.editor = dropped
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    degraded = [w for w in rep.warnings if w.startswith("editor: DEGRADED")]
    assert len(degraded) == 1
    assert "editor changed the story count" in degraded[0]
    assert "Rewritten headline 1" in rep.narrative_text  # the draft shipped
    assert not any(s["step"] == "editor_pass" for s in rep.steps)  # no cost step


def test_editor_tier_change_degrades_to_draft(migrated_con, fake_model):
    """Never-adds-facts guard 2: re-tiering a story is a guard failure."""
    slots = [slot(1), slot(2)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    retiered = stories_payload(slots)
    retiered["stories"][1]["tier"] = "quick"
    retiered["stories"][1].pop("why_it_matters", None)
    retiered["stories"][1].pop("watch_for", None)
    fake_model.editor = retiered
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    degraded = [w for w in rep.warnings if w.startswith("editor: DEGRADED")]
    assert len(degraded) == 1 and "editor changed a tier" in degraded[0]


def test_editor_success_discloses_and_merges_cost(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    tightened = stories_payload(slots)
    tightened["stories"][0]["why_it_matters"] = "Tighter and better."
    fake_model.editor = tightened
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    notes = [w for w in rep.warnings if w.startswith("editor: ") and "words" in w]
    assert len(notes) == 1
    assert "% tighter)" in notes[0]
    assert "Tighter and better." in rep.narrative_text  # the EDIT shipped
    editor_steps = [s for s in rep.steps if s["step"] == "editor_pass"]
    # B2: the editor seat runs on the Claude API lane (Haiku 4.5).
    assert len(editor_steps) == 1 and editor_steps[0]["model"] == "claude-haiku-4-5"
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1]
    )
    assert entry["editor"] == notes[0]


def test_BUG8_validator_violating_edit_must_be_handled_not_crash(
    migrated_con, fake_model
):
    """KNOWN-RED candidate (BUG-8) — self-contained acceptance: mandate 2
    says ALL narrative validators re-run on the EDITED payload, and mandate 3
    says degrade-never-die. But validate_narrative_payload on the edited
    payload (generate.py ~:1095) runs OUTSIDE both the editor try/except and
    any GenerateError wrapper — an edit that passes the shape guards yet
    violates a validator (here: tightening a lede clips the mandatory revival
    date) raises a raw ValueError: unhandled, un-logged (run_generate only
    logs GenerateError), and fatal to the run. Contract: a handled failure —
    either degrade to the (re-validated) draft with disclosure, or a
    GenerateError that reaches the failure log. This test passes when either
    lands; it must never see a bare ValueError."""
    slots = [slot(1, revived=({"topic": "T", "last_covered": "2026-07-01"},))]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)  # draft carries the date
    clipped = stories_payload(slots)
    clipped["stories"][0]["lede"] = "Tightened lede without the date. Second sentence."
    fake_model.editor = clipped
    fake_model.script = compliant_script(slots)
    # FIX LANDED (ADR-0009 §1) — freeze the implemented branch: the edit is
    # DISCARDED with disclosure, the draft re-validates and ships.
    rep = run(migrated_con, date=A_DAY, refresh=False)
    disclosures = [w for w in rep.warnings if "editor: output FAILED validation" in w]
    assert len(disclosures) == 1
    assert "degraded to the writer's draft" in disclosures[0]
    assert "the edit was discarded" in disclosures[0]
    assert "2026-07-01" in rep.narrative_text  # the draft's revival date shipped
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1]
    )
    assert entry["editor"].endswith("[DISCARDED: failed validation]")


def test_BUG8_draft_also_failing_is_a_logged_visible_error(migrated_con, fake_model):
    """The other lawful branch: when the DRAFT itself violates a validator
    (shape-check passed, validator did not), the degrade lands on a draft
    that also fails — a logged GenerateError, never a raw crash."""
    slots = [slot(1, revived=({"topic": "T", "last_covered": "2026-07-01"},))]
    seed_briefing(migrated_con, A_DAY, slots)
    bad_draft = stories_payload(slots)
    bad_draft["stories"][0]["lede"] = "No date here. Second sentence also dateless."
    fake_model.narrative = bad_draft
    fake_model.editor = bad_draft  # editor echoes the same broken content
    fake_model.script = compliant_script(slots)
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY, refresh=False)
    assert "failed validation" in str(excinfo.value)
    log_lines = (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()
    entry = json.loads(log_lines[-1])
    assert entry["status"] == "failed"
    assert isinstance(entry.get("warnings"), list)  # retention on failures too


def test_audio_lands_on_the_record(migrated_con, fake_model, monkeypatch):
    out_paths = []
    _fake_audio_ok(monkeypatch, out_paths)
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    assert out_paths == [str(paths.DATA_DIR / "briefings" / f"{A_DAY}.wav")]
    row = migrated_con.execute(
        "SELECT audio_file_path FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["audio_file_path"] == out_paths[0]  # persisted on the record
    # P3.1 item 4 pin FLIP (mechanical, intended): the configured default
    # engine is now openai (ear-test ruling 2026-07-06); the contract this
    # test pins — config's engine flows through to the step log and the
    # disclosure line — is unchanged.
    assert any(s["step"] == "tts_openai" for s in rep.steps)
    assert any(w.startswith("audio: openai — 5.0 min in 70s") for w in rep.warnings)
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1]
    )
    assert entry["audio"] == out_paths[0]


def test_audio_failure_degrades_with_disclosure(migrated_con, fake_model, monkeypatch):
    from newslens import audio as audio_mod

    def broken(*a, **kw):
        raise audio_mod.AudioError("engine wedged")

    monkeypatch.setattr(audio_mod, "generate_audio", broken)
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)  # never a dead run
    assert any(
        w == "audio: SKIPPED — engine wedged (the text briefing is unaffected)"
        for w in rep.warnings
    )
    row = migrated_con.execute(
        "SELECT audio_file_path, narrative_text FROM briefings WHERE date = ?",
        (A_DAY,),
    ).fetchone()
    assert row["audio_file_path"] is None
    assert row["narrative_text"]  # the text briefing landed regardless


def test_sample_audio_lands_beside_the_sample_and_record_is_byte_identical(
    migrated_con, fake_model, monkeypatch
):
    """The gate-spec pin, landed: a default-flag sample request forces
    refresh=False, writes its audio NEXT TO the sample file, and leaves the
    record row byte-identical (hashed before/after)."""
    import hashlib

    out_paths = []
    _fake_audio_ok(monkeypatch, out_paths)
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    run(migrated_con, date=A_DAY, refresh=False)  # the record, with audio

    def row_hash():
        row = migrated_con.execute(
            "SELECT * FROM briefings WHERE date = ?", (A_DAY,)
        ).fetchone()
        return hashlib.sha256(repr(tuple(row)).encode()).hexdigest()

    before = row_hash()
    out_paths.clear()
    fake_model.narrative = stories_payload(slots, variant="B", my_read="A judgment.")
    # DEFAULT flags: refresh not passed — sample mode must force it off.
    rep = generate.run_generate(
        date=A_DAY, con=migrated_con, env=ENV, variant_override="B"
    )
    assert rep.sample is True
    assert out_paths == [
        str(paths.DATA_DIR / "briefings" / f"{A_DAY}-variant-B-SAMPLE.wav")
    ]
    assert row_hash() == before  # the record did not move by one byte
# --- M6 fix loop: A7/A8 text-quality package pins ---------------------------------------

def test_A7_unsanctioned_framing_is_a_validation_error():
    slots = [slot(1)]
    payload = stories_payload(slots)
    payload["stories"][0]["why_label"] = "Why this slaps"
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(payload, slots, "A")
    assert "why_label 'Why this slaps' not in the sanctioned menu" in str(excinfo.value)
    missing = stories_payload(slots)
    del missing["stories"][0]["watch_label"]
    with pytest.raises(ValueError) as excinfo2:
        generate.validate_narrative_payload(missing, slots, "A")
    assert "watch_label None not in the sanctioned menu" in str(excinfo2.value)


def test_A7_assembly_renders_the_declared_framings():
    slots = [slot(1)]
    payload = stories_payload(slots)
    payload["stories"][0]["why_label"] = "The stakes"
    payload["stories"][0]["watch_label"] = "The next test"
    stories, _ = generate.validate_narrative_payload(payload, slots, "A")
    text = generate.assemble_narrative(A_DAY, "A", stories, _inputs_for(slots))
    assert "**The stakes:**" in text and "**The next test:**" in text
    assert "**Why it matters:**" not in text  # the label is the writer's choice


def test_A7_editor_may_not_change_labels(migrated_con, fake_model):
    """Code guard: even a SANCTIONED relabel by the editor degrades — A7
    labels belong to the writer."""
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    relabeled = stories_payload(slots)
    relabeled["stories"][0]["why_label"] = "The debate"  # sanctioned, still barred
    fake_model.editor = relabeled
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    degraded = [w for w in rep.warnings if w.startswith("editor: DEGRADED")]
    assert len(degraded) == 1
    assert "editor changed why_label (A7 labels are the writer's)" in degraded[0]


def test_A7_all_one_rhythm_warns():
    slots = [slot(1), slot(2), slot(3)]
    payload = stories_payload(slots)
    for s in payload["stories"]:
        s["why_label"] = "Why markets care"
    _, warns = generate.validate_narrative_payload(payload, slots, "A")
    assert any("A7 wants varied rhythm" in w for w in warns)
    varied = stories_payload(slots)  # fixture varies labels by position
    _, warns2 = generate.validate_narrative_payload(varied, slots, "A")
    assert not any("varied rhythm" in w for w in warns2)


def test_A8_lead_near_full_picture_length_warns():
    # NL-63 M2: the A8 flag threshold rises with the doubled full-picture
    # register — a briefed lead should clear ~440 words comfortably.
    slots = [slot(1)]
    thin = stories_payload(slots)  # fixture lead is well under 440 words
    _, warns = generate.validate_narrative_payload(thin, slots, "A")
    assert any("near full-picture length" in w for w in warns)
    deep = stories_payload(slots)
    deep["stories"][0]["why_it_matters"] = "specific detail " * 250  # ~500+ words
    _, warns2 = generate.validate_narrative_payload(deep, slots, "A")
    assert not any("near full-picture length" in w for w in warns2)


def test_A8_delete_on_sight_and_never_add_facts_prompt_guards():
    text = (paths.PROMPTS_DIR / generate.PROMPT_EDITOR).read_text(encoding="utf-8")
    assert "DELETE ON SIGHT" in text            # priority-0, canonized examples
    assert "YOU NEVER ADD FACTS" in text        # the one hard constraint, retained
    assert "why_label / watch_label" in text    # labels named as PRESERVE ABSOLUTELY


def test_editor_budget_abort_routes_through_the_degrade_path(
    migrated_con, fake_model, monkeypatch
):
    """The editor's own cap-abort no longer kills the run — it degrades with
    the estimate named, and the pipeline continues to script + persist."""
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    ests = iter([0.0001, 999.0, 0.0001])  # narrative ok, EDITOR over, script ok
    monkeypatch.setattr(generate, "_est_cost",
                        lambda p, m, step="narrative": next(ests))
    rep = run(migrated_con, date=A_DAY, refresh=False)
    degraded = [w for w in rep.warnings if w.startswith("editor: DEGRADED")]
    assert len(degraded) == 1 and "editor pass estimate" in degraded[0]
    assert not any(s["step"] == "editor_pass" for s in rep.steps)
    assert rep.script_text  # the run survived to the script pass
    row = migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"]  # and persisted


def test_warnings_and_framings_are_retained_in_ok_log_entries(
    migrated_con, fake_model
):
    slots = [slot(1), slot(2)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1]
    )
    assert entry["warnings"] == rep.warnings          # full array, verbatim
    assert any("audio: SKIPPED" in w for w in entry["warnings"])
    assert entry["framings"] == ["Why it matters", "Why markets care"]
# --- M7 carryovers 18a/18b: hedge-ratio tripwire + draft forensics --------------------------

def test_hedge_ratio_tripwire_fires_on_qualifier_stripping(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    hedged = stories_payload(slots)
    hedged["stories"][0]["why_it_matters"] = (
        "Officials say this could raise costs and may slow approvals; analysts "
        "reportedly expect delays, though the timeline is unclear."
    )
    fake_model.narrative = hedged
    stripped = stories_payload(slots)
    stripped["stories"][0]["why_it_matters"] = (
        "This raises costs and slows approvals; analysts see delays on a set "
        "timeline."
    )
    fake_model.editor = stripped
    fake_model.script = compliant_script(slots)
    rep = run(migrated_con, date=A_DAY, refresh=False)
    trip = [w for w in rep.warnings if w.startswith("editor hedge-ratio:")]
    assert len(trip) == 1
    assert "check that epistemic qualifiers weren't stripped" in trip[0]


def test_draft_stories_forensics_in_ok_log_entries(migrated_con, fake_model):
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    draft = stories_payload(slots)
    tightened = stories_payload(slots)
    tightened["stories"][0]["why_it_matters"] = "Tighter."
    fake_model.narrative = draft
    fake_model.editor = tightened
    fake_model.script = compliant_script(slots)
    run(migrated_con, date=A_DAY, refresh=False)
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()[-1]
    )
    assert entry["draft_stories"] == draft["stories"]     # pre-edit forensics
    assert entry["stories"][0]["why_it_matters"] == "Tighter."  # the final text
