"""Ranking + corroboration (milestone 3): the editor's story budget.

Pipeline position (spec §B steps 2-3): ingested source_items -> LLM-assisted
clustering & scoring -> deterministic selection -> corroboration labels ->
briefings row (story_slots + corroboration_labels + token_cost) + a
ranking_runs instrumentation row.

DIVISION OF JUDGMENT (ADR-0004): the LLM decides only what is genuinely
semantic — which items are the same story, which of the principal's tags a
story actually matches, and a world-impact score. NL-138 (principal ruling
2026-08-02, DECISIONS "SEVEN-ITEM RULING SLATE" item ④) took the model's
one-sentence PROSE reason out of that list entirely — see the override label
below. Everything above that layer is deterministic, inspectable code: tag weights
(topic 1.0 / domain 0.5 — taxonomy contract §B rule 4), the followed-analyst
boost, slot selection, the urgency override gate and its cap, and
corroboration counting. "Why did this story rank?" must always be answerable
from stored data, not from a model's mood.

THE URGENCY OVERRIDE (taxonomy contract §E, Kass's dissent binding):
  * Pool: clusters with ZERO personal signal (no tag, no memory, no followed
    source). Followed-analyst content is never override material — it already
    carries a personal signal.
  * Bar: world_impact >= OVERRIDE_THRESHOLD (8/10 = "global systemic
    consequence... not merely widely covered"). Cap: at most 1 of the 5 slots.
    The slot may go unfilled — that is a normal outcome, not a failure.
  * Label (NL-138, principal ruling ④ 2026-08-02 — THE PROSE REASON IS DEAD):
    a fired override renders the CODE-OWNED tag form, composed at render time
    from `labels.WHY_CHOSEN_BECAUSE` + `labels.WHY_WORLD_NEWS` ("Chosen
    because: Important World News"). Nothing about the label is stored on the
    slot and nothing about it comes from the model. What used to stand here —
    `override_label = OVERRIDE_LABEL_PREFIX + the model's one-sentence reason`,
    persisted per slot and rendered verbatim — died with the field it wrapped.
    His finding, verbatim: a reason like the Iran one "implies the user had
    global oil or middle east stability or energy prices or international
    shipping as one of their topics, which they didn't." The model was scoring
    WORLD importance and the sentence read as a claim about USER relevance;
    that register error is misinformation about the product's own behaviour,
    and no amount of prompt tuning makes a world-impact sentence honest about
    a reader it was never shown. Still rendered every time, still no silent
    fallback — the form is just no longer prose.
  * Instrumented: every run (fired or not) appends a ranking_runs row with the
    pool size, threshold, and outcome — the day-14 recalibration reads these.
    NL-138: it reads the STRUCTURED fields (world_impact, fired, matched tags),
    which is what it always keyed on; the prose `reason` key is gone from the
    meta for the same reason it is gone from the slot (ledger death, ruled).

STRUCTURED-OUTPUT DISCIPLINE (ENGINEERING.md): the LLM response is validated
hard (shape, id existence, no cross-cluster id reuse, tag names/levels only
from the provided sets, score ranges). One retry total; then a visible
RankingError — never silent garbage downstream. Failed runs still log a
ranking_runs row with status=failed for the instrumentation trail.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from . import config, db, llm, memory, paths, steering

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
# Active ranking model + prices — B2 (approved Option C) moved the rank seat off
# gpt-4o onto the Claude lane; it has moved again since (ENG-M0: Sonnet 5,
# subscription), which is exactly why no model id is written here. The seam's
# SEATS["rank"] row is the SINGLE SOURCE OF TRUTH for model + price; these
# module names DERIVE
# from it so the legacy `usd` ledger key, the pre-call estimate, and the
# persisted token_cost label all track the seat automatically (dispatch B2:
# "shadow math must use per-seat prices, not a global constant"). REVERT =
# flip SEATS["rank"] back to **_GPT4O_API in llm.py, one clean diff. Historical
# note: rank ran gpt-4o at $2.50/$10.00 from 2026-07-05 (ADR-0004 up-tier).
RANK_MODEL = llm.SEATS["rank"].model
RANK_USD_PER_MTOK_IN = llm.SEATS["rank"].usd_per_mtok_in
RANK_USD_PER_MTOK_OUT = llm.SEATS["rank"].usd_per_mtok_out
# The documented fallback rung (kept as a constant so the fallback is a named
# fact, not lore): gpt-4o-mini ran ranking M3 -> M5-day-1.
MODEL = "gpt-4o-mini"
LLM_TIMEOUT_S = 90
# 3000, raised from 1600 in M4: with memory threads in play, 12 clusters of
# title+summary+reason+matches measured right at the old cap and the model's
# JSON truncated mid-string on a live run (~$0.0018 worst case at 4o-mini
# rates — the budget guard scales with this constant automatically).
#
# ENG-M0 (2026-08-06): 3,000 -> 36,000. THE BUG THIS CLOSES, stated plainly:
# 3,000 has been truncating real days on the API lane for months and nobody saw
# it, because the SUBSCRIPTION lane — the default since B3 — sends no max_tokens
# at all and therefore ignores this constant entirely. The api lane is the
# registered fall-over, so the failure was latent: the day the subscription lane
# went unavailable and NEWSLENS_LANE_FALLBACK=api fired, rank would have
# truncated at 3,000 and died on `finish_reason == "length"` (ranking.py's
# truncation guard) on BOTH attempts, killing the edition — with the cap-hit
# error naming a number nobody had revisited since gpt-4o-mini.
#
# THE ARITHMETIC (measured, not guessed):
#   * observed all-time rank output ceiling on this seat, read read-only from
#     ranking_runs.token_usage (n=9 subscription runs): 22,748 completion tokens
#     (run 45, 2026-07-25, Haiku 4.5 + thinking, 550-item pool). p50 ~16,750.
#   * MEASURED on the SHIPPED post-flip seat (Sonnet 5, adaptive/high,
#     subscription, live probe 2026-08-06, n=2 against the real founder pool):
#     16,508 out @ 550 items (148.7s) and 13,407 out @ 780 items (120.1s) —
#     both comfortably BELOW the historical Haiku ceiling, and the 780 draw
#     LOWER than the 550 draw, so the bigger pool does not inflate output.
#   * size against the CONSERVATIVE ceiling, not the measurement: 22,748 x 1.5
#     margin = 34,122 -> ship 36,000. That is 1.58x the all-time observed
#     ceiling and 2.7x the measured 780-item draw.
# Thinking BILLS AS OUTPUT and counts against max_tokens on the api lane, so
# this budget covers deliberation + JSON together — which is why it is sized off
# a thinking-ON observation and not off the ~6.5KB of JSON the seat actually
# returns.
#
# TWO CONSEQUENCES, both deliberate and both load-bearing:
#   1. STREAMING. 36,000 is above llm._STREAM_MIN_MAX_TOKENS (5,000), so the api
#      fall-over now takes the SSE streaming path. That is the point, not a side
#      effect: streaming turns llm's cfg.timeout_s from a TOTAL wall-clock cap
#      into a per-read INTER-EVENT idle bound, which is the only way a 120-150s
#      rank call survives on a 90s api timeout. At 3,000 the call was on the
#      blocking json.load path and a slow draw idle-died (the NL-93 class).
#   2. THE PRE-CALL ESTIMATE RISES. estimate_cost_usd prices the full output
#      ceiling, and ranking.py's `est > cap` check is a HARD ABORT before the
#      call. At Sonnet $3/$15 with a 780-item prompt the estimate goes
#      ~$0.114 -> ~$0.609. That is intentional headroom-pricing, it is ~2x the
#      measured real spend (~$0.31), and the run cap must accommodate it — which
#      is exactly why the cap re-derivation rides in this same batch.
MAX_COMPLETION_TOKENS = 36000
PROMPT_FILE = "rank_select.txt"
USER_AGENT = "NewsLens/0.1 (personal news briefing prototype; ranking)"

# Recency rule (principal amendment 2026-07-04): candidate stories must have
# occurred/developed since the last briefing, or within the cap, whichever
# window is SHORTER. Principal gave 10-14 days; 14 chosen as the constant
# (ADR-0004 amendment). First-ever briefing defaults to the cap. "Developed"
# is measured by fetch time (first-seen) — published_at is too unreliable
# across feeds to anchor eligibility.
#
# NL-142 (2026-08-06) — WHAT "FETCH TIME (FIRST-SEEN)" NOW MEANS. Read this
# before reasoning about the window; the words above did not change but the
# fact underneath them did.
#   BEFORE: ingest keyed on (url, UTC fetch-day), so an item still sitting in
#   a feed re-inserted every day carrying that day's stamp. "Fetched inside
#   the window" therefore decoded to "STILL SITTING IN THE FEED": nothing
#   aged out while its feed kept carrying it, and a frozen feed (see
#   ingest.STALE_FEED_DAYS) became a perpetual-freshness machine pumping
#   years-old stories in as fresh candidates, forever. Live specimen: the CNN
#   top-stories feed, frozen at 2023-04-25, contributed 20 pool slots a day.
#   AFTER: one URL is one row and its fetched_at is its FIRST sighting, which
#   never moves. "Fetched inside the window" now decodes to "FIRST SEEN
#   INSIDE THE WINDOW" — what this comment always claimed it meant.
# THE REAL BEHAVIORAL CONSEQUENCE, stated plainly so nobody rediscovers it as
# a bug: a slow-developing story that lingers in a feed under one unchanged
# URL for longer than RECENCY_CAP_DAYS now LEAVES the candidate pool, where
# before it stayed indefinitely. That is the recency law as written, finally
# enforced — and it is the mechanism by which the pool ages honestly instead
# of accumulating until the cap evicts by file position. A genuinely fresh
# development re-enters as its own URL.
# Full reasoning: adr/0022-nl142-url-identity-and-first-seen-recency.md.
RECENCY_CAP_DAYS = 14
# ENG-M0 (2026-08-06): 550 -> 780, and VALID ONLY WITH THE SONNET RANK SEAT.
#
# THE NUMBER. NL-142 measured the raise at 1.42x; 550 x 1.42 = 781 -> 780 (a
# round number that keeps the arithmetic pins legible). What makes it SAFE is not
# arithmetic but a live measurement: the rank seat flipped Haiku -> Sonnet 5 in
# this same batch, and the probe battery ran the REAL founder pool at both sizes
# on the shipped seat (2026-08-06, subscription lane):
#     550 items, n=3 -> 132.0-148.7s, 15,034-16,508 out, 12 clusters, 0 repairs
#     780 items, n=3 -> 120.1-156.6s, 13,407-17,425 out, 12 clusters, 0 repairs
# The n=6 battery shows overlapping ranges, not a faster-bigger claim; the
# output budget is sized off the 22,748 all-time output ceiling, never off any
# single draw pair. Neither size in any draw
# reproduced the id-transcription slip class that killed run 48 (a mis-copied
# 'B15H') and fresh1 run 3 under Haiku. A bigger list under the seat that already
# slipped twice at the smaller list would be the wrong direction — both halves
# land together or neither does.
MAX_INPUT_ITEMS = 780

# ---------------------------------------------------------------------------
# FAIR-FILL (ENG-M0) — the cap stops evicting whole ingest runs
# ---------------------------------------------------------------------------
# THE PATHOLOGY, measured read-only on the founder DB (2026-08-06). One ingest
# run stamps every row it writes with a single shared `fetched_at` (NL-142), so
# `ORDER BY fetched_at DESC, id DESC LIMIT N` consumes runs WHOLE, newest first.
# The real 14-day window held SEVEN ingest stamps totalling 4,014 items:
#     2026-08-06  574 | 08-03  583 | 08-02  563 | 08-01  583
#     07-26       561 | 07-25  585 | 07-24  565
# At a 550 cap the pool was therefore 550 rows of the 2026-08-06 run and NOTHING
# ELSE — six in-window ingest runs evicted entirely, every day, silently. That is
# the class NL-142's pool receipts made visible ("eviction follows your
# sources.yaml order, not story age"); this is the fix that surface was asking
# for, and it is why the raise ALONE would not have been enough: 780 still only
# reaches one-and-a-bit runs.
#
# THE RULE — a guaranteed floor per stamp, then recency takes the rest.
# Every distinct in-window ingest stamp is guaranteed up to
# FAIR_FILL_MIN_PER_STAMP slots; whatever remains is filled newest-stamp-first
# until the cap is full. Within a stamp, order is unchanged (id DESC).
#
# WHY A FLOOR AND NOT AN EQUAL QUOTA. An equal split (780/7 = 111 each) would be
# "fairer" and WRONG: this is a daily briefing, the recency law is deliberate,
# and handing a 14-day-old ingest run the same weight as this morning's would
# invert it. The floor ends the wholesale-eviction pathology without touching the
# recency bias. Worked against the real window above: 7 stamps x 20 = 140
# guaranteed, 640 left; the newest run takes ALL 574 of its rows, the next takes
# 106, the remaining five keep their 20 apiece. The reader's newest ingest is
# still complete — it just no longer consumes the entire pool.
#
# 20 IS DELIBERATELY SMALL: a presence guarantee, not a quota. It costs the
# newest run nothing while the window holds few stamps, and it degrades
# gracefully — a stamp with fewer rows than the floor contributes what it has and
# its unused slots return to the recency fill.
FAIR_FILL_MIN_PER_STAMP = 20
MAX_CLUSTERS = 12
# ---------------------------------------------------------------------------
# PER-CLUSTER ITEM CAP (NL-133, chartered by the NL-130 gate ruling R-B /
# commit constraint C-4: "the structural end of the quadratic-source-map
# class"). MAX_INPUT_ITEMS bounds the POOL; nothing bounded what one cluster
# could take out of it, and `render_source_map` (analysis.py) is O(n^2) in the
# count of keys sharing an outlet — every key names every sibling on its own
# line. A validation-passing payload that put 63+ same-outlet items in one
# cluster therefore rendered a source map past PROMPT_MARGIN_CHARS (40,000),
# which re-opened the pay-then-skip sliver the 2026-08-01 ordering ruling
# closed. The margin cannot fix this: no constant bounds a quadratic. A cap on
# the INPUT does, which is why the fix lives here and not there.
#
# THE VALUE, derived (full derivation in research/2026-08-02--nl133-build.md;
# every number measured, none guessed):
#   * FLOOR — the all-time observed maximum is 44 items in one cluster (175
#     cluster records swept read-only across `briefings` + `briefings_history`;
#     the next-largest is 26). 48 never truncates a shape this product has ever
#     produced.
#   * CEILING — 48 is the largest cap whose WORST renderable prompt still fits
#     `brief_bound_chars` (analysis.py). Measured through the real constructors
#     (every key on ONE host, plus the 8 Sonar keys `_sonar_verify` allows on
#     that same host and CONTEXT_CAP=15 prior-briefing keys).
#     RE-MEASURED 2026-08-13 (NL-142), AND THIS PARAGRAPH WAS STALE. It read
#     "49 is the largest ... cap 48 -> 77,164 (slack 1,457); cap 49 -> +586;
#     cap 50 BREACHES by 295", which were NL-133's numbers at NL-133's field
#     maxima. NL-139 then raised two of those inputs from observations to
#     CLAMPS (Sonar title 183 -> 200, memory topic 64 -> 80) and the pin —
#     which reads the constants, so it never went red — moved underneath the
#     prose. The current ladder, off the same pin's own harness at 61fef71 and
#     unchanged by NL-142: cap 48 -> 77,780 vs the 78,621 bound (slack 841);
#     cap 49 -> 78,651, WHICH ALREADY BREACHES by 30; cap 50 -> 79,532.
#     The real headroom is ONE item, not two: 48 is AT the ceiling, not one
#     step inside it. Nothing here changes behaviour — the cap and the pin were
#     always right — but the margin this cap is chosen against is tighter than
#     the record said, which is the same finding NL-142 reports for the ingest
#     clamps (analysis.py, "THE MARGIN IS EXHAUSTED").
#     AND THE LADDER ABOVE IS ITS HARNESS'S, NOT THE MACHINE'S (NL-142 fix
#     loop 2, gate F-G0). That harness puts the Sonar keys on a 27-char shared
#     host, where nothing clamps; the TRUE worst case puts every clamped field
#     at its clamp with the Sonar keys sharing a host the vendor door still
#     admits. Measured through the real constructors after the F-G0 close:
#       cap 46 -> 76,780 (slack 1,841) · cap 47 -> 77,643 (slack 978)
#       cap 48 -> 78,516 (slack   105) · cap 49 -> 79,399 (BREACH by 778)
#     Same verdict, one third of the room: 48 is AT the ceiling, and the true
#     slack behind it is 105 chars rather than 841. BEFORE the close, the same
#     ladder breached at EVERY rung — 48 -> 81,972, over by 3,351 — so the
#     "48 is at the ceiling" line was true of a machine that did not exist
#     until fix loop 2 landed. Both ladders are pinned: NL-133's in its own
#     file, the true one in tests/test_nl142_ingest_bounds.py.
#   * The window [45, 48] is NARROW — ~9% over today's real maximum (it read
#     [45, 49] before the 2026-08-13 re-measurement above). That is a
#     property of the 40,000 allowance, not of this cap, and it is on the
#     record: raising MAX_CLUSTER_ITEMS REQUIRES raising PROMPT_MARGIN_CHARS
#     (a money-guard constant — bound_usd rises with it). The arithmetic pin in
#     tests/test_nl133_cluster_item_cap.py enforces exactly that coupling from
#     the constants, so the class cannot silently re-open.
# Over-cap clusters are TRUNCATED, never rejected: a degenerate cluster is the
# ranker model's doing, and refusing the whole payload would cost a re-draw for
# a shape we can honestly repair. Which items survive: `_cap_cluster_items`.
MAX_CLUSTER_ITEMS = 48
# NL-63 M2 — the AMENDED slot contract (DECISIONS 2026-07-13): minimum SIX
# stories surfaced, 6-7 by the day's material. MAX_SLOTS is the upper clamp;
# SLOT_FLOOR is the floor a normal day should clear — a thinner day ships fewer
# WITH a disclosure line, never padded to the floor (Rook's thin-day rule).
MAX_SLOTS = 7
SLOT_FLOOR = 6
# The analyst-briefed FULL-PICTURE tier = the top 3 slots (1 lead + 2 medium).
# EXACTLY three, and — the fragmentation contract — thread-DISTINCT (a causal
# arc gets ONE prominent slot; siblings demote to In Brief).
ANALYST_TIER_SLOTS = 3
# Fragmentation (NL-61/62 item D): the tripwire flags a suspected same-event
# FAMILY in the analyst tier when two of its slots share this many proper nouns
# — Rook's deterministic check that catches a no-thread day-zero crisis the
# thread cap can't (FLAGS for the day-14 read, never folds).
TRIPWIRE_PROPER_NOUN_OVERLAP = 3
# Quiet-thread demotion (NL-57): a tracked thread (has a ledger) re-covered
# without new development yields its prominent slot. Content-novelty proxy —
# max token-Jaccard of a candidate against the PRIOR edition's stories: at/above
# ZERO it is the same story with nothing new (Following only); at/above SMALL it
# has moved a notch (a still-tracking In Brief snippet); below, a real new
# development (normal selection). Deterministic, code-owned (Remy's proxy).
QUIET_ZERO_JACCARD = 0.60
QUIET_SMALL_JACCARD = 0.35

# Personal-impact weights (taxonomy contract §B rule 4 + §A followed_analyst).
# Deliberately code constants, not env vars: tuning them is a reviewed diff.
TOPIC_WEIGHT = 1.0
DOMAIN_WEIGHT = 0.5
MEMORY_WEIGHT = 1.0          # an active live thread matches at topic grade
FOLLOWED_BOOST = 0.35        # additive personal credit for followed writers
PERSONAL_SHARE = 0.55        # combined = 0.55*personal + 0.45*world/10
OVERRIDE_THRESHOLD = 8       # of 10 — "global systemic consequence"

# OVERRIDE_LABEL_PREFIX is DELETED (NL-138, ruling ④ 2026-08-02). It existed
# only to prefix the model's prose reason ("This story doesn't match your
# tagged interests, but we included it because " + <sentence>), so it died with
# the sentence. Deleted rather than kept-as-retired: unlike labels.py — whose
# RETIRED-NOT-RENDERED marker exists because that module IS the string table
# every surface imports by name — this constant had four consumers and the
# NL-138 sweep re-pointed every one of them at labels.WHY_CHOSEN_BECAUSE /
# labels.WHY_WORLD_NEWS. A dangling name here would be an invitation to
# re-compose the dead label, not a safety rail.

# Standing caveat — rendered in every rank output AND stored, per the 07-02
# corroboration ruling ("caveat in the output, not just in docs").
CORROBORATION_CAVEAT = (
    "Corroboration counts distinct outlets in your source list; it does not "
    "detect uncredited wire-service reuse beyond the excluded domains listed, "
    "and a single well-sourced report is not automatically less reliable than "
    "several outlets repeating one wire story."
)

# Active-rank-model pricing, for the pre-call budget estimate + cost log
# (tracks RANK_MODEL; mini's 0.15/0.60 return with the fallback if reverted).
USD_PER_MTOK_IN = RANK_USD_PER_MTOK_IN
USD_PER_MTOK_OUT = RANK_USD_PER_MTOK_OUT


class RankingError(RuntimeError):
    """Visible, handled ranking failure — the CLI prints it and exits 1."""


@dataclass
class RankedSlot:
    slot: int
    story_title: str
    summary: str
    item_ids: List[int]
    outlets: List[str]                 # distinct, as stored on the items
    matched_tags: List[Dict[str, str]]
    matched_memory: List[str]
    followed_analyst: bool
    personal_score: float
    world_impact: int
    combined_score: float
    override: bool
    # NL-138: `override_label` is GONE from the slot. The override FLAG above
    # is the whole persisted record of the event — every surface that used to
    # read the stored label now composes the tag form from the flag at render
    # time. Old briefings rows still carry the key in their story_slots JSON;
    # nothing reads it (sweep in research/2026-08-02--nl138-build.md), and
    # every reader goes through `.get()`, so an archived edition renders the
    # same tag form a fresh one does.
    corroboration_count: int
    corroboration_label: str
    wire_items_excluded: int
    # Lifecycle v2 (ADR-0006): dormant-thread matches are MATCH-ONLY — they
    # contribute nothing to any score; they exist so a slot-earning story can
    # auto-revive a thread. revived_threads is filled by persist() with the
    # pre-revival coverage date for the narrative's back-reference.
    matched_dormant: List[str] = field(default_factory=list)
    revived_threads: List[Dict] = field(default_factory=list)
    # `world_impact_reason` is DELETED (NL-138, ruling ④ 2026-08-02). M5 added
    # it as seed material for the writer's "Why it matters" movement (content
    # §5.1, ADR-0007 item 9) and NL-134 F3 moved it to the deep view; the
    # ruling ends both. The prose is not written any more (the rank prompt
    # stopped asking), not stored (this field), and not rendered (the deep
    # view's WHY_FULL_REASON block is gone). What the day-14 override
    # calibration reads — world_impact, `override`, matched_tags — is
    # untouched and was always the structured half.
    # NL-57 quiet-thread demotion: a tracked thread re-covered with only a small
    # development since the last edition surfaces as a demoted "still tracking"
    # In-Brief snippet, never a prominent slot. The note carries the dated
    # context ("no movement since <date>") from the ledger; the render composes
    # the full still-tracking register (state + next fixed point) at read time.
    still_tracking: bool = False
    still_tracking_note: str = ""


@dataclass
class RankReport:
    date: str
    slots: List[RankedSlot] = field(default_factory=list)
    caveat: str = CORROBORATION_CAVEAT
    override_fired: bool = False
    override_pool_size: int = 0
    item_count: int = 0
    cluster_count: int = 0
    window_days: float = 0.0        # recency window actually applied
    window_basis: str = ""          # "since your last briefing" / cap
    history_days: float = 0.0       # how much ingested lookback really exists
    token_usage: Dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def local_today(now: Optional[Callable[[], datetime]] = None) -> str:
    """The org's ONE definition of "today". The optional clock exists so a
    caller that already carries an injectable clock (`schedule.run_scheduled`,
    NL-146 fix loop 1 / QA F-8) can pin its day through this function instead of
    re-deriving the expression beside it — a second spelling of "today" is how a
    scheduler and a renderer end up disagreeing about which day it is."""
    return (now or datetime.now)().strftime("%Y-%m-%d")


def candidate_window(
    con: sqlite3.Connection, target_date: str, now_utc: Optional[datetime] = None
) -> Dict:
    """The recency window (principal amendment 2026-07-04):
    window = min(time since the last briefing, RECENCY_CAP_DAYS).

    "Last briefing" excludes the row for target_date itself — an idempotent
    re-rank of the same date must use the window since the PREVIOUS briefing,
    not since its own prior version minutes ago. First-ever briefing (no
    prior row) defaults to the cap.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    cap_start = now_utc - timedelta(days=RECENCY_CAP_DAYS)
    row = con.execute(
        "SELECT MAX(generated_at) AS last_at FROM briefings WHERE date != ?",
        (target_date,),
    ).fetchone()
    last_at = row["last_at"] if row else None
    basis = "first briefing — full cap"
    start = cap_start
    if last_at:
        try:
            last_dt = datetime.strptime(last_at[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            last_dt = None
        if last_dt is not None:
            last_dt = min(last_dt, now_utc)  # clock-skew clamp
            if last_dt > cap_start:
                start, basis = last_dt, "since your last briefing"
            else:
                basis = f"{RECENCY_CAP_DAYS}d cap (last briefing is older)"
    days = round((now_utc - start).total_seconds() / 86400.0, 2)
    return {
        "start_iso": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "days": days,
        "basis": basis,
        "last_briefing_at": last_at,
        "cap_days": RECENCY_CAP_DAYS,
    }


def ingested_history_days(
    con: sqlite3.Connection, now_utc: Optional[datetime] = None
) -> float:
    """How far back ingested items actually go — the honesty half of the
    recency rule: RSS feeds carry limited history, so early runs may have far
    less lookback than the window requests, and the report must say so."""
    now_utc = now_utc or datetime.now(timezone.utc)
    row = con.execute("SELECT MIN(fetched_at) AS oldest FROM source_items").fetchone()
    if not row or not row["oldest"]:
        return 0.0
    try:
        oldest = datetime.strptime(row["oldest"][:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return 0.0
    return max(0.0, round((now_utc - oldest).total_seconds() / 86400.0, 2))


def gather_items(con: sqlite3.Connection, start_iso: str) -> List[sqlite3.Row]:
    """Items fetched inside the candidate window, newest first, capped.

    Cluster eligibility ("a cluster qualifies iff its NEWEST item is
    in-window — an old story with a fresh development qualifies") holds BY
    CONSTRUCTION here: every clusterable item is in-window, so every cluster's
    newest item is too. The old story qualifies through its fresh items; we
    don't resurrect out-of-window rows to prove a story's age."""
    rows = con.execute(
        "SELECT id, source_type, outlet, url, title, published_at, fetched_at,"
        " wire_syndication_flag FROM source_items WHERE fetched_at >= ?"
        " ORDER BY fetched_at DESC, id DESC",
        (start_iso,),
    ).fetchall()
    return fair_fill(rows, MAX_INPUT_ITEMS, FAIR_FILL_MIN_PER_STAMP)


def fair_fill(rows, cap, floor):
    """Apply the cap WITHOUT evicting whole ingest runs (ENG-M0 — see the
    FAIR_FILL_MIN_PER_STAMP block above for the measured pathology).

    `rows` arrives newest-stamp-first, id DESC within a stamp; the return
    preserves exactly that order, so nothing downstream sees a reordering — the
    only thing that changes is WHICH rows survive the cap.

    Two passes, both cheap and both pure:
      1. FLOOR — walk the stamps newest-first and reserve up to `floor` rows for
         each. A stamp with fewer rows than the floor reserves only what it has,
         and the slack returns to pass 2 automatically (the reservation is
         counted, not assumed).
      2. RECENCY FILL — spend whatever the cap has left, newest stamp first,
         topping each stamp up beyond its floor until the cap is full.

    Degenerate cases are the important ones and they are all no-ops: a window
    with ONE stamp reduces to the old `LIMIT cap` behaviour exactly (pass 1
    reserves `floor`, pass 2 hands the same stamp everything else); a window
    already under the cap returns every row; floor <= 0 disables fair-fill
    entirely and restores the pre-ENG-M0 semantics."""
    if cap <= 0:
        return []
    if len(rows) <= cap:
        return list(rows)

    by_stamp = {}                       # stamp -> [row, ...] (order preserved)
    order = []                          # stamps, newest first
    for r in rows:
        stamp = r["fetched_at"]
        if stamp not in by_stamp:
            by_stamp[stamp] = []
            order.append(stamp)
        by_stamp[stamp].append(r)

    # Pass 1: the floor.
    take = {}
    budget = cap
    if floor > 0:
        for stamp in order:
            n = min(floor, len(by_stamp[stamp]), budget)
            take[stamp] = n
            budget -= n
            if budget <= 0:
                break
    # Pass 2: recency fill.
    for stamp in order:
        if budget <= 0:
            break
        room = len(by_stamp[stamp]) - take.get(stamp, 0)
        if room <= 0:
            continue
        extra = min(room, budget)
        take[stamp] = take.get(stamp, 0) + extra
        budget -= extra

    kept = set()
    for stamp, n in take.items():
        for r in by_stamp[stamp][:n]:
            kept.add(r["id"])
    # Re-emit in the ORIGINAL order so the caller's contract is unchanged.
    return [r for r in rows if r["id"] in kept]


def pool_composition(con: sqlite3.Connection, start_iso: str) -> Dict:
    """What the candidate window actually held, and what the cap threw away.

    NL-142 item 2 — RECEIPTS, NOT SILENCE. The 550-item cap is a SELECTION
    CAPACITY DERATE and it had been firing unremarked: 27 of 48 ranking runs
    sat at item_count=550 with nothing in the record naming what fell off the
    end, so a reader's chosen outlet could lose its entire day, every day,
    invisibly. (Measured on the real DB: the 2026-08-06 run evicted 24 items —
    all 20 Bloomberg Markets plus 4 Bloomberg Politics. Bloomberg Markets is
    simply first in the file.) This extends the routine-derating-is-a-
    checkpoint law to the pool: every run records its composition, and a run
    that evicts anything says so by name.

    WHY EVICTION IS POSITIONAL AND NOT "OLDEST". One ingest run stamps every
    row it writes with a single shared `fetched_at`, so `ORDER BY fetched_at
    DESC, id DESC` degenerates to reverse INSERTION order within a run, and
    insertion order is the reader's sources.yaml order. The cap therefore
    evicts from the TOP OF THE FILE, deterministically, not from the oldest
    news. Nothing here fixes that — fair-fill is ENG-M0's surface. This makes
    it VISIBLE, which is the precondition for anyone noticing it needs fixing.

    Read-only: one COUNT and one grouped read over the same window predicate
    gather_items uses. Returns window_total, capped_to, evicted, and the
    evicted outlets in eviction order (first to fall off first)."""
    total = con.execute(
        "SELECT COUNT(*) AS n FROM source_items WHERE fetched_at >= ?",
        (start_iso,),
    ).fetchone()["n"]
    evicted = max(0, total - MAX_INPUT_ITEMS)
    by_outlet: List[Tuple[str, int]] = []
    if evicted:
        # The rows the LIMIT would have dropped: same ordering, skipped past
        # the cap. OFFSET is exactly the cap, so this is the complement of
        # gather_items' result set over an identical predicate and sort.
        rows = con.execute(
            "SELECT outlet FROM source_items WHERE fetched_at >= ?"
            " ORDER BY fetched_at DESC, id DESC LIMIT -1 OFFSET ?",
            (start_iso, MAX_INPUT_ITEMS),
        ).fetchall()
        counts: Dict[str, int] = {}
        order: List[str] = []
        for row in rows:
            name = row["outlet"]
            if name not in counts:
                order.append(name)
            counts[name] = counts.get(name, 0) + 1
        by_outlet = [(name, counts[name]) for name in order]
    return {
        "window_total": total,
        "capped_to": min(total, MAX_INPUT_ITEMS),
        "evicted": evicted,
        "evicted_by_outlet": by_outlet,
    }


def pool_warning(composition: Dict) -> Optional[str]:
    """The run-log line for a capped pool — names the count AND the outlets.

    Born from the 27 silent runs: 'hit the cap' alone told the reader nothing
    actionable. Naming the first-evicted outlets is what turns the warning
    into something a reader can act on (reorder the file, cut a feed, or
    escalate the cap)."""
    if not composition["evicted"]:
        return None
    named = ", ".join(
        f"{outlet} ({count})" for outlet, count in composition["evicted_by_outlet"][:3]
    )
    more = len(composition["evicted_by_outlet"]) - 3
    if more > 0:
        named += f", +{more} more outlet(s)"
    return (
        f"item window hit the {MAX_INPUT_ITEMS}-item cap: "
        f"{composition['window_total']} candidates in window, "
        f"{composition['evicted']} EVICTED before ranking — "
        f"first to fall: {named}. Eviction follows your sources.yaml order "
        "(top of the file dies first), not story age"
    )


def active_memory_topics(con: sqlite3.Connection) -> List[str]:
    """Ranker reads active memory rows exactly like tags (taxonomy contract
    §A): ACTIVE only, capped at the 15 most-recently-referenced (spec §B).
    Delegates to memory.active_context — one implementation of the cap."""
    return memory.active_context(con)


# ---------------------------------------------------------------------------
# NL-70: the rank-id short key (Crockford base32 render alias + check symbol)
# ---------------------------------------------------------------------------
# The ranking pass shows the model ~550 candidate lines keyed `[id=KEY]`. Raw
# 4-digit DB ids sit at the model's token-copy edge and 5 digits worsen the
# slip rate (NL-70). Instead of the raw int we render a per-run PRESENTATION
# ALIAS: the canonical id encoded as Crockford base32 with a trailing mod-37
# check symbol (7714 -> "7H2" + check "J" -> "7H2J"). Two properties this buys:
#   * shorter than the decimal id at every scale (7714 -> "7H2J", 4 chars),
#     without densifying the id space (the SPARSE-ID LAW still holds — these are
#     the raw ids, just re-radixed, so a fabricated id still lands OUTSIDE the
#     real set and hard-rejects at the isinstance-int / unknown-id guard in
#     validate_payload);
#   * a single mis-copied symbol changes the decoded value, whose check symbol no
#     longer matches the copied one, so the key fails to decode LOUDLY instead of
#     silently landing on a real NEIGHBOURING id — the run-30 in-vocab-slip class
#     that decimal ids leave undefended (adjacency is real: 8355 -> "853Y",
#     8356 -> "854Z", so the check symbol is doing the work, not the radix).
# The DB id stays canonical; nothing is stored as base32. decode_keys() is the
# ONLY place the model's string keys are turned back into ints, and it runs
# BEFORE validate_payload so the isinstance-int guard keeps operating on ints,
# untouched. It accepts KEYS ONLY — a bare JSON-number int bypasses the check
# symbol entirely (that is QA F1's silent in-vocab channel), so it is rejected
# here. A decode/check failure raises ValueError, which rides the EXISTING
# corrected-retry except in call_llm_validated — same class as an invented-id
# reject, never a silent pass.
_RANKKEY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (no I,L,O,U)
_RANKKEY_CHECK_EXTRA = "*~$=U"                          # check-only symbols for 32..36
_RANKKEY_CHECK_ALPHABET = _RANKKEY_ALPHABET + _RANKKEY_CHECK_EXTRA  # 37 symbols


def _build_rankkey_decoders() -> Tuple[Dict[str, int], Dict[str, int]]:
    """Decode tables: case-insensitive, folding the Crockford confusables
    (I/L -> 1, O -> 0). The check table additionally accepts the 5 check-only
    symbols (values 32..36); the body table does NOT (a U/*/~/$/= in the body is
    an invalid symbol)."""
    body: Dict[str, int] = {}
    for i, ch in enumerate(_RANKKEY_ALPHABET):
        body[ch] = i
        body[ch.lower()] = i
    for bad, val in (("I", 1), ("L", 1), ("O", 0)):
        body[bad] = val
        body[bad.lower()] = val
    check = dict(body)
    for i, ch in enumerate(_RANKKEY_CHECK_EXTRA, start=32):
        check[ch] = i
        check[ch.lower()] = i
    return body, check


_RANKKEY_DECODE, _RANKKEY_CHECK_DECODE = _build_rankkey_decoders()


def encode_rank_key(n: int) -> str:
    """Canonical DB id -> its per-run `[id=KEY]` alias: Crockford base32 body +
    trailing mod-37 check symbol. Presentation only; the exact inverse is
    decode_rank_key. Reference parity (build contract §1): 7714 -> "7H2J",
    99999 -> "31MZS", 1000000 -> "YGJ01"."""
    if n < 0:
        raise ValueError(f"rank id must be non-negative, got {n!r}")
    if n == 0:
        body = "0"
    else:
        syms: List[str] = []
        m = n
        while m > 0:
            syms.append(_RANKKEY_ALPHABET[m % 32])
            m //= 32
        body = "".join(reversed(syms))
    return body + _RANKKEY_CHECK_ALPHABET[n % 37]


def decode_rank_key(key: str) -> int:
    """`[id=KEY]` alias -> canonical int id (exact inverse of encode_rank_key).
    Case-insensitive; folds the I/L->1, O->0 confusables. Raises ValueError —
    which rides the corrected-retry path — on a malformed body OR a check-symbol
    mismatch. The mismatch case is the single-symbol-slip defense: a slipped body
    decodes to a DIFFERENT int whose checksum no longer matches the copied
    symbol, so a neighbouring-id slip is caught before the vocab lookup."""
    if not isinstance(key, str):
        raise ValueError(f"item_id key must be a string, got {type(key).__name__}")
    s = key.strip()
    if len(s) < 2:
        raise ValueError(f"item_id key {key!r} too short to carry a check symbol")
    body, chk = s[:-1], s[-1]
    n = 0
    for ch in body:
        if ch not in _RANKKEY_DECODE:
            raise ValueError(f"item_id key {key!r} has invalid symbol {ch!r}")
        n = n * 32 + _RANKKEY_DECODE[ch]
    if chk not in _RANKKEY_CHECK_DECODE:
        raise ValueError(f"item_id key {key!r} has invalid check symbol {chk!r}")
    if _RANKKEY_CHECK_DECODE[chk] != n % 37:
        raise ValueError(f"item_id key {key!r} failed its check symbol (a mis-copied id)")
    return n


def decode_keys(payload: object) -> object:
    """Rewrite the model's `[id=KEY]` string codes back to canonical int ids
    BEFORE validate_payload sees the payload (twin of repair_duplicate_ids).
    This is the ONLY seam that turns keys back into ints, so validate_payload's
    isinstance-int guard and its closed-vocab (unknown-id) check stay UNTOUCHED
    and keep operating on ints.

    KEYS ONLY. Post-NL-70, honest model output is bracketed [id=KEY] strings —
    the prompt and the corrected-retry text both say so. Every item_id element
    must therefore be a decodable string key; ANYTHING ELSE — a bare JSON-number
    int, a JSON boolean, a float — is a decode FAILURE that raises ValueError and
    rides the corrected-retry path (same class as an invented-id reject), never a
    silent pass. A string key fails the same way on a bad Crockford format OR a
    check-symbol mismatch (the run-30 neighbour-slip defense).

    Why bare ints must NOT pass through (QA F1, fix loop 1): a JSON-number int
    never reaches the check symbol, so an in-window fabrication like 8500 against
    a {8355..8904} window would validate SILENTLY — the exact run-30 in-vocab
    class the check symbol exists to catch. Rejecting bare ints keeps the check
    symbol on the only path in. This also closes the JSON-boolean hole (bool is an
    int subclass that would otherwise satisfy validate_payload's isinstance-int
    check and ride the guard).

    No stored-int payload can re-enter here: validate_payload has exactly ONE call
    site (the decode chain in _call_llm_validated below), so decode_keys runs once,
    on raw model output only — canonical DB ints never round-trip through it.
    Shape errors (item_ids not a list, etc.) are left for validate_payload to name."""
    if not isinstance(payload, dict) or not isinstance(payload.get("clusters"), list):
        return payload
    bad: List[str] = []
    pending: List[Tuple[Dict, List]] = []
    for c in payload["clusters"]:
        if not isinstance(c, dict) or not isinstance(c.get("item_ids"), list):
            continue  # not this seam's class — validate_payload names the shape error
        decoded: List[int] = []
        for x in c["item_ids"]:
            try:
                decoded.append(decode_rank_key(x))  # rejects non-str (int/bool/float) too
            except ValueError as exc:
                bad.append(str(exc))
        pending.append((c, decoded))
    if bad:
        raise ValueError("unresolvable item_id key(s): " + "; ".join(bad))
    for c, decoded in pending:
        c["item_ids"] = decoded
    return payload


def render_items_block(items: List[sqlite3.Row]) -> str:
    """The candidate lines exactly as the ranker sees them, ascending id.

    EXTRACTED FROM build_prompt (NL-17 M2) so the replay harness rebuilds the
    rank input through the SAME renderer that produced it. A second copy of this
    loop in replay.py would be a rebuild that proves its own copy faithful and
    the real prompt not at all — the BUG-1 lesson applied to an instrument.
    Behaviour is byte-identical to the inline form it replaces."""
    return "\n".join(
        f"[id={encode_rank_key(r['id'])}] {r['outlet']} | "
        + r["title"].replace("[", "(").replace("]", ")")
        for r in sorted(items, key=lambda r: r["id"])
    )


def build_prompt(
    date_local: str,
    items: List[sqlite3.Row],
    cfg: config.SourcesConfig,
    memory_topics: List[str],
    window_desc: str,
    dormant: Optional[List[str]] = None,
) -> str:
    template = (paths.PROMPTS_DIR / PROMPT_FILE).read_text(encoding="utf-8")
    # Ascending id order + an explicit [id=N] key: copying exact ids out of a
    # ~550-line list is where the model slips. Live M4 findings, in order:
    # invented near-miss ids (fixed by ascending sort + temp 0), then numbers
    # LIFTED FROM HEADLINES as ids ("Top Links 1151" -> id 115, deterministic
    # at temp 0) — the bracketed key makes the id token structurally
    # unmistakable. Presentation only; selection order is irrelevant.
    # Brackets are sanitized out of titles so a headline can never fabricate
    # an "[id=N]" token (M4 gate: closes the id-in-headline class outright,
    # including a hostile feed publishing literal id markers).
    # SPARSE-ID LAW (run 28, 2026-07-14 — never densify these ids): the ids
    # below are RAW DB ids, rendered sparse ON PURPOSE. The closed-vocab
    # guard's rejection power depends on fabricated ids landing OUTSIDE the
    # real id set; a "compress ids to 1..N" remap here would have put run
    # 28's fabricated lattice (383-613) INSIDE the vocabulary and silently
    # mis-attributed every cluster. Detection property pinned by
    # test_run28_fabrication_lands_outside_the_real_vocabulary.
    # NL-70: the key is the Crockford base32 render alias of the raw id (see
    # encode_rank_key above), not the decimal id — shorter and check-guarded,
    # while the raw id below stays the canonical thing decode_keys returns.
    items_block = render_items_block(items)
    tag_lines = [f"- {name} (domain)" for name in cfg.interests_broad]
    tag_lines += [f"- {name} (topic)" for name in cfg.interests_granular]
    memory_block = (
        "\n".join(f"- {t}" for t in memory_topics) if memory_topics else "(none right now)"
    )
    dormant_block = (
        "\n".join(f"- {t}" for t in dormant) if dormant else "(none right now)"
    )
    return template.format(
        date_local=date_local,
        window_desc=window_desc,
        items_block=items_block,
        tags_block="\n".join(tag_lines),
        memory_block=memory_block,
        dormant_block=dormant_block,
        max_clusters=MAX_CLUSTERS,
    )


# ---------------------------------------------------------------------------
# LLM call + hard validation
# ---------------------------------------------------------------------------

def estimate_cost_usd(prompt: str, max_completion: int = MAX_COMPLETION_TOKENS) -> float:
    in_tokens = len(prompt) / 3.5  # conservative chars-per-token
    return (in_tokens / 1e6) * USD_PER_MTOK_IN + (max_completion / 1e6) * USD_PER_MTOK_OUT


def usage_to_usd(usage: Dict) -> float:
    return (usage.get("prompt_tokens", 0) / 1e6) * USD_PER_MTOK_IN + (
        usage.get("completion_tokens", 0) / 1e6
    ) * USD_PER_MTOK_OUT


# B3-D5 (the flap window): the rank seat is resolved through effective_seat
# ONCE per rank operation and threaded to every reader — the cost_sink ledger,
# _post_chat's TRANSPORT, the run-log disclosure, and the persisted token_cost —
# via this request-scoped global (generate._ACTIVE_SEAT_CFG's pattern). Post-D2
# effective_seat is filesystem-dependent (it resolves the `claude` binary), so
# re-resolving per reader let a binary that vanished mid-run (a CLI reinstall/
# upgrade) FORK the transport (fell to the metered api wire) from the ledger
# (still says subscription/usd_charged=0.0) — the D1 lie via a new door. One
# resolution closes the window: every reader rides the same (cfg, reason), so a
# flap either dies loud or is labeled truthfully, never silently. Set by the
# OUTERMOST of _run_rank_body / call_llm_validated; nested calls reuse it.
_ACTIVE_RANK: Optional[Tuple["llm.SeatConfig", Optional[str]]] = None


def _effective_rank() -> Tuple["llm.SeatConfig", Optional[str]]:
    """The active (cfg, fallback_reason) for the current rank operation. Returns
    the request-scoped resolution if one is set, else resolves fresh (a direct
    _post_chat call outside call_llm_validated — the signature-test path)."""
    if _ACTIVE_RANK is not None:
        return _ACTIVE_RANK
    return llm.effective_seat("rank")


def _post_chat(key: str, prompt: str) -> Dict:
    # Transport delegates to the provider seam (llm.py). B2 moved this seat onto
    # the Claude lane; read the current model/lane off llm.SEATS["rank"] (this
    # comment named claude-haiku-4-5 until NL-147). timeout 90s,
    # temperature 0 (exact-copy discipline for ids/tag names — M4 live finding),
    # json_mode on. The anthropic provider synthesises the OpenAI-shaped `.raw`
    # (choices/usage), so call_llm_validated's parse/retry law is UNTOUCHED, and
    # it satisfies json_mode by nudging bare JSON while the caller's json.loads +
    # validate_payload + corrected retry remain the backstop (the same discipline
    # gpt-4o rode). The anthropic lane reads its own endpoint + credential, so the
    # url/key passed here are the (harmless) openai offline-test seam values. This
    # function keeps its signature: it is the suite's monkeypatch target.
    # B3-D5: transport rides the SAME resolution the gate/ledger used (via
    # _ACTIVE_RANK) — never a fresh effective_seat that a mid-run binary flap
    # could resolve to a different lane than the ledger records.
    _cfg, _ = _effective_rank()
    return llm.chat(
        llm.LaneRequest(
            cfg=_cfg,
            prompt=prompt,
            temperature=0,
            max_tokens=MAX_COMPLETION_TOKENS,
            json_mode=True,
            user_agent=USER_AGENT,
            api_key=key,
            url=OPENAI_CHAT_URL,  # openai offline-test seam; ignored by anthropic
        )
    ).raw


def _cap_cluster_items(
    ids: List[int],
    outlets: Optional[Dict[int, str]] = None,
    cap: int = MAX_CLUSTER_ITEMS,
) -> Tuple[List[int], List[int]]:
    """Trim an over-cap cluster to `cap` items. Returns (kept, dropped), each
    in the MODEL'S OWN ORDER — the trim decides membership, never sequence.

    THE RULE — round-robin across outlets, model order inside each outlet.
    Group the ids by outlet in first-appearance order, then take each group's
    1st item, then each group's 2nd, and so on until the cap is full. Two
    properties this buys, and they are the reason the rule is not "keep the
    first 48":
      * OUTLET DIVERSITY SURVIVES. Every outlet in the cluster keeps at least
        one item whenever the cluster's distinct-outlet count is <= cap (real
        max observed 18–19 across sweeps — a join-time quantity that moves
        with id churn; far below the cap either way). A cluster of 59 Reuters
        items plus one lone AP item
        keeps the AP item — the naive head-slice drops it if it sorted last.
      * THE CORROBORATION EVIDENCE SURVIVES. `corroborate` counts DISTINCT
        non-wire outlets, so preserving the outlet set preserves the trust
        label ("Reported by N named outlets") the reader sees. What the trim
        costs is per-outlet DEPTH — and in the text-rich regime that produces
        the largest maps, depth past ~45 sources is already
        unreadable (`render_material`'s water-fill cannot feed more than that
        inside MATERIAL_BUDGET_CHARS, so those items reach the analyst as
        citable keys with no text behind them). (With short real excerpts all
        items would be fed — a supporting argument, not the load-bearing one;
        NL-133 build §1.)
        ONE NAMED EDGE, not covered: this function sees outlets, not the
        `wire_syndication_flag`. If an outlet's surviving representative is
        wire-syndicated and the non-wire item behind it was dropped, that
        outlet leaves `corroborate`'s NAMED count while staying in the map —
        the count moves DOWN, the conservative direction for a trust label
        (ADR-0004's own doctrine), and only inside a cluster already past 48
        items. Fixing it means plumbing the wire flag this far up; deliberately
        not done (NL-133 report, open item).
    Without an outlet map (a direct `validate_payload` call — every unit test,
    and any future caller that has ids but no rows) each item becomes its own
    group, so the round-robin degrades EXACTLY to model order: deterministic
    either way, and no false grouping is ever invented from missing data.
    """
    if len(ids) <= cap:
        return list(ids), []
    groups: List[List[int]] = []      # positions, grouped by outlet
    index: Dict[str, List[int]] = {}
    for pos, item_id in enumerate(ids):
        outlet = (outlets or {}).get(item_id) or ""
        key = outlet or f"\x00{pos}"  # unknown outlet -> its own group
        if key not in index:
            index[key] = []
            groups.append(index[key])
        index[key].append(pos)
    keep: set = set()
    depth = 0
    while len(keep) < cap:
        advanced = False
        for group in groups:
            if depth < len(group):
                keep.add(group[depth])
                advanced = True
                if len(keep) >= cap:
                    break
        if not advanced:
            break
        depth += 1
    return ([i for pos, i in enumerate(ids) if pos in keep],
            [i for pos, i in enumerate(ids) if pos not in keep])


def validate_payload(
    payload: object,
    known_ids: set,
    tag_levels: Dict[str, str],
    memory_topics: List[str],
    dormant_topics: Optional[List[str]] = None,
    notes: Optional[List[str]] = None,
    item_outlets: Optional[Dict[int, str]] = None,
    truncations: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Hard schema validation of the LLM's cluster payload. Raises ValueError
    with ALL problems found (not just the first) so a retry/report is
    actionable. Extra unknown keys are tolerated; everything we consume is
    checked. matched_dormant (lifecycle v2) validates against the provided
    dormant list — match-only; scoring never sees it.

    NL-133: an over-cap cluster is TRIMMED to MAX_CLUSTER_ITEMS rather than
    rejected (`_cap_cluster_items` picks which items survive; pass
    `item_outlets` — id -> outlet — to make the trim diversity-preserving, and
    a list as `truncations` to receive one disclosure record per trimmed
    cluster). Every REJECTION above still reads the model's FULL id list: an
    invented id or a cross-cluster re-use hard-rejects exactly as before, even
    when the offending id sits in the part the trim would have dropped. The
    trim is our hygiene, never a way for a bad payload to slip past."""
    problems: List[str] = []
    if not isinstance(payload, dict) or not isinstance(payload.get("clusters"), list):
        raise ValueError("payload must be a JSON object with a `clusters` list")
    clusters = payload["clusters"]
    if len(clusters) > MAX_CLUSTERS * 2:
        raise ValueError(f"{len(clusters)} clusters — far over the {MAX_CLUSTERS} cap; refusing")

    seen_ids: set = set()
    valid: List[Dict] = []
    memory_set = set(memory_topics)
    dormant_set = set(dormant_topics or [])
    for i, c in enumerate(clusters, start=1):
        where = f"cluster #{i}"
        if not isinstance(c, dict):
            problems.append(f"{where}: not an object")
            continue
        title = c.get("story_title")
        if not isinstance(title, str) or not title.strip():
            problems.append(f"{where}: story_title missing/empty")
        summary = c.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append(f"{where}: summary missing/empty")
        ids = c.get("item_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(x, int) for x in ids):
            problems.append(f"{where}: item_ids must be a non-empty list of integers")
            ids = []
        unknown = [x for x in ids if x not in known_ids]
        if unknown:
            problems.append(f"{where}: invented item_ids {unknown}")
        dupes = [x for x in ids if x in seen_ids]
        if dupes:
            problems.append(f"{where}: item_ids {dupes} already used by another cluster")
        seen_ids.update(ids)

        # NL-133 per-cluster cap. AFTER every rejection above (which all read
        # the model's full list) and BEFORE `valid` — so what leaves this
        # function is bounded, and nothing the model got wrong is hidden by
        # the trim. `seen_ids` also keeps the FULL list: an item we dropped
        # here is still spoken for, and a later cluster re-using it is still
        # the same integrity violation it was before the cap existed.
        ids, dropped_ids = _cap_cluster_items(ids, item_outlets)
        if dropped_ids and truncations is not None:
            kept_outlets = {(item_outlets or {}).get(x) for x in ids}
            truncations.append({
                "cluster": (title if isinstance(title, str) else "")[:80]
                           or f"cluster #{i}",
                "kept": len(ids),
                "dropped": len(dropped_ids),
                # the diversity claim, measured on this actual trim rather
                # than asserted: outlets present before vs after
                "outlets_before": len({(item_outlets or {}).get(x)
                                       for x in ids + dropped_ids}),
                "outlets_after": len(kept_outlets),
            })

        mtags = c.get("matched_tags", [])
        if not isinstance(mtags, list):
            problems.append(f"{where}: matched_tags must be a list")
            mtags = []
        clean_tags: List[Dict[str, str]] = []
        for t in mtags:
            if isinstance(t, str) and t in tag_levels:
                # Schema TOLERANCE, not repair (ADR-0004 M5 amendment): the
                # dict's level field carries zero model information — any
                # level differing from OUR vocabulary map is rejected anyway —
                # so a bare exact-match name is informationally identical to
                # the dict form. Normalized deterministically from the map,
                # counted, and disclosed. Non-matching strings still reject.
                clean_tags.append({"name": t, "level": tag_levels[t]})
                if notes is not None:
                    notes.append(t)
            elif (
                not isinstance(t, dict)
                or t.get("name") not in tag_levels
                or t.get("level") != tag_levels.get(t.get("name"))
            ):
                problems.append(f"{where}: matched_tags entry {t!r} is not an exact listed tag")
            else:
                clean_tags.append({"name": t["name"], "level": t["level"]})

        mmem = c.get("matched_memory", [])
        if not isinstance(mmem, list) or not all(isinstance(m, str) for m in mmem):
            problems.append(f"{where}: matched_memory must be a list of strings")
            mmem = []
        bad_mem = [m for m in mmem if m not in memory_set]
        if bad_mem:
            problems.append(f"{where}: matched_memory {bad_mem} not in the provided threads")

        mdorm = c.get("matched_dormant", [])
        if not isinstance(mdorm, list) or not all(isinstance(m, str) for m in mdorm):
            problems.append(f"{where}: matched_dormant must be a list of strings")
            mdorm = []
        bad_dorm = [m for m in mdorm if m not in dormant_set]
        if bad_dorm:
            problems.append(
                f"{where}: matched_dormant {bad_dorm} not in the provided dormant threads"
            )

        impact = c.get("world_impact")
        if not isinstance(impact, (int, float)) or isinstance(impact, bool) or not 0 <= impact <= 10:
            problems.append(f"{where}: world_impact must be a number 0-10")
            impact = 0
        # NL-138 (ruling ④ 2026-08-02): `world_impact_reason` is no longer
        # asked for, no longer required, and no longer carried. A model that
        # still emits it is TOLERATED-AND-IGNORED, not rejected — which is this
        # function's existing idiom rather than a new leniency: every field
        # below is WHITELIST-CONSTRUCTED into a fresh dict from keys we name,
        # so an unknown key has never been able to reach a consumer. There is
        # no extra-keys rejection pass anywhere in this validator (`matched_
        # dormant` arrived the same way and old prompts' payloads kept
        # validating). Rejecting here would also make one stale prompt file
        # fail every run of a paid seat for a field nothing reads.
        valid.append(
            {
                "story_title": (title or "").strip()[:300],
                "summary": (summary or "").strip()[:400],
                "item_ids": ids,
                "matched_tags": clean_tags,
                # NL-133, the SECOND route into the same quadratic: every
                # matched_memory entry becomes a P key in the analyst's source
                # map (`memory_core.prior_for_slot` yields one prior entry per
                # entry, and all P keys share the outlet identity "newslens
                # (prior edition)" — so they name each other, quadratically).
                # Nothing bounded the LIST, only its vocabulary: 80 repeats of
                # one valid topic rendered a 46,391-char map with zero cluster
                # items (measured). De-duplicated in first-mention order, which
                # bounds P by the vocabulary itself (memory.CONTEXT_CAP = 15)
                # and costs nothing: a repeated topic carries no information
                # any consumer reads (scoring tests truthiness,
                # `update_references` already iterates `set(topics)`, the
                # quiet-thread and fragmentation passes build sets).
                "matched_memory": list(dict.fromkeys(
                    m for m in mmem if m in memory_set)),
                "matched_dormant": [m for m in mdorm if m in dormant_set],
                "world_impact": int(round(float(impact))),
            }
        )
    if problems:
        raise ValueError("; ".join(problems))
    return valid


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """OpenAI puts the actionable part (error.code + message) in the body —
    surface it instead of guessing from the status code (found live in the M3
    spot-check: a bare '429' reads as a key problem when it's really quota)."""
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        err = payload.get("error") or {}
        code = err.get("code") or err.get("type") or ""
        msg = (err.get("message") or "").strip()[:200]
        return ": ".join(x for x in (code, msg) if x)
    except Exception:  # body unreadable/not JSON — the status alone will do
        return ""


def _retry_after_seconds(exc: urllib.error.HTTPError, default: float = 10.0) -> float:
    """Clamped to finite [0, 20] — a hostile/garbage Retry-After (negative,
    nan, inf) must never reach time.sleep(), where it would raise outside the
    RankingError taxonomy and bypass BUG-6 logging (M3 review carryover)."""
    try:
        value = float(exc.headers.get("Retry-After", default))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < 0:
        return default
    return min(value, 20.0)


def repair_duplicate_ids(payload: object) -> Tuple[object, Dict]:
    """THE one disclosed deterministic repair (M3 fix loop 1, live finding):
    on real ~600-item days the model puts a story that straddles topics into
    two clusters, re-using its item_ids. Contract: keep the item's FIRST
    cluster assignment, drop later duplicates, count every drop, disclose in
    the run report AND ranking_runs.meta — never silent. A cluster emptied by
    the repair is dropped whole (and disclosed). ONLY this violation class is
    repaired: invented ids, re-leveled tags, ranges, empty fields etc. still
    hard-reject in validate_payload — which also RETAINS its own duplicate
    check as a backstop behind this repair (defense in depth).

    Shapes this function can't interpret pass through untouched for
    validate_payload to reject with its usual diagnosis.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("clusters"), list):
        return payload, {"repaired": 0}
    seen: set = set()
    dropped: List[Dict] = []
    emptied: List[str] = []
    new_clusters: List = []
    for idx, c in enumerate(payload["clusters"], start=1):
        if not isinstance(c, dict) or not isinstance(c.get("item_ids"), list) or not c["item_ids"]:
            new_clusters.append(c)  # not this repair's class — validator's problem
            continue
        label = (str(c.get("story_title") or f"cluster #{idx}"))[:80]
        kept: List = []
        for x in c["item_ids"]:
            if isinstance(x, int) and x in seen:
                dropped.append({"item_id": x, "dropped_from": label})
                continue
            if isinstance(x, int):
                seen.add(x)
            kept.append(x)  # non-ints kept for the validator to reject
        if kept:
            fixed = dict(c)
            fixed["item_ids"] = kept
            new_clusters.append(fixed)
        else:
            emptied.append(label)
    if not dropped:
        return payload, {"repaired": 0}
    info = {
        "repaired": len(dropped),
        "dropped": dropped[:20],
        "clusters_emptied": emptied,
    }
    return {**payload, "clusters": new_clusters}, info


# The ONE retry, CORRECTED (run 28, 2026-07-14 live finding). A blind retry
# re-POSTs byte-identical bytes, so at temperature 0 the model returned the
# byte-identical output: run 28's call+retry both emitted the SAME fabricated
# id-lattice (ids 383-613, arithmetic step ~20, none of them in the real
# 3679-4228 window) — ~$0.025 spent twice for a guaranteed-identical failure,
# the retry powerless by construction. The retry for the MALFORMED-OUTPUT class
# therefore carries a concrete correction turn: the retry INPUT differs, so
# attempt 2 is a genuine second draw steered at the exact rule that failed.
#
# THE TEMP-0 REWORK (ENG-M0, 2026-08-06) — WHAT THIS PARAGRAPH USED TO CLAIM AND
# WHY IT NO LONGER HOLDS. It used to read "temperature stays 0 (the M4 exact-copy
# finding holds)". That sentence is now FALSE and had to go rather than quietly
# rot: the rank seat is Sonnet 5, and the Claude 4.6+ family REJECTS temperature
# with a 400, so `sampling=False` on the seat makes the anthropic api provider
# OMIT the parameter entirely. `temperature=0` is still passed by _post_chat
# below — it is part of the LaneRequest contract and a sampling=True rollback
# target would honor it — but for the shipped seat it reaches no wire.
#
# WHAT ACTUALLY BUYS THE TRANSCRIPTION DISCIPLINE NOW, in the order it bites:
#   1. the PROMPT's rule text — the bracketed [id=KEY] render, the sparse-id law,
#      and the "copy verbatim, never invent" instruction in rank_select.txt;
#   2. the CHECK SYMBOL on every key (NL-70) — a mis-copied character fails
#      decode_keys and hard-rejects rather than mis-attributing;
#   3. this corrected retry — still the second line, and now strictly MORE
#      useful than it was: with sampling omitted the retry is no longer
#      identical-by-construction even before the correction text is added, so
#      the run-28 "powerless by construction" property cannot recur on this seat.
#   4. the seat itself — Sonnet 5 replaced Haiku 4.5 precisely because Haiku
#      slipped this class twice in the record (run 48; fresh1 run 3).
# Nothing here weakened: the guard that CATCHES a slip is unchanged, and the
# thing that was doing the work (the closed vocabulary + check symbol) was never
# the sampling parameter.
#
# Scoped to malformed output only — a 5xx/timeout/429 retry re-sends the original
# prompt unchanged (those failures are transport, not the model's doing). The id
# vocabulary is NOT compressed: the closed-vocab guard's power is that fabricated
# ids land OUTSIDE the real (sparse, 4-digit) id set and hard-reject; a dense
# 1..N remap would put the same fabrication INSIDE the vocabulary and silently
# mis-attribute it.
RETRY_CORRECTION = (
    "CORRECTION — your previous response was rejected as invalid, most likely "
    "for one of these hard rules:\n"
    "1. Every item_id MUST be the short alphanumeric [id=KEY] code copied "
    "verbatim from an INPUT ITEMS line above — every character, letters and "
    "digits both, as a JSON string. Do NOT invent, guess, renumber, shorten, or "
    "generate keys, and never emit an evenly-spaced or made-up sequence; if you "
    "are unsure of an item's key, leave that item out. Numbers inside titles are "
    "never ids.\n"
    "2. matched_tags / matched_memory / matched_dormant entries must be copied "
    "EXACTLY from the provided lists (tags with their listed level), or left "
    "empty.\n"
    "Re-cluster the SAME INPUT ITEMS and return only the one JSON object of the "
    "required shape."
)


def call_llm_validated(
    key: str,
    prompt: str,
    known_ids: set,
    tag_levels: Dict[str, str],
    memory_topics: List[str],
    repairs: Optional[Dict] = None,
    dormant_topics: Optional[List[str]] = None,
    cost_sink: Optional[List[Dict]] = None,
    item_outlets: Optional[Dict[int, str]] = None,
) -> Tuple[List[Dict], Dict]:
    """B3-D5: the request-scoped resolution boundary. Resolve the rank seat
    through effective_seat EXACTLY ONCE for this call (if an outer scope —
    _run_rank_body — has not already) and publish it on _ACTIVE_RANK so the
    transport (_post_chat) and the cost_sink ledger ride the SAME (cfg, reason).
    A `claude` binary that flaps mid-call can no longer fork the metered api
    transport from a subscription/usd_charged=0.0 ledger row. The body is
    _call_llm_validated; this thin wrapper owns the scope + its teardown."""
    global _ACTIVE_RANK
    _own_scope = _ACTIVE_RANK is None
    if _own_scope:
        _ACTIVE_RANK = llm.effective_seat("rank")
    try:
        return _call_llm_validated(
            key, prompt, known_ids, tag_levels, memory_topics,
            repairs=repairs, dormant_topics=dormant_topics, cost_sink=cost_sink,
            item_outlets=item_outlets)
    finally:
        if _own_scope:
            _ACTIVE_RANK = None


def _call_llm_validated(
    key: str,
    prompt: str,
    known_ids: set,
    tag_levels: Dict[str, str],
    memory_topics: List[str],
    repairs: Optional[Dict] = None,
    dormant_topics: Optional[List[str]] = None,
    cost_sink: Optional[List[Dict]] = None,
    item_outlets: Optional[Dict[int, str]] = None,
) -> Tuple[List[Dict], Dict]:
    """One call + ONE retry total, then a visible RankingError.

    Between parse and validation, repair_duplicate_ids fixes (and counts) the
    one repairable violation class; pass a dict as `repairs` to receive the
    returning attempt's repair info (an out-param so the (clusters, usage)
    return shape stays stable).

    Pass a list as `cost_sink` to receive one entry per BILLED attempt —
    including attempts that fail validation or truncate after billing. The
    (clusters, usage) return shape shows only the returning attempt; without
    the ledger a corrected-retry recovery is invisible after the money is
    spent (rank-side twin of generate.py's cost_sink). On total failure the
    raised RankingError carries the ledger as `.llm_attempts`.

    Retryable: 5xx, timeouts/connection failures, transient 429 rate limits,
    malformed/failed-validation output (the spec'd path). NOT retryable:
    auth (401/403), insufficient_quota 429 (retrying spends nothing and fixes
    nothing — it needs the principal's billing action), other 4xx.

    The malformed-output retry is CORRECTED, not blind: after a validation
    failure the retry prompt carries RETRY_CORRECTION so attempt 2 is not a
    byte-identical re-POST (run 28 fix — see the constant). Transport retries
    (5xx/429/network) re-send the original prompt unchanged."""
    # B1 fail-loud gate (D1 close) + B3-D5: read the ONE request-scoped rank
    # resolution the wrapper published on _ACTIVE_RANK — the SAME (cfg, reason)
    # _post_chat's transport rides, so the gate/ledger seat and the wire seat can
    # never diverge (a NEWSLENS_LANE misconfig surfaces at the wrapper's
    # effective_seat before any transport; a binary flap can't re-resolve a
    # different lane here than the transport uses). B3-D2: _fb_reason labels the
    # ledger when the principal-armed single-fall fired.
    rank_cfg, _fb_reason = _effective_rank()
    last_error = "unknown"
    backoff = 1.0
    usage: Dict = {}
    next_prompt = prompt  # augmented below only after a malformed-output failure
    for attempt in (1, 2):
        try:
            response = _post_chat(key, next_prompt)
            usage = response.get("usage") or {}
            if cost_sink is not None:
                # Ledger BEFORE the truncation check: a truncated draw is a
                # billed draw (generate.py cost_sink precedent — the property
                # that made BUG-32's abort-path fold necessary there).
                # B3-D1 (money record): legacy `usd` == usd_charged (ADR-0014
                # §5), lane-aware — 0.0 on the subscription lane. usage_to_usd
                # would FORK it (it always shadow-prices), so the durable record
                # would show charged spend for a $0 subscription run. cost_fields
                # is the single source: usd, usd_shadow, usd_charged, lane, model.
                fields = llm.cost_fields(rank_cfg, usage, fallback_reason=_fb_reason)
                entry = {
                    "step": "rank_select",
                    "attempt": attempt,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "usd": fields["usd_charged"],
                }
                entry.update(fields)
                cost_sink.append(entry)
            choice = response["choices"][0]
            if choice.get("finish_reason") == "length":
                # Name truncation precisely — "malformed JSON" hides the real
                # cause (live M4 finding: completions hit the token cap).
                raise ValueError(
                    "completion truncated at the max_tokens cap "
                    f"({MAX_COMPLETION_TOKENS}) — response unusable"
                )
            content = choice["message"]["content"]
            payload = json.loads(content)
            # NL-70: decode the model's [id=KEY] string codes back to canonical
            # ints BEFORE anything downstream (repair, then validate) — a
            # decode/check failure raises ValueError and rides the corrected-retry
            # path below, same class as an invented-id reject.
            payload = decode_keys(payload)
            payload, repair_info = repair_duplicate_ids(payload)
            shape_notes: List[str] = []
            trimmed: List[Dict] = []
            clusters = validate_payload(
                payload, known_ids, tag_levels, memory_topics, dormant_topics,
                notes=shape_notes, item_outlets=item_outlets,
                truncations=trimmed,
            )
            if repairs is not None:
                repairs.clear()
                repairs.update(repair_info)
                if shape_notes:
                    repairs["tag_shape_normalized"] = len(shape_notes)
                if trimmed:
                    # Same channel as every other disclosed deterministic
                    # repair: the run warning + ranking_runs.meta.repairs.
                    repairs["clusters_truncated"] = trimmed
            return clusters, usage
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            if exc.code in (401, 403):
                # B2: provider-conditional off the in-scope rank_cfg so an
                # anthropic seat's key failure names the RIGHT console; the
                # openai arm is unchanged (the rollback path).
                if rank_cfg.provider == "anthropic":
                    raise RankingError(
                        f"Anthropic rejected the key (HTTP {exc.code}"
                        + (f"; {detail}" if detail else "")
                        + ") — regenerate at console.anthropic.com/settings/keys "
                        "and update .env"
                    ) from exc
                raise RankingError(
                    f"OpenAI rejected the key (HTTP {exc.code}"
                    + (f"; {detail}" if detail else "")
                    + ") — regenerate at platform.openai.com/api-keys and update .env"
                ) from exc
            if (exc.code == 400 and rank_cfg.provider == "anthropic"
                    and "credit balance is too low" in detail):
                # Anthropic signals an exhausted balance as a 400 (key valid but
                # can't spend) — named precisely BEFORE the generic 4xx arm.
                raise RankingError(
                    f"Anthropic account has no available credit ({detail}) — the "
                    "key is valid but can't spend; add credits at "
                    "console.anthropic.com billing (the doctor's read-only key "
                    "check cannot catch this)"
                ) from exc
            if exc.code == 429:
                if "insufficient_quota" in detail:
                    raise RankingError(
                        f"OpenAI account has no available quota ({detail}) — the "
                        "key is valid but can't spend; add credits / check "
                        "billing at platform.openai.com (the doctor's read-only "
                        "key check cannot catch this)"
                    ) from exc
                last_error = f"rate limited (HTTP 429{'; ' + detail if detail else ''})"
                backoff = _retry_after_seconds(exc)
            elif exc.code >= 500:
                last_error = f"HTTP {exc.code}" + (f" ({detail})" if detail else "")
            else:
                provider_name = ("Anthropic" if rank_cfg.provider == "anthropic"
                                 else "OpenAI")
                raise RankingError(
                    f"{provider_name} rejected the ranking call (HTTP {exc.code}"
                    + (f"; {detail}" if detail else "") + ")"
                ) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # malformed JSON / failed validation — the spec'd retry-then-fail path.
            # Correct the retry so it is not a byte-identical re-POST (run 28):
            # a plain re-send at temp 0 reproduces the exact same fabrication.
            last_error = f"malformed LLM output ({exc})"
            next_prompt = prompt + "\n\n" + RETRY_CORRECTION
        except Exception as exc:  # timeout / connection — network-shaped
            last_error = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
        if attempt == 1:
            time.sleep(backoff)
    err = RankingError(
        f"ranking call failed after one retry: {last_error} — no briefing row "
        "was written; re-run `newslens rank` (this failure is logged)"
    )
    # The ledger must survive the raise: a double failure still billed for
    # every attempt that returned usage (run 28 spent real money and logged
    # token_usage NULL — this is that hole's rank-side close).
    err.llm_attempts = cost_sink or []
    raise err


# ---------------------------------------------------------------------------
# Deterministic selection, override, corroboration
# ---------------------------------------------------------------------------

def personal_score(cluster: Dict, followed: bool, memory_steers: bool = False,
                   state: Optional["steering.SteeringState"] = None) -> float:
    """A6 (2026-07-05): thread matches contribute to selection ONLY when
    settings.threads_steer_selection is true. With steering off (the default
    of record), matched_memory is recognition-only here — exactly the M4
    zero-influence pattern — while persist() keeps recording references,
    revivals, and continuity regardless.

    NL-17 M2 — THE ENTITY ARM, and why it is three lines rather than a rewrite.
    `state` defaults to None -> steering.INERT, whose two effect sets are empty.
    An empty `suppressed_tags` makes the filter below true for every tag and an
    unarmed state makes the entity append unreachable, so this function computes
    byte-identical values to pre-M2 HEAD whenever steering is dark. That is
    structure, not a claim — pinned by
    tests/test_nl17_m2_steering.py::test_dark_scoring_is_byte_identical_to_head.

    THE TWO NEW BRANCHES ARE ONE LAW (engineering R2.1, the weight-atomic
    correction). A tag is dropped ONLY because a live `vocabulary_moves` row
    moved that concept into the entity vocabulary, and the SAME row is what put
    its entity into `weighted_entities` — steering.derive builds both sets in
    one pass and skips a move whole if either half would be false. No
    "moved-but-tag-scored" or "tag-off-but-unsteered" instant is reachable here.

    REPLACEMENT, NOT ADDITION. ENTITY_WEIGHT joins the EXISTING max() pool and
    is appended AT MOST ONCE however many entities the cluster matched
    (count-once, contribution-level, cap-independent — Rook's form, adopted into
    product criterion (d)). max() never sums, so entity weight and tag weight
    cannot compound for one concept, and no score becomes reachable that a plain
    topic tag could not already produce (the ceiling theorem)."""
    st = state or steering.INERT
    weights = [
        TOPIC_WEIGHT if t["level"] == "topic" else DOMAIN_WEIGHT
        for t in cluster["matched_tags"]
        if (t.get("name") or "").casefold() not in st.suppressed_tags
    ]
    if cluster["matched_memory"] and memory_steers:
        weights.append(MEMORY_WEIGHT)
    if st.armed and any(e in st.weighted_entities
                        for e in cluster.get("matched_entities") or []):
        # `matched_entities` is OBSERVATION — every watched entity whose alias
        # this cluster's items carried, storyline-altitude follows included,
        # because the receipts must record what was seen. WEIGHT is the
        # intersection with `weighted_entities`, which only entity-altitude
        # follows enter. That is criterion (c) made structural: a storyline
        # thread is visible in the receipts and worth exactly zero here.
        weights.append(steering.ENTITY_WEIGHT)
    base = max(weights) if weights else 0.0
    if followed:
        base += FOLLOWED_BOOST
    return min(base, 1.0)


def combined_score(personal: float, world_impact: int) -> float:
    return round(PERSONAL_SHARE * personal + (1 - PERSONAL_SHARE) * (world_impact / 10.0), 4)


def corroborate(items: List[sqlite3.Row]) -> Tuple[int, str, int, List[str]]:
    """Distinct-outlet counting with wire exclusion (07-02 ruling).
    Counts distinct stored outlets of non-wire RSS items. Sonar-discovered
    items are citable but are NOT 'named outlets' (not in the principal's
    list). LWW attribution ruling (ADR-0004): a URL syndicated across our own
    feeds holds ONE outlet attribution per day, so it counts once —
    undercounting, the conservative direction for a trust label."""
    named = sorted({
        r["outlet"] for r in items
        if r["source_type"] == "rss" and not r["wire_syndication_flag"]
    })
    wire_excluded = len([r for r in items if r["wire_syndication_flag"]])
    count = len(named)
    if count == 0:
        label = "Sourced via wire syndication or discovery only — treat as a single source"
    elif count == 1:
        label = "Reported by 1 named outlet"
    else:
        label = f"Reported by {count} named outlets"
    if wire_excluded:
        label += f" (plus {wire_excluded} wire-syndicated item(s), excluded from the count)"
    return count, label, wire_excluded, named


_DEDUP_STOPWORDS = frozenset(
    "a an the of in into on at to for and or as with over after amid its his "
    "her their this that is are was were be has have had by from up down out "
    "new says said".split()
)
DEDUP_JACCARD = 0.45  # M6, gate-reconciled: reproducible dup pair (QA fixture) J=0.667; distinct pairs <0.35 (ADR-0009 §2)


def _sig_tokens(cluster: Dict) -> frozenset:
    text = f"{cluster.get('story_title', '')} {cluster.get('summary', '')}".lower()
    return frozenset(
        (w[:-1] if len(w) > 3 and w.endswith("s") else w)  # meet/meets, summit/summits
        for w in re.findall(r"[a-z0-9']+", text)
        if w not in _DEDUP_STOPWORDS and len(w) > 2
    )


def _near_duplicate(a: Dict, b: Dict) -> bool:
    """Deterministic same-story detection across SELECTED slots (M6 live
    finding: the model produced two clusters of one NATO story and both
    slotted). Significant-token Jaccard over title+summary >= DEDUP_JACCARD."""
    ta, tb = _sig_tokens(a), _sig_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= DEDUP_JACCARD


_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z]{3,}\b")


def _proper_nouns(cluster: Dict) -> frozenset:
    """Distinctive proper-noun set of a cluster (title+summary) for Rook's
    fragmentation tripwire — capitalized alphabetic tokens len>=4, minus the
    dedupe stopwords. Deterministic, conservative (under-counts)."""
    text = f"{cluster.get('story_title', '')} {cluster.get('summary', '')}"
    return frozenset(
        w.lower() for w in _PROPER_NOUN_RE.findall(text)
        if w.lower() not in _DEDUP_STOPWORDS)


def _thread_has_ledger(con: sqlite3.Connection, topic: str) -> bool:
    from . import memory_core
    tid = memory_core.resolve_thread_id(con, topic)
    return tid is not None and bool(memory_core.ledger_for_thread(con, tid))


def _still_tracking_note(con: sqlite3.Connection, tracked: List[str],
                         prior_date: Optional[str]) -> str:
    """The dated context for a quiet thread's still-tracking snippet — 'no
    movement since <last ledger date>' (content §5.2's A8 teeth). The render
    composes the full register (state + next fixed point) at read time."""
    from . import memory_core
    for topic in tracked:
        tid = memory_core.resolve_thread_id(con, topic)
        if tid is None:
            continue
        entries = memory_core.ledger_for_thread(con, tid)
        if entries:
            return f"no movement since {memory_core.human_date(entries[-1]['edition_date'])}"
    return "still tracking"


def _classify_quiet_threads(scored: List, con: sqlite3.Connection,
                            prior_edition: Optional[Dict]) -> Dict[int, Tuple[str, str]]:
    """NL-57 (item 3): a candidate that re-covers a TRACKED thread (one with a
    ledger) without a new development is quiet. Content-novelty proxy: the
    candidate's max token-Jaccard against the PRIOR edition's stories decides
    the magnitude — >= QUIET_ZERO is the same story with nothing new (Following
    only), >= QUIET_SMALL is a notch (a still-tracking In-Brief snippet), below
    is a real development (normal). Returns id(cluster) -> (magnitude, note).
    Empty when there is no prior edition (day-one / cold start)."""
    out: Dict[int, Tuple[str, str]] = {}
    prior_stories = (prior_edition or {}).get("stories") or []
    prior_date = (prior_edition or {}).get("date")
    prior_sigs = [ps for ps in (_sig_tokens(st) for st in prior_stories) if ps]
    if not prior_sigs:
        return out
    for (c, _items, _f, _p, _comb) in scored:
        tracked = [t for t in (c.get("matched_memory") or [])
                   if _thread_has_ledger(con, t)]
        if not tracked:
            continue
        cand = _sig_tokens(c)
        if not cand:
            continue
        overlap = max(len(cand & ps) / len(cand | ps) for ps in prior_sigs)
        if overlap >= QUIET_ZERO_JACCARD:
            out[id(c)] = ("zero", _still_tracking_note(con, tracked, prior_date))
        elif overlap >= QUIET_SMALL_JACCARD:
            out[id(c)] = ("small", _still_tracking_note(con, tracked, prior_date))
    return out


def _apply_thread_cap(chosen: List,
                      quiet: Dict[int, Tuple[str, str]]
                      ) -> Tuple[List, List[Dict], List[Dict]]:
    """Fragmentation contract (item 2) + quiet-small demotion (item 3): reorder
    `chosen` (score order in) so the ANALYST tier (the top ANALYST_TIER_SLOTS
    slots) is thread-DISTINCT — one prominent slot per causal arc — and carries
    no still-tracking demotion. A same-arc sibling or a quiet-small candidate is
    pushed below the analyst tier (into In Brief); nothing is dropped (Rook's
    non-destructive rule — a wrongly capped sibling still appears, as a snippet).

    Exhausted-pool edge (BUG-33): when fewer than ANALYST_TIER_SLOTS thread-
    distinct/non-quiet entries exist, the leftover analyst-tier positions have
    nothing distinct to fill them, so a demoted sibling positionally re-enters
    the prominent tier. The one-slot-per-arc LAW is violated at that edge — this
    makes it LOUD, not silent: the affected demotion records that it stayed
    in-tier (never claiming a demotion its slot contradicts), and
    `tier_underfilled` names every sibling that occupies an analyst slot.

    Returns (reordered, demotions, tier_underfilled)."""
    analyst_threads: set = set()
    promoted, demoted = [], []
    demotions: List[Dict] = []
    demo_by_entry: Dict[int, Dict] = {}
    for entry in chosen:                       # score order
        c = entry[0]
        threads = {t for t in (c.get("matched_memory") or []) if t}
        is_quiet_small = quiet.get(id(c), (None,))[0] == "small"
        collide = threads & analyst_threads
        if (len(promoted) < ANALYST_TIER_SLOTS and not collide
                and not is_quiet_small):
            promoted.append(entry)
            analyst_threads |= threads
            continue
        demoted.append(entry)
        d = None
        if collide:
            d = {"story": c["story_title"],
                 "reason": "same-arc sibling (fragmentation cap)",
                 "threads": sorted(collide)}
        elif is_quiet_small:
            d = {"story": c["story_title"],
                 "reason": "quiet thread (still-tracking)",
                 "threads": sorted(threads)}
        if d is not None:
            demotions.append(d)
            demo_by_entry[id(entry)] = d

    reordered = promoted + demoted
    # BUG-33: every analyst-tier position past the last genuinely-promoted slot
    # is held by a demoted entry (the pool ran out of thread-distinct
    # candidates) — disclose it, and correct that entry's demotion record so the
    # log never claims a demotion the position contradicts.
    tier_underfilled: List[Dict] = []
    for pos, entry in enumerate(reordered[:ANALYST_TIER_SLOTS], start=1):
        if pos <= len(promoted):
            continue                           # a genuinely promoted, distinct slot
        c = entry[0]
        tier_underfilled.append({
            "story": c["story_title"], "slot": pos,
            "threads": sorted({t for t in (c.get("matched_memory") or []) if t})})
        d = demo_by_entry.get(id(entry))
        if d is not None:                      # honest: it did NOT leave the tier
            d["in_tier_slot"] = pos
            d["reason"] += (f" — pool exhausted, kept in analyst slot {pos} "
                            "(no thread-distinct candidate to replace it)")
    return reordered, demotions, tier_underfilled


def _tripwire_families(analyst_entries: List) -> List[Dict]:
    """Rook's fragmentation tripwire: FLAG (never fold) a suspected same-event
    family in the analyst tier — two thread-distinct analyst slots sharing
    >= TRIPWIRE_PROPER_NOUN_OVERLAP proper nouns are probably one arc the thread
    cap couldn't see (a no-thread day-zero crisis). Data for the day-14 read."""
    nouns = [(e[0]["story_title"], _proper_nouns(e[0])) for e in analyst_entries]
    flags = []
    for i in range(len(nouns)):
        for j in range(i + 1, len(nouns)):
            shared = nouns[i][1] & nouns[j][1]
            if len(shared) >= TRIPWIRE_PROPER_NOUN_OVERLAP:
                flags.append({"slots": [i + 1, j + 1],
                              "stories": [nouns[i][0], nouns[j][0]],
                              "shared": sorted(shared)})
    return flags


def _prior_edition(con: sqlite3.Connection, date: str) -> Optional[Dict]:
    """The most recent edition BEFORE `date` (its selected stories), for NL-57's
    content-novelty proxy. story_slots is the ranker's persisted selection — the
    stories the reader last saw for this line. None on a first-ever edition."""
    row = con.execute(
        "SELECT date, story_slots FROM briefings WHERE date < ?"
        " AND story_slots IS NOT NULL ORDER BY date DESC LIMIT 1", (date,)).fetchone()
    if row is None:
        return None
    try:
        slots = json.loads(row["story_slots"] or "[]")
    except (ValueError, TypeError):
        return None
    stories = [{"story_title": s.get("story_title", ""),
                "summary": s.get("summary", "")}
               for s in slots if isinstance(s, dict)]
    return {"date": row["date"], "stories": stories}


def select_slots(
    clusters: List[Dict],
    items_by_id: Dict[int, sqlite3.Row],
    followed_outlets: set,
    memory_steers: bool = False,
    con: Optional[sqlite3.Connection] = None,
    prior_edition: Optional[Dict] = None,
    state: Optional["steering.SteeringState"] = None,
    entity_hits: Optional[Dict[int, List[int]]] = None,
    look_sources: Optional[Dict[int, str]] = None,
    look_notes: Optional[Dict[int, str]] = None,
) -> Tuple[List[RankedSlot], Dict]:
    steer = state or steering.INERT
    scored = []
    for c in clusters:
        cluster_items = [items_by_id[i] for i in c["item_ids"] if i in items_by_id]
        followed = any(r["outlet"] in followed_outlets for r in cluster_items)
        p = personal_score(c, followed, memory_steers, steer)
        comb = combined_score(p, c["world_impact"])
        # Stamped on the cluster so the replay envelope can persist the scores
        # the run ACTUALLY used. data's M3 no-stacking audit recomputes these
        # from the persisted inputs and requires exact equality — an audit that
        # recomputes without a recorded value to compare against is checking its
        # own arithmetic, not the run's.
        c["personal_score"], c["combined_score"] = p, comb
        scored.append((c, cluster_items, followed, p, comb))

    # NL-57 quiet-thread classification (item 3) — needs the ledger + the prior
    # edition; without a DB it degrades to normal selection (test-friendly).
    quiet = (_classify_quiet_threads(scored, con, prior_edition)
             if con is not None else {})
    quiet_zero = [
        {"story": s[0]["story_title"], "note": quiet[id(s[0])][1]}
        for s in scored if quiet.get(id(s[0]), (None,))[0] == "zero"
    ]
    # Quiet-ZERO candidates leave Today entirely (Following only — the thread
    # stays visible in Following, it just does not re-surface as a story).
    active = [s for s in scored if quiet.get(id(s[0]), (None,))[0] != "zero"]
    # Stamped on the cluster for the same reason the scores above are (fix loop
    # 1, QA F-7): this verdict is reachable ONLY with the run's own ledger and
    # its own prior edition, and both move. A replay that re-derived it from
    # today's tables would be replaying today's quiet into yesterday's run — the
    # exact error `replay._CfgShim` exists to prevent on the tag side. Persisted
    # in `_replay_envelope` so `replay.flip_replay` can hold the exclusion
    # identical on both slates instead of counting a flip the live pipeline
    # removed before selection ever ran.
    for entry in scored:
        entry[0]["quiet_zero"] = quiet.get(id(entry[0]), (None,))[0] == "zero"

    primaries = sorted(
        (s for s in active if s[3] > 0), key=lambda s: s[4], reverse=True
    )
    zero_pool = sorted(
        (s for s in active if s[3] == 0),
        key=lambda s: (s[0]["world_impact"], s[4]),
        reverse=True,
    )

    override_pick = None
    if zero_pool and zero_pool[0][0]["world_impact"] >= OVERRIDE_THRESHOLD:
        override_pick = zero_pool[0]

    take_primary = MAX_SLOTS - (1 if override_pick else 0)
    chosen = primaries[:take_primary] + ([override_pick] if override_pick else [])
    chosen.sort(key=lambda s: s[4], reverse=True)

    # Slot-dup guard (code-owned, deterministic): collapse near-duplicate
    # selections, promote the next-ranked primary, disclose. The override
    # instance loses to a primary duplicate (its slot then goes unfilled —
    # a normal outcome).
    deduped = []
    dropped_dupes = []
    for entry in chosen:
        dup_of = next(
            (kept for kept in deduped if _near_duplicate(entry[0], kept[0])), None
        )
        if dup_of is not None:
            dropped_dupes.append(
                {"dropped": entry[0]["story_title"], "kept": dup_of[0]["story_title"]}
            )
            if entry is override_pick:
                override_pick = None
            continue
        deduped.append(entry)
    if dropped_dupes:
        # Target: the primary quota refills; a dropped override's slot stays
        # unfilled (a normal outcome, per the override contract).
        target = take_primary + (1 if override_pick else 0)
        pool = [p for p in primaries if p not in deduped and p is not override_pick]
        for candidate in pool:
            if len(deduped) >= target:
                break
            if any(_near_duplicate(candidate[0], kept[0]) for kept in deduped):
                continue
            deduped.append(candidate)
        deduped.sort(key=lambda s: s[4], reverse=True)
    chosen = deduped

    # Fragmentation cap (item 2) + quiet-small demotion (item 3): the analyst
    # tier becomes thread-distinct and still-tracking-free; siblings fall to In
    # Brief. Then the tripwire reads the (thread-distinct) analyst tier for a
    # no-thread family it could not catch.
    chosen, cap_demotions, tier_underfilled = _apply_thread_cap(chosen, quiet)
    family_flags = _tripwire_families(chosen[:ANALYST_TIER_SLOTS])

    slots: List[RankedSlot] = []
    for n, (c, cluster_items, followed, p, comb) in enumerate(chosen, start=1):
        count, label, wire_excluded, named = corroborate(cluster_items)
        is_override = override_pick is not None and c is override_pick[0]
        q = quiet.get(id(c))
        is_still = q is not None and q[0] == "small"
        slots.append(
            RankedSlot(
                slot=n,
                story_title=c["story_title"],
                summary=c["summary"],
                item_ids=c["item_ids"],
                outlets=named,
                matched_tags=c["matched_tags"],
                matched_memory=c["matched_memory"],
                followed_analyst=followed,
                personal_score=round(p, 3),
                world_impact=c["world_impact"],
                combined_score=comb,
                override=is_override,
                corroboration_count=count,
                corroboration_label=label,
                wire_items_excluded=wire_excluded,
                # match-only: never touched personal_score/selection above —
                # carried through so persist() can apply earned-slot revival
                matched_dormant=c.get("matched_dormant", []),
                still_tracking=is_still,
                still_tracking_note=(q[1] if is_still else ""),
            )
        )
    override_slot = next((s.slot for s in slots if s.override), None)
    meta = {
        "dedup": {"dropped": dropped_dupes} if dropped_dupes else {"dropped": []},
        "override": {
            "pool_size": len(zero_pool),
            "threshold": OVERRIDE_THRESHOLD,
            "fired": override_pick is not None,
            # world-impact of the best zero-match candidate (named precisely —
            # M3 review cosmetic: the old key read like a combined score)
            "top_zero_match_world_impact": zero_pool[0][0]["world_impact"] if zero_pool else None,
            "story": override_pick[0]["story_title"] if override_pick else None,
            # NL-138 ledger death (ruling ④): the prose `reason` key is gone
            # from this row. THE DAY-14 OVERRIDE CALIBRATION IS UNHARMED and
            # that is the ruling's own finding, not a hope — it reads
            # pool_size, threshold, fired, top_zero_match_world_impact, slot,
            # and the slot's matched_tags/world_impact, every one of which is
            # still written here. `story` stays: a title identifies WHICH
            # story fired so the read can be traced back to the edition; it is
            # not a justification and makes no claim about the reader.
            "slot": override_slot,
        },
        # NL-63 M2 selection-layer instrumentation (the day-14 read).
        "slot_contract": {
            "count": len(slots), "floor": SLOT_FLOOR, "max": MAX_SLOTS,
            "analyst_tier": ANALYST_TIER_SLOTS,
            "thin_day": len(slots) < SLOT_FLOOR,
        },
        "fragmentation": {
            "demotions": cap_demotions,        # same-arc siblings + quiet demotes
            "family_flags": family_flags,      # Rook's tripwire (flag, never fold)
            # BUG-33: siblings that positionally occupy analyst slots because the
            # thread-distinct pool was exhausted (one-slot-per-arc's edge case,
            # disclosed not silent). Empty on any day with a full distinct tier.
            "tier_underfilled": tier_underfilled,
        },
        "quiet_threads": {
            "following_only": quiet_zero,       # zero-delta re-surfaces dropped
            "still_tracking": [s.story_title for s in slots if s.still_tracking],
        },
        "weights": {
            "topic": TOPIC_WEIGHT, "domain": DOMAIN_WEIGHT, "memory": MEMORY_WEIGHT,
            "followed_boost": FOLLOWED_BOOST, "personal_share": PERSONAL_SHARE,
            "entity": steering.ENTITY_WEIGHT,
        },
        "model": RANK_MODEL,
        "prompt_file": PROMPT_FILE,
    }
    meta["entities"] = _entity_meta(steer, scored, chosen, slots,
                                    entity_hits or {}, look_sources or {},
                                    look_notes or {})
    return slots, meta


def _entity_meta(steer: "steering.SteeringState", scored: List, chosen: List,
                 slots: List[RankedSlot], entity_hits: Dict[int, List[int]],
                 look_sources: Dict[int, str],
                 look_notes: Dict[int, str]) -> Dict:
    """THE PER-RUN ENTITY RECEIPTS — every followed entity, every run, seen=0
    included (engineering :110; Onna's "quiet must render", product R2 §R2.2).

    This is NL-18's substrate and the record NL-14 starved without: the
    seven-run empty streak is structurally impossible once absence is a WRITTEN
    state carrying a note. It is also the instrument the pre-registered trigger
    table reads (adr/0023, metrics M1/M3/M5).

    RECEIPTS ARE META, NEVER A DELTA. Nothing here reaches the writer's ledger,
    the memory tables, or any reader surface — the never-a-delta property that
    let steering ship selection-side survives untouched.

    `outranked_by` answers the only question a receipt with look=1, selected=0
    leaves open: what beat it. Computed against the entity's BEST scoring
    cluster this run, capped at the slot count so one dormant actor cannot grow
    the row without bound."""
    ent_best: Dict[int, float] = {}
    for c, _items, _f, _p, comb in scored:
        for eid in c.get("matched_entities") or []:
            if comb > ent_best.get(eid, -1.0):
                ent_best[eid] = comb
    selected: Dict[int, Dict] = {}
    for n, entry in enumerate(chosen, start=1):
        for eid in entry[0].get("matched_entities") or []:
            selected.setdefault(eid, {"slot": n, "story": entry[0]["story_title"]})
    outranked: Dict[int, List[Dict]] = {}
    for eid, best in ent_best.items():
        if eid in selected:
            continue
        outranked[eid] = [
            {"story": s.story_title, "combined": s.combined_score}
            for s in slots if s.combined_score > best
        ][:MAX_SLOTS]
    return {
        "armed": steer.armed,
        "reserve": steering.ENTITY_LOOK_RESERVE,
        "watched": len(steer.watched),
        "receipts": steering.receipts(steer, entity_hits, look_sources,
                                      selected, outranked, look_notes),
        "moves": [
            {"id": m.id, "concept": m.concept, "entity_id": m.entity_id,
             "blessed_at": m.blessed_at, "blessed_by": m.blessed_by}
            for m in steer.moves
        ],
        "suppressed_tags": sorted(steer.suppressed_tags),
        # Degrade-loud, count-once, tripwire-disclosed, never a dead run: a
        # non-empty list here is a real defect the run SURVIVED and reported.
        "integrity": list(steer.integrity),
    }


def _replay_envelope(prompt: str, items: List[sqlite3.Row],
                     memory_topics: List[str], dormant: List[str],
                     cfg: config.SourcesConfig, window_desc: str,
                     date_local: str, clusters: List[Dict]) -> Dict:
    """THE REPLAY HARDENER (charter item 8; the probe's named gap).

    The 2026-08-02 engineering probe had to rebuild run 47's prompt by hand to
    ask whether a different seat would have formed the Fed cluster. It worked,
    and it worked because someone reconstructed the inputs from memory — which
    is not a property a program can rely on twice. This envelope makes the
    reconstruction MECHANICAL and, more importantly, CHECKABLE: the sha is what
    turns "we rebuilt something plausible" into "we rebuilt those bytes".

    WHAT IS STORED, AND WHY NOT MORE. The ordered item ids, not the rendered
    lines. `source_items` rows carry the titles, so the rebuild reads them back
    and re-renders through `render_items_block` — the same renderer the run
    used — and compares shas. Storing ~550 rendered lines per run would make the
    envelope roughly 50 kB instead of ~3 kB, and it would still be checked by
    the same sha, so it would buy durability, not fidelity.

    THE DURABILITY BOUND, STATED SO A GREEN REBUILD IS NEVER OVER-READ.
    `ingest.upsert_item` UPDATEs `title` in place on every later sighting of a
    URL (ingest.py:226). A feed that revises a headline inside the candidate
    window therefore changes bytes this envelope does not hold, and the rebuild
    for that run will MISMATCH — permanently. That is a real coverage limit of
    this instrument, not a bug in it: the sha reports the drift LOUDLY instead
    of replaying a prompt that was never sent, and `items_sha256` is stored
    separately from `prompt_sha256` precisely so a mismatch localises to the
    items rather than to "something changed". Replays are consequently a
    FRESH-RUN instrument, strongest the same day, and the audit records
    `recomputable: false` rather than guessing (data's M3 counter-metric —
    missing is FAIL, default-deny).

    `clusters` are the model's OUTPUT and are reconstructible from nothing, so
    they ARE stored — bounded by MAX_CLUSTERS at ~1 kB each. They carry the
    scores the run used, which is what lets the flip-replay attribute a
    selection delta to steering rather than to a re-scored world.
    """
    template = ""
    try:
        template = (paths.PROMPTS_DIR / PROMPT_FILE).read_text(encoding="utf-8")
    except OSError:
        pass
    return {
        "prompt_sha256": _sha256(prompt),
        "items_sha256": _sha256(render_items_block(items)),
        "template_sha256": _sha256(template),
        "prompt_file": PROMPT_FILE,
        "item_ids": [r["id"] for r in sorted(items, key=lambda r: r["id"])],
        "threads": {"active": list(memory_topics), "dormant": list(dormant or [])},
        "tags": {"broad": list(cfg.interests_broad),
                 "granular": list(cfg.interests_granular)},
        "window_desc": window_desc,
        "date_local": date_local,
        "max_clusters": MAX_CLUSTERS,
        # FOLLOWED_BOOST is outlet-derived, so a re-score cannot reproduce the
        # run's numbers without knowing which outlets were followed WHEN IT RAN.
        # Persisted for that reason alone.
        "followed_outlets": sorted({s.name for s in cfg.followed_analyst_sources}),
        "clusters": [
            {k: c.get(k) for k in (
                "story_title", "summary", "item_ids", "matched_tags",
                "matched_memory", "matched_dormant", "world_impact",
                "matched_entities", "look_injected", "look_entity_id",
                # `quiet_zero` is the NL-57 verdict this run reached with THIS
                # run's ledger and prior edition (select_slots stamps it). It is
                # a receipt, not a re-derivable field — see the stamp's comment.
                "personal_score", "combined_score", "quiet_zero")}
            for c in clusters
        ],
    }


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def apply_looks(
    clusters: List[Dict],
    hits: Dict[int, List[int]],
    state: "steering.SteeringState",
    items_by_id: Dict[int, sqlite3.Row],
) -> Tuple[List[Dict], Dict[int, str], Dict[int, str], Dict]:
    """THE RESERVED LOOK. Returns (clusters, look_sources, look_notes, meta).

    The guarantee, stated exactly: a followed entity at entity altitude whose
    aliases matched this run's intake gets its items CLUSTERED AND SCORED. Not
    slotted — "a guaranteed look, never a guaranteed slot" is the promise
    language the product council adopted verbatim (R2 item 2).

    THREE OUTCOMES, all receipted:
      * look_source='model'    — the ranker already formed a cluster over the
                                 entity's items. The guarantee is satisfied at
                                 ZERO reserve cost, which is the common case and
                                 the reason a 2-slot reserve is enough.
      * look_source='injected' — the ranker omitted it and the reserve minted a
                                 deterministic cluster (`steering.build_look`).
                                 This is the sanctioned post-model fallback
                                 (engineering :121), taken because asking the
                                 ranker to score injected candidates honestly
                                 was left an OPEN compliance question, not a
                                 settled one.
      * look_source=''         — no look. With seen>0 this is Kass's contention
                                 receipt and it always carries its reason.

    DARK LAW: injection is gated on `state.armed`. Observation is not — the
    'model' outcome is recorded on every run, dark included, because noticing
    that the ranker formed a Fed cluster changes nothing about the edition and
    is exactly the evidence the dark era exists to bank.

    NO CALENDAR CAN REACH THIS FUNCTION. Candidacy comes only from `hits`, and
    `hits` comes only from alias matches against items in THIS run's intake. An
    entity with no pool items has no arm by which to obtain a look, on any date.
    """
    overlaps = steering.annotate(clusters, hits, state)
    look_sources: Dict[int, str] = {}
    look_notes: Dict[int, str] = {}
    covered = {eid for c in clusters for eid in (c.get("matched_entities") or [])}
    for eid in covered:
        look_sources[eid] = "model"

    candidates: List[Dict] = []
    for w in state.watched:
        ids = hits.get(w.id) or []
        if not ids or w.id in covered:
            continue
        if not w.steers:
            # Criterion (c): a storyline-altitude follow is WEIGHTLESS, so an
            # injected cluster for it would score 0 and could never be selected.
            # Spending reserve on it would take the look from an entity that can
            # use it. Receipted with its own reason, never silently skipped.
            look_notes[w.id] = ("seen but no look — follow sits at storyline "
                                "altitude, which carries zero entity weight")
            continue
        if not state.armed:
            look_notes[w.id] = ("seen but no look — steering is dark "
                                "(threads_steer_selection false)")
            continue
        candidates.append({"entity_id": w.id, "seen": len(ids),
                           "newest_item_id": ids[0]})

    granted, denied = steering.allocate_looks(candidates,
                                              steering.ENTITY_LOOK_RESERVE)
    injected: List[Dict] = []
    for cand in granted:
        w = state.by_id(cand["entity_id"])
        if w is None:
            continue
        ids, _dropped = _cap_cluster_items(hits[w.id],
                                           {i: items_by_id[i]["outlet"]
                                            for i in hits[w.id]
                                            if i in items_by_id})
        look = steering.build_look(w, ids, items_by_id)
        injected.append(look)
        look_sources[w.id] = "injected"
    for cand in denied:
        look_notes.setdefault(
            cand["entity_id"],
            f"seen but no look — {len(candidates)} entities contended for "
            f"{steering.ENTITY_LOOK_RESERVE} reserved look(s); allocation is "
            "pool-signal strength, then freshest item, then entity id")

    model_clusters = list(clusters)
    n_model = len(model_clusters)
    clusters = model_clusters + injected
    displaced: List[Dict] = []
    if injected and len(clusters) > MAX_CLUSTERS:
        # `injected` IS THE GATE, and the count is only the second condition.
        # Fix loop 1 (QA F-3, the NO-GO): this branch used to fire on cluster
        # COUNT alone, so a run that minted NOTHING still evicted ranker
        # clusters — at DARK, where the milestone's whole premise is that
        # nothing changes. A 13-cluster payload is a LAWFUL live input:
        # `parse_clusters` refuses only > MAX_CLUSTERS * 2 (:989-990), so 13-24
        # model clusters reach here on any run, and on every one of them the
        # batch as first built dropped a cluster HEAD would have scored, emitted
        # "reserved look displaced ... to make room" with `injected == []`, and
        # — because personal-matched clusters are protected — evicted the
        # zero-match wi>=8 cluster FIRST, i.e. the override contract's own
        # material. Displacement is the PRICE OF A LOOK; with no look there is
        # nothing to charge for, and the pass-through restores HEAD byte
        # identity at dark AND at armed-with-no-injection.
        #
        # RULED AT GATE 2026-08-08 (M2 gate R1, org-decidable under the ratified
        # promise): CAP-HEADROOM FORM — a look consumes headroom when it exists,
        # otherwise displaces exactly ONE cluster; the model's own overflow is
        # never charged to the look. Coincides with as-built for all
        # n_model <= 12; changes nothing reachable at dark. Implementation lands
        # with the arming milestone, born-red pinned at n_model >= 13 armed; the
        # as-built evict-to-12 stands until then (pinned by
        # test_the_cluster_cap_does_not_move).
        #
        # THE CAP DOES NOT MOVE (engineering :110). MAX_CLUSTERS is coupled to
        # PROMPT_MARGIN_CHARS through NL-133's arithmetic pin — raising it is a
        # money-guard change, not a tuning knob — so a look that would exceed
        # the cap DISPLACES instead.
        #
        # WHICH cluster leaves, in ascending eviction order: personal-match
        # clusters last (evicting a followed tag's story to make room for a
        # followed entity's would manufacture Ruth's starvation from the other
        # direction), then lowest world_impact (so a wi>=8 zero-match candidate
        # — the override contract's own material — is the LAST thing evicted),
        # then model order. Deterministic, and disclosed as a run warning.
        keep_n = max(0, MAX_CLUSTERS - len(injected))
        order = sorted(
            range(n_model),
            key=lambda i: (
                bool(model_clusters[i].get("matched_tags")
                     or model_clusters[i].get("matched_memory")
                     or model_clusters[i].get("matched_entities")),
                model_clusters[i].get("world_impact", 0),
                -i,
            ),
        )
        evict = set(order[:max(0, n_model - keep_n)])
        displaced = [model_clusters[i] for i in sorted(evict)]
        clusters = [c for i, c in enumerate(model_clusters)
                    if i not in evict] + injected
    meta = {
        "overlaps": overlaps,
        "injected": [{"entity_id": c["look_entity_id"],
                      "story": c["story_title"], "items": len(c["item_ids"])}
                     for c in injected],
        "contended": len(candidates),
        "denied": [c["entity_id"] for c in denied],
        "displaced": [{"story": c.get("story_title", ""),
                       "world_impact": c.get("world_impact", 0)}
                      for c in displaced],
    }
    return clusters, look_sources, look_notes, meta


# ---------------------------------------------------------------------------
# Persistence (idempotent per date; prior version archived first)
# ---------------------------------------------------------------------------

def pending_selection(
    con: sqlite3.Connection, date: str
) -> Optional[sqlite3.Row]:
    """The STAGED selection for `date` (NL-106), or None when nothing is staged.

    A staged row exists only between a regenerate's rank stage and its promote
    (generate.persist_generation) — or forever after, if that run died. Mid-run
    readers prefer it over the live row's slots; the server never calls this,
    because the reader's world is the live row and only the live row.

    Table-guarded — but MISSING-TABLE ONLY (gate finding G-1). On a pre-0023
    database the table is simply absent, which is positive evidence that nothing
    was ever staged, so the read degrades to the live row rather than killing a
    run. Every OTHER member of sqlite3.OperationalError — locked, disk I/O,
    malformed — is evidence of nothing at all, and this read DECIDES WHICH WRITE
    PATH THE PROMOTE TAKES: a freak error swallowed here silently selects the
    plain path, installing a new narrative against the OLD slots on the live row
    while the staged row survives. That is exactly the mixture this table exists
    to prevent, produced with no error surfaced anywhere. So: degrade on absence,
    propagate on everything else.

    This is deliberately NARROWER than the house 0012/0013 degrade pattern
    (memory.py, server.py), and the difference is the point — those degrades gate
    read-only rendering, where swallowing an error costs a blank pane. This one
    gates a write, where swallowing an error costs the invariant. The staging
    WRITE takes the same stance from the other end and fails loud (see persist).
    """
    try:
        return con.execute(
            "SELECT date, story_slots, corroboration_labels, token_cost,"
            " created_at FROM briefings_pending WHERE date = ?", (date,)
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise


def persist(con: sqlite3.Connection, report: RankReport, meta: Dict) -> List[Dict]:
    """Upsert the briefings row for the date. If one exists, its current state
    is archived to briefings_history BEFORE overwrite (the idempotent-re-run
    rule binds from the first overwritable briefing — ADR-0001, live now).
    Every run also appends a ranking_runs instrumentation row.

    NL-106 amendment (stage-and-promote): when the existing row is READABLE (it
    has a narrative), this function no longer archives or overwrites anything.
    It stages the new selection in briefings_pending and leaves the live row
    alone, so the reader's edition survives the whole run; the archive-then-
    overwrite happens atomically with the new body in persist_generation. The
    bodyless and no-row arms are unchanged.

    Lifecycle v2: DETECTS earned-slot auto-revival here — POST-selection by
    construction (only slots that already won on merits reach this function),
    which is the hard constraint's guarantee that dormant threads never boost
    their own revival. NL-108: detection is all that happens at rank. The
    memory table is not written by this function at all; the transition is
    applied by generate.persist_generation when the edition publishes. Returns
    the PENDING revival list [{topic, last_covered}] — what will be revived on
    publish, computed against memory as of rank."""
    # Revival PREVIEW before serialization: capture each matched dormant
    # thread's previous coverage date so the slot JSON carries the
    # back-reference ("last covered <date>") for M5's narrative.
    revived_preview: Dict[str, Dict] = {}
    for s in report.slots:
        for topic in s.matched_dormant:
            key = topic.casefold()
            if key in revived_preview:
                continue
            row = con.execute(
                "SELECT m.topic, b.date AS last_covered FROM memory m"
                " LEFT JOIN briefings b ON b.id = m.last_referenced_briefing_id"
                " WHERE lower(m.topic) = lower(?) AND m.status = 'dormant'",
                (topic,),
            ).fetchone()
            if row is not None:
                revived_preview[key] = {
                    "topic": row["topic"], "last_covered": row["last_covered"]
                }
    for s in report.slots:
        s.revived_threads = [
            revived_preview[t.casefold()]
            for t in s.matched_dormant
            if t.casefold() in revived_preview
        ]

    story_slots = json.dumps([s.__dict__ for s in report.slots])
    corroboration = json.dumps(
        {
            "standing_caveat": report.caveat,
            "per_story": [
                {
                    "slot": s.slot,
                    "corroboration_count": s.corroboration_count,
                    "corroboration_label": s.corroboration_label,
                    "wire_items_excluded": s.wire_items_excluded,
                    "outlets": s.outlets,
                }
                for s in report.slots
            ],
        }
    )
    # B3-D1 (durable money record): price the persisted rank step through the
    # seam's cost_fields so the briefings.token_cost row carries the full lane/
    # shadow key set and its `usd`/`total_usd` are usd_charged — 0.0 on the
    # subscription lane, never usage_to_usd (which always shadow-prices and would
    # show charged spend for a $0 subscription run). B3-D2: the fall label rides
    # too. Byte-identical on the api lane (charged == shadow == usage_to_usd).
    _rank_cfg, _rank_fb = _effective_rank()   # B3-D5: the transport's resolution, not a fresh one
    _rank_fields = llm.cost_fields(_rank_cfg, report.token_usage,
                                   fallback_reason=_rank_fb)
    token_cost = json.dumps(
        {
            "steps": [
                {
                    "step": "rank_select",
                    "prompt_tokens": report.token_usage.get("prompt_tokens"),
                    "completion_tokens": report.token_usage.get("completion_tokens"),
                    "usd": _rank_fields["usd_charged"],
                    **_rank_fields,
                }
            ],
            "total_usd": _rank_fields["usd_charged"],
        }
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with con:
        existing = con.execute(
            "SELECT * FROM briefings WHERE date = ?", (report.date,)
        ).fetchone()
        if existing is not None and existing["narrative_text"]:
            # NL-106 STAGE: a READABLE edition already exists for this date, so
            # this is a regenerate — and the old edition must survive the whole
            # run. Do not archive, do not touch the live row: STAGE the new
            # selection and let persist_generation promote it in one transaction
            # once a replacement body exists (the promote does the archive, so
            # ADR-0001 still gets exactly one history row per replaced edition,
            # carrying the old slots WITH the old body).
            #
            # This is the fix for the ~30-minute window in which a regenerate
            # left a listed-but-blank edition in the Archive (NL-103 QA-1). The
            # M3-gate rule the old NULL enforced — old narrative must never live
            # against new slots on the live row — is honoured MORE strictly here:
            # the live row keeps BOTH its old slots and its old narrative until
            # they are replaced together, atomically.
            #
            # INSERT OR REPLACE (not ON CONFLICT DO UPDATE): `date` is the whole
            # primary key, nothing references this table and it carries no
            # triggers, so replace IS the upsert — and it needs no SQLite
            # version floor beyond the one the rest of the schema already sets.
            try:
                con.execute(
                    "INSERT OR REPLACE INTO briefings_pending (date, story_slots,"
                    " corroboration_labels, token_cost, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (report.date, story_slots, corroboration, token_cost, now),
                )
            except sqlite3.OperationalError as exc:
                # A staging write that cannot land must NOT fall back to the
                # destructive path — that would silently re-open the exact hole
                # this table closes. Fail loud; `with con:` rolls the whole
                # transaction back, so the saved edition is untouched.
                raise RankingError(
                    "cannot stage this re-rank: the briefings_pending table is "
                    "missing (migration 0023 has not been applied) — run "
                    "`newslens migrate`, then rank again. Your saved edition "
                    f"was left exactly as it was ({exc})"
                ) from exc
        elif existing is not None:
            # A row with NO body: nothing readable to protect (a failed FIRST
            # run, or a rank that never got published). Byte-for-byte the
            # pre-NL-106 behaviour — archive the husk, overwrite in place, and
            # stage nothing.
            con.execute(
                "INSERT INTO briefings_history (briefing_id, date, story_slots,"
                " corroboration_labels, narrative_text, script_text,"
                " audio_file_path, token_cost, generated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    existing["id"], existing["date"], existing["story_slots"],
                    existing["corroboration_labels"], existing["narrative_text"],
                    existing["script_text"], existing["audio_file_path"],
                    existing["token_cost"], existing["generated_at"],
                ),
            )
            # New slots invalidate any narrative written for the OLD slots —
            # NULL the generation fields on re-rank (M3 gate review, NOTES
            # item 11; the archived history row above preserves them).
            con.execute(
                "UPDATE briefings SET story_slots = ?, corroboration_labels = ?,"
                " token_cost = ?, generated_at = ?, narrative_text = NULL,"
                " script_text = NULL, audio_file_path = NULL WHERE id = ?",
                (story_slots, corroboration, token_cost, now, existing["id"]),
            )
        else:
            con.execute(
                "INSERT INTO briefings (date, story_slots, corroboration_labels,"
                " token_cost, generated_at) VALUES (?, ?, ?, ?, ?)",
                (report.date, story_slots, corroboration, token_cost, now),
            )
        # NL-108: THE MEMORY SIDE-EFFECTS DO NOT FIRE HERE ANY MORE.
        #
        # Continuity's spine (update_references -> the dormancy clock and the
        # most-recently-referenced cap) and earned-slot auto-revival
        # (revive_matched, dormant -> active) used to run at RANK time, in this
        # transaction. That advanced both clocks for editions that never
        # published: this function commits at the rank stage, and analysis,
        # narrative, script, audio and budget all come AFTER it. A run that died
        # downstream — or every losing attempt of the NL-146 retry ladder — left
        # threads looking fresher than the reader's record supported.
        #
        # They now fire at generate.persist_generation, inside the promote's own
        # transaction, keyed off the slots that edition actually installs. This
        # is the SAME trigger discipline the moat's delta ledger already runs on
        # (M1 gate F, generate.py: "a delta is written ONLY once its edition is
        # published"); memory's clocks were the last sidecar still writing on
        # rank rather than on publication.
        #
        # `revived_preview` above stays here and stays READ-ONLY: the slot JSON
        # needs each thread's prior coverage date ("last covered <date>") before
        # serialization, and reading it cannot move anything. It is also what
        # makes the deferral honest — the narrative's back-reference is computed
        # against the state as of rank, and the promote applies the transition
        # the preview described.
        revived = list(revived_preview.values())
        if revived:
            # The rank's SELECTION record — what this attempt would revive on
            # publish, not what it revived. The transition itself is recorded by
            # the published edition (slot JSON revived_threads); a rank attempt
            # that never publishes now leaves the memory table untouched, and
            # this row is the only trace it was ever considered.
            meta["revivals_pending"] = revived
        con.execute(
            "INSERT INTO ranking_runs (date, meta, token_usage) VALUES (?, ?, ?)",
            (
                report.date,
                json.dumps(
                    {
                        **meta,
                        "status": "ok",
                        "item_count": report.item_count,
                        "cluster_count": report.cluster_count,
                        "slots": len(report.slots),
                    }
                ),
                # SQL NULL for absent usage, matching log_failed_run (M3
                # review cosmetic — one convention, not two).
                json.dumps(report.token_usage) if report.token_usage else None,
            ),
        )
    return revived


def _sources_file_path() -> str:
    """The sources file THIS process resolves — for error messages only.

    Degrades to the bare name rather than raising: paths.SOURCES_FILE goes
    through the PEP 562 guard, which raises RuntimeError in an unsanctioned
    process, and an error message must never turn one error into a different,
    worse one."""
    try:
        return str(paths.SOURCES_FILE)
    except Exception:                       # noqa: BLE001 — message-only path
        return "sources.yaml"


def _sources_label() -> str:
    """How to NAME the sources file in a refusal. Byte-identical to the
    pre-M2 wording for the founder (the default profile) — his messages do not
    move — and profile-qualified for everyone else."""
    try:
        slug = paths.current_profile()
    except Exception:                       # noqa: BLE001 — message-only path
        return "sources.yaml"
    if slug == paths.DEFAULT_PROFILE:
        return "sources.yaml"
    return f"profile {slug!r}'s sources.yaml"


def _profile_label() -> str:
    """How to NAME the acting profile in a refusal — same degrade idiom as
    _sources_label(), and for the same reason.

    Gate F6 (2026-07-25): the no-interests refusal interpolated
    paths.current_profile() bare. current_profile() RAISES on a malformed
    NEWSLENS_PROFILE (normalize_profile's slug refusal), so a library caller
    that reached this branch — run_rank(cfg=..., env={}) with a bad profile
    env — got a ProfileError out of the message-building step instead of the
    RankingError refusal it was owed. A refusal must never turn one error into
    a different, worse one; the degraded label says so out loud."""
    try:
        return repr(paths.current_profile())
    except Exception:                       # noqa: BLE001 — message-only path
        return "<unresolved>"


def log_failed_run(
    con: sqlite3.Connection,
    date: str,
    error: str,
    attempts: Optional[List[Dict]] = None,
) -> None:
    """Failures are instrumentation too — the day-14 readout must see them.

    `attempts`: billed-attempt ledger off the raised error, when it carried
    one — a total failure can still have spent real money (run 28 did, and
    its row said token_usage NULL; the ledger in meta is the honest record).
    """
    failure_meta: Dict = {"status": "failed", "error": error[:500]}
    if attempts:
        failure_meta["llm_attempts"] = attempts
    with con:
        con.execute(
            "INSERT INTO ranking_runs (date, meta, token_usage) VALUES (?, ?, NULL)",
            (date, json.dumps(failure_meta)),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_rank(
    date: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
    cfg: Optional[config.SourcesConfig] = None,
    env: Optional[dict] = None,
) -> RankReport:
    import os

    src_env = env if env is not None else os.environ
    date = date or local_today()

    cfg = cfg if cfg is not None else config.load_sources()
    # Stage-0 M2: NAME the profile and the resolved file in both refusals. The
    # M1 posture is unchanged — a profile with no tags refuses to rank, and
    # that refusal IS the Commissioning's door — but "sources.yaml" stopped
    # being a unique noun the moment a second profile could exist, and a reader
    # told to edit "sources.yaml" has several to choose from. The wrong one is
    # the founder's.
    if cfg.problems:
        raise RankingError(
            f"{_sources_label()} has problems: " + "; ".join(cfg.problems))
    if not cfg.has_interests:
        raise RankingError(
            f"profile {_profile_label()} has no interests configured "
            "— ranking needs THIS reader's own tags (the personal-impact axis "
            f"has nothing to match without them). Add them under `interests:` "
            f"in {_sources_file_path()}, or through the served UI's interest "
            "editor."
        )
    key = (src_env.get("OPENAI_API_KEY") or "").strip()
    # A″ (2026-07-17): rank is anthropic (subscription by default, api the
    # fall-over) since B2 — the OpenAI key is the INERT offline-test seam value the seam
    # passes through and the anthropic providers ignore. Require it ONLY if the
    # rank seat ever resolves to openai (it never does today; the anthropic seat's
    # own gate — check_lane in the seam — handles availability). A keyless-OpenAI
    # rank with the anthropic seat is HEALTHY (was a stale blanket refusal).
    if llm.seat_is_openai("rank", src_env) and not key:
        raise RankingError(
            "the rank seat resolves to OpenAI (gpt-4o) but OPENAI_API_KEY is not "
            "set — get one at platform.openai.com/api-keys, then add to .env"
        )

    own_con = con is None
    if own_con:
        db.migrate()
        con = db.connect()
    try:
        # BUG-6 (M3 fix loop 1): EVERY RankingError raised once the date is
        # known and a connection exists logs an instrumentation row — the
        # day-14 readout must see runs dying on a misconfigured cap or a
        # broken prompt, not just LLM failures. Refusals that happen before
        # this point (no key / no interests / sources problems) have no
        # connection to log through, by construction.
        try:
            return _run_rank_body(con, date, cfg, src_env, key)
        except RankingError as exc:
            log_failed_run(
                con, date, str(exc),
                attempts=getattr(exc, "llm_attempts", None),
            )
            raise
    finally:
        # B3-D5: guaranteed teardown of the request-scoped rank resolution set by
        # _run_rank_body (this try wraps that body 1:1) — always reset, so a
        # raise mid-run never leaks a stale (cfg, reason) into the next rank op.
        global _ACTIVE_RANK
        _ACTIVE_RANK = None
        if own_con:
            con.close()


def _run_rank_body(
    con: sqlite3.Connection,
    date: str,
    cfg: config.SourcesConfig,
    src_env,
    key: str,
) -> RankReport:
    """run_rank's post-connection body. Raises RankingError for every handled
    failure; the caller logs each one to ranking_runs (BUG-6)."""
    # Memory sync FIRST: memory.md is the source of truth at generation time
    # (spec §B, literally) — the principal's hand edits must be in the DB
    # before the context is pulled. A broken file is a loud, logged failure:
    # silently ignoring memory edits is the transparency surface's one
    # unforgivable bug.
    try:
        mem_sync = memory.sync_memory(con)
    except memory.MemorySyncError as exc:
        raise RankingError(str(exc)) from exc
    # Snapshot the file identity the sync just wrote: the post-run refresh at
    # the bottom must not clobber a hand-edit made DURING the ~90s LLM call
    # (M4 gate optional, adopted — see the guarded write below).
    try:
        mem_mtime = paths.MEMORY_FILE.stat().st_mtime_ns
    except OSError:
        mem_mtime = None

    window = candidate_window(con, date)
    history = ingested_history_days(con)
    items = gather_items(con, window["start_iso"])
    composition = pool_composition(con, window["start_iso"])
    if not items:
        raise RankingError(
            f"no ingested items inside the candidate window "
            f"({window['days']}d, {window['basis']}) — if your last briefing "
            "was moments ago, nothing new has arrived since; otherwise run "
            "`newslens ingest` first"
        )
    memory_topics = active_memory_topics(con)
    # Lifecycle v2: dormant threads join the prompt as a MATCH-ONLY
    # vocabulary — zero scoring influence (personal_score never reads
    # matched_dormant); a match only matters after a story has already
    # earned its slot, when persist() applies the auto-revival.
    dormant = memory.dormant_topics(con)
    window_desc = f"the last {window['days']:g} day(s), {window['basis']}"

    try:
        prompt = build_prompt(date, items, cfg, memory_topics, window_desc, dormant)
    except OSError as exc:
        raise RankingError(f"cannot read prompts/{PROMPT_FILE} ({exc})") from exc
    except Exception as exc:  # noqa: BLE001 — principal-editable template:
        # ANY render error (KeyError from a typo'd {placeholder}, etc.)
        # must be a visible, named failure — same class-wide discipline as
        # discovery's BUG-3 fix.
        raise RankingError(
            f"prompts/{PROMPT_FILE} did not render "
            f"({type(exc).__name__}: {exc}) — check its {{placeholders}}"
        ) from exc

    cap = config.budget_cap_usd_per_run(src_env)
    est = estimate_cost_usd(prompt)
    if est > cap:
        raise RankingError(
            f"estimated ranking cost ${est:.4f} exceeds BUDGET_CAP_USD_PER_RUN "
            f"${cap:.2f} — aborting before the call (raise the cap in .env if "
            "this is intentional)"
        )

    tag_levels = {name: "domain" for name in cfg.interests_broad}
    tag_levels.update({name: "topic" for name in cfg.interests_granular})
    known_ids = {r["id"] for r in items}

    repair_sink: Dict = {}
    attempt_ledger: List[Dict] = []
    # B3-D5: resolve the rank seat ONCE for the whole rank operation, AFTER the
    # cap pre-check (error precedence preserved) and publish it on _ACTIVE_RANK.
    # call_llm_validated (its transport + ledger), the disclosure below, and
    # persist's durable token_cost all read THIS one resolution via
    # _effective_rank — so a `claude` binary that flaps mid-run can never fork
    # the transport lane from the ledger/persisted lane. run_rank's finally is
    # the guaranteed teardown (it wraps this whole body 1:1).
    global _ACTIVE_RANK
    _ACTIVE_RANK = llm.effective_seat("rank")
    clusters, usage = call_llm_validated(
        key, prompt, known_ids, tag_levels, memory_topics,
        repairs=repair_sink, dormant_topics=dormant, cost_sink=attempt_ledger,
        # NL-133: the trim needs to know which outlet each candidate came from
        # or it cannot preserve diversity. Same rows `known_ids` is built from,
        # read once — no extra query.
        item_outlets={r["id"]: r["outlet"] for r in items},
    )

    items_by_id = {r["id"]: r for r in items}
    followed_outlets = {s.name for s in cfg.followed_analyst_sources}
    # NL-17 M2 — steering, derived ONCE per run and threaded from here.
    # `armed` is the whole dark law: cfg.threads_steer_selection is False by
    # default and has never been true on a live run, so on every live path today
    # `steer` is INERT-shaped and every branch it gates is unreachable.
    steer = steering.for_run(con, armed=cfg.threads_steer_selection,
                             tag_levels=tag_levels)
    entity_hits = steering.match_items(
        ((r["id"], r["title"]) for r in items), steer.watched)
    clusters, look_sources, look_notes, look_meta = apply_looks(
        clusters, entity_hits, steer, items_by_id)
    slots, meta = select_slots(
        clusters, items_by_id, followed_outlets,
        memory_steers=cfg.threads_steer_selection,
        con=con, prior_edition=_prior_edition(con, date),
        state=steer, entity_hits=entity_hits,
        look_sources=look_sources, look_notes=look_notes,
    )
    meta["entities"].update(look_meta)
    meta["replay"] = _replay_envelope(
        prompt, items, memory_topics, dormant, cfg, window_desc, date, clusters)
    meta["threads_steer_selection"] = cfg.threads_steer_selection
    meta["window"] = window
    meta["history_days"] = history
    # NL-142 item 2: pool composition on EVERY run, capped or not. Recorded
    # unconditionally on purpose — "no eviction today" is itself the receipt
    # that makes the 27-silent-runs history readable going forward, and a
    # metric that only appears when it is bad cannot show a trend.
    meta["pool"] = composition
    if (repair_sink.get("repaired") or repair_sink.get("tag_shape_normalized")
            or repair_sink.get("clusters_truncated")):
        # Disclosed repair/tolerance (never silent, never unpersisted): the
        # warning renders in CLI output AND the detail persists in
        # ranking_runs.meta.repairs — BUG-7: a tag-shape-only run (the common
        # case) must feed the day-30 tolerance-frequency readout too.
        meta["repairs"] = repair_sink
    if len(attempt_ledger) > 1:
        # A recovered corrected-retry must never be silent: token_usage holds
        # only the returning attempt, so the meta ledger + this warning are
        # the sole record that attempt 1 billed and failed — and the sole
        # evidence the run-28 fix fired in the wild.
        meta["llm_attempts"] = attempt_ledger

    report = RankReport(
        date=date,
        slots=slots,
        item_count=len(items),
        cluster_count=len(clusters),
        window_days=window["days"],
        window_basis=window["basis"],
        history_days=history,
        token_usage=usage,
    )
    report.override_fired = meta["override"]["fired"]
    report.override_pool_size = meta["override"]["pool_size"]
    # B3-D2: disclose the armed single-fall in the run log (never silent). If
    # rank ran the api fall-over because the subscription lane was unavailable
    # and NEWSLENS_LANE_FALLBACK=api is armed, say so — this run spent real API
    # money the subscription lane would not.
    _, _rank_fb = _effective_rank()   # B3-D5: reuse the one resolution, no re-resolve
    if _rank_fb:
        report.warnings.append(
            "rank ran the API fall-over lane (NEWSLENS_LANE_FALLBACK=api armed; "
            f"subscription lane unavailable: {_rank_fb}) — this billed real API "
            "money; ledger row labeled lane=api(fallback:…). Fix the CLI or "
            "unset the fallback to fail loud instead")
    # Memory surfacing (spec §B: staleness is SURFACED, never silent; sync
    # edits are acknowledged so the principal knows the file was honored).
    report.warnings.extend(mem_sync.summary_lines())
    if len(attempt_ledger) > 1:
        true_usd = round(sum(a.get("usd") or 0.0 for a in attempt_ledger), 6)
        report.warnings.append(
            f"rank retry: attempt 1 billed then failed validation — the "
            f"corrected retry recovered; true LLM spend ${true_usd:.4f} "
            f"across {len(attempt_ledger)} attempts (full ledger in "
            "ranking_runs.meta.llm_attempts; token_usage shows the returning "
            "attempt only)"
        )
    if meta["dedup"]["dropped"]:
        names = "; ".join(
            f"{d['dropped']!r} (same story as {d['kept']!r})"
            for d in meta["dedup"]["dropped"]
        )
        report.warnings.append(
            f"slot-dup guard: collapsed {len(meta['dedup']['dropped'])} "
            f"near-duplicate selection(s) — {names}"
        )
    # NL-63 M2 selection-layer disclosures (never silent; the day-14 read).
    sc = meta.get("slot_contract") or {}
    if sc.get("thin_day"):
        report.warnings.append(
            f"thin day: {sc['count']} slot(s) surfaced, under the {sc['floor']}-"
            "story floor — shipped as-is, never padded to the floor "
            "(the material wasn't there)")
    frag = meta.get("fragmentation") or {}
    for d in frag.get("demotions") or []:
        if d.get("in_tier_slot"):
            # BUG-33: this sibling did NOT leave the prominent tier — the
            # under-fill line below tells that truth; a "demoted out" line here
            # would contradict its own slot.
            continue
        report.warnings.append(
            f"selection: {d['story']!r} demoted out of the prominent tier — "
            f"{d['reason']} (threads: {', '.join(d['threads']) or 'none'})")
    for u in frag.get("tier_underfilled") or []:
        report.warnings.append(
            f"selection: analyst tier under-filled — {u['story']!r} kept in "
            f"analyst slot {u['slot']} despite sharing arc "
            f"({', '.join(u['threads']) or 'none'}); fewer than "
            f"{ANALYST_TIER_SLOTS} thread-distinct stories were available "
            "(one-slot-per-arc violated at the edge — disclosed, not silent)")
    for fl in frag.get("family_flags") or []:
        report.warnings.append(
            "fragmentation tripwire (FLAG, not folded): analyst slots "
            f"{fl['slots']} share proper nouns {fl['shared']} — possible same "
            f"event ({fl['stories'][0]!r} / {fl['stories'][1]!r}); day-14 read")
    qt = meta.get("quiet_threads") or {}
    for q in qt.get("following_only") or []:
        report.warnings.append(
            f"quiet thread: {q['story']!r} not surfaced on Today ({q['note']}) — "
            "no new development; it stays visible under Following (NL-57)")
    # NL-17 M2 steering disclosures. DEGRADE-LOUD, TRIPWIRE-DISCLOSED, NEVER A
    # DEAD RUN: every one of these is a defect the run survived and reported,
    # and each also persists in ranking_runs.meta.entities so the day-N read
    # sees frequency, not just today's console.
    ents = meta.get("entities") or {}
    for bad in ents.get("integrity") or []:
        report.warnings.append(
            f"steering integrity ({bad['kind']}): concept {bad['concept']!r} "
            f"[move {bad['move_id']}] — {bad['reason']}")
    for inj in ents.get("injected") or []:
        report.warnings.append(
            f"reserved look: minted a cluster for entity {inj['entity_id']} "
            f"({inj['items']} item(s), {inj['story']!r}) — the ranker omitted "
            "it; a look is guaranteed, a slot never is")
    for d in ents.get("displaced") or []:
        report.warnings.append(
            f"reserved look displaced a ranker cluster: {d['story']!r} "
            f"(world_impact {d['world_impact']}) left the {MAX_CLUSTERS}-cluster "
            "cap to make room — the cap does not move (NL-133 coupling)")
    denied = ents.get("denied") or []
    if denied:
        report.warnings.append(
            f"reserved looks contended: {ents.get('contended')} entities with "
            f"pool items, {steering.ENTITY_LOOK_RESERVE} reserved look(s); "
            f"entity id(s) {denied} recorded seen>0 look=0 (allocation: "
            "pool-signal strength, freshest item, entity id)")
    if qt.get("still_tracking"):
        report.warnings.append(
            "quiet thread: still-tracking snippet(s) for "
            + ", ".join(repr(s) for s in qt["still_tracking"]) + " (NL-57)")
    if repair_sink.get("tag_shape_normalized"):
        report.warnings.append(
            f"tag-shape normalization: {repair_sink['tag_shape_normalized']} "
            "bare-string tag name(s) accepted (exact vocabulary matches; "
            "levels from the canonical map — disclosed schema tolerance, "
            "ADR-0004 M5 amendment)"
        )
    if repair_sink.get("clusters_truncated"):
        trims = repair_sink["clusters_truncated"]
        detail = "; ".join(
            f"{t['cluster']!r} {t['kept'] + t['dropped']} -> {t['kept']} items "
            f"({t['outlets_before']} outlets -> {t['outlets_after']})"
            for t in trims)
        report.warnings.append(
            f"cluster cap: {len(trims)} cluster(s) trimmed to the "
            f"{MAX_CLUSTER_ITEMS}-item per-cluster cap — {detail}. Items were "
            "dropped round-robin across outlets — every outlet keeps a "
            "representative whenever a cluster has at most "
            f"{MAX_CLUSTER_ITEMS} distinct outlets, and the per-trim outlet "
            "counts above are the measured fact (NL-133); details stored in "
            "ranking_runs.meta.repairs"
        )
    if repair_sink.get("repaired"):
        emptied = repair_sink.get("clusters_emptied") or []
        report.warnings.append(
            f"clustering repair: {repair_sink['repaired']} duplicate item "
            f"assignment(s) dropped (kept each item's first cluster"
            + (f"; {len(emptied)} cluster(s) emptied and removed" if emptied else "")
            + ") — details stored in ranking_runs.meta.repairs"
        )
    if history < window["days"]:
        # The honesty half of the recency rule: never imply a lookback
        # the ingested corpus doesn't actually have.
        report.warnings.append(
            f"candidate window: {window['days']:g}d ({window['basis']}); "
            f"ingested history available: {history:g}d — early runs see "
            "less than the window requests"
        )
    # NL-142 item 2: the cap's receipts. `composition` is computed for EVERY
    # run (it lands in ranking_runs.meta, so a quiet run is still on record as
    # quiet); the warning fires only when something was actually thrown away.
    _pool_warning = pool_warning(composition)
    if _pool_warning:
        report.warnings.append(_pool_warning)
    revived = persist(con, report, meta)
    if revived:
        # Every automatic transition is surfaced, dated, never silent
        # (lifecycle v2 contract) — and NL-108 makes the surfacing honest about
        # WHEN. At this point nothing has been revived: the transition applies
        # at the promote, so a run that dies between here and there leaves these
        # threads dormant, exactly as it found them. Announcing "auto-revived"
        # in the past tense here is what the 2026-08-10 case did, and that
        # morning's revival was announced to a run that never published.
        names = ", ".join(
            r["topic"] + (f" (last covered {r['last_covered']})" if r["last_covered"] else "")
            for r in revived
        )
        report.warnings.append(
            f"memory: {len(revived)} dormant thread(s) earned their slots and "
            f"will auto-revive when this edition publishes: {names} — memory.md "
            "changes at publication, not now"
        )
    # memory.md must reflect THIS run's own effects (revivals, new reference
    # dates) immediately — not on the next run's sync. Render-only, and
    # GUARDED: if the file changed since the opening sync wrote it (a hand
    # edit during the LLM call), skip the refresh and say so — a transparency
    # surface never overwrites edits it hasn't read (M4 gate optional, adopted).
    try:
        current_mtime = paths.MEMORY_FILE.stat().st_mtime_ns
    except OSError:
        current_mtime = None
    if mem_sync.stale_refusal:
        # NL-81 EMBEDDED DEGRADE (contract §5.2, Onna's split). The opening
        # sync refused the file, which means it imported NOTHING and rewrote
        # NOTHING — so this refresh must not write either, or the "neither side
        # mutated" property the refusal promises would be false by the end of
        # the same run. The edition itself still completes on database state;
        # the refusal is already in report.warnings (extended from
        # mem_sync.summary_lines above), so it cannot be missed.
        report.warnings.append(
            "memory.md post-run refresh skipped too — the file is out of date "
            "and this run left it, and the database, exactly as they were"
        )
    elif mem_mtime is not None and current_mtime != mem_mtime:
        report.warnings.append(
            "memory.md changed while this run was in flight — post-run refresh "
            "skipped to protect your edit; the next sync will reconcile it"
        )
    else:
        try:
            memory.write_memory_file(con)
        except OSError as exc:  # non-fatal (opening sync validated
            # writability), but never silent:
            report.warnings.append(
                f"memory.md could not be refreshed after this run ({exc}) — it "
                "will catch up on the next sync"
            )
    return report
