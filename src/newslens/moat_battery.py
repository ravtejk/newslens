"""moat_battery.py — the NL-75 Phase-2 blinded artifact battery (T1/T2/T3).

THE SPEC OF RECORD is the moat Executive Brief §4 (product-org/workspace/briefs/
2026-07-16--newslens--moat-strategy.md) and the pre-registered program table it
summarises (workspace/debates/2026-07-16--newslens--data-2.md §4, "Phase 1 —
Blinded artifact battery"). Three tests, one session, ~$3 authorised:

  T1  prose-first vs sectioned RETRO-PAIRS, furniture parity (colophon rides
      both — Content's law).  5 pairs, ~$0.75.
  T2  the EXPRESSION ABLATION 2x2 — ledger-context ON/OFF x prose/sectioned,
      over 3 editions.  ~$1.35.  Decision rule: context-on wins >=2/3 clearly
      -> the ranking is believed; indifferent -> the ranking inverts and "moat"
      demotes to "feature".
  T3  Concept B (THE STANDING FILE) static thread-first recomposition, read
      ALONGSIDE in the same session (Vero's demand, honoured).  $0 LLM.

This module is the HARNESS ONLY. It never scores, never decides, and — like
scripts/battery, whose conventions it deliberately extends rather than forks —
it never spends without `--run`:

  * DRY-RUN DEFAULT.  Every subcommand plans, prices, and discloses; ZERO LLM
    calls and ZERO writes until `--run`.  A cell that cannot be produced is
    reported BLOCKED in the dry run, at $0, with the reason named.
  * READ-ONLY ON THE RECORD.  The DB is opened via db.connect_readonly() and
    only SELECTed.  The briefing of record, the ledger, and rank are never
    touched.  Artifacts land under <DATA_DIR>/battery/<session-date>/phase2/.
  * THE CAP BINDS CHARGED DOLLARS.  Same named divergence as the writer
    battery (gate FIX-1, 2026-07-17): generate's edition cap binds on SHADOW
    (Onna's law); a principal-invoked bounded experiment bounds REAL spend,
    with each subscription arm's shadow disclosed per cell.  NOTE the cap is
    per INVOCATION, not per session — `plan` prints the session total so the
    ~$3 authorisation is checked against the sum, not against one run.
  * ONE MODEL, HELD FIXED.  This battery is not a model A/B (that is
    scripts/battery).  Every cell runs the writer seat's own model so the only
    variables are the ones the 2x2 declares.  A model swap here would confound
    the ablation, so there is deliberately no --arms flag.
  * BLIND PACK.  `pack` shuffles the produced cells within each read-set,
    writes them under blind/ as unlabelled artifacts, and seals the mapping in
    _KEY-do-not-open-until-scored.txt.  NO manifest ever lands in blind/.

BLINDING LIMITS, ON THE RECORD (Stig, data-2 §3): the form axis is not
blindable — prose and sections are visually distinguishable on sight — so T1's
blinding is ORDER-ONLY.  The context axis (T2's ON/OFF) is genuinely blind.
Randomised unlabelled order, fixed pre-registered questions, Medium ceiling;
never claim more (Hux's cap is program law).

Import-safe and offline-testable: prompt building, ablation, rendering, the
scoring hooks, the Concept-B pack and the blind pack need no key and no
network.  scripts/moat-battery is the thin launcher.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import analysis, battery, config, db, generate, llm, memory_core, paths, ranking

# --- the axes -------------------------------------------------------------

FORM_SECTIONED = "sectioned"
FORM_PROSE = "prose_first"
FORMS = (FORM_PROSE, FORM_SECTIONED)

CONTEXT_ON = "on"
CONTEXT_OFF = "off"

# The rung-(a) payload the ablation removes.  Bea's test is "ledger-context ON
# vs OFF" (data-2 §2, Mara) and rung (a) is exactly the thread_ledger +
# expired_watch hop that generate.load_briefing_inputs attaches — so those two
# are the DEFAULT ablation.  `baseline` (NL-77's cold-start backgrounder) is a
# DIFFERENT channel that landed after the program was registered; it is
# offered as an opt-in field, never silently folded into "context".
ABLATION_FIELDS: Dict[str, Tuple[str, type]] = {
    "ledger": ("thread_ledger", str),
    "watch": ("expired_watch", list),
    "baseline": ("thread_baseline", str),
}
DEFAULT_ABLATE = ("ledger", "watch")

# The prose-first COMPOSE prompt — a Content-owned artifact that does not exist
# in the tree yet.  `--form-mode compose` refuses (at $0, in the dry run) until
# it does; see FORM MODES in the module docstring's companion note below.
PROSE_FIRST_PROMPT = "narrative_prose_first.txt"

FORM_MODE_RENDER = "render"
FORM_MODE_COMPOSE = "compose"

# Pre-registered instruments, quoted from the record (never paraphrased here):
#   T1  — "pre-registered 1-tap verdict + three fixed questions per pair (what
#          did you learn / what's missing / which tomorrow)"
#          workspace/debates/2026-07-16--newslens--data.md:71
#          (the T1 decision rule words the second one "would miss":
#           data-2.md §4 T1 row)
#   T2  — "Blinded 2x2 reads"; rule: context-on wins >=2/3 clearly
#   T3  — Greta's question: "which one is the product you described"
#          (design.md, Concept B "cheapest test" — mornings, not screens)
# NOTE the 1-tap verdict INSTRUMENT itself is illegal until 08-06 (prompted
# daily interaction = day-30 contamination, Stig's taxonomy).  A session verdict
# inside this battery is legal; a longitudinal verdict stream is not.
T1_QUESTIONS = (
    "What did you learn?",
    "What's missing?  (the T1 rule words it: which sentences would you MISS)",
    "Which one would you want tomorrow?",
)
T2_QUESTIONS = (
    "What did you learn?",
    "What's missing?",
    "Which one would you want tomorrow?",
)
T3_QUESTION = ("Not \"which is prettier\" — which one is the product you "
               "described?")

# HSR §1 step 5, VERBATIM (research/2026-07-16--hsr-baseline.md).  §7 says the
# after-measurement repeats §1 identically, so this pattern is copied, not
# re-authored.  It deliberately over-fires ("against", "remains competitive")
# — every hit is adjudicated by hand.  Do not "improve" it.
HSR_SWEEP_PATTERN = (r"reinstat|resum|renew|re-?impos|again|once more|"
                     r"consecutive|last (week|covered|time|month)|previous|"
                     r"remains|continues|since|follows the")
_HSR_SWEEP_RE = re.compile(HSR_SWEEP_PATTERN, re.I)

# A dated anchor: "July 12", "Jul 12", "2026-07-12", "July 12, 2026".
_ANCHOR_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2})\b",
    re.I)

# The poisoned-antecedent bound (HSR §5.1(2) + §7): the 07-14 backfill wrote
# source-echoed diction INTO the ledger, so a post-plumbing callback that
# "finds" one of those rows is a FALSE hit.  §7 states the bound as
# "from-07-17": rows written before the Forward-Claim machinery landed are
# suspect as antecedents.  The worksheet FLAGS them; it never scores them.
POISONED_ANTECEDENT_BOUND = "2026-07-17"


# ---------------------------------------------------------------------------
# Inputs, the ablation seam, and the degenerate-cell guard
# ---------------------------------------------------------------------------

def load_inputs(con: sqlite3.Connection, date: str) -> Dict:
    """The EXISTING record's narrative inputs for `date`, read-only — the same
    load the live narrative pass and scripts/battery use.  Raises
    generate.GenerateError when there is no briefing row / no slots: the
    harness refuses, it never fabricates an edition.

    NL-107: outside any generating run, so the briefs are bound to the
    edition's publish stamp — the ablation cells must differ in the memory
    block alone, not in which run's analysis they happened to pick up."""
    inputs = generate.load_briefing_inputs(con, date)
    published_at = inputs["row"]["generated_at"]
    briefs: Dict[int, Optional[Dict]] = {}
    for s in inputs["slots"]:
        n = int(s["slot"])
        doc = analysis.coherent_valid_brief(con, date, n, published_at)
        if doc:
            briefs[n] = doc
    inputs["briefs_by_slot"] = briefs
    inputs["date"] = date          # assemble_narrative reads inputs['date']
    return inputs


def ablate_inputs(inputs: Dict, fields: Sequence[str] = DEFAULT_ABLATE) -> Dict:
    """A COPY of `inputs` whose slots carry no rung-(a) memory payload — the
    context-OFF cell of the 2x2.

    Copy, never mutate: the ON cell and the RENDER both keep the original
    slots, so the colophon/furniture is byte-identical across the ablation axis
    (Content's furniture-parity law) and the only difference reaching the model
    is the memory block itself."""
    unknown = [f for f in fields if f not in ABLATION_FIELDS]
    if unknown:
        raise ValueError(
            f"unknown ablation field(s) {', '.join(unknown)} — known: "
            f"{', '.join(sorted(ABLATION_FIELDS))}")
    slots = []
    for s in inputs["slots"]:
        t = dict(s)
        for f in fields:
            key, empty = ABLATION_FIELDS[f]
            t[key] = empty()
        slots.append(t)
    return dict(inputs, slots=slots)


def build_cell_prompt(inputs: Dict, context: str, form: str,
                      form_mode: str = FORM_MODE_RENDER,
                      ablate: Sequence[str] = DEFAULT_ABLATE) -> str:
    """The writer prompt for one cell.

    FORM MODES — the one genuinely under-specified axis, surfaced not guessed:
      * `render`  (default): form is a RENDER decision, so both forms share ONE
        draft and one prompt.  This is Inez's adopted synthesis — "prose-first
        is a change in the render, not in the record" (content.md, Room verdict
        Q2) — and it makes the form comparison confound-free at zero extra
        spend.
      * `compose`: form is a COMPOSITION decision, so the prose-first cell gets
        its own prompt (prompts/narrative_prose_first.txt).  This is what the
        Data council PRICED ("3 editions x 4 renders"), and the prompt file
        does not exist.  Refused, loudly, until Content ships it — a prompt is
        code (ENGINEERING.md) and the implementer does not author the writer's
        voice.
    """
    if form_mode == FORM_MODE_COMPOSE and form == FORM_PROSE:
        raise MissingArtifact(
            f"--form-mode compose needs prompts/{PROSE_FIRST_PROMPT} (the "
            "prose-first writer prompt). It does not exist in the tree; a "
            "writer prompt is a Content-owned artifact, not an implementer "
            "guess. Use --form-mode render (one draft, two renders — "
            "confound-free and $0 extra) or have Content ship the prompt.")
    src = inputs if context == CONTEXT_ON else ablate_inputs(inputs, ablate)
    return generate.build_narrative_prompt(src["date"], "A", src)


class MissingArtifact(Exception):
    """A cell needs an artifact the tree does not hold (a prompt, a ledger).
    Raised during PLANNING so the refusal costs $0."""


def ablation_is_degenerate(inputs: Dict,
                           ablate: Sequence[str] = DEFAULT_ABLATE) -> bool:
    """True when context-ON and context-OFF build the SAME prompt — i.e. this
    edition has no ledger content for the ablation to remove.

    This is the guard that keeps the 2x2 honest.  A degenerate cell pair spends
    real money to compare a thing against itself and would read as "context
    made no difference" for a reason that has nothing to do with the
    hypothesis.  The program's own honesty note says so: "only 07-14 has a live
    ledger; a 2x2 needs up to 2 hand-traced retro-mock ledgers" (data-2 §2,
    Mara).  Where those retro-mocks are absent, this returns True and the
    harness refuses to spend."""
    on = generate.build_narrative_prompt(inputs["date"], "A", inputs)
    off = generate.build_narrative_prompt(
        inputs["date"], "A", ablate_inputs(inputs, ablate))
    return on == off


