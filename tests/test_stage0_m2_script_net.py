"""Stage-0 M2 item 4 — the script lane's continuity net (M0 finding F2).

RED-2 itself lives in tests/test_stage0_m0_coldstart.py (its strict-xfail
marker comes off in this milestone). This file is the net's own contract:
what it catches, what it must NOT catch, and the prompt half — the callback
license is DATA-GATED, the same day-one-silence family every other surface
got at M0.

The two halves matter separately. The prompt gate stops the model being
*invited* to fabricate continuity on day one; the detector catches it when the
model does it anyway. Neither is sufficient: a prompt is a request, and a
validator that fires on a licensed callback would make the product's own
memory unusable.

Sandbox: tree conftest autouse fixtures. $0 — every seam injected.
"""
from __future__ import annotations

import json

import pytest

from newslens import db, generate, memory, memory_core, paths

from test_generate import compliant_script, seed_briefing, slot, stories_payload

DATE = "2026-07-25"
PRIOR = "2026-07-24"
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}


@pytest.fixture
def virgin_con():
    db.migrate()
    con = db.connect()
    yield con
    con.close()


def _inputs(con, date=DATE, slots=None):
    slots = slots or [slot(1, tags=(), mem=())]
    seed_briefing(con, date, slots)
    return generate.load_briefing_inputs(con, date)


def _thread_with_history(con, topic, date, what="The corridor closed."):
    """A followed thread carrying ONE dated ledger delta that predates the
    edition — the minimum 'real thread data' the callback license names."""
    memory.add_thread(con, topic)
    tid = con.execute("SELECT id FROM memory WHERE topic = ?",
                      (topic,)).fetchone()["id"]
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, 1, 'advances', ?, 'It matters.', '[\"S1\"]')",
        (tid, date, what))
    con.commit()
    return tid


# ===========================================================================
# The detector — an unsupported spoken continuity claim reaches the record
# ===========================================================================

DAY_ONE_CLAIMS = [
    "As we covered last week, the corridor fight deepened.",
    "We have been tracking this story for weeks.",
    "We've been following the blockade since it started.",
    "As we reported on Tuesday, the vote slipped.",
    "This is the third week we've tracked this.",
    "Regular listeners will remember the first vote.",
    "We told you this would come back.",
]


@pytest.mark.parametrize("claim", DAY_ONE_CLAIMS)
def test_a_day_one_spoken_continuity_claim_is_named_in_the_findings(
        virgin_con, claim):
    """BORN-RED. Each shape must reach the record BY NAME — 'a finding fired'
    is not enough for a warning a human has to act on."""
    con = virgin_con
    inputs = _inputs(con)
    findings = generate.script_continuity_findings(
        con, claim + " " + compliant_script(inputs["slots"]), inputs, DATE)
    assert findings, f"no finding for {claim!r}"
    joined = " ".join(findings).lower()
    assert any(w in joined for w in claim.lower().split()[:4]), (
        f"the finding did not name the claim: {findings}")


def test_the_day_one_episode_ships_its_continuity_claims_into_the_run_record(
        virgin_con, monkeypatch):
    """The run-level half: through run_generate, not the helper. A poisoned
    day-one script must leave a WARNING in the report — the surface the
    principal (and the Stage-0 tester's debrief) actually reads."""
    import time as _time
    con = virgin_con
    slots = [slot(1, tags=(), mem=())]
    seed_briefing(con, DATE, slots)
    poisoned = ("Good morning. As we covered last week, the corridor fight "
                "deepened — we have been tracking this story for weeks. "
                + compliant_script(slots))

    def chat(key, prompt, max_tokens, temperature, json_mode):
        if json_mode:
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(stories_payload(slots))}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": poisoned}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    rep = generate.run_generate(date=DATE, con=con, env=dict(ENV), refresh=False)
    record = " || ".join(rep.warnings).lower()
    assert "as we covered" in record or "been tracking" in record


def test_the_net_stays_quiet_on_a_clean_day_one_script(virgin_con):
    """The false-positive floor: an ordinary day-one episode draws NOTHING.
    A net that warns on every edition is a net nobody reads."""
    con = virgin_con
    inputs = _inputs(con)
    clean = compliant_script(inputs["slots"])
    assert generate.script_continuity_findings(con, clean, inputs, DATE) == []


# ===========================================================================
# The licensing half — real history licenses the claim
# ===========================================================================

def test_a_real_prior_edition_plus_thread_history_licenses_the_callback(
        virgin_con):
    """The net must NOT fire when the claim is TRUE. A prior edition on record
    AND a dated ledger delta predating this edition is exactly the thread-arc
    callback the prompt licenses; flagging it would make the product's own
    memory unusable."""
    con = virgin_con
    _thread_with_history(con, "Corridor Blockade", PRIOR)
    seed_briefing(con, PRIOR, [slot(1, title="Corridor", tags=(),
                                    mem=("Corridor Blockade",))])
    inputs = _inputs(con, DATE, [slot(1, title="Corridor", tags=(),
                                      mem=("Corridor Blockade",))])
    text = ("We have been tracking the corridor blockade since Jul 24. "
            + compliant_script(inputs["slots"]))
    assert generate.script_continuity_findings(con, text, inputs, DATE) == []


