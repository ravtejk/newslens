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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import config, db, llm, memory, paths, ranking
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
OVERRIDE_TEXT_LABEL = (
    "**Outside your interests:** this story matches none of the tags or "
    "threads steering your selection; it's here because {reason}"
)
WINDOW_LINE = (
    "Generated {timestamp}. Covers items fetched {start} → {end}. NewsLens "
    "sees only its configured sources within this window."
)
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
    analysis_usd: float = 0.0          # M9-M3: the analysis stage's spend
    deep_views: Dict[str, str] = field(default_factory=dict)  # slot -> availability (Axel instrumentation)
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
# B2 uses this to transport editor/script on the Claude API Haiku seats and
# narrative on gpt-4o — closing the B1 "B4 residual" (a frozen writer transport
# under a per-step ledger) without a signature change. None => the writer seat
# (direct callers / the signature test keep the historical gpt-4o writer path).
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
# script_adapt do not carry it, so their (Haiku) prompts never split.
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
    # ledger attributes — editor/script ride the Claude API Haiku seat, narrative
    # stays gpt-4o. Because check_lane already passed for seat_cfg, the transport
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
                    # per-seat from the seam (editor/script are Haiku now), so the
                    # entry never forks the model that ran from the price the
                    # ledger records. B3-D2: the fall label rides too.
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
                    # anthropic (Haiku) editor/script seat names the RIGHT
                    # console; the openai arm is unchanged (the rollback path).
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
    narrative*->writer, editor*->editor, script*->script). B2: editor/script are
    the Claude API Haiku seats; the narrative/writer family stays gpt-4o."""
    return llm.resolve_seat(llm.seat_for_step(step))


def _est_cost(prompt: str, max_tokens: int, step: str = "narrative") -> float:
    # B2: the pre-call budget estimate uses the STEP'S seat prices (Haiku for
    # editor/script), not a global writer constant — so the ladder's headroom
    # math tracks the seat that will actually be charged.
    cfg = _step_seat_cfg(step)
    return (len(prompt) / 3.5 / 1e6) * cfg.usd_per_mtok_in + (
        max_tokens / 1e6
    ) * cfg.usd_per_mtok_out


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
    shadow keys, sourced from the STEP'S seat (B2: editor/script Haiku, narrative
    gpt-4o). Replaces the WRITER_MODEL + WRITER-rate _step_cost that forked the
    ledger the moment editor/script left gpt-4o. B3-D6: reads the SAME run-scoped
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

def load_briefing_inputs(con: sqlite3.Connection, date: str) -> Dict:
    row = con.execute(
        "SELECT * FROM briefings WHERE date = ?", (date,)
    ).fetchone()
    if row is None:
        raise GenerateError(
            f"no briefing row for {date} — generate the record first "
            "(a plain `newslens generate`), then request samples or "
            "narrative-only re-runs against it"
        )
    try:
        slots = json.loads(row["story_slots"] or "[]")
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

    window_meta = None
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
        corroboration = json.loads(row["corroboration_labels"] or "{}")
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
    }


def _override_reason(slot: Dict) -> str:
    label = slot.get("override_label") or ""
    if label.startswith(ranking.OVERRIDE_LABEL_PREFIX):
        return label[len(ranking.OVERRIDE_LABEL_PREFIX):].strip()
    return label.strip() or "it cleared a high global-impact bar"


def _slot_budget_line(slot_n: int) -> str:
    # Budget lines are tier-aware (A2). NL-63 M2 — the AMENDED slot contract:
    # the lead and both full-picture stories DOUBLE their Today-page depth, and
    # "In Brief" (slot 4+) is the OLD medium register (structured — NOT the dead
    # <=60-word snippet). Slots 1-3 are EXACTLY the three full-picture stories.
    # Fix (obs: the writer under-delivered the doubled bands by ~20% and the
    # lead came in at a third of its target): word targets are stated HARD, as
    # floors not decoration, and the lead's primacy is spelled out — the model
    # treated the old soft "~550-750" as optional and wrote a full-picture-
    # length lead. Steering, not a gate: only the 450-word briefed-lead floor
    # is enforced (with retry); these targets steer the whole edition up to band.
    if slot_n == 1:
        return ("FULL tier (the lead) — TARGET ~640 words, and NEVER under 550. "
                "This is THE LEAD: it must be the single LONGEST story of the "
                "day, visibly longer than any full-picture story below — a lead "
                "that reads as short as a slot-2 story is a failure. Spend the "
                "budget: lede 3-6 sentences; why_it_matters a full 8-12 "
                "sentences built from source specifics; watch_for 2-3 sentences")
    if slot_n in (2, 3):
        return ("MEDIUM tier (a full-picture story, DOUBLED depth) — TARGET ~440 "
                "words, floor 350, shorter than the lead but a real full "
                "picture: lede 3-5 sentences; why_it_matters 5-8 sentences; "
                "watch_for 1-2 sentences")
    return ("QUICK tier (the 'In Brief' register — a compact STRUCTURED mini-"
            "story, NOT a headline snippet) — TARGET ~220 words, floor 180: "
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

    story_parts = []
    for s in inputs["slots"]:
        n = s["slot"]
        lines = [f"STORY {n} — budget: {_slot_budget_line(n)}"]
        # NL-63 M2: slots 1-3 are the EXACTLY-THREE full-picture stories (1 lead
        # + 2 medium); slot 3 no longer demotes to quick (the amended contract
        # pins it to full-picture), so the old analyst medium-vs-quick annotation
        # is gone — _slot_budget_line already states MEDIUM for it.
        lines.append(f"working title (rewrite it): {s.get('story_title', '')}")
        lines.append(f"what happened (one line): {s.get('summary', '')}")
        if s.get("world_impact_reason"):
            lines.append(
                f"ranking's significance seed (rephrase, never paste): {s['world_impact_reason']}"
            )
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
        if s.get("override"):
            lines.append(
                "OVERRIDE STORY — outside the reader's tags (the pipeline "
                "renders its own label; your lede may acknowledge naturally)"
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
            if slot_no <= 3 and not (inputs.get("briefs_by_slot") or {}):
                pass  # whole stage absent: run-level warning already covers it
            elif slot_no <= 3:
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
) -> Tuple[List[Dict], List[str]]:
    """Structural checks BLOCK (retry-then-fail); style checks warn.
    Mandatory disclosures (revival dates) block."""
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
        allowed = (
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
            parts.append(OVERRIDE_TEXT_LABEL.format(reason=_override_reason(slot)))
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
        matches = ", ".join(
            [t["name"] for t in slot.get("matched_tags", [])]
            + slot.get("matched_memory", [])
        )
        # Latent bug found by the cold-start sample: no-match is not the same
        # as override — never point at a label that isn't there.
        if matches:
            here_for = matches
        elif slot.get("override"):
            here_for = "editor's override — see note above"
        else:
            here_for = "world-impact selection (no tag or thread match)"
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


# P3.1 item 3 (principal ruling (5)): the lead's tier must EXPRESS.
# NL-63 M2 re-derivation under the AMENDED contract: the edition total is now
# 1,800-2,500 lead-weighted and A2's lead band is 450-900; the lead target is
# ~640. The floor lands at 450 — the band minimum, above a full-picture story's
# ~440 so a briefed lead can never sink to full-picture length, and well under
# the 900 ceiling. Enforced hard-with-retry ONLY when a valid lead brief exists;
# thin days without a brief stay warn-free (the material excuse is real there).
LEAD_FLOOR_WORDS = 450


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
            lines.append(f"story {n}: OVERRIDE — reason: {_override_reason(s)}")
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
    numbers actually present — robust to non-contiguous slot ids."""
    ordered = sorted(int(s["slot"]) for s in inputs["slots"])
    k = _script_coverage(len(ordered))
    return set(ordered[:k])