def ledger_coverage(inputs: Dict) -> Tuple[int, int]:
    """(slots carrying a thread_ledger block, total slots) — the cheap
    preflight number the dry run prints per candidate date."""
    slots = inputs["slots"]
    return sum(1 for s in slots if (s.get("thread_ledger") or "").strip()), len(slots)


# ---------------------------------------------------------------------------
# The two renders — Sten's two-class split, mechanised
# ---------------------------------------------------------------------------
#
# content.md, Sten's split (adopted wholesale by Vera, binding for T1/T2):
#   1. THE INTERPRETIVE LAYER — why-it-matters, watch-for, continuity context,
#      "my read".  Judgment rendered as text.  "What dies: the furniture. What
#      survives: the function, as diction."  -> these labels DIE in prose-first.
#   2. THE EPISTEMIC LEDGER — corroboration counts, the wire-reuse caveat, tier
#      marks, source attribution, the override disclosure.  Testimony about the
#      text's own reliability.  "The trust machinery must not be beautiful."
#      -> these ride BOTH forms, byte-identical.  That IS furniture parity.
#
# So render_prose_first is render_sectioned with exactly the interpretive
# labels removed and their content flowed into the story's prose block.
# Everything else — header, table of contents, separators, override label,
# headline, colophon meta line, window line, corroboration caveat — is
# identical by construction, which is what makes the pair a FORM comparison
# rather than a furniture-removal comparison (Data's Content-lead dependency,
# data.md:71).

def render_sectioned(date: str, stories: List[Dict], inputs: Dict) -> str:
    """The SHIPPED render — the live product's form, unmodified.  Delegates to
    generate.assemble_narrative so this cell can never drift from what the
    reader actually gets.

    The one post-process is _normalise_window_end (gate F-B): assemble_
    narrative's window line falls back to a wall clock when the record has no
    `ran_at`, which is a per-render value in a blind artifact. See that
    function for why the fix lives here rather than in generate.py."""
    return _normalise_window_end(
        generate.assemble_narrative(date, "A", stories, inputs), inputs)


def render_prose_first(date: str, stories: List[Dict], inputs: Dict) -> str:
    """Concept A / Content Q2's prose-first Today, rendered from the same draft.

    PROTOTYPE: render-mode approximation — see build_cell_prompt's FORM MODES.
    Dropping a label is not the same as composing without one; a writer given
    the prose-first contract would join these moves differently.  This render
    is the confound-free cheap arm, and the report says so."""
    weekday, human = generate._spoken_date(date)
    slots = inputs["slots"]
    parts = [f"# NewsLens — {weekday}, {human}", "", "In today's briefing:"]
    parts += [f"- {st['headline']}" for st in stories]
    parts.append("")

    for st, slot in zip(stories, slots):
        parts.append("---")
        if slot.get("override"):
            # Epistemic furniture (class 2) — rides both forms verbatim.
            # NL-138 (ruling ④): the constant is the whole line now, no
            # {reason} to fill. Conformance is preserved by reading the SAME
            # constant assemble_narrative reads, which is why this render did
            # not need re-deriving — only the call shape changed.
            parts.append(generate.OVERRIDE_TEXT_LABEL)
            parts.append("")
        parts.append(f"**{st['headline']}**")
        parts.append("")
        # Class 1: the interpretive layer, labels dead, function surviving as
        # consecutive prose. Order preserved from the sectioned form so the
        # pair differs in FURNITURE only, never in content or sequence.
        for field in ("lede", "why_it_matters", "my_read", "watch_for"):
            text = (st.get(field) or "").strip()
            if text:
                parts.append(text)
                parts.append("")
        # Class 2: the colophon — built by the SAME code path as the sectioned
        # render so parity is structural, not remembered.
        parts.append(_colophon_line(st, slot, inputs))
        parts.append("")

    parts.append("---")
    parts.append("*" + _window_line(inputs) + "*")
    parts.append("")
    parts.append("*" + ranking.CORROBORATION_CAVEAT + "*")
    return "\n".join(parts)


def _colophon_line(st: Dict, slot: Dict, inputs: Dict) -> str:
    """The per-story epistemic colophon, verbatim in both forms (§5.7)."""
    matches = ", ".join(
        [t["name"] for t in slot.get("matched_tags", [])]
        + slot.get("matched_memory", []))
    if matches:
        here_for = matches
    elif slot.get("override"):
        here_for = "editor's override — see note above"
    else:
        here_for = "world-impact selection (no tag or thread match)"
    meta_line = slot.get("corroboration_label", "")
    outlets = slot.get("outlets") or []
    outlet_names = f" — {', '.join(outlets)}" if outlets else ""
    a_note = ""
    deep_views = inputs.get("deep_views") or {}
    slot_no = str(slot.get("slot", ""))
    if st.get("tier") in ("full", "medium") and deep_views \
            and deep_views.get(slot_no) not in ("available", None):
        a_note = " Analysis: unavailable — built from feed excerpts."
    return f"*{meta_line}{outlet_names}. Here for: {here_for}.{a_note}*"


def _window_line(inputs: Dict) -> str:
    wm = inputs.get("window_meta") or {}
    window = (wm.get("window") or {}) if isinstance(wm, dict) else {}
    start = (window.get("start_iso") or "window-start unavailable")[:16]
    # NO WALL CLOCK IN THE FALLBACK (gate F-B). `end` is a property of the
    # RECORD's fetch window, so when the record does not carry one the honest
    # answer is "unavailable" — matching the start_iso convention on the line
    # above. The old `or datetime.now(...)` stamped each cell's own render
    # clock in ISO form: a SECOND timestamp format, which _RENDER_STAMP_RE
    # does not match, so neither redact_for_blind nor _assert_blind_clean saw
    # it — QA F2's de-blinding channel reopened through a different door on
    # any record whose window_meta lacks ran_at.
    ran = wm.get("ran_at")
    end = ran[:16] if ran else WINDOW_END_SENTINEL
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return generate.WINDOW_LINE.format(timestamp=now_ts, start=start, end=end)


WINDOW_END_SENTINEL = "window-end unavailable"

# The wall clock generate.assemble_narrative substitutes for a missing
# `ran_at`, as it appears in the rendered window line: `… → 2026-07-25T01:07.`
_WINDOW_END_CLOCK_RE = re.compile(
    r"(Covers items fetched .*? → )(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})")


def _normalise_window_end(text: str, inputs: Dict) -> str:
    """Strip the render-clock fallback out of a rendered window line.

    generate.assemble_narrative carries its OWN copy of this line and its own
    `or datetime.now(...)` fallback (generate.py:1508). That twin is out of
    this diff by the gate's instruction — but leaving it alone would make the
    SECTIONED form stamp a wall clock while the prose form stamps the
    sentinel, so the two forms of one draft would disagree on furniture and a
    per-render value would still reach the blind copy. Normalising here fixes
    both forms in this harness's own layer, without touching generate.py.

    No-op when the record HAS a `ran_at`: then the line is a real record fact
    and identical across cells already."""
    wm = inputs.get("window_meta") or {}
    if isinstance(wm, dict) and wm.get("ran_at"):
        return text
    return _WINDOW_END_CLOCK_RE.sub(r"\1" + WINDOW_END_SENTINEL, text)


RENDERS = {FORM_SECTIONED: render_sectioned, FORM_PROSE: render_prose_first}


def furniture_parity(prose_a: str, prose_b: str) -> Tuple[bool, List[str]]:
    """Content's law, checkable: every epistemic-furniture line present in one
    form is present in the other.  Returns (ok, missing lines).

    Furniture lines are the italic small print — the colophon, the window line,
    the corroboration caveat — plus the override disclosure.  If a form drops
    one, the pair is testing furniture removal, not form (the exact confound
    Data flagged as the Content-lead dependency)."""
    def furniture(text: str) -> List[str]:
        out = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("*") and s.endswith("*") and len(s) > 2:
                out.append(s)
            elif s.startswith("**Outside your interests:**"):
                out.append(s)
        return out
    fa, fb = furniture(prose_a), furniture(prose_b)
    # The window line carries a wall-clock render timestamp; compare on the
    # stable prefix so parity is not defeated by the minute rolling over.
    def key(s: str) -> str:
        return s.split("Generated ")[0] if "Generated " in s else s
    ka, kb = [key(x) for x in fa], [key(x) for x in fb]
    # MULTISET, not membership (QA F5): two stories legitimately carry
    # textually identical colophons whenever they share an outlet set and a
    # tag. A membership test lets a form DROP one of them and still pass,
    # because the surviving duplicate satisfies `x in kb`. Counter compares
    # how MANY of each line each form carries, which is the law as written.
    ca, cb = Counter(ka), Counter(kb)
    missing: List[str] = []
    for line, n in (ca - cb).items():
        missing.extend([line] * n)
    for line, n in (cb - ca).items():
        missing.extend([line] * n)
    return (not missing), missing


# ---------------------------------------------------------------------------
# Scoring hooks (register-target-spec §7) and the HSR §7 worksheet
# ---------------------------------------------------------------------------

def prose_units(text: str) -> List[str]:
    """The reader-facing text units of a rendered artifact, for §1's marking.

    A rendered edition is not one blob of prose: it opens with a markdown
    header and a table of contents, separates stories with `---`, and carries
    italic colophon/window/caveat furniture. Feeding that straight to a
    sentence splitter (which breaks on `.!?`) glues the header, the whole TOC
    and the first paragraph into ONE "sentence" — unreadable, and it makes the
    diction counts depend on furniture.

    So: drop the structural furniture (separators, the italic epistemic lines —
    those are testimony ABOUT the text, never claims IN it), keep the TOC
    entries and bold headlines as units of their own (HSR §5.1(1) treats
    TOC+headline+lede as one surface-set, so the headline text is in scope),
    and sentence-split the remaining paragraphs.
    """
    units: List[str] = []
    for block in (text or "").split("\n"):
        s = block.strip()
        if not s or s.startswith("---") or s.startswith("# "):
            continue
        if s.startswith("*") and s.endswith("*") and not s.startswith("**"):
            continue                      # italic colophon / window / caveat
        if s == "In today's briefing:":
            continue
        if s.startswith("- ") or (s.startswith("**") and s.endswith("**")):
            units.append(s.strip("*- ").strip())    # TOC entry / headline
            continue
        units.extend(memory_core._sentences(s))
    return units