def test_a_prior_edition_alone_does_not_license_a_thread_arc_claim(
        virgin_con):
    """Edition 2 with no ledger history on the story: 'the third week we've
    tracked this' is still a fabrication. The license is REAL history, not
    'we have shipped before'."""
    con = virgin_con
    seed_briefing(con, PRIOR, [slot(1, title="Other", tags=(), mem=())])
    inputs = _inputs(con, DATE, [slot(1, title="New Story", tags=(), mem=())])
    text = ("This is the third week we've tracked this. "
            + compliant_script(inputs["slots"]))
    assert generate.script_continuity_findings(con, text, inputs, DATE), (
        "an arc claim on a thread with no ledger record went unflagged")


def test_the_mandatory_revival_disclosure_is_never_flagged(virgin_con):
    """The worst false positive available to this net, pinned shut.

    "We last covered this on July 5" is a MANDATORY spoken disclosure —
    validate_script warns when a revival date is missing. A continuity net that
    flagged the show for making a required statement would be worse than no
    net: it would train the reader to ignore the findings list. Two independent
    exemptions cover it (a revived thread IS real prior coverage, and the
    demanded date is exempt outright); this pin holds if either survives."""
    con = virgin_con
    slots = [slot(1, tags=(), mem=(),
                  revived=({"topic": "Corridor", "last_covered": "2026-07-05"},))]
    inputs = _inputs(con, DATE, slots)
    text = ("We last covered this on July 5. "
            + compliant_script(inputs["slots"]))
    assert generate.script_continuity_findings(con, text, inputs, DATE) == []


def test_a_source_attributed_continuity_claim_is_exempt(virgin_con):
    """The attribution exemption the narrative-side net already grants: a
    claim handed to a source is the source's, not the show's."""
    con = virgin_con
    inputs = _inputs(con)
    text = ("Reuters reports the corridor has been closed for weeks. "
            + compliant_script(inputs["slots"]))
    assert generate.script_continuity_findings(con, text, inputs, DATE) == []


def test_the_attribution_exemption_does_not_launder_a_you_recall_claim(
        virgin_con):
    """BORN-RED (gate F4). The exemption above is the net's one legal escape,
    and an attribution verb sitting anywhere in the sentence is enough to earn
    it — so a claim about the LISTENER'S memory could ride out on a source's
    back: "As you recall, officials said…". The show, not the source, is the
    one asserting the listener remembers; a source can never be the citation
    for the audience's own history with the show.

    Two assertions, two proof classes, kept honest:
      * the bare form is a CARRIED (born-green) control — it already fired,
        and must keep firing;
      * the attributed form is the born-red half — `|you` in
        _SELF_REFERENTIAL_RE (generate.py) is the only thing that closes it.
    """
    con = virgin_con
    inputs = _inputs(con)
    tail = compliant_script(inputs["slots"])

    bare = "As you recall, the corridor fight deepened. " + tail
    assert generate.script_continuity_findings(con, bare, inputs, DATE), (
        "the bare audience-memory claim stopped firing")

    escape = ("As you recall, officials said the corridor had been closed "
              "for weeks. ") + tail
    findings = generate.script_continuity_findings(con, escape, inputs, DATE)
    assert findings, (
        "an attribution verb laundered a claim about the LISTENER's memory "
        "past the source-attribution exemption")
    assert "as you recall" in " ".join(findings).lower(), (
        f"the finding did not name the claim: {findings}")


# ===========================================================================
# The prompt half — the callback license is DATA-GATED
# ===========================================================================

def test_the_script_prompt_withholds_the_callback_license_on_day_one(
        virgin_con):
    """BORN-RED. prompts/script_adapt.txt licenses 'thread-arc callbacks from
    real thread data' UNCONDITIONALLY today, with an exemplar that is exactly
    the fabrication shape. On edition 1 there is no thread data — the license
    must not be issued, and the prompt must say so."""
    con = virgin_con
    inputs = _inputs(con)
    prompt = generate.build_script_prompt(DATE, "A", "narrative", inputs)
    assert "third week we've tracked this" not in prompt, (
        "the day-one prompt still ships the thread-arc callback exemplar")
    low = prompt.lower()
    assert "first" in low and (
        "no prior" in low or "no thread" in low or "no previous" in low), (
        "the day-one prompt does not state the silence rule")


def test_the_script_prompt_issues_the_callback_license_when_history_is_real(
        virgin_con):
    """The other direction: with real thread history the license IS issued —
    the net must not become a silent ban on the product's own memory."""
    con = virgin_con
    _thread_with_history(con, "Corridor Blockade", PRIOR)
    seed_briefing(con, PRIOR, [slot(1, title="Corridor", tags=(),
                                    mem=("Corridor Blockade",))])
    inputs = _inputs(con, DATE, [slot(1, title="Corridor", tags=(),
                                      mem=("Corridor Blockade",))])
    prompt = generate.build_script_prompt(DATE, "A", "narrative", inputs)
    # Gate F1: the bare needle also matched the day-one withhold text
    # ("NO thread-arc callbacks"), so assert the license exemplar is present
    # AND the withhold is absent — vacuous-in-the-break-direction no more.
    assert "thread-arc callback" in prompt.lower()
    assert "no thread-arc callbacks" not in prompt.lower()


def test_the_narrative_side_nets_were_not_weakened(virgin_con):
    """RED-2's fix contract, second clause, as a mechanism: the narrative
    repetition net still bites on the empty ledger. The script net is ADDED
    machinery; nothing was relaxed to make room for it."""
    con = virgin_con
    slots = [slot(1, tags=(), mem=())]
    stories = [{"headline": "Blockade reinstated on the corridor",
                "lede": "The blockade was reinstated overnight.",
                "why_it_matters": "It matters."}]
    findings = generate.repetition_antecedent_findings(con, stories, slots, DATE)
    assert findings, "the narrative-side repetition net stopped biting"
