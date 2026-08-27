"""The writer (milestone 5): narrative generation + script adaptation.

Implements the Content Lead's contract
(workspace/debates/2026-07-05--newslens--content.md §5). The architectural
rule inherited from §5.7 and M3: FURNITURE IS CODE-OWNED. The model writes
only the per-story prose movements (headline / lede / why_it_matters /
watch_for, plus my_read on variant-B days) as validated JSON fields; code
assembles everything deterministic — title line, at-a-glance list, the
canonical override label, per-story meta-lines, the footer block (window
honesty line + standing caveat verbatim + variant stamp). Binding labels
never depend on a stochastic writer.

Voice variants (§5.2): strict daily alternation, A on even date-ordinals
(anchor: 2026-07-05, dogfood day 1, is A), computed — never model-chosen.
Forcing the off-parity variant produces a SAMPLE: rendered to a file, never
written to the briefings row, so alternation-of-record stays clean.

Chain semantics (ADR-0007): `generate` is end-to-end on-demand — by default
it runs ingest (fresh pull) then rank (fresh budget; idempotent, archived)
then writes. `--no-refresh` consumes the existing briefing row instead
(narrative-only iteration; also how the variant-B sample avoids re-ranking).

Script pass (§5.8): input is the assembled narrative text + structured label
data ONLY — never raw sources. The fact-subset rule and hedge preservation
are validated heuristically (§5.9 items 7-8: warn-grade, flagged for review);
mandatory disclosures (override spoken elements, revival dates) are
presence-checked hard; the sign-off is frozen furniture, appended if missing.
The spoken caveat was retired from the episode by NL-58 ruling 2 (the app
carries it) — no longer prompted, appended, or checked.

Instrumentation (§5.10) is a state file, not a migration:
data/generation_log.jsonl — one append-only JSON line per generate attempt
(variant, sample, word counts, per-step costs, disclosure renders, failures).
M7's read/listen events join against it by date.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import config, db, labels, llm, memory, paths, ranking
# NL-69: the repetition-word machinery moved to its home beside
# has_predating_antecedent (single source of truth; the write-side self-mark in
# migration 0014 shares it). Imported here so generate._REPETITION_RE and the
# read-site's _repetition_subject_units keep resolving unchanged.
from .memory_core import _REPETITION_RE, _repetition_subject_units

# Writer model — B4 (R-B4a): the writer/narrative seat flipped to Claude Opus
# 4.8 on the Claude API lane (adaptive thinking, effort xhigh). These names now
# DERIVE from the seam's SEATS["writer"] row (the ranking.RANK_MODEL:61-63
# precedent — "derive from SEATS or die": llm.SEATS["writer"] KeyErrors loudly if
# the seat ever disappears, never silently falling to a stale literal). So the
# legacy `usd` ledger key, _step_cost, and any model label track the seat
# automatically. REVERT to GPT-4o = flip SEATS["writer"] back to **_GPT4O_API in
# llm.py, one clean diff. Historical: gpt-4o from 2026-07-05 (4o-mini failed the
# register trigger day 1); Opus flip 2026-07-16 (B4, battery-judged).
WRITER_MODEL = llm.SEATS["writer"].model
WRITER_USD_PER_MTOK_IN = llm.SEATS["writer"].usd_per_mtok_in
WRITER_USD_PER_MTOK_OUT = llm.SEATS["writer"].usd_per_mtok_out
LLM_TIMEOUT_S = 120
# NL-63 M2 (amended slot contract): a 6-7 story edition at ~1,800-2,500 words is
# ~3,300-3,600 completion tokens of prose plus the JSON scaffold. GPT-4o sized
# this at 4,600 (prose + margin). B4 — the Opus 4.8 writer runs ADAPTIVE THINKING
# at effort xhigh, and thinking BILLS AS OUTPUT and counts against max_tokens: a
# ceiling sized only for prose would length-finish inside the thinking block (a
# failed run + a paid retry — the expensive failure). 16,000 leaves ~11-12k of
# thinking headroom above the ~4.6k prose ceiling. This is the on-the-wire
# ceiling, NOT the expected bill: actual output = thinking + prose, whatever the
# task earns (a short edition thinks less). The per-step pre-check (_est_cost)
# prices this ceiling pessimistically at Opus $25/MTok-out = $0.40, so the run
# budget cap MUST clear it (config.DEFAULT_BUDGET_CAP_USD_PER_RUN raised to $1.50
# — a principal money checkpoint). Revert-to-GPT-4o would drop this to ~4,600.
NARRATIVE_MAX_TOKENS = 16000
# THE PODCAST CONTRACT (principal 2026-07-14, twice-amended: floor REMOVED same
# day — "as long as it has to be"): a SHORTER digest, emergent length, ceiling
# only. Size the cap to the "definitely <11 min" ≈ 1,650-word ceiling: ~2,230
# prose tokens, ~1.35x headroom (the gate's minor-#2 lesson — never
# length-finish into a failed run + paid retry) -> 3,000.
# Honest to the shorter contract (down from the dead listening-band; the per-story
# guides sit well under this). Per-step cap pre-check holds the run under $0.25
# (est out at 3,000 tok = $0.030).
SCRIPT_MAX_TOKENS = 3000
NARRATIVE_TEMPERATURE = 0.3
SCRIPT_TEMPERATURE = 0.4

PROMPT_A = "narrative_variant_a.txt"
PROMPT_EDITOR = "editor_pass.txt"
# The editor re-emits the full (now doubled) story JSON to tighten it — its
# completion cap tracks the narrative's (NL-63 M2). 2,800 would clip the edited
# payload of a full edition, tripping the degrade-to-draft path spuriously.
EDITOR_MAX_TOKENS = 4600
EDITOR_TEMPERATURE = 0.2
PROMPT_B = "narrative_variant_b.txt"
PROMPT_SCRIPT = "script_adapt.txt"

BRIEFINGS_DIR_NAME = "briefings"
GENERATION_LOG_NAME = "generation_log.jsonl"

# ---------------------------------------------------------------------------
# NL-154 — GENERATION-LOG RETENTION (ratified NL-149 slate ④)
# ---------------------------------------------------------------------------
# THE MEASUREMENT FIRST, because the charter's figure was off by ~22x and the
# design follows the real shape. On his tree at a62c1aa:
#
#     699,786 bytes across 51 lines
#     27 RUN lines    659,686 B   min 368   max 41,432   mean 24,432
#     24 STAGE lines   40,100 B   min 777   max  3,732   mean  1,670
#
# So growth is ~26 KB per generate (one fat run line plus roughly one stage
# line), not the ~1.1 KB the row assumed. Whole-file read+parse measured at
# 2.58 ms today and projects to ~37 ms at 10 MB — real, but this is a slow leak
# and not a fire, which is why the policy below is conservative rather than
# aggressive.
#
# THE POLICY, in one sentence: when the live file passes LOG_MAX_BYTES, the
# OLDEST lines move into a numbered archive segment beside it, and the newest
# LOG_RETAIN_RUNS runs stay live.
#
# ROTATION MOVES BYTES AND NEVER DELETES THEM. Nothing here unlinks a record;
# archive segments accumulate. A deletion policy is HIS call and is deliberately
# absent — noted as this row's residue rather than quietly assumed.
#
# WHY SIZE TRIGGERS BUT COUNT RETAINS. Size is the thing that hurts (every page
# build reads this file); run-count is the thing the readers contract on (the
# reports screen shows 30). Triggering on the pain and retaining on the contract
# means the retention floor can never be cut by a run that happened to be fat.
LOG_MAX_BYTES = 4 * 1024 * 1024

# TWICE the reports screen's 30 (server._RUNLOG_MAX_ROWS), so that screen is
# whole with a full spare set behind it, and NL-146's scheduled-fire forensics
# keep months of mornings in the file `schedule.last_fire` reads. At the
# measured ~26 KB/run this settles the live file at ~1.5 MB.
LOG_RETAIN_RUNS = 60

# Archive segments live beside the log, are numbered in the order they were cut
# (0001 is the OLDEST), and say "archive" in their own name — the dispatch's
# "honestly named as archived". They are never rewritten once cut.
LOG_ARCHIVE_PREFIX = "generation_log.archive-"
LOG_ARCHIVE_SUFFIX = ".jsonl"
LOG_ARCHIVE_GLOB = f"{LOG_ARCHIVE_PREFIX}*{LOG_ARCHIVE_SUFFIX}"

# THE INDEX EXISTS FOR ONE READER AND ONE NUMBER. server._run_log_entries
# returns a TOTAL run count that the settings row renders ("N runs recorded")
# and the report's truncation line quotes ("the 30 most recent of N"). Once
# older runs live in an archive, a live-only count would silently shrink that
# number the morning after a rotation — the record would be intact and the
# screen would be lying about it. Counting the archives on every page build
# would reintroduce exactly the whole-file read this row exists to remove, so
# the counts are computed ONCE at rotation and stored here.
#
# DERIVED AND REBUILDABLE, never authoritative: every number in it can be
# recomputed by reading the segments, and a missing or corrupt index degrades to
# "no archived runs known" rather than to a crash or a guess.
LOG_INDEX_NAME = "generation_log.archive-index.json"

# Word bands [KNOB] — §5.1 totals / §5.8 script band. Warn-grade (§5.9 #9).
#
# NL-63 M2 — the AMENDED slot contract (DECISIONS 2026-07-13). DERIVATION of the
# doubled budgets from the contract's lead-weighted logic:
#   pre-amendment per-slot targets (the shipped shape): lead 320 · full-picture
#   (2-3) 220 each · In-Brief (4+) 140 each; edition band (900, 1300).
#   RULINGS: (a) stories 2-3 DOUBLE their Today-page depth -> 220 -> 440.
#   (b) "In Brief" carries the depth stories 2-3 had BEFORE -> the old medium
#       register, 220 (structured, NOT the dead <=60-word snippet). (c) 1 lead +
#       3 full-picture unchanged in COUNT. (d) lead-weighted: the lead must stay
#       the HEAVIEST register — at 440-per-medium the old 320 lead would sink
#       BELOW a full-picture story, breaking the weighting, so the lead doubles
#       too (320 -> 640), holding the same lead:medium ratio (~1.45) it had.
#   RESULT: lead 640 · full-picture (2-3) 440 · In-Brief (4+) 220. A 6-story day
#   = 640 + 2*440 + 3*220 = 2,180; a 7-story day = 2,400 — both inside the new
#   ~1,800-2,500 edition band the ruling names. The old 900-1,300 target is dead.
NARRATIVE_BAND = (1800, 2500)              # amended contract; 6-7 story edition
# (This is the TODAY-PAGE band only. The PODCAST is a separate, shorter digest
#  with its own emergent ceiling-only contract (<11 min; floor REMOVED
#  2026-07-14) — see the script constants below; the script never derives its
#  length from this narrative band.)
# Per-slot Today-page targets — a function so it is unbounded over 6-7 slots and
# the three registers stay explicit (documentation for the prompt/warn bands).
def per_slot_words(n: int) -> int:
    return 640 if n == 1 else 440 if n in (2, 3) else 220
# THE PODCAST CONTRACT REWRITTEN + REFINED (principal 2026-07-14, DECISIONS;
# supersedes the 07-02 10-13 min ruling — "way too long"). The episode is a
# SHORTER, lead-focused DIGEST, NOT a reading of the edition. Binding shifts:
#   (1) length is EMERGENT under a ceiling — never fill toward it: "it will be
#       as long as it has to be" (floor REMOVED, principal 2026-07-14 second
#       amendment, DECISIONS "podcast floor REMOVED"). The only length contract
#       is definitely <11 min ≈ 1,650 words at ~150 wpm (SCRIPT_CEILING_WORDS);
#       SCRIPT_DEGENERATE_WORDS below is a brokenness backstop, NOT a floor.
#   (2) the LEAD is the center of gravity — the deepest single segment.
#   (3) 2-5 stories: the lead + 1-4 more (never every story) — see
#       SCRIPT_MAX_STORIES / _script_coverage: the top slots by rank.
# Per-story quality GUIDES (ceilings, never floors to fill): lead ~400 · each
# supporting story ~200. Cold open+menu ~90, outro ~70.
# DERIVED per-k guide ceilings (open+outro + lead + 200*(k-1)): k=2 -> 760,
# k=3 -> 960, k=4 -> 1,160, k=5 -> 1,360 words (~5-9 min) — all under the
# episode ceiling with room to spare; no lower bound exists to clear.
def script_segment(n: int) -> int:
    return 400 if n == 1 else 200
SCRIPT_OPEN_WORDS = 90
SCRIPT_OUTRO_WORDS = 70
# Lead-focused selection: the episode covers the lead + up to 4 more, deterministic
# by the edition's own rank order (story_slots is rank-ordered; the lead is slot 1).
# No new LLM judgment — code names the covered slots; the writer covers exactly them.
SCRIPT_MAX_STORIES = 5
# Degenerate-output guard (podcast floor REMOVED — principal 2026-07-14, DECISIONS
# "NewsLens — podcast floor REMOVED", which supersedes the ">4 min" half of the
# same day's refined contract AND dissolves the pending thin-day relaxation).
# This is NOT a length or quality contract: the >4-min / 600-word lower bookend
# retired with that ruling — emergent length now runs unopposed DOWNWARD, and a
# naturally short episode is correct at ANY length the material earns. What stays
# is a brokenness-only sanity floor. Below it an output cannot physically carry
# the four required structural pieces — an orienting cold open, the dateline
# formula, a real lead segment (the episode's center of gravity), and the outro
# sign-off: the furniture alone (open + dateline + sign-off) is ~50-70 words and a
# real lead adds >=50, so under ~120 words the output is furniture wrapped around
# a stub — empty / degenerate, not a legitimately short episode. Coverage-
# INDEPENDENT: brokenness is not a function of k, so a thin day gets the SAME flat
# floor as a full one (the old coverage-relaxed min(600, ceiling*0.66) retired
# with the bookend). It sits far below the thinnest realistic complete episode (a
# clean 1-story digest lands ~300; the k=1 guide ceiling is 560) so an emergent-
# short episode never trips it, and well above degenerate stubs (~20-60 words).
# Length is the LAST broken signal — call_llm's length-finish check (truncation,
# ~generate.py:361) and validate_script's mandatory-disclosure `hard` list are
# checked FIRST; this floor only catches a non-truncated, disclosure-complete body
# that still delivered no real episode. Exact value is a gate-tunable
# implementation call (the ruling: threshold is "an implementation call").
SCRIPT_DEGENERATE_WORDS = 120
# Hard upper bound (the "definitely <11 minutes" ≈ 1,650 words): the prompt states
# it and the post-ship warn fires above the per-k guide ceiling (running long /
# filling toward the bound). Never fill toward it — emergent length lives beneath.
SCRIPT_CEILING_WORDS = 1650

# A1 (principal editorial review 2026-07-05): variant A is THE voice; B is
# retired and the alternation window ended early (alternation_end logged).
# The parity code below stays dormant for historical reproducibility.
ACTIVE_VOICE = "A"
# Variant anchor: A on EVEN toordinal — 2026-07-05 (dogfood day 1) is even.
VARIANT_A_PARITY = 0

# --- Canonical strings (contract §5.7 / §5.2 / §5.8; verbatim, frozen) -------
#
# ‼ FROZEN-SURFACE CHANGE, NL-138 — cited to the principal's ruling ④
#   (DECISIONS 2026-08-02, "SEVEN-ITEM RULING SLATE"). Saying so loudly because
#   §5.7 canonical strings are a frozen contract and a re-scope that arrives
#   quietly is the failure mode.
#
#   WAS (frozen since M5):
#       "**Outside your interests:** this story matches none of the tags or
#        threads steering your selection; it's here because {reason}"
#   where {reason} was the ranking model's one-sentence world_impact_reason.
#
#   The ruling kills that {reason} everywhere — generation, ledger, and every
#   reader surface — because the sentence was written about WORLD importance
#   and read as a claim about the READER'S interests. His finding, verbatim:
#   it "implies the user had global oil or middle east stability or energy
#   prices or international shipping as one of their topics, which they
#   didn't." With the reason gone the old string cannot be filled, so the
#   edition's override line RE-SCOPES to the ruled tag form — the same two-part
#   vocabulary NL-134 F3 already ships on every front-page story, composed from
#   labels.py so the markdown edition and the web front page can never drift:
#
#       **Chosen because:** Important World News
#
#   No placeholder remains: this is a constant now, not a template. The three
#   consumers (assemble_narrative, moat_battery's conformance render, and the
#   spoken-disclosure validator at validate_script) are re-pointed in the same
#   diff, and `_override_reason` — the helper that unwrapped the stored prose —
#   is deleted.
OVERRIDE_TEXT_LABEL = f"**{labels.WHY_CHOSEN_BECAUSE}** {labels.WHY_WORLD_NEWS}"
WINDOW_LINE = (
    "Generated {timestamp}. Covers items fetched {start} → {end}. NewsLens "
    "sees only its configured sources within this window."
)
# NL-151 clause 3 — THE SKIP DISCLOSURE. It lands now because clause 2 landed:
# `run_analysis` reports `fetch_skipped`, the assembler below renders from it,
# and the pin that guarded against half a disclosure shipping is inverted to
# guard the whole one. NL-148's objection is answered rather than overruled —
# the sentence is no longer wired to an unreachable trigger.
#
# HIS WORDS, VERBATIM (ruling 2026-08-09, carried into the 2026-08-13 ruling as
# clause 3's wording). The only substitution is the story's own title; no
# rewording, no grammar-fitting, no "we" voice.
#
# WHERE IT RENDERS: the assembler's footer block, AFTER the standing
# corroboration caveat — the literal bottom of the briefing, which is what he
# ruled. Placing it there also keeps the pinned footer ORDER intact (the window
# line still precedes the caveat), so the disclosure is additive to the
# furniture contract rather than a re-cut of it.
#
# TRUST-QUIET, and the register is a ruling not a preference: one plain
# sentence per skipped story, no count, no "we tried", no machinery theater —
# and the story PROMOTED into the freed depth slot gets NO badge anywhere. It
# is simply covered. A reader who never looks at the footer should not be able
# to tell a promotion happened; a reader who does should learn exactly what was
# lost and nothing more.
FETCH_SKIP_LINE = "Fetch failed for prioritized story {title}."


# ---------------------------------------------------------------------------
# NL-151b — THE DEPTH-TIER VECTOR (his arm (ii), 2026-08-14)
# ---------------------------------------------------------------------------
#
# The depth tier used to be a POSITION: slot 1 is the lead, 2-3 are the
# full-picture stories, 4+ are In Brief. Nine sites across four modules said so
# in their own words. Under arm (ii) that stops being true on a morning where a
# prioritized story loses its full text: the story keeps its SLOT NUMBER (which
# is what keeps NL-148 finding (b) — brief mis-attribution — off the table) and
# the depth TREATMENT walks down the ranking to the next story that has
# material. So position and tier come apart, and every site that inferred one
# from the other has to read the vector instead.
#
# THE VECTOR IS THE STAGE'S, NOT THIS MODULE'S. `run_analysis` reports
# `depth_tiers` — one entry per slot, the tier that slot ACTUALLY RAN AT, with
# every non-depth slot as "quick". It is the only place that knows, because it
# is the only place that watched the fetches. generate consumes it; it never
# re-derives it, for the same reason the clause-3 disclosure is sourced from
# `fetch_skipped` rather than from an absent brief: an absent brief has many
# causes and only the stage knows which one this was.
#
# ABSENCE MEANS TODAY, NOT "EVERYTHING IS QUICK". A sample, a `--no-refresh`
# run, a run whose analysis stage died — none of them have a live vector, and
# their answer is the positional contract, unchanged. That fallback is what
# keeps the twenty-eight test files that build payloads from
# `tier_for_position` green without a value swap.


def depth_arm_is_in_brief(arm: Optional[str] = None) -> bool:
    """Is the depth-skip contract running HIS arm — (ii), demote to in brief?

    Reads `analysis.FETCH_SKIP_ARM` when no arm is named, so there is exactly
    one answer in the process.

    THE DROP ARM ANSWERS TRUE, AND THAT IS A DISCLOSURE, NOT A BUG. Arm (i)
    ("drop" — the story leaves the edition body) was never built: it needs the
    slot-identity plumbing his ruling made unnecessary. Rather than let the
    constant imply a behaviour nobody wrote, every non-OFF arm gets the
    in-brief propagation and a pin says so by name. See the fork block in
    analysis.py."""
    from . import analysis as analysis_mod
    return (analysis_mod.FETCH_SKIP_ARM if arm is None else arm) \
        != analysis_mod.FETCH_SKIP_ARM_OFF


def edition_tiers(inputs: Dict, n: int) -> List[str]:
    """The per-position tier vector THIS edition runs on.

    The stage's vector when there is one and the arm is live; the positional A2
    contract otherwise. Short vectors pad with "quick" rather than reverting to
    positional — a slot the walk never reached was never in the depth tier, and
    silently promoting it back would be the L1 breach by the other door.

    ARM-GATED HERE and nowhere else. At the OFF arm this returns today's
    positional vector whatever the record holds, so the legacy behaviour is
    reproduced byte-for-byte by construction rather than by care."""
    from . import analysis as analysis_mod
    v = list(inputs.get("depth_tiers") or [])
    if not v or not depth_arm_is_in_brief():
        return analysis_mod.positional_tiers(n)
    return (v + ["quick"] * n)[:n]


def depth_slot_numbers(slots: List[Dict], tiers: List[str]) -> List[int]:
    """The slot NUMBERS holding the depth treatment.

    Two different keys meet here and the join is deliberate. The vector is
    POSITIONAL — the stage's walk built it by enumerating this same slot list
    — while the record (`analysis_briefs`, `latest_valid_brief`, the run log)
    is keyed by SLOT NUMBER. So the vector is read by position and the answer
    is reported as `slot["slot"]`, rather than assuming the two coincide.

    Under arm (ii) they do coincide, because nothing is filtered and nothing is
    reordered — that coincidence is the whole reason (ii) was the cheap arm and
    it is pinned by name (test_L2_slot_NUMBERING_never_moves...). Writing the
    zip anyway costs one line and means the sites that used to say `n <= 3` are
    not each re-deriving the equality a tenth time."""
    return [int(s["slot"]) for s, t in zip(slots, tiers)
            if t in ("full", "medium")]


VARIANT_B_STAMP = (
    'Voice: B — includes the narrator\'s own analytical judgments, always '
    'labeled "My read."'
)
VARIANT_A_STAMP = "Voice: A."
# RETIRED from the podcast pipeline (NL-58 ruling 2, DECISIONS 2026-07-10): the
# spoken caveat is deliberately OUT of the episode — the app carries the caveat,
# the podcast does not. The constant is kept only so tests can assert its
# ABSENCE from generated scripts; it is no longer prompted for or appended.
SPOKEN_CAVEAT = (
    "The usual reminder: outlet counts measure independent pickup across "
    "your sources, not truth — one strong single-source report can beat five "
    "copies of the same wire story."
)
SIGNOFF = "That's your briefing."

# Banned-string scan (§5.9 #10) — lowercase substring matching.
BANNED_STRINGS = [
    "remains to be seen", "only time will tell", "time will tell",
    "could potentially", "bears watching",
    "canary in the coal mine", "perfect storm", "domino effect",
    "tip of the iceberg", "watershed moment", "game-changer",
    "see you tomorrow",
    "you read", "you skipped",
    "impact score", "/10",
]

# A3 warn-scans (principal's own examples; warn-grade — quotes are legal)
TRUISM_WARN_STRINGS = [
    "critical component of", "profound implications", "raises questions about",
    "remains to be seen", "underscores the importance", "highlights the importance",
    "strain household budgets", "far-reaching consequences",
]
MORALIZE_WARN_STRINGS = ["divisive", "controversial", "troubling", "worrisome"]
MECHANICAL_TRANSITIONS = ["turning to", "in economic news", "finally,"]

# A7 (Round 2): sanctioned framing menus — the writer declares a framing per
# movement to fit the story; validators check MEMBERSHIP, never fixed names.
WHY_FRAMINGS = (
    "Why it matters", "Why markets care", "The debate", "What's unknown",
    "The background", "The stakes", "What changed",
)
WATCH_FRAMINGS = (
    "Watch for", "What happens next", "The next test", "What would change this",
)

_WORD_RE = re.compile(r"\b\w+\b")
_NUM_RE = re.compile(r"\d[\d,.]*")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


class GenerateError(RuntimeError):
    """Visible, handled generation failure — CLI prints it and exits 1."""


@dataclass
class GenReport:
    date: str
    variant: str
    sample: bool = False
    no_threads: bool = False
    narrative_text: str = ""
    script_text: str = ""
    narrative_words: int = 0
    script_words: int = 0
    per_story_words: List[int] = field(default_factory=list)
    steps: List[Dict] = field(default_factory=list)   # per-step token costs
    warnings: List[str] = field(default_factory=list)
    artifact_path: str = ""
    ingest_summary: str = ""
    continuity_status: str = "none"   # ok | none | corrupt
    analysis_usd: float = 0.0          # M9-M3: the analysis stage's spend (CHARGED)
    # NL-95: the analysis stage's SHADOW spend (always api-priced). Equals
    # analysis_usd on the api lane; on a subscription-lane analyst analysis_usd
    # is 0.0 while this stays non-zero — the edition cap keys off THIS, and the
    # failed-run ledger row records it beside (never inside) the money figure.
    # Exact twin of memory_usd / memory_shadow_usd below.
    analysis_shadow_usd: float = 0.0
    deep_views: Dict[str, str] = field(default_factory=dict)  # slot -> availability (Axel instrumentation)
    # NL-151: the prioritized stories this run skipped out of the depth tier,
    # as the analysis stage recorded them. On the run record because the report
    # and schedule surfaces (NL-146) must show a skip as honestly as the
    # briefing's footer does — a skip visible to the reader but absent from the
    # run log would be the record disagreeing with the machine.
    fetch_skipped: List[Dict] = field(default_factory=list)
    # NL-151b: the tier vector this edition ran on, and the slot numbers that
    # held the depth treatment. Recorded because after a demotion "which three
    # stories got the full picture" is no longer derivable from the slot
    # numbers — the reports screen, the archive reader and any later forensic
    # read would each have to guess, and three guesses is how a record starts
    # disagreeing with itself.
    depth_tiers: List[str] = field(default_factory=list)
    depth_slots: List[int] = field(default_factory=list)
    memory_usd: float = 0.0            # NL-63: state-rewrite spend charged (real money)
    # R-B3a (B3): the state-rewrite SHADOW spend (always API-priced). Equals
    # memory_usd on the api lane; on a subscription-lane state seat memory_usd
    # is 0.0 while this stays non-zero — the ledger row keys off THIS so a
    # $0-charged subscription rewrite never vanishes from the record.
    memory_shadow_usd: float = 0.0
    memory: Dict = field(default_factory=dict)  # NL-63: ledger/state instrumentation for diagnose
    # BUG-6/32 family (NL-63 M2 obs): call_llm's raw per-ATTEMPT cost record —
    # every writer attempt that reached the API, including ones that failed
    # validation (a degenerate-stub script, a truncation) and paid retries. The
    # OK path bills from report.steps (the display breakdown); this ledger is
    # what a FAILED-abort entry folds so its money record is never a null.
    attempt_ledger: List[Dict] = field(default_factory=list)
    # NL-149 item 2 — THE STAGE TIMELINE (the persisted half of his charter).
    # One entry per phase boundary the run actually passed through:
    #   {"label": <PROGRESS_LABELS word>, "model": <seat model or None>,
    #    "started_at": <iso Z>, "elapsed_s": <float, set when the stage closes>}
    # DISTINCT FROM `steps` ON PURPOSE, and not folded into it: `steps` is the
    # MONEY ledger, one row per LLM call (narrative attempt 1, editor attempt 2,
    # script_retry, script_adapt, state_rewrites, tts_*) — several rows for one
    # stage, and no row at all for ingest/rank/persist. The timeline is one row
    # per STAGE, which is the thing the reader watches and the thing his charter
    # asks the report to keep. Enriching `steps` with durations would have had to
    # answer "which of the editor's two attempts owns the stage's minutes"; there
    # is no honest answer, so the two ledgers stay side by side.
    stage_timeline: List[Dict] = field(default_factory=list)
    run_started_at: str = ""        # iso Z, stamped when the timeline opens
    run_elapsed_s: float = 0.0      # whole-run wall seconds, set at close
    # NL-146 — TRIGGER PROVENANCE. "scheduled" | "interactive" | "" (unrecorded).
    # Carried on the REPORT rather than threaded through _run_generate_body's
    # signature because both log arms already hold the report and only one of
    # them lives inside that function: a parameter would have had to be passed
    # down one level and back up none, for a value the pipeline never reads.
    # Set once in run_generate from its `trigger` argument; nothing else writes
    # it, nothing in the pipeline branches on it.
    trigger: str = ""


def wc(text: str) -> int:
    return len(_WORD_RE.findall(text))


def variant_for(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return "A" if d.toordinal() % 2 == VARIANT_A_PARITY else "B"


def _spoken_date(date_str: str) -> Tuple[str, str]:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = d.strftime("%A")
    return weekday, f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _time_of_day() -> str:
    h = datetime.now().hour
    if h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    return "evening"


# ---------------------------------------------------------------------------
# LLM call (same error taxonomy as ranking's, different knobs per pass)
# ---------------------------------------------------------------------------

# The writer family's User-Agent (narrative/editor/script all POST as this);
# lifted to a constant so the seam delegation keeps the exact bytes.
WRITER_UA = "NewsLens/0.1 (personal news briefing prototype; writer)"


# The seat _chat transports on for the current call. call_llm (the sole
# orchestrator) resolves seat_for_step(step) ONCE, gates it, sets it here, and
# resets it in a finally — so _chat rides the SAME seat the gate checked and the
# ledger attributes, while KEEPING ITS EXACT SIGNATURE (the ADR-0014 §2 law,
# pinned by test_signatures_preserved: _chat is the suite's monkeypatch target).
# B2 introduced this to transport editor/script on a different seat from
# narrative — closing the B1 "B4 residual" (a frozen writer transport under a
# per-step ledger) without a signature change. WHICH models those are is not
# stated here on purpose: it has changed three times since B2 (the B2-era text
# named Haiku and gpt-4o and was still saying so at NL-147), and `llm.SEATS` is
# the only honest answer. None => the writer seat (direct callers / the signature
# test keep the historical writer path).
# Request-scoped, single-threaded pipeline, always reset in call_llm's finally.
_ACTIVE_SEAT_CFG: "Optional[llm.SeatConfig]" = None

# B3-D6 (the generate flap window): the ONE effective_seat resolution per
# writer-family seat for a run, published by _run_generate_body and shared by
# EVERY reader — call_llm's gate + transport (via _ACTIVE_SEAT_CFG) + cost_sink,
# _step_ledger's DURABLE report.steps/token_cost row, and the fall warning.
# Post-D2 effective_seat is filesystem-dependent, and _step_ledger runs AFTER
# call_llm's _ACTIVE_SEAT_CFG teardown; re-resolving there let a `claude` binary
# that vanished mid-run PERSIST a lane the transport never rode (the D1 lie via
# the durable record) — or RAISE LaneUnavailable at a display site over an
# already-paid step. One resolution per seat closes it (the ranking _ACTIVE_RANK
# pattern). Keyed by seat; a direct call_llm (a test, no run scope) fresh-
# resolves. Reset in generate()'s outer finally.
_ACTIVE_STEP_SEATS: "Dict[str, tuple]" = {}


def _resolve_step_seat(step: str) -> "tuple":
    """The (SeatConfig, fallback_reason) a generate step rides — the run-scoped
    resolution _run_generate_body published, else a fresh effective_seat for a
    direct caller (the _effective_rank fallback). Every generate reader of a
    step's seat goes through here so the gate, transport, cost_sink, durable
    step row, and warning can never diverge on a mid-run binary flap (B3-D6)."""
    seat = llm.seat_for_step(step)
    snap = _ACTIVE_STEP_SEATS.get(seat)
    if snap is not None:
        return snap
    return llm.effective_seat(seat)


# B4 prompt caching: the narrative prompt is [stable law] then [volatile edition
# data]. This sentinel is the boundary — everything before it (voice + the full
# binding contract incl. the register-spec law) is byte-stable across a variant's
# calls within an edition run (the corrected retry, an idempotent same-day
# re-run); everything from it on (reader tags, threads, prior briefing, stories)
# is the per-edition material. Split there so the law rides a cache_control
# system block. The marker is unique to the narrative templates — editor_pass /
# script_adapt do not carry it, so their prompts never split.
_NARRATIVE_CACHE_SENTINEL = "\n=== THE READER'S TAGS"


def _split_cache_prefix(cfg: "llm.SeatConfig", prompt: str):
    """(system_prefix, user_body) for the anthropic api writer seat, else
    (None, prompt) unchanged. Gated on provider=='anthropic' so a REVERT to the
    gpt-4o writer sends the prompt as one user message exactly as pre-B4 (the
    openai provider has no cached-prefix surface); gated on the sentinel so only
    the narrative prompt splits. The law text is byte-preserved — only its ROLE
    moves (user -> system), the standard caching shape; the split is applied
    uniformly across the battery's model arms, so it never confounds the
    comparison. cache_control on the system block gives a within-TTL reuse (the
    retry / same-day re-run) its ~0.1x read; A/B do not share (variant B is
    retired — one live writer call per edition)."""
    if cfg.provider != "anthropic":
        return None, prompt
    idx = prompt.find(_NARRATIVE_CACHE_SENTINEL)
    if idx <= 0:
        return None, prompt
    return prompt[:idx], prompt[idx:]


def _chat(key: str, prompt: str, max_tokens: int, temperature: float,
          json_mode: bool) -> Dict:
    # Transport delegates to the provider seam (llm.py) on _ACTIVE_SEAT_CFG (set
    # by call_llm to the gated per-step seat; the writer seat by default).
    # temperature/max_tokens/json_mode are the caller's per-call knobs, passed
    # through unchanged. Returns the native-shaped `.raw` (OpenAI shape for the
    # gpt-4o seats; the anthropic provider synthesises the same shape for the
    # Claude lane) so call_llm's parse/retry law is untouched. Keeps its exact
    # signature: it is the suite's monkeypatch target.
    #
    # ENG-M0 (2026-08-06) — THE TEMPERATURE PASSED HERE NOW REACHES NO WIRE for
    # any step this function serves. narrative/editor/script all resolve to Opus
    # 4.8 seats carrying sampling=False, and the Claude 4.6+ family rejects
    # temperature with a 400, so the anthropic api provider OMITS it. The
    # parameter stays in the signature deliberately — it is the suite's
    # monkeypatch surface and a sampling=True rollback target would honor it —
    # but nothing here should be read as "these steps are temperature-controlled"
    # any more. Whatever determinism these seats have now comes from their
    # prompts and their validators, not from a sampling knob.
    cfg = _ACTIVE_SEAT_CFG or llm.resolve_seat("writer")
    system, user = _split_cache_prefix(cfg, prompt)   # B4: narrative caching
    return llm.chat(
        llm.LaneRequest(
            cfg=cfg,
            prompt=user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            user_agent=WRITER_UA,
            api_key=key,
            system=system,
            # openai offline-test seam: generate has always POSTed via
            # ranking.OPENAI_CHAT_URL (the suite patches that name). The
            # anthropic lane (writer/editor/script) reads its own endpoint +
            # credential and ignores this url.
            url=ranking.OPENAI_CHAT_URL,
        )
    ).raw


# The validation/truncation retry is CORRECTED, not blind (rank-side twin —
# ranking.RETRY_CORRECTION / commit 3b40d6a). A byte-identical re-POST at the
# same knobs reproduces the same near-miss: run 28 spent ~$0.025 twice for a
# guaranteed-identical rank failure; the 2026-07-14 script run failed script
# validation twice (565w under the since-REMOVED floor), never told why. call_llm
# is SHARED by the narrative/editor/script steps, so the correction can't name
# one step's rule the way rank's fixed id-fabrication text does — it ECHOES the
# validator's own ValueError, so attempt 2 is steered at exactly the rule that
# failed, uniformly for every validate-bearing step. Scoped like the rank fix:
# only the (ValueError/KeyError/IndexError/TypeError) malformed-or-validation
# class gets the correction; transport retries (429/5xx/timeout/connection)
# re-send the ORIGINAL prompt unchanged (those failures are not the model's
# doing). The block is anchored to the ORIGINAL prompt below, never compounding.
RETRY_CORRECTION_PREFIX = "CORRECTION — your previous draft was rejected: "
RETRY_CORRECTION_SUFFIX = (
    ". Fix exactly that failure and nothing else; every other contract rule "
    "above still binds. Return only the corrected output."
)


def call_llm(key: str, prompt: str, step: str, max_tokens: int,
             temperature: float, json_mode: bool,
             validate=None, cost_sink: Optional[List[Dict]] = None
             ) -> Tuple[str, Dict]:
    """One call + ONE retry total (network-shaped, truncation, or validation
    failure), then GenerateError. Returns (content, usage). `validate`
    raises ValueError to trigger the retry path.

    The validation/truncation retry is CORRECTED, not blind (rank-side twin,
    ranking.call_llm_validated): after a malformed/failed-validation attempt the
    retry prompt carries RETRY_CORRECTION_PREFIX + the exact ValueError text +
    RETRY_CORRECTION_SUFFIX, anchored to the ORIGINAL `prompt` (never
    compounding, never leaking across calls). Transport retries
    (5xx/429/timeout/connection) re-send the original prompt unchanged.

    `cost_sink` (money honesty, BUG-6/32 family): if given, EVERY attempt that
    reaches the API and returns usage records its real cost here BEFORE
    validation can reject it — so an attempt that truncated or failed
    validation (and still billed) is never lost from a failed run's money
    record. The OK-path caller keeps billing report.steps from the returned
    usage; this sink is a separate, complete per-attempt ledger."""
    # Fail-loud gate (D1 close): resolve THIS step's seat ONCE and preflight its
    # lane BEFORE any transport or retry. B2 CLOSES the B1 "B4 residual": _chat
    # now transports on THIS resolved seat_cfg (below), so the seat the ledger
    # attributes, the seat the gate checks, and the seat whose bytes ride the
    # wire are one and the same — a per-seat override (NEWSLENS_LANE_EDITOR=…)
    # can never let one seat's transport charge while the ledger files another
    # seat's lane. A config error surfaces immediately, never after a pointless
    # retry+sleep, and never a silent wrong-lane call.
    # B3-D2/D6: read the ONE run-scoped resolution for this step's seat (the gate
    # + fall already applied when _run_generate_body published it) — the SAME
    # (seat_cfg, _fb_reason) _step_ledger's durable row and the cost_sink ride, so
    # a mid-run binary flap can never fork the transport lane from the record. A
    # direct call_llm (no run scope) fresh-resolves + gates via effective_seat.
    # The narrative/writer seat is openai/api and never falls.
    seat_cfg, _fb_reason = _resolve_step_seat(step)
    last_error = "unknown"
    backoff = 1.0
    next_prompt = prompt  # augmented below only after a validation/malformed miss
    global _ACTIVE_SEAT_CFG
    _prev_seat_cfg = _ACTIVE_SEAT_CFG
    # B2: point _chat's transport at the SAME seat the gate preflighted and the
    # ledger attributes — whichever seat each step maps to (llm.SEATS is the map;
    # this comment named the B2-era Haiku/gpt-4o pair until NL-147). Because
    # check_lane already passed for seat_cfg, the transport
    # can never hit an unavailable lane inside the loop (the FIX-2 GenerateError-
    # wrapped-LaneUnavailable carve-out is structurally impossible — a raw
    # LaneUnavailable dies at the gate above, before the seat is armed).
    _ACTIVE_SEAT_CFG = seat_cfg
    try:
        for attempt in (1, 2):
            try:
                response = _chat(key, next_prompt, max_tokens, temperature,
                                 json_mode)
                usage = response.get("usage") or {}
                if cost_sink is not None:
                    # B2: lane/shadow keys from the SAME resolution the gate/
                    # transport used. legacy `usd` == usd_charged, sourced
                    # per-seat from the seam rather than from a global writer
                    # constant, so the entry never forks the model that ran from
                    # the price the ledger records. B3-D2: the fall label rides
                    # too.
                    fields = llm.cost_fields(seat_cfg, usage,
                                             fallback_reason=_fb_reason)
                    entry = {
                        "step": step, "attempt": attempt,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "usd": fields["usd_charged"],
                    }
                    entry.update(fields)
                    cost_sink.append(entry)
                choice = response["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise ValueError(
                        f"completion truncated at the {step} token cap "
                        f"({max_tokens})"
                    )
                content = choice["message"]["content"]
                if validate is not None:
                    validate(content)
                return content, usage
            except urllib.error.HTTPError as exc:
                detail = ranking._http_error_detail(exc)
                if exc.code in (401, 403):
                    # B2: provider-conditional off the in-scope seat_cfg so an
                    # anthropic editor/script seat names the RIGHT console; the
                    # openai arm is unchanged (the rollback path).
                    if seat_cfg.provider == "anthropic":
                        raise GenerateError(
                            f"Anthropic rejected the key (HTTP {exc.code}"
                            + (f"; {detail}" if detail else "")
                            + ") — regenerate at console.anthropic.com/settings/keys "
                            "and update .env"
                        ) from exc
                    raise GenerateError(
                        f"OpenAI rejected the key (HTTP {exc.code}"
                        + (f"; {detail}" if detail else "")
                        + ") — regenerate at platform.openai.com/api-keys"
                    ) from exc
                if (exc.code == 400 and seat_cfg.provider == "anthropic"
                        and "credit balance is too low" in detail):
                    # Anthropic signals an exhausted balance as a 400 (key valid
                    # but can't spend) — named precisely, before the generic arm.
                    raise GenerateError(
                        f"Anthropic account has no available credit ({detail}) — "
                        "the key is valid but can't spend; add credits at "
                        "console.anthropic.com billing (the doctor's read-only "
                        "key check cannot catch this)"
                    ) from exc
                if exc.code == 429 and "insufficient_quota" in detail:
                    raise GenerateError(
                        f"OpenAI account has no available quota ({detail}) — add "
                        "credits / check billing at platform.openai.com"
                    ) from exc
                if exc.code == 429:
                    last_error = f"rate limited (HTTP 429{'; ' + detail if detail else ''})"
                    backoff = ranking._retry_after_seconds(exc)
                elif exc.code >= 500:
                    last_error = f"HTTP {exc.code}" + (f" ({detail})" if detail else "")
                else:
                    provider_name = ("Anthropic" if seat_cfg.provider == "anthropic"
                                     else "OpenAI")
                    raise GenerateError(
                        f"{provider_name} rejected the {step} call (HTTP {exc.code}"
                        + (f"; {detail}" if detail else "") + ")"
                    ) from exc
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                # malformed output / failed validation / truncation — the spec'd
                # retry-then-fail path. Correct the retry so it is not a byte-
                # identical re-POST (rank run-28 precedent): quote the exact
                # failure so attempt 2 is steered at the rule that failed.
                # Anchored to the ORIGINAL `prompt`, not `next_prompt`, so a
                # correction can never compound if the attempt count ever grows
                # past two.
                last_error = f"invalid {step} output ({exc})"
                next_prompt = (
                    prompt + "\n\n"
                    + RETRY_CORRECTION_PREFIX + str(exc) + RETRY_CORRECTION_SUFFIX
                )
            except Exception as exc:  # timeout / connection — network-shaped
                # transport, not the model's doing: the retry re-sends ORIGINAL
                # bytes (next_prompt stays `prompt` — no correction).
                # NL-160 — THE AUTH CARVE-OUT (the ranking.py twin). Re-sending
                # ORIGINAL bytes is exactly the wrong move for an expired
                # session: identical bytes, identical CLI, identical failure one
                # backoff later. The editor/script/writer steps all sit on the
                # subscription lane, so this arm can carry it.
                if isinstance(exc, llm.SubscriptionAuthError):
                    raise GenerateError(
                        f"{step} cannot authenticate: {exc} — nothing was "
                        "written, and no retry was attempted because this "
                        "failure class cannot succeed on one (this failure is "
                        "logged)"
                    ) from exc
                last_error = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
            if attempt == 1:
                time.sleep(backoff)
        raise GenerateError(
            f"{step} failed after one retry: {last_error} — nothing was written; "
            "re-run `newslens generate` (this failure is logged)"
        )
    finally:
        # Always disarm the request-scoped seat so a direct _chat call (or the
        # next call_llm) is never contaminated by this step's seat.
        _ACTIVE_SEAT_CFG = _prev_seat_cfg


def _step_seat_cfg(step: str) -> "llm.SeatConfig":
    """The resolved seat for a generate step (llm.seat_for_step maps
    narrative*->writer, editor*->editor, script*->script). The model/lane/price
    behind each of those seats is `llm.SEATS`' to say, never this docstring's —
    it asserted "editor/script are the Claude API Haiku seats; the
    narrative/writer family stays gpt-4o" until NL-147, by which point all three
    clauses were false."""
    return llm.resolve_seat(llm.seat_for_step(step))


def _est_cost(prompt: str, max_tokens: int, step: str = "narrative") -> float:
    # B2: the pre-call budget estimate uses the STEP'S seat prices, not a global
    # writer constant — so the ladder's headroom math tracks the seat that will
    # actually be charged.
    cfg = _step_seat_cfg(step)
    return (len(prompt) / 3.5 / 1e6) * cfg.usd_per_mtok_in + (
        max_tokens / 1e6
    ) * cfg.usd_per_mtok_out


# ---------------------------------------------------------------------------
# THE LANE-AWARE CAP (principal's ruling 2026-08-12, DECISIONS item 2)
# ---------------------------------------------------------------------------
#
# His words: "raise the budget cap, this is all running over subscription
# anyway. My generation shouldn't fail because of it." His run had died at the
# script stage on $2.5513 of SHADOW spend against a $2.50 cap while the amount
# actually billed to him was $0.00 — every seat on the run was a subscription
# seat, where `usd_charged` is 0 by construction (llm.cost_fields: charged ==
# shadow on the api lane, 0.0 on the subscription lane).
#
# The cap therefore splits in two, and only in two:
#
#   CHARGED dollars   -> the HARD cap, byte-for-byte the behaviour that ships
#                        today. On an all-api run charged == shadow and the
#                        estimate is charged, so this arm is the OLD predicate
#                        exactly; BUDGET_CAP_USD_PER_RUN keeps meaning what it
#                        has always meant for real money.
#   SHADOW-only spend -> a WARN. The step proceeds. Nothing was billed, so
#                        nothing may kill the run.
#
# NL-80 (the no-output-ceiling class) is why the warn is not a silent pass: the
# warn CARRIES THE RUNNING TOTALS (shadow, charged, cap) into report.warnings ->
# the generation-log entry, so a subscription run whose shadow figure runs away
# is louder on the record than it was when it merely died. The other half of
# that guard — the analysis stage's derating ladder and the finite cap itself —
# is DELIBERATELY UNTOUCHED (DECISIONS 2026-08-12 item 2 names the unattended-
# derating law as the thing that stays guarding): a shadow-heavy run still
# derates its analysis material, it just no longer dies.
#
# Trust-quiet: this lands in warnings/receipts only. Nothing about a budget
# verdict reaches the edition body.

# The three run-killing/step-blocking cap gates below all speak through this one
# verdict so they cannot fork (the "named ONCE" idiom this module already uses
# for MATERIAL_BUDGET_CHARS et al).
CAP_KILL, CAP_WARN, CAP_CLEAR = "kill", "warn", "clear"


def _cap_verdict(step: str, est: float, spent: float, charged: float,
                 cap: float) -> str:
    """CAP_KILL / CAP_WARN / CAP_CLEAR for one about-to-be-made call.

    `spent` is the run's SHADOW total (Onna's law — the cap ladder has always
    accumulated shadow so the subscription lane degrades like the api lane);
    `charged` is the run's real money. `est` is shadow-denominated, so the
    charged projection adds it ONLY when this step's seat actually bills — the
    same (seat, fallback) resolution call_llm's gate, transport and durable
    ledger ride, never a fresh one, so a mid-run binary flap cannot make the
    budget gate and the ledger disagree about which lane paid.

    KILL when real money would cross the cap; WARN when only the shadow figure
    would; CLEAR otherwise. Nothing here spends, logs, or mutates."""
    cfg, _reason = _resolve_step_seat(step)
    est_charged = est if cfg.lane == "api" else 0.0
    if charged + est_charged > cap:
        return CAP_KILL
    if spent + est > cap:
        return CAP_WARN
    return CAP_CLEAR


def _cap_warn_line(step: str, est: float, spent: float, charged: float,
                   cap: float) -> str:
    """The warn's text. It states the totals ON PURPOSE (NL-80): a reader of the
    generation log must be able to see runaway shadow spend even though it no
    longer stops anything."""
    return (
        f"budget: {step} continued past the ${cap:.2f} cap on SHADOW spend only "
        f"— ${spent:.4f} shadow + ${est:.4f} estimated, ${charged:.4f} actually "
        f"charged. Subscription-lane spend is not billed per call and must not "
        f"kill a run (principal 2026-08-12); the cap still HARD-STOPS charged "
        f"dollars. Totals are recorded here so runaway shadow stays visible."
    )


def _step_cost(usage: Dict) -> float:
    # The writer/narrative seat's rate — WRITER_USD_* now DERIVE from
    # SEATS["writer"] (B4: Opus 4.8 $5/$25), so this helper re-prices with the
    # seat automatically. The per-step DURABLE ledger uses _step_ledger below
    # (seat-sourced via llm.cost_fields); this helper stays for the narrative
    # path's direct callers and their pinned test.
    return (usage.get("prompt_tokens", 0) / 1e6) * WRITER_USD_PER_MTOK_IN + (
        usage.get("completion_tokens", 0) / 1e6
    ) * WRITER_USD_PER_MTOK_OUT


def _step_ledger(step: str, usage: Dict) -> Dict:
    """The per-step DURABLE-ledger fields for report.steps (-> persist_generation
    -> briefings.token_cost + the generation log) — model/lane/usd plus the
    shadow keys, sourced from the STEP'S seat (B2, whatever those seats are on
    the day — see llm.SEATS). Replaces the WRITER_MODEL + WRITER-rate _step_cost
    that forked the ledger the moment editor/script left gpt-4o. B3-D6: reads the
    SAME run-scoped
    resolution call_llm's gate/transport/cost_sink used (via _resolve_step_seat),
    NEVER a fresh effective_seat — so a `claude` binary that vanished mid-run
    can't persist a lane the transport didn't ride, or raise LaneUnavailable at
    this display site over an already-paid step. A fallen editor/script row
    records lane=api(fallback:…) exactly as the wire did. Direct callers fresh-
    resolve (the _effective_rank fallback)."""
    cfg, reason = _resolve_step_seat(step)
    fields = llm.cost_fields(cfg, usage, fallback_reason=reason)
    return {"model": cfg.model, "lane": fields["lane"],
            "usd": fields["usd_charged"], **fields}


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

def load_briefing_inputs(con: sqlite3.Connection, date: str,
                         prefer_pending: bool = False) -> Dict:
    """The stage inputs for `date`, built from the briefings row.

    prefer_pending (NL-106) is the MID-RUN reading: when a regenerate has staged
    a new selection (briefings_pending), the stages consume the STAGED slots and
    corroboration while the live row still holds the readable old edition for
    the reader. Everything else — row id, date, every error — is unchanged.

    It defaults OFF, and exactly one caller turns it on: _run_generate_body, the
    in-flight run — and there, only for runs that can PROMOTE (gate FIX-2: a
    sample cannot, and must keep its standing promise that it consumes the
    existing row). The other callers (backfill, the prompt/cost batteries) are
    reading the PUBLISHED edition of record, and must keep seeing what the
    reader sees — a backfill that built ledger deltas from a staged selection
    against a published narrative would be the mixture this whole change exists
    to prevent, one table over.

    Returns `staged_created_at`: when a staged payload was consumed, the moment
    it was staged; None otherwise. That is how a caller can tell it is finishing
    someone else's run (gate FIX-3) — the key is additive and inert for the three
    default callers, which never consume a staged payload at all.
    """
    row = con.execute(
        "SELECT * FROM briefings WHERE date = ?", (date,)
    ).fetchone()
    if row is None:
        raise GenerateError(
            f"no briefing row for {date} — generate the record first "
            "(a plain `newslens generate`), then request samples or "
            "narrative-only re-runs against it"
        )
    slots_json = row["story_slots"]
    corroboration_json = row["corroboration_labels"]
    pending = ranking.pending_selection(con, date) if prefer_pending else None
    if pending is not None:
        slots_json = pending["story_slots"]
        corroboration_json = pending["corroboration_labels"]
    try:
        # NOTE (NL-106): the two refusals below are byte-identical whichever
        # payload was read — deliberately, so no new reader-facing string enters
        # under the ratified register. They still fire on the payload actually
        # consumed, which is the part that matters: a corrupt or empty STAGED
        # selection refuses the run exactly as a corrupt live row does.
        slots = json.loads(slots_json or "[]")
    except ValueError as exc:
        raise GenerateError(f"briefings.story_slots for {date} is corrupt: {exc}") from exc
    if not slots:
        raise GenerateError(
            f"the briefing row for {date} has no story slots — rank refused "
            "or produced nothing; re-run `newslens rank`"
        )
    items_by_slot: Dict[int, List[sqlite3.Row]] = {}
    for s in slots:
        ids = s.get("item_ids") or []
        marks = ",".join("?" for _ in ids) or "NULL"
        items_by_slot[s["slot"]] = con.execute(
            f"SELECT id, outlet, title, url, published_at, raw_excerpt,"
            f" source_type, wire_syndication_flag FROM source_items"
            f" WHERE id IN ({marks}) ORDER BY id",
            ids,
        ).fetchall() if ids else []

    threads = con.execute(
        "SELECT topic, principal_note FROM memory WHERE status = 'active'"
        " ORDER BY last_referenced_briefing_id IS NULL,"
        " last_referenced_briefing_id DESC, id LIMIT ?",
        (memory.CONTEXT_CAP,),
    ).fetchall()

    # Continuity, with the M4-gate mandated distinction: a prior row whose
    # slots JSON is corrupt is NOT the same as "no prior briefing" — silent
    # continuity loss is unacceptable in the product whose point is continuity.
    prior_row = con.execute(
        "SELECT id FROM briefings WHERE date < ? ORDER BY date DESC LIMIT 1",
        (date,),
    ).fetchone()
    prior_ctx = memory.prior_briefing_context(con, date)
    if prior_row is not None and prior_ctx is None:
        continuity_status = "corrupt"
    elif prior_ctx is not None:
        continuity_status = "ok"
    else:
        continuity_status = "none"

    # NL-107 tooth (d), F-a — rival-gated by gate fix F2. The fetch window
    # belongs to the rank that chose the slots being read, so this read follows
    # the payload, but ONLY where the two can actually disagree:
    #   * consuming a STAGED selection (the in-flight regenerate) -> the newest
    #     ok run IS that run's own rank; take it, unbounded.
    #   * reading the LIVE row while a rival is staged -> bound the rank by the
    #     edition's publish stamp, exactly as the briefs are bound. Without it a
    #     date carrying a dead regenerate's rank hands default readers the OLD
    #     slots with the NEW run's window — the same pairing defect one column
    #     over.
    #   * reading the LIVE row with NO rival -> unbounded, byte for byte the
    #     pre-batch read. This is gate fix F2, and it is not cosmetic: bounding
    #     here bit the ORDINARY first run, whose ranking_runs row lands with
    #     real milliseconds just after a rank stamp floored to the second
    #     (ranking.py:1498). QA measured ~0.1%/run of first runs losing their
    #     own window that way, and a missing window degrades the footer line to
    #     the render WALL CLOCK — the de-blinding channel gate F-B closed in
    #     the moat harness. No rival means nothing to disambiguate, so the read
    #     that cannot be wrong is the one that ran before this batch.
    # The gate's ruling that this metadata is advisory stands; the rival gate
    # is what makes pairing it free.
    from . import analysis as _analysis
    window_meta = None
    if pending is None and _analysis.rival_exists(con, date):
        run_row = con.execute(
            "SELECT meta, ran_at FROM ranking_runs WHERE date = ? AND"
            " json_extract(meta, '$.status') = 'ok' AND ran_at < ?"
            " ORDER BY id DESC LIMIT 1",
            (date, _analysis.publish_bound(row["generated_at"])),
        ).fetchone()
    else:
        run_row = con.execute(
            "SELECT meta, ran_at FROM ranking_runs WHERE date = ? AND"
            " json_extract(meta, '$.status') = 'ok' ORDER BY id DESC LIMIT 1",
            (date,),
        ).fetchone()
    if run_row:
        try:
            window_meta = {
                "window": json.loads(run_row["meta"]).get("window"),
                "ran_at": run_row["ran_at"],
            }
        except ValueError:
            window_meta = None

    try:
        corroboration = json.loads(corroboration_json or "{}")
    except ValueError:
        corroboration = {}

    # NL-75 rung (a): attach each slot's thread MEMORY for the writer — the
    # standing state + last-N dated deltas (Engineering's one missing hop:
    # the analyst already had this via the P-channel; the writer had thread
    # NAMES only). before_date=date is strict (prior coverage only; today's own
    # delta is written after generation). Plus the expired watch-items this
    # edition must CONVERT (the accountability loop). Cheap read-side joins on
    # tables the renders already trust; skipped gracefully pre-migration.
    from . import memory_core as _mc
    for s in slots:
        topics = [t for t in (s.get("matched_memory") or []) if t]
        blocks: List[str] = []
        expired: List[Dict] = []
        for topic in topics:
            # D7 (NL-75 QA): the ledger/state read and the expired-watch read are
            # DECOUPLED seams. The ledger read (0010/0012 tables, always present
            # once 0012 has run) must never die with the watch read: on a
            # 0013-less DB the shared try/except cleared the already-built ledger
            # blocks, silently disabling rung (a) — the approved core deliverable
            # — whenever the un-approved 0013 migration was declined. Now the
            # watch read's absence degrades ONLY the register (expired -> []); its
            # failure surfaces at the post-persist register write ("watch-items:
            # register update failed after persist"), so declining 0013 degrades
            # the watch register alone, never the ledger.
            try:
                blk = _mc.writer_thread_context(con, topic, before_date=date)
                if blk:
                    blocks.append(blk)
            except sqlite3.OperationalError:
                # pre-0012 DB (supersession table absent) — this thread's ledger
                # degrades to nothing rather than crash the run.
                pass
            try:
                expired.extend(
                    _mc.expired_unconverted_watch_items(con, topic, date))
            except sqlite3.OperationalError:
                # pre-0013 DB (watch_items absent) — the register degrades to []
                # INDEPENDENTLY; the ledger read above is unaffected.
                pass
        s["thread_ledger"] = "\n".join(blocks)
        # NL-77 writer-flow LAST: the cold-start BACKGROUNDER, as its OWN labeled
        # section — context for the writer, never blended into edition prose as
        # unattributed knowledge. Only a 'ready' baseline as-of this edition
        # surfaces; the block carries its non-licensing law inline. Degrades to
        # '' on a pre-0017 DB (the read is table-guarded).
        bg_blocks: List[str] = []
        for topic in topics:
            try:
                bg = _mc.writer_baseline_block(con, topic, before_date=date)
                if bg:
                    bg_blocks.append(bg)
            except sqlite3.OperationalError:
                pass
        s["thread_baseline"] = "\n\n".join(bg_blocks)
        s["expired_watch"] = expired

    return {
        "row": row,
        "slots": slots,
        "items_by_slot": items_by_slot,
        "threads": threads,
        "prior_ctx": prior_ctx,
        "continuity_status": continuity_status,
        "window_meta": window_meta,
        "corroboration": corroboration,
        # NL-106 FIX-3: the staging stamp of the payload this call consumed,
        # None when the live row was read. The one fact a caller needs to know
        # it is completing a run it did not start.
        "staged_created_at": pending["created_at"] if pending is not None else None,
    }


# `_override_reason` is DELETED (NL-138, ruling ④). It unwrapped the stored
# `override_label` back into the model's prose sentence for three consumers
# (the markdown edition line, the writer's spoken-labels block, and the spoken
# validator's presence check). All three now use the code-owned tag form; there
# is no stored label left to unwrap. Its "it cleared a high global-impact bar"
# fallback went with it — that string existed to cover an unparseable label,
# and a constant cannot be unparseable.


def _slot_budget_line(slot_n: int, tier: Optional[str] = None) -> str:
    # Budget lines are tier-aware (A2). NL-63 M2 — the AMENDED slot contract:
    # the lead and both full-picture stories DOUBLE their Today-page depth, and
    # "In Brief" (slot 4+) is the OLD medium register (structured — NOT the dead
    # <=60-word snippet). Slots 1-3 are EXACTLY the three full-picture stories.
    #
    # NL-151b: `tier` OVERRIDES the slot-number derivation, and this is the site
    # where the demotion becomes real for the writer. Under his arm (ii) slot 1
    # can be an In-Brief story and slot 4 can be the lead — a budget line keyed
    # on the number would brief the writer for a tier the validator then
    # rejects, which is a failed run rather than a demoted story. `tier=None`
    # reproduces the positional line exactly, for every caller that has no
    # vector (samples, fixtures, the twenty-eight-file payload contract).
    #
    # LENGTH REGIME 2026-07-30 (principal-ratified, product-wide): these numbers
    # are TARGETS, not floors. The NL-63 M2 fix stated them "HARD, as floors not
    # decoration" to cure a writer under-delivering its bands — but a minimum a
    # model cannot honestly reach is an instruction to PAD, and padding is the
    # one thing the no-fabrication rule forbids. So the floor words come out and
    # the ORDERING rule (the lead is the day's longest story) stays: on a thin
    # day the other stories tighten, the lead does not inflate.
    if tier is None:
        tier = "full" if slot_n == 1 else "medium" if slot_n in (2, 3) else "quick"
    if tier == "full":
        return ("FULL tier (the lead) — TARGET ~640 words WHEN THE MATERIAL "
                "SUPPORTS IT. This is THE LEAD: it must be the single LONGEST "
                "story of the day, visibly longer than any full-picture story "
                "below. The lead leads by WEIGHT, not by word count: a lead "
                "built on one or two full texts is SHORT, and short is a PASS. "
                "The no-fabrication rule outranks every length rule — never "
                "reach a number with material you were not given. On a thin day "
                "the other stories TIGHTEN so the lead still leads; the lead "
                "never pads to get there. On a rich day, spend the budget: lede "
                "3-6 sentences; why_it_matters a full 8-12 sentences built from "
                "source specifics; watch_for 2-3 sentences")
    if tier == "medium":
        return ("MEDIUM tier (a full-picture story, DOUBLED depth) — TARGET ~440 "
                "words, shorter than the lead but a real full "
                "picture: lede 3-5 sentences; why_it_matters 5-8 sentences; "
                "watch_for 1-2 sentences")
    return ("QUICK tier (the 'In Brief' register — a compact STRUCTURED mini-"
            "story, NOT a headline snippet) — TARGET ~220 words: "
            "lede 2-3 sentences; why_it_matters 3-5 sentences; watch_for 1-2 "
            "sentences")


def build_narrative_prompt(date: str, variant: str, inputs: Dict) -> str:
    prompt_file = PROMPT_A if variant == "A" else PROMPT_B
    template = (paths.PROMPTS_DIR / prompt_file).read_text(encoding="utf-8")

    cfg = config.load_sources()
    tag_lines = [f"- {t} (broad)" for t in cfg.interests_broad]
    tag_lines += [f"- {t} (specific)" for t in cfg.interests_granular]

    thread_lines = []
    for t in inputs["threads"]:
        note = f"  [emphasis note, steer silently: {t['principal_note']}]" if t["principal_note"] else ""
        thread_lines.append(f"- {t['topic']}{note}")

    if inputs["continuity_status"] == "ok":
        prior_block = inputs["prior_ctx"]["text_block"] + (
            "\n(Callback rules apply: delta-only, max 2 optional callbacks.)"
        )
    elif inputs["continuity_status"] == "corrupt":
        prior_block = (
            "(A prior briefing exists but its record is unreadable — "
            "continuity is suspended for this run. Do not reference prior "
            "coverage.)"
        )
    else:
        prior_block = "(This is the first briefing — no prior coverage to reference.)"

    # NL-134 F2(a) — COLD-START HONESTY. On a profile's FIRST briefing there is
    # no "usual" to be off: any your-usual-map / your-interests-history framing
    # is a FALSE claim about a reader history that does not exist. (The TWO
    # CLOCKS law in both variant prompts already forbids narrating the reader's
    # clock; this makes the writer able to obey it, by telling it which clock
    # reads zero.) The signal is already computed above and already drives
    # prior_block — 'none' means no prior briefing row exists at all. 'corrupt'
    # is deliberately NOT first-edition: a prior briefing DOES exist there, its
    # record is merely unreadable, so the reader does have a history and the
    # honest move is silence about it, not a cold-start claim. The rank meta's
    # history_days 0.0 / "first briefing — full cap" says the same thing, but
    # that metadata is advisory (NL-107 gate ruling) while continuity_status is
    # authoritative here — so the authoritative signal is the one threaded.
    first_edition = inputs.get("continuity_status") == "none"

    # NL-151b: the tier the writer is BRIEFED for, per position, from the
    # analysis stage's own walk. Resolved once for the whole prompt so the
    # budget line and the analysis-availability note below can never disagree
    # about a slot's tier — they did not disagree before because both derived
    # from the slot number, and that shared derivation is exactly what moves.
    prompt_tiers = edition_tiers(inputs, len(inputs["slots"]))

    story_parts = []
    for i, s in enumerate(inputs["slots"]):
        n = s["slot"]
        tier = prompt_tiers[i] if i < len(prompt_tiers) else "quick"
        lines = [f"STORY {n} — budget: {_slot_budget_line(n, tier)}"]
        # NL-63 M2: slots 1-3 are the EXACTLY-THREE full-picture stories (1 lead
        # + 2 medium); slot 3 no longer demotes to quick (the amended contract
        # pins it to full-picture), so the old analyst medium-vs-quick annotation
        # is gone — _slot_budget_line already states MEDIUM for it. NL-151b
        # amends the SOURCE of that tier, not the register of any tier: the
        # three full-picture stories are still exactly three, they are just not
        # guaranteed to be slots 1-3 on a morning a prioritized fetch died.
        lines.append(f"working title (rewrite it): {s.get('story_title', '')}")
        lines.append(f"what happened (one line): {s.get('summary', '')}")
        # NL-138 (ruling ④): the "ranking's significance seed" line is GONE.
        # It fed the ranker's one-sentence world_impact_reason to the writer as
        # seed material for the "Why it matters" movement (content §5.1,
        # ADR-0007 item 9). The field is no longer generated, so the line could
        # only ever fire on an archived edition re-run — and re-seeding a new
        # narrative from prose the ruling retired for a register error is
        # exactly the leak the ruling's "generation itself" clause closes. The
        # writer keeps every structured input below (tags, threads, ledger,
        # baseline, watch-fors) plus the cluster's own summary and material.
        tags = ", ".join(t["name"] for t in s.get("matched_tags", [])) or "(none)"
        threads_m = ", ".join(s.get("matched_memory", [])) or "(none)"
        lines.append(f"matched tags: {tags} | matched threads: {threads_m}")
        # NL-75 rung (a): the thread's MEMORY reaches the writer here — standing
        # state + last-N dated deltas. Dates are load-bearing: compose the arc
        # in the sentence ("what began as X on Jul 5 had by Jul 10 become Y"),
        # never as furniture. The two-clocks law: this is EDITION history, in
        # prose; the reader's own history is never referenced.
        if s.get("thread_ledger"):
            lines.append(s["thread_ledger"])
        # NL-77 writer-flow LAST: the cold-start backgrounder rides AFTER the
        # ledger as its own labeled section (never merged into it). It is context
        # only; any continuity word drawn from it must carry the dated baseline
        # cite (the block says so inline; the diction validator enforces it).
        if s.get("thread_baseline"):
            lines.append(s["thread_baseline"])
        # NL-75 the accountability loop: watch-fors this edition PROMISED whose
        # date has passed. Each MUST convert — never re-shipped, never dropped.
        for w in s.get("expired_watch", []):
            due = w.get("due_date") or "(no parseable date)"
            lines.append(
                f"EXPIRED WATCH-FOR you flagged on {w.get('edition_date')} "
                f"(due {due}, now past): \"{w.get('observable', '')}\"\n"
                "  You promised the reader to watch this; the date has passed. "
                "CONVERT it in this story — exactly ONE of: RESOLVED (report the "
                "outcome the record or today's sources now hold), UNANSWERED "
                "(say plainly that today's sources are silent on it — the "
                "silence is itself the content), or SUPERSEDED (name what "
                "overtook it). NEVER re-ship it as a fresh forward-looking "
                "watch-for; NEVER drop it silently.")
        lines.append(f"corroboration: {s.get('corroboration_label', '')}")
        if s.get("corroboration_count") == 1:
            outlets = s.get("outlets") or []
            lines.append(
                f"SINGLE-OUTLET STORY — name the outlet in the lede prose: "
                f"{outlets[0] if outlets else 'the sole outlet'}"
            )
        if s.get("override") and first_edition:
            # NL-134 F2(a): the cold-start arm. Two specimens on record wrote a
            # reader-history claim into a FIRST briefing (fresh1 2026-08-02;
            # persona public-health 2026-07-28), both opening on the phrase the
            # prompt itself supplied. The acknowledgement is not banned — it is
            # banned WHEN THERE IS NOTHING TO ACKNOWLEDGE.
            lines.append(
                "OVERRIDE STORY — outside the reader's tags. THIS IS THE "
                "READER'S FIRST BRIEFING: they have no reading history with "
                "you, so there is no 'usual' for this story to be off and no "
                "established interests to contrast it against. Introduce it on "
                "its own world-impact merits. Make NO claim about what the "
                "reader normally reads, follows, tracks or expects — on a first "
                "briefing every such claim is false (the pipeline renders its "
                "own label)"
            )
        elif s.get("override"):
            lines.append(
                "OVERRIDE STORY — outside the reader's tags (the pipeline "
                "renders its own label; your lede may acknowledge naturally, "
                "in your own words — no supplied phrasing to copy)"
            )
        for rv in s.get("revived_threads", []):
            if rv.get("last_covered"):
                lines.append(
                    f"REVIVAL (mandatory disclosure): thread {rv['topic']!r} — "
                    f"the lede's first two sentences MUST contain 'last covered "
                    f"{rv['last_covered']}' (date exactly as written here), a "
                    "one-clause prior summary, and what's new"
                )
        slot_no = int(n)
        brief_doc = (inputs.get("briefs_by_slot") or {}).get(slot_no)
        if brief_doc and brief_doc.get("brief"):
            # M9-M3: trace, don't generate (content §5.6 migration). The
            # brief's cited ledger IS this story's report lane; the two-lane
            # rule as amended admits retrieved-and-cited material to your
            # grounding. You introduce no analytic specific absent from the
            # brief or the cluster titles; effects only with the brief's
            # basis + holder; never a forward claim absent from it.
            from . import analysis as analysis_mod
            lines.append(
                "ANALYSIS BRIEF — your REPORT lane for this story. TRACE, "
                "DON'T GENERATE: every analytic specific you write traces to "
                "this brief or the cluster items; copy effects with their "
                "basis and holder, never generate your own; what the brief "
                "lists as unknown stays unknown:")
            lines.append(analysis_mod.render_writer_view(brief_doc["brief"]))
            lines.append("cluster items (context only — the brief above is "
                         "the ledger):")
            for it in inputs["items_by_slot"].get(n, []):
                lines.append(f"  * [{it['outlet']}] {it['title']}")
        else:
            # NL-151b: keyed on the story's TIER, not on `slot_no <= 3`. This
            # line is the DEPTH tier's apology — it exists because a
            # full-picture story with no brief is a degraded one. A quick-tier
            # In-Brief story has never carried it and must not start now: under
            # arm (ii) the demoted story is not a degraded depth story at all,
            # it is an ordinary In-Brief story, and telling the writer
            # otherwise would re-import the degradation L1 just removed.
            is_depth = tier in ("full", "medium")
            if is_depth and not (inputs.get("briefs_by_slot") or {}):
                pass  # whole stage absent: run-level warning already covers it
            elif is_depth:
                lines.append(
                    "(analysis unavailable for this story — the excerpts "
                    "below are the report lane; disclosed in the meta line)")
            lines.append("source items (your REPORT lane for this story):")
            for it in inputs["items_by_slot"].get(n, []):
                excerpt = (it["raw_excerpt"] or "").strip()[:700]
                lines.append(f"  * [{it['outlet']}] {it['title']}")
                if excerpt:
                    lines.append(f"    excerpt: {excerpt}")
        story_parts.append("\n".join(lines))

    weekday, human = _spoken_date(date)
    return template.format(
        date_line=f"{weekday}, {human}",
        tags_block="\n".join(tag_lines),
        threads_block="\n".join(thread_lines) or "(none)",
        prior_block=prior_block,
        stories_block="\n\n".join(story_parts),
    )


# ---------------------------------------------------------------------------
# Narrative validation + assembly (code owns the furniture)
# ---------------------------------------------------------------------------

def _outlet_token(outlet: str) -> str:
    """First significant token of an outlet display name, lowercased —
    "BBC News — World" -> "bbc"; "The Hill" -> "hill" (gate ride: a leading
    article is never the name a writer uses)."""
    for tok in re.split(r"[\s—-]+", outlet):
        if tok.lower() in ("the", "a", "an"):
            continue
        if len(tok) > 2 or tok.isupper():
            return tok.lower()
    return outlet.lower()


def _scan_banned(text: str) -> List[str]:
    low = text.lower()
    return [b for b in BANNED_STRINGS if b in low]


def validate_narrative_payload(
    payload: object, slots: List[Dict], variant: str,
    depth_tiers: Optional[List[str]] = None,
) -> Tuple[List[Dict], List[str]]:
    """Structural checks BLOCK (retry-then-fail); style checks warn.
    Mandatory disclosures (revival dates) block.

    `depth_tiers` (NL-151b) is the per-position tier vector the pipeline
    briefed the writer for. It stays OPTIONAL and its absence reproduces the
    A2 positional gate exactly — that is not politeness to old callers, it is
    the correct answer for every caller that genuinely has no vector (samples,
    `--no-refresh` on an unanalysed date, the fixtures in twenty-eight test
    files). What it must never become is a SUGGESTION: given a vector, the
    gate is that vector, and a writer returning the positional tiers on a
    demotion morning is returning a full-picture story built on no full text."""
    if not isinstance(payload, dict) or not isinstance(payload.get("stories"), list):
        raise ValueError("payload must be a JSON object with a `stories` list")
    stories = payload["stories"]
    if len(stories) != len(slots):
        raise ValueError(
            f"{len(stories)} stories returned for {len(slots)} slots — must match"
        )
    warnings: List[str] = []
    clean: List[Dict] = []
    for i, (s, slot) in enumerate(zip(stories, slots)):
        n = slot["slot"]
        if not isinstance(s, dict):
            raise ValueError(f"story {n}: not an object")
        tier = s.get("tier")
        # A2 tier positions — NL-63 M2 AMENDED slot contract: EXACTLY 3 full-
        # picture stories at positions 1-3 (1 lead "full" + 2 "medium"), every
        # remaining story is "quick" (the In-Brief register). Slot 3 is pinned to
        # "medium" — the old analyst medium-vs-quick demotion is RETIRED, because
        # a demoted slot 3 would leave only 2 full-picture stories, violating the
        # exactly-3 ruling. Code enforces every position now; the model proposes
        # no tier of its own.
        #
        # NL-151b: still exactly three full-picture stories, still code-owned,
        # still no model-proposed tier — the vector says WHICH positions hold
        # them when a prioritized fetch died. Without a vector this expression
        # is the positional tuple it has always been.
        allowed = (
            (depth_tiers[i],) if depth_tiers and i < len(depth_tiers) else
            ("full",) if i == 0 else
            ("medium",) if i in (1, 2) else
            ("quick",)
        )
        if tier not in allowed:
            raise ValueError(
                f"story {n}: tier {tier!r} not allowed at this position "
                f"(expected one of {allowed})"
            )
        out = {"tier": tier}
        # NL-63 M2: EVERY tier is now a structured story — the amended "In Brief"
        # (quick) register carries the depth stories 2-3 had before (lede +
        # why_it_matters + watch_for), NOT the dead <=60-word headline snippet.
        required = ("headline", "lede", "why_it_matters", "watch_for")
        for fld in required:
            v = s.get(fld)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"story {n}: {fld} missing/empty (tier {tier})")
            out[fld] = v.strip()
        # A7: declared framings, menu-membership enforced — on every tier now
        # (In Brief stories frame their movements from the menu like the rest).
        wl = s.get("why_label")
        if wl not in WHY_FRAMINGS:
            raise ValueError(
                f"story {n}: why_label {wl!r} not in the sanctioned menu"
            )
        xl = s.get("watch_label")
        if xl not in WATCH_FRAMINGS:
            raise ValueError(
                f"story {n}: watch_label {xl!r} not in the sanctioned menu"
            )
        out["why_label"], out["watch_label"] = wl, xl
        my_read = s.get("my_read")
        if variant == "A":
            if isinstance(my_read, str) and my_read.strip():
                raise ValueError(f"story {n}: variant A must not carry my_read")
            out["my_read"] = None
        else:
            if isinstance(my_read, str) and my_read.strip():
                # Code owns the label (§5.7): strip a model-written "My read:"
                # prefix so assembly never doubles it (M5 live finding).
                out["my_read"] = re.sub(
                    r"^\s*my read:\s*", "", my_read.strip(), flags=re.I
                ) or None
            else:
                out["my_read"] = None
        if len(_WORD_RE.findall(out["headline"])) > 14:
            warnings.append(f"story {n}: headline over the 12-word band")
        # Mandatory revival disclosure: date verbatim in the lede's opening.
        for rv in slot.get("revived_threads", []):
            date_needed = rv.get("last_covered")
            if date_needed:
                first_two = " ".join(re.split(r"(?<=[.!?])\s+", out["lede"])[:2])
                if date_needed not in first_two:
                    raise ValueError(
                        f"story {n}: revival date {date_needed!r} missing from "
                        "the lede's first two sentences (mandatory disclosure)"
                    )
        # Single-source: outlet named in lede prose (writer-owned warning).
        # Token-level match: display names like "BBC News — World" are
        # legitimately spoken as "the BBC" (M5 live finding).
        if slot.get("corroboration_count") == 1 and slot.get("outlets"):
            if _outlet_token(slot["outlets"][0]) not in out["lede"].lower():
                warnings.append(
                    f"story {n}: single-outlet story should name "
                    f"{slot['outlets'][0]!r} in the lede prose"
                )
        text_blob = " ".join(v for v in out.values() if isinstance(v, str))
        hits = _scan_banned(text_blob)
        if hits:
            warnings.append(f"story {n}: banned strings present: {hits}")
        low = text_blob.lower()
        truisms = [x for x in TRUISM_WARN_STRINGS if x in low]
        if truisms:
            warnings.append(f"story {n}: truism-class phrases (A3): {truisms}")
        moralize = [x for x in MORALIZE_WARN_STRINGS if x in low]
        if moralize:
            warnings.append(
                f"story {n}: moralization-class words in own voice? (A3, "
                f"quotes are fine): {moralize}"
            )
        clean.append(out)
    # A7 rhythm warn: five stories must never share one framing.
    why_labels = [c.get("why_label") for c in clean if c.get("why_label")]
    if len(why_labels) >= 3 and len(set(why_labels)) == 1:
        warnings.append(
            f"all {len(why_labels)} movement stories share one framing "
            f"({why_labels[0]!r}) — A7 wants varied rhythm [warn-only]"
        )
    # A8 lead-depth pressure: a lead near full-picture (slot-2) length is a flag.
    # NL-63 M2: full-picture stories now run ~440 words, so the flag threshold
    # rises with them — a doubled lead should clear ~440 comfortably.
    if clean and clean[0].get("tier") == "full":
        lead_words = len(_WORD_RE.findall(
            " ".join(v for v in clean[0].values() if isinstance(v, str))))
        if lead_words <= 440:
            warnings.append(
                f"lead landed at {lead_words} words — near full-picture length; "
                "A8 wants the lead's why-movement built from source specifics"
            )
    return clean, warnings


# ---------------------------------------------------------------------------
# NL-75 THE FORWARD-CLAIM RULES (Content council 2026-07-16) — generation-side
# writer/editor validation. Warn-grade and SURFACED (report.warnings ->
# generation_log -> diagnose): a visible, handled error path, not a silent
# no-op and not a dead run on a heuristic. The primary defense is the prompt
# steer; these are the safety net. Escalation to block-with-informed-retry
# (Content rule i) is flagged for the gate as a severity decision.
# ---------------------------------------------------------------------------

# Continuity/repetition diction (Content rule iii) — _REPETITION_RE and the
# subject-scoping _repetition_subject_units now live in memory_core beside
# has_predating_antecedent (single source; the 0014 write-side self-mark shares
# them) and are imported at the top of this module. generate._REPETITION_RE
# still resolves via that import.

# D3a (NL-75 QA): attribution is a FRAME (a verb/phrase that hands the word to a
# source), NOT a bare quote byte. 32% of real edition prose carries a possessive
# apostrophe, so a bare "'" laundered unattributed repetition words past rule
# iii; the possessive alone must not attribute. A quote character counts only
# when the repetition word itself sits INSIDE a quoted span (see _is_source_
# attributed / _match_in_quoted_span).
_ATTRIB_MARKERS = (
    "said", "says", "according to", "reported", "reports", "call it",
    "calls it", "called it", "described", "per ", "cited",
    "claim", "announced", "warned", "warns", "told",
)
_QUOTES = "\"'“”‘’"


def _match_in_quoted_span(sentence: str, start: int, end: int) -> bool:
    """The repetition word (sentence[start:end]) sits inside a quoted span: an
    OPENING quote before it (a quote not preceded by an alnum — so a possessive
    apostrophe in "Tehran's" never opens a span) and a CLOSING quote after it (a
    quote not followed by an alnum). Distinguishes a quoted word from a bare
    possessive. Boundary: a stray opening span earlier in the sentence plus a
    stray closing span later can read as enclosing — warn-grade output, and
    over-attributing a genuinely quoted-heavy sentence is the safe direction."""
    def is_open(i: int) -> bool:
        return sentence[i] in _QUOTES and (i == 0 or not sentence[i - 1].isalnum())

    def is_close(i: int) -> bool:
        return sentence[i] in _QUOTES and (
            i == len(sentence) - 1 or not sentence[i + 1].isalnum())

    has_open = any(is_open(i) for i in range(0, start))
    has_close = any(is_close(i) for i in range(end, len(sentence)))
    return has_open and has_close


def _is_source_attributed(sentence: str, match: Optional[re.Match] = None) -> bool:
    """A repetition word is legal when carried by a source (Content rule iii's
    middle state): in an attribution FRAME ("today's reports call it 'X'",
    "according to", "per ") OR with the word itself sitting inside a quoted span
    ("a step reports call \"reinstated\""). A bare possessive apostrophe is
    neither (D3a)."""
    low = sentence.lower()
    if any(m in low for m in _ATTRIB_MARKERS):
        return True
    if match is not None and _match_in_quoted_span(
            sentence, match.start(), match.end()):
        return True
    return False


# D6 (NL-75 QA, the HIGH one — HSR §5.1(2)): the antecedent SUBJECT must
# discriminate the repetition's OBJECT, not echo the whole sentence.
# _repetition_subject_units (imported from memory_core at the top of this
# module) scopes it to a bounded window AFTER the match, minus the thread
# topic's own words; a thread-topic word alone must never license.


def repetition_antecedent_findings(con, stories: List[Dict], slots: List[Dict],
                                   edition_date: str) -> List[str]:
    """Content rule iii, poisoned-antecedent hardened. A repetition word is
    licensed only by a PREDATING ledger antecedent (a same-day backfill row
    citing edition-day sources does NOT count — the antecedent must predate the
    edition) OR by explicit source attribution. Neither → the 'reinstated'
    class, flagged."""
    from . import memory_core as mc
    out: List[str] = []
    for story, slot in zip(stories, slots):
        if not isinstance(story, dict):
            continue
        topics = [t for t in (slot.get("matched_memory") or []) if t]
        blob = " ".join(story.get(f, "") for f in ("headline", "lede", "why_it_matters")
                        if isinstance(story.get(f), str))
        for sent in re.split(r"(?<=[.!?])\s+", blob):
            m = _REPETITION_RE.search(sent)
            if not m:
                continue
            units = _repetition_subject_units(sent, m, topics)
            # D6-R (QA re-verify, fix loop 1): an EMPTY subject set must never
            # license — with no discriminating units, has_predating_antecedent
            # falls through to any-prior-history ("The strait is back on." on
            # a thread with unrelated priors). Conservative direction: this is
            # warn-grade surface, so the false positive costs a warning; the
            # false negative ships unearned diction.
            licensed = bool(units) and any(
                mc.has_predating_antecedent(con, t, units, edition_date)
                for t in topics)
            # NL-77 (D1/D2): a baseline-derived continuity word is licensed ONLY
            # by a dated baseline cite backed by an ACTUAL ready baseline on a
            # matched thread whose as_of the cited date matches — the cite is a
            # currency, not a spelling. A counterfeit '(baseline, Jul 14)' (no
            # issuing baseline, no matched thread, or a mismatched date) licenses
            # nothing and stays flagged (never bare).
            if (licensed or _is_source_attributed(sent, m)
                    or mc.licensing_baseline_cite(con, topics, sent, edition_date)):
                continue
            out.append(
                f"story {slot.get('slot')}: repetition word {m.group(0)!r} has "
                "no predating ledger antecedent and is not source-attributed — "
                "the 'reinstated' class (Content rule iii). Ship the record's "
                "date in the sentence, attribute the word to a source, or cut it.")
    return out


# ---------------------------------------------------------------------------
# SPOKEN continuity — the script lane's net (Stage-0 M2; M0 QA finding F2)
#
# Distinct from _REPETITION_RE, and deliberately so. That vocabulary is about
# the WORLD repeating ("reinstated", "resumed", "again"); this one is about the
# SHOW claiming to have said something before ("as we covered", "we've been
# tracking", "regular listeners will remember"). A day-one episode can carry
# the second with none of the first, which is exactly what F2 found: the
# narrative-side nets never see script text, validate_script had no continuity
# check at all, and a first-ever episode saying "As we covered last week"
# shipped with zero hard problems and zero warnings.
#
# Why this is warn-grade and not hard: a false positive costs a line in the run
# record, a false negative ships a fabricated relationship with the listener —
# the one thing a memory product cannot be caught doing. Conservative direction
# is to fire.
# ---------------------------------------------------------------------------
_SCRIPT_CONTINUITY_RE = re.compile(
    r"("
    # the show's own prior coverage, first person
    r"as (?:we|i) (?:covered|reported|discussed|noted|mentioned|said|flagged|told you)"
    r"|(?:we|i)(?:'ve|'d| have| had)? been (?:tracking|following|covering|watching|reporting on)"
    r"|(?:we|i) (?:first |last )?(?:covered|reported on|told you|flagged) "
    r"|(?:we|i) (?:keep|kept) (?:coming back|returning) to"
    # ordinal arc claims ("the third week we've tracked this")
    r"|(?:second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r"|\d+(?:st|nd|rd|th)) (?:week|day|month|time) (?:we|i)(?:'ve| have)?\s*"
    r"(?:tracked|covered|followed|reported)"
    # audience-memory claims
    r"|(?:regular|longtime|long-time) listeners"
    r"|(?:you'll|you will|you may) (?:remember|recall)"
    r"|(?:as|like) (?:you|listeners) (?:may |might )?(?:recall|remember)"
    # explicit prior-edition references
    r"|(?:last|previous) (?:week|month|time),? (?:we|i)\b"
    r"|(?:yesterday|last week)'s (?:episode|briefing|edition)"
    r")", re.I)


# The show talking about itself, or addressing its audience's memory. Used to
# withhold the source-attribution exemption: "we"/"our"/"listeners"/"you'll
# remember" name the SPEAKER and the LISTENER, never a source.
#
# Gate F4 (2026-07-25): bare `you` closes an ESCAPE. The contracted forms alone
# left "As you recall, officials said…" self-referential-negative, so the
# attribution marker ("said") exempted a claim the SHOW was making about the
# listener's memory — the exact fabrication class this net exists to catch.
# Blast radius is scoped by construction: this RE is searched against
# `m.group(0)`, i.e. only inside phrases _SCRIPT_CONTINUITY_RE already matched,
# never the whole sentence — a source's own "you" cannot reach it. The
# `you'll|you will|you may` forms are now subsumed but stay: they name the
# shapes on record, and the alternation's truth value is order-independent.
_SELF_REFERENTIAL_RE = re.compile(r"\b(we|i|our|listeners|you'll|you will"
                                  r"|you may|you)\b"
                                  r"|\bwe['’]", re.I)


def _has_real_prior_coverage(inputs: Dict) -> bool:
    """Does this edition have prior coverage the show can honestly point at?

    TWO conditions, and both are load-bearing:
      * a readable PRIOR BRIEFING exists (continuity_status 'ok' — 'corrupt'
        deliberately does not count: continuity is suspended for that run, the
        same call build_narrative_prompt makes), and
      * some story on this edition carries REAL thread history — a dated ledger
        delta predating today (thread_ledger, built strictly before_date), or a
        revived thread, which by definition was covered before.

    The second condition is why "we have shipped an edition before" is not
    enough: on edition 2, "the third week we've tracked this" is still a
    fabrication. This is the same predicate the prompt's callback license uses
    — one law, evaluated in one place, so the thing the model is invited to do
    and the thing the validator permits can never drift apart.
    """
    if inputs.get("continuity_status") != "ok":
        return False
    return any(s.get("thread_ledger") or s.get("revived_threads")
               for s in inputs.get("slots", []))


def _demanded_revival_dates(inputs: Dict) -> List[str]:
    """Every spoken form of a revival date this run REQUIRES the script to
    voice. validate_script warns when one is missing, so the continuity net
    must never flag one for being present — a validator that fights a mandatory
    disclosure is worse than no validator."""
    out: List[str] = []
    for s in inputs.get("slots", []):
        for rv in (s.get("revived_threads") or []):
            if rv.get("last_covered"):
                out.extend(_date_spoken_forms(rv["last_covered"]))
    return [f.lower() for f in out]


def script_continuity_findings(con, script: str, inputs: Dict,
                               edition_date: str) -> List[str]:
    """Unsupported SPOKEN continuity claims, named one per claim.

    Returns warn-grade findings for the run record. `con` is accepted for
    symmetry with the narrative-side nets (and so a future rung can consult the
    ledger directly); the licensing evidence it would read is already carried
    on `inputs` by load_briefing_inputs, which built it from that same
    connection strictly before this edition's date.
    """
    if _has_real_prior_coverage(inputs):
        return []                      # the callback is licensed; say nothing
    demanded = _demanded_revival_dates(inputs)
    out: List[str] = []
    seen: set = set()
    for sent in re.split(r"(?<=[.!?])\s+", script or ""):
        m = _SCRIPT_CONTINUITY_RE.search(sent)
        if not m:
            continue
        low = sent.lower()
        # A claim handed to a SOURCE is the source's, not the show's — the
        # exemption repetition_antecedent_findings already grants.
        #
        # But it must not apply to a claim the show makes about ITSELF. The
        # attribution-marker vocabulary contains "reported" and "told", so
        # "As we reported on Tuesday" and "We told you this would come back"
        # both read as source-attributed to that helper — and those are
        # exactly the fabrications this net exists to catch. A first-person or
        # audience-addressed claim is never attributable to a source: the
        # show is the speaker, and the speaker cannot be its own citation.
        if not _SELF_REFERENTIAL_RE.search(m.group(0)) \
                and _is_source_attributed(sent, m):
            continue
        # A mandatory dated revival disclosure is never a finding.
        if any(d in low for d in demanded):
            continue
        phrase = " ".join(m.group(0).split())
        if phrase.lower() in seen:
            continue
        seen.add(phrase.lower())
        out.append(
            f"spoken continuity claim {phrase!r} has no prior coverage on "
            "record — this edition has no readable prior briefing carrying "
            "thread history, so the episode is claiming a relationship with "
            "the listener that does not exist. Cut it, or attribute it to a "
            "source (M0 finding F2).")
    return out


_BASELINE_REF_RE = re.compile(r"\bbaseline\b", re.I)


def baseline_diction_findings(con, stories: List[Dict], slots: List[Dict],
                              edition_date: str) -> List[str]:
    """NL-77 the dated-anchored diction validator (the writer-side rule migration
    0014 deferred; sequencing law item 2). It closes the ONE gap the generic
    repetition net (repetition_antecedent_findings) leaves on a baselined thread:
    a continuity word GESTURED at the baseline without dating it ('per the
    baseline, reinstated ...') reads as source-attributed and slips the generic
    net — but a baseline licenses continuity diction ONLY dated-anchored, so this
    is exactly the poison to refuse. (A pure-bare word with no baseline reference
    is already flagged by the generic net — not re-flagged here, to avoid
    double-surfacing.) Fires only on threads carrying a ready baseline as-of the
    edition; degrades to no findings on a pre-0017 DB."""
    from . import memory_core as mc
    out: List[str] = []
    for story, slot in zip(stories, slots):
        if not isinstance(story, dict):
            continue
        topics = [t for t in (slot.get("matched_memory") or []) if t]
        baselined = []                          # (topic, as_of) — the issuing floors
        for t in topics:
            tid = mc.resolve_thread_id(con, t)
            if tid is not None:
                b = mc.ready_baseline(con, tid, before_date=edition_date)
                if b:
                    baselined.append((t, b["as_of_date"]))
        if not baselined:
            continue
        blob = " ".join(story.get(f, "") for f in ("headline", "lede", "why_it_matters")
                        if isinstance(story.get(f), str))
        for sent in re.split(r"(?<=[.!?])\s+", blob):
            m = _REPETITION_RE.search(sent)
            if not m:
                continue
            # A VALID dated baseline cite (currency, not spelling) is the licensed
            # form — no finding.
            if mc.licensing_baseline_cite(con, topics, sent, edition_date):
                continue
            # Only the baseline-GESTURE case is this rule's gap.
            if not _BASELINE_REF_RE.search(sent):
                continue
            # A word with a real predating ledger antecedent on ANY matched thread
            # (D4: all topics, not just the baselined ones) is licensed the
            # ordinary way — not a baseline claim.
            units = _repetition_subject_units(sent, m, topics)
            if bool(units) and any(
                    mc.has_predating_antecedent(con, t, units, edition_date)
                    for t in topics):
                continue
            # D4 no-double-surfacing: fire ONLY when the generic net is SILENT on
            # this word — i.e. it is SOURCE-ATTRIBUTED (the evasion 'per the
            # baseline, reinstated' the generic net lets through). A pure-bare
            # word (incl. one in a sentence using 'baseline' in its ordinary
            # sense) is the generic net's job and is flagged exactly once there.
            if not _is_source_attributed(sent, m):
                continue
            names = ", ".join(t for t, _ in baselined)
            as_of = baselined[0][1]
            out.append(
                f"story {slot.get('slot')}: continuity word {m.group(0)!r} on a "
                f"cold-start thread ({names}) is attributed to the baseline but "
                "NOT dated-anchored — a baseline licenses continuity diction only "
                f"inside the dated cite '{mc.baseline_cite(as_of)}', never bare "
                "(NL-77 dated-anchored-never-bare).")
    return out


def future_relative_watch_findings(stories: List[Dict], slots: List[Dict],
                                   edition_date: str) -> List[str]:
    """Content rule i: date-bearing forward material must resolve STRICTLY later
    than the edition. A watch-for naming a date on-or-before the edition is the
    stale-July-12 class (the render guard from v7.2 stays as a backstop; this
    catches it at generation)."""
    from . import memory_core as mc
    out: List[str] = []
    year = (edition_date or "")[:4]
    for story, slot in zip(stories, slots):
        if not isinstance(story, dict):
            continue
        wf = story.get("watch_for") or ""
        dates = set()
        for m in mc._ISO_RE.finditer(wf):
            dates.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
        if year.isdigit():
            for dm in mc._MONTH_DAY_RE.finditer(wf):
                mon = mc._MONTH_NUM[dm.group(1).lower()]
                dates.add(f"{year}-{mon:02d}-{int(dm.group(2)):02d}")
        for d in sorted(d for d in dates if d <= edition_date):
            out.append(
                f"story {slot.get('slot')}: watch-for names {mc.human_date(d)} "
                f"({d}) — not future-relative to the {edition_date} edition "
                "(Content rule i, the stale-watch-for class). A watch-for points "
                "strictly forward; convert or drop this, never re-ship it.")
    return out


# D5 (NL-75 QA): the conversion check runs against the story BODY only. An
# expired watch-for is NEVER re-shipped; a re-ship whose only reference sits in
# `watch_for` (dateless — the evasion clause rule i cannot grep) would otherwise
# make the observable 'referenced' and classify 'resolved', closing the very
# debt the edition just re-incurred. Body = headline + lede + why_it_matters;
# the register write path (run_generate) uses the SAME body-only prose.
_STORY_BODY_FIELDS = ("headline", "lede", "why_it_matters")


def _story_body_prose(story: Dict) -> str:
    return " ".join(story.get(f, "") for f in _STORY_BODY_FIELDS
                    if isinstance(story.get(f), str))


def expiry_conversion_findings(stories: List[Dict], slots: List[Dict]) -> List[str]:
    """Content rule ii: an EXPIRED watch-for (a promise whose date has passed,
    carried on the slot as `expired_watch`) must CONVERT in this edition —
    RESOLVED / UNANSWERED / SUPERSEDED — never silently dropped. Flags an
    expired item the story's BODY does not address (a reference that lives only
    in `watch_for` is a re-ship, D5 — flagged here, never a conversion)."""
    from . import memory_core as mc
    out: List[str] = []
    for story, slot in zip(stories, slots):
        if not isinstance(story, dict):
            continue
        expired = slot.get("expired_watch") or []
        if not expired:
            continue
        prose = _story_body_prose(story)
        for w in expired:
            if mc.classify_conversion(w.get("observable", ""), prose) is None:
                obs = (w.get("observable", "") or "")[:60]
                out.append(
                    f"story {slot.get('slot')}: expired watch-for \"{obs}...\" "
                    f"(due {w.get('due_date')}) was NOT converted — the story "
                    "neither reports its outcome, notes the sources are silent, "
                    "nor says what superseded it (Content rule ii: never "
                    "silently dropped, never re-shipped).")
    return out


def forward_claim_findings(con, stories: List[Dict], slots: List[Dict],
                           edition_date: str) -> List[str]:
    """The three Forward-Claim Rules, run generation-side over the edited
    stories. Returns surfaced warnings."""
    out: List[str] = []
    out.extend(repetition_antecedent_findings(con, stories, slots, edition_date))
    out.extend(baseline_diction_findings(con, stories, slots, edition_date))
    out.extend(future_relative_watch_findings(stories, slots, edition_date))
    out.extend(expiry_conversion_findings(stories, slots))
    return out


# ---------------------------------------------------------------------------
# M1/M2 — editor-preservation (editor-preservation batch, 2026-07-21). The
# deterministic matcher lives in memory_core (ledger_callbacks); these are the
# generate-side seams: build the predating ledger context, render the PROTECT
# block the editor is TOLD to keep (belt), and — in the degrade seam — enforce
# it by the post-edit diff (suspenders).
# ---------------------------------------------------------------------------

def _ledger_callback_context(con, slots: List[Dict],
                             edition_date: str) -> List[Dict]:
    """Per-story ledger context for mc.ledger_callbacks: {"topics", "rows"} for
    each slot, in slot order. Rows are the thread's PREDATING deltas (the exact
    NL-75 antecedent surface — ledger_for_thread with before_date, superseded
    rows dropped) plus the newest predating standing state, each normalized to
    {date, text, provenance, kind}. No fresh query with different cutoff
    semantics is introduced (dispatch guardrail)."""
    from . import memory_core as mc
    ctx: List[Dict] = []
    for slot in slots:
        topics = [t for t in (slot.get("matched_memory") or []) if t]
        rows: List[Dict] = []
        for topic in topics:
            tid = mc.resolve_thread_id(con, topic)
            if tid is None:
                continue
            for e in mc.ledger_for_thread(con, tid, before_date=edition_date):
                if e.get("superseded_by"):
                    continue          # Rook's gate: a corrected delta anchors nothing
                rows.append({
                    "date": e.get("edition_date"),
                    "text": f"{e.get('what_happened', '')} {e.get('significance', '')}",
                    "provenance": e.get("provenance"),   # None => record-established
                    "kind": "delta",
                })
            st = mc.latest_state(con, tid, before_date=edition_date, strict=True)
            if st:
                rows.append({
                    "date": st.get("as_of_date"),
                    "text": st.get("state_text", ""),
                    "provenance": None,     # thread_state is untyped => record-grade
                    "kind": "state",
                })
        ctx.append({"topics": topics, "rows": rows})
    return ctx


def _render_protect_block(callback_tags: List) -> str:
    """The editor-facing PROTECT list (belt). One line per pinned dated callback,
    fact-level (date + subject), never the verbatim sentence — demanding verbatim
    retention would rebuild the 'stamp in prose clothing' the arc-line contract
    killed (Vera's constraint); the editor keeps the FACT and rewords at will."""
    from . import memory_core as mc
    prot = [t for t in callback_tags if t.tag == "PROTECT"]
    if not prot:
        return ("(no dated ledger callbacks in this draft — nothing pinned; edit "
                "under your ordinary license)")
    lines: List[str] = []
    for t in prot:
        subj = ", ".join(t.subject_units)
        lines.append(
            f"  - story {t.story_index + 1}: KEEP the {mc.human_date(t.date)} "
            f"({t.date}) accountability reference to [{subj}] — date AND subject "
            "must both survive, in any wording.")
    return "\n".join(lines)


def selection_names(slot: Dict) -> List[str]:
    """The followed things this slot matched — tag names first, then tracked
    threads; order-preserving, case-insensitively deduped, empties dropped.

    THE NL-68 DEDUPE LAW, and now its only implementation. The exhibit is a tag
    and a tracked thread of the SAME name doubling the line ("Strait of Hormuz,
    Strait of Hormuz"); the law says that must stay dead on every surface that
    merges the two lists.

    MOVED HERE from server._selection_names by NL-165 ① (2026-08-27) because the
    law was only half-kept: the web lane called it, the markdown lane composed
    the same merge by hand and never deduped, so the doubling shipped in every
    edition that had a twin — data/briefings/2026-08-26.md line 43 and
    2026-08-24.md line 32 are the specimens the batch was chartered on. Behaviour
    is byte-identical to the server code this replaces; only the address changed,
    and it changed DOWNWARD in the import graph (server imports generate, never
    the reverse) so both lanes and moat_battery can reach one function instead of
    keeping three copies honest by memory.

    Not to be confused with server._reason_classes, which is deliberately a
    DIFFERENT resolution of the same exhibit: it keeps the classes apart and lets
    the THREAD win the collision, because the reason line states the class in
    words and the thread fill carries the delta obligation (2026-07-31 round §6).
    Both kill the doubling; only the surviving class differs."""
    ordered: List[str] = []
    seen: set = set()
    tag_names = [t.get("name", "") for t in slot.get("matched_tags") or []
                 if isinstance(t, dict)]
    for name in tag_names + list(slot.get("matched_memory") or []):
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


def here_for_text(slot: Dict) -> str:
    """The 'Here for' rationale — CODE-OWNED, from the slot (never prose):
    matched tags + tracked threads through the dedupe law, else the editor's
    override, else the world-impact fallback.

    ONE composition, read by the §5.7 markdown meta line below and by
    moat_battery's prose-first colophon. Those were separate literal copies
    until NL-165 ①; the battery's T1 arm compares prose against sectioned and
    calls the colophon furniture that "rides both forms, byte-identical", which
    a second copy can only keep true by luck."""
    matches = ", ".join(selection_names(slot))
    if matches:
        return matches
    if slot.get("override"):
        return "editor's override — see note above"
    return "world-impact selection (no tag or thread match)"


def assemble_narrative(
    date: str, variant: str, stories: List[Dict], inputs: Dict
) -> str:
    weekday, human = _spoken_date(date)
    slots = inputs["slots"]
    parts = [f"# NewsLens — {weekday}, {human}", "", "In today's briefing:"]
    parts += [f"- {st['headline']}" for st in stories]
    parts.append("")

    for st, slot in zip(stories, slots):
        parts.append("---")
        if slot.get("override"):
            # NL-138: the constant IS the line now (no .format) — see the
            # frozen-surface note on OVERRIDE_TEXT_LABEL.
            parts.append(OVERRIDE_TEXT_LABEL)
            parts.append("")
        parts.append(f"**{st['headline']}**")
        parts.append("")
        parts.append(st["lede"])
        parts.append("")
        # NL-63 M2: EVERY tier is a structured story now — the In Brief (quick)
        # register carries its why/watch movements like the rest (the dead
        # bare-lede snippet is gone). Guard on the field's presence so an
        # archived edition's old headline-only quick story still renders clean.
        if st.get("why_it_matters"):
            why_label = st.get("why_label") or "Why it matters"
            parts.append(f"**{why_label}:** {st['why_it_matters']}")
            if st.get("my_read"):
                parts.append("")
                parts.append(f"**My read:** {st['my_read']}")
            parts.append("")
            if st.get("watch_for"):
                watch_label = st.get("watch_label") or "Watch for"
                parts.append(f"**{watch_label}:** {st['watch_for']}")
                parts.append("")
        # NL-165 ①: this merge used to be composed here, RAW — no dedupe — so a
        # tag and a tracked thread of the same name doubled the name on this
        # lane every edition while the web lane deduped. One call now, through
        # the law itself; the no-match/override branches it also carries are the
        # cold-start fix (no-match is not the same as override — never point at
        # a label that isn't there) and are unchanged.
        here_for = here_for_text(slot)
        meta_line = slot.get("corroboration_label", "")
        outlets = slot.get("outlets") or []
        outlet_names = f" — {', '.join(outlets)}" if outlets else ""
        # M9-M3 ladder label (content §5.7): a depth story built without a
        # valid brief says so in its own trailing meta — reader-facing UI
        # shows nothing (degraded-hidden == absent, Axel's ruling), the
        # artifact carries the honest label.
        a_note = ""
        deep_views = inputs.get("deep_views") or {}
        slot_no = str(slot.get("slot", ""))
        if st.get("tier") in ("full", "medium") and deep_views \
                and deep_views.get(slot_no) not in ("available", None):
            a_note = " Analysis: unavailable — built from feed excerpts."
        parts.append(f"*{meta_line}{outlet_names}. Here for: {here_for}.{a_note}*")
        parts.append("")

    # Footer block — fixed order, deterministic (§5.7).
    parts.append("---")
    wm = inputs.get("window_meta") or {}
    window = (wm.get("window") or {}) if isinstance(wm, dict) else {}
    start = (window.get("start_iso") or "window-start unavailable")[:16]
    end = (wm.get("ran_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"))[:16]
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append("*" + WINDOW_LINE.format(timestamp=now_ts, start=start, end=end) + "*")
    parts.append("")
    parts.append("*" + ranking.CORROBORATION_CAVEAT + "*")
    # NL-151 clause 3 — the skip disclosure, at the literal bottom. Renders
    # from the analysis stage's own `fetch_skipped` record, so it cannot claim
    # a skip the stage did not make. Only entries the STAGE marked `disclose`
    # appear: a slot that never opened a socket was not a fetch failure, and
    # this renderer does not re-decide that — it reads the flag set beside the
    # outcome that justified it.
    #
    # Nothing renders when nothing was skipped, so the footer of an ordinary
    # edition is byte-identical to today's.
    for skip in (inputs.get("fetch_skipped") or []):
        if not skip.get("disclose"):
            continue
        parts.append("")
        parts.append("*" + FETCH_SKIP_LINE.format(
            title=skip.get("story_title") or "(untitled)") + "*")
    # A1: the variant stamp retired with the alternation window (samples are
    # labeled by their file headers; no methodology self-reference in output).
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Script pass
# ---------------------------------------------------------------------------

def _script_coverage(n_slots: int) -> int:
    """How many stories the episode covers (principal 2026-07-14): the lead +
    up to SCRIPT_MAX_STORIES-1 more, deterministic by the edition's own rank
    order — the top slots. Never every story; a thin day covers what exists.
    No new LLM judgment: code names the count; the covered slots are 1..k."""
    return max(1, min(n_slots, SCRIPT_MAX_STORIES))


def _script_budgets(n_slots: int) -> Tuple[int, str, int]:
    """Per-story QUALITY CEILINGS for the covered stories + a soft episode-word
    ceiling. THE PODCAST CONTRACT REWRITTEN (principal 2026-07-14): the episode
    is a SHORTER, lead-focused digest; length is EMERGENT. These are ceilings and
    guides, NEVER floors to fill — a naturally short episode on a thin day is
    correct output. The episode covers only k = _script_coverage(n_slots) stories
    (the lead + the next-most-consequential), so the ceiling and the per-segment
    guidance run over the COVERED slots only, not the whole edition. There is no
    length floor (principal 2026-07-14, floor REMOVED) — the only lower check is
    the flat SCRIPT_DEGENERATE_WORDS brokenness backstop, independent of this
    ceiling. Returns (ceiling_words, per_desc, k)."""
    k = _script_coverage(n_slots)
    ceiling = SCRIPT_OPEN_WORDS + SCRIPT_OUTRO_WORDS + sum(
        script_segment(i) for i in range(1, k + 1)
    )
    desc = " · ".join(
        f"slot {i}: up to ~{script_segment(i)}" for i in range(1, k + 1)
    )
    return ceiling, desc, k


def _norm_nums(text: str) -> set:
    return {x.replace(",", "").rstrip(".") for x in _NUM_RE.findall(text or "")}


def trace_check_numerals(stories: List[Dict], inputs: Dict) -> List[str]:
    """M3 gate 1a — §5.6 trace-don't-generate teeth, warn-grade (the same
    logic as §5.9 #7: derived numerals — "doubled", "up 4%" — legitimately
    compute from brief figures, so this warns and never rejects; the
    pre-registered escalation to reject-grade lives in NOTES-M2). Briefed
    slots only: the numeral universe is the writer view of the brief + the
    slot's cluster titles + story_title/summary; a story numeral outside
    it is named, per slot."""
    from . import analysis as analysis_mod
    briefs = inputs.get("briefs_by_slot") or {}
    if not briefs:
        return []
    warns: List[str] = []
    titles_by_slot: Dict[int, List[str]] = {}
    for n_key, items in (inputs.get("items_by_slot") or {}).items():
        titles_by_slot[int(n_key)] = [it["title"] or "" for it in items]
    for st, slot in zip(stories, inputs["slots"]):
        n = int(slot["slot"])
        doc = briefs.get(n)
        if not doc or not doc.get("brief"):
            continue
        universe = _norm_nums(analysis_mod.render_writer_view(doc["brief"]))
        universe |= _norm_nums(" ".join(titles_by_slot.get(n, [])))
        universe |= _norm_nums(slot.get("story_title", ""))
        universe |= _norm_nums(slot.get("summary", ""))
        story_text = " ".join(v for v in st.values() if isinstance(v, str))
        loose = sorted(_norm_nums(story_text) - universe)
        if loose:
            warns.append(
                f"story {n}: numeral(s) outside the brief+cluster universe "
                f"({', '.join(loose[:6])}) — §5.6 trace-don't-generate check "
                "[warn-grade; derived arithmetic is legitimate]")
    return warns


# P3.1 item 3 was "the lead's tier must EXPRESS", enforced hard-with-retry at a
# 450-word floor (NL-63 M2's re-derivation: edition total 1,800-2,500
# lead-weighted, A2's lead band 450-900, target ~640).
#
# LENGTH REGIME 2026-07-30 — THIS IS NO LONGER A FLOOR. The principal ratified
# floors -> targets product-wide: a rewrite-longer retry is pad-toward-minimum,
# which is exactly the behaviour the no-fabrication rule forbids, and the
# thinnest real slot-1 day in the record (founder brief 17 — 15 rows, two full
# texts, 16,717 held chars; frozen at tests/fixtures/slot1_thin_corpus.json)
# has no honest 640-word lead in it. 450 survives only as the DISCLOSURE
# threshold: below it the run leaves an observability note so D3's
# week-of-editions falsifier has data. No action, no retry, no discard.
LEAD_SHORT_NOTE_WORDS = 450


def _lead_words(payload: Dict) -> int:
    stories = payload.get("stories") or []
    if not stories or not isinstance(stories[0], dict):
        return 0
    return wc(" ".join(v for v in stories[0].values() if isinstance(v, str)))


def build_analysis_facts_block(inputs: Dict) -> str:
    """M3 gate 1b (§607's assumption shipped): the editor receives, for
    briefed slots, the brief's pinned facts + ledger holders/values — the
    fact universe against which a specific not present is a FABRICATION to
    cut, not tighten (constraint line lives in editor_pass.txt)."""
    briefs = inputs.get("briefs_by_slot") or {}
    if not briefs:
        return "(no analysis briefs this run — the excerpt lanes govern)"
    lines: List[str] = []
    for n in sorted(briefs):
        b = (briefs[n] or {}).get("brief") or {}
        lines.append(f"story {n} (briefed — its fact universe):")
        for f in b.get("pinned_facts", []):
            lines.append(f"  fact: {f.get('fact', '')}")
        for e in b.get("ledger", []):
            if e.get("discrepancy"):
                a, bb = e.get("a") or {}, e.get("b") or {}
                lines.append(f"  discrepancy: {a.get('value', '')} VS "
                             f"{bb.get('value', '')} (unresolved — never merge)")
            else:
                lines.append(f"  claim: {e.get('claim', '')}")
        for ef in b.get("effects", []):
            lines.append(f"  take [{ef.get('basis', '')}: {ef.get('holder', '')}]: "
                         f"{ef.get('effect', '')}")
    return "\n".join(lines)


def build_labels_block(inputs: Dict, covered: Optional[set] = None) -> str:
    # covered (script path, principal 2026-07-14): the digest covers only the top
    # slots, so it is fed labels for the COVERED stories only — a mandatory spoken
    # disclosure (override, revival) belongs to a story the episode actually
    # airs. covered=None (editor path) = every slot, unchanged.
    lines = []
    for s in inputs["slots"]:
        n = s["slot"]
        if covered is not None and int(n) not in covered:
            continue
        if s.get("override"):
            # NL-138 (ruling ④): the writer is no longer handed the ranker's
            # prose reason to voice. The spoken disclosure now carries the same
            # tag-form vocabulary the text edition and the front page carry —
            # named here as the phrase the validator will look for, so the
            # instruction and the check can never drift.
            lines.append(
                f"story {n}: OVERRIDE — this story matched none of the "
                f"reader's topics or threads; say it was chosen as "
                f"{labels.WHY_WORLD_NEWS.lower()}. The phrase "
                f"'{labels.WHY_WORLD_NEWS.lower()}' must be spoken; the rest "
                f"of the sentence is yours"
            )
        if s.get("corroboration_count") == 1 and s.get("outlets"):
            lines.append(f"story {n}: SINGLE-SOURCE — outlet: {s['outlets'][0]}")
        for rv in s.get("revived_threads", []):
            if rv.get("last_covered"):
                lines.append(
                    f"story {n}: REVIVAL — say the date: last covered {rv['last_covered']}"
                )
        lines.append(
            f"story {n}: corroboration for the ear: {s.get('corroboration_label', '')}"
        )
    lines.append("corrections flagged upstream: none this run")
    return "\n".join(lines)


def script_covered_slots(inputs: Dict) -> set:
    """The slot numbers the digest airs (principal 2026-07-14): the top
    k = _script_coverage(n) by the edition's rank order. story_slots is
    rank-ordered (the lead is slot 1), so the covered set is the k lowest slot
    numbers actually present — robust to non-contiguous slot ids.

    NL-151b GATE R-B (2026-08-14) — THE DEPTH TIER GOES FIRST. The charter was
    written when "the lead is slot 1" was an invariant; his arm (ii) retired
    that, so reading rank ORDER positionally is the same inference the nine
    propagation sites just gave up. The edition's depth slots are taken first
    and the rest of k is filled with the lowest remaining slot numbers, so the
    episode airs the stories the edition actually treated deeply.

    IT REDUCES TO THE OLD LINE BY CONSTRUCTION, not by care: at the OFF arm and
    on any morning with no vector, `edition_tiers` answers the positional
    contract, whose depth slots ARE the lowest present ids (1 full + 2 medium),
    and the fill completes the same k-lowest set the old `ordered[:k]` returned.
    Byte-identity of the built prompt on those days is pinned by name."""
    ordered = sorted(int(s["slot"]) for s in inputs["slots"])
    k = _script_coverage(len(ordered))
    tiers = edition_tiers(inputs, len(ordered))
    covered = sorted(depth_slot_numbers(inputs["slots"], tiers))[:k]
    for n in ordered:                      # fill to k, lowest remaining first
        if len(covered) >= k:
            break
        if n not in covered:
            covered.append(n)
    return set(covered)


def script_lead_slot(inputs: Dict, covered: Optional[set] = None) -> int:
    """The slot the episode opens on and spends its deepest segment.

    THE FULL-TIER STORY, not position 1 (gate R-B, 2026-08-14). Print and audio
    must not make two different claims about today's most important story: the
    print lead is the story with the full tier — the 640-word budget, the deep
    view — and on a demotion morning that is the PROMOTED story. Falls back to
    the lowest covered slot when no full tier is present (a vector that never
    reached one, an empty edition), which is what position 1 meant anyway."""
    covered = script_covered_slots(inputs) if covered is None else covered
    tiers = edition_tiers(inputs, len(inputs["slots"]))
    for s, t in zip(inputs["slots"], tiers):
        if t == "full" and int(s["slot"]) in covered:
            return int(s["slot"])
    return min(covered) if covered else 1


def build_script_prompt(date: str, variant: str, narrative: str, inputs: Dict) -> str:
    template = (paths.PROMPTS_DIR / PROMPT_SCRIPT).read_text(encoding="utf-8")
    n_slots = len(inputs["slots"])
    _, per_desc, k = _script_budgets(n_slots)
    covered = script_covered_slots(inputs)
    lead = script_lead_slot(inputs, covered)
    others = k - 1
    # NL-151b GATE R-B (2026-08-14) — THE CARRIED PREDICATE, and the whole
    # byte-identity guarantee rests on it: `_carried` is true exactly when the
    # tier vector moved NOTHING off the rank order — the covered set is still
    # the k lowest present ids and the lead is still the first of them. On
    # those days (every OFF day, every no-vector day, every ordinary armed
    # morning: 16 of 17 real dates) every string below is emitted with the
    # pre-fix bytes, including the literal "story 1" the old line hard-coded
    # for an edition whose first present id is not 1. Only a vector that
    # actually moved the depth changes a byte of this prompt.
    _ordered = sorted(int(s["slot"]) for s in inputs["slots"])
    _carried = (sorted(covered) == _ordered[:k]
                and (not _ordered or lead == _ordered[0]))
    _lead_name = "story 1" if _carried else f"story {lead}"
    if k <= 1:
        coverage_line = (
            "This edition has a single story — cover the LEAD only; there is no "
            "second story to air.")
    elif k >= n_slots:
        coverage_line = (
            f"This episode covers all {k} stories in the edition — the LEAD "
            f"({_lead_name}, the deepest segment) plus the other {others}, in rank "
            "order. The lead is the episode's center of gravity.")
    else:
        # "1 through {last}" is exact ONLY while the covered set is a prefix of
        # the present ids (the old invariant, documented in QA's ragged-ids
        # pin). A moved depth tier can cover a non-prefix set, so that day gets
        # the slots named — an inclusive range would silently order the writer
        # to cover a story the episode excludes.
        scope = (f"Cover stories 1 through {max(covered)} ONLY" if _carried
                 else "Cover stories "
                      + ", ".join(str(x) for x in sorted(covered)) + " ONLY")
        coverage_line = (
            f"This episode covers {k} stories — the LEAD ({_lead_name}, the deepest "
            f"segment) plus the {others} next-most-consequential. {scope}"
            f"; the remaining {n_slots - k} stories are NOT "
            "in this episode — they live in the text briefing. The lead is the "
            "episode's center of gravity; never cover every story.")
    if not _carried:
        # The per-story CEILINGS follow the lead too, or the writer is told to
        # spend 400 words on the story the edition demoted. `_script_budgets`'
        # episode ceiling is position-independent (one 400 + k-1 × 200,
        # whichever slots those are), so the :4901 call site and every ceiling
        # pin are untouched — only the guide text names different slots.
        per_desc = " · ".join(
            f"slot {n}: up to ~{script_segment(1 if n == lead else 2)}"
            for n in sorted(covered))
    weekday, human = _spoken_date(date)
    epistemic = (
        '; epistemic first person ("I think") is banned in this voice'
        if variant == "A"
        else '; epistemic first person is allowed only when voicing the '
        'briefing\'s labeled "My read" judgments'
    )
    # NL-58 ruling 2: the spoken caveat is OUT of the podcast (the app carries
    # the caveat; the spoken furniture is a deliberate, principal-ruled
    # contract change). The prompt no longer asks for it — {spoken_caveat} is
    # gone from the template — and validate_script no longer appends it.
    return template.format(
        date_line=f"{weekday}, {human}",
        time_of_day=_time_of_day(),
        coverage_line=coverage_line,
        band_high=SCRIPT_CEILING_WORDS,
        minutes_high=round(SCRIPT_CEILING_WORDS / 150),
        budget_open=SCRIPT_OPEN_WORDS,
        budget_stories=per_desc,
        budget_outro=SCRIPT_OUTRO_WORDS,
        weekday=weekday,
        spoken_date=human,
        epistemic_rule=epistemic,
        continuity_license=_continuity_license_block(inputs),
        labels_block=build_labels_block(inputs, covered),
        narrative_text=narrative,
    )


def _continuity_license_block(inputs: Dict) -> str:
    """The thread-arc callback license, DATA-GATED (Stage-0 M2; M0 finding F2).

    The template used to license this unconditionally, exemplar and all — and
    the exemplar ("third week we've tracked this") is precisely the shape a
    day-one episode must never produce. Every other cold-start surface got its
    day-one silence at M0 (the writer's prior block, the analyst's prior
    channel, the editor's protect block); the spoken lane was the one still
    handing out an invitation with no data behind it.

    Gated on the SAME predicate the validator uses, so the model is never
    invited to do a thing the run record will then flag it for."""
    if _has_real_prior_coverage(inputs):
        return ('thread-arc callbacks from real thread data ("third week '
                "we've tracked this — first time it's moved\");")
    return (
        "NO thread-arc callbacks — this edition has no prior coverage on\n"
        "record. Never imply this show has said anything before: no \"as we\n"
        "covered\", no \"we've been tracking\", no \"regular listeners will\n"
        "remember\", no week/day counts. This is the first time any of it is\n"
        "being said out loud, and saying otherwise invents a relationship with\n"
        "the listener that does not exist;")


def _date_spoken_forms(iso_date: str) -> List[str]:
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return [iso_date]
    month = _MONTHS[d.month - 1]
    day = d.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return [iso_date, f"{month} {day}", f"{month} {day}{suffix}"]


_TTS_PLUS_RE = re.compile(r"\b([A-Z][A-Za-z]*)\+(?=[\s.,;:!?)\"']|$)")
_TTS_CURRENCY_SUFFIX_RE = re.compile(
    # BUG18: consume an existing trailing " dollars" so the model's routine
    # redundancy ("$2 billion dollars") can't double into a spoken stutter.
    r"\$(\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|thousand|[TBMK])\b"
    r"(?:\s+dollars\b)?")
_TTS_CURRENCY_BARE_RE = re.compile(r"\$(\d[\d,]*(?:\.\d+)?)(?:\s+dollars\b)?")
_TTS_THOUSANDS_RE = re.compile(r"\b(\d{1,3}),000,000\b")
_TTS_THOUSAND_RE = re.compile(r"\b(\d{1,3}),000\b")
_TTS_RANGE_RE = re.compile(r"\b(\d{4})\s*[–—-]\s*(\d{4})\b")
_TTS_SUFFIX_WORDS = {"T": "trillion", "B": "billion", "M": "million",
                     "K": "thousand"}


def tts_safe_pass(text: str) -> Tuple[str, List[str]]:
    """P3 item 8 — deterministic, code-owned, enumerated transforms that
    cater to the voice model's observed limitations (the tics class:
    'eight hundred zero zero zero', 'dollar five T'). Runs AFTER script
    validation (the validators see the model's own output; these are
    furniture-class rewrites of FORM, never facts), each application
    disclosed. Idempotent by construction: every output form is a fixed
    point of every rule."""
    notes: List[str] = []

    def sub(rx, repl, label):
        nonlocal text
        text, n = rx.subn(repl, text)
        if n:
            notes.append(f"{label} ×{n}")

    # "$5T" / "$1.2 billion" -> "5 trillion dollars" / "1.2 billion dollars"
    def currency_suffix(m):
        num, suf = m.group(1), m.group(2)
        word = _TTS_SUFFIX_WORDS.get(suf, suf)
        return f"{num} {word} dollars"
    sub(_TTS_CURRENCY_SUFFIX_RE, currency_suffix, "currency-with-magnitude")
    # bare "$188,000" -> "188,000 dollars" (thousands rule below then speaks it)
    sub(_TTS_CURRENCY_BARE_RE, r"\1 dollars", "bare-currency")
    # "800,000,000" handled first, then "800,000" -> "800 thousand"
    sub(_TTS_THOUSANDS_RE, r"\1 million", "even-millions")
    sub(_TTS_THOUSAND_RE, r"\1 thousand", "even-thousands")
    # "OPEC+" -> "OPEC plus"
    sub(_TTS_PLUS_RE, r"\1 plus", "plus-suffix")
    # "2024-2026" -> "2024 to 2026"
    sub(_TTS_RANGE_RE, r"\1 to \2", "year-range")
    # "5%" -> "5 percent"
    text, n = re.subn(r"(\d)\s*%", r"\1 percent", text)
    if n:
        notes.append(f"percent ×{n}")
    return text, notes


# P3.1 anchor fix (QA contract 2026-07-09, tests/test_p31_enforcement.py
# test_cold_open_anchor_evasion_variants_pinned_as_actual): accept "it's"
# OR "it is" — and require one of them, so the possessive "its Monday,
# July 6 meeting" can no longer false-anchor mid-prose. The typographic
# apostrophe is handled by _anchor_view below, not the regex.
_DATELINE_RE = re.compile(r"\bit(?:'s| is) [a-z]+, [a-z]+ \d{1,2}")
COLD_OPEN_MAX_SENTENCES = 3
COLD_OPEN_MAX_WORDS = 60        # the ruling's "~50" plus handoff-line slack
REPEAT_GRAMS_THRESHOLD = 3      # distinct shared 6-grams between two sections
MAX_STRUCTURAL_REPORTS = 3


def _anchor_view(body: str) -> str:
    """Anchor-matching view of a script: lowercased, with the typographic
    U+2019 apostrophe normalized to ASCII (1:1 char replacement, so match
    offsets stay valid against the original text). Common in LLM output
    and invisible in a text review — without this, curly-quote typography
    silently switches the HARD cold-open cap off (QA anchor-fix contract
    2026-07-09, tests/test_p31_enforcement.py)."""
    return body.lower().replace("’", "'")


def script_structural_check(body: str) -> List[str]:
    """P3.1 (principal rulings 2026-07-06, item 4 — the spoken editorial
    bar, enforcement-grade): the cold open orients and hands off within
    <=3 sentences / ~50 words with no story pre-play, and no two sections
    of the script retell the same material. Violations are HARD-WITH-RETRY
    at the script stage: one retry with the violations injected, then ship
    the better attempt WITH disclosure — never silently, never a dead run,
    never an infinite retry against the cap. Calibrated against the
    2026-07-06 script that shipped to the principal's ears (must catch)
    and a legitimate script (must pass) — both pinned as fixtures."""
    out: List[str] = []
    low = _anchor_view(body)
    m = _DATELINE_RE.search(low)
    if m:
        pre = body[:m.start()]
        sents = [x for x in re.split(r"(?<=[.!?])\s+", pre) if x.strip()]
        words = len(pre.split())
        if len(sents) > COLD_OPEN_MAX_SENTENCES or words > COLD_OPEN_MAX_WORDS:
            out.append(
                f"cold open runs {len(sents)} sentences / {words} words "
                f"before the dateline — the cap is "
                f"{COLD_OPEN_MAX_SENTENCES} sentences / ~50 words: a "
                "one-line hook, then \"It's [date]. Here's what matters "
                "today.\" The story's facts belong in the story.")
    paras = [pp for pp in low.split("\n\n") if len(pp.split()) >= 15]
    gram_sets = []
    for pp in paras:
        ws = re.findall(r"[a-z']+", pp)
        gram_sets.append({" ".join(ws[i:i + 6]) for i in range(len(ws) - 5)})
    reported = 0
    for i in range(len(gram_sets)):
        for j in range(i + 1, len(gram_sets)):
            shared = gram_sets[i] & gram_sets[j]
            if len(shared) >= REPEAT_GRAMS_THRESHOLD:
                ex = sorted(shared)[0]
                out.append(
                    f"sections {i + 1} and {j + 1} retell the same material "
                    f"({len(shared)} shared 6-word runs, e.g. \"{ex}...\") "
                    "— every layer adds NEW information; say it once, in "
                    "the right place")
                reported += 1
                if reported >= MAX_STRUCTURAL_REPORTS:
                    return out
    return out


def validate_script(
    text: str, narrative: str, inputs: Dict, covered: Optional[set] = None
) -> Tuple[str, List[str], List[str]]:
    """Returns (possibly-repaired text, hard_problems, warnings).
    Hard problems: missing mandatory spoken disclosures (override elements,
    revival dates) — retry material. The sign-off is frozen furniture,
    deterministically appended if absent (verbatim string, not facts) with a
    disclosure warning. The spoken caveat was retired from the podcast by NL-58
    ruling 2 (the app carries it) — no longer appended or checked here.
    Fact-subset + hedge checks warn (§5.9 #7-8: flag for review, never
    auto-fix).

    `covered` (principal 2026-07-14, digest contract): the slot numbers the
    episode actually airs. A mandatory spoken disclosure is only owed for a
    story the digest COVERS — an override or revival on an uncovered lower-rank
    story is disclosed by the text briefing, not the episode. covered=None =
    every slot (backward-compatible for direct callers / whole-edition scripts)."""
    hard: List[str] = []
    warnings: List[str] = []
    body = text.strip()
    low = body.lower()

    for s in inputs["slots"]:
        n = s["slot"]
        if covered is not None and int(n) not in covered:
            continue
        if s.get("override"):
            # ‼ FROZEN-SURFACE RE-SCOPE, NL-138 — principal's ruling ④
            #   (DECISIONS 2026-08-02). See the OVERRIDE_TEXT_LABEL note above;
            #   this is the spoken half of the same §5.7 contract change.
            #
            #   WAS: two hard checks — (1) the acknowledgment "outside your"
            #   must be spoken, and (2) the first four words of the ranker's
            #   prose reason must appear in the script.
            #
            #   Check (2) is not weakened, it is UNSATISFIABLE: there is no
            #   prose reason left to look for. Check (1) folds into it rather
            #   than surviving alongside, because the ruled tag form carries
            #   the same fact in the vocabulary every other surface uses — a
            #   story "chosen as Important World News" IS a story that matched
            #   none of the reader's tags or threads (true by construction:
            #   the override pool is the ZERO-personal-signal pool). Keeping a
            #   second, differently-worded assertion would be the one thing
            #   NL-134's "just once" spec forbids.
            #
            #   NET DISCLOSURE STRENGTH: unchanged. An override the episode
            #   airs must still voice its disclosure or the script is retried;
            #   what moved is which words satisfy it. build_labels_block hands
            #   the writer this exact phrase, so instruction and check read the
            #   same constant.
            if labels.WHY_WORLD_NEWS.lower() not in low:
                hard.append(
                    f"story {n}: spoken override missing its "
                    f"{labels.WHY_WORLD_NEWS!r} acknowledgment")
        for rv in s.get("revived_threads", []):
            date_needed = rv.get("last_covered")
            if date_needed and not any(f.lower() in low for f in _date_spoken_forms(date_needed)):
                # A5: spoken presentation is licensed; the TEXT disclosure
                # stays hard (validate_narrative_payload). Warn-grade here.
                warnings.append(
                    f"story {n}: spoken revival date {date_needed!r} not voiced"
                )
        # A5: per-story spoken attribution (incl. single-source phrasing) is
        # editorial judgment now — no presence check. Accuracy checks stay.

    # NL-58 ruling 2: the spoken caveat is OUT of the podcast (DECISIONS
    # 2026-07-10). The append machinery and the NOTES 28c paraphrase-removal
    # (which existed only to keep the model's paraphrase from doubling the
    # verbatim append) are both gone — nothing appends, so nothing can double.
    # The app-side caveat footer is untouched. Only the sign-off remains frozen
    # furniture: appended verbatim if the model dropped it.
    if SIGNOFF.lower() not in low:
        body = body.rstrip() + "\n\n" + SIGNOFF
        warnings.append("sign-off was missing — appended verbatim")

    if "see you tomorrow" in low:
        hard.append("schedule promise ('see you tomorrow') — banned, v1 is on-demand")

    # Fact-subset proxy (§5.9 #7): script numerals must exist in the narrative
    # (comma-insensitive; sanctioned ear-rounding words exempt the check only
    # for the rounded phrase, not for new precise figures).
    narrative_nums = {x.replace(",", "").rstrip(".") for x in _NUM_RE.findall(narrative)}
    script_nums = {x.replace(",", "").rstrip(".") for x in _NUM_RE.findall(body)}
    # NOTES 28b: the old blanket {2, 3} exemption becomes principled —
    # enumeration-of-structure numerals (counts up to the story count:
    # "Two quick ones", the menu's shape) are script furniture, not facts;
    # anything else single-digit is checked like every other numeral.
    enum_ok = {str(i) for i in range(1, len(inputs["slots"]) + 1)}
    loose = sorted(x for x in script_nums - narrative_nums if x not in enum_ok)
    if loose:
        warnings.append(f"script numerals absent from narrative (review): {loose[:8]}")
    # Hedge preservation (§5.9 #8, coarse): "will" in script needs "will" in narrative.
    if re.search(r"\bwill\b", body, re.I) and not re.search(r"\bwill\b", narrative, re.I):
        warnings.append("script uses 'will' where the narrative never does — hedge check")

    hits = _scan_banned(body)
    if hits:
        warnings.append(f"script banned strings: {hits}")

    # P3 #2's warn-grade never-repeat detector PROMOTED to the structural
    # hard-with-retry class (principal ruling 2026-07-06: the warn fired on
    # the exact run that shipped to his ears) — see script_structural_check.
    # P3 #3 — rhythm: three consecutive long sentences kill spoken pacing.
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    run = 0
    for s in sentences:
        run = run + 1 if len(s.split()) > 24 else 0
        if run >= 3:
            warnings.append(
                "rhythm (P3 #3): three consecutive 25+-word sentences — "
                "vary length for the ear")
            break
    # P3 #4 — written register has no place in speech.
    register_hits = [w for w in ("the latter", "the former", "aforementioned",
                                 "respectively") if w in low]
    if ";" in body:
        register_hits.append("semicolon")
    if register_hits:
        warnings.append(f"speech-not-prose (P3 #4): written-register "
                        f"constructions: {register_hits}")
    mech = [x for x in MECHANICAL_TRANSITIONS if x in low]
    if mech:
        warnings.append(f"mechanical transition defaults (A4): {mech}")
    # A4 intro formula: the dateline should not be the opening breath.
    dateline_pos = low.find("it's ")
    if 0 <= dateline_pos < 60:
        warnings.append(
            "intro formula (A4): dateline arrives before any what/why/"
            "uncertainty framing"
        )
    return body, hard, warnings


# ---------------------------------------------------------------------------
# Persistence + instrumentation + artifact
# ---------------------------------------------------------------------------

def run_memory_pass(con: sqlite3.Connection, date: str, key: str, cap: float,
                    spent: float, briefs_by_slot: Dict[int, Optional[Dict]],
                    slots: List[Dict], report: "GenReport",
                    state_chat=None,
                    published_at: Optional[str] = None) -> float:
    """NL-63 M1 memory pass. Two writes: (1) the delta LEDGER — Pax's economy,
    ~$0, the validated arc persists as the thread's delta; (2) the standing
    STATE — the ONLY new LLM spend, and ONLY for threads that advanced/reversed
    today (write law), pre-checked against the $0.25 cap, stale-but-honest on
    any failure. Returns the updated `spent`. `state_chat` is injectable so the
    offline suite exercises this exact path without spending.

    `published_at` (NL-107) is the edition stamp the CALLER's briefs_by_slot
    was read against, and it only travels so the delta's cited brief_id is the
    same brief. The inline pass leaves it None on purpose: it runs AFTER
    persist_generation (the promote), so for that run newest IS coherent —
    see the ordering comment at the call site in _run_generate_body."""
    from . import memory_core
    brow = con.execute("SELECT id FROM briefings WHERE date = ?",
                       (date,)).fetchone()
    briefing_id = brow["id"] if brow else None
    delta_rep = memory_core.write_deltas_for_edition(
        con, date, briefing_id, briefs_by_slot, slots,
        published_at=published_at)
    report.warnings.append("memory: " + delta_rep.summary())
    # Delta-7 photocopy gap (Content council 2026-07-16): a near-duplicate
    # significance is WARN-grade — surfaced here (and into report.memory below)
    # so diagnose sees it; the delta itself is written as-is.
    for sus in delta_rep.photocopy_suspects:
        report.warnings.append(
            f"memory: photocopy-suspect delta on {sus['thread']!r} "
            f"({sus['date']}) — significance near-identical (Jaccard "
            f"{sus['score']}) to the {sus['against_edition']} delta; written "
            "as-is (WARN), supersede/repair if a true duplicate")
    state_results: List[Dict] = []
    try:
        state_template = (paths.PROMPTS_DIR / "thread_state.txt").read_text(
            encoding="utf-8")
    except OSError as exc:
        state_template = ""
        report.warnings.append(
            f"memory: state prompt unreadable ({exc}) — state rewrites "
            "skipped, prior states kept stale-but-honest")
    try:
        for tid in delta_rep.moved_thread_ids:
            if not state_template:
                break
            trow = con.execute("SELECT topic FROM memory WHERE id = ?",
                               (tid,)).fetchone()
            topic = trow["topic"] if trow else f"thread {tid}"
            sr = memory_core.rewrite_state(
                con, tid, topic, date, briefing_id, key, state_template,
                remaining_usd=cap - spent, chat=state_chat)
            # Cap binds on SHADOW (Onna's law): remaining_usd above is cap-spent,
            # and spent accumulates shadow so a subscription seat (usd_charged==0)
            # still counts against the cap at its API-equivalent price.
            spent += sr.shadow_usd
            report.memory_usd += sr.cost_usd
            report.memory_shadow_usd += sr.shadow_usd
            state_results.append({"thread": topic, "outcome": sr.outcome,
                                  "detail": sr.detail, "usd": round(sr.cost_usd, 6),
                                  "usd_shadow": round(sr.shadow_usd, 6)})
            if sr.outcome in ("stale", "rejected", "skipped-budget",
                              "skipped-no-ledger", "failed"):
                report.warnings.append(
                    f"memory: state for {topic!r} {sr.outcome} — {sr.detail}")
    finally:
        # The step lands even when a later thread's rewrite raises mid-loop
        # (gate Fix 1, loop #5): paid spend must reach report.steps BEFORE the
        # exception propagates, or both callers' containment folds see nothing
        # and briefings.token_cost under-reports money the CLI prints.
        #
        # R-B3a (B3): the row is gated on SHADOW spend, not charged. A
        # subscription-lane state seat bills usd_charged == 0.0 while its
        # usd_shadow is non-zero; the OLD `if report.memory_usd:` guard dropped
        # that row entirely — the state seat's whole spend vanished from the
        # ledger the moment the lane went subscription (the guard class the
        # rider names). Now: record whenever there was shadow spend, and carry
        # BOTH figures (usd == usd_charged for back-compat; usd_shadow always
        # populated). On the api lane the two are equal, so no existing row moves.
        if report.memory_shadow_usd:
            _state_cfg = llm.resolve_seat("state")
            _state_charged = round(report.memory_usd, 6)
            _state_shadow = round(report.memory_shadow_usd, 6)
            report.steps.append({"step": "state_rewrites",
                                 "model": _state_cfg.model,
                                 "lane": _state_cfg.lane,
                                 "usd": _state_charged,
                                 "usd_shadow": _state_shadow,
                                 "usd_charged": _state_charged})
    report.memory = {
        "deltas_written": len(delta_rep.written),
        "deltas_skipped": len(delta_rep.skipped),
        # BUG-29: the skip REASONS ride into the durable report (not just a
        # count) — a self-reference / two-clause / unresolvable-thread refusal
        # is the trust case; a silent refusal is indistinguishable from amnesia.
        # From here they reach the generation log and diagnose's MEMORY section.
        "deltas_skipped_reasons": list(delta_rep.skipped),
        "threads_moved": len(delta_rep.moved_thread_ids),
        "state_rewrites": state_results,
        # Delta-7 photocopy gap: the WARN-grade near-duplicate significances,
        # carried durably (like deltas_skipped_reasons) so diagnose can show
        # them — a silent write-as-is would be indistinguishable from amnesia.
        "photocopy_suspects": list(delta_rep.photocopy_suspects),
    }
    return spent


@dataclass
class BackfillReport:
    """The outcome of a memory backfill (live-contact fix #4). `refused` is a
    first-class, honest outcome (stale-but-honest beats fabricated context) — the
    edition's gap stays recorded, never filled with invented material."""
    date: str
    refused: bool = False
    reason: str = ""
    deltas_written: int = 0
    deltas_skipped: int = 0
    threads_moved: int = 0
    memory_usd: float = 0.0
    cap: float = 0.0
    state_rewrites: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def run_memory_backfill(
    date: Optional[str] = None, con: Optional[sqlite3.Connection] = None,
    env: Optional[dict] = None, state_chat=None, force: bool = False,
) -> BackfillReport:
    """Live-contact fix #4 — run the NL-63 memory pass for an ALREADY-PUBLISHED
    edition of record whose moat was never written (a `--no-refresh` record
    completion under the old gate). A gate flip alone cannot cure an edition that
    already shipped: re-running `generate` would archive and REWRITE the edition
    of record (unacceptable). This thin driver reaches the same run_memory_pass
    path WITHOUT regenerating anything.

    CONTEXT FIDELITY (disclosed): run_memory_pass reads its inputs from PERSISTED
    rows, not volatile narrative-stage state — briefs_by_slot from the valid
    briefs of the PUBLISHED edition (NL-107: bounded by its publish stamp, since
    unlike the inline pass this driver may run in a world where a later
    regenerate wrote briefs and died), slots from the briefing's story_slots,
    the ledger from thread_deltas. The live inline pass reads the SAME sources
    (see _run_generate_body) — for a date with no dead regenerate the two reads
    are identical row-for-row. So the backfill's delta-write + state-rewrite
    context is byte-identical to a live inline pass — there is NO degradation.
    The only difference is `spent`=0.0 (the backfill is its own run doing only
    the memory pass; the edition's generation cost was already billed to its
    token_cost), so the FULL cap is available to the state rewrite. The state-rewrite spend is
    folded into the edition's token_cost exactly as the live path's
    _fold_cost_steps does — WITHOUT re-archiving; the narrative/script are never
    touched.

    REFUSES (never fabricates) when context is unrecoverable:
      * no briefing of record for the date (nothing published to backfill);
      * no valid analysis brief persisted for the date — the arc a delta writes
        FROM never existed; the backfill refuses rather than invent it.

    Idempotent: a second backfill writes no new delta, moves no thread, bills $0.
    `state_chat` is injectable so the offline suite exercises this exact path
    without spending; disclose-don't-crash contains any pass failure.
    """
    import os

    src_env = env if env is not None else os.environ
    date = date or ranking.local_today()
    key = (src_env.get("OPENAI_API_KEY") or "").strip()
    bf = BackfillReport(date=date)

    own_con = con is None
    if own_con:
        db.migrate()
        con = db.connect()
    try:
        # 1. the edition of record must exist (recoverable-context gate 1).
        try:
            inputs = load_briefing_inputs(con, date)
        except GenerateError as exc:
            bf.refused = True
            bf.reason = str(exc)
            return bf
        slots = inputs["slots"]

        # 1b. the edition must be PUBLISHED, not merely ranked (gate Fix 3,
        # loop #5): rank creates the briefings row before generate publishes,
        # so a rank-succeeded/generate-failed day leaves row + valid briefs +
        # NULL narrative — and a backfill here would write ledger deltas
        # citing an edition that never shipped, the orphan-delta class the
        # M1 gate-F reorder exists to prevent.
        nrow = con.execute(
            "SELECT narrative_text FROM briefings WHERE date = ?", (date,)
        ).fetchone()
        if nrow is None or not (nrow["narrative_text"] or "").strip():
            bf.refused = True
            bf.reason = (
                f"the {date} edition was ranked but never PUBLISHED (no "
                "narrative on the record) — a delta may only cite a published "
                "edition; complete it first (`newslens generate --no-refresh`) "
                "and then backfill")
            return bf

        # 2. reconstruct briefs_by_slot from PERSISTED valid briefs — the SAME
        #    source the live inline pass reads (_run_generate_body lines ~1666).
        #
        #    NL-107 tooth (b): bounded by the PUBLISHED edition's own stamp.
        #    The published-gate above keys on narrative presence, and NL-106
        #    changed what that means after a failed regenerate — the gate now
        #    (correctly) passes on a genuinely published old edition, so the
        #    accidental interlock that used to stop this path is gone. Without
        #    the bound a backfill would write ledger deltas citing the DEAD
        #    run's arcs against the surviving edition's slots: the render
        #    mixture, made durable in the thread ledger.
        from . import analysis as analysis_mod
        published_at = inputs["row"]["generated_at"]
        briefs_by_slot: Dict[int, Optional[Dict]] = {}
        # NL-151b: the depth slots of the edition being backfilled, recovered
        # from its own run record. The backfill never runs the analysis stage,
        # so `n <= 3` would silently skip a PROMOTED story's brief — and the
        # ledger deltas the backfill writes are derived from these briefs, so
        # the miss would land in the thread record permanently rather than in
        # one morning's edition. At the OFF arm this reduces to `n <= 3`.
        backfill_depth = depth_slot_numbers(
            slots,
            edition_tiers({"depth_tiers":
                           analysis_mod.depth_tiers_from_record(date, len(slots))},
                          len(slots)))
        for s in slots:
            n = int(s["slot"])
            if n in backfill_depth:
                doc = analysis_mod.coherent_valid_brief(con, date, n,
                                                        published_at)
                if doc:
                    briefs_by_slot[n] = doc
        if not briefs_by_slot:
            bf.refused = True
            bf.reason = (
                f"no valid analysis brief persisted for {date} — the delta arc "
                "the ledger writes FROM was never on the record; backfill refuses "
                "rather than fabricate context (stale-but-honest: the gap stays "
                "recorded, not filled with invented material)")
            return bf

        # NL-72 (gate chip, loop #5): a backfill for an edition OLDER than a
        # thread's existing activity would build state from FUTURE-DATED ledger
        # entries and stamp it with the older as_of_date — poisoning BUG-30's
        # strict prior-coverage reads (a state stamped `date` holding later
        # knowledge). Refuse when any thread the pass WOULD MOVE already carries
        # a newer delta or state; --force overrides with a disclosed warning.
        from . import memory_core
        offenders = memory_core.backfill_newer_activity(
            con, date, slots, briefs_by_slot)
        if offenders:
            detail = "; ".join(
                f"{o['thread']} has activity through {o['newer_date']}"
                for o in offenders)
            if not force:
                bf.refused = True
                bf.reason = (
                    f"the {date} backfill would MOVE thread(s) that already "
                    f"carry NEWER activity ({detail}) — building state from "
                    f"future-dated ledger entries and stamping it as-of {date} "
                    "would poison strict prior-coverage reads (a state stamped "
                    f"{date} holding later knowledge, worse than the latest-by-"
                    "id regression NL-72 guards). Re-run with --force to build "
                    "the older-edition state from the ledger as it stands "
                    "(the poison is then a disclosed, deliberate choice)")
                return bf
            bf.warnings.append(
                f"NL-72 --force override: backfilling {date} over thread(s) "
                f"with newer activity ({detail}) — the standing state is being "
                f"regenerated from the FULL ledger and stamped as-of {date}, so "
                "it may carry knowledge that postdates the edition; this was an "
                "explicit --force choice, not a silent one")

        cap = config.budget_cap_usd_per_run(src_env)
        bf.cap = cap
        report = GenReport(date=date, variant=ACTIVE_VOICE)
        spent = 0.0
        # disclose-don't-crash containment (BUG-34 twin): the edition is already
        # PUBLISHED — a pass failure must not raise past this driver.
        try:
            spent = run_memory_pass(con, date, key, cap, spent, briefs_by_slot,
                                    slots, report, state_chat=state_chat,
                                    published_at=published_at)
        except Exception as exc:  # noqa: BLE001 — the edition is on the record
            report.warnings.append(
                f"memory backfill pass failed ({exc}) — the edition {date} is "
                "already PUBLISHED and unaffected; its delta ledger / standing "
                "state may be only partially updated — a repeat backfill "
                "completes missing ledger entries; standing state catches up "
                "on the thread's next real move")
        # money honesty: fold any state-rewrite spend into the edition's
        # token_cost (only the memory pass populates report.steps here) WITHOUT
        # re-archiving — the narrative/script of record stay untouched.
        late_steps = list(report.steps)
        if late_steps:
            _fold_cost_steps(con, date, late_steps)

        bf.deltas_written = report.memory.get("deltas_written", 0)
        bf.deltas_skipped = report.memory.get("deltas_skipped", 0)
        bf.threads_moved = report.memory.get("threads_moved", 0)
        bf.state_rewrites = report.memory.get("state_rewrites", [])
        bf.memory_usd = report.memory_usd
        # Preserve any pre-pass warning already on the report (the NL-72 --force
        # disclosure is appended before the pass runs) — append, never replace.
        bf.warnings = list(bf.warnings) + list(report.warnings)
        return bf
    finally:
        if own_con:
            con.close()


@dataclass
class StateRepairReport:
    """NL-73 outcome. `refused` is the honest no-op when nothing is stale (the
    common, healthy case) — distinct from a failed run. `repaired` carries one
    entry per stale thread the pass touched: {thread, thread_id, outcome, detail,
    usd, as_of}."""
    refused: bool = False
    reason: str = ""
    cap: float = 0.0
    spent_usd: float = 0.0
    repaired: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def run_state_repair(
    thread_id: Optional[int] = None, all_threads: bool = False,
    con: Optional[sqlite3.Connection] = None, env: Optional[dict] = None,
    state_chat=None,
) -> StateRepairReport:
    """NL-73 the state-repair rung: rewrite the standing state for threads whose
    latest LIVE delta postdates their latest state (the exact shape a failed
    state rewrite leaves — the delta landed, the rewrite failed, and under the
    fixed moved-semantics it self-heals only on the thread's NEXT real move). A
    targeted repair does the healing now: full-ledger regeneration per the write
    law (memory_core.rewrite_state, stamped at the latest live delta's date),
    cap pre-checked, disclose-don't-crash, refuses when nothing is stale.

    Exactly ONE selector: `thread_id=N` scopes to one thread; `all_threads=True`
    sweeps every stale thread. Passing neither or both is a caller error
    (ValueError) — the CLI enforces the mutually-exclusive group.

    The paid spend is durable on each new thread_state row's cost_usd (there is
    no single edition to fold into — a repair is thread-scoped, not edition-
    scoped); rep.spent_usd is the run total. `state_chat` is injectable so the
    offline suite exercises this exact path without spending."""
    import os

    if (thread_id is None) == (not all_threads):
        raise ValueError(
            "run_state_repair needs EXACTLY ONE of thread_id / all_threads "
            f"(got thread_id={thread_id!r}, all_threads={all_threads!r})")

    src_env = env if env is not None else os.environ
    key = (src_env.get("OPENAI_API_KEY") or "").strip()
    rep = StateRepairReport()

    own_con = con is None
    if own_con:
        db.migrate()
        con = db.connect()
    try:
        from . import memory_core
        stale = memory_core.find_stale_state_threads(con, thread_id=thread_id)
        if not stale:
            rep.refused = True
            rep.reason = (
                "no thread has a standing state behind its latest live delta — "
                "nothing is stale, nothing to repair"
                + (f" (thread {thread_id})" if thread_id is not None else ""))
            return rep

        try:
            state_template = (paths.PROMPTS_DIR / "thread_state.txt").read_text(
                encoding="utf-8")
        except OSError as exc:
            rep.refused = True
            rep.reason = (f"state prompt unreadable ({exc}) — cannot repair "
                          "without the regeneration template")
            return rep

        cap = config.budget_cap_usd_per_run(src_env)
        rep.cap = cap
        spent = 0.0
        try:
            for s in stale:
                # Each rewrite stamps the state at the thread's LATEST LIVE delta
                # date — the state catches up exactly to where the ledger is.
                sr = memory_core.rewrite_state(
                    con, s["thread_id"], s["topic"], s["latest_delta_date"],
                    None, key, state_template, remaining_usd=cap - spent,
                    chat=state_chat)
                spent += sr.shadow_usd   # cap on shadow (== cost_usd on the api lane)
                rep.repaired.append({
                    "thread": s["topic"], "thread_id": s["thread_id"],
                    "outcome": sr.outcome, "detail": sr.detail,
                    "usd": round(sr.cost_usd, 6),
                    "as_of": s["latest_delta_date"]})
                if sr.outcome != "written":
                    rep.warnings.append(
                        f"state repair for {s['topic']!r} {sr.outcome} — "
                        f"{sr.detail}")
        finally:
            # rewrite_state never raises post-paid-chat (D2 fix), so spent is
            # always complete here; the finally is defense-in-depth against a
            # pre-chat raise (template render) leaving the total unreported.
            rep.spent_usd = round(spent, 6)
        return rep
    finally:
        if own_con:
            con.close()


# ===========================================================================
# NL-77 the thread cold-start backgrounder — the generator + the retroactive
# command driver. The generation is ONE analyst-model call (the existing analyst
# machinery pointed backwards, via call_analysis_model), the same seam
# ANALYSIS_MODEL/STATE_MODEL ride. (Stale comment corrected at NL-95: the seat
# is Claude Sonnet 5 on the subscription lane with an armed api fall-over, not
# "GPT-4o, ~$0.01-0.02" — the GPT-4o-era figure was three model changes old and
# it is the same wrong premise that left this path's cap unenforced.) Refusal never fabricates; the spend
# is durable on the thread_baselines row. DO NOT run the retroactive sweep
# against real data — it is a principal checkpoint (and waits on the junk-sweep
# ruling); the command exists so it CAN be run, under his word, against the
# sandbox first.
# ===========================================================================
_BASELINE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")   # a plausible year, not any 4-digit quantity


def _default_baseline_chat(key: str, prompt: str) -> Tuple[Dict, float, float]:
    """The real backgrounder call on the ANALYSIS_MODEL seam (the existing
    analyst machinery — call_analysis_model: one retry, then raises; cost
    accumulates every billed attempt). Injectable so the offline suite exercises
    this exact path without spending.

    NL-95: passes the analyst's (parsed, usd_charged, usd_shadow) straight
    through — the caller's tolerant unpack keeps 2-tuple injected chats working."""
    from . import analysis
    return analysis.call_analysis_model(key, prompt)


def _baseline_bare_repetition(text: str) -> List[str]:
    """Continuity words in `text` that sit in a sentence carrying NO date at all
    (no ISO date, no 'Month D', no 4-digit year, no baseline cite) — the "never
    bare" class the backgrounder law forbids. A repetition word inside a dated
    clause ('reimposed the sanctions lifted in 2015') is fine."""
    from . import memory_core as mc
    bad: List[str] = []
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        m = _REPETITION_RE.search(sent)
        if not m:
            continue
        dated = (mc._ISO_RE.search(sent) or mc._MONTH_DAY_RE.search(sent)
                 or _BASELINE_YEAR_RE.search(sent) or mc.has_baseline_cite(sent))
        if not dated:
            bad.append(m.group(0))
    return bad


class BaselineRejected(ValueError):
    """The backgrounder failed its validation teeth (fabrication/bare-continuity
    class) — a 'failed' baseline row is written, never fabricated content."""


def _validate_baseline(raw) -> Tuple[str, str, List[str]]:
    """validate_brief-grade teeth for the backgrounder genre. Returns
    (backgrounder, state_seed, cites) or raises BaselineRejected. The adversary
    is the model author (analysis._require_str precedent): a non-string / empty
    field, or a BARE continuity word, is rejected — the honest refusal beats
    invented founding history."""
    if not isinstance(raw, dict):
        raise BaselineRejected(
            f"baseline response was not a JSON object ({type(raw).__name__})")
    bg = raw.get("backgrounder")
    seed = raw.get("state_seed")
    if not isinstance(bg, str) or not bg.strip():
        raise BaselineRejected("baseline 'backgrounder' is missing or not a "
                               "non-empty string")
    if not isinstance(seed, str) or not seed.strip():
        raise BaselineRejected("baseline 'state_seed' is missing or not a "
                               "non-empty string")
    bare = _baseline_bare_repetition(bg) + _baseline_bare_repetition(seed)
    if bare:
        raise BaselineRejected(
            "baseline carries bare continuity diction with no dated anchor "
            f"({', '.join(sorted(set(bare)))}) — a backgrounder licenses a "
            "continuity word only inside a dated clause (never bare)")
    cites_raw = raw.get("cites")
    cites = [c.strip() for c in cites_raw
             if isinstance(c, str) and c.strip()] if isinstance(cites_raw, list) else []
    return bg.strip(), seed.strip(), cites


@dataclass
class BaselineGenResult:
    thread_id: int
    topic: str
    outcome: str              # written | rejected | skipped-budget | failed
    detail: str = ""
    cost_usd: float = 0.0     # usd_CHARGED — persisted to thread_baselines
    # NL-95: usd_SHADOW — the cap figure. Twin of StateRewriteResult.shadow_usd.
    shadow_usd: float = 0.0


def generate_thread_baseline(
    con: sqlite3.Connection, thread_id: int, topic: str, note: str, date: str,
    key: str, remaining_usd: float, chat=None,
) -> BaselineGenResult:
    """Generate ONE thread's entry-zero backgrounder end to end: render the
    backwards-pointed prompt, cap-pre-check, ONE analyst-model call, validate
    (teeth), then either record a 'ready' baseline (backgrounder + state_seed,
    marked external-synthesis, spend durable on the row) or a 'failed' baseline
    (honest refusal — never fabricated). On a budget skip NO row is written and
    the 'pending' intent stands for a later run. `chat(key, prompt) -> (raw,
    charged[, shadow])` is injectable so the suite spends nothing; a 2-tuple
    chat still works and its shadow defaults to charged (NL-95 tolerant
    unpack, the api-lane invariant)."""
    from . import memory_core as mc
    res = BaselineGenResult(thread_id=thread_id, topic=topic, outcome="failed")
    chat = chat or _default_baseline_chat
    try:
        template = (paths.PROMPTS_DIR / "thread_baseline.txt").read_text(
            encoding="utf-8")
    except OSError as exc:
        res.outcome = "failed"
        res.detail = f"baseline prompt unreadable ({exc}) — nothing generated"
        return res
    prompt = template
    for k, v in {"topic": topic, "note": (note or "").strip() or "(none)",
                 "date": date, "date_human": mc.human_date(date)}.items():
        prompt = prompt.replace("{" + k + "}", v)

    from . import analysis
    est = analysis.estimate_synthesis_usd(prompt)
    if est > remaining_usd:
        res.outcome = "skipped-budget"
        res.detail = (f"baseline estimate ${est:.4f} exceeds remaining "
                      f"${remaining_usd:.4f} — pending intent kept, nothing written")
        return res
    try:
        raw, cost, *rest = chat(key, prompt)
        shadow = rest[0] if rest else cost
    except Exception as exc:  # noqa: BLE001 — degrade to an honest failed row
        # A transport failure is a FAILURE, not a "stale" (there is no prior
        # baseline to keep — unlike a state rewrite; the misleading borrowed name
        # is dropped). The row lands 'failed'; the spend (if any billed) rides on
        # the result (BUG-32 money-honesty class). NL-95: the shadow figure
        # rides too, mirroring memory_core.rewrite_state's exception arm — a
        # failed subscription-lane attempt still consumed cap headroom.
        res.cost_usd = float(getattr(exc, "usd_spent", 0.0) or 0.0)
        res.shadow_usd = float(getattr(exc, "usd_shadow", res.cost_usd) or 0.0)
        res.outcome = "failed"
        res.detail = f"baseline call failed ({type(exc).__name__}: {exc})"
        mc.record_baseline(
            con, thread_id, date, mc.BASELINE_STATUS_FAILED, reason=res.detail,
            model=analysis.ANALYSIS_MODEL, cost_usd=res.cost_usd)
        return res
    res.cost_usd = cost
    res.shadow_usd = shadow
    # Post-paid: never let the spend escape as an exception (BUG-32 money-honesty
    # class) — every path below records a row carrying the cost.
    try:
        try:
            bg, seed, cites = _validate_baseline(raw)
        except BaselineRejected as exc:
            res.outcome = "rejected"
            res.detail = f"baseline rejected ({exc}) — failed row recorded, not fabricated"
            mc.record_baseline(
                con, thread_id, date, mc.BASELINE_STATUS_FAILED, reason=res.detail,
                cites=None, model=analysis.ANALYSIS_MODEL, cost_usd=cost)
            return res
        mc.record_baseline(
            con, thread_id, date, mc.BASELINE_STATUS_READY, backgrounder=bg,
            state_seed=seed, cites=cites,
            reason="auto (NL-77 backgrounder): external-synthesis founding floor",
            model=analysis.ANALYSIS_MODEL, cost_usd=cost)
        res.outcome = "written"
        res.detail = (f"backgrounder {len(bg.split())} words, "
                      f"{len(cites)} cite(s), state seed "
                      f"{len(mc._sentences(seed))} sentence(s)")
        return res
    except Exception as exc:  # noqa: BLE001 — never lose a paid baseline's spend
        res.outcome = "failed"
        res.detail = (f"baseline write failed after a paid call "
                      f"({type(exc).__name__}: {exc}) — spend recorded on result")
        return res


@dataclass
class BaselineBackfillReport:
    """NL-77 retroactive-baseline outcome. `refused` is the honest no-op when
    nothing awaits a baseline (every cold-start thread already floored)."""
    refused: bool = False
    reason: str = ""
    cap: float = 0.0
    # NL-95: spent_usd is the CAP figure (SHADOW) — it is what `cap - spent`
    # is computed from, and it matches its state-family sibling
    # (StateRepairReport.spent_usd, which has been the shadow figure since
    # R-B3a). charged_usd is the real money. In-memory only; never persisted,
    # so no historical row's meaning moves — but cli.py's memory-baseline
    # printer is a reader of spent_usd and moves in the same change.
    spent_usd: float = 0.0
    charged_usd: float = 0.0
    generated: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def run_baseline_backfill(
    thread_id: Optional[int] = None, all_threads: bool = False,
    con: Optional[sqlite3.Connection] = None, env: Optional[dict] = None,
    date: Optional[str] = None, chat=None,
) -> BaselineBackfillReport:
    """NL-77 the retroactive-baseline command driver (and the single-thread
    materializer for a just-followed thread). Generates the entry-zero
    backgrounder for followed threads with an EMPTY ledger and no ready baseline.
    EXACTLY ONE selector: `thread_id=N` (one thread) or `all_threads=True` (sweep
    the backlog). Cap pre-checked; SPENDS one analyst-model call per thread;
    refuses when nothing awaits; disclose-don't-crash. Spend is durable on each
    thread_baselines row's cost_usd; spent_usd is the run total.

    IMPORTANT: the real-data sweep is a principal checkpoint (thread renames /
    deletes — the junk sweep — land BEFORE baselines). `chat` is injectable so
    the suite exercises this path without spending; the CLI runs it only with the
    principal's word."""
    import os

    if (thread_id is None) == (not all_threads):
        raise ValueError(
            "run_baseline_backfill needs EXACTLY ONE of thread_id / all_threads "
            f"(got thread_id={thread_id!r}, all_threads={all_threads!r})")

    src_env = env if env is not None else os.environ
    key = (src_env.get("OPENAI_API_KEY") or "").strip()
    date = date or ranking.local_today()
    rep = BaselineBackfillReport()

    own_con = con is None
    if own_con:
        db.migrate()
        con = db.connect()
    try:
        from . import memory_core
        if thread_id is not None:
            trow = con.execute("SELECT id, topic, principal_note, status FROM "
                               "memory WHERE id = ?", (thread_id,)).fetchone()
            if trow is None:
                rep.refused = True
                rep.reason = f"no thread with id {thread_id}"
                return rep
            # D3 (§F): the entry-zero genre is for FOLLOWED threads. A dismissed
            # thread is one the reader explicitly stopped — refuse BEFORE any cap
            # check or paid call (mirrors the --all lane's active/dormant filter
            # in threads_awaiting_baseline; never inferred back into wanting one).
            if trow["status"] == "dismissed_user":
                rep.refused = True
                rep.reason = (f"thread {trow['topic']!r} is dismissed — the reader "
                              "stopped following it; no cold-start backgrounder "
                              "(§F: nothing inferred from a dismissal)")
                return rep
            if con.execute("SELECT 1 FROM thread_deltas WHERE thread_id = ? LIMIT 1",
                           (thread_id,)).fetchone():
                rep.refused = True
                rep.reason = (f"thread {trow['topic']!r} already has a ledger record "
                              "— the entry-zero genre is for EMPTY-ledger cold starts")
                return rep
            if memory_core.ready_baseline(con, thread_id):
                rep.refused = True
                rep.reason = f"thread {trow['topic']!r} already has a ready baseline"
                return rep
            # Gate FIX-2: a standing pending intent's own date wins over the
            # run date — the identical rule threads_awaiting_baseline applies
            # (latest here is never 'ready'; the ready-refusal precedes).
            latest = memory_core.latest_baseline(con, thread_id)
            targets = [{"thread_id": thread_id, "topic": trow["topic"],
                        "note": trow["principal_note"] or "",
                        "as_of": latest.get("as_of_date") if latest else None}]
        else:
            awaiting = memory_core.threads_awaiting_baseline(con)
            if not awaiting:
                rep.refused = True
                rep.reason = ("no followed thread awaits a baseline — every "
                              "cold-start thread already has its founding floor")
                return rep
            targets = []
            for a in awaiting:
                trow = con.execute("SELECT principal_note FROM memory WHERE id = ?",
                                   (a["thread_id"],)).fetchone()
                targets.append({"thread_id": a["thread_id"], "topic": a["topic"],
                                "note": (trow["principal_note"] if trow else "") or "",
                                "as_of": a["as_of"]})

        cap = config.budget_cap_usd_per_run(src_env)
        rep.cap = cap
        spent = 0.0
        charged = 0.0
        for t in targets:
            as_of = t["as_of"] or date
            gr = generate_thread_baseline(
                con, t["thread_id"], t["topic"], t["note"], as_of, key,
                remaining_usd=cap - spent, chat=chat)
            # NL-95 ENFORCEMENT FIX #3 (and stale comment #1 corrected): the
            # comment here claimed the baseline rides "gpt-4o/api — not a
            # subscription seat", so charged == shadow and cost_usd was the cap
            # figure. That has been false since B4: the baseline rides the
            # ANALYST seat, which is Claude Sonnet 5 on the SUBSCRIPTION lane
            # with an armed api fall-over (llm.py). On that lane cost_usd is
            # $0, so `spent` never moved and the cap bound nothing at all —
            # a --all sweep over a long backlog was uncapped. The run cap binds
            # SHADOW (Onna's law); the charged figure rides the row.
            spent += gr.shadow_usd
            charged += gr.cost_usd
            rep.generated.append({"thread": t["topic"], "thread_id": t["thread_id"],
                                  "outcome": gr.outcome, "detail": gr.detail,
                                  "usd": round(gr.cost_usd, 6),
                                  "usd_shadow": round(gr.shadow_usd, 6),
                                  "as_of": as_of})
            if gr.outcome != "written":
                rep.warnings.append(
                    f"baseline for {t['topic']!r} {gr.outcome} — {gr.detail}")
        rep.spent_usd = round(spent, 6)
        rep.charged_usd = round(charged, 6)
        return rep
    finally:
        if own_con:
            con.close()


def _apply_memory_effects(
    con: sqlite3.Connection, briefing_id: int, slots_json: Optional[str]
) -> List[Dict]:
    """NL-108 — the memory side-effects of PUBLISHING an edition, applied at
    the promote and nowhere else.

    Continuity's spine (`update_references` -> the dormancy clock and the
    most-recently-referenced cap) and earned-slot auto-revival
    (`revive_matched`, dormant -> active) used to fire in `ranking.persist`, at
    rank time. Everything expensive and failure-prone happens after that point,
    so a run that died downstream had already advanced thread clocks and flipped
    dormant threads for an edition nobody ever read — and the NL-146 retry
    ladder could do it several times in one morning.

    The threads come from the slots being INSTALLED, not from the rank report:
    that is what ties the write to the published selection by construction. On
    the promote path those are the staged slots; on the plain path they are the
    slots already on the row, which is what a `--no-refresh` publish installs.
    Either way this reads the same JSON the reader's edition renders from.

    Called INSIDE the caller's publish transaction, so the memory writes commit
    if and only if the edition does. That closes the hole in both directions:
    no clock moves for an unpublished edition, and no published edition leaves
    its threads un-referenced (which would age them toward a premature
    dormancy). Returns the revivals actually applied [{topic, last_covered}].
    """
    try:
        slots = json.loads(slots_json or "[]")
    except ValueError:
        # A row whose slots JSON will not parse is a corrupt edition, but this
        # is the publish transaction — raising here would roll back a finished
        # edition over a sidecar. Publish it; the memory effects are simply not
        # derivable, and the next real edition re-references the threads.
        return []
    if not isinstance(slots, list):
        return []
    referenced: List[str] = []
    dormant_matched: List[str] = []
    for s in slots:
        if not isinstance(s, dict):
            continue
        referenced.extend(t for t in (s.get("matched_memory") or []) if t)
        dormant_matched.extend(t for t in (s.get("matched_dormant") or []) if t)
    if referenced:
        memory.update_references(con, briefing_id, referenced)
    if not dormant_matched:
        return []
    # Still filtered to status='dormant' inside revive_matched, so a thread the
    # principal dismissed between rank and publish is not resurrected by the
    # promote, and a thread some other path already revived is not re-dated.
    return memory.revive_matched(con, briefing_id, dormant_matched)


def persist_generation(
    con: sqlite3.Connection, date: str, narrative: str, script: str,
    steps: List[Dict], audio_path: Optional[str] = None
) -> List[Dict]:
    """Write narrative/script onto the briefing row. If a narrative already
    exists (re-generation), archive the row to briefings_history first —
    same rule persist() applies on re-rank.

    NL-106 — THE PROMOTE. When a regenerate staged a new selection at rank time
    (briefings_pending), this is where the swap happens, in ONE transaction:
    archive the displaced edition, install the staged slots together with the
    new body, drop the staged row. Before this instant the reader has the whole
    old edition; after it they have the whole new one; there is no instant in
    between. A run that dies before reaching here leaves the staged row behind
    and the reader's edition untouched — the next re-rank writes over it.

    NL-108 — THIS IS ALSO WHERE MEMORY MOVES. Thread reference dates and
    earned-slot revivals are applied here, in the same transaction, off the
    slots this edition installs (see _apply_memory_effects). Publication is the
    single trigger for every durable consequence of an edition. Returns the
    revivals applied, so the caller can announce a transition that has actually
    happened."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with con:
        row = con.execute("SELECT * FROM briefings WHERE date = ?", (date,)).fetchone()
        if row is None:
            raise GenerateError(f"briefing row for {date} vanished mid-run")
        pending = ranking.pending_selection(con, date)
        if pending is not None:
            # (a) Archive UNCONDITIONALLY. A staged row is only ever created
            # against a readable live row, so what is being displaced here is
            # always a real edition — and ADR-0001 wants exactly one history
            # row per replaced edition, carrying the OLD slots WITH the OLD
            # body (the pairing the old rank-time archive used to produce).
            con.execute(
                "INSERT INTO briefings_history (briefing_id, date, story_slots,"
                " corroboration_labels, narrative_text, script_text,"
                " audio_file_path, token_cost, generated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["id"], row["date"], row["story_slots"],
                 row["corroboration_labels"], row["narrative_text"],
                 row["script_text"], row["audio_file_path"],
                 row["token_cost"], row["generated_at"]),
            )
            # (b) The new ledger is built on the STAGED token_cost — the rank
            # step this edition actually paid for — NOT the live row's, which
            # belongs to the edition being displaced and has just been archived
            # with it. Folding the old edition's spend in here would invent
            # money against the new edition (NL-95: `usd` is real money);
            # dropping the staged base would lose the new rank step. Same fold
            # shape as the plain path below.
            try:
                staged_cost = json.loads(pending["token_cost"] or "{}")
            except ValueError:
                staged_cost = {}
            all_steps = (staged_cost.get("steps") or []) + steps
            total = round(sum(s.get("usd") or 0 for s in all_steps), 6)
            con.execute(
                "UPDATE briefings SET story_slots = ?, corroboration_labels = ?,"
                " narrative_text = ?, script_text = ?, audio_file_path = ?,"
                " token_cost = ?, generated_at = ? WHERE id = ?",
                (pending["story_slots"], pending["corroboration_labels"],
                 narrative, script, audio_path,
                 json.dumps({"steps": all_steps, "total_usd": total}), now,
                 row["id"]),
            )
            # (c) The staging row has done its job. Dropping it inside the same
            # transaction is what makes "staged" mean "not yet published".
            con.execute("DELETE FROM briefings_pending WHERE date = ?", (date,))
            # (d) NL-108: the edition is now the record, so its threads get
            # their reference date and their revivals — off the STAGED slots,
            # the ones just installed, in this same transaction.
            return _apply_memory_effects(con, row["id"], pending["story_slots"])
        if row["narrative_text"]:
            con.execute(
                "INSERT INTO briefings_history (briefing_id, date, story_slots,"
                " corroboration_labels, narrative_text, script_text,"
                " audio_file_path, token_cost, generated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["id"], row["date"], row["story_slots"],
                 row["corroboration_labels"], row["narrative_text"],
                 row["script_text"], row["audio_file_path"],
                 row["token_cost"], row["generated_at"]),
            )
        try:
            token_cost = json.loads(row["token_cost"] or "{}")
        except ValueError:
            token_cost = {}
        existing_steps = token_cost.get("steps") or []
        all_steps = existing_steps + steps
        total = round(sum(s.get("usd") or 0 for s in all_steps), 6)
        con.execute(
            "UPDATE briefings SET narrative_text = ?, script_text = ?,"
            " audio_file_path = ?, token_cost = ?, generated_at = ?"
            " WHERE id = ?",
            (narrative, script, audio_path,
             json.dumps({"steps": all_steps, "total_usd": total}), now, row["id"]),
        )
        # NL-108, plain path: the slots already on the row ARE this edition's
        # selection (this arm never rewrites them), including the --no-refresh
        # publish where rank ran in an earlier process.
        return _apply_memory_effects(con, row["id"], row["story_slots"])


def _fold_cost_steps(con: sqlite3.Connection, date: str,
                     steps: List[Dict]) -> None:
    """Append late-arriving steps (the post-persist memory pass) into the
    briefing row's token_cost WITHOUT re-archiving — persist_generation already
    published the edition; this only keeps the persisted cost total honest."""
    if not steps:
        return
    with con:
        row = con.execute("SELECT id, token_cost FROM briefings WHERE date = ?",
                          (date,)).fetchone()
        if row is None:
            return
        try:
            tc = json.loads(row["token_cost"] or "{}")
        except ValueError:
            tc = {}
        all_steps = (tc.get("steps") or []) + steps
        total = round(sum(s.get("usd") or 0 for s in all_steps), 6)
        con.execute("UPDATE briefings SET token_cost = ? WHERE id = ?",
                    (json.dumps({"steps": all_steps, "total_usd": total}), row["id"]))


def fold_late_steps(report: "GenReport") -> List[Dict]:
    """The failed-run money record: call_llm's raw per-attempt ledger plus the
    two stages that bill in their own modules (analysis, memory).

    THE LAW THIS FUNCTION EXISTS TO HOLD (NL-95): `usd` is REAL MONEY. The
    failed entry's `total_usd` is a plain sum over these rows' `usd`, so a
    shadow figure folded into that key would invent dollars the principal never
    spent — on the subscription lane, an entire failed run's worth of them. The
    shadow figure rides under its own key instead, and a row is emitted when
    EITHER figure is non-zero, so a $0-charged subscription stage can no longer
    vanish from the record entirely (the R-B3a reasoning, applied to the fold).

    Extracted from _run_generate's except arm at M2 so the law is testable
    without staging a mid-pipeline failure; the except arm calls this and
    nothing else builds that ledger.

    SCOPE, deliberately narrow (engineering-3 §2 row 10 ruled the ANALYSIS row
    only): the memory row is untouched — same single `usd` key, same
    emit-if-charged condition it has had since NL-63. That leaves a known
    asymmetry: on a subscription-lane state seat memory_usd is $0 while
    memory_shadow_usd is not, so a failed run's memory spend still vanishes
    from this record entirely. report.memory_shadow_usd already exists and the
    fix is the same two lines applied one tuple down — flagged as a candidate,
    NOT taken here, because widening a money-record contract past what was
    adjudicated is the kind of quiet scope drift this fold is being fixed for."""
    ledger = list(report.attempt_ledger)
    # Analysis: BOTH figures, and emitted when EITHER is non-zero — on the
    # subscription lane charged is $0, and an emit-if-charged condition would
    # make the shadow key unreachable on the only lane where it differs.
    if report.analysis_usd or report.analysis_shadow_usd:
        ledger.append({"step": "analysis",
                       "usd": round(report.analysis_usd, 6),
                       "usd_shadow": round(report.analysis_shadow_usd, 6)})
    if report.memory_usd:
        ledger.append({"step": "memory", "usd": round(report.memory_usd, 6)})
    return ledger


# ---------------------------------------------------------------------------
# NL-154 — the log's own files: where they are, how they rotate, who reads them
# ---------------------------------------------------------------------------

# EVERY HELPER TAKES AN OPTIONAL DIRECTORY, because the log is PROFILE-SCOPED
# and one of its readers proves it: readerserve.read_ledger reads
# `profile_layout(slug)["DATA_DIR"]`, not `paths.DATA_DIR`, so a segment
# enumerator hard-wired to the process's own profile would have sent the reader
# worlds' spend ledger looking in the founder's directory. Default None = this
# process's profile, which is what every other caller wants.
def log_file(data_dir: Optional[Path] = None) -> Path:
    """The LIVE log. Every writer appends here; rotation is what keeps it from
    growing forever."""
    return (data_dir or paths.DATA_DIR) / GENERATION_LOG_NAME


def log_archives(data_dir: Optional[Path] = None) -> List[Path]:
    """Archive segments, OLDEST FIRST. Sorted by name, which is sorted by cut
    order because the numbers are zero-padded — a lexical sort on a padded
    counter is a chronological sort, and it stays one without a stat call per
    file (mtimes lie after a copy; the false-mtime receipt class is on record,
    Records 08-13-2 G-2)."""
    try:
        return sorted((data_dir or paths.DATA_DIR).glob(LOG_ARCHIVE_GLOB))
    except OSError:
        return []


def log_segments(data_dir: Optional[Path] = None) -> List[Path]:
    """Every segment of the record, oldest first, LIVE LAST — the reading order
    for anything that wants the whole history (diagnose's totals, the spend
    ledger). Appending order across the list is the log's original append order,
    so a caller that concatenates gets the record back exactly as it was written.

    THE UNION IS DISJOINT BY CONSTRUCTION: `rotate_log_if_needed` writes the
    lines it drops into the archive and the lines it keeps into the live file,
    never both. So a reader may concatenate segments without dedup, and no entry
    is counted twice.

    AND THE CONCATENATION IS ROTATION-INVARIANT, which is the property
    readerserve's session delta leans on: rotation MOVES lines between segments
    without reordering or altering them, so `"".join(segments)` is byte-identical
    before and after a cut. A session that rotates mid-flight still sees its own
    appends as a clean suffix, not as "the ledger changed shape"."""
    return log_archives(data_dir) + [log_file(data_dir)]


def is_run_line(entry: Dict) -> bool:
    """A RUN entry, as opposed to the analysis stage's instrumentation line or
    NL-146's fire-decision line.

    KEY PRESENCE, never line shape — the file's own idiom, and the same test
    server._run_log_entries applies. The schedule key is imported from its owner
    rather than re-spelled here: a second spelling of that discriminator is a
    rotation that counts fire lines as runs while the reports screen does not.

    PUBLIC since NL-155 (2026-08-14), and for that exact reason: `readerserve`
    was the last reader of generation_log.jsonl with no discriminator at all,
    and the fix is to CALL this one rather than grow a fourth spelling of it.
    A private name on the org's shared predicate was quietly arguing for
    duplication."""
    from . import schedule
    if not isinstance(entry, dict):
        return False
    return (entry.get("stage") is None
            and entry.get(schedule.SCHEDULE_LINE_KEY) is None)


def read_log_index() -> Dict:
    """The archive counts, or an honest empty. Never raises."""
    try:
        data = json.loads((paths.DATA_DIR / LOG_INDEX_NAME)
                          .read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def count_runs_in(segments: List[Path]) -> int:
    """RUN entries actually sitting in those files, read line by line.

    THE REBUILD PRIMITIVE. Unreadable segments are skipped rather than raising:
    this runs on the recovery path, and a recovery that dies on one bad file
    recovers nothing."""
    total = 0
    for p in segments:
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        total += sum(1 for ln in body.splitlines()
                     if ln.strip() and _safe_is_run(ln))
    return total


def archived_run_count() -> int:
    """How many RUN entries live in archive segments.

    DERIVED AND REBUILDABLE, never authoritative: every number in the index can
    be recomputed by reading the segments, and THIS FUNCTION IS WHERE THAT
    PROMISE IS KEPT (QA F-1, 2026-08-14 — the docstring made the claim and no
    code honoured it, so a lost index answered 0 and the next rotation re-based
    from that 0, compounding the loss on every subsequent cut).

    THE INDEX IS A CACHE, READ FIRST, because it is one small file and the
    steady-state page build must stay one bounded read no matter how much
    history accumulates. It is trusted only while it is CREDIBLE, which is a
    two-part test:

      - it parses and carries a non-negative integer count, and
      - the segments it lists are EXACTLY the segments on disk.

    The second half is the one that matters in practice, and it is an EQUALITY
    rather than a containment in either direction, because the set can drift
    both ways. The index write is deliberately best-effort and LAST (a full disk
    must not cost the run record), so one reachable failure is "a segment landed
    and the index did not hear about it". The other is his hands: SETUP.md
    invites him to delete old segments, and a subset test would have called the
    surviving segments credible against a total that still counted the deleted
    one — a permanent over-report that the NEXT rotation bakes into a fresh
    index (QA F-15 / gate R-B, 2026-08-14; measured 280 reported against 140 on
    disk, then 420 against 280). Both directions are detectable for the price of
    the glob this function already needs.

    Otherwise the count is RECOMPUTED from the segments: slower, rare, and
    right — and self-healing, because the next rotation writes its `prior` from
    the recount.

    THE BOUND, stated once so it is not mistaken for a stronger promise: what is
    detected is SET DRIFT. An index whose count is forged over a segment set
    that still matches disk, or a segment whose CONTENTS were edited in place
    while its name stayed, are trusted until that set changes. Closing those
    would take per-segment counts, which is real machinery for an informational
    row on a one-user app and is not reachable by any operation the product
    invites."""
    segments = log_archives()
    if not segments:
        # No archives, nothing archived. Also the pre-first-rotation state,
        # which is every profile's state until the log gets fat.
        return 0
    idx = read_log_index()
    try:
        cached = int(idx.get("archived_runs"))
    except (TypeError, ValueError):
        cached = None
    listed = idx.get("segments")
    accounts_for_disk = (isinstance(listed, list)
                         and {p.name for p in segments} == set(listed))
    if cached is not None and cached >= 0 and accounts_for_disk:
        return cached
    return count_runs_in(segments)


def _split_index(lines: List[str], retain_runs: int) -> int:
    """Index of the first line to KEEP, so that `retain_runs` run entries remain.

    Returns 0 when the file does not hold more than `retain_runs` runs — i.e.
    "there is nothing safe to archive", which is the answer that protects the
    retention floor on a file that is huge for some other reason (a burst of fat
    stage lines, one pathological run). Rotation is skipped rather than forced.

    Walks from the END, because retention is defined from the newest run
    backwards and the file is append-ordered."""
    seen = 0
    for i in range(len(lines) - 1, -1, -1):
        raw = lines[i]
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            # A torn or hand-mangled line is carried with whatever side of the
            # split it falls on; it is never a run for counting purposes and it
            # is never dropped.
            continue
        if is_run_line(entry):
            seen += 1
            if seen > retain_runs:
                # This line is one run PAST the retention floor, so the keep
                # window starts on the line after it.
                return i + 1
    return 0


def rotate_log_if_needed(max_bytes: int = LOG_MAX_BYTES,
                         retain_runs: int = LOG_RETAIN_RUNS) -> Optional[Path]:
    """Move the oldest lines into a fresh archive segment. Returns the segment
    written, or None when nothing needed moving.

    NEVER RAISES INTO A GENERATE. This is called on the way into a log append,
    and the append is frequently the last act of a ~30-minute pipeline that
    already succeeded. Losing that record because housekeeping hit a full disk
    would be the tail wagging the dog, so every failure arm here returns None
    and leaves the log exactly as it was — un-rotated is a slow file, which is
    the condition we started in.

    THE CRASH DIRECTION IS DUPLICATION, NEVER LOSS, WITH ONE WRITER PER
    DATA_DIR — stated because it is the one property worth choosing, and
    qualified because the unqualified sentence reads as a concurrency guarantee
    it does not make (QA F-2 / gate R-D, 2026-08-14). The archive is written and
    swapped into place BEFORE the live file is replaced by its retained tail, so
    a kill between the two `os.replace` calls leaves the archived lines present
    in both files. That is visible, recoverable and harmless to every count
    except a naive union; the opposite order would have a window in which those
    lines exist nowhere.

    WHAT THE QUALIFIER EXCLUDES: this is read-all -> write-archive ->
    os.replace, and a line appended by ANOTHER PROCESS on the same DATA_DIR
    inside that window is overwritten by the replace. Bound: one line, and only
    for a second writer the in-flight marker did not stop. Not closed with a
    lock — real machinery, on a one-user app, for a window behind a guard —
    and pinned as a documented bound instead (xfail, strict) so the day anyone
    lands the lock the pin passes and forces the marker off.
    """
    import os   # module-local, matching this file's three other os users

    live = log_file()
    try:
        if live.stat().st_size <= max_bytes:
            return None
    except OSError:
        return None
    try:
        text = live.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    lines = text.splitlines()
    cut = _split_index(lines, retain_runs)
    if cut <= 0:
        return None

    dropped, kept = lines[:cut], lines[cut:]
    existing = log_archives()
    try:
        nxt = int(existing[-1].name[len(LOG_ARCHIVE_PREFIX):]
                  .split(".")[0]) + 1 if existing else 1
    except (ValueError, IndexError):
        nxt = len(existing) + 1
    segment = paths.DATA_DIR / f"{LOG_ARCHIVE_PREFIX}{nxt:04d}{LOG_ARCHIVE_SUFFIX}"

    # THE RUNNING TOTAL IS TAKEN BEFORE THE CUT LANDS, and the ordering is the
    # whole fix (QA F-1). `archived_run_count` now rebuilds from the segments
    # when the index is not credible — so it must be asked while the segments
    # still hold only the PRIOR cuts. Asked after, the rebuild would count the
    # lines we are about to add to `moved` and the total would double.
    prior = archived_run_count()

    def _atomic(path: Path, body: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)

    try:
        _atomic(segment, "\n".join(dropped) + "\n")
        _atomic(live, ("\n".join(kept) + "\n") if kept else "")
    except OSError:
        return None

    # The index, refreshed from what was just cut. Written LAST and best-effort:
    # it is derived data, and a failed write now costs nothing but speed —
    # `archived_run_count` notices that the new segment is unaccounted for and
    # recomputes from disk until a later rotation writes a credible index again.
    moved = sum(1 for ln in dropped if ln.strip() and _safe_is_run(ln))
    try:
        _atomic(paths.DATA_DIR / LOG_INDEX_NAME, json.dumps({
            "archived_runs": prior + moved,
            "segments": [p.name for p in log_archives()],
            "rotated_at": datetime.now(timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, indent=2) + "\n")
    except OSError:
        pass
    return segment


def _safe_is_run(raw: str) -> bool:
    try:
        return is_run_line(json.loads(raw))
    except ValueError:
        return False


def log_generation(entry: Dict) -> None:
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # NL-154: ONE rotation site, and it is here because this is the ONE funnel
    # every fat line goes through — generate's own run records and NL-146's
    # fire decisions (schedule.log_fire calls this). analysis.py's stage line
    # has its own appender and is deliberately NOT a second rotation site: two
    # rotators racing over one file is a hazard, stage lines are the small ones
    # (1.7 KB mean, measured), and every stage line is followed by the run entry
    # whose append rotates for it.
    rotate_log_if_needed()
    log_path = paths.DATA_DIR / GENERATION_LOG_NAME
    entry = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **entry}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def write_artifact(date: str, variant: str, sample: bool, narrative: str,
                   script: str, no_threads: bool = False) -> Path:
    out_dir = paths.DATA_DIR / BRIEFINGS_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    if not sample:
        name = f"{date}.md"
    elif no_threads:
        name = f"{date}-no-threads-SAMPLE.md"
    else:
        name = f"{date}-variant-{variant}-SAMPLE.md"
    path = out_dir / name
    if no_threads:
        header = (
            "<!-- SAMPLE — no active threads (cold-start view); not the "
            "briefing of record -->\n\n"
        )
    elif sample:
        header = (
            f"<!-- SAMPLE — variant {variant} for comparison; NOT the briefing "
            "of record for this date -->\n\n"
        )
    else:
        header = ""
    path.write_text(
        header + narrative
        + "\n\n---\n\n## Podcast script (feeds M6 audio; not part of the read briefing)\n\n"
        + script + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# NL-88: live-progress side-channel (PURE OBSERVABILITY — no pipeline change)
#
# run_generate accepts an OPTIONAL progress(label, model) callback and fires it
# at each phase boundary the run ALREADY passes through. The one property that
# MUST hold: a progress callback can NEVER affect a generation. Every emit goes
# through _emit_progress, which is a no-op when progress is None and SWALLOWS
# any exception (a bad callback, a seat lookup that raises). Generation
# behavior, output, cost, and ordering are byte-identical whether or not a
# callback is passed. The internal phase key -> plain, non-engineer label map
# lives HERE, in one place; both the web UI and the CLI render these labels.
# ---------------------------------------------------------------------------

PROGRESS_LABELS: Dict[str, str] = {
    "ingest":    "Gathering the news",
    "rank":      "Ranking stories",
    "analysis":  "Reading the stories closely",
    "narrative": "Writing the briefing",
    "editor":    "Editing",
    "script":    "Adapting the script",
    "audio":     "Making the audio",
    "persist":   "Saving",
    "state":     "Updating the story threads",
}


def _emit_progress(progress: Optional[Callable[[str, Optional[str]], None]],
                   phase: str, seat: Optional[str] = None,
                   env: Optional[dict] = None) -> None:
    """Fire the live-progress side-channel for one phase boundary (NL-88).

    NON-INTERFERING by construction: a None `progress` returns immediately (a
    no-op code path), and any `Exception` raised by the callback OR by the
    seat/model lookup is swallowed here — no ordinary progress error can abort,
    slow, reorder, or alter a generation. The swallow is deliberately
    `except Exception`, NOT `BaseException`: a KeyboardInterrupt/SystemExit
    raised while a callback runs PROPAGATES on purpose, so a ~40-min generate
    stays interruptible (a Ctrl-C must land the same whether it hits inside the
    callback or one instruction later). The real callbacks — _GenJob._progress
    and the CLI printer — never raise, so output, cost, and ordering are
    byte-identical whether or not a callback is passed."""
    if progress is None:
        return
    try:
        label = PROGRESS_LABELS.get(phase, phase)
        model: Optional[str] = None
        if seat is not None:
            try:
                model = llm.resolve_seat(seat, env).model
            except Exception:  # noqa: BLE001 — a seat lookup never affects a run
                model = None
        progress(label, model)
    except Exception:  # noqa: BLE001 — a progress error NEVER touches generation
        pass


# NL-149 QA F-2: the grid the two timing surfaces floor onto. Kept as a
# timedelta so the floor is exact integer arithmetic on microseconds.
# server._GenJob._close_stage_locked carries the same constant by value and the
# same rule — tests/test_nl149_fixloop1.py pins that they agree.
_TENTH = timedelta(seconds=0.1)


def _utc_stamp() -> str:
    """A stage/run start stamp, MILLISECOND-precise (NL-149 QA F-2).

    It used to format "%Y-%m-%dT%H:%M:%SZ" — throwing the sub-second part of
    every start away, so `_wall_seconds_since` then measured from an instant up
    to a second EARLIER than the stage began. Measured over 12 samples of 0.05s
    of real work: mean +0.550s, always positive. The run total absorbs that
    once; an N-stage run absorbs it N times, so the saved report could say a run
    took less than its own steps add up to — on the one surface whose whole job
    is that arithmetic. Still ends with "Z", and `fromisoformat` parses the
    fractional form unchanged."""
    return (datetime.now(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")


def _wall_seconds_since(iso: str) -> float:
    """Wall seconds from an iso Z stamp to now, FLOORED to 0.1s, never negative.

    Wall clock and not `time.monotonic` deliberately: the live surface
    (server._GenJob) already computes its elapsed this way from the same kind of
    stamp, and one timing concept across the two halves of NL-149 is worth more
    than immunity to a mid-run clock adjustment on a personal machine. The floor
    at 0 is what a backwards clock costs us: an understated stage, never a
    negative duration on screen.

    FLOOR, NOT ROUND (NL-149 QA F-2, and the half a millisecond stamp does not
    close). The stage windows are contiguous, non-overlapping and nested inside
    the run window, so `sum(steps) <= total` is arithmetic, not preference.
    Sub-second stamps alone do not deliver it: with round(), each stage can
    round UP by 0.05s while the total rounds down by 0.05s. Simulated over 2,000
    runs per cell, that residual is 30.3% of ten-stage/two-minute runs (99.9%
    when the stages are ~0.06s) — a tenth of the old error, still a record that
    contradicts itself. Flooring is structural rather than merely smaller,
    because floor(a) + floor(b) <= floor(a + b) on a fixed grid: the parts can
    never add to more than the whole, at any stage count or duration (measured
    0.0% across every cell). It also matches how the number is READ —
    server._fmt_elapsed and webui.genFmt both truncate to the second — and it
    keeps this helper's stated direction: understate a stage, never overstate
    one. server._GenJob._close_stage_locked floors by the same rule, so the live
    panel and the saved report cannot disagree about the same step.

    The floor runs on the timedelta's EXACT microseconds rather than on
    `total_seconds() * 10`: that product is a float and 2.675 * 10 is
    26.749999999999996, so a float floor would silently drop a tenth on
    arbitrary durations — the one arithmetic error this fix exists to remove."""
    try:
        delta = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(iso.replace("Z", "+00:00")))
        return max(0.0, (delta // _TENTH) / 10)
    except Exception:  # noqa: BLE001 — a clock read never breaks a generation
        return 0.0


def timeline_recorder(report: GenReport,
                      inner: Optional[Callable[[str, Optional[str]], None]]
                      ) -> Callable[[str, Optional[str]], None]:
    """Wrap the caller's progress callback so every phase boundary is RECORDED
    as well as announced (NL-149 item 2).

    WHY A WRAPPER AND NOT A REPORT ARGUMENT ON `_emit_progress`: the nine emit
    sites would each have had to remember to pass it, and a site that forgot
    would go silently un-timed — the unasserted-no-op class ENGINEERING.md's
    wiring rule exists for. Wrapping once, in run_generate, means the timeline
    covers exactly the boundaries the progress channel covers, by construction,
    for every caller: the web job, the CLI printer, AND `progress=None` (which
    is why the wrapper is installed unconditionally — a terminal `newslens
    generate` must land in the settings report like any other run).

    NON-INTERFERENCE IS PRESERVED IN BOTH DIRECTIONS. The recording is inside
    its own swallow, so a broken clock cannot stop the caller's callback from
    firing; and the caller's callback is called AFTER the record, so a raising
    callback (the pinned `boom` case) cannot stop the recording. The whole
    wrapper still runs inside `_emit_progress`'s own `except Exception`, so
    neither half can reach the generation."""
    def _record(label: str, model: Optional[str]) -> None:
        try:
            close_open_stage(report)
            report.stage_timeline.append({
                "label": label, "model": model,
                "started_at": _utc_stamp(), "elapsed_s": None,
            })
        except Exception:  # noqa: BLE001 — never touches the generation
            pass
        if inner is not None:
            inner(label, model)
    return _record


def close_open_stage(report: GenReport) -> None:
    """Stamp the elapsed of the stage currently open, if any. A stage's duration
    is measured boundary-to-boundary — the same span the live clock counts as
    'on this step' — so the LAST stage's duration runs to the close call at the
    end of the run and therefore includes the post-`state` tail (artifact write,
    watch register). That is stated rather than hidden: it is the honest reading
    of 'time on the last step' when the last step has no successor."""
    if not report.stage_timeline:
        return
    last = report.stage_timeline[-1]
    if last.get("elapsed_s") is None:
        last["elapsed_s"] = _wall_seconds_since(last.get("started_at") or "")


def close_stage_timeline(report: GenReport) -> None:
    """End-of-run: close the open stage and stamp the whole-run elapsed. Called
    on BOTH log arms (ok and failed/paused) — a run that ends without an edition
    is exactly the run whose timeline the reader wants."""
    close_open_stage(report)
    if report.run_started_at:
        report.run_elapsed_s = _wall_seconds_since(report.run_started_at)


def _analysis_pause_class() -> type:
    """`analysis.SystemicFetchFailure`, resolved lazily.

    A function rather than a module-level import because the dependency runs
    ONE way — generate imports analysis, never the reverse — and every other
    use of analysis in this module is a local import inside the run body for
    that same reason. Naming the class in an `except` clause at module scope
    would be the one line that inverts it."""
    from . import analysis
    return analysis.SystemicFetchFailure


def is_retryable(exc: BaseException) -> bool:
    """Is this failure the "back off and try again" kind, or the "this run is
    broken" kind? (NL-146 — the fork the NL-148 seam was left for.)

    NAMED ONCE, deliberately. The failed-run log arm below stamps
    `retryable: true` from this predicate and `schedule.run_scheduled` turns its
    whole ladder on it — one rule, two readers. The alternative (the log arm
    keeping an inline isinstance while the scheduler writes its own copy) is two
    versions of a money-shaped rule that drift apart in one refactor, and the
    direction they drift is the expensive one: a scheduler that thinks a broken
    run is retryable re-bills a whole pipeline unattended, at 6am, unwatched.

    TRUE FOR THE SYSTEMIC FETCH PAUSE ONLY. That pause is the one failure which
    (a) is caused by something outside this machine and is plausibly transient,
    and (b) is PROVEN to have published nothing and completed no story — NL-148
    pins `any_valid_brief` False and `SUM(cost_usd) == 0` at the instant it
    fires, so a retry has no completed work to re-bill. Every other
    GenerateError may have spent real money in the tail (the editor, both script
    attempts), and retrying one of those unattended spends it twice."""
    return isinstance(exc, _analysis_pause_class())


def run_generate(
    date: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
    env: Optional[dict] = None,
    variant_override: Optional[str] = None,
    refresh: bool = True,
    no_threads: bool = False,
    progress: Optional[Callable[[str, Optional[str]], None]] = None,
    trigger: Optional[str] = None,
) -> GenReport:
    """`trigger` is PROVENANCE, not behaviour (NL-146 item 4). It names who
    started this run — "scheduled" (launchd fired it while nobody watched) or
    "interactive" (he pressed the button / typed the verb) — and rides into BOTH
    log arms so the reports screen can say which. Nothing in the pipeline reads
    it; a scheduled run and an interactive run of the same date are the same
    run.

    DEFAULT IS None, AND THAT IS THE HONEST DEFAULT. Defaulting to
    "interactive" would stamp that word on the battery, the backfills and every
    scripted caller, which is a claim about a human that was never true. An
    absent key means unrecorded, the reports screen renders nothing for it, and
    every run already in the log stays exactly as honest as it was — no count is
    quoted here on purpose (fix loop 1, QA F-6: the figure that used to sit in
    this sentence was never measured against the file)."""
    import os

    src_env = env if env is not None else os.environ
    date = date or ranking.local_today()
    key = (src_env.get("OPENAI_API_KEY") or "").strip()
    # NOTES 28a (keyless-refusal log asymmetry): the check itself moved into
    # the logged region below — a keyless refusal now lands in
    # generation_log.jsonl exactly like every other failed run, instead of
    # being the one failure the record never saw.

    scheduled = ACTIVE_VOICE  # A1: alternation ended; A is the voice of record
    variant = (variant_override or scheduled).upper()
    if variant not in ("A", "B"):
        raise GenerateError(f"variant must be A or B, got {variant!r}")
    sample = (variant != scheduled) or no_threads
    if sample and refresh:
        # M5 gate finding 1: a sample must NEVER mutate the briefing of
        # record — the refresh chain's rank persist archives and NULLs the
        # record narrative before the sample renders. Samples always consume
        # the existing row; a plain `generate` is how the record refreshes.
        refresh = False
    report = GenReport(date=date, variant=variant, sample=sample)
    if sample:
        report.warnings.append(
            "sample request: refresh chain skipped — the briefing of record "
            "is untouched (run a plain `generate` to refresh the record)"
        )
    report.no_threads = no_threads
    report.trigger = (trigger or "").strip()      # NL-146 provenance; see above
    if no_threads:
        report.warnings.append(
            "no-threads SAMPLE (cold-start view): thread/memory context "
            "emptied, tags kept — rendered to a file, briefings row untouched"
        )
    if variant != scheduled:
        report.warnings.append(
            f"voice {variant} is retired (editorial review A1; {scheduled} is "
            "the voice of record) — SAMPLE mode: rendered to a file, the "
            "briefing of record untouched"
        )

    # NL-149 item 2: the run's own clock starts HERE — before the first phase
    # boundary — so the persisted total covers the whole run and not just the
    # part after the first emit. Installed unconditionally (see
    # timeline_recorder): a CLI run passes a printer, the web job passes its
    # stamper, a scripted run passes None, and all three land in the report.
    report.run_started_at = _utc_stamp()
    progress = timeline_recorder(report, progress)

    own_con = con is None
    if own_con:
        db.migrate()
        con = db.connect()
    try:
        try:
            return _run_generate_body(
                con, date, src_env, key, report, refresh, no_threads, progress
            )
        except (GenerateError, _analysis_pause_class()) as exc:
            # BUG-6/32 family (NL-63 M2 obs): a run that aborts mid-pipeline
            # still spent real money — narrative, the editor,
            # and BOTH script attempts on a degenerate-stub abort all bill before
            # the raise. Fold that accumulated spend into the failed entry so
            # the money record is never a silent null. attempt_ledger is
            # call_llm's raw per-attempt cost record; the analysis stage runs
            # in its own module, so its spend is folded from report.analysis_usd
            # (and any pre-abort memory spend from report.memory_usd).
            #
            # NL-148 clause 4 joins this arm rather than propagating unlogged:
            # the pause is a run that ended without an edition, and "the one
            # failure the record never saw" is the asymmetry the keyless-refusal
            # note above exists to forbid.
            ledger = fold_late_steps(report)
            close_stage_timeline(report)
            entry = {"date": date, "variant": variant, "sample": sample,
                     "status": "failed", "error": str(exc)[:500],
                     # NL-146: provenance, emitted only when the caller named it
                     # (see run_generate's docstring on the None default).
                     **({"trigger": report.trigger} if report.trigger else {}),
                     "steps": ledger,
                     "total_usd": round(
                         sum(s.get("usd") or 0 for s in ledger), 6),
                     # NL-149 item 2: a failed run keeps its timeline. The
                     # settings report renders failures too — a run that died in
                     # the editor after 22 minutes is the single most useful row
                     # the log can hand the reader, and NL-146's unattended runs
                     # will produce them unwatched.
                     "stage_timeline": report.stage_timeline,
                     "elapsed_s": report.run_elapsed_s,
                     "warnings": report.warnings}
            # THE SEAM NL-146 INHERITS (and the reason `status` does NOT move):
            # every existing consumer reads status=='failed', so the pause keeps
            # that word and adds a marker beside it. A scheduled run reads THIS
            # bit to tell "retryable — nothing fetched, back off and try again"
            # from "this run is broken", which is the fork its ladder (auto-retry
            # w/ backoff -> yesterday's edition + quiet note) turns on. NL-146
            # builds the ladder; this milestone only makes the fork legible.
            #
            # NL-146 LANDED THE LADDER, and the inline isinstance that used to
            # sit here moved into `is_retryable` above — so the bit this entry
            # carries and the bit `schedule.run_scheduled` acts on are the SAME
            # predicate evaluated once, not two copies of it.
            if is_retryable(exc):
                entry["paused"] = "fetch"
                entry["retryable"] = True
            log_generation(entry)
            raise
    finally:
        # B3-D6: guaranteed teardown of the run-scoped writer-family resolutions
        # (_run_generate_body publishes them; this wraps that body 1:1) — always
        # reset, so a raise mid-run never leaks a stale (cfg, reason) into the
        # next run's steps.
        global _ACTIVE_STEP_SEATS
        _ACTIVE_STEP_SEATS = {}
        # FIX-1 (B4-D1): clear the analysis stage's published analyst resolution
        # (generate's stage-entry preflight publishes it; run_analysis reuses it)
        # so a raise mid-run never leaks a stale (cfg, reason) into the next run.
        from . import analysis as _analysis_td
        _analysis_td._clear_analyst()
        if own_con:
            con.close()


def _run_generate_body(
    con: sqlite3.Connection, date: str, src_env, key: str,
    report: GenReport, refresh: bool, no_threads: bool = False,
    progress: Optional[Callable[[str, Optional[str]], None]] = None,
) -> GenReport:
    from . import ingest

    # A″ (2026-07-17, keyless-OpenAI audit): the blanket "OPENAI_API_KEY not set
    # -> refuse the whole run" check is GONE — it was written when every seat was
    # gpt-4o. Post-B4 rank/editor/script/writer/analyst are anthropic and the
    # OpenAI key is inert for them, so a keyless-OpenAI generate runs the full
    # content path on the anthropic lanes. The ONLY seat that still needs the
    # OpenAI key is `state` (gpt-4o); its keyless requirement is enforced,
    # provider-aware and fail-loud, at the state stage preflight below (not as a
    # blanket that pre-empts every anthropic stage).

    if refresh:
        _emit_progress(progress, "ingest", env=src_env)
        try:
            ing = ingest.run_ingest(con=con, env=src_env)
        except config.SourcesParseError as exc:
            raise GenerateError(str(exc)) from exc
        report.ingest_summary = (
            f"{len(ing.succeeded)}/{ing.attempted} sources; "
            f"{ing.items_new} new items; discovery: {ing.discovery_status}"
        )
        if ing.degradation_message:
            report.warnings.append(ing.degradation_message)
        _emit_progress(progress, "rank", "rank", env=src_env)
        try:
            rank_rep = ranking.run_rank(date=date, con=con, env=src_env)
        except ranking.RankingError as exc:
            raise GenerateError(f"rank stage failed: {exc}") from exc
        report.warnings.extend(rank_rep.warnings)

    # NL-106: THE mid-run read. The staged selection is what this run writes
    # about while the reader still holds the old edition. On a run with nothing
    # staged (first run, or a narrative-only re-run of an uninterrupted edition)
    # it reads the live row exactly as before.
    #
    # FIX-2 (gate, from QA-2): staged reads belong to runs that CAN PROMOTE. A
    # sample never reaches persist_generation (`if not report.sample` at the
    # persist block) and forces refresh=False above, so an unconditional
    # preference had it narrating a stale staged selection while its own warning
    # promised the reader "the briefing of record is untouched … samples always
    # consume the existing row". No data damage — but a sample that misreports
    # what it sampled is exactly the class of small lie this product does not get
    # to tell.
    inputs = load_briefing_inputs(con, date, prefer_pending=not report.sample)
    # FIX-3 (gate ruling R1): consuming a staged payload this run did not create
    # must never be silent. A refresh run consumes its OWN staging, seconds old —
    # nothing to disclose. A `--no-refresh` run consuming one is by definition
    # finishing an earlier, interrupted regenerate, and the principal is entitled
    # to know whose stories are about to be published under today's date.
    if not refresh and inputs.get("staged_created_at"):
        report.warnings.append(
            "completing an earlier interrupted regenerate: this run is "
            f"publishing the story selection ranked at "
            f"{inputs['staged_created_at']}, not a fresh one — run a plain "
            "`newslens generate` instead if you want today's stories re-ranked "
            "from scratch"
        )
    if no_threads:
        # Cold-start view (ADR-0007 amendment): tags stay; every thread/memory
        # trace is stripped from a COPY of the inputs — thread list, per-story
        # matched_memory, and revival data — so prompt, validators, assembly
        # meta-lines, and script labels are all consistently thread-free. The
        # persisted slots are untouched (samples never persist).
        inputs["threads"] = []
        # D4 (NL-75 QA): rung (a) attached `thread_ledger`/`expired_watch` in
        # load_briefing_inputs BEFORE this strip, so the copy must empty THEM too
        # — otherwise the cold-start sample's prompt ships the MEMORY block and
        # the EXPIRED WATCH-FOR conversion demand it just stripped its threads to
        # avoid (ADR-0007 amendment: 'every thread/memory trace is stripped').
        inputs["slots"] = [
            {**s, "matched_memory": [], "revived_threads": [],
             "thread_ledger": "", "thread_baseline": "", "expired_watch": []}
            for s in inputs["slots"]
        ]
    report.continuity_status = inputs["continuity_status"]
    if inputs["continuity_status"] == "corrupt":
        report.warnings.append(
            "continuity SUSPENDED this run: a prior briefing exists but its "
            "story record is unreadable — the writer was told not to reference "
            "prior coverage (M4 gate must-address: this is distinguished from "
            "'first briefing', never silent)"
        )

    cap = config.budget_cap_usd_per_run(src_env)
    spent = 0.0
    # The run's REAL money, accumulated beside the shadow figure so the cap gates
    # can tell "$2.55 of subscription-lane shadow" from "$2.55 billed" (principal
    # 2026-08-12 — see _cap_verdict). Every `spent +=` below has a `charged +=`
    # twin sourced from the SAME durable ledger row, so the two figures can never
    # be derived from different resolutions of the same step. Read only by the
    # three cap gates, which all sit above the post-persist memory pass — that
    # pass's own charged spend rides report.memory_usd and is not re-summed here.
    charged = 0.0

    # FIX-1 (B3, ruled into this milestone): stage-boundary lane preflight.
    # A misconfigured lane — an unregistered provider/lane, or a subscription
    # seat whose `claude` binary won't resolve — is a CONFIG error, not a
    # transient one. It must KILL the run here, ONCE, at stage entry, BEFORE any
    # expensive work or persist, rather than being swallowed downstream into a
    # depth-absent edition (analyze_story's per-slot broad except) or a silently
    # stale moat (run_memory_pass's post-persist broad except). Per-slot /
    # per-thread degrade stays for TRANSIENT failures only. The seats that
    # already fail loud at the CLI boundary (rank/writer/editor/script) don't
    # need a preflight here — only the two historically-swallowed seats do, and
    # each is gated on whether its stage actually runs this pass. check_lane is
    # a pure resolution/registration check (+ a binary stat for a subscription
    # seat) — no transport, no spend. LaneUnavailable propagates raw, the same
    # kill-class behavior a rank/writer misconfig already has.
    if refresh and not no_threads:
        # FIX-1 (B4-D1): PUBLISH the analyst's ONE resolution at stage entry
        # (effective_seat gates + applies the armed fall, replacing the bare
        # check_lane) OUTSIDE the swallowing analysis try below — a raw
        # LaneUnavailable still KILLS the run here. run_analysis (which generate
        # hosts) REUSES this published resolution instead of re-resolving, so the
        # early kill-gate and the stage's transport/ledger/report ride the SAME
        # (cfg, reason) — no fork on a mid-run `claude` flap. Torn down in
        # generate()'s outer finally.
        from . import analysis as _analysis_pf
        _analysis_pf._publish_analyst()
    if not no_threads:
        llm.check_lane(llm.resolve_seat("state"))     # memory (state-rewrite) stage
        # A″: check_lane gates provider registration + a subscription binary, but
        # NOT the api key. 2026-07-17 (option a) the state seat flipped to
        # Haiku/subscription, so seat_is_openai("state") is now False and this
        # keyless-OpenAI arm goes QUIET — a keyless-OpenAI generate completes fully
        # (the state rewrite rides the claude -p subscription lane like rank/editor/
        # script). The arm stays PROVIDER-AWARE and in place: it fires again the
        # instant any future ruling puts an openai model back on the state seat, so
        # a keyless run can never quietly degrade its state rewrites to stale
        # (run_memory_pass would otherwise turn the 401 into a warning and ship a
        # stale moat) — a missing key for a resolving-openai seat is a CONFIG error
        # that must kill LOUD here at the stage boundary (FIX-1 semantics), before
        # the analysis/writer spend.
        if llm.seat_is_openai("state", src_env) and not key:
            raise GenerateError(
                "OPENAI_API_KEY not set, and the state/memory seat resolves to "
                "OpenAI (gpt-4o) — the state rewrite cannot run without it. Every "
                "other generate stage runs keyless-OpenAI on the anthropic lanes; "
                "set OPENAI_API_KEY in .env, or (pending the state-seat ruling) "
                "flip the state seat to an anthropic model/lane."
            )

    # B3-D6: resolve each writer-family seat ONCE for this run and publish it on
    # _ACTIVE_STEP_SEATS, so call_llm's gate/transport/cost_sink AND _step_ledger's
    # DURABLE report.steps row ride the SAME (cfg, reason) — a `claude` binary that
    # vanishes mid-run can no longer fork them (the D1 lie via the durable record,
    # or a LaneUnavailable raised at a display site over an already-paid step).
    # writer is openai/api (never falls); editor/script default to the subscription
    # lane. An unavailable seat with the fallback UNARMED is left UNSCOPED so
    # call_llm's per-step gate still fails loud at the stage that uses it (deferred
    # kill preserved — not a stage-entry death for a seat a path might not reach).
    # The fall warning derives from THIS resolution — exactly what the steps ride,
    # so it can neither over- nor under-warn — one per fallen seat (QA's pin).
    global _ACTIVE_STEP_SEATS
    _ACTIVE_STEP_SEATS = {}
    for _seat in ("writer", "editor", "script"):
        try:
            _ACTIVE_STEP_SEATS[_seat] = llm.effective_seat(_seat)
        except llm.LaneUnavailable:
            pass                              # unarmed/unavailable — call_llm's gate fails loud
    for _seat, (_c, _r) in _ACTIVE_STEP_SEATS.items():
        if _r:
            report.warnings.append(
                f"{_seat} ran the API fall-over lane (NEWSLENS_LANE_FALLBACK=api "
                f"armed; subscription lane unavailable: {_r}) — this billed real "
                "API money the subscription lane would not; ledger rows labeled "
                "lane=api(fallback:…). Fix the CLI or unset the fallback to fail loud")

    # --- Analysis pass (M9-M3): the writer writes FROM the brief ---
    # Runs only on record-refreshing runs (samples and --no-refresh reuse
    # whatever valid briefs exist — read-only). Failure of the whole stage
    # is a disclosed degrade to today's excerpt behavior, never a dead run.
    from . import analysis as analysis_mod

    briefs_by_slot: Dict[int, Optional[Dict]] = {}
    analyst_slot3_tier: Optional[str] = None
    # NL-148 clause 4: the stage's systemic-failure verdict, read AFTER the
    # try/except below so the pause is raised OUTSIDE the degrade handler.
    # Empty dict = the stage never reported one (it died, or never ran).
    a_rep: Dict = {}
    if refresh and not no_threads:
        _emit_progress(progress, "analysis", "analyst", env=src_env)
        try:
            a_rep = analysis_mod.run_analysis(
                date=date, con=con, env=src_env, already_spent=spent,
                tiers_override=["full", "medium", "medium"])
            # NL-95 ENFORCEMENT FIX #2: the edition cap decrements by the
            # analysis stage's SHADOW total. It used to add the CHARGED total,
            # which is $0 on the subscription lane — so an entire analysis
            # stage could run and leave the writer's cap headroom untouched.
            # The .get fallback degrades to the old behaviour for shape-stubbed
            # test reports; never worse than today.
            spent += a_rep.get("total_usd_shadow",
                               a_rep.get("total_usd") or 0.0) or 0.0
            # ...and the CHARGED twin (2026-08-12 cap ruling): `total_usd` is the
            # stage's real money, $0 on the subscription lane, == shadow on api.
            charged += a_rep.get("total_usd") or 0.0
            report.analysis_usd = a_rep.get("total_usd") or 0.0
            report.analysis_shadow_usd = a_rep.get("total_usd_shadow") or 0.0
            for w in a_rep.get("warnings", []):
                report.warnings.append(f"analysis: {w}")
            if a_rep.get("derating"):
                report.warnings.append(
                    "analysis DERATING under the cap — escalation-flag class")
        except Exception as exc:  # noqa: BLE001 — stage-wide disclosed degrade
            report.warnings.append(
                f"analysis stage unavailable this run ({type(exc).__name__}: "
                f"{exc}) — writer degrades to feed-excerpt material, disclosed")

    # NL-148 CLAUSE 4 — THE PAUSE. Deliberately OUTSIDE the try above, and
    # that placement is the enforcement, not a preference: the handler one
    # line up is a stage-wide `except Exception` that degrades to feed-excerpt
    # material and carries on. Raising inside its reach — even into a
    # dedicated arm — would leave the contract one refactor away from
    # inverting, and the inverted form is the one thing this clause forbids:
    # publishing a thinned edition built on no retrieval while telling the
    # reader it worked. Out here, no handler between this line and the caller
    # can turn the pause back into an edition.
    #
    # Taken at the RUN level because the ruling says "pause GENERATION". The
    # stage measures; this owns the pipeline and stops it — before a single
    # writer token is spent, and long before `persist_generation`, so nothing
    # is published, the reader keeps the edition they already had, and the
    # retry re-enters a run with no completed stories to re-bill.
    #
    # WHAT THE STAGE'S VERDICT MEANS, restated here because this is where the
    # run acts on it (FIX-1, gate Ruling A 2026-08-12): "at least one slot
    # actually TRIED to fetch, nothing came back anywhere, and no valid brief
    # exists". It is NOT "every slot tried" — a tier-excluded slot never tries,
    # on a healthy day or a dead one, and reading its silence as a veto let one
    # such slot shield a whole-network outage from the pause. See the verdict
    # block in analysis.py for the any/all split and the P-A receipt.
    if a_rep.get("fetch_systemic_failure"):
        raise analysis_mod.SystemicFetchFailure(
            analysis_mod.FETCH_PAUSE_MESSAGE)
    # NL-107 — RUN-INTERNAL by design: unbounded, newest-wins. This is the run
    # reading its own analysis stage, minutes before its own promote stamps
    # generated_at, so the reader's read (analysis.coherent_valid_brief) would
    # hide the very briefs this run must write from. The same holds for a
    # `--no-refresh` completion of an interrupted regenerate: the briefs it
    # needs postdate the OLD edition's stamp, and its own promote is what makes
    # them the edition of record's briefs.
    #
    # A SAMPLE is the exception (gate fix F3, from build flag F-b): it is not a
    # generation context at all. Samples force refresh=False, so they never
    # rank, never stage and never write a brief, and FIX-2 already has them
    # consuming the LIVE row's slots rather than a staged selection. Pairing
    # those slots with a rival's newest brief was the render mixture inside a
    # sample artifact — so a sample reads what the reader reads. On a date with
    # no rival staged that is the same row this loop returns anyway.
    # NL-151b — THE VECTOR REACHES THE RUN. Live from the stage when it ran;
    # RECOVERED from the run record when it did not (`--no-refresh`, a sample,
    # a stage that died). The recovery is the L1 hole this batch had to close:
    # falling back to the positional vector on a `--no-refresh` finishing an
    # interrupted armed regenerate would put the fetch-failed story back in a
    # depth slot with no brief behind it — a degraded lead, produced by the
    # machinery that exists to forbid one. `edition_tiers` is the arm gate, so
    # at the OFF arm every line below reduces to today's `n <= 3`.
    inputs["depth_tiers"] = (
        a_rep.get("depth_tiers")
        or analysis_mod.depth_tiers_from_record(date, len(inputs["slots"])))
    depth_tiers = edition_tiers(inputs, len(inputs["slots"]))
    inputs["depth_tiers"] = depth_tiers
    report.depth_tiers = depth_tiers
    depth_slots = depth_slot_numbers(inputs["slots"], depth_tiers)
    report.depth_slots = depth_slots
    for s in inputs["slots"]:
        n = int(s["slot"])
        if n in depth_slots:
            doc = (analysis_mod.coherent_valid_brief(
                       con, date, n, inputs["row"]["generated_at"])
                   if report.sample else
                   analysis_mod.latest_valid_brief(con, date, n))
            if doc:
                briefs_by_slot[n] = doc
    # NL-63 M2: slot 3 is pinned to full-picture (medium) — the demote-to-quick
    # verdict is RETIRED (exactly-3 full-picture). analyst_slot3_tier is kept
    # only as the inputs marker some paths still read; it no longer alters tiers
    # or the deep-view ladder. deep_views reflects analyst-brief PRESENCE alone.
    analyst_slot3_tier = analysis_mod.analyst_slot3_tier(con, date)
    inputs["briefs_by_slot"] = briefs_by_slot
    # NL-151 clause 3: the stage's skip record reaches the assembler's footer.
    # Sourced from `a_rep` and NOT re-derived from briefs_by_slot — an absent
    # brief has many causes (model rejection, budget floor, a dead stage) and
    # only the stage knows which of them was a fetch failure. `a_rep` is {} when
    # the stage never ran or died, which yields no disclosure, which is correct:
    # a run that never reached the fetcher has no fetch failure to report.
    inputs["fetch_skipped"] = a_rep.get("fetch_skipped") or []
    report.fetch_skipped = inputs["fetch_skipped"]
    inputs["analyst_slot3_tier"] = analyst_slot3_tier
    # NL-151b: the ladder's denominator is the DEPTH TIER, which after a
    # demotion is not `(1, 2, 3)`. Leaving the literal here would have reported
    # the promoted slot's brief as missing (it is not in 1-3) and the demoted
    # slot's absence as a degrade (it is an In-Brief story now, and In-Brief
    # stories have never had a deep view to be absent) — a doubly wrong
    # asymmetry figure on the one instrument that measures the ladder.
    report.deep_views = {
        str(n): ("available" if briefs_by_slot.get(n) else "absent")
        for n in depth_slots if any(int(s["slot"]) == n for s in inputs["slots"])
    }
    inputs["deep_views"] = report.deep_views  # assembler reads the ladder label

    # NL-63 M1 gate F (orphan-delta reorder): the memory pass no longer runs
    # HERE (before the narrative). Writing the ledger before the edition is
    # persisted stranded delta entries citing an UNPUBLISHED edition whenever a
    # narrative/script/audio failure raised after this point. The pass now runs
    # AFTER persist_generation (below), so deltas are written only once the
    # edition is on the record — see the memory block near the run's end.

    # --- Narrative pass ---
    _emit_progress(progress, "narrative", "writer", env=src_env)
    n_prompt = build_narrative_prompt(date, report.variant, inputs)
    est = _est_cost(n_prompt, NARRATIVE_MAX_TOKENS)
    _v = _cap_verdict("narrative", est, spent, charged, cap)
    if _v == CAP_KILL:
        raise GenerateError(
            f"estimated narrative cost ${est:.4f} exceeds the remaining budget "
            f"cap (${cap:.2f}) — aborting before the call"
        )
    if _v == CAP_WARN:
        report.warnings.append(
            _cap_warn_line("narrative", est, spent, charged, cap))
    draft_holder: List[Dict] = []

    def _shape_check(content: str) -> None:
        payload = json.loads(content)
        if not isinstance(payload, dict) or not isinstance(payload.get("stories"), list):
            raise ValueError("draft must be a JSON object with a `stories` list")
        if len(payload["stories"]) != len(inputs["slots"]):
            raise ValueError(
                f"{len(payload['stories'])} draft stories for "
                f"{len(inputs['slots'])} slots — must match"
            )
        draft_holder[:] = [payload]

    _, usage_n = call_llm(
        key, n_prompt, "narrative", NARRATIVE_MAX_TOKENS,
        NARRATIVE_TEMPERATURE, True, validate=_shape_check,
        cost_sink=report.attempt_ledger,
    )
    draft_payload = draft_holder[0]
    step_n = {"step": f"narrative_{report.variant}",
              "prompt_tokens": usage_n.get("prompt_tokens"),
              "completion_tokens": usage_n.get("completion_tokens"),
              **_step_ledger("narrative", usage_n)}
    report.steps.append(step_n)
    # Cap binds on SHADOW (Onna's law): usd == usd_charged (0.0 on a
    # subscription seat) but the cap must count the API-equivalent price, so
    # `spent` accumulates usd_shadow. On the api lane the two are equal — no
    # cost/cap test moves; the flip only matters once editor/script go
    # subscription (below), where charged is 0 but the run must still be capped.
    spent += step_n["usd_shadow"] or 0
    charged += step_n["usd_charged"] or 0     # the money half (2026-08-12 cap)

    # --- Editor pass (M6 mandate 2): cut/tighten/concretize ONLY — the
    # editor may never add facts; the edited payload is what gets fully
    # validated, persisted, and adapted. Editor failure degrades to the
    # unedited draft WITH disclosure — never a dead run.
    edited_payload = draft_payload
    editor_note = "editor: skipped"
    _emit_progress(progress, "editor", "editor", env=src_env)
    # A9/A10 (editor-preservation batch, 2026-07-21): tag the DRAFT's dated
    # ledger callbacks DETERMINISTICALLY (no LLM) so the editor is TOLD to keep
    # them (belt, injected below) and a post-edit diff can ENFORCE it (suspenders,
    # in the degrade seam). Computed on the DRAFT before the editor runs. Wrapped
    # degrade-safe: a matcher/DB hiccup must never kill the run — it just leaves
    # nothing pinned this edition (the pre-batch behavior).
    _protect_facts: List[Tuple[str, Tuple[str, ...]]] = []
    _poison_facts: List[Tuple[str, Tuple[str, ...]]] = []
    _protect_block = "(callback matcher unavailable this run — nothing pinned)"
    try:
        from . import memory_core as _mc_cb
        _cb_ctx = _ledger_callback_context(con, inputs["slots"], date)
        _callback_tags = _mc_cb.ledger_callbacks(draft_payload, _cb_ctx, date)
        _protect_facts = [(t.date, t.subject_units)
                          for t in _callback_tags if t.tag == "PROTECT"]
        _poison_facts = [(t.marker, t.subject_units)
                         for t in _callback_tags if t.tag == "POISON"]
        _protect_block = _render_protect_block(_callback_tags)
        if _protect_facts:
            report.warnings.append(
                f"A9 preserve: pinned {len(_protect_facts)} dated ledger "
                f"callback(s) for the editor to keep")
    except Exception as exc:   # noqa: BLE001 — never let instrumentation kill a run
        report.warnings.append(
            f"A9 preserve: callback matcher skipped ({type(exc).__name__}: {exc}) "
            "— nothing pinned this edition")
    try:
        e_template = (paths.PROMPTS_DIR / PROMPT_EDITOR).read_text(encoding="utf-8")
        e_prompt = e_template.format(
            labels_block=build_labels_block(inputs),
            analysis_facts_block=build_analysis_facts_block(inputs),
            protect_block=_protect_block,
            draft_json=json.dumps(draft_payload, ensure_ascii=False),
        )
        est_e = _est_cost(e_prompt, EDITOR_MAX_TOKENS, "editor")
        # SCOPE NOTE, disclosed rather than silent (fix-loop item 7): this gate's
        # GenerateError is caught below and DEGRADES the run to the unedited
        # draft — it is not literally one of the two run-killing gates the
        # ruling names. It rides the same verdict anyway, because the thing the
        # principal objected to is his generation being damaged by money nobody
        # was charged, and shipping an unedited edition is that damage in its
        # quieter form. A CHARGED breach still degrades exactly as today.
        _v = _cap_verdict("editor", est_e, spent, charged, cap)
        if _v == CAP_KILL:
            raise GenerateError(
                f"editor pass estimate ${est_e:.4f} would exceed the run cap"
            )
        if _v == CAP_WARN:
            report.warnings.append(
                _cap_warn_line("editor", est_e, spent, charged, cap))
        edited_holder: List[Dict] = []

        def _editor_shape(content: str) -> None:
            payload = json.loads(content)
            if not isinstance(payload, dict) or not isinstance(payload.get("stories"), list):
                raise ValueError("editor must return the same JSON shape")
            if len(payload["stories"]) != len(draft_payload["stories"]):
                raise ValueError("editor changed the story count")
            for de, dr in zip(payload["stories"], draft_payload["stories"]):
                if not (isinstance(de, dict) and isinstance(dr, dict)):
                    continue
                if de.get("tier") != dr.get("tier"):
                    raise ValueError("editor changed a tier")
                for lbl in ("why_label", "watch_label"):
                    if dr.get(lbl) is not None and de.get(lbl) != dr.get(lbl):
                        raise ValueError(f"editor changed {lbl} (A7 labels are the writer's)")
            edited_holder[:] = [payload]

        _, usage_e = call_llm(
            key, e_prompt, "editor", EDITOR_MAX_TOKENS,
            EDITOR_TEMPERATURE, True, validate=_editor_shape,
            cost_sink=report.attempt_ledger,
        )
        edited_payload = edited_holder[0]
        step_e = {"step": "editor_pass",
                  "prompt_tokens": usage_e.get("prompt_tokens"),
                  "completion_tokens": usage_e.get("completion_tokens"),
                  **_step_ledger("editor", usage_e)}
        report.steps.append(step_e)
        spent += step_e["usd_shadow"] or 0   # editor: subscription-lane seat — cap on shadow
        charged += step_e["usd_charged"] or 0          # ...and the money half
        before = sum(wc(" ".join(v for v in s.values() if isinstance(v, str)))
                     for s in draft_payload["stories"] if isinstance(s, dict))
        after = sum(wc(" ".join(v for v in s.values() if isinstance(v, str)))
                    for s in edited_payload["stories"] if isinstance(s, dict))
        pct = round((before - after) / before * 100) if before else 0
        editor_note = f"editor: {before} -> {after} words ({pct}% tighter)"
        report.warnings.append(editor_note)
        # Carryover 18a: mechanical tripwire for epistemic-qualifier deletion.
        hedge_re = re.compile(
            r"\b(could|may|might|likely|expect(?:s|ed)?|appears?|suggests?|"
            r"unclear|reportedly|unconfirmed)\b", re.I)
        draft_text = " ".join(
            v for s in draft_payload["stories"] if isinstance(s, dict)
            for v in s.values() if isinstance(v, str))
        edited_text = " ".join(
            v for s in edited_payload["stories"] if isinstance(s, dict)
            for v in s.values() if isinstance(v, str))
        h_before, h_after = len(hedge_re.findall(draft_text)), len(hedge_re.findall(edited_text))
        if h_before >= 3 and h_after < h_before * 0.5:
            report.warnings.append(
                f"editor hedge-ratio: {h_before} -> {h_after} hedge words — "
                "check that epistemic qualifiers weren't stripped from kept "
                "claims (carryover 18a tripwire)"
            )
    except (GenerateError, OSError) as exc:
        editor_note = f"editor: DEGRADED to unedited draft ({exc})"
        report.warnings.append(editor_note)

    # ALL narrative validators run on the EDITED text (mandate 2) — INSIDE
    # the degrade seam (BUG-8): a validator-violating edit (live repro: the
    # editor clipped a mandatory revival date) degrades to the re-validated
    # draft with disclosure; a draft that ALSO fails is a logged, visible
    # GenerateError — never a raw crash.
    try:
        # LENGTH REGIME 2026-07-30 — the editor's length guard is GONE. P3.1
        # item 3 discarded any edit that cut a briefed lead below 450 words,
        # which made "tighten" conditional on a minimum the editor could not
        # always honestly meet. A shorter lead that keeps the specifics is now
        # the desired outcome, so it ships. What did NOT go is the FACT guard
        # directly below: the editor may still never lose a dated ledger
        # callback, and that discard rides this same degrade seam.
        # A9 preserve-enforcement (editor-preservation batch): the teeth. A
        # dated ledger callback the DRAFT carried whose (date + subject) fact no
        # longer survives the edit is DISCARDED via this SAME degrade path —
        # the shape the retired LEAD_FLOOR guard used to share with it: raise
        # ValueError -> the edit is dropped, the writer's draft ships with
        # disclosure. (It is now the ONLY user of this path.) This is the direct
        # HSR unblock: the length-editor can no longer delete the writer's clean
        # dated accountability callbacks (e8/e9) while keeping the poison one.
        # Degrade-to-draft is the LONGER, pricier text (Onna) — couples to the
        # shadow cap — so the firing is instrumented below for degrade-rate.
        if edited_payload is not draft_payload and _protect_facts:
            _lost = _mc_cb.protect_facts_lost(_protect_facts, edited_payload, date)
            if _lost:
                _lost_desc = "; ".join(
                    f"{d} [{', '.join(u)}]" for d, u in _lost)
                # Instrumentation (Onna, blocking-for-observability): a distinct,
                # greppable degrade-rate warning AND a structured report.steps
                # marker (cost-folding tolerates a non-cost step — it sums
                # s.get('usd')|0), both emitted BEFORE the raise so they survive
                # the discard.
                report.warnings.append(
                    f"A9-DEGRADE: editor discarded — {len(_lost)} dated ledger "
                    f"callback(s) lost ({_lost_desc}); degraded to the writer's "
                    "draft (LONGER text; degrade-rate event)")
                report.steps.append({
                    "step": "a9_preserve_degrade",
                    "callbacks_lost": len(_lost),
                    "facts": [{"date": d, "subject": list(u)} for d, u in _lost],
                })
                raise ValueError(
                    f"editor lost {len(_lost)} dated ledger callback(s) "
                    f"({_lost_desc}) — the writer's clean accountability "
                    "callbacks must survive (A9 preserve-enforcement)")
        stories, narrative_warnings = validate_narrative_payload(
            edited_payload, inputs["slots"], report.variant,
            depth_tiers=inputs.get("depth_tiers"),
        )
        # BUG17 wiring (M3 gate 1a): the trace check runs on the EDITED
        # stories — an invented numeral the editor introduced (or kept)
        # never reaches the record silently.
        narrative_warnings.extend(trace_check_numerals(stories, inputs))
    except ValueError as exc:
        if edited_payload is not draft_payload:
            report.warnings.append(
                f"editor: output FAILED validation ({exc}) — degraded to the "
                "writer's draft (disclosed; the edit was discarded)"
            )
            editor_note += " [DISCARDED: failed validation]"
            try:
                stories, narrative_warnings = validate_narrative_payload(
                    draft_payload, inputs["slots"], report.variant,
                    depth_tiers=inputs.get("depth_tiers"),
                )
                # BUG17 wiring, degrade path: the surviving DRAFT stories
                # get the same trace check — both validation sites covered.
                narrative_warnings.extend(trace_check_numerals(stories, inputs))
            except ValueError as exc2:
                raise GenerateError(
                    f"narrative draft failed validation after editor degrade: {exc2}"
                ) from exc2
        else:
            raise GenerateError(f"narrative failed validation: {exc}") from exc
    report.warnings.extend(narrative_warnings)
    # LENGTH REGIME 2026-07-30 — OBSERVABILITY, NOT ENFORCEMENT. What stood
    # at the pre-editor site was P3.1 item 3's floor retry (pad-toward-
    # minimum; banned product-wide by the ratification). Nothing is retried,
    # rejected, or discarded; a short lead on a thin day is a PASS. The note
    # measures the SHIPPED lead — post-editor, post-degrade (gate ruling R4,
    # batch A 2026-07-31): an editor-shortened lead is exactly the data
    # D3's week-of-editions revisit needs, and the revisit reads shipped
    # lengths from the record; this line is the run-time disclosure of the
    # same number.
    #
    # The brief gate is KEPT from the retry this replaced: a slot with no
    # valid analysis brief was never warned about, and the length regime did
    # not widen that (the record-derived revisit covers briefless days).
    #
    # D3 REVISIT BINDING (gate ruling R4.i, 2026-07-31 — batch B item 6).
    # When the week-of-editions falsifier is run, it counts SHIPPED LEAD WORDS
    # QUERIED FROM THE RECORD:
    #
    #     SELECT date, narrative_text FROM briefings ORDER BY date
    #
    # — lead = story 1 of `narrative_text`, counted with `_lead_words`. It does
    # NOT count these note lines. The note is CORROBORATION, never the count.
    # The reason is a measurement gap this line cannot close on its own: the
    # note is brief-gated, so a briefless thin day ships a short lead and
    # emits nothing, and counting notes would silently undercount exactly the
    # days D3 exists to find. The record has every shipped lead regardless of
    # gate, editor behaviour, or degrade path.
    #
    # NL-151b GATE G-2 AMENDMENT (2026-08-14) — "lead = story 1" ABOVE IS THE
    # PRE-ARMING READING AND THE FALSIFIER MUST NOT KEEP IT. After his arm (ii)
    # the lead is the FULL-TIER story per the run record's `tiers`, which is
    # position 1 on every edition published before this commit and on every
    # ordinary morning after it — but NOT on a demotion morning, where story 1
    # is the demoted In-Brief story and the real lead is the promoted one. So
    # the week-of-editions query above must be read with the date's `tiers`
    # beside it (`analysis._tiers_for(date, n)`) and the lead taken as the full
    # position; counting `narrative_text`'s first story on a demotion date
    # measures an In-Brief story against a lead threshold.
    #
    # THE NOTE ITSELF IS LEFT POSITIONAL, deliberately and for now: its gate
    # (`briefs_by_slot.get(1)`) suppresses it exactly when slot 1 demotes — a
    # demoted slot has no depth brief — so it emits NO FALSE LINE, it just goes
    # quiet on the ~1-in-17 morning and leaves the promoted lead unmeasured.
    # Keying the note to the vector is hygiene-batch work, not a fix (gate G-2,
    # comment-grade).
    lead_w = _lead_words({"stories": stories})
    if (inputs.get("briefs_by_slot") or {}).get(1) \
            and lead_w < LEAD_SHORT_NOTE_WORDS:
        report.warnings.append(
            f"lead length note: story 1 ran {lead_w} words, under the "
            f"{LEAD_SHORT_NOTE_WORDS}-word disclosure threshold — no floor "
            "action; length regime 2026-07-30 (D3 week-of-editions data)")
    # NL-75 THE FORWARD-CLAIM RULES — run generation-side over the EDITED
    # stories (the same text that persists). Repetition diction without a
    # predating antecedent (poisoned-antecedent hardened), stale watch-fors,
    # and unconverted expired watch-fors surface as visible warnings.
    report.warnings.extend(
        forward_claim_findings(con, stories, inputs["slots"], date))

    # A10 WARN-ONLY this week (editor-preservation batch): a POISON-marked
    # sentence — continuity diction tracing to a POSITIVE source-echo delta
    # (Rook: the positive mark only, never a no-antecedent fallback) — that
    # SURVIVED into the shipped text emits a warn-only marker so poison-survival
    # is measurable from day one. NO DROP, NO DEGRADE on poison this week; the
    # A10 hard-drop is M3, explicitly OUT of this build.
    if _poison_facts:
        from . import memory_core as _mc
        _final_text = " ".join(
            v for s in stories if isinstance(s, dict)
            for v in s.values() if isinstance(v, str))
        _final_tokens = set(_mc._salient_units(_final_text))
        for _marker, _units in _poison_facts:
            # Token-aware survival, mirroring the F1 fix: the marker matches on a
            # WORD BOUNDARY (multi-word markers like 'back on' survive; 'again'
            # no longer false-matches inside 'against'), units by whole-token
            # membership. Raw substring inflated this warn-only signal we collect
            # this week.
            _marker_present = bool(_marker) and re.search(
                r"\b" + re.escape(_marker) + r"\b", _final_text, re.I) is not None
            _units_present = any((u or "").lower() in _final_tokens for u in _units)
            if _marker_present and _units_present:
                report.warnings.append(
                    f"A10-WARN: source-echo continuity diction survived the edit "
                    f"— {_marker!r} on [{', '.join(_units)}] traces to a "
                    "source-echo ledger row (warn-only this week; no drop — "
                    "poison-survival instrumentation)")

    narrative = assemble_narrative(date, report.variant, stories, inputs)
    report.narrative_text = narrative
    report.narrative_words = wc(narrative)
    report.per_story_words = [
        wc(" ".join(v for v in st.values() if isinstance(v, str))) for st in stories
    ]
    # NL-63 M2 amended registers (A2 warn guidance, ~2x the pre-amendment bands):
    #   full = lead (doubled); medium = full-picture (doubled); quick = In Brief
    #   (the OLD medium register — structured, NOT the dead <=60-word snippet).
    TIER_BANDS = {"full": (450, 900), "medium": (200, 600), "quick": (100, 300)}
    for st, words in zip(stories, report.per_story_words):
        lo_t, hi_t = TIER_BANDS[st["tier"]]
        if not lo_t <= words <= hi_t:
            report.warnings.append(
                f"{st['tier']} story {words} words — outside the "
                f"{lo_t}-{hi_t} tier guidance (A2) [KNOB; warn-only]"
            )
    # NL-63 M2: the edition band scales off the ACTUAL per-slot targets (lead
    # 640 / full-picture 440 / In-Brief 220), not a fixed /5 divisor — so a 6-,
    # 7-, or thin-day edition each warns against its own expected total. The
    # NARRATIVE_BAND (1,800-2,500) is the canonical 6-7 story reference.
    expected = sum(per_slot_words(int(s["slot"])) for s in inputs["slots"])
    lo_band, hi_band = int(expected * 0.7), int(expected * 1.25)
    if not (lo_band <= report.narrative_words <= hi_band):
        report.warnings.append(
            f"narrative {report.narrative_words} words — outside the "
            f"~{lo_band}-{hi_band} guidance band for "
            f"{len(inputs['slots'])} slot(s) [KNOB; warn-only]"
        )

    # --- Script pass ---
    _emit_progress(progress, "script", "script", env=src_env)
    s_prompt = build_script_prompt(date, report.variant, narrative, inputs)
    est_s = _est_cost(s_prompt, SCRIPT_MAX_TOKENS, "script")
    # THIS is the gate that killed his 2026-08-12 run at $2.5513 shadow / $0.00
    # charged. On the subscription lane it now warns and the run continues.
    _v = _cap_verdict("script", est_s, spent, charged, cap)
    if _v == CAP_KILL:
        raise GenerateError(
            f"estimated script cost ${est_s:.4f} would exceed the run budget "
            f"cap (${cap:.2f}, ${charged:.4f} charged of ${spent:.4f} shadow) — "
            "narrative was NOT persisted; raise the cap or re-run"
        )
    if _v == CAP_WARN:
        report.warnings.append(
            _cap_warn_line("script", est_s, spent, charged, cap))
    script_holder: List[str] = []
    script_warnings: List[str] = []

    guide_ceiling, _, n_covered = _script_budgets(len(inputs["slots"]))
    covered_slots = script_covered_slots(inputs)

    def _validate_script(content: str) -> None:
        body, hard, warns = validate_script(content, narrative, inputs,
                                            covered=covered_slots)
        if hard:
            # Missing mandatory spoken disclosures — the FIRST broken signal,
            # checked before length (principal 2026-07-14). Retry material.
            raise ValueError("; ".join(hard))
        # FLOOR REMOVED (principal 2026-07-14, second amendment — "as long as
        # it has to be"): NO length contract remains below the ceiling; a short
        # complete episode ships at any length the material earns. The flat
        # SCRIPT_DEGENERATE_WORDS check is a brokenness backstop only —
        # coverage-independent, because brokenness isn't a function of k. It is
        # the LAST broken signal: truncation is caught upstream (call_llm's
        # length-finish check), missing disclosures are the `hard` list above;
        # a body under ~120 words cannot physically carry the intro formula +
        # dateline + a real lead segment + the outro — furniture around a stub.
        if wc(body) < SCRIPT_DEGENERATE_WORDS:
            raise ValueError(
                f"script degenerate: {wc(body)} words — below the "
                f"{SCRIPT_DEGENERATE_WORDS}-word brokenness backstop (NOT a "
                "length contract; disclosures and truncation checked and "
                "clear — this output cannot contain intro + lead + outro, "
                "it is a stub, not a short episode)"
            )
        script_holder[:] = [body]
        script_warnings[:] = warns

    _, usage_s = call_llm(
        key, s_prompt, "script", SCRIPT_MAX_TOKENS,
        SCRIPT_TEMPERATURE, False, validate=_validate_script,
        cost_sink=report.attempt_ledger,
    )
    script = script_holder[0]
    # Provenance: validate warns travel with the attempt that SHIPS — the
    # extend happens after the structural block resolves which attempt
    # that is (previously the first attempt's warns landed here
    # unconditionally, and the retry's landed at its call site whether or
    # not that attempt shipped).
    shipped_script_warns = script_warnings[:]
    # BUG21 fix (QA contract: tests/test_p31_enforcement.py::
    # test_structural_retry_skipped_when_real_spend_already_ate_the_cap):
    # count the script step's REAL cost into `spent` BEFORE the structural
    # retry decision below — mirroring the narrative twin, which counts
    # step_n into `spent` as soon as the step completes (its floor-retry
    # pre-check is retired; length regime 2026-07-30). Without this the retry
    # pre-check under-counts true spend by one script call, and a run can
    # overshoot the cap by one retry. (report.steps keeps its original
    # append position after the block.)
    step_s = {"step": "script_adapt",
              "prompt_tokens": usage_s.get("prompt_tokens"),
              "completion_tokens": usage_s.get("completion_tokens"),
              **_step_ledger("script", usage_s)}
    spent += step_s["usd_shadow"] or 0.0   # script: subscription-lane seat — cap on shadow
    charged += step_s["usd_charged"] or 0.0        # ...and the money half

    # P3.1 items 1+2 — the spoken editorial bar, enforcement-grade: ONE
    # retry with the exact violations injected; then ship the better
    # attempt WITH disclosure. Never silent, never a dead run, never a
    # second retry against the cap.
    structural = script_structural_check(script)
    if structural:
        retry_prompt = (
            s_prompt
            + "\n\n=== YOUR PREVIOUS ATTEMPT WAS REJECTED — STRUCTURAL "
            "VIOLATIONS (fix exactly these; everything else above still "
            "binds) ===\n"
            + "\n".join(f"- {v}" for v in structural))
        est_r = _est_cost(retry_prompt, SCRIPT_MAX_TOKENS, "script_retry")
        if spent + est_r > cap:
            report.warnings.append(
                "script STRUCTURAL violations stand (retry skipped — "
                f"${est_r:.4f} would exceed the cap): " + " | ".join(structural))
        else:
            try:
                _, usage_r = call_llm(
                    key, retry_prompt, "script_retry", SCRIPT_MAX_TOKENS,
                    SCRIPT_TEMPERATURE, False, validate=_validate_script,
                    cost_sink=report.attempt_ledger,
                )
                retry_script = script_holder[0]
                retry_script_warns = script_warnings[:]
                step_r = {"step": "script_retry",
                          "prompt_tokens": usage_r.get("prompt_tokens"),
                          "completion_tokens": usage_r.get("completion_tokens"),
                          **_step_ledger("script_retry", usage_r)}
                report.steps.append(step_r)
                spent += step_r["usd_shadow"] or 0.0   # script retry: cap on shadow
                structural_2 = script_structural_check(retry_script)
                if not structural_2:
                    script = retry_script
                    shipped_script_warns = retry_script_warns
                    report.warnings.append(
                        "script structural retry: violations cleared "
                        f"({len(structural)} fixed)")
                elif len(structural_2) < len(structural):
                    script = retry_script
                    shipped_script_warns = retry_script_warns
                    report.warnings.append(
                        "script structural retry: improved but "
                        f"{len(structural_2)} violation(s) REMAIN — shipped "
                        "with disclosure: " + " | ".join(structural_2))
                else:
                    report.warnings.append(
                        "script structural retry did not improve — first "
                        "attempt shipped with disclosure: "
                        + " | ".join(structural))
            except GenerateError as exc:
                report.warnings.append(
                    f"script structural retry failed ({exc}) — first attempt "
                    "shipped with disclosure: " + " | ".join(structural))

    report.warnings.extend(shipped_script_warns)
    # Stage-0 M2 (M0 finding F2 / RED-2): the spoken continuity net, run on the
    # script that actually SHIPS — after the structural retry has resolved
    # which attempt that is, and before tts_safe_pass, so the validator sees
    # the model's own words (the P3 #8 ordering rule). Warn-grade: a fabricated
    # continuity claim becomes a FINDING in the run record instead of silence.
    for finding in script_continuity_findings(con, script, inputs, date):
        report.warnings.append(f"script continuity: {finding}")
    # P3.1 anchor fix (QA contract 2026-07-09): a shipped script with no
    # detectable dateline has no cold-open boundary to measure — the HARD
    # cap is unenforceable. Never a silent exemption: disclose it (the
    # dateline itself is the script contract's job upstream).
    if not _DATELINE_RE.search(_anchor_view(script)):
        report.warnings.append(
            "cold-open cap unenforceable: no dateline anchor found — the "
            "hard cold-open cap was not applied to this script")

    # P3 #8: deterministic TTS-safe pass — AFTER validation (validators see
    # the model's own output; these are enumerated furniture-class rewrites
    # of form, never facts), disclosed per transform class.
    script, tts_notes = tts_safe_pass(script)
    if tts_notes:
        report.warnings.append(
            f"tts-safe pass (P3 #8, code-owned): {', '.join(tts_notes)}")
    report.steps.append(step_s)
    report.script_text = script
    report.script_words = wc(script)
    # Emergent-length enforcement (principal 2026-07-14): NO lower-bound warn — a
    # naturally short digest is correct. Only the "never fill toward the bound"
    # direction warns: an episode over its k-story guide ceiling (or the hard
    # 11-min bound) is running long / padding.
    if report.script_words > int(guide_ceiling * 1.15):
        report.warnings.append(
            f"script {report.script_words} words — over the ~{guide_ceiling}-word "
            f"guide for a {n_covered}-story digest (hard ceiling "
            f"{SCRIPT_CEILING_WORDS}); the episode is a digest, not a reading — "
            "tighten, never fill toward the bound [KNOB; warn-only]"
        )

    # --- Audio step (M6 mandate 1): the last stage; a synth failure
    # degrades to a no-audio run WITH disclosure, never a dead run.
    _emit_progress(progress, "audio")
    from . import audio as audio_mod

    cfg_full = config.load_sources()
    audio_path_str = None
    out_dir = paths.DATA_DIR / BRIEFINGS_DIR_NAME
    if report.sample:
        stem = (f"{date}-no-threads-SAMPLE" if report.no_threads
                else f"{date}-variant-{report.variant}-SAMPLE")
    else:
        stem = date
    try:
        result = audio_mod.generate_audio(
            script, out_dir / f"{stem}.wav",
            engine=cfg_full.tts_engine, openai_key=key,
            budget_cap=max(0.0, cap - spent),
        )
        audio_path_str = result.path
        report.steps.append({
            "step": f"tts_{result.engine}", "model": result.engine,
            "duration_s": result.duration_s, "gen_time_s": result.gen_time_s,
            "usd": result.est_cost_usd,
        })
        report.warnings.append(
            f"audio: {result.engine} — {result.duration_s / 60:.1f} min in "
            f"{result.gen_time_s:.0f}s"
            + (f" (${result.est_cost_usd:.4f})" if result.est_cost_usd else " ($0)")
        )
    except audio_mod.AudioError as exc:
        report.warnings.append(
            f"audio: SKIPPED — {exc} (the text briefing is unaffected)"
        )

    # --- Persist (never for samples), artifact, instrumentation ---
    if not report.sample:
        _emit_progress(progress, "persist")
        # DO NOT REFLOW the call below. Its first three positional arguments are
        # kept on one physical line deliberately: the ratified NL-107 order pin
        # greps this source for that exact call spelling to prove the promote
        # still precedes the memory pass. The needle is spelled out ONLY by the
        # call itself — never here — because a pin a comment can satisfy is not
        # a pin (ENGINEERING.md; NL-108 QA F-2, which caught this comment
        # answering the grep before the code did).
        applied_revivals = persist_generation(con, date, narrative, script,
                                              report.steps,
                                              audio_path=audio_path_str)
        # NL-108: the edition is on the record, so its memory effects are too —
        # they committed in the same transaction. NOW the lifecycle v2 promise
        # ("every automatic transition is surfaced, dated, never silent") can be
        # kept in the past tense, because the transition has actually happened.
        # The rank stage announced these as pending; this is the confirmation,
        # and on a run that died before here neither line was ever printed.
        if applied_revivals:
            names = ", ".join(
                r["topic"]
                + (f" (last covered {r['last_covered']})" if r["last_covered"] else "")
                for r in applied_revivals
            )
            report.warnings.append(
                f"memory: {len(applied_revivals)} dormant thread(s) auto-revived "
                f"by slot-earning stories in this published edition: {names} — "
                "see memory.md")
        # memory.md is rendered at the END of the rank stage, which is before
        # the promote applied any of this — so the file must be re-taken here or
        # it would out-vote the database on the next sync (file wins on status).
        #
        # Contained like the memory pass below, and for the same reason: the
        # edition is ALREADY PUBLISHED. The helper handles the expected OSErrors
        # itself; this catches the rest — notably paths.MEMORY_FILE raising
        # RuntimeError in an unsanctioned process (the 2026-07-14 incident
        # guard), which must never turn a published edition into a crash.
        try:
            _left_alone = memory.refresh_file_after_publish(con, applied_revivals)
        except Exception as exc:            # noqa: BLE001 — post-persist
            _left_alone = (
                f"memory.md was not refreshed after publication ({exc}). The "
                "database is correct and the next sync reconciles the file")
        if _left_alone:
            report.warnings.append(_left_alone)
        # --- Memory core (NL-63 M1): the delta ledger + standing state ---
        # M1 gate F (orphan-delta reorder): runs AFTER persist_generation so a
        # delta is written ONLY once its edition is published — a narrative,
        # script, or audio failure now aborts BEFORE any ledger write, never
        # stranding an entry that cites an unpublished edition.
        #
        # M1 gate F REVISED — live-contact fix #4 (the moat gap on --no-refresh
        # record runs): the trigger is PERSISTENCE, not the refresh chain. Any
        # run that reaches this block has already persisted the edition of record
        # (the enclosing `if not report.sample`), so it writes the moat — INCLUDING
        # a `--no-refresh` record completion (rank already paid, generate's re-rank
        # failed, --no-refresh was the correct publish path — the 2026-07-14 case).
        # The old `refresh` gate assumed --no-refresh == iteration; the record
        # proved it can be the edition of record. briefs_by_slot here is read from
        # latest_valid_brief (persisted rows) on BOTH the refresh and --no-refresh
        # path, so the moat write is identical either way. Samples never persist and
        # never reach here; `not no_threads` stays as a defensive guard (a
        # no_threads run is always a sample, so it is structurally already excluded).
        # Idempotency makes re-runs self-limiting: a repeat finds every delta on
        # file, writes nothing, moves no thread, and bills nothing (see
        # write_deltas_for_edition's moved_thread_ids). run_memory_pass appends the
        # state-rewrite step to report.steps; fold that late spend into the persisted
        # briefing cost so briefings.token_cost stays honest (money-honesty rule).
        if not no_threads:
            _emit_progress(progress, "state", "state", env=src_env)
            steps_before = len(report.steps)
            try:
                spent = run_memory_pass(con, date, key, cap, spent, briefs_by_slot,
                                        inputs["slots"], report)
            except Exception as exc:  # noqa: BLE001 — post-persist containment
                # BUG-34: the memory pass runs AFTER persist_generation (M1 gate
                # F reorder) — the edition is already ON THE RECORD. A raise here
                # must NOT crash a published edition. Disclose and contain: the
                # run completes, the artifact + generation log below still write,
                # and the moat is simply left unchanged for this run. (A failure
                # BEFORE persist keeps its abort behavior — this catches only the
                # post-persist window.)
                report.warnings.append(
                    f"memory pass failed after persist ({exc}) — the edition is "
                    "already PUBLISHED and unaffected; its delta ledger / "
                    "standing state may be incomplete for this run — run "
                    "`newslens memory-backfill --date <date>` for missing "
                    "ledger entries; a stale standing state catches up on the "
                    "thread's next real move (never re-run `generate` for "
                    "this: it would archive and rewrite the published edition)")
            finally:
                # _fold_cost_steps stays honest for any PARTIAL state spend the
                # pass recorded before raising (empty steps fold to nothing).
                late_steps = report.steps[steps_before:]
                if late_steps:
                    _fold_cost_steps(con, date, late_steps)
        # NL-75 the expiry register (post-persist, contained like the memory
        # pass): persist this edition's watch-fors as ledger-adjacent objects,
        # and record conversions for the expired items this edition addressed.
        # $0 spend; a failure never crashes a PUBLISHED edition.
        try:
            from . import memory_core as _mc
            _brow = con.execute("SELECT id FROM briefings WHERE date = ?",
                                (date,)).fetchone()
            _bid = _brow["id"] if _brow else None
            _mc.persist_watch_items(con, date, _bid, stories, inputs["slots"])
            for _story, _slot in zip(stories, inputs["slots"]):
                # D5: classify against the story BODY only — a re-shipped
                # observable sitting in `watch_for` must NOT close the debt.
                _prose = _story_body_prose(_story)
                for _w in (_slot.get("expired_watch") or []):
                    _outcome = _mc.classify_conversion(_w.get("observable", ""), _prose)
                    if _outcome:
                        _mc.record_watch_conversion(
                            con, _w, date, _bid, _outcome, _prose[:280])
        except Exception as exc:  # noqa: BLE001 — post-persist containment
            report.warnings.append(
                f"watch-items: register update failed after persist ({exc}) — "
                "the edition is PUBLISHED and unaffected")
    report.artifact_path = str(
        write_artifact(date, report.variant, report.sample, narrative, script,
                       no_threads=no_threads)
    )
    close_stage_timeline(report)          # NL-149 item 2 (see the failed arm)
    log_generation({
        "date": date, "variant": report.variant, "sample": report.sample,
        "no_threads": no_threads,
        "status": "ok",
        # NL-146: provenance, emitted only when the caller named it (see
        # run_generate's docstring on why the default is None and not
        # "interactive").
        **({"trigger": report.trigger} if report.trigger else {}),
        "tiers": [s.get("tier") for s in stories],
        "framings": [s.get("why_label") for s in stories],
        "editor": editor_note,
        "analysis_usd": round(report.analysis_usd, 6),          # CHARGED
        # NL-95: the cap figure beside the money figure. Equal on the api lane;
        # on the subscription lane analysis_usd is 0 and this is not.
        "analysis_usd_shadow": round(report.analysis_shadow_usd, 6),
        "memory_usd": round(report.memory_usd, 6),   # NL-63: state-rewrite spend
        "memory": report.memory,                     # NL-63: ledger/state instrumentation
        "deep_views": report.deep_views,  # Axel's asymmetry instrumentation
        "fetch_skipped": report.fetch_skipped,   # NL-151 clause 2/3
        # NL-151b: the depth tier as it actually ran. `tiers` above is the same
        # vector read off the shipped stories, and the two agreeing is a
        # property worth being able to check on the record rather than assert
        # in a test alone.
        "depth_slots": report.depth_slots,
        "draft_stories": draft_payload.get("stories"),  # carryover 18b: forensics
        "stories": stories,  # M7: the UI's structured render source (ADR-0010)
        "audio": audio_path_str,
        "warnings": report.warnings,
        "narrative_words": report.narrative_words,
        "per_story_words": report.per_story_words,
        "script_words": report.script_words,
        "per_story_tiers": [st.get("tier") for st in stories],
        "override_rendered": any(s.get("override") for s in inputs["slots"]),
        "revival_rendered": any(s.get("revived_threads") for s in inputs["slots"]),
        "continuity": report.continuity_status,
        "steps": report.steps,
        "total_usd": round(sum(s.get("usd") or 0 for s in report.steps), 6),
        "stage_timeline": report.stage_timeline,   # NL-149 item 2
        "elapsed_s": report.run_elapsed_s,
    })
    return report