def artifact_hooks(prose: str) -> Dict:
    """register-target-spec §7's blinded-read scoring hooks, per artifact:
    "shape adherence vs. feel scored separately; per-artifact counts —
    synthesis-line sourcing rank, anchor-date presence, banned-lexicon hits
    (zero by construction)."

    Deterministic counts only.  No judgment, no LLM, no score."""
    # THE THIRD HOOK IS NOT MACHINE-COMPUTABLE, AND SAYS SO (QA F7).
    # register-spec §7 asks for three per-artifact counts: anchor-date
    # presence, banned-lexicon hits, and SYNTHESIS-LINE SOURCING RANK. The
    # first two are deterministic string facts and are computed below. The
    # third is not: it asks which sources a bolded synthesis line rests on and
    # how strong they are — a judgment about whether a claim's support is
    # first-hand reporting, single-outlet, or downstream echo. No regex knows
    # that. Rather than ship two of three and let the docstring imply three,
    # the gap is named in the output the same way hsr_worksheet refuses to
    # print a rate: an explicit "human adjudication" marker, so nobody reads a
    # missing hook as a zero.
    banned = generate._scan_banned(prose)     # ONE lexicon, never a second copy
    anchors = _ANCHOR_DATE_RE.findall(prose)
    # Diction is counted over the reader-facing UNITS, not the raw markdown:
    # counting the blob would score the colophon's furniture and let the two
    # forms' counts differ for a reason that is not the prose.
    units = prose_units(prose)
    return {
        "chars": len(prose),
        "units": len(units),
        "anchor_dates_present": len(anchors) > 0,
        "anchor_date_count": len(anchors),
        "banned_lexicon_hits": sorted(set(banned)),
        "continuity_diction_hits": sum(
            1 for u in units if _HSR_SWEEP_RE.search(u)),
        "synthesis_line_sourcing_rank": (
            "HUMAN ADJUDICATION — register-spec §7's third hook is not "
            "machine-computable (it ranks the STRENGTH of the sources a "
            "synthesis line rests on). Not scored here; score it by hand off "
            "the artifact and its cell manifest. This field exists so the "
            "hook is never silently missing."),
    }