def build_script_prompt(date: str, variant: str, narrative: str, inputs: Dict) -> str:
    template = (paths.PROMPTS_DIR / PROMPT_SCRIPT).read_text(encoding="utf-8")
    n_slots = len(inputs["slots"])
    _, per_desc, k = _script_budgets(n_slots)
    covered = script_covered_slots(inputs)
    others = k - 1
    if k <= 1:
        coverage_line = (
            "This edition has a single story — cover the LEAD only; there is no "
            "second story to air.")
    elif k >= n_slots:
        coverage_line = (
            f"This episode covers all {k} stories in the edition — the LEAD "
            f"(story 1, the deepest segment) plus the other {others}, in rank "
            "order. The lead is the episode's center of gravity.")
    else:
        last = max(covered)
        coverage_line = (
            f"This episode covers {k} stories — the LEAD (story 1, the deepest "
            f"segment) plus the {others} next-most-consequential. Cover stories "
            f"1 through {last} ONLY; the remaining {n_slots - k} stories are NOT "
            "in this episode — they live in the text briefing. The lead is the "
            "episode's center of gravity; never cover every story.")
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
        labels_block=build_labels_block(inputs, covered),
        narrative_text=narrative,
    )


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
            reason = _override_reason(s)
            reason_head = " ".join(reason.split()[:4]).rstrip(".,").lower()
            if "outside your" not in low:
                hard.append(f"story {n}: spoken override missing the outside-your-tags acknowledgment")
            if reason_head and reason_head not in low:
                hard.append(f"story {n}: spoken override missing its reason")
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
                    state_chat=None) -> float:
    """NL-63 M1 memory pass. Two writes: (1) the delta LEDGER — Pax's economy,
    ~$0, the validated arc persists as the thread's delta; (2) the standing
    STATE — the ONLY new LLM spend, and ONLY for threads that advanced/reversed
    today (write law), pre-checked against the $0.25 cap, stale-but-honest on
    any failure. Returns the updated `spent`. `state_chat` is injectable so the
    offline suite exercises this exact path without spending."""
    from . import memory_core
    brow = con.execute("SELECT id FROM briefings WHERE date = ?",
                       (date,)).fetchone()
    briefing_id = brow["id"] if brow else None
    delta_rep = memory_core.write_deltas_for_edition(
        con, date, briefing_id, briefs_by_slot, slots)
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
    rows, not volatile narrative-stage state — briefs_by_slot from
    latest_valid_brief, slots from the briefing's story_slots, the ledger from
    thread_deltas. The live inline pass reads the SAME sources (see
    _run_generate_body). So the backfill's delta-write + state-rewrite context is
    byte-identical to a live inline pass — there is NO degradation. The only
    difference is `spent`=0.0 (the backfill is its own run doing only the memory
    pass; the edition's generation cost was already billed to its token_cost), so
    the FULL cap is available to the state rewrite. The state-rewrite spend is
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
        from . import analysis as analysis_mod
        briefs_by_slot: Dict[int, Optional[Dict]] = {}
        for s in slots:
            n = int(s["slot"])
            if n <= 3:
                doc = analysis_mod.latest_valid_brief(con, date, n)
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
                                    slots, report, state_chat=state_chat)
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
# machinery pointed backwards; ~$0.01-0.02, GPT-4o via call_analysis_model), the
# same seam ANALYSIS_MODEL/STATE_MODEL ride. Refusal never fabricates; the spend
# is durable on the thread_baselines row. DO NOT run the retroactive sweep
# against real data — it is a principal checkpoint (and waits on the junk-sweep
# ruling); the command exists so it CAN be run, under his word, against the
# sandbox first.
# ===========================================================================
_BASELINE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")   # a plausible year, not any 4-digit quantity