def hsr_worksheet(con: sqlite3.Connection, date: str, stories: List[Dict],
                  slots: List[Dict], prose: str) -> str:
    """A §7-shaped ADJUDICATION WORKSHEET — deliberately NOT an HSR number.

    hsr-baseline §7 orders the after-measurement to "repeat §1, identically",
    and §5.2 records why no machine may close it: applied by the letter, the
    naive matcher scores the failure exhibit at 100%.  The hit test needs
    tense/temporal sanity and bearing-on-today's-delta — human adjudication.
    So this emits exactly what §1 steps 3-5 ask a human to mark, plus §7's two
    named traps, and refuses to print a rate.

    Wired to the real §7 machinery, not a re-implementation:
      * the continuity sweep is §1 step 5's regex, verbatim;
      * the poisoned-antecedent trap runs generate.repetition_antecedent_
        findings (Content rule iii, poisoned-antecedent hardened — a same-day
        backfill row does NOT license a repetition word);
      * gate FIX-5's numerator exclusion runs memory_core.is_baseline_sourced_
        sentence, whose only intended consumer is this off-line measurement.
    """
    lines: List[str] = [
        f"# HSR adjudication worksheet — {date}",
        "",
        "**THIS IS NOT AN HSR NUMBER.** hsr-baseline §5.2: applied by the "
        "letter, the naive matcher scores the failure exhibit at 100%. The hit "
        "test requires tense/temporal sanity and bearing-on-today's-delta — "
        "human adjudication, per §1 step 6. This sheet only marks the "
        "candidates §1 steps 3-5 tell a human to mark.",
        "",
        "**Compare against (§7):** as-shipped 0/1 (0%) · retrospective 0/4 "
        "(0%) · ceiling 4/4. Report as-shipped only.",
        "",
        "## 1 · Continuity-diction sweep (§1 step 5, regex verbatim)",
        f"`{HSR_SWEEP_PATTERN}`",
        "",
    ]
    swept = [s for s in prose_units(prose) if _HSR_SWEEP_RE.search(s)]
    if swept:
        for s in swept:
            excl = (" — **EXCLUDED from the numerator** (gate FIX-5: dated "
                    "baseline cite = researched founding context, not "
                    "surfaced reader history)"
                    if memory_core.is_baseline_sourced_sentence(s) else "")
            dated = " [carries a dated anchor]" if _ANCHOR_DATE_RE.search(s) else ""
            lines.append(f"- {s}{dated}{excl}")
    else:
        lines.append("- (no hits — the regex over-fires by design, so zero "
                     "hits is a real signal of no continuity diction)")
    lines += [
        "",
        "## 2 · Poisoned-antecedent trap (§5.1(2) / §7)",
        "§7: a post-plumbing \"reinstated (per our Jul 14 coverage)\" callback "
        "is a FALSE hit — the Jul 14 row is source-echo, not record-established "
        f"history. Rows written before {POISONED_ANTECEDENT_BOUND} are suspect "
        "as antecedents; the check below is strictly-predating and "
        "provenance-aware.",
        "",
    ]
    try:
        findings = generate.repetition_antecedent_findings(
            con, stories, slots, date)
    except sqlite3.OperationalError as exc:      # pre-migration record
        findings = []
        lines.append(f"- (antecedent check unavailable on this record: {exc})")
    if findings:
        for f in findings:
            lines.append(f"- FLAG: {f}")
    else:
        lines.append("- no unlicensed repetition words found.")
    lines += [
        "",
        "## 3 · Dated-anchor inventory (§1 step 3 candidates)",
        "Every date below must resolve to a ledger row / shipped edition / "
        "dated-anchor baseline cite (register-spec §7) — resolve by hand.",
        "",
    ]
    anchors = sorted(set(_ANCHOR_DATE_RE.findall(prose)))
    lines += [f"- {a}" for a in anchors] or ["- (none)"]
    lines += [
        "",
        "## 4 · Adjudicate (§1 step 4)",
        "For each marked sentence classify provenance: ledger-matchable · "
        "prior-coverage-carried · source-carried · memory-note-carried · "
        "unsupported. Then §1 step 6: a slot is a HIT only if a DATED "
        "pre-edition ledger fact ships CORRECTLY (right tense, bearing on "
        "today's delta).",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# T3 — the Concept B input pack ($0, no LLM, read-only)
# ---------------------------------------------------------------------------

def _loudness_by_slot(date: str, slots: List[Dict]) -> Dict[int, str]:
    """Concept B's three loudness levels, per slot number, for one edition.

    The edition's own depth-tier vector when the record has one; the positional
    grammar otherwise. `full` is the lead, `medium` is secondary, everything
    else is an In-Brief move — the same three-way split Design specified, keyed
    off what the edition did instead of off the slot's ordinal.

    GATE R-D RE-AIM (2026-08-14): `_tiers_for` and NOT
    `depth_tiers_from_record`, because the two answer different questions and
    this pack asks the first one. `_tiers_for` reads the RUN entry — "what did
    the last published edition look like" — while `depth_tiers_from_record`
    reads the STAGE entry, which exists in a window where nothing published
    (an interrupted regenerate, a stage whose run died). In that window the old
    call made this pack contradict the shipped page it feeds Design from, and
    the sentence above would have been an in-file claim the code refuses. The
    no-record fallback is unchanged in effect: `_tiers_for` returns the
    positional vector, which reaches the same three labels through the zip
    below (pinned)."""
    tiers = analysis._tiers_for(date, len(slots))
    if not tiers or not generate.depth_arm_is_in_brief():
        return {int(s["slot"]): ("lead" if int(s["slot"]) == 1
                                 else "secondary" if int(s["slot"]) <= 3
                                 else "in-brief")
                for s in slots}
    out: Dict[int, str] = {}
    for s, t in zip(slots, tiers):
        out[int(s["slot"])] = ("lead" if t == "full"
                               else "secondary" if t == "medium"
                               else "in-brief")
    return out


def concept_b_pack(con: sqlite3.Connection, date: str,
                   inputs: Dict) -> Tuple[str, Dict]:
    """The material Design hand-builds Concept B from — NOT the design artifact.

    Design's own cheapest test says the recomposition is "hand-built from the
    live ledger, no product code" (design.md, Concept B).  So the harness's job
    is extraction, at $0: for the real edition, which FILES moved, what each
    file's standing state is (with its AS OF stamp), the dated ledger rows
    behind it, and which stories mint day-one files ("New files opened" — the
    mandatory loud section, the serendipity valve).

    Loudness grammar per Concept B: the lead file, secondary files, and
    one-line In-Brief moves.

    NL-151b — WHICH STORY IS WHICH follows the EDITION, not the slot number.
    Design's grammar (three loudness levels) is unchanged and is not this
    harness's to amend; what changed is that "slot 1 = the lead" stopped being
    true on a morning where the lead's fetches died and the depth treatment
    walked down the ranking. The pack is INPUT MATERIAL for a hand-built
    concept, so calling a demoted story "the lead file" would feed the design
    exploration a fact the edition contradicts. Editions with no recorded
    vector (every edition before the contract, and every unanalysed date) fall
    back to the positional grammar, which is what they actually shipped.
    """
    slots = inputs["slots"]
    loudness = _loudness_by_slot(date, slots)
    files: List[Dict] = []
    new_files: List[Dict] = []
    for s in slots:
        n = int(s["slot"])
        loud = loudness[n]
        topics = [t for t in (s.get("matched_memory") or []) if t]
        story = {
            "slot": n,
            "loudness": loud,
            "working_title": s.get("story_title", ""),
            "what_happened": s.get("summary", ""),
            "corroboration": s.get("corroboration_label", ""),
            "outlets": s.get("outlets") or [],
        }
        if not topics:
            new_files.append(story)
            continue
        for topic in topics:
            tid = memory_core.resolve_thread_id(con, topic)
            entry = dict(story, topic=topic, thread_id=tid)
            if tid is None:
                entry["standing_state"] = None
                entry["as_of"] = None
                entry["ledger"] = []
            else:
                st = memory_core.latest_state(con, tid, before_date=date,
                                              strict=True)
                entry["standing_state"] = st["state_text"] if st else None
                entry["as_of"] = st["as_of_date"] if st else None
                entry["ledger"] = [
                    {"date": e["edition_date"],
                     "human": memory_core.human_date(e["edition_date"]),
                     "what_happened": e["what_happened"],
                     "significance": e.get("significance") or "",
                     "superseded_by": e.get("superseded_by")}
                    for e in memory_core.ledger_for_thread(
                        con, tid, before_date=date)]
            files.append(entry)

    payload = {"date": date, "moved_files": files, "new_files": new_files}

    weekday, human = generate._spoken_date(date)
    md: List[str] = [
        f"# Concept B input pack — the standing file, {weekday}, {human}",
        "",
        "*INPUT PACK, not the artifact.* Design hand-builds the thread-first "
        "recomposition from this; the harness only extracts, read-only, $0. "
        "Concept B's shape (design.md): thread name -> one-line standing state "
        "with AS OF stamp -> today's development as delta prose -> \"-> The "
        "file.\" Loudness: 1 lead file, 2 secondary, the rest one-line moves.",
        "",
        "## Files that moved",
        "",
    ]
    if not files:
        md.append("*(no slot on this edition matches a thread — every story "
                  "below opens a day-one file)*")
        md.append("")
    for f in files:
        md.append(f"### {f['topic']}  ·  {f['loudness']}")
        if f["standing_state"]:
            md.append(f"**Standing state (as of "
                      f"{memory_core.human_date(f['as_of'])}):** "
                      f"{f['standing_state']}")
        else:
            md.append("**Standing state:** none on record yet — a file with "
                      "history but no synthesised state.")
        md.append("")
        md.append(f"**Today's move (slot {f['slot']}):** "
                  f"{f['working_title']} — {f['what_happened']}")
        md.append("")
        if f["ledger"]:
            md.append("**The file so far (dated, strictly prior):**")
            for e in f["ledger"]:
                struck = " *(superseded)*" if e["superseded_by"] else ""
                sig = f" — {e['significance']}" if e["significance"] else ""
                md.append(f"- {e['human']}: {e['what_happened']}{sig}{struck}")
        else:
            md.append("**The file so far:** empty — day-one file.")
        md.append("")
    md += ["## New files opened", "",
           "*Mandatory and loud (Concept B): every un-threaded story mints a "
           "day-one thread. This is the serendipity valve — it must stay loud "
           "or the product becomes an echo chamber of the already-followed.*",
           ""]
    if new_files:
        for f in new_files:
            md.append(f"- **{f['working_title']}** ({f['loudness']}, slot "
                      f"{f['slot']}) — {f['what_happened']}")
    else:
        md.append("- (none — every story on this edition landed on an "
                  "existing file)")
    md += ["", "---", "", f"**The question (Greta's):** {T3_QUESTION}", ""]
    return "\n".join(md), payload


# ---------------------------------------------------------------------------
# The blind pack
# ---------------------------------------------------------------------------

_BLIND_LABELS = "ABCDEFGH"

# How many cells a COMPLETE read-set holds, per test — the shape plan_cells
# builds (t1: 1 context x 2 forms; t2: 2 contexts x 2 forms). Used only to
# DISCLOSE an incomplete set at pack time; the pack still seals what exists,
# because a failed arm's survivors are worth reading — the operator just must
# not be told a hole is a whole.
EXPECTED_SET_SIZE = {"t1": 2, "t2": 4}

# Which CONTRASTS a read-set must actually contain to be readable. Cardinality
# is not composition (QA R2): a hand-assembled T1 set of
# ctx-on__sectioned + ctx-off__sectioned counts 2-of-2 and would have sealed
# with a clean key, yet has no FORM contrast in it — the F1 partial-set class
# in a new coat. T1 varies form (context pinned ON); T2 varies both.
REQUIRED_CONTRAST = {"t1": ("form",), "t2": ("context", "form")}


def set_composition_issue(test: str, members: Sequence[Dict]) -> Optional[str]:
    """Why this read-set cannot be read as its test intends, or None.

    Checks size AND contrast, because a set can be the right size and still
    carry nothing to compare. Unreachable from _execute (which always emits
    the full grid) — this guards hand-edited and re-used session dirs, which
    is exactly how a pack gets assembled when an arm failed and someone
    stitched the survivors together."""
    want = EXPECTED_SET_SIZE.get(test)
    if want is None:                       # writer layout: arms vary, no grid
        return None
    if len(members) != want:
        return f"INCOMPLETE: {len(members)} of {want} cells"
    missing = [axis for axis in REQUIRED_CONTRAST.get(test, ())
               if len({m.get(axis) for m in members}) < 2]
    if missing:
        present = {axis: sorted({str(m.get(axis)) for m in members})
                   for axis in REQUIRED_CONTRAST.get(test, ())}
        return (f"WRONG COMPOSITION: {want} cells but no "
                f"{'/'.join(missing)} contrast — {present}")
    return None


# The window line's wall-clock stamp: `Generated 2026-07-24 05:07 UTC.`
_RENDER_STAMP_RE = re.compile(
    r"Generated \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC")
REDACTED_STAMP = "Generated [render time redacted for the blind read] UTC"


def redact_render_timestamp(text: str) -> str:
    """Normalise the render stamp out of an artifact bound for a blind read.

    THE LEAK (QA F2, proven on the SHIPPED 07-23 pack — ARM stamps 05:07 /
    05:14 / 05:22 UTC = production order, readable straight off the blinded
    files). generate.assemble_narrative stamps WINDOW_LINE with
    datetime.now() AT RENDER TIME. _execute renders in a fixed, code-known
    order (plan_cells yields contexts on-then-off) and each draft is a
    multi-minute live call — so the earliest stamps are context-ON and the
    latest are context-OFF. Sorting the blind files by their own visible
    timestamp recovers the ablation arm deterministically. The blind copy is
    the ONLY place this must not appear; the unblinded cell dir keeps the real
    stamp, and the sealed key records the mapping.

    The harness already knew this line was unstable — furniture_parity splits
    on "Generated " to neutralise it before comparing. This is that same
    neutralisation, finally reaching the copy that matters.

    Note the redaction is deliberately VISIBLE rather than silent: a reader
    who sees the marker knows a field was withheld for blinding, which is
    honest, and cannot mistake it for a real render time."""
    return _RENDER_STAMP_RE.sub(REDACTED_STAMP, text or "")


def blind_identifiers(cells: Optional[Sequence[Dict]] = None) -> List[str]:
    """Every token that would NAME the arm inside a blind artifact: the model
    ids (one price table, shared with the writer battery), the seat-override
    env var names, the lane words, and this pack's own cell-dir names.
    Longest first, so a redaction pass replaces `ctx-on__sectioned` whole
    rather than leaving `__sectioned` behind after eating `ctx-on`."""
    out = set(battery._ARM_PRICES)
    out |= {"NEWSLENS_MODEL_WRITER", "NEWSLENS_LANE_WRITER"}
    out |= {f"ctx-{CONTEXT_ON}", f"ctx-{CONTEXT_OFF}"}
    out |= {c["cell"] for c in (cells or []) if c.get("cell")}
    return sorted((t for t in out if t), key=len, reverse=True)


def redact_for_blind(text: str, cells: Optional[Sequence[Dict]] = None) -> str:
    """Everything a blind copy must not carry, removed BY CONSTRUCTION.

    Two channels, one pass: the render timestamp (QA F2) and the arm
    identifiers (QA F8 — QA planted a model name, a lane name and a cell-dir
    name inside a blind artifact and the filename-only check passed them).

    Redacting beats detecting here. A detector turns a leak into a crash at
    seal time, which is better than shipping it but still leaves the operator
    stuck; redaction means the blind copy simply cannot carry the identifier,
    and _assert_blind_clean stays behind it as the proof that the redaction
    ran. The marker is deliberately visible: a reader who sees it knows a
    field was withheld for blinding and cannot mistake it for content."""
    out = redact_render_timestamp(text)
    for token in blind_identifiers(cells):
        out = out.replace(token, "[redacted for the blind read]")
    return out


_BLIND_LABEL_RE = re.compile(r"[A-Z]+")


def is_blind_label(name: str) -> bool:
    """True iff `name` is a label `blind_label` can produce.

    Derived from the generator rather than from a precomputed prefix (QA R1):
    the validator used to build its label space from `range(512)` while the
    generator is unbounded, so at 512+ cells it would have rejected labels the
    generator had lawfully produced — a loud, practically unreachable, but
    internally inconsistent failure.

    `blind_label` is bijective base-26 over the non-empty uppercase strings
    (A..Z, then AA..ZZ, then AAA.., each length exhausted before the next), so
    its image is EXACTLY `[A-Z]+` — no bound, no drift. The round-trip is
    pinned in the suite over a range spanning the 26/702 rollovers."""
    return bool(name) and _BLIND_LABEL_RE.fullmatch(name) is not None


def blind_label(i: int) -> str:
    """Spreadsheet-style label for blind slot `i` (0-based): A..Z, AA..AZ, ...

    Unbounded ON PURPOSE (QA F4). The old `zip(_BLIND_LABELS, members)` capped
    the alphabet at 8 and SILENTLY truncated past it — a 12-cell read-set
    sealed 8 artifacts, dropped 4, and reported success. Silent data loss in
    the one artifact the whole battery exists to produce. A generator cannot
    truncate, so the failure mode is gone rather than merely detected."""
    if i < 0:
        raise ValueError("blind label index must be >= 0")
    out = ""
    n = i
    while True:
        out = chr(ord("A") + n % 26) + out
        n = n // 26 - 1
        if n < 0:
            return out


def build_blind_pack(run_dir: Path, seed: Optional[int] = None,
                     layout: str = "phase2") -> Dict:
    """Shuffle the produced cells into an unlabelled blind pack.

    Layout (mirrors the shipped 07-23 writer-battery pack):
        blind/SET-<n>/<A|B|...>.md      the artifacts, no labels, no manifests
        blind/QUESTIONS.md              the pre-registered questions
        blind/_KEY-do-not-open-until-scored.txt

    A READ-SET is one test on one edition — the unit the reader compares
    within (a T1 pair, a T2 2x2).  Cells are shuffled inside the set AND the
    set order is shuffled, both from `seed`, which the key records so any
    shuffle is reproducible and auditable.

    NO manifest.json, no cell-named directory, and no arm label ever enters
    blind/ — that is the whole point, and _assert_blind_clean proves it.

    `layout` selects the source tree: "phase2" (this battery's
    <test>/<date>/<cell>/) or "writer" (the writer battery's
    <date>/<model>__<lane>/, which has no packer of its own — see
    _discover_writer_cells)."""
    cells = (_discover_writer_cells(run_dir) if layout == "writer"
             else _discover_cells(run_dir))
    if not cells:
        raise MissingArtifact(
            f"no produced cells under {run_dir} — run `moat-battery t1 --run` "
            "and/or `t2 --run` first (the pack seals what exists, it never "
            "renders).")
    blind_dir = run_dir / "blind"
    # Sealing twice is a CONTAMINATION hazard, not a convenience: a second
    # shuffle over fewer cells leaves the first pack's stale SET-n directories
    # in place, and the reader cannot tell a stale set from a live one. Refuse
    # and make the operator move the old pack — never silently half-overwrite.
    if blind_dir.exists() and any(blind_dir.iterdir()):
        raise MissingArtifact(
            f"a blind pack already exists at {blind_dir} — sealing again would "
            "leave the previous shuffle's SET dirs standing beside the new "
            "ones and the reader could not tell them apart. Move or delete it "
            "first (the key file records the old seed if you need it back).")
    if seed is None:
        seed = random.randrange(1, 2 ** 31)
    rng = random.Random(seed)

    sets: Dict[Tuple[str, str], List[Dict]] = {}
    for c in cells:
        sets.setdefault((c["test"], c["date"]), []).append(c)
    ordered_keys = sorted(sets)
    rng.shuffle(ordered_keys)

    blind_dir.mkdir(parents=True, exist_ok=True)
    key_lines = [
        "SEALED KEY — do not open until every artifact is read and ranked.",
        f"session: {run_dir}",
        f"shuffle seed: {seed}  (reproducible: --seed {seed})",
        "",
        "BLINDING LIMITS ON RECORD (Stig, data-2 §3): the FORM axis is not "
        "blindable — prose and sections are distinguishable on sight — so T1's "
        "blinding is ORDER-ONLY. Randomised unlabelled order, fixed "
        "pre-registered questions, Medium ceiling (Hux's cap is program law). "
        "Never claim more.",
        "",
        "THE CONTEXT AXIS: blind as to WHICH arm, with two known channels "
        "closed and one open. CLOSED: (1) the render timestamp — "
        "assemble_narrative stamps wall-clock time at render, cells render in "
        "code-known context order, and each draft is a multi-minute call, so "
        "sorting by stamp recovered the arm; blind copies are redacted "
        "(redact_render_timestamp) and _assert_blind_clean re-checks the "
        "sealed files. (2) model/lane/cell-dir names, scanned out of the "
        "blind text. This key does NOT claim the axis is unconditionally "
        "'genuinely blind' — that claim was false while channel (1) was open, "
        "and an earlier version of this file made it.",
        "",
        "RENDER-MODE LEAK (disclose it, do not launder it): in --form-mode "
        "render the two FORMS of one context arm are the same draft rendered "
        "twice, so a reader who notices two artifacts carrying identical "
        "sentences has identified which pair shares a context arm — without "
        "learning WHICH arm it is. It weakens set-level blinding; it does not "
        "reveal the ablation. --form-mode compose does not have this property.",
        "",
    ]
    mapping: List[Dict] = []
    incomplete: List[str] = []
    for i, k in enumerate(ordered_keys, start=1):
        test, date = k
        members = sorted(sets[k], key=lambda c: c["cell"])
        rng.shuffle(members)
        set_dir = blind_dir / f"SET-{i}"
        set_dir.mkdir(parents=True, exist_ok=True)
        issue = set_composition_issue(test, members)
        flag = ""
        if issue:
            flag = f"   *** {issue} ***"
            incomplete.append(f"SET-{i} ({test.upper()} {date}): {issue}")
        key_lines.append(f"SET-{i}  ({test.upper()}, edition {date}){flag}")
        for j, c in enumerate(members):
            label = blind_label(j)
            # THE BLIND COPY IS REDACTED (QA F2 + F8): render stamp and arm
            # identifiers both. The original prose keeps everything in its
            # cell dir; only this copy is normalised.
            (set_dir / f"{label}.md").write_text(
                redact_for_blind(c["prose"], cells), encoding="utf-8")
            key_lines.append(
                f"  {label}.md  ->  arm={c['cell']}" if test == "writer"
                else (f"  {label}.md  ->  context={c['context']}  "
                      f"form={c['form']}   [{c['cell']}]"))
            mapping.append({"set": f"SET-{i}", "label": label, "test": test,
                            "date": date, "context": c["context"],
                            "form": c["form"], "cell": c["cell"]})
        key_lines.append("")

    (blind_dir / "QUESTIONS.md").write_text(
        _questions_md(ordered_keys), encoding="utf-8")
    (blind_dir / "_KEY-do-not-open-until-scored.txt").write_text(
        "\n".join(key_lines), encoding="utf-8")
    _assert_blind_clean(blind_dir, cells)
    return {"seed": seed, "sets": len(ordered_keys), "cells": len(mapping),
            "mapping": mapping, "blind_dir": str(blind_dir),
            "incomplete": incomplete}


def _questions_md(keys: Sequence[Tuple[str, str]]) -> str:
    tests = {t for t, _ in keys}
    out = ["# Pre-registered questions — read these BEFORE the artifacts", "",
           "Read every artifact in a set, answer the questions, rank the set, "
           "and only then open the key. The questions are fixed and were "
           "registered before the artifacts existed.", ""]
    if "t1" in tests or "t2" in tests:
        qs = T1_QUESTIONS if "t1" in tests else T2_QUESTIONS
        out.append("## Per artifact")
        out += [f"{i}. {q}" for i, q in enumerate(qs, start=1)]
        out += ["", "## Per set", "Rank the artifacts. Say plainly if you "
                "cannot tell them apart — indifference is a result, and one "
                "of the pre-registered decision rules turns on it.", ""]
    if "t3" in tests:
        out += ["## Concept B", T3_QUESTION, ""]
    return "\n".join(out)


def _discover_cells(run_dir: Path) -> List[Dict]:
    """Every produced cell under <run_dir>/<test>/<date>/<cell>/narrative.md.
    Cell dir name is `ctx-<on|off>__<form>` — parsed back, never re-derived
    from a manifest (the manifest must not be needed to build the pack)."""
    out: List[Dict] = []
    for test in ("t1", "t2"):
        tdir = run_dir / test
        if not tdir.is_dir():
            continue
        for ddir in sorted(p for p in tdir.iterdir() if p.is_dir()):
            for cdir in sorted(p for p in ddir.iterdir() if p.is_dir()):
                art = cdir / "narrative.md"
                if not art.is_file():
                    continue
                name = cdir.name
                if "__" not in name or not name.startswith("ctx-"):
                    continue
                ctx, form = name.split("__", 1)
                out.append({"test": test, "date": ddir.name, "cell": name,
                            "context": ctx[len("ctx-"):], "form": form,
                            "prose": art.read_text(encoding="utf-8")})
    return out


def _discover_writer_cells(run_dir: Path) -> List[Dict]:
    """Cells of a WRITER-BATTERY session: <run_dir>/<model>__<lane>/narrative.md.

    Why this lives here (flagged to the gate, QA F2's second half): the writer
    battery has NO packer in code at all — src/newslens/battery.py produces
    per-arm artifacts and stops, and the shipped 2026-07-23 blind pack was
    assembled BY HAND. That is precisely why it leaked production order
    (05:07 / 05:14 / 05:22 UTC readable straight off the blinded files): there
    was no code to carry a redaction. So rather than patch a copy that does
    not exist — or bolt a packer onto battery.py's money paths mid-fix-loop —
    the leak-free packer is offered here, and the writer battery's next pack
    (the Fable replication arms) can use it via `moat-battery pack --layout
    writer --out <root> --session <date>`.

    battery.py is NOT modified by this."""
    out: List[Dict] = []
    if not run_dir.is_dir():
        return out
    for cdir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if cdir.name == "blind" or "__" not in cdir.name:
            continue
        art = cdir / "narrative.md"
        if not art.is_file():
            continue
        model, lane = cdir.name.rsplit("__", 1)
        out.append({"test": "writer", "date": run_dir.name, "cell": cdir.name,
                    "context": model, "form": lane,
                    "prose": art.read_text(encoding="utf-8")})
    return out


def _assert_blind_clean(blind_dir: Path, cells: Optional[List[Dict]] = None
                        ) -> None:
    """The blind dir holds artifacts, the questions and the sealed key — and
    the artifacts' CONTENT names nothing the read is blind to.

    Two layers, because QA F8 showed shape-checking alone is theatre: the
    planted model name, lane name and cell-dir name all sat inside a blind
    artifact and passed a filename-only check.

      LAYER 1 (shape) — no manifests, no stray top-level files, no file whose
        stem is not a blind label.
      LAYER 2 (content) — scan the sealed text for the identifiers that would
        name the arm: every model in the price table, the lane names, every
        cell-dir name in this pack, and the render stamp F2 redacts. The key
        file itself is exempt: naming the arms is its entire job.

    This is an assertion, not a lint — a contaminated pack must never reach a
    reader, and the cost of being wrong is the whole battery's validity."""
    offenders = []
    for p in blind_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(blind_dir)
        if p.name == "manifest.json" or p.name.endswith(".json"):
            offenders.append(f"{rel} (manifest/JSON in the blind dir)")
        elif p.parent == blind_dir and p.name not in (
                "QUESTIONS.md", "_KEY-do-not-open-until-scored.txt"):
            offenders.append(f"{rel} (stray file at the blind root)")
        elif p.parent != blind_dir and not is_blind_label(p.stem):
            offenders.append(f"{rel} (not a blind label)")

    # Layer 2 — content. Only the artifacts; the key is allowed to say it all.
    needles = {m for m in battery._ARM_PRICES}
    needles |= {"NEWSLENS_MODEL_WRITER", "NEWSLENS_LANE_WRITER"}
    needles |= {c["cell"] for c in (cells or [])}
    needles |= {"ctx-on", "ctx-off"}
    for p in sorted(blind_dir.rglob("*.md")):
        if p.parent == blind_dir:
            continue                      # QUESTIONS.md is authored, not sealed
        text = p.read_text(encoding="utf-8")
        rel = p.relative_to(blind_dir)
        for n in sorted(needles):
            if n and n in text:
                offenders.append(f"{rel} (content names {n!r})")
        if _RENDER_STAMP_RE.search(text):
            offenders.append(f"{rel} (carries an un-redacted render timestamp "
                             "— recovers production order, QA F2)")

    # WITHIN A SET, THE WINDOW LINE MUST BE IDENTICAL (gate F-B backstop).
    # Every cell of a read-set renders the same edition, so its fetch-window
    # line is a property of the record and cannot legitimately differ. If two
    # cells disagree, something per-render leaked into it — which is exactly
    # how the `ran_at` fallback used to smuggle a wall clock past the
    # timestamp regex. Catch the CLASS, not just the one format.
    for set_dir in sorted(p for p in blind_dir.iterdir() if p.is_dir()):
        seen: Dict[str, List[str]] = {}
        for p in sorted(set_dir.glob("*.md")):
            for line in p.read_text(encoding="utf-8").splitlines():
                if "Covers items fetched" in line:
                    seen.setdefault(line.strip(), []).append(p.name)
                    break
        if len(seen) > 1:
            offenders.append(
                f"{set_dir.name} (cells disagree on the window line — a "
                f"per-render value leaked into it: {sorted(seen)})")
    if offenders:
        raise AssertionError(
            "blind pack contaminated — these would un-blind the read: "
            + ", ".join(sorted(offenders)))


# ---------------------------------------------------------------------------
# Planning + execution
# ---------------------------------------------------------------------------

class Cell:
    """One artifact to produce: (test, edition date, context, form)."""

    def __init__(self, test: str, date: str, context: str, form: str,
                 draft_key: Tuple[str, str, str]):
        self.test, self.date = test, date
        self.context, self.form = context, form
        self.draft_key = draft_key      # cells sharing a key share ONE call

    @property
    def dirname(self) -> str:
        return f"ctx-{self.context}__{self.form}"

    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return f"<Cell {self.test} {self.date} {self.dirname}>"


def plan_cells(test: str, dates: Sequence[str],
               form_mode: str = FORM_MODE_RENDER) -> List[Cell]:
    """The cells one test wants over `dates`.

    T1 holds context at ON (the live product's state) and varies FORM.
    T2 is the full 2x2: context {on, off} x form {prose_first, sectioned}.
    In `render` mode both forms share a draft, so the draft key omits form."""
    cells: List[Cell] = []
    contexts = (CONTEXT_ON,) if test == "t1" else (CONTEXT_ON, CONTEXT_OFF)
    for date in dates:
        for ctx in contexts:
            for form in FORMS:
                key = ((test, date, ctx) if form_mode == FORM_MODE_RENDER
                       else (test, date, ctx + "/" + form))
                cells.append(Cell(test, date, ctx, form, key))
    return cells


def discover_dates(con: sqlite3.Connection, limit: int) -> List[str]:
    """The most recent edition dates that actually have prose on the record.
    Edition 1 was rank-only (`narrative_text` NULL, HSR §3) — a retro-pair
    needs a shipped narrative, so those are excluded here, not at spend time."""
    rows = con.execute(
        "SELECT date FROM briefings WHERE narrative_text IS NOT NULL"
        " ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    return sorted(r["date"] for r in rows)


def _writer_model_and_lane(lane: str) -> Tuple[str, llm.SeatConfig]:
    cfg = llm.resolve_seat("writer", {"NEWSLENS_LANE_WRITER": lane})
    return cfg.model, cfg


def sink_charged(sink: Sequence[Dict]) -> float:
    """Dollars actually BILLED across every attempt in a cost sink.

    call_llm appends a sink entry the moment an attempt returns usage — before
    validation and before the truncation check — so a malformed or truncated
    attempt that still billed is in here. Summing the sink is therefore the
    only honest basis for the spend receipt; reading the final attempt's usage
    (what this harness used to do) silently drops attempt 1 of every corrected
    retry, and drops BOTH attempts when a draft fails outright."""
    return round(sum(float(e.get("usd_charged") or 0.0) for e in sink), 6)


def _run_draft(key: str, prompt: str, inputs: Dict, lane: str,
               sink: Optional[List[Dict]] = None) -> Tuple[Dict, Dict]:
    """One live writer call for one draft.  Only the LANE is overridden — the
    model is the writer seat's own, held fixed across every cell so the 2x2's
    declared variables are the only variables.

    `sink` is owned by the CALLER (gate F-A). It has to outlive this function:
    when call_llm exhausts its retry and raises, the attempts it already paid
    for are recorded in the sink, and a sink created here would be lost with
    the frame — the receipt would then report $0 for a cell that billed twice
    (~$0.90 worst case on Opus at the 16k ceiling)."""
    if sink is None:
        sink = []
    prev_lane = os.environ.get("NEWSLENS_LANE_WRITER")
    os.environ["NEWSLENS_LANE_WRITER"] = lane
    t0 = datetime.now(timezone.utc)
    try:
        content, usage = generate.call_llm(
            key, prompt, "narrative", generate.NARRATIVE_MAX_TOKENS,
            generate.NARRATIVE_TEMPERATURE, True,
            validate=battery._shape_check(inputs), cost_sink=sink)
    finally:
        if prev_lane is None:
            os.environ.pop("NEWSLENS_LANE_WRITER", None)
        else:
            os.environ["NEWSLENS_LANE_WRITER"] = prev_lane
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    draft = json.loads(content)
    model, cfg = _writer_model_and_lane(lane)
    # Deliberate reuse of the writer battery's price table and draft validator
    # (battery._arm_prices / _shape_check / _arm_estimate): ONE price map and
    # ONE draft contract across both batteries. A second copy here is exactly
    # how BUG-1 shipped in two places — the divergence, not the sharing, is the
    # hazard.
    pin, pout = battery._arm_prices(model)
    shadow = llm.cost_fields(cfg, usage)
    meta = {
        "model": model,
        "lane": lane,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "usd_real_at_model_price": round(
            (usage.get("prompt_tokens", 0) / 1e6) * pin
            + (usage.get("completion_tokens", 0) / 1e6) * pout, 6),
        # FINAL attempt only — kept for continuity with the writer battery's
        # manifest shape, and deliberately sitting next to the all-attempts
        # figure so the two can never be confused again (gate F-A).
        "usd_charged_seam": shadow["usd_charged"],
        "usd_shadow_seam": shadow["usd_shadow"],
        # EVERY billed attempt, including a corrected retry's discarded first
        # draft. This is the number the receipt and the DECISIONS spend line
        # must use.
        "usd_charged_all_attempts": sink_charged(sink),
        "elapsed_s": round(elapsed, 1),
        "attempts": len(sink),
    }
    return draft, meta


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser, *, paid: bool = True) -> None:
    p.add_argument("--session", default=None, metavar="YYYY-MM-DD",
                   help="battery session date — the artifact root "
                        "<DATA_DIR>/battery/<session>/phase2 (default: today)")
    p.add_argument("--out", default=None, metavar="DIR",
                   help="battery output root (default: <DATA_DIR>/battery)")
    p.add_argument("--dates", default=None,
                   help="comma-separated edition dates (default: discovered "
                        "from the record, most recent editions with prose)")
    if paid:
        p.add_argument("--lane", default="api", choices=["api", "subscription"],
                       help="writer lane for every cell (default: api — the "
                            "lane the program's dollar figures assume; "
                            "subscription is $0 CHARGED with the shadow "
                            "disclosed)")
        p.add_argument("--form-mode", default=FORM_MODE_RENDER,
                       choices=[FORM_MODE_RENDER, FORM_MODE_COMPOSE],
                       help="how the form axis varies (default: render — one "
                            "draft, two renders, confound-free; compose needs "
                            f"prompts/{PROSE_FIRST_PROMPT}, which does not "
                            "exist yet)")
        p.add_argument("--run", action="store_true",
                       help="make the LIVE writer calls (default: dry-run — "
                            "plan + estimates only, ZERO calls, ZERO writes). "
                            "An arm can take one corrected retry, so "
                            "worst-case spend is ~2x the printed estimate.")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="moat-battery",
        description="NL-75 Phase-2 blinded artifact battery (T1 retro-pairs, "
                    "T2 expression ablation 2x2, T3 Concept B pack). Dry-run "
                    "by default; --run makes LIVE calls.")
    sub = p.add_subparsers(dest="cmd", required=True)

    t1 = sub.add_parser("t1", help="prose-first vs sectioned retro-pairs")
    _add_common(t1)
    t1.add_argument("--pairs", type=int, default=5,
                    help="how many editions to pair (default 5, the "
                         "registered N)")

    t2 = sub.add_parser("t2", help="expression ablation 2x2 (context x form)")
    _add_common(t2)
    t2.add_argument("--editions", type=int, default=3,
                    help="how many editions (default 3, the registered N)")
    t2.add_argument("--ablate", default=",".join(DEFAULT_ABLATE),
                    help="which context fields the OFF cell drops (default: "
                         f"{','.join(DEFAULT_ABLATE)}; also available: "
                         "baseline)")

    t3 = sub.add_parser("t3", help="Concept B input pack ($0, no LLM)")
    _add_common(t3, paid=False)
    t3.add_argument("--mornings", type=int, default=2,
                    help="how many editions to extract (default 2 — the "
                         "registered N: \"2 mornings ($0 LLM, design hrs)\", "
                         "data-2 §4 T3; Design's own test says the real "
                         "edition \"and one more morning\")")
    t3.add_argument("--run", action="store_true",
                    help="write the pack (default: dry-run. $0 either way — "
                         "this subcommand never makes an LLM call)")

    pk = sub.add_parser("pack", help="seal the produced cells into a blind pack")
    pk.add_argument("--session", default=None, metavar="YYYY-MM-DD")
    pk.add_argument("--out", default=None, metavar="DIR")
    pk.add_argument("--seed", type=int, default=None,
                    help="shuffle seed (default: random, recorded in the key)")
    pk.add_argument("--layout", default="phase2", choices=["phase2", "writer"],
                    help="which artifact tree to seal (default: phase2). "
                         "`writer` seals a scripts/battery session "
                         "(<date>/<model>__<lane>/) — that battery has no "
                         "packer of its own, which is why its shipped 07-23 "
                         "pack leaked render order; this path redacts.")
    pk.add_argument("--run", action="store_true",
                    help="write the pack (default: dry-run listing). $0.")

    pl = sub.add_parser("plan", help="whole-session dry run: T1+T2+T3 cost")
    _add_common(pl, paid=False)
    pl.add_argument("--lane", default="api", choices=["api", "subscription"])
    pl.add_argument("--form-mode", default=FORM_MODE_RENDER,
                    choices=[FORM_MODE_RENDER, FORM_MODE_COMPOSE])
    return p


def _session_root(args) -> Tuple[str, Path]:
    session = getattr(args, "session", None) or ranking.local_today()
    out_root = Path(args.out) if getattr(args, "out", None) else (
        paths.DATA_DIR / "battery")
    return session, out_root / session / "phase2"


def _resolve_dates(con, args, want: int) -> List[str]:
    if getattr(args, "dates", None):
        return [d.strip() for d in args.dates.split(",") if d.strip()]
    return discover_dates(con, want)


def _plan_test(con, test: str, dates: Sequence[str], form_mode: str,
               ablate: Sequence[str], lane: str, cap: float,
               cumulative: float = 0.0
               ) -> Tuple[List[Dict], float, List[str]]:
    """Price and status every cell of one test.  Returns (rows, cumulative,
    notes).  Nothing here spends or writes."""
    rows: List[Dict] = []
    notes: List[str] = []
    model, _ = _writer_model_and_lane(lane)
    priced_drafts: Dict[Tuple[str, str, str], float] = {}
    for date in dates:
        try:
            inputs = load_inputs(con, date)
        except (generate.GenerateError, sqlite3.OperationalError) as exc:
            # sqlite3.OperationalError too (QA F3, site 1): "database is
            # locked" is the exact signature of a concurrent `newslens
            # generate` holding a write txn. Planning must REFUSE honestly at
            # $0, not escape as a traceback from a dry run.
            rows.append({"date": date, "status": "BLOCKED", "usd": 0.0,
                         "reason": f"{type(exc).__name__}: {exc}"})
            continue
        have, total = ledger_coverage(inputs)
        if test == "t2" and ablation_is_degenerate(inputs, ablate):
            rows.append({
                "date": date, "status": "BLOCKED", "usd": 0.0,
                "reason": (f"DEGENERATE 2x2 — {have}/{total} slots carry "
                           "ledger context, so context-ON and context-OFF "
                           "build the identical prompt. The ablation would "
                           "compare a thing against itself. Needs a "
                           "hand-traced retro-mock ledger (data-2 §2)")})
            continue
        for cell in plan_cells(test, [date], form_mode):
            try:
                prompt = build_cell_prompt(inputs, cell.context, cell.form,
                                           form_mode, ablate)
            except MissingArtifact as exc:
                rows.append({"date": date, "cell": cell.dirname,
                             "status": "BLOCKED", "reason": str(exc),
                             "usd": 0.0})
                continue
            if cell.draft_key in priced_drafts:
                rows.append({"date": date, "cell": cell.dirname,
                             "status": "PLANNED", "usd": 0.0,
                             "reason": "shares the draft above (render mode)"})
                continue
            est = battery._arm_estimate(prompt, model)
            charged = 0.0 if lane == "subscription" else est
            if cumulative + charged > cap:
                rows.append({"date": date, "cell": cell.dirname,
                             "status": "SKIP", "usd": charged,
                             "reason": (f"cumulative charged "
                                        f"${cumulative + charged:.4f} would "
                                        f"exceed the ${cap:.2f} cap")})
                continue
            cumulative += charged
            priced_drafts[cell.draft_key] = est
            rows.append({"date": date, "cell": cell.dirname,
                         "status": "PLANNED", "usd": charged,
                         "shadow": est,
                         "reason": ("$0 CHARGED (subscription); shadow "
                                    f"${est:.4f}") if lane == "subscription"
                         else ""})
        notes.append(f"{date}: ledger context on {have}/{total} slots")
    return rows, cumulative, notes