def _default_baseline_chat(key: str, prompt: str) -> Tuple[Dict, float]:
    """The real backgrounder call on the ANALYSIS_MODEL seam (the existing
    analyst machinery — call_analysis_model: one retry, then raises; cost
    accumulates every billed attempt). Injectable so the offline suite exercises
    this exact path without spending."""
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
    cost_usd: float = 0.0


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
    cost)` is injectable so the suite spends nothing."""
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
        raw, cost = chat(key, prompt)
    except Exception as exc:  # noqa: BLE001 — degrade to an honest failed row
        # A transport failure is a FAILURE, not a "stale" (there is no prior
        # baseline to keep — unlike a state rewrite; the misleading borrowed name
        # is dropped). The row lands 'failed'; the spend (if any billed) rides on
        # the result (BUG-32 money-honesty class).
        res.cost_usd = float(getattr(exc, "usd_spent", 0.0) or 0.0)
        res.outcome = "failed"
        res.detail = f"baseline call failed ({type(exc).__name__}: {exc})"
        mc.record_baseline(
            con, thread_id, date, mc.BASELINE_STATUS_FAILED, reason=res.detail,
            model=analysis.ANALYSIS_MODEL, cost_usd=res.cost_usd)
        return res
    res.cost_usd = cost
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
    spent_usd: float = 0.0
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
        for t in targets:
            as_of = t["as_of"] or date
            gr = generate_thread_baseline(
                con, t["thread_id"], t["topic"], t["note"], as_of, key,
                remaining_usd=cap - spent, chat=chat)
            # Baseline rides the analyst seat (gpt-4o/api — not a subscription
            # seat), so usd_charged == usd_shadow; cost_usd is the cap figure.
            spent += gr.cost_usd
            rep.generated.append({"thread": t["topic"], "thread_id": t["thread_id"],
                                  "outcome": gr.outcome, "detail": gr.detail,
                                  "usd": round(gr.cost_usd, 6), "as_of": as_of})
            if gr.outcome != "written":
                rep.warnings.append(
                    f"baseline for {t['topic']!r} {gr.outcome} — {gr.detail}")
        rep.spent_usd = round(spent, 6)
        return rep
    finally:
        if own_con:
            con.close()


def persist_generation(
    con: sqlite3.Connection, date: str, narrative: str, script: str,
    steps: List[Dict], audio_path: Optional[str] = None
) -> None:
    """Write narrative/script onto the briefing row. If a narrative already
    exists (re-generation), archive the row to briefings_history first —
    same rule persist() applies on re-rank."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with con:
        row = con.execute("SELECT * FROM briefings WHERE date = ?", (date,)).fetchone()
        if row is None:
            raise GenerateError(f"briefing row for {date} vanished mid-run")
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


def log_generation(entry: Dict) -> None:
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
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


def run_generate(
    date: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
    env: Optional[dict] = None,
    variant_override: Optional[str] = None,
    refresh: bool = True,
    no_threads: bool = False,
    progress: Optional[Callable[[str, Optional[str]], None]] = None,
) -> GenReport:
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

    own_con = con is None
    if own_con:
        db.migrate()
        con = db.connect()
    try:
        try:
            return _run_generate_body(
                con, date, src_env, key, report, refresh, no_threads, progress
            )
        except GenerateError as exc:
            # BUG-6/32 family (NL-63 M2 obs): a run that aborts mid-pipeline
            # still spent real money — narrative, its floor retry, the editor,
            # and BOTH script attempts on a degenerate-stub abort all bill before
            # the raise. Fold that accumulated spend into the failed entry so
            # the money record is never a silent null. attempt_ledger is
            # call_llm's raw per-attempt cost record; the analysis stage runs
            # in its own module, so its spend is folded from report.analysis_usd
            # (and any pre-abort memory spend from report.memory_usd).
            ledger = list(report.attempt_ledger)
            for late_step, usd in (("analysis", report.analysis_usd),
                                   ("memory", report.memory_usd)):
                if usd:
                    ledger.append({"step": late_step, "usd": round(usd, 6)})
            log_generation({"date": date, "variant": variant, "sample": sample,
                            "status": "failed", "error": str(exc)[:500],
                            "steps": ledger,
                            "total_usd": round(
                                sum(s.get("usd") or 0 for s in ledger), 6),
                            "warnings": report.warnings})
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

    inputs = load_briefing_inputs(con, date)
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
    if refresh and not no_threads:
        _emit_progress(progress, "analysis", "analyst", env=src_env)
        try:
            a_rep = analysis_mod.run_analysis(
                date=date, con=con, env=src_env, already_spent=spent,
                tiers_override=["full", "medium", "medium"])
            spent += a_rep.get("total_usd") or 0.0
            report.analysis_usd = a_rep.get("total_usd") or 0.0
            for w in a_rep.get("warnings", []):
                report.warnings.append(f"analysis: {w}")
            if a_rep.get("derating"):
                report.warnings.append(
                    "analysis DERATING under the cap — escalation-flag class")
        except Exception as exc:  # noqa: BLE001 — stage-wide disclosed degrade
            report.warnings.append(
                f"analysis stage unavailable this run ({type(exc).__name__}: "
                f"{exc}) — writer degrades to feed-excerpt material, disclosed")
    for s in inputs["slots"]:
        n = int(s["slot"])
        if n <= 3:
            doc = analysis_mod.latest_valid_brief(con, date, n)
            if doc:
                briefs_by_slot[n] = doc
    # NL-63 M2: slot 3 is pinned to full-picture (medium) — the demote-to-quick
    # verdict is RETIRED (exactly-3 full-picture). analyst_slot3_tier is kept
    # only as the inputs marker some paths still read; it no longer alters tiers
    # or the deep-view ladder. deep_views reflects analyst-brief PRESENCE alone.
    analyst_slot3_tier = analysis_mod.analyst_slot3_tier(con, date)
    inputs["briefs_by_slot"] = briefs_by_slot
    inputs["analyst_slot3_tier"] = analyst_slot3_tier
    report.deep_views = {
        str(n): ("available" if briefs_by_slot.get(n) else "absent")
        for n in (1, 2, 3) if any(int(s["slot"]) == n for s in inputs["slots"])
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
    if spent + est > cap:
        raise GenerateError(
            f"estimated narrative cost ${est:.4f} exceeds the remaining budget "
            f"cap (${cap:.2f}) — aborting before the call"
        )
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

    # P3.1 item 3: tier expression. A briefed lead under the floor gets ONE
    # retry with the deficiency injected; a second miss ships with
    # disclosure (severity judgment: warn-after-retry, not a dead run —
    # the briefing always ships, per the reconciled ladder's spirit).
    lead_w = _lead_words(draft_payload)
    if (inputs.get("briefs_by_slot") or {}).get(1) and lead_w < LEAD_FLOOR_WORDS:
        floor_msg = (
            f"story 1 (the lead) ran {lead_w} words — FAR under its floor of "
            f"{LEAD_FLOOR_WORDS} (it has a full analysis brief, so the material "
            "excuse is gone). Rewrite the lead ALONE, much longer: TARGET ~640 "
            f"words, an absolute floor of {LEAD_FLOOR_WORDS}. The brief gives "
            "you a cited ledger, mechanism, effects, and unknowns — spend them: "
            "a full 8-12-sentence why_it_matters built from those source "
            "specifics is the bulk of the lift. The lead is THE LEAD: it must "
            "end up the LONGEST story of the day, clearly longer than any "
            "full-picture story. Keep every other story's tier and length "
            "exactly as they are.")
        retry_n_prompt = (n_prompt + "\n\n=== YOUR PREVIOUS DRAFT WAS "
                          "REJECTED — TIER-EXPRESSION VIOLATION (fix exactly "
                          "this; everything above still binds) ===\n- "
                          + floor_msg)
        est_rn = _est_cost(retry_n_prompt, NARRATIVE_MAX_TOKENS)
        if spent + est_rn > cap:
            report.warnings.append(
                f"lead tier floor: {lead_w} words < {LEAD_FLOOR_WORDS} "
                f"(retry skipped — would exceed the cap) — shipped with "
                "disclosure")
        else:
            try:
                _, usage_rn = call_llm(
                    key, retry_n_prompt, "narrative_retry",
                    NARRATIVE_MAX_TOKENS, NARRATIVE_TEMPERATURE, True,
                    validate=_shape_check, cost_sink=report.attempt_ledger,
                )
                retry_payload = draft_holder[0]
                step_rn = {"step": "narrative_retry",
                           "prompt_tokens": usage_rn.get("prompt_tokens"),
                           "completion_tokens": usage_rn.get("completion_tokens"),
                           **_step_ledger("narrative_retry", usage_rn)}
                report.steps.append(step_rn)
                spent += step_rn["usd_shadow"] or 0   # cap on shadow (see above)
                retry_w = _lead_words(retry_payload)
                if retry_w >= LEAD_FLOOR_WORDS:
                    draft_payload = retry_payload
                    report.warnings.append(
                        f"lead tier floor: retry brought the lead {lead_w} "
                        f"-> {retry_w} words")
                elif retry_w > lead_w:
                    draft_payload = retry_payload
                    report.warnings.append(
                        f"lead tier floor: retry improved {lead_w} -> "
                        f"{retry_w} words, still under {LEAD_FLOOR_WORDS} — "
                        "shipped with disclosure")
                else:
                    report.warnings.append(
                        f"lead tier floor: retry did not improve ({lead_w} "
                        f"words) — shipped with disclosure")
            except GenerateError as exc:
                report.warnings.append(
                    f"lead tier floor retry failed ({exc}) — {lead_w}-word "
                    "lead shipped with disclosure")

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
        if spent + est_e > cap:
            raise GenerateError(
                f"editor pass estimate ${est_e:.4f} would exceed the run cap"
            )
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
        # P3.1 item 3 (editor guard): tightening never cuts a briefed lead
        # below its tier floor — the M6 cut power gains a floor, not a new
        # power. A violating edit is DISCARDED via the existing degrade
        # path (ValueError -> draft, disclosed).
        if (inputs.get("briefs_by_slot") or {}).get(1) \
                and edited_payload is not draft_payload \
                and _lead_words(edited_payload) < LEAD_FLOOR_WORDS \
                and _lead_words(draft_payload) >= LEAD_FLOOR_WORDS:
            raise ValueError(
                f"editor cut the lead to {_lead_words(edited_payload)} words "
                f"— below its {LEAD_FLOOR_WORDS}-word tier floor (the draft "
                "met it)")
        # A9 preserve-enforcement (editor-preservation batch): the teeth. A
        # dated ledger callback the DRAFT carried whose (date + subject) fact no
        # longer survives the edit is DISCARDED via this SAME degrade path —
        # exactly the LEAD_FLOOR mirror above: raise ValueError -> the edit is
        # dropped, the writer's draft ships with disclosure. This is the direct
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
    if spent + est_s > cap:
        raise GenerateError(
            f"estimated script cost ${est_s:.4f} would exceed the run budget "
            f"cap (${cap:.2f}, ${spent:.4f} already spent) — narrative was NOT "
            "persisted; raise the cap or re-run"
        )
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
    # step_n before its floor-retry pre-check. Without this the retry
    # pre-check under-counts true spend by one script call, and a run can
    # overshoot the cap by one retry. (report.steps keeps its original
    # append position after the block.)
    step_s = {"step": "script_adapt",
              "prompt_tokens": usage_s.get("prompt_tokens"),
              "completion_tokens": usage_s.get("completion_tokens"),
              **_step_ledger("script", usage_s)}
    spent += step_s["usd_shadow"] or 0.0   # script: subscription-lane seat — cap on shadow

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
        persist_generation(con, date, narrative, script, report.steps,
                           audio_path=audio_path_str)
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
    log_generation({
        "date": date, "variant": report.variant, "sample": report.sample,
        "no_threads": no_threads,
        "status": "ok",
        "tiers": [s.get("tier") for s in stories],
        "framings": [s.get("why_label") for s in stories],
        "editor": editor_note,
        "analysis_usd": round(report.analysis_usd, 6),
        "memory_usd": round(report.memory_usd, 6),   # NL-63: state-rewrite spend
        "memory": report.memory,                     # NL-63: ledger/state instrumentation
        "deep_views": report.deep_views,  # Axel's asymmetry instrumentation
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
    })
    return report