def _print_rows(rows: List[Dict]) -> None:
    # NOTE: no `out=sys.stdout` default — a default argument binds THIS
    # module's import-time sys.stdout, which escapes any later redirection
    # (pytest's capture, a caller's contextlib.redirect_stdout). Resolve the
    # stream at call time by just using print().
    for r in rows:
        cell = f" {r['cell']}" if r.get("cell") else ""
        usd = f" est ${r['usd']:.4f}" if r.get("usd") else ""
        reason = f" — {r['reason']}" if r.get("reason") else ""
        print(f"    [{r['status']:<7}] {r['date']}{cell}{usd}{reason}")


def _cmd_paid(args, test: str) -> int:
    lane = args.lane
    form_mode = args.form_mode
    ablate = tuple(a.strip() for a in getattr(args, "ablate",
                                              ",".join(DEFAULT_ABLATE)).split(",")
                   if a.strip())
    unknown = [a for a in ablate if a not in ABLATION_FIELDS]
    if unknown:
        print(f"moat-battery: unknown --ablate field(s) {', '.join(unknown)} — "
              f"known: {', '.join(sorted(ABLATION_FIELDS))}", file=sys.stderr)
        return 2

    cap = config.budget_cap_usd_per_run(os.environ)
    session, run_dir = _session_root(args)
    want = args.pairs if test == "t1" else args.editions

    try:
        con = db.connect_readonly()
    except sqlite3.OperationalError as exc:
        print(f"moat-battery: refused — cannot open the record read-only "
              f"({exc}); there is no edition to build artifacts from",
              file=sys.stderr)
        return 1
    try:
        dates = _resolve_dates(con, args, want)
        if not dates:
            print("moat-battery: refused — the record holds no edition with "
                  "prose (a retro-pair needs a shipped narrative)",
                  file=sys.stderr)
            return 1
        model, cfg = _writer_model_and_lane(lane)
        title = ("T1 — prose-first vs sectioned retro-pairs"
                 if test == "t1" else
                 "T2 — expression ablation 2x2 (context x form)")
        print(f"NewsLens moat battery, Phase 2 · {title}")
        print(f"  session {session}; editions: {', '.join(dates)}")
        print(f"  writer seat held FIXED: {model} [{lane}] "
              f"(thinking={cfg.thinking}, effort={cfg.effort}); "
              f"form-mode={form_mode}")
        if test == "t2":
            print(f"  ablation drops: {', '.join(ablate)}")
        print(f"  budget cap ${cap:.2f}/run — the cap binds CHARGED dollars "
              "and is PER INVOCATION, not per session")

        rows, cumulative, notes = _plan_test(
            con, test, dates, form_mode, ablate, lane, cap)
        for n in notes:
            print(f"  · {n}")
        _print_rows(rows)
        planned = [r for r in rows if r["status"] == "PLANNED"]
        blocked = [r for r in rows if r["status"] == "BLOCKED"]
        skipped = [r for r in rows if r["status"] == "SKIP"]
        print(f"  {len(planned)} cell(s) planned, {len(blocked)} blocked, "
              f"{len(skipped)} skipped by the cap; est total charged "
              f"${cumulative:.4f}")

        if not args.run:
            print("  DRY RUN — no calls made, no files written. Re-run with "
                  "--run to execute.")
            return 0
        # THE PARTIAL-SET REFUSAL (QA F1). "A partial 2x2/pair set is not the
        # pre-registered test" is the rule — and it binds on the HOLE, not on
        # whatever punched it. A cap SKIP reaches the identical bad state as a
        # BLOCK by a different door: --run would spend real money and produce
        # ctx-on with no ctx-off counterpart, i.e. a read-set with no ablation
        # contrast, which under render mode is two near-identical renders of
        # the SAME arm. Refuse both, name the cause, say the way out.
        if blocked or skipped:
            why = []
            if blocked:
                why.append(f"{len(blocked)} BLOCKED")
            if skipped:
                why.append(f"{len(skipped)} SKIPPED by the ${cap:.2f} cap")
            # NB: never .capitalize() a sentence carrying an env var name —
            # it lowercases BUDGET_CAP_USD_PER_RUN into something the operator
            # cannot copy-paste. Build the fix clause already-cased.
            fixes = []
            if blocked:
                fixes.append("fix the blockers")
            if skipped:
                fixes.append("raise BUDGET_CAP_USD_PER_RUN")
            print(f"moat-battery: refused — {' and '.join(why)} above, so this "
                  f"would ship an INCOMPLETE read-set. A partial 2x2/pair set "
                  f"is not the pre-registered test (a set without its "
                  f"counterpart arm has no contrast to read). To proceed: "
                  f"{', '.join(fixes)}, or narrow --dates. Nothing was spent.",
                  file=sys.stderr)
            return 1
        return _execute(con, test, dates, form_mode, ablate, lane, cap,
                        run_dir)
    finally:
        con.close()


def _execute(con, test: str, dates: Sequence[str], form_mode: str,
             ablate: Sequence[str], lane: str, cap: float,
             run_dir: Path) -> int:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if lane == "api" and not key:
        print("moat-battery: --run on the api lane needs ANTHROPIC_API_KEY — "
              "set it in .env, then re-run", file=sys.stderr)
        return 1
    if lane == "subscription":
        try:
            llm.check_lane(llm.resolve_seat(
                "writer", {"NEWSLENS_LANE_WRITER": "subscription"}))
        except llm.LaneUnavailable as exc:
            print(f"moat-battery: --run on the subscription lane but the "
                  f"claude CLI is unavailable — {exc}", file=sys.stderr)
            return 1

    print(f"  writing artifacts under {run_dir}/")
    ok = 0
    charged_total = 0.0     # what actually billed
    planned_est = 0.0       # the estimate ledger the cap gate binds on
    moved = 0               # dates that changed under us mid-run
    # The SAME resolved model _plan_test priced. Reading llm.SEATS directly
    # here would diverge from the plan whenever NEWSLENS_MODEL_WRITER is set
    # (the plan would price the override, execute the default).
    priced_model, _ = _writer_model_and_lane(lane)
    # THE SPEND REPORT MUST SURVIVE EVERY EXIT PATH (QA F3). Money has already
    # moved by the time anything in this loop can fail, so the "charged $X"
    # line is not a success message — it is the receipt. try/finally, always.
    try:
        for date in dates:
            # The per-date load is INSIDE the disclose-and-continue net for the
            # same reason (QA F3, site 2): the plan read this date cleanly, then a
            # concurrent `newslens generate` committed between the two reads.
            # sqlite3.OperationalError ("database is locked") is that race's exact
            # signature. Previously it escaped from OUTSIDE the per-cell try,
            # after earlier dates had already been charged, and the principal got
            # a stack trace instead of a receipt.
            try:
                inputs = load_inputs(con, date)
            except (generate.GenerateError, sqlite3.OperationalError) as exc:
                moved += 1
                print(f"    [!      ] {date} SKIPPED — the record moved or became "
                      f"unreadable under the run ({type(exc).__name__}: {exc}). "
                      "Disclosed; other dates continue.", file=sys.stderr)
                continue
            drafts: Dict[Tuple[str, str, str], Tuple[Dict, Dict]] = {}
            # A draft that FAILED is remembered as such (found writing gate
            # F-A's pin 2). In render mode two cells share one draft_key, so
            # without this the sibling cell re-runs the same doomed prompt and
            # call_llm bills its two attempts all over again — 4 billed
            # attempts where the plan priced ONE draft. Failure is cached
            # exactly like success: one draft per draft_key, as disclosed.
            failed_drafts: set = set()
            for cell in plan_cells(test, [date], form_mode):
                if cell.draft_key in failed_drafts:
                    print(f"    [SKIP   ] {date} {cell.dirname} — its draft "
                          "failed above; not re-billed")
                    continue
                # THE SINK IS OWNED HERE (gate F-A), not inside _run_draft: it
                # must survive the call raising, because the attempts call_llm
                # already paid for are recorded in it. A sink that dies with
                # the callee's frame is a receipt that reports $0 for a cell
                # that billed twice.
                sink: List[Dict] = []
                try:
                    if cell.draft_key not in drafts:
                        prompt = build_cell_prompt(inputs, cell.context, cell.form,
                                                   form_mode, ablate)
                        # THE SAME ARITHMETIC THE PLAN PRINTED (the writer
                        # battery's rule): gate on the PRE-CALL estimate, never on
                        # spend-so-far. Gating on actual spend would let the run
                        # produce MORE cells than the dry run disclosed — the
                        # principal approved a printed plan, so execute must not
                        # exceed it. `planned_est` is the running ESTIMATE ledger;
                        # `charged_total` is what actually billed, reported at the
                        # end alongside it.
                        est = battery._arm_estimate(prompt, priced_model)
                        charged_est = 0.0 if lane == "subscription" else est
                        if planned_est + charged_est > cap:
                            print(f"    [SKIP   ] {date} {cell.dirname} — est "
                                  f"${charged_est:.4f} would put the run at "
                                  f"${planned_est + charged_est:.4f}, over the "
                                  f"${cap:.2f} cap")
                            continue
                        planned_est += charged_est
                        try:
                            drafts[cell.draft_key] = _run_draft(
                                key, prompt, inputs, lane, sink)
                        except Exception:
                            failed_drafts.add(cell.draft_key)
                            raise
                        finally:
                            # Bill from the SINK, in a finally: every attempt
                            # that reached the API is counted whether the
                            # draft ultimately succeeded, failed validation
                            # and was retried, or failed outright.
                            charged_total += sink_charged(sink)
                    draft, meta = drafts[cell.draft_key]
                    # RENDER always from the UN-ablated inputs: the furniture is
                    # identical across the ablation axis by construction, so the
                    # only difference the reader sees is the prose itself.
                    prose = RENDERS[cell.form](date, draft["stories"], inputs)
                    cell_dir = run_dir / test / date / cell.dirname
                    cell_dir.mkdir(parents=True, exist_ok=True)
                    (cell_dir / "narrative.json").write_text(
                        json.dumps(draft, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    (cell_dir / "narrative.md").write_text(prose, encoding="utf-8")
                    (cell_dir / "hsr-worksheet.md").write_text(
                        hsr_worksheet(con, date, draft["stories"], inputs["slots"],
                                      prose), encoding="utf-8")
                    manifest = dict(meta, test=test, date=date,
                                    context=cell.context, form=cell.form,
                                    form_mode=form_mode,
                                    ablated=list(ablate) if cell.context ==
                                    CONTEXT_OFF else [],
                                    hooks=artifact_hooks(prose))
                    (cell_dir / "manifest.json").write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    retry_note = ("" if meta.get("attempts", 1) <= 1 else
                                  f", {meta['attempts']} attempts billing "
                                  f"${meta['usd_charged_all_attempts']:.4f} "
                                  "in total")
                    print(f"    [+      ] {date} {cell.dirname} — "
                          f"{meta['completion_tokens']} out tok, real "
                          f"${meta['usd_real_at_model_price']:.4f} (charged "
                          f"${meta['usd_charged_seam']:.4f}{retry_note}) "
                          f"-> {cell_dir}/")
                    ok += 1
                except Exception as exc:      # noqa: BLE001 — disclosed, not hidden
                    # A failed cell still BILLED for whatever attempts reached
                    # the API. Name the money here as well as in the receipt —
                    # a FAILED line that looks free is how spend goes missing.
                    spent = sink_charged(sink)
                    cost_note = (f" — billed ${spent:.4f} across "
                                 f"{len(sink)} attempt(s) before failing"
                                 if sink else " — nothing billed")
                    print(f"    [!      ] {date} {cell.dirname} FAILED "
                          f"({type(exc).__name__}: {exc}){cost_note}; "
                          "disclosed, other cells continue", file=sys.stderr)
            # Furniture parity is Content's law — check it, do not assume it.
            _report_parity(run_dir / test / date)
    finally:
        # THE RECEIPT — printed on the happy path, on a mid-run refusal, and
        # on an exception on its way out. Whatever else happened, the
        # principal learns what was billed.
        moved_note = (f" {moved} date(s) skipped — the record moved under the "
                      "run." if moved else "")
        print(f"moat-battery: {ok} cell(s) produced; charged "
              f"${charged_total:.4f} (estimated ${planned_est:.4f})."
              f"{moved_note} Seal them with `moat-battery pack --run`. The "
              "record was never touched.")
    return 0 if ok else 1


def _report_parity(date_dir: Path) -> None:
    cells = {p.name: (p / "narrative.md") for p in date_dir.iterdir()
             if p.is_dir()} if date_dir.is_dir() else {}
    texts = {n: f.read_text(encoding="utf-8") for n, f in cells.items()
             if f.is_file()}
    names = sorted(texts)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            ok, missing = furniture_parity(texts[names[i]], texts[names[j]])
            if not ok:
                print(f"    [WARN   ] furniture parity broken between "
                      f"{names[i]} and {names[j]}: {missing[:3]}",
                      file=sys.stderr)


def _cmd_t3(args) -> int:
    session, run_dir = _session_root(args)
    try:
        con = db.connect_readonly()
    except sqlite3.OperationalError as exc:
        print(f"moat-battery: refused — cannot open the record read-only "
              f"({exc})", file=sys.stderr)
        return 1
    try:
        dates = _resolve_dates(con, args, args.mornings)
        print("NewsLens moat battery, Phase 2 · T3 — Concept B input pack")
        print(f"  session {session}; editions: {', '.join(dates)}")
        print("  cost: $0.00 — this subcommand NEVER makes an LLM call "
              "(Concept B is hand-built from the ledger, design.md)")
        packs = []
        for date in dates:
            try:
                inputs = load_inputs(con, date)
            except generate.GenerateError as exc:
                print(f"    [BLOCKED] {date} — {exc}")
                continue
            md, payload = concept_b_pack(con, date, inputs)
            moved = len(payload["moved_files"])
            new = len(payload["new_files"])
            print(f"    [PLANNED] {date} — {moved} file(s) moved, {new} new "
                  f"file(s) opened")
            packs.append((date, md, payload))
        if not args.run:
            print("  DRY RUN — nothing written. Re-run with --run to write "
                  "the pack.")
            return 0
        for date, md, payload in packs:
            d = run_dir / "t3" / date
            d.mkdir(parents=True, exist_ok=True)
            (d / "concept-b-pack.md").write_text(md, encoding="utf-8")
            (d / "concept-b-pack.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8")
            print(f"    [+      ] {date} -> {d}/")
        return 0 if packs else 1
    finally:
        con.close()


def _cmd_pack(args) -> int:
    layout = getattr(args, "layout", "phase2")
    session, run_dir = _session_root(args)
    if layout == "writer":
        # The writer battery's tree is <out>/<date>/, with no phase2 level.
        run_dir = (Path(args.out) if args.out else paths.DATA_DIR / "battery"
                   ) / session
    print(f"NewsLens moat battery · blind pack ({layout} layout)")
    print(f"  session {session}; source {run_dir}/")
    print("  cost: $0.00 — the pack seals artifacts that already exist")
    try:
        cells = (_discover_writer_cells(run_dir) if layout == "writer"
                 else _discover_cells(run_dir))
    except OSError as exc:
        print(f"moat-battery: refused — {exc}", file=sys.stderr)
        return 1
    if not cells:
        print(f"moat-battery: refused — no produced cells under {run_dir}; "
              "run `moat-battery t1 --run` and/or `t2 --run` first",
              file=sys.stderr)
        return 1
    # Keep the MEMBERS, not just a count: composition needs the cells (QA R2).
    sets: Dict[Tuple[str, str], List[Dict]] = {}
    for c in cells:
        sets.setdefault((c["test"], c["date"]), []).append(c)
    # SETS ARE FLAGGED, NOT MERELY COUNTED (QA F4 rider + R2). The old output
    # printed "3 cell(s)" for a 2x2 and left the operator to notice 3 != 4;
    # and a right-SIZED set with no contrast in it (ctx-on__sectioned +
    # ctx-off__sectioned for a T1 pair) passed silently. A failed or
    # hand-stitched arm is exactly when a hole is easiest to miss and most
    # damaging: the set still LOOKS like a read-set. Seal it anyway (the
    # survivors are worth reading) but never let it pass as whole.
    bad = 0
    for (test, date), members in sorted(sets.items()):
        issue = set_composition_issue(test, members)
        flag = ""
        if issue:
            flag = f"  *** {issue} — this set cannot carry its full contrast"
            bad += 1
        print(f"    [PLANNED] {test.upper()} {date} — {len(members)} cell(s) "
              f"-> one shuffled read-set{flag}")
    if bad:
        print(f"  WARNING: {bad} of {len(sets)} read-set(s) are INCOMPLETE or "
              "WRONGLY COMPOSED. They will be sealed and marked in the key — "
              "read them knowing the contrast is partial, or re-run the "
              "missing cells first.")
    if not args.run:
        print("  DRY RUN — nothing written. Re-run with --run to seal.")
        return 0
    res = build_blind_pack(run_dir, args.seed, layout=layout)
    print(f"    [+      ] {res['sets']} set(s), {res['cells']} artifact(s) -> "
          f"{res['blind_dir']}/  (seed {res['seed']})")
    for line in res.get("incomplete") or []:
        print(f"    [WARN   ] incomplete read-set sealed — {line}")
    print("  Read the artifacts and QUESTIONS.md FIRST; the key is sealed, no "
          "manifest is in the blind dir, and render timestamps + arm names are "
          "redacted from the blind copies.")
    return 0


def _disclose_overlap(con, per_test: Dict[str, List[str]], args,
                      cap: float) -> None:
    """Name the T1/T2 ctx-on overlap and price it — WITHOUT sharing the draft.

    T1 and T2 both build a context-ON arm for any edition they share, so those
    drafts are paid for twice. Reusing one draft across both tests would save
    the money and is REFUSED, for three reasons worth stating where the
    operator can see them:

      1. HALO. The same two artifacts would appear in two read-sets in one
         session; a ranking formed in the T1 set carries into the T2 set.
         Stig named fatigue and halo as this battery's contaminants (data-2
         §3) — sharing manufactures both.
      2. NON-INDEPENDENCE. T1 and T2 are separate pre-registered tests with
         separate decision rules (T1: prose >=4/5 including one "would miss";
         T2: context-on wins >=2/3). Shared artifacts make the two readouts
         statistically dependent while the rules treat them as independent
         evidence — that inflates apparent agreement between them.
      3. RE-READ IS NOT A FRESH JUDGMENT. The second encounter with identical
         prose is a memory test, not a reading.

    The SAFE way to capture the same saving is disjoint editions, which the
    operator can choose with --dates. This function prices the overlap so that
    choice is informed rather than accidental."""
    t1, t2 = set(per_test.get("t1") or []), set(per_test.get("t2") or [])
    shared = sorted(t1 & t2)
    if not shared:
        return
    dup = 0.0
    model, _ = _writer_model_and_lane(args.lane)
    for date in shared:
        try:
            inputs = load_inputs(con, date)
        except (generate.GenerateError, sqlite3.OperationalError):
            continue
        prompt = build_cell_prompt(inputs, CONTEXT_ON, FORM_SECTIONED,
                                   args.form_mode)
        dup += (0.0 if args.lane == "subscription"
                else battery._arm_estimate(prompt, model))
    print(f"  OVERLAP: T1 and T2 both build a context-ON arm for "
          f"{', '.join(shared)} — about ${dup:.4f} of the session total is the "
          "same arm paid for twice.")
    print("  NOT deduped on purpose: one draft serving both read-sets would "
          "put identical artifacts in two sets in one session (halo + "
          "fatigue, Stig's named contaminants) and make two separately "
          "pre-registered tests statistically dependent.")
    pool = discover_dates(con, 64)
    if len(pool) < len(t1) + len(t2):
        return
    spare = [d for d in reversed(pool) if d not in t2][:len(t1)]
    t2_dates = sorted(t2)
    print(f"  To capture that saving SAFELY, give the tests disjoint "
          f"editions — e.g. `t1 --dates {','.join(sorted(spare))}` "
          f"against `t2 --dates {','.join(t2_dates)}`.")
    # THE RECIPE MUST RUN AS WRITTEN (QA F10). Suggesting commands the
    # harness's own F1 gate then refuses is worse than saying nothing: the
    # operator follows the advice and hits a wall. T2 on its registered N=3
    # editions is 6 drafts, which overruns the default $2.50 cap — so price
    # it here and either name the cap it needs or name the smaller set that
    # clears the current one.
    need = _t2_cap_needed(con, t2_dates, args)
    if need is None or need <= cap:
        return
    # Offer the LARGEST subset that clears the current cap, not a hardcoded
    # two: at a tight cap two editions may not fit either, and advice with no
    # runnable alternative is the same dead end F10 is about. One edition is
    # still a complete 2x2 read-set — fewer replicates, not a partial set.
    tail = ""
    for n in range(len(t2_dates) - 1, 0, -1):
        subset = t2_dates[-n:]
        need_n = _t2_cap_needed(con, subset, args)
        if need_n is not None and need_n <= cap:
            tail = (f" — or run {n} edition{'s' if n > 1 else ''}, "
                    f"`t2 --dates {','.join(subset)}` (~${need_n:.2f}), "
                    "which clears the current cap")
            break
    print(f"  NOTE: that T2 command needs BUDGET_CAP_USD_PER_RUN raised to "
          f"~${need:.2f} ({len(t2_dates)} editions x 2 context arms) — it is "
          f"over the ${cap:.2f} cap, so at the current cap the partial-set "
          f"gate refuses it{tail}.")


def _t2_cap_needed(con, dates: Sequence[str], args) -> Optional[float]:
    """What a T2 run over `dates` would charge — the cap it needs to clear the
    partial-set gate. None when nothing could be priced."""
    model, _ = _writer_model_and_lane(args.lane)
    if args.lane == "subscription":
        return 0.0
    total, priced = 0.0, 0
    for date in dates:
        try:
            inputs = load_inputs(con, date)
        except (generate.GenerateError, sqlite3.OperationalError):
            continue
        for ctx in (CONTEXT_ON, CONTEXT_OFF):
            prompt = build_cell_prompt(inputs, ctx, FORM_SECTIONED,
                                       args.form_mode)
            total += battery._arm_estimate(prompt, model)
            priced += 1
    return total if priced else None


def _cmd_plan(args) -> int:
    """The whole-session disclosure: what Phase 2 costs end to end."""
    cap = config.budget_cap_usd_per_run(os.environ)
    try:
        con = db.connect_readonly()
    except sqlite3.OperationalError as exc:
        print(f"moat-battery: refused — cannot open the record read-only "
              f"({exc})", file=sys.stderr)
        return 1
    try:
        session, run_dir = _session_root(args)
        model, cfg = _writer_model_and_lane(args.lane)
        print("NewsLens moat battery — PHASE 2 SESSION PLAN (dry run, $0)")
        print(f"  session {session}; artifacts would land under {run_dir}/")
        print(f"  writer seat held FIXED: {model} [{args.lane}]; "
              f"form-mode={args.form_mode}")
        print(f"  budget cap ${cap:.2f}/run (CHARGED dollars, PER INVOCATION "
              "— the session total below is the number to check against the "
              "~$3 authorisation)")
        total = 0.0
        per_test: Dict[str, List[str]] = {}
        for test, want in (("t1", 5), ("t2", 3)):
            dates = _resolve_dates(con, args, want)
            per_test[test] = list(dates)
            print(f"  {test.upper()}:")
            rows, cum, notes = _plan_test(
                con, test, dates, args.form_mode, DEFAULT_ABLATE,
                args.lane, cap)
            for n in notes:
                print(f"    · {n}")
            _print_rows(rows)
            print(f"    subtotal est charged ${cum:.4f}")
            total += cum
        print("  T3: $0.00 — Concept B pack makes no LLM call")
        print(f"  SESSION TOTAL est charged ${total:.4f} "
              f"(worst case with one corrected retry per draft: "
              f"${total * 2:.4f})")
        _disclose_overlap(con, per_test, args, cap)
        print("  DRY RUN — no calls made, no files written.")
        return 0
    finally:
        con.close()


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    # A real principal-run entrypoint (like cli.main / doctor.main /
    # battery.main): it reads the real record read-only and writes artifacts
    # under DATA_DIR/battery. Sanction the guarded paths for this process. A
    # sandboxed run (env overrides set) resolves those overrides regardless —
    # redirection outranks sanction — so the offline tests stay hermetic.
    paths.allow_real_paths()
    # Stage-0 M2 (M1 gate rider): the profile boundary, BEFORE any guarded
    # path resolves or the record opens. Same reasoning as battery.main — the
    # paid arms write artifacts under a profile-resolved DATA_DIR, so a typo'd
    # NEWSLENS_PROFILE must be refused, never provisioned.
    from . import profiles
    active_profile, refusal = profiles.resolve_entrypoint_profile()
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    for line in profiles.redirection_warnings(active_profile):
        print(f"warning: {line}", file=sys.stderr)
    config.load_env()
    if args.cmd in ("t1", "t2"):
        return _cmd_paid(args, args.cmd)
    if args.cmd == "t3":
        return _cmd_t3(args)
    if args.cmd == "pack":
        return _cmd_pack(args)
    return _cmd_plan(args)


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
