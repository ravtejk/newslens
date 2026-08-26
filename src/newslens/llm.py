"""llm.py — the provider seam (B1 of the depth-architecture build).

One module owns LLM transport + lane selection + token/cost attribution so
that adding a provider (B2: Claude API lane) or a transport (B3: the
`claude -p` subscription lane) is a plug HERE, not surgery across the three
historical call sites (generate.call_llm, ranking.call_llm_validated,
analysis.call_analysis_model).

B2 UPDATE — the Claude API lane + Haiku seats land here: the "anthropic:api"
provider (raw urllib POST to /v1/messages, zero SDK dep) is registered, and the
rank/editor/script seats flip to claude-haiku-4-5 on the api lane. The
state/memory seat joins the table (gate ruling R1). Writer/analyst/synthesis
stay gpt-4o (their flips are B4/B6). See _anthropic_provider + the SEATS table.

B3 UPDATE — the `claude -p` SUBSCRIPTION lane lands here (subscription-lane
mandate, DECISIONS 2026-07-16): the "anthropic:subscription" provider (a thin
subprocess, NOT the Agent SDK — the 3.9 floor holds) is registered, and the
rank/editor/script DEFAULT lane flips api -> subscription (subscription is
ALWAYS the priority; the api lane is the registered fall-over). The subprocess
strips ANTHROPIC_API_KEY, disables all tools + the injection surface, runs in
an empty scratch cwd, and reads its prompt on stdin (Rook's four conditions).
Binary resolution is NEWSLENS_CLAUDE_BIN -> PATH -> ~/.local/bin/claude; a
missing binary is LaneUnavailable at the gate. See _subscription_provider,
resolve_claude_bin, and check_lane's subscription arm.

THE SEAT MAP TODAY — READ IT OFF `SEATS`, NEVER OFF THIS DOCSTRING. The B1/B2/B3
paragraphs above are a CHANGELOG (what each increment did, kept as the record of
how the seam grew); they are not a description of the current roster, and every
one of them has been superseded at the model level by ENG-M0 (2026-08-02/06) and
NL-17 M3 fix loop 1 (2026-08-09). Restating the roster in prose is precisely how
it went stale — so this paragraph states only the LAW that binds it:

  * NO SEAT MAY NAME A HAIKU MODEL (the principal's no-Haiku law, 2026-08-09).
    NL-147 removed the last of them: the live seats first (ENG-M0 and M3), then
    the unarmed `_HAIKU_API`/`_HAIKU_SUB` rollback rows and their price
    constants. `test_nl147_no_haiku.py` is the tooth; it reads `SEATS` and the
    module's own seat-row dicts, so a re-introduction fails rather than drifts.
  * Lane law is unchanged and orthogonal: subscription is the default for every
    anthropic seat, api is the registered fall-over, and a fall-over moves the
    LANE only — `effective_seat` does `replace(cfg, lane="api")` and never
    substitutes a model, which is why a lane event cannot change what runs.

B1 SCOPE — PURE REFACTOR (acceptance bar: existing suite green, unchanged):
  * B1 registered only the "openai" provider; every seat resolved to gpt-4o
    on the "api" lane — the current stack, expressed as config (the SEATS
    table below is the one-constant-seam precedent generalised to a table).
  * The three historical transport functions (generate._chat,
    ranking._post_chat, analysis._analysis_chat) keep their EXACT signatures
    (they are the suite's monkeypatch targets) and delegate their bodies
    here — so every current caller runs GPT-4o exactly as today. The request
    bytes and the returned OpenAI-shaped dict are identical; each caller's
    own retry/validation law is untouched.
  * The lane interface (LaneRequest -> LaneResponse, carrying token counts
    and cost attribution) is DEFINED here for B2/B3 to implement. Neither the
    Claude API lane nor the subscription lane is implemented in B1.

Binding rulings this seam is SHAPED to (DECISIONS.md 2026-07-16):
  * Subscription lane is ALWAYS priority once it exists; the API lane is the
    fail-over. Provider selection is therefore keyed on (provider, lane) so
    B3 registers a "anthropic:subscription" plug that a seat prefers; until
    B3 lands, only the api lane exists.
  * FAIL-LOUD default: a seat resolved to a lane/provider with no registered
    implementation raises LaneUnavailable naming the fix — never a silent
    wrong-lane call. NEWSLENS_LANE_FALLBACK=api is the principal-armed
    opt-in; B1 reads/reports the flag (execution needs a second lane, B2/B3).
  * A lane OWNS its own env/credentials: LaneRequest carries the credential,
    and a provider decides whether/how to use it — so the B3 subscription
    provider can build a `claude -p` subprocess whose env STRIPS
    ANTHROPIC_API_KEY (Rook's silent-billing guard) regardless of the caller.
  * Cost ledger supports SHADOW pricing: usd_shadow is ALWAYS computed from
    the seat's QA-pinned price table; usd_charged == usd_shadow on the api
    lane and 0.0 on the subscription lane (B3). Budget caps bind on
    usd_shadow in both lanes.

Pure stdlib (urllib) — preserves the project's deliberate zero-SDK posture.
This module imports nothing from generate/ranking/analysis: it is a leaf, so
there is no import cycle (those three import `llm`, not the reverse).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable, Dict, FrozenSet, Optional, Tuple

# The OpenAI chat endpoint (the seam's single copy — ranking.OPENAI_CHAT_URL
# and analysis's inline literal both named this same URL before B1).
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# The Claude API lane (B2). Raw urllib POST to the Messages API — zero SDK
# dependency, preserving the project's deliberate stdlib posture (the 3.9 venv
# has no anthropic SDK, by design). The lane reads its OWN endpoint (ADR-0014
# §2/§4: the anthropic provider does not honour LaneRequest.url, which is the
# openai offline-test seam — see _anthropic_provider).
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# GPT-4o pricing (USD per MTok) — the QA-pinned price table for the shadow
# ledger. Matches the per-file constants the three call sites carry today
# (ranking.RANK_USD_*, generate.WRITER_USD_*, analysis.ANALYSIS_USD_*).
# usd_shadow is computed from these, so a lane flip never forks the cost
# dashboard (Onna's law).
GPT4O_USD_PER_MTOK_IN = 2.50
GPT4O_USD_PER_MTOK_OUT = 10.00

# NL-147: HAIKU_USD_PER_MTOK_IN/OUT (1.00 / 5.00) were deleted here with the two
# Haiku seat rows they priced (see the block above the seat rows). They had no
# other consumer — grep-verified — so keeping them would have left a dangling
# price for a model no seat may use. The PRICE FACT itself is not lost and was
# never this module's to hold: `battery._ARM_PRICES["claude-haiku-4-5"]` is the
# model->rate map that prices historical ledger rows and battery arms, and it
# KEEPS its Haiku row (ledger data about what was already spent is a record, not
# a seat target — the no-Haiku law governs what may RUN).

# Claude Opus 4.8 pricing (USD per MTok) — B4: the writer seat flips to Opus on
# the Claude API lane. Thinking tokens BILL AS OUTPUT (adaptive thinking on the
# writer), so the shadow's out-rate covers thinking + prose. QA-pinned; a lane
# flip never forks the cost dashboard.
OPUS_USD_PER_MTOK_IN = 5.00
OPUS_USD_PER_MTOK_OUT = 25.00

# Claude Sonnet 5 pricing (USD per MTok) — B4: the analyst seat flips to Sonnet.
# The shadow uses the STANDARD $3/$15 (a conservative upper bound), NOT the
# temporary intro $2/$10 (through 2026-08-31) — never under-price the cap's
# figure. Document the intro so the cross-check to real billing is honest.
SONNET_USD_PER_MTOK_IN = 3.00
SONNET_USD_PER_MTOK_OUT = 15.00


# ---------------------------------------------------------------------------
# Config schema: a seat is data (providers as plugins, seats as a table)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeatConfig:
    """One seat's provider binding. B1: every seat is gpt-4o/openai/api.

    `thinking`/`effort` are the Claude knobs (B2 maps them to the Messages
    API `thinking:{type:"adaptive"}` + `output_config.effort`); they are None
    for the OpenAI seats and carried here so B2 is a config diff, not a
    schema change.
    """
    seat: str
    provider: str                    # "openai"  (B2: "anthropic")
    model: str
    lane: str                        # "api"     (B3: "subscription")
    usd_per_mtok_in: float
    usd_per_mtok_out: float
    timeout_s: int
    thinking: Optional[str] = None   # None | "adaptive"                (B2)
    effort: Optional[str] = None     # None | low|medium|high|xhigh|max  (B2)
    # B4: whether the model accepts sampling params (temperature/top_p/top_k).
    # The Claude 4.6+ family — Opus 4.8 and Sonnet 5 — REJECTS them with a 400;
    # GPT-4o still accepts them. False => the anthropic api provider OMITS
    # temperature (never a 400 on those seats).
    # WHO IS TRUE TODAY, since this default is what a new row inherits: after
    # ENG-M0 and NL-17 M3, EVERY anthropic row sets sampling=False, so `synthesis`
    # (gpt-4o) is the only seat left running the True default — and its request
    # bytes are byte-unchanged from B2/B1 (the pinned body tests do not move).
    # The B2-era phrasing here named "the Haiku/openai seats"; the Haiku half of
    # that pair no longer exists (NL-147, the no-Haiku law).
    sampling: bool = True
    # 2026-07-17 (field-charged): the `claude -p` subscription lane pays process
    # startup + agentic-harness overhead on top of generation, so the API-lane-
    # calibrated timeout is too tight there (his first post-B4 generate FAILED at
    # rank: claude -p exceeded the 90s API-calibrated cap on BOTH attempts, no
    # briefing row). timeout_sub_s is the per-seat SUBSCRIPTION-lane timeout; the
    # subscription provider uses (timeout_sub_s or timeout_s), so a seat that
    # never sets it keeps its api timeout on both lanes. api-lane timeouts are
    # unchanged (timeout_s), so the api pinned paths do not move.
    timeout_sub_s: Optional[int] = None


# The seat table — code constants (the one-constant-seam precedent, one row
# per seat).
# B2-ERA NOTE, kept as the record of that increment's reasoning and NOT a
# description of the shipped walls: B2 changed model/provider/lane/price and NOT
# timeouts, on the ground that Haiku was faster than GPT-4o so the existing
# headroom held (rank & analyst 90s, the writer family 120s, state 60s). Every
# one of those numbers has since been re-measured twice — the 2026-07-26
# thinking-seam re-tune and the 2026-08-06 ENG-M0 re-measure, both derived in the
# timeout blocks below. Read the walls off `SEATS`, not off this paragraph.
_GPT4O_API = dict(
    provider="openai", model="gpt-4o", lane="api",
    usd_per_mtok_in=GPT4O_USD_PER_MTOK_IN,
    usd_per_mtok_out=GPT4O_USD_PER_MTOK_OUT,
)

# B3 (subscription-lane mandate, DECISIONS 2026-07-16), the standing rule for
# every anthropic-provider row below: the seats DEFAULT to the `claude -p`
# SUBSCRIPTION lane — subscription is ALWAYS the priority, the api lane is the
# registered fall-over (NEWSLENS_LANE_<SEAT>=api, or the principal-armed
# NEWSLENS_LANE_FALLBACK=api). A lane flip changes the transport and usd_charged
# (0.0 on subscription) and NOTHING else: shadow is API-priced on either lane, so
# the cost dashboard never forks (Onna's law, the price-constant block above).
#
# ------------------------------------------------------------------------------
# NL-147: THE TWO HAIKU ROWS THAT USED TO LIVE HERE ARE DELETED (the no-Haiku law,
# principal 2026-08-09: no Haiku for any part of the product).
#
# WHAT THEY WERE, kept as a receipt because the deletion is the point: B2's
# `_HAIKU_API` / `_HAIKU_SUB` put rank/editor/script on claude-haiku-4-5, and the
# comment above them named `**_HAIKU_API` as the ROLLBACK TARGET a future
# maintainer should flip a row back to. ENG-M0 (2026-08-02/06) moved every one of
# those seats off Haiku — rank -> Sonnet 5, editor/script/state -> Opus 4.8 — and
# NL-17 M3 fix loop 1 took the last live seat (follow_altitude -> Sonnet 5), which
# left the two dicts referenced by NOTHING but that comment.
#
# WHY THEY STILL MATTERED ENOUGH TO DELETE, stated precisely so the census is not
# over-read: they were NOT a reachable fall-over. `effective_seat` falls a seat by
# `replace(cfg, lane="api")` — it moves the LANE on the seat's own row and never
# substitutes a model — so no armed fallback, no NEWSLENS_LANE_<SEAT>=api and no
# NEWSLENS_LANE_FALLBACK=api could ever have landed a call on Haiku once the seat
# rows moved. The live hazard was the PROSE: a standing instruction to a future
# maintainer to flip a row back to a model the principal has since outlawed. That
# is a trap with a fuse, not dead weight, and prose is exactly how the last two
# Haiku slips got in (llm.py's own record, the rank seat).
#
# THE ROLLBACK STORY THAT REPLACES IT, true of the tree as it stands: a seat's
# rollback target is ITS OWN ROW with `lane="api"` — same model, same prices, the
# api transport — which is what `effective_seat` already produces and what the
# api-lane tests pin. A MODEL rollback has no registered alternative by design;
# it is a seat ruling, and it goes through the principal like the rest of them.
# `**_GPT4O_API` remains the one registered non-anthropic target (synthesis's row,
# and the state seat's documented revert — see its row).
# ------------------------------------------------------------------------------

# B4 (Option C): the writer seat is Opus 4.8. 2026-07-17 (field batch, item C):
# the principal RULED it onto the `claude -p` SUBSCRIPTION lane and it is now
# FIELD-PROVEN — edition 7 (2026-07-17 22:45Z) ran the Opus narrative on the
# subscription lane end-to-end ($0.655 shadow, $0 charged), spot-check PASSED.
# So subscription is the default here too, joining the Haiku seats; the API lane
# is the registered fall-over (NEWSLENS_LANE_WRITER=api, or the armed
# NEWSLENS_LANE_FALLBACK=api). model + prices are unchanged (shadow is API-priced
# regardless of lane); only the transport and usd_charged move (0.0 on subscription).
# TRUNCATION-GAP CAVEAT (accepted residual, ADR-0015 known gap): the api lane
# REQUIRES max_tokens and its finish_reason=="length" guard catches a truncated
# ~2,500-word edition; the subscription lane has NO max_tokens and cannot see a
# truncation. On this lane the CATCH is the caller's validation floors (word
# count + structure) rather than a length-finish — a truncated edition fails
# those, not the guard. effort maps best-effort on subscription (`/effort`-style,
# ADR-0015 §2 "wobbliest part of lane (b)") vs exact on the api fall-over.
# adaptive thinking on (`thinking:{type:"adaptive"}`) — thinking BILLS AS OUTPUT
# and counts against max_tokens on the api fall-over (NARRATIVE_MAX_TOKENS carries
# the headroom). sampling=False: Opus 4.8 rejects temperature with a 400.
# REVERT = flip lane back to "api" in one clean diff (WRITER_MODEL derives from
# this row; the api fall-over bytes stay correct — pinned in the api-lane tests).
_OPUS_WRITER_SUB = dict(
    provider="anthropic", model="claude-opus-4-8", lane="subscription",
    usd_per_mtok_in=OPUS_USD_PER_MTOK_IN,
    usd_per_mtok_out=OPUS_USD_PER_MTOK_OUT,
    thinking="adaptive", effort="xhigh", sampling=False,
)

# B4: the analyst seat WAS Sonnet 5 (2026-07-17 item C, field-proven edition 7).
# ENG-M0 (seat ruling (b), 2026-08-02 rank + 2026-08-06 amendment): the analyst
# is now OPUS 4.8. The analyst is the seat that reads the retrieved corpus and
# writes the evidence-bound brief every downstream seat quotes from; it is
# reasoning work, and it was the cheapest seat still doing reasoning.
# adaptive thinking on, effort "high" (unchanged from the Sonnet row — the
# analyst's job did not change, the model under it did). sampling=False: Opus 4.8
# rejects temperature with a 400, exactly as Sonnet 5 did, so the omission that
# was already correct for this seat stays correct.
# THE LADDER MOVES WITH THIS ROW, by derivation and not by hand: analysis.py's
# ANALYSIS_USD_{IN,OUT}_PER_MTOK read this dict, so the out-rate $15 -> $25 and
# every constants-derived pin downstream (brief_bound_usd, the sonar ladder line,
# the NL-130 sweep, the NL-133 arithmetic) re-derives with no other edit.
# Same truncation-gap caveat as the writer — validate_brief's teeth, not a
# length-finish, are what catch a truncated brief on the subscription lane.
# The api lane is the registered fall-over (NEWSLENS_LANE_ANALYST=api).
_OPUS_ANALYST_SUB = dict(
    provider="anthropic", model="claude-opus-4-8", lane="subscription",
    usd_per_mtok_in=OPUS_USD_PER_MTOK_IN,
    usd_per_mtok_out=OPUS_USD_PER_MTOK_OUT,
    thinking="adaptive", effort="high", sampling=False,
)

# ENG-M0: the RANK seat, Haiku 4.5 -> SONNET 5 on the subscription lane.
#
# WHY THIS SEAT MOVES AT ALL — THE SLIP CLASS, with two live specimens. rank's
# job is to copy ~550 sparse [id=KEY] codes verbatim out of a long list and
# cluster them; a single mis-copied character kills the whole edition, because
# the closed-vocab guard (correctly) hard-rejects a fabricated id rather than
# silently mis-attributing it. Haiku slipped twice in the record:
#   * run 48 (2026-08-02, founder DB): "item_id key 'B15H' failed its check
#     symbol (a mis-copied id)" — failed after the one corrected retry, NO
#     briefing row written for that day;
#   * fresh1 run 3 (the second profile) — the same class.
# That is the transcription discipline the M4 temp-0 finding was protecting, and
# it is now protected by a more capable seat instead of by a sampling parameter
# the model no longer accepts.
#
# THE COUPLING TO THE POOL RAISE, stated because it is load-bearing in both
# directions: MAX_INPUT_ITEMS 550 -> 780 makes this prompt ~1.42x longer, i.e.
# ~230 more ids to transcribe without slipping. The raise is VALID ONLY WITH
# this seat — a bigger list under the seat that already slipped twice at the
# smaller list is the wrong direction. Both halves land together or neither does.
#
# thinking="adaptive": rank is NOT in _THINKING_OFF_SUB_SEATS and does not join
# it — its deliberation is doing real work (clustering ~550 headlines), and
# Rook's gate (an unmeasured off-arm first-attempt validity, where a format miss
# costs a whole extra call and a second miss kills the generate) still stands.
# Declaring it here makes the api fall-over honest too: it now SENDS the thinking
# param it was already paying for on the subscription lane. effort "high" — the
# same rung the analyst runs; rank is a judgment task, not a mechanical one.
# sampling=False: Sonnet 5 rejects temperature with a 400. See the temp-0
# retry-law rework in ranking.RETRY_CORRECTION — the exact-copy discipline now
# rests on the prompt's rule text and the corrected retry, not on temp 0.
_SONNET_RANK_SUB = dict(
    provider="anthropic", model="claude-sonnet-5", lane="subscription",
    usd_per_mtok_in=SONNET_USD_PER_MTOK_IN,
    usd_per_mtok_out=SONNET_USD_PER_MTOK_OUT,
    thinking="adaptive", effort="high", sampling=False,
)

# ENG-M0: the three remaining Haiku CONTENT seats -> OPUS 4.8, subscription.
#
# These three (editor, script, state) were the seats the 2026-07-26 thinking-seam
# flip put in _THINKING_OFF_SUB_SEATS: cheap Haiku models whose deliberation was
# measured to be 84-97% of their output tokens while the ANSWER stayed the same
# size. That was the right fix for a Haiku seat asked to do mechanical work.
# ENG-M0 changes the premise: these seats stop being mechanical. The editor makes
# real editorial judgment (what to cut, what a hedge costs, which A9 marks must
# survive), the script seat adapts prose for the ear, and the state seat writes
# the durable thread memory every later edition reads. So all three LEAVE
# _THINKING_OFF_SUB_SEATS in this same diff and declare adaptive thinking.
#
# THE CONSEQUENCE THAT MUST NOT BE MISSED — this is a DOUBLE flip (model AND
# thinking), and the thinking half moves the token volume far more than the model
# half does. The production record, read read-only from generation_log.jsonl:
#   editor_pass   thinking ON (pre-07-26, Haiku):  17,878 / 21,596 / 28,772 out
#                 thinking OFF (post-07-26):        3,429 /  4,211 /  4,005 out
#   script_adapt  thinking ON (pre-07-26, Haiku):  12,551 / 13,866 / 22,707 out
#                 thinking OFF (post-07-26):        1,896 /  2,135 /  2,033 out
# The 180s/120s/60s walls these seats carry TODAY were sized at 3.3x/4.2x/10x the
# THINKING-OFF ceiling. Turning thinking back on returns these seats to the
# thinking-ON regime — at which the shipped walls are not margin, they are a
# guaranteed timeout (editor 28,772 tok at the 71 tok/s throughput floor is
# ~405s against a 180s wall). The timeouts below are therefore RE-MEASURED on
# the flipped seats, never inherited; see the timeout block above SEATS.
# effort: the editor and script seats run "high" (judgment over prose the reader
# reads and hears); state runs "medium" — it writes five sentences of durable
# memory, and its off-arm ceiling was 6.0s, so the cheapest rung that still
# deliberates is the honest one.
# sampling=False on all three: Opus 4.8 rejects temperature with a 400. Their
# callers still PASS a temperature (generate._chat, memory_core's 0.2); the
# anthropic api provider now omits it for these seats, and the callers' comments
# are reworked in the same batch to stop claiming a determinism temp 0 buys.
# NL-17 M3 fix loop 1 (F-2): the follow-altitude RESOLVER leaves Haiku.
#
# WHY, and it is a law rather than a tuning preference: the principal's no-Haiku
# law. QA found this seat is not a dormant remainder — it is the FIRST LINK OF
# THE ARM CHAIN. The "tap Follow on the Fed" gesture the arming sitting requires
# routes `server.py -> follow_altitude.resolve_altitude -> effective_seat(
# "follow_altitude")`, so the one gesture M3 asks him to make was the one that
# fired a Haiku call.
#
# WHAT MOVES, AND WHAT DELIBERATELY DOES NOT:
#   * model + prices -> Sonnet 5 / the Sonnet table. `follow_altitude.
#     estimate_usd` prices off `cfg.usd_per_mtok_*`, so the dry-run plan and the
#     budget cap follow this row with no second edit.
#   * sampling=False, mirroring the ENG-M0 rows: Sonnet 5 REJECTS `temperature`
#     with a 400, and `resolve_altitude` passes RESOLVER_TEMPERATURE=0.0 on
#     every call. The subscription lane never forwards it, so the live path was
#     safe either way — but the API fall-over would have 400'd on its first real
#     use, which is precisely the lane you reach when things are already going
#     wrong. Same trap the rank swap hit; closed here rather than discovered.
#   * thinking STAYS None and the seat STAYS in _THINKING_OFF_SUB_SEATS. NL-99's
#     whole finding was that unrequested extended thinking — not the lane — made
#     this resolve take 9-46s and degrade 4/4 in production. A model swap does
#     not license re-importing the tax the seat was rescued from: this is a
#     mechanical single-turn classification, and the caller's parse + validate +
#     corrected-retry law is the backstop.
#   * timeout_sub_s is RE-MEASURED on the flipped seat, never inherited (the
#     ENG-M0 rule). See the seat row for the measurement.
#   * THE LANE DOES NOT MOVE, and that is a deliberate non-change this loop was
#     asked to re-examine and declined to make. subscription is HEAD's committed
#     value (63f4115, NL-99 "resolver comes home", 2026-07-26) which EXPLICITLY
#     SUPERSEDED the 07-20 api-lane ruling (d431277) after diagnosing that the
#     ~48s resolve was unrequested extended thinking, not the lane — 52 live
#     falsifier calls at 1.85-2.89s, $0 charged. DECISIONS 2026-07-26 records the
#     supersession in those words ("out of law — SUPERSEDED by this later
#     ruling"). A fix loop scoped to F-1..F-7 does not get to reverse a committed
#     principal lane ruling as a side effect, and flipping to api would re-arm
#     metered charge on the one seat a reader waits on. Raised to the gate with
#     receipts instead. The api lane remains reachable exactly as NL-99 left it:
#     NEWSLENS_LANE_FOLLOW_ALTITUDE=api, per-instance and never automatic.
_SONNET_RESOLVER_SUB = dict(
    provider="anthropic", model="claude-sonnet-5", lane="subscription",
    usd_per_mtok_in=SONNET_USD_PER_MTOK_IN,
    usd_per_mtok_out=SONNET_USD_PER_MTOK_OUT,
    sampling=False,
)
_OPUS_EDITOR_SUB = dict(
    provider="anthropic", model="claude-opus-4-8", lane="subscription",
    usd_per_mtok_in=OPUS_USD_PER_MTOK_IN,
    usd_per_mtok_out=OPUS_USD_PER_MTOK_OUT,
    thinking="adaptive", effort="high", sampling=False,
)
_OPUS_SCRIPT_SUB = dict(
    provider="anthropic", model="claude-opus-4-8", lane="subscription",
    usd_per_mtok_in=OPUS_USD_PER_MTOK_IN,
    usd_per_mtok_out=OPUS_USD_PER_MTOK_OUT,
    thinking="adaptive", effort="high", sampling=False,
)
_OPUS_STATE_SUB = dict(
    provider="anthropic", model="claude-opus-4-8", lane="subscription",
    usd_per_mtok_in=OPUS_USD_PER_MTOK_IN,
    usd_per_mtok_out=OPUS_USD_PER_MTOK_OUT,
    thinking="adaptive", effort="medium", sampling=False,
)

# TIMEOUT RE-TUNE 2026-07-26 (principal ruling: per-seat subscription timeouts
# may be RAISED; watchdog-only — no retry-count, thinking or lane change rides
# it). The 300s walls were set in 2026-07-17 as "api ceiling + ~300s lane tax",
# before anyone had measured what the subscription lane actually emits. The
# thinking-tax measurement did (debates/2026-07-26--newslens--engineering.md):
# subscription latency is output-token-linear at 71-106 tok/s (n=11, mean 88)
# plus ~1s of CLI startup, and 84-97% of these seats' output tokens are
# deliberation the seat row explicitly declined. The seats were therefore
# sitting against walls calibrated for a much smaller output.
#
# THE ARITHMETIC, per seat, against its OBSERVED per-call production ceiling
# (read read-only from generation_log.jsonl n=7 and ranking_runs n=6; these are
# per-CALL ledger rows, not per-edition sums):
#
#   seat    max out-tok   @71 tok/s   @88   @106   old wall   margin@71
#   editor       28,772        406s   328s   272s       300s      0.74x  <- BREACHED
#   script       22,707        321s   259s   215s       300s      0.94x  <- BREACHED
#   rank         22,748        321s   260s   216s       300s      0.93x  <- BREACHED
#   state       ~16,183        229s   185s   154s       300s   NOT A CEILING (avg)
#   writer       36,842        520s   420s   349s       900s      1.73x     holds
#
# editor is not a projection: the measurement's own shipped-arm probe hit
# `timeout_sub_s=300` at 300.02s exactly, and production has emitted 28,772
# editor tokens in one call (2026-07-18) — it completed only because that day
# ran near the top of the throughput band.
#
# RAISED 300 -> 600 on all four Haiku batch seats, state included (I first held
# state for want of a measurement; the gate overruled that, correctly). state's
# ~16,183 is an EDITION AVERAGE, and an average cannot bound a single CALL — nor
# does anything else on this lane: STATE_MAX_TOKENS binds the api lane only, so
# an uncapped state call has no ceiling short of ~80k tokens (~1140s @ 71/s).
#
# THE TRADE, stated: on a genuinely HUNG call, detection now takes 600s
# instead of 300s (and a two-attempt seat's worst case doubles with it). Bought
# with that: a legitimately slow ~320s pass that today times out, retries, and
# may still die — burning ~600s and possibly the edition — now simply finishes
# at ~320s. A timeout is an airbag, not a schedule: raising it CANNOT extend a
# healthy generation's wall clock, because the value is only ever
# subprocess.run(..., timeout=) (llm.py `_subscription_provider`). api-lane
# timeouts (timeout_s) are untouched, so no api pinned path moves.
#
# --- DOWN-TUNE 2026-07-26, riding the thinking-seam flip -----------------------
# The 600s above were a STOPGAP for the TAXED present, and the gate attached a
# rider to them: re-tune DOWNWARD in the same diff as any flip. That rider fires
# now. Leaving a 600s watchdog on a 45s path is not caution, it is a blindfold —
# the exact mistake the resolver made with 45s on a 14-48s path.
#
# Sized off the MEASURED off-arm observations (eng-6 §3.1, n=2 per seat on real
# production prompts), at >=3x the observed ceiling:
#
#   seat    off-arm observed   ceiling   x3      SHIPPED   margin vs ceiling
#   editor  45.4s / 54.5s       54.5s   163.5s     180s        3.30x
#   script  28.5s / 18.6s       28.5s    85.5s     120s        4.21x
#   state    5.7s /  6.0s        6.0s    18.0s      60s       10.00x
#
# SECOND PROPERTY, deliberately checked: each wall also has to outlast a call
# that TRIPS this seat's token band, or the watchdog would kill the very call
# the alarm exists to report. At the band ceiling and the throughput FLOOR (71
# tok/s): editor 6,000 tok = 86s < 180s; script 4,000 = 57s < 120s; state 1,200
# = 18s < 60s. The alarm can always fire and be read before the wall lands.
#
# --- RE-MEASURED 2026-08-06, ENG-M0 (the seat flip's chartered re-measure) -----
# EVERY wall below was re-derived against the seats as they now ship, because
# the flip changed BOTH factors that set a wall: the model (Haiku -> Opus 4.8 /
# Sonnet 5) and the thinking regime (editor/script/state left
# _THINKING_OFF_SUB_SEATS). Inheriting the old numbers was not an option in
# either direction — the thinking-OFF-sized walls would have been a guaranteed
# timeout if volume had returned to the taxed regime, and (as it turns out) the
# stopgap walls are now far too loose for what the seats actually do.
#
# THE THROUGHPUT MEASUREMENT, live on the subscription lane against real
# production prompts (probe battery 2026-08-06; full receipts in the ENG-M0
# report). This is the number the record could NEVER supply: generation_log
# stores tokens but never elapsed, so Opus throughput did not exist at any n
# until this probe.
#   Opus 4.8   67.1 -  94.3 tok/s (n=5: editor n=2, script/state/analyst n=1 each)
#   Sonnet 5  111.0 - 115.5 tok/s (n=6: rank, 550- and 780-item pools, n=3 each)
# CORRECTION ON RECORD: the first cut of this block claimed a 90 tok/s Opus floor
# off the editor alone. The three owed probes (script/state/analyst, run after
# the editor) measured 67.1-73.4 — the editor is the FAST end of the Opus band,
# not the floor. Every derived wall below was recomputed against 67.
# Both sit inside the previously-measured Haiku band (71-106, n=11, mean 88), so
# the lane's throughput is a property of the LANE more than of the model. The
# derivations below use a 90 tok/s FLOOR for Opus and 111 for Sonnet.
#
# THE HEADLINE FINDING, because it inverts the pre-flip fear: OPUS WITH ADAPTIVE
# THINKING IS DRAMATICALLY MORE TOKEN-EFFICIENT THAN HAIKU WITH THE CLI'S DEFAULT
# EXTENDED THINKING. The editor seat, same real prompt, three regimes:
#   Haiku + CLI default thinking (pre-07-26):   17,878 - 28,772 out
#   Haiku + MAX_THINKING_TOKENS=0 (post-07-26):  3,429 -  4,211 out
#   Opus 4.8 + adaptive         (MEASURED now):   5,382 -  5,867 out
# Adaptive thinking decides how much to deliberate; the taxed Haiku regime spent
# 84-97% of output on deliberation unconditionally. So turning thinking back ON
# costs ~1.4x the thinking-off volume, NOT the ~5-7x the taxed record implied.
# That ratio (5,867/4,211 = 1.39) is MEASURED on the editor and EXTRAPOLATED to
# script and state below — stated plainly because it is the one inference here.
#
# PER-SEAT DERIVATION. Provenance is labelled per row; a wall marked DERIVED is
# not a guess but it is not a live measurement of THAT seat either, and the
# report lists the probes still owed.
#
#   seat     ceiling (provenance)              /throughput   x margin   SHIPPED
#   rank     156.63s MEASURED (n=6 battery     —             3.83x       600
#            max, 780-item draw, 17,425 out;
#            ranges overlap across sizes)
#   editor    62.2s MEASURED (5,867 out)       —             3.9x        240
#   script    66.8s MEASURED (4,670 out)       —             3.6x        240
#   state     29.8s MEASURED (2,000 out)       —             4.0x        120
#   analyst   71.9s MEASURED (5,278 out)       —            10.0x        720
#
# WHAT THE THREE OWED PROBES CHANGED, kept on the record because the derivation
# was wrong in a way worth naming. The first cut DERIVED these three from the
# editor's throughput and each seat's thinking-off production ceiling, and both
# inputs were optimistic: script shipped at 180s (a 2.7x margin against its real
# 66.8s, UNDER the family's >=3x rule) and state at 90s off a derived 5.8s
# ceiling that measured 29.8s — 5x out. Derivation is not measurement, and this
# is what the charter meant by RE-MEASURED, not guessed.
#   writer   900 UNTOUCHED — the writer did not move in this batch.
#   follow_altitude 20 UNTOUCHED — the flagged exception, still Haiku, still
#            thinking-off, still the 8s reader-facing UI wall. DO NOT TOUCH.
#            SUPERSEDED AT THE MODEL, 2026-08-09 (NL-17 M3 fix loop 1): this seat
#            left Haiku for Sonnet 5 under the no-Haiku law. The line above is
#            ENG-M0's record and stays as written; what survived the flip is what
#            it was actually protecting — thinking-off, and the reader-facing
#            walls — and M3 RE-MEASURED the 20s subscription wall on the flipped
#            seat rather than inheriting it (see the seat row).
#
# state keeps a deliberately fat 15.5x: it is a 5.8s call, the absolute wall is
# still only 90s, and what it writes is the DURABLE thread memory every later
# edition reads — the asymmetry between "wait 90s" and "lose the thread record"
# is not close. This is the same reasoning that gave it 10x at 60s pre-flip.
#
# SECOND PROPERTY, checked exactly as the 07-26 down-tune checked it: each wall
# must also outlast a call that TRIPS this seat's token band (below), or the
# watchdog would kill the very call the alarm exists to report. At each band
# ceiling and the MEASURED Opus 67 tok/s floor: editor 10,000 tok = 149s < 240s;
# script 10,000 = 149s < 240s; state 4,000 = 60s < 120s. rank at its 30,000 band
# and the Sonnet 111 floor = 270s < 600s. Every alarm can fire and be read
# before its wall lands.
SEATS: Dict[str, SeatConfig] = {
    "rank":      SeatConfig("rank",      timeout_s=90,  timeout_sub_s=600, **_SONNET_RANK_SUB),
    # item C (2026-07-17): writer/analyst on the subscription lane. timeout_sub_s
    # = the api-calibrated ceiling + a ~300s subscription lane tax (claude -p
    # subprocess spin-up + agentic-harness verbosity — the same absolute tax the
    # mechanical Haiku seats pay over their api timeouts). analyst 240->540,
    # writer 600->900 (Opus xhigh on the harness is the slowest path in the
    # system; edition 7 ran fine but uninstrumented — pad the tax generously).
    "analyst":   SeatConfig("analyst",   timeout_s=240, timeout_sub_s=720, **_OPUS_ANALYST_SUB),
    "writer":    SeatConfig("writer",    timeout_s=600, timeout_sub_s=900, **_OPUS_WRITER_SUB),
    "editor":    SeatConfig("editor",    timeout_s=120, timeout_sub_s=240, **_OPUS_EDITOR_SUB),
    "script":    SeatConfig("script",    timeout_s=120, timeout_sub_s=240, **_OPUS_SCRIPT_SUB),
    # NL-17-M1 increment A (the altitude slice): the follow-altitude resolver
    # seat. A cheap mechanical single-turn classification (given a followed
    # thread, pick entity|storyline + the primary entity + a disclosure line).
    # MODEL: Sonnet 5, subscription — the same model class as `rank`. (This line
    # read "the same MODEL class as rank/editor/script (Haiku 4.5)" until NL-147;
    # it was written at M1 and outlived three seat flips. rank is Sonnet 5 and
    # editor/script are Opus 4.8, so the sentence had stopped being true of all
    # three seats it named as well as of this one.)
    #
    # THE HISTORY, kept because it was a correct read of a symptom and a WRONG
    # read of the cause (2026-07-20, DECISIONS "RESOLVER LANE FIX"): the feature
    # was 100% broken in production — a 12s subscription wall against a real
    # ~48s `claude -p` resolve gave 4/4 source=degrade and ZERO source=auto
    # commits, ever. The api lane did the same call in ~1.2s, so the seat was
    # ruled onto api and this row became the one exception to the all-
    # subscription default. That fixed the symptom.
    #
    # THE ACTUAL CAUSE, found 2026-07-25 (eng-4 §6, measured n=13 + control):
    # THE LANE WAS NEVER SLOW. `_subscription_provider` ignored SeatConfig
    # .thinking, so every resolve paid for extended thinking it never asked for.
    # As-shipped 9.38-46.1s p50 23.7s; with MAX_THINKING_TOKENS=0, the same seat
    # on the same lane: 1.85-2.89s p50 2.03s, $0 charged. The transport was the
    # bug; the lane was the scapegoat.
    #
    # SO THE SEAT COMES HOME (NL-99, THE $0-RUN LAW): code default is the
    # SUBSCRIPTION lane again, and this seat is armed in _THINKING_OFF_SUB_SEATS
    # so the flip does not re-import the tax it was fleeing. d431277's api
    # default is SUPERSEDED — the principal ruled that nothing metered runs for
    # what the subscription covers, and cents/month is still metered.
    # ESCAPE HATCH, meaning inverted AGAIN and this time out of law:
    # NEWSLENS_LANE_FOLLOW_ALTITUDE=api forces the METERED lane. That path is
    # reachable only by the principal's deliberate, per-instance sanction under
    # the $0-RUN LAW; it is not a fall-over and nothing arms it automatically
    # (effective_seat forbids api<-subscription; the api fallback stays UNARMED
    # per the 07-17 pure-fail-loud ruling). thinking/effort stay None — not
    # reasoning work; the caller's parse+validate+corrected-retry law backstops
    # the prompt-shaped JSON (rank's twin).
    # DELIBERATELY NOT in _STEP_PREFIX_SEAT: it is not a `generate` edition step
    # (its output is a REPORT, never edition state or a selection weight), so it
    # is reached only through follow_altitude.resolve_altitude, never
    # seat_for_step / generate.call_llm.
    # TIMEOUTS. subscription (the default now): timeout_sub_s=20 against a
    # measured 1.85-2.89s thinking-off resolve — ~6.9x headroom, on the family
    # sizing rule (>=3x off-arm ceiling; editor 3.3x / script 4.2x / state 10x).
    # RESOLVED BY THE GATE 2026-07-26: eng-4 C2 specified 20; the dispatch's 45
    # was conservatism, not evidence (52 live falsifier calls, no tail past ~3s),
    # and this is the one seat where a reader waits out the full wall before the
    # proven degrade — 20 caps that pin. api (sanctioned-exception only):
    # timeout_s=8, sized in 2026-07 against a healthy HAIKU api round-trip of
    # ~1.2s. A hung provider on either lane still degrades to the PROVEN
    # this-story commit (exact copy) in a beat, never pinning the reader.
    # DISCLOSED, NL-147, and left as it stands rather than quietly adjusted: the
    # 8s API wall is the one number on this row that was NOT re-measured when the
    # seat flipped to Sonnet 5 — M3's two n=9 runs below are both SUBSCRIPTION.
    # It is a sanctioned-exception lane nobody reaches without setting
    # NEWSLENS_LANE_FOLLOW_ALTITUDE=api by hand, and the degrade path makes a trip
    # cheap rather than fatal, so this is a stale premise and not a live break —
    # but "re-measured, never inherited" is not true of it, and measuring it costs
    # metered api calls this batch is not authorised to make. Raised to the gate.
    # NL-17 M3 fix loop 1 (F-2): Haiku 4.5 -> Sonnet 5, subscription (no-Haiku
    # law; this seat is the arm chain's first link — see _SONNET_RESOLVER_SUB).
    # TIMEOUTS RE-MEASURED ON THE FLIPPED SEAT, never inherited (ENG-M0's rule,
    # and the 07-17 field failure it came from). TWO INDEPENDENT n=9 RUNS, both
    # against founder threads 33-41, title-only, all first-attempt, $0 charged —
    # and BOTH are quoted, because one run's max is not a wall's evidence
    # (gate G-3):
    #   implementer (scratchpad/f2_reprobe.json):  min 2.90 / mean 3.25 / max 3.72s
    #   verifier    (scratchpad/fl1/reprobe9):     min 2.923 / mean 3.705 / max 5.856s
    # Same seat, same threads, same day: the TAIL moved 3.72 -> 5.856s between
    # runs, which is the honest reading of this seat's variance and the reason a
    # single run should never have set the number. The wall is sized on the
    # CONSERVATIVE tail. Haiku's baseline was 1.85-2.89s, so Sonnet costs ~1.3x
    # at the median and ~2x at the tail; the tail is what sizes a wall.
    # The 20s subscription wall therefore sits at 3.42x the worst observed
    # ceiling (5.4x on the friendlier run): inside the family rule (>=3x) but
    # with real margin, not abundant margin. KEPT rather than widened — this is
    # the one seat where a reader waits out the whole wall before the proven
    # degrade, so padding it past the evidence would spend that wait on nothing.
    # REVISIT-IF: any observed resolve past ~7s puts the ceiling inside 3x. The
    # next seat change re-measures rather than inheriting 3.42x as headroom.
    "follow_altitude": SeatConfig("follow_altitude", timeout_s=8, timeout_sub_s=20, **_SONNET_RESOLVER_SUB),
    # synthesis has no live call site yet (B6 builds it); it is declared here
    # so the seat table is the whole roster the design named, not a subset.
    "synthesis": SeatConfig("synthesis", timeout_s=120, **_GPT4O_API),
    # The state/memory seat joined the seam in B2 (gate ruling R1). 2026-07-17 the
    # PRINCIPAL RULED it onto Haiku 4.5 / the subscription lane (option (a)) —
    # state was the last gpt-4o *content* seat, so this flip completes the
    # keyless-OpenAI migration: a keyless-OpenAI generate now runs end-to-end on
    # the anthropic lanes (the only remaining gpt-4o seat, synthesis, has no live
    # call site in the generate path). STATE_MODEL + the STATE_USD_* price
    # constants derive from this row (memory_core R-B4a), so model/price/transport
    # follow with no other module edit. timeout_sub_s 300 -> 600 -> 60: the 600
    # was a stopgap for the taxed present and carried a revisit-DOWNWARD-on-any-
    # thinking-flip rider. That flip is THIS diff, so the rider fires — 60s is
    # 10x the measured off-arm ceiling (5.7-6.0s) and still outlasts a call that
    # trips the >1,200-token band (~18s at the throughput floor).
    # THE PRE-REGISTERED GATE, ARMED FOR THIS FLIP: the MANDATORY spot-check +
    # revert-if from DECISIONS 2026-07-17 ("state seat flips to Haiku/
    # subscription; audio held" — the durable record) now covers the THINKING
    # flip as well as the original model/lane flip. Any validate_state trip /
    # photocopy-suspect flag / quality miss on the next generates -> either drop
    # "state" from _THINKING_OFF_SUB_SEATS (the cheap, targeted revert — one
    # line, restores today's behavior exactly) or revert this row to
    # **_GPT4O_API in one clean diff (needs the OpenAI key restored), or
    # escalate to the battery's state arm. The thinking revert is the FIRST
    # rung now: it is smaller than the lane revert and it is the thing that
    # changed.
    "state":     SeatConfig("state",     timeout_s=60,  timeout_sub_s=120, **_OPUS_STATE_SUB),
}

# Seats DECLARED in the roster but with no live call site anywhere in the product
# (B6 builds synthesis's). The doctor treats a dormant seat's provider-key
# requirement as informational, never a FAIL — there is no run for the key to
# protect (gate ruling 2, 2026-07-17). B6 REMOVES synthesis from this set in the
# same diff that lands its call site, re-arming the hard key requirement.
DORMANT_SEATS = frozenset({"synthesis"})

# generate._chat is shared by the narrative/editor/script steps; in B1 all
# three are the identical gpt-4o/120s writer-family seat, so this map only
# keeps the ledger's seat label honest — it changes NO request. B4 splits the
# writer seat onto Opus and must thread the per-step seat through _chat's
# transport (marked at the call site); until then transport uses "writer".
_STEP_PREFIX_SEAT = (
    ("rank", "rank"),
    ("narrative", "writer"),
    ("editor", "editor"),
    ("script", "script"),
)


def seat_for_step(step: str) -> str:
    """The seat a generate step's ledger entry is labelled with. Every live step
    enumerates in _STEP_PREFIX_SEAT (rank / narrative* / editor* / script*).

    FIX-6 (B4): an unknown step RAISES — it no longer silently defaults to the
    writer seat. The default was behaviour-neutral when the writer was gpt-4o (B1)
    and identical to the other writer-family seats; post-B4 the writer is Opus 4.8
    (the PRICIEST seat), so a silent default would bill Opus AND mislabel the
    ledger under a typo'd/new step. Add a _STEP_PREFIX_SEAT row instead."""
    for prefix, seat in _STEP_PREFIX_SEAT:
        if step.startswith(prefix):
            return seat
    raise ValueError(
        f"seat_for_step: unknown step {step!r} — no seat prefix matches. Known "
        f"prefixes: {', '.join(p for p, _ in _STEP_PREFIX_SEAT)}. Add a "
        "_STEP_PREFIX_SEAT row (a silent default would bill the Opus writer seat "
        "and mislabel the ledger)."
    )


# ---------------------------------------------------------------------------
# Lane interface: request -> response + token counts + cost attribution.
# A provider is Callable[[LaneRequest], LaneResponse]. It owns its own
# transport AND its own credential/env acquisition (so the B3 subscription
# provider can strip ANTHROPIC_API_KEY from its subprocess env).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Usage:
    """Normalised token counts across providers. `cache_read_tokens` is
    RECORDED so the ~0.1x cache-read assumption is measured (B2), but B1 does
    not discount usd_shadow for it — see cost_fields()."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True)
class LaneRequest:
    cfg: SeatConfig
    prompt: str
    temperature: float
    max_tokens: int
    json_mode: bool
    user_agent: str
    # The credential. A provider decides whether/how to use it; the B3
    # subscription lane ignores it and strips its subprocess env (Rook #1).
    api_key: str
    # Transport-endpoint override (openai-lane). Primarily the OFFLINE-TEST
    # seam: the suite redirects the real transport at a loopback fake server
    # by monkeypatching ranking.OPENAI_CHAT_URL, so each caller passes the
    # endpoint name it historically used. None => the provider's default
    # (OPENAI_CHAT_URL). B2's anthropic lane reads its own endpoint.
    url: Optional[str] = None
    # B4 prompt caching: the STABLE prefix (the seat's law/instructions, byte-
    # stable within an edition run), split OUT of the volatile per-call `prompt`
    # by the caller. The anthropic api provider emits it as a `system` block
    # marked cache_control:{type:"ephemeral"} so a reuse within the 5-minute TTL
    # (the analyst's static brief instructions across an edition's slots; a
    # writer/analyst corrected retry; a same-day idempotent re-run) is served at
    # ~0.1x. None (any call whose caller passes no stable prefix — the mechanical
    # json seats never do) => the request bytes are byte-unchanged from B2/B1.
    # The subscription lane has NO cache_control surface, so its
    # provider folds this prefix inline (never dropped); documented per dispatch.
    system: Optional[str] = None


@dataclass(frozen=True)
class LaneResponse:
    content: str
    usage: Usage
    finish_reason: Optional[str]
    # The provider-native response. B1 callers read `.raw` (OpenAI shape) and
    # do their own strict parsing exactly as before B1; B2 decides whether an
    # anthropic provider synthesises OpenAI-shaped `.raw` or migrates callers
    # onto the normalised `.content`/`.usage`/`.finish_reason` fields.
    raw: Dict


Provider = Callable[["LaneRequest"], "LaneResponse"]


class LaneUnavailable(RuntimeError):
    """A seat resolved to a lane/provider with no registered implementation.
    Fail-loud by ruling — the message names the exact fix."""


class SubscriptionAuthError(RuntimeError):
    """The `claude -p` CLI reported an AUTH-class failure: the logged-in session
    is expired, absent, or rejected (NL-160, from the 2026-08-24 outage).

    A RuntimeError SUBCLASS on purpose. Every existing caller catches this lane's
    failures as transport-shaped (`except Exception`, or RuntimeError) and must go
    on catching this one unchanged — the subclass adds no new escape, it adds a
    QUESTION the callers' retry loops can now ask. Because this is the one
    transport failure a retry can never fix: re-sending the same bytes to the same
    un-authed CLI fails identically, one backoff later. Measured on the record —
    five interactive generates on 2026-08-24, each burning ~35-41s on two doomed
    attempts, every one of them already lost at attempt 1."""


# The auth-class markers, matched case-insensitively against the CLI's own failure
# text. Deliberately SHORT and specific: this predicate decides whether a failure
# is retried at all, so a loose marker ("error", "failed") would quietly disarm
# the one-retry law for the whole transport class — the expensive direction of the
# drift. "Failed to authenticate: OAuth session expired" is the string measured
# live on 2026-08-24; the rest are the CLI's neighbouring auth phrasings.
_AUTH_FAILURE_MARKERS: Tuple[str, ...] = (
    "failed to authenticate",
    "oauth session expired",
    "oauth token expired",
    "session expired",
    "not logged in",
    "please log in",
    "claude login",
    "authentication_error",
    "invalid api key",
    "unauthorized",
)

# The fix, in the failure's own message. An auth failure is the one lane error the
# principal can clear himself in ten seconds, and the message is where he will be
# standing when he needs to know that.
AUTH_FIX_HINT = (
    "the logged-in `claude` session is expired or absent, so a retry cannot help "
    "— run `claude` once interactively to log in (SETUP.md), or flip the seat to "
    "its api fall-over (NEWSLENS_LANE_<SEAT>=api, needs ANTHROPIC_API_KEY)"
)


# WHERE a failure detail was read from — and the source is not decoration, it
# decides whether that text is allowed to answer the auth question at all
# (NL-160 gate R-5). The envelope and stderr are the CLI's own diagnostic
# channels: text there is the CLI SPEAKING ABOUT ITSELF. Raw stdout is the
# fallback for a CLI that produced neither, so it is where a TRUNCATED envelope's
# model PROSE lands — and prose about, say, unauthorized access to a network is
# ordinary vocabulary for a news product. Classifying that as an auth failure
# would take away the retry the one-retry law owes a genuinely transient crash:
# the expensive direction of this rule's drift, and the reason the classifier's
# input is narrowed rather than its marker list.
DETAIL_FROM_ENVELOPE = "envelope"
DETAIL_FROM_STDERR = "stderr"
DETAIL_FROM_STDOUT = "stdout"
DETAIL_FROM_NOTHING = "none"

# The only two sources the classifier may read. The other two stay MESSAGE-ONLY:
# they are still printed verbatim (an honest detail beats a blank — that is the
# whole of defect ①), they simply never decide whether a retry happens.
_CLASSIFIABLE_DETAIL_SOURCES: Tuple[str, ...] = (DETAIL_FROM_ENVELOPE,
                                                 DETAIL_FROM_STDERR)


def is_auth_failure_text(text: str) -> bool:
    """Does this CLI failure text name an AUTH-class failure?

    ONE implementation, two readers (the single-validator rule): the transport
    below reaches its raise through this table, and the doctor's live probe
    reaches its verdict through the same one. Two copies of it would drift, and
    the drift that matters is a doctor reporting "logged in" about a machine
    whose pipeline cannot authenticate — which is exactly the 2026-08-24 failure
    (the doctor ran green through the whole outage).

    TEXT ONLY — this predicate does not know where the text came from, so it is
    not the WHOLE rule either reader acts on: both call `is_auth_failure_detail`,
    which adds the source restriction above. Kept public and separate because the
    marker table is the half worth testing and mutating on its own."""
    low = (text or "").lower()
    return any(marker in low for marker in _AUTH_FAILURE_MARKERS)


def is_auth_failure_detail(detail: str, source: str) -> bool:
    """The auth question as the transport and the doctor are ALLOWED to ask it:
    the marker table, but only against text the CLI itself wrote (NL-160 gate
    R-5).

    The accepted residual, stated so a future reader does not "fix" it: a CLI
    that reported an auth rejection ONLY as bare non-JSON stdout would fall
    through to one wasted retry — the pre-NL-160 status quo, and the detail it
    prints is still honest. That is the cheap direction. The direction this
    guard closes is the expensive one."""
    return (source in _CLASSIFIABLE_DETAIL_SOURCES
            and is_auth_failure_text(detail))


def subscription_failure_parts(stdout: str, stderr: str,
                               limit: int = 200) -> Tuple[str, str]:
    """(detail, source) — THE ONE WALKER over a failed `claude -p`'s output.

    `claude -p --output-format json` reports its OWN failures on STDOUT, inside
    the result envelope ({"type":"result","is_error":true,"result":"Failed to
    authenticate: ..."}), and leaves stderr EMPTY. So interpolating stderr alone —
    which is what this lane did until NL-160 — prints a BLANK for the
    production-likeliest failure there is. On the record five times over
    (generation_log.jsonl, 2026-08-24): "claude -p (rank) exited 1:  — no briefing
    row was written", the diagnosis sitting unread in the envelope the whole time.

    Order: the envelope's own text, then stderr, then raw stdout, then an explicit
    statement that there was nothing to read. Never a blank — a failure message
    that says nothing is the defect this function exists to close.

    SOURCE RIDES WITH THE TEXT, and one function returns both, because the
    alternative is a second envelope parser somewhere else asking "was that the
    envelope?" — and two walkers over the same bytes is how a classifier and a
    message start disagreeing about what the CLI said."""
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        for field in ("result", "error", "message"):
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()[:limit], DETAIL_FROM_ENVELOPE
            # {"error": {"message": ...}} — the API-shaped nesting.
            if isinstance(value, dict):
                inner = value.get("message")
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()[:limit], DETAIL_FROM_ENVELOPE
    for raw, source in (((stderr or "").strip(), DETAIL_FROM_STDERR),
                        ((stdout or "").strip(), DETAIL_FROM_STDOUT)):
        if raw:
            return raw[:limit], source
    return "no output on stdout or stderr", DETAIL_FROM_NOTHING


def subscription_failure_detail(stdout: str, stderr: str,
                                limit: int = 200) -> str:
    """The detail alone — `subscription_failure_parts` without the source.

    A one-line delegation, NOT a second reader: every caller that only prints
    the text keeps its old signature, and no second copy of the walk exists to
    drift from the first."""
    return subscription_failure_parts(stdout, stderr, limit)[0]


# ---------------------------------------------------------------------------
# OpenAI provider (the only lane registered in B1)
# ---------------------------------------------------------------------------

def _openai_content(raw: Dict) -> str:
    """LENIENT extraction — never raises on a malformed response. The strict
    parse that triggers a caller's retry still happens in the caller on
    `.raw`, so today's exact error behaviour is preserved."""
    try:
        return raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""


def _openai_finish_reason(raw: Dict) -> Optional[str]:
    try:
        return raw["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return None


def _openai_usage(raw: Dict) -> Usage:
    u = raw.get("usage") or {}
    details = u.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0
    return Usage(
        prompt_tokens=u.get("prompt_tokens") or 0,
        completion_tokens=u.get("completion_tokens") or 0,
        cache_read_tokens=cached or 0,
    )


def _openai_provider(req: LaneRequest) -> LaneResponse:
    """The historical OpenAI transport, moved verbatim in shape: a raw urllib
    POST to chat/completions. Returns the native OpenAI response as `.raw` so
    the three B1 callers parse it exactly as before. `response_format` is
    included only in json_mode (the writer path omitted it; rank/analyst
    always set it) — matching each caller's request byte-for-byte.

    urllib is referenced through the `urllib.request` module (not a bound
    import) so the suite's `monkeypatch.setattr(urllib.request, "urlopen", …)`
    interception still covers this path.
    """
    cfg = req.cfg
    body = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": req.prompt}],
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
    }
    if req.json_mode:
        body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        req.url or OPENAI_CHAT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {req.api_key}",
            "Content-Type": "application/json",
            "User-Agent": req.user_agent,
        },
    )
    with urllib.request.urlopen(request, timeout=cfg.timeout_s) as resp:
        raw = json.load(resp)
    return LaneResponse(
        content=_openai_content(raw),
        usage=_openai_usage(raw),
        finish_reason=_openai_finish_reason(raw),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Anthropic (Claude API) provider — B2's api lane. Raw urllib, zero SDK dep.
# ---------------------------------------------------------------------------

# The system nudge for a json_mode seat (rank). The Messages API has no native
# json_object mode, so — per the dispatch/ADR-0014 ruling — the Claude lane
# satisfies the SAME parse-and-validate contract the caller already enforces by
# steering the model toward bare JSON and letting the caller's existing
# validation/retry law be the backstop (a fenced or preambled reply fails
# json.loads and takes the corrected retry, exactly as a malformed GPT-4o reply
# would). Documented choice: prompt-shaped JSON, no silent post-hoc repair.
_ANTHROPIC_JSON_SYSTEM = (
    "Output only the single JSON object the instructions describe. Emit no prose "
    "before or after it, and no markdown code fences."
)

# stop_reason -> the OpenAI finish_reason the callers branch on. "max_tokens" ->
# "length" is load-bearing: every caller raises its truncation error on
# finish_reason == "length", so the cap-hit path must map to it exactly.
_STOP_REASON_MAP = {
    "end_turn": "stop", "stop_sequence": "stop",
    "max_tokens": "length", "tool_use": "tool_calls", "refusal": "content_filter",
}

# NL-93: SSE streaming for the api lane's LONG-output calls. The non-streaming
# /v1/messages POST held one blocking socket read for the whole generation, so
# the xhigh writer (adaptive thinking + 16k max_tokens) sat minutes with zero
# response bytes and the server closed the idle connection -> RemoteDisconnected
# (reproduced 3/3 arms x retries, sandboxed AND unsandboxed, 2026-07-24; short
# calls fine). Streaming ("stream": true) keeps the connection fed with SSE text
# deltas + periodic pings so it never idle-dies, and — because each readline is
# its own socket op — it turns cfg.timeout_s from a TOTAL wall-time cap into a
# per-read INTER-EVENT idle bound (see _anthropic_provider).
#
# TRIGGER = the per-call max_tokens BUDGET (not the seat's thinking flag).
# Thinking tokens BILL AS OUTPUT and count against max_tokens (SEATS notes), so
# the token budget bounds the whole call's wall time: a large-budget call (the
# writer 16k, the analyst 6k) can run minutes and must stream; a small-budget
# call — including a thinking seat asked for only a few hundred tokens — finishes
# in a beat and is safe (and proven) on the blocking json.load path. Keying on
# the budget (not `cfg.thinking`) is therefore both the failure-aligned predictor
# AND what keeps every cheap short call byte-identical.
#
# The bar sits BELOW the analyst's real budget (ANALYSIS_MAX_TOKENS=6000) and
# ABOVE the editor's (EDITOR_MAX_TOKENS=4600) — llm is a leaf (no import of
# generate/analysis, by design), so the coupling is documented here, not
# referenced. RESIDUAL: trimming ANALYSIS_MAX_TOKENS below this bar would
# silently drop the analyst off streaming — keep the bar under the analyst budget.
_STREAM_MIN_MAX_TOKENS = 5000


def _should_stream(cfg: SeatConfig, max_tokens: int) -> bool:
    """Stream the LONG-call class only (NL-93 constraint 4): a per-call max_tokens
    budget at/above _STREAM_MIN_MAX_TOKENS. Real writer (16000), analyst (6000),
    and — since ENG-M0 — rank (36,000) calls stream on the api lane (rank's pins
    moved with the SSE accept); every current short/medium api call (editor,
    script, state, follow_altitude, and any cheap small-budget probe) stays
    below the bar, so its request bytes (no `stream` key) and its blocking
    json.load transport are UNCHANGED — its pinned tests do not move, and the
    latency-sensitive interactive follow_altitude path keeps its exact
    non-streaming semantics. `cfg` is accepted for forward flexibility (a future
    seat-specific rule) but the budget alone decides today."""
    return max_tokens >= _STREAM_MIN_MAX_TOKENS


def _anthropic_credential() -> str:
    """The Claude API lane OWNS its own credential (ADR-0014 §4): it reads
    ANTHROPIC_API_KEY from the environment itself rather than the OpenAI key the
    historical callers still pass as LaneRequest.api_key. Never echoed, logged,
    or returned anywhere but the x-api-key header."""
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def _anthropic_content(raw: Dict) -> str:
    """LENIENT text extraction from the content-block array — never raises on a
    malformed response (the caller's strict json.loads/validate on the
    synthesised .raw is what triggers a retry, so today's error behaviour is
    preserved). Concatenates every text block."""
    try:
        return "".join(
            b.get("text", "")
            for b in (raw.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
    except (AttributeError, TypeError):
        return ""


def _anthropic_finish_reason(raw: Dict) -> Optional[str]:
    sr = raw.get("stop_reason") if isinstance(raw, dict) else None
    return _STOP_REASON_MAP.get(sr, sr)


def _anthropic_usage(raw: Dict) -> Usage:
    """input_tokens/output_tokens -> prompt/completion; both cache fields
    recorded (B4's caching reads cache_creation/cache_read from the ledger)."""
    u = (raw.get("usage") or {}) if isinstance(raw, dict) else {}
    return Usage(
        prompt_tokens=u.get("input_tokens") or 0,
        completion_tokens=u.get("output_tokens") or 0,
        cache_read_tokens=u.get("cache_read_input_tokens") or 0,
        cache_creation_tokens=u.get("cache_creation_input_tokens") or 0,
    )


def _openai_shaped(usage: Usage, content: str, finish: Optional[str],
                   native: Dict) -> Dict:
    """Synthesise the OpenAI response shape the three historical callers parse
    (`.raw["choices"][0]["message"]["content"]`, `.raw["usage"]`), so the Claude
    lane is a drop-in for the gpt-4o seats without rewriting each caller's strict
    parse/validate/retry law (ADR-0014 §2 left this to B2: synthesise, don't
    migrate callers). cost_fields reads prompt_tokens/completion_tokens +
    prompt_tokens_details.cached_tokens + the additive cache_creation_tokens key
    off this dict, so the shadow ledger is lane-agnostic. The native anthropic
    response rides under `_anthropic` for forensics."""
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "prompt_tokens_details": {"cached_tokens": usage.cache_read_tokens},
            "cache_creation_tokens": usage.cache_creation_tokens,
        },
        "_anthropic": native,
    }


def _iter_sse_events(resp):
    """Yield each parsed SSE event dict from a streaming /v1/messages response
    (NL-93). Line-delimited SSE: `event:`/`data:` frames separated by a blank
    line; dispatch is off the data JSON's own `type` field (robust to which line
    named the event).

    FAIL-LOUD, exact exception classes (NL-93 constraint 3). A socket read error
    mid-stream (timeout / connection reset / RemoteDisconnected) propagates
    UNCHANGED from resp.readline — it is a transport error and the callers
    classify it exactly as today (their `except Exception` transport arm, retry
    the original bytes). A malformed `data:` JSON frame is re-raised as
    ConnectionError — NEVER a bare json.JSONDecodeError: JSONDecodeError IS a
    ValueError, and the callers route the ValueError family to the CORRECTED-
    RETRY arm, so a stream corruption surfacing as ValueError would be misrouted.
    ConnectionError (an OSError subclass, the RemoteDisconnected family) lands in
    the transport arm, matching the class the non-streaming path already raised."""
    data_lines = []
    while True:
        raw = resp.readline()              # transport errors propagate untouched
        if not raw:                        # EOF (b"") — no terminator left
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":                     # blank line == event boundary
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    yield json.loads(payload)
                except ValueError as exc:  # json.JSONDecodeError <: ValueError
                    raise ConnectionError(
                        f"malformed SSE data frame from /v1/messages: {exc}"
                    ) from exc
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip(" "))
        # event: / id: / retry: / ':' comment lines carry no payload -> ignored


def _accumulate_sse(resp) -> Dict:
    """Consume the SSE event stream and rebuild the SAME native /v1/messages dict
    the non-streaming path returns from json.load(resp) — so _anthropic_content,
    _anthropic_usage and _anthropic_finish_reason (and the whole downstream
    validation + cost + retry surface) run UNCHANGED (NL-93 constraint 2: cost
    must not move by a token, stop_reason 'max_tokens' must still map to
    'length').

    Reconstruction from the event grammar:
      * message_start.message seeds id/model/role and the BASE usage
        (input_tokens + cache_read/creation; output_tokens is the small initial
        count, overwritten below).
      * content_block_start/delta accumulate per index into text/thinking blocks.
        Only text blocks become `content` text (thinking blocks are preserved in
        the array but _anthropic_content already skips them, as it does for the
        non-streaming thinking blocks — no thinking token leaks into content).
      * message_delta carries the FINAL cumulative usage.output_tokens and the
        terminal stop_reason.
      * message_stop terminates the stream.

    NO SILENT PARTIALS (constraint 3): a stream that ends (EOF) without a
    message_stop, or an in-band `error` event, raises ConnectionError (a
    transport class) — never a truncated-but-returned text."""
    message = None
    blocks: Dict[int, Dict] = {}
    stop_reason = None
    final_usage = None
    saw_stop = False
    for evt in _iter_sse_events(resp):
        # F1 (gate): the per-event dispatch is wrapped so a malformed frame that
        # trips a TypeError (a delta whose "text" is a non-string, a block whose
        # "index" is an unorderable type, …) surfaces as a TRANSPORT class — never
        # a bare TypeError, which is in the callers' corrected-retry tuple and
        # would misroute a stream corruption to the model-correction arm. The
        # readline/iterator itself is NOT wrapped (it stays outside this try), so
        # a socket.timeout / RemoteDisconnected mid-stream still propagates its own
        # transport class unchanged (the propagation pins hold).
        try:
            etype = evt.get("type") if isinstance(evt, dict) else None
            if etype == "message_start":
                message = evt.get("message") or {}
            elif etype == "content_block_start":
                idx = evt.get("index", 0)
                cb = evt.get("content_block") or {}
                blk = blocks.setdefault(idx, {"type": None, "text": "", "thinking": ""})
                blk["type"] = cb.get("type") or blk["type"]
                if cb.get("type") == "text":
                    blk["text"] += cb.get("text", "") or ""
                elif cb.get("type") == "thinking":
                    blk["thinking"] += cb.get("thinking", "") or ""
            elif etype == "content_block_delta":
                idx = evt.get("index", 0)
                d = evt.get("delta") or {}
                blk = blocks.setdefault(idx, {"type": None, "text": "", "thinking": ""})
                dt = d.get("type") if isinstance(d, dict) else None
                if dt == "text_delta":
                    blk["text"] += d.get("text", "") or ""
                    blk["type"] = blk["type"] or "text"
                elif dt == "thinking_delta":
                    blk["thinking"] += d.get("thinking", "") or ""
                    blk["type"] = blk["type"] or "thinking"
                # signature_delta / input_json_delta carry no assistant text -> ignored
            elif etype == "message_delta":
                d = evt.get("delta") or {}
                if isinstance(d, dict) and d.get("stop_reason") is not None:
                    stop_reason = d.get("stop_reason")
                u = evt.get("usage")
                if isinstance(u, dict):
                    final_usage = u            # cumulative final output_tokens
            elif etype == "message_stop":
                saw_stop = True
                break
            elif etype == "error":
                err = (evt.get("error") or {}) if isinstance(evt, dict) else {}
                raise ConnectionError(
                    "Anthropic streamed an error event "
                    f"({err.get('type')}: {err.get('message')})"
                )
            # ping / content_block_stop / unknown -> ignored
        except TypeError as exc:
            raise ConnectionError(
                f"malformed SSE event (bad field type): {exc}"
            ) from exc
    if not saw_stop:
        raise ConnectionError(
            "Anthropic SSE stream ended without a message_stop event — "
            "incomplete response (transport failure, no partial returned)"
        )
    # F1 (gate): the reconstruction is wrapped for the SAME reason — e.g.
    # sorted(blocks) over mixed-type indices is a TypeError that must be a
    # transport class, not a corrected-retry misroute.
    try:
        native: Dict = dict(message) if isinstance(message, dict) else {}
        content = []
        for idx in sorted(blocks):
            blk = blocks[idx]
            if blk.get("type") == "text":
                content.append({"type": "text", "text": blk.get("text", "")})
            elif blk.get("type") == "thinking":
                content.append({"type": "thinking", "thinking": blk.get("thinking", "")})
        native["content"] = content
        if stop_reason is not None:
            native["stop_reason"] = stop_reason
        usage = dict(native.get("usage") or {})
        if isinstance(final_usage, dict):
            usage.update(final_usage)      # output_tokens (+ any keys the delta reports)
        native["usage"] = usage
        return native
    except TypeError as exc:
        raise ConnectionError(
            "malformed SSE stream (reconstruction failed on a bad field type): "
            f"{exc}"
        ) from exc


def _anthropic_provider(req: LaneRequest) -> LaneResponse:
    """Claude API lane transport: a raw urllib POST to /v1/messages. The lane
    reads its OWN endpoint (ANTHROPIC_MESSAGES_URL) and IGNORES req.url — the
    callers pass the openai offline-test seam url, which does not apply here.
    Headers: x-api-key (the lane's own credential) + anthropic-version +
    content-type. max_tokens is REQUIRED by the Messages API. thinking/effort
    are sent only when the seat sets them (the mechanical seats leave both None —
    today `follow_altitude`, and `synthesis` on the openai side).

    NL-93: the LONG-call class (writer/analyst — see _should_stream) POSTs with
    "stream": true and the SSE event deltas are accumulated by _accumulate_sse
    into the SAME native dict json.load would return, so everything below the
    read (content extraction, json_mode cleanup, usage, finish_reason, the
    synthesised .raw) is transport-agnostic. Short/medium seats keep the exact
    non-streaming json.load path.

    urllib is referenced through the `urllib.request` module (not a bound
    import) so the suite's `monkeypatch.setattr(urllib.request, "urlopen", …)`
    interception covers this path exactly as it covers the openai provider."""
    cfg = req.cfg
    body = {
        "model": cfg.model,
        "max_tokens": req.max_tokens,          # REQUIRED by the Messages API
        "messages": [{"role": "user", "content": req.prompt}],
    }
    # B4: the Claude 4.6+ family (Opus 4.8, Sonnet 5) REJECTS temperature with a
    # 400 — omit it when the seat says so. A sampling=True seat keeps
    # `temperature` where it was (right after `messages`) so its pinned request
    # bytes do not move; after NL-147 that is the openai side only (`synthesis`),
    # every anthropic row having set sampling=False.
    if cfg.sampling:
        body["temperature"] = req.temperature
    # B4 prompt caching + json nudge. `system` is a list when a cacheable prefix
    # is present (cache_control:{ephemeral} on the big stable block, the json
    # nudge appended after it as its own volatile-free block); a plain STRING for
    # a json_mode seat with no prefix (`rank`'s shape — byte-unchanged from B2,
    # where that seat happened to be Haiku). Render
    # order is tools -> system -> messages, so the cache breakpoint on the system
    # block covers everything up to the volatile user `prompt`.
    if req.system:
        blocks = [{"type": "text", "text": req.system,
                   "cache_control": {"type": "ephemeral"}}]
        if req.json_mode:
            blocks.append({"type": "text", "text": _ANTHROPIC_JSON_SYSTEM})
        body["system"] = blocks
    elif req.json_mode:
        body["system"] = _ANTHROPIC_JSON_SYSTEM
    if cfg.thinking:                            # None on the mechanical seats -> omitted
        body["thinking"] = {"type": cfg.thinking}
    if cfg.effort:                             # None on the mechanical seats -> omitted
        body["output_config"] = {"effort": cfg.effort}
    # NL-93: stream the LONG-call class (writer/analyst; see _should_stream) so a
    # minutes-long generation never idle-dies (RemoteDisconnected). `stream` is
    # added LAST and only on this path, so the non-streaming request bytes (the
    # short seats) are byte-unchanged and their pinned body tests do not move.
    stream = _should_stream(cfg, req.max_tokens)
    if stream:
        body["stream"] = True
    request = urllib.request.Request(
        ANTHROPIC_MESSAGES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": _anthropic_credential(),
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            "User-Agent": req.user_agent,
        },
    )
    # TIMEOUT SEMANTICS (NL-93 constraint 5). urlopen's `timeout` is the socket
    # timeout; on the NON-streaming path it bounds the one blocking read (total
    # wall time, as today). On the STREAMING path each _accumulate_sse readline
    # is its own socket op, so the SAME cfg.timeout_s becomes the connect +
    # per-read INTER-EVENT IDLE bound — a healthy long stream that keeps emitting
    # deltas/pings is never killed by a total-wall cap (that was the failure),
    # only a genuine stall past timeout_s raises socket.timeout (transport-shaped,
    # retried like any 5xx). timeout_sub_s is subscription-lane and out of scope.
    with urllib.request.urlopen(request, timeout=cfg.timeout_s) as resp:
        native = _accumulate_sse(resp) if stream else json.load(resp)
    content = _anthropic_content(native)
    # DEF-A′ (2026-07-17, field-charged): apply the SAME json_mode extraction on
    # the api lane. First scoped subscription-only on the theory the api lane's
    # corrected-retry recovers fenced JSON — the field refuted it: rank on the api
    # lane (NEWSLENS_LANE_RANK=api) FAILED char-0 on BOTH attempts against the real
    # 17,446-token prompt (ranking_runs 36; both attempts fenced/preambled, neither
    # truncated — completion 2549/2480 < the 3000 cap; $0.0602 charged for
    # nothing). Real Haiku fences regardless of lane; the B2 pins only proved a
    # SINGLE synthetic reply recovers. Extraction-FIRST as presentation cleanup;
    # the corrected retry stays the second line for genuinely malformed shapes
    # (see _extract_json_result — never a repair, so validation is unchanged and a
    # no-object / invalid reply still fails through to the retry).
    if req.json_mode:
        content = _extract_json_result(content)
    usage = _anthropic_usage(native)
    finish = _anthropic_finish_reason(native)
    return LaneResponse(
        content=content, usage=usage, finish_reason=finish,
        raw=_openai_shaped(usage, content, finish, native),
    )


# ---------------------------------------------------------------------------
# Anthropic (Claude) SUBSCRIPTION lane — B3. A thin `claude -p` subprocess,
# NOT the Python Agent SDK (ADR-0014 §5.2: the 3.9 floor + zero-SDK posture).
# Rook's four red conditions are the milestone contract and are enforced HERE:
#   (1) the child env STRIPS ANTHROPIC_API_KEY (else the CLI prefers the key and
#       silently bills the API while the ledger says $0-subscription — D1 class);
#   (2) ALL tools disabled + the injection surface (CLAUDE.md/skills/plugins/
#       hooks/MCP/agents) off, cwd = a fresh empty scratch dir — the prompt is
#       built from untrusted fetched news text;
#   (3) fail-loud availability (a missing/unauthed binary is LaneUnavailable at
#       the gate, never a silent wrong-lane call) — see check_lane;
#   (4) usd_charged == 0.0 (subscription), usd_shadow always API-priced, caps
#       bind on shadow — see cost_fields (unchanged; lane-driven).
# Flags pinned READ-ONLY against the installed CLI's --help (v2.1.212):
#   -p --output-format json     headless single JSON result (ADR-0014 spike #5)
#   --model <model>             seat model
#   --tools ""                  "" disables ALL built-in tools (Rook #2)
#   --safe-mode                 no CLAUDE.md/skills/plugins/hooks/MCP/agents
#   --strict-mcp-config         + no MCP servers (none are passed)
#   --no-session-persistence    hermetic: no session files written to disk
# ---------------------------------------------------------------------------

# The known install location on the principal's machine (dispatch B2): the CLI
# is NOT on the non-login-shell PATH, so resolution falls back to this default
# after the NEWSLENS_CLAUDE_BIN override and PATH.
CLAUDE_BIN_DEFAULT = os.path.expanduser("~/.local/bin/claude")

# The base argv (everything but --model/--effort/--append-system-prompt). A
# tuple so it is never mutated in place.
_SUBSCRIPTION_BASE_FLAGS: Tuple[str, ...] = (
    "-p", "--output-format", "json",
    "--tools", "",                 # "" == disable ALL built-in tools (Rook #2)
    "--safe-mode",                 # no CLAUDE.md/skills/plugins/hooks/MCP/agents
    "--strict-mcp-config",         # + no MCP servers (none passed on argv)
    "--no-session-persistence",    # hermetic: nothing written outside the sandbox
)

# The ONLY env vars the child inherits — an ALLOWLIST (Rook: allowlist, not
# blocklist). ANTHROPIC_API_KEY is deliberately ABSENT and popped defensively.
# HOME lets the CLI find its own subscription auth (~/.claude / keychain); the
# locale/PATH vars keep it well-behaved. No NEWSLENS_*, no OPENAI_API_KEY, no
# proxy vars ride into the child.
_SUBSCRIPTION_ENV_ALLOW: Tuple[str, ...] = (
    "HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR",
)


def resolve_claude_bin(env: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], str]:
    """Resolve the `claude` CLI for the subscription lane. Precedence:
    NEWSLENS_CLAUDE_BIN (explicit override) -> PATH (shutil.which) -> the known
    default (~/.local/bin/claude). Returns (path, source) with source in
    {"env","path","default"} on success, or (None, reason) if nothing resolves
    to an executable file. Pure filesystem resolution — NO spawn.

    An explicit NEWSLENS_CLAUDE_BIN that is NOT an executable file fails loud
    (returns None) rather than silently falling through to PATH — the operator
    pointed at a specific binary, and a wrong path must be named, not skipped.
    (This is also what keeps the test suite from ever reaching the real binary:
    the conftest points NEWSLENS_CLAUDE_BIN at a canned-success STUB shim that
    exists. NL-155 truth-edit: this said "a non-existent sentinel" until
    2026-08-14, describing an alternative ADR-0015 considered and REJECTED —
    a sentinel reddened the ~680 assertions that only need check_lane to pass.
    See tests/conftest.py's SCRUBBED_ENV_VARS note and `_STUB_CLAUDE_SRC`.)

    THE ENV IS THE WHOLE WORLD (NL-156, 2026-08-14). When a caller hands in an
    env, every leg reads THAT mapping — including PATH, which defaults to ""
    (search nothing) rather than to None. `shutil.which(path=None)` silently
    consults os.environ["PATH"], so a hand-built env without a PATH key used to
    resolve against the CALLING PROCESS's PATH — on a developer machine with the
    real `claude` installed that is a resolution the caller never asked for and
    cannot see. The default leg (CLAUDE_BIN_DEFAULT) is a fixed machine path and
    is deliberately NOT env-derived; the suite kills it structurally in conftest
    so a partial env cannot reach the real binary through it either."""
    env = os.environ if env is None else env
    override = (env.get("NEWSLENS_CLAUDE_BIN") or "").strip()
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override, "env"
        return None, (f"NEWSLENS_CLAUDE_BIN={override!r} is not an executable "
                      "file — fix the path or unset it to fall back to PATH")
    found = shutil.which("claude", path=env.get("PATH", ""))
    if found:
        return found, "path"
    if os.path.isfile(CLAUDE_BIN_DEFAULT) and os.access(CLAUDE_BIN_DEFAULT, os.X_OK):
        return CLAUDE_BIN_DEFAULT, "default"
    return None, (
        "the `claude` CLI could not be found — install it, then set "
        "NEWSLENS_CLAUDE_BIN, add it to PATH, or place it at "
        f"{CLAUDE_BIN_DEFAULT}"
    )


# ---------------------------------------------------------------------------
# THE THINKING SEAM (eng-4 §6 C1; flips authorized by the principal 2026-07-26)
# ---------------------------------------------------------------------------
# THE BUG THIS CLOSES: the api transport honors `SeatConfig.thinking` (it sends
# the `thinking` param only when the seat declares one). The subscription
# transport never did — so every seat declaring `thinking=None` was silently
# paying for extended thinking it explicitly did not ask for. Measured, n=16 on
# real production prompts (debates/2026-07-26--newslens--engineering.md §3):
# 84-97% of these seats' output tokens were deliberation, and the ANSWER was
# the same size in both arms. state spent ~14,000 output tokens to write five
# sentences.
#
# THE MECHANISM: `MAX_THINKING_TOKENS=0` in the child env. It is INJECTED BY
# THIS CODE, never passed through from the parent — deliberately absent from
# _SUBSCRIPTION_ENV_ALLOW, so the principal's own shell cannot silently change
# product behavior in either direction.
#
# THE ALLOWLIST is per-seat because the flip is a QUALITY decision per seat,
# not a transport cleanup: these seats emit content he reads and hears.
#   * state / script / editor — AUTHORIZED 2026-07-26 (DECISIONS "FLIPS
#     AUTHORIZED"), each with its quality gate ARMED not blocking: state's
#     pre-registered spot-check + revert-if (its seat row), script's
#     validate_script + script_structural_check + his ear test, editor's
#     A9-preservation instruments (hedge-ratio tripwire, tier/A7-label
#     immutability, before/after word discipline).
#   * rank — DELIBERATELY EXCLUDED. Rook's gate, adopted by the principal: its
#     off-arm first-attempt VALIDITY is unmeasured at usable n, a format miss
#     costs a whole extra call, and a second miss kills the generate. It stays
#     taxed until an n>=10-per-arm measurement clears it, and its 600s watchdog
#     stays with it.
#   * writer / analyst — never candidates. They DECLARE thinking="adaptive";
#     their deliberation was ordered and is doing work.
# ADA'S DISSENT, ON RECORD AND STILL UNRESOLVED (eng-4 §5.5): a per-seat
# allowlist is a temporary lie; the correct end state is cfg.thinking honored
# unconditionally on both lanes with no exception list. This set is scaffolding
# and should shrink to nothing, not grow a governance process.
# ENG-M0 (2026-08-06): state / script / editor LEFT this set in the same diff
# that flipped them Haiku -> Opus 4.8 with thinking="adaptive". The 07-26 flip
# was correct for a cheap seat doing mechanical work; the seat ruling changes the
# premise (these seats now do editorial and memory JUDGMENT), so declining their
# deliberation would be declining the thing they were promoted to do. Their walls
# were re-measured in the same diff — see the timeout block above SEATS — because
# leaving thinking-OFF-sized walls on thinking-ON seats is a guaranteed timeout,
# not a margin.
# ADA'S DISSENT IS NOW MOSTLY SATISFIED (eng-4 §5.5: "the correct end state is
# cfg.thinking honored unconditionally with no exception list"). The set is down
# to ONE member and it is the principal's own flagged exception, not scaffolding:
# follow_altitude is the 8s UI wall — a reader waits out that wall before the
# proven degrade, and the measured thinking-off resolve is 1.85-2.89s against a
# thinking-on 9.38-46.1s. The list should shrink to nothing only if that seat
# ever stops being a synchronous reader-facing path.
_THINKING_OFF_SUB_SEATS: FrozenSet[str] = frozenset({
    "follow_altitude",   # NL-99: the seat this mechanism was diagnosed on
})
_MAX_THINKING_TOKENS_VAR = "MAX_THINKING_TOKENS"


def _subscription_env(env: Dict[str, str],
                      cfg: Optional[SeatConfig] = None) -> Dict[str, str]:
    """The child process env — an allowlist with ANTHROPIC_API_KEY guaranteed
    absent (Rook #1). Defensive pop in case a future allowlist entry aliases it.

    When `cfg` names an armed seat, MAX_THINKING_TOKENS=0 is INJECTED (see the
    seam block above). cfg stays optional so existing callers and tests that
    only assert the allowlist keep working unchanged."""
    child = {k: env[k] for k in _SUBSCRIPTION_ENV_ALLOW if k in env}
    child.pop("ANTHROPIC_API_KEY", None)
    # Injection, not pass-through: the var is set from the seat table, and a
    # parent-env value can never reach the child (it is not in the allowlist,
    # and this assignment is unconditional for an armed seat).
    if cfg is not None and cfg.seat in _THINKING_OFF_SUB_SEATS:
        child[_MAX_THINKING_TOKENS_VAR] = "0"
    return child


def _subscription_usage(payload: Dict, prompt: str,
                        content: str) -> Tuple[Usage, bool]:
    """Normalise the CLI's usage block. Returns (Usage, estimated). If the CLI
    reported token counts we LEDGER them (input/output/cache_read); if it did
    NOT, we ESTIMATE from char length and LABEL the estimate (mandate: never
    fake precision — the shadow row carries usd_shadow_estimated=True)."""
    u = payload.get("usage")
    if isinstance(u, dict) and (u.get("input_tokens") or u.get("output_tokens")):
        return Usage(
            prompt_tokens=u.get("input_tokens") or 0,
            completion_tokens=u.get("output_tokens") or 0,
            cache_read_tokens=u.get("cache_read_input_tokens") or 0,
            cache_creation_tokens=u.get("cache_creation_input_tokens") or 0,
        ), False
    # ~3.5 chars/token, the same conservative ratio the cost estimators use.
    return Usage(prompt_tokens=int(len(prompt) / 3.5),
                 completion_tokens=int(len(content) / 3.5)), True


def _balanced_objects(s: str) -> "list":
    """Every top-level balanced {...} substring in `s`, in order (string-literal
    aware, so a brace inside a JSON string is not miscounted). A pure scan."""
    out = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    out.append(s[start:i + 1])
    return out


def _extract_json_result(text: str) -> str:
    """Pull the JSON object out of a possibly fenced / preambled / verbose result.

    FIELD DEFECT (2026-07-17 falsifier real run, DEF-A): `claude -p` runs the
    model inside the Claude Code agentic harness (a large built-in system prompt
    even under --safe-mode; ~4.2k cache_creation tokens/call), so on the
    SUBSCRIPTION lane it emits conversational prose ± a fenced ```json block, NOT
    reliably bare JSON — the `--append-system-prompt` nudge is swamped. Every
    first attempt failed json.loads at char 0 (a leading backtick/letter); the
    corrected retry recovered only 13/24.

    PRESENTATION cleanup ONLY (the hard constraint: extraction never weakens
    VALIDATION). This returns a SUBSTRING of the model's output — it never
    repairs invalid JSON, coerces values, or synthesises fields — so the caller's
    json.loads + shape validation are UNCHANGED: a result with no JSON object, or
    a fenced-but-shape-invalid one, still fails the caller exactly as before.
    Extraction also never OVERRULES the shape validator (gate ruling 3,
    2026-07-17): whole-result JSON that parses as a non-dict (an array or a
    scalar), bare or fenced, passes through intact for the validator to referee
    — silently picking one member of an array is a choice the validator used to
    make, and keeps making. Extraction only ever digs an object out of non-JSON
    prose or a fence. Applied only on json_mode requests; bare-JSON output is a
    no-op (so the api-lane fakes, which return bare JSON, never move)."""
    s = (text or "").strip()
    if s.startswith("{") and s.endswith("}"):
        return s                                    # already bare — the common case
    # Gate ruling 3: whole-result JSON that is NOT a dict passes through
    # untouched — pre-extraction it parsed fine and the SHAPE validator rejected
    # it; that outcome is the validator's to referee, not extraction's to dodge.
    try:
        if not isinstance(json.loads(s), dict):
            return s
    except ValueError:
        pass
    # unwrap a whole-string markdown fence: ```json\n ... \n```
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            body = s[nl + 1:]
            end = body.rfind("```")
            if end != -1:
                inner = body[:end].strip()
                if inner.startswith("{") and inner.endswith("}"):
                    return inner
                # Same non-dict guard for the fence body: a fenced array/scalar
                # returns the BODY (the validator must see the payload, not the
                # fence) — never a silently-chosen member.
                try:
                    if not isinstance(json.loads(inner), dict):
                        return inner
                except ValueError:
                    pass
                s = inner                           # scan the fence body below
    # otherwise scan for balanced {...} objects and prefer the LAST that parses to
    # a dict (the answer follows any reasoning); else the last balanced object;
    # else the original (a non-object result — the caller's json.loads rejects it).
    candidates = _balanced_objects(s)
    for cand in reversed(candidates):
        try:
            if isinstance(json.loads(cand), dict):
                return cand
        except ValueError:
            continue
    return candidates[-1] if candidates else s


def _subscription_provider(req: LaneRequest) -> LaneResponse:
    """Claude subscription lane: a `claude -p --output-format json` subprocess.
    The prompt rides on STDIN (immune to ARG_MAX at 24k-char material budgets);
    cwd is a fresh empty scratch dir removed after the call; the env is the
    stripped allowlist. is_error / non-zero exit / non-JSON stdout are
    transport-shaped (RuntimeError -> the caller retries the ORIGINAL bytes
    once, same law as a 5xx) — EXCEPT the auth class, which raises the
    SubscriptionAuthError subclass so the callers' retry loops can decline a
    second attempt that provably cannot succeed (NL-160); a timeout SIGKILLs the
    child (subprocess.run) and
    surfaces as TimeoutError (also transport-shaped). LaneRequest.api_key /
    .url (the openai offline-test seam) are IGNORED — this lane owns its own
    auth (the logged-in CLI) and never makes an HTTP call of its own."""
    cfg = req.cfg
    bin_path, source = resolve_claude_bin()
    if bin_path is None:
        # Belt-and-suspenders. Since NL-156 the gate resolves the binary from
        # the env it was HANDED (check_lane(cfg, env)) while this transport
        # resolves from os.environ, so the two agree only because load_env()
        # merges .env into os.environ at every entrypoint — a caller that hands
        # check_lane a mapping os.environ does not carry would preflight one
        # binary and call another (QA F-8, 2026-08-14). Under that coincidence
        # this fires on a between-gate-and-call disappearance; without it, on a
        # gate/transport disagreement. Either way it is a raise, never a
        # silent fall-through.
        raise LaneUnavailable(
            f"seat '{cfg.seat}' is on the claude -p subscription lane but "
            f"{source}"
        )
    args = [bin_path, *_SUBSCRIPTION_BASE_FLAGS, "--model", cfg.model]
    if cfg.effort:                              # None on the mechanical seats -> omitted
        args += ["--effort", cfg.effort]
    if req.json_mode:                           # the same JSON nudge the api lane uses
        args += ["--append-system-prompt", _ANTHROPIC_JSON_SYSTEM]
    # B4: the subscription lane has NO cache_control surface (dispatch). A
    # cacheable prefix (req.system) is not DROPPED here — it rides inline as the
    # system prompt, so a writer/analyst seat pinned to this lane (a gate/
    # principal lane-ruling choice) still sees its law. No cache benefit; the
    # ledger's usd_shadow is the same either way.
    if req.system:
        args += ["--append-system-prompt", req.system]
    scratch = tempfile.mkdtemp(prefix="newslens-claude-lane-")
    # Lane-aware timeout (2026-07-17 field fix): the subscription lane pays CLI
    # startup + agentic-harness overhead, so the api-calibrated timeout_s is too
    # tight (rank's live 90s double-timeout). Use the seat's subscription timeout
    # when set, else fall back to timeout_s. api-lane timeouts are untouched.
    timeout = cfg.timeout_sub_s or cfg.timeout_s
    try:
        proc = subprocess.run(
            args, input=req.prompt, cwd=scratch,
            env=_subscription_env(dict(os.environ), cfg),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run has already killed the child; surface transport-shaped.
        raise TimeoutError(
            f"claude -p ({cfg.seat}/{cfg.model}) exceeded {timeout}s "
            "— the child was killed"
        ) from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if proc.returncode != 0:
        # NL-160: read the ENVELOPE, not just stderr. The CLI puts its own
        # diagnosis on stdout and leaves stderr empty, so the old stderr-only
        # interpolation printed a blank on the failure class that actually fires.
        # The SOURCE rides along because only the CLI's own channels (envelope,
        # stderr) may decide the auth question — see is_auth_failure_detail.
        detail, detail_source = subscription_failure_parts(proc.stdout,
                                                           proc.stderr)
        message = f"claude -p ({cfg.seat}) exited {proc.returncode}: {detail}"
        if is_auth_failure_detail(detail, detail_source):
            raise SubscriptionAuthError(f"{message} — {AUTH_FIX_HINT}")
        raise RuntimeError(message)
    try:
        payload = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"claude -p ({cfg.seat}) returned non-JSON stdout "
            f"({proc.stdout[:120]!r})"
        ) from exc
    if not isinstance(payload, dict) or payload.get("is_error"):
        if isinstance(payload, dict):
            detail = str(payload.get("result"))[:200]
            detail_source = DETAIL_FROM_ENVELOPE
        else:
            # Valid JSON that is NOT the result envelope (a bare string, a list)
            # is not the CLI diagnosing itself — it is raw stdout that happened
            # to parse. Message-only, same rule as unparseable stdout.
            detail = str(payload)[:200]
            detail_source = DETAIL_FROM_STDOUT
        message = f"claude -p ({cfg.seat}) reported an error result: {detail}"
        # NL-160: the SAME classification as the non-zero-exit arm above. An
        # auth failure the CLI chooses to report with exit 0 + is_error=true is
        # the same un-retryable failure; which arm catches it is the CLI's
        # business, not a reason to retry one and not the other.
        if is_auth_failure_detail(detail, detail_source):
            raise SubscriptionAuthError(f"{message} — {AUTH_FIX_HINT}")
        raise RuntimeError(message)
    result = payload.get("result") or ""
    # DEF-A (2026-07-17): on json_mode requests, extract the JSON object from the
    # `claude -p` agentic-harness output (fenced / preambled / verbose prose) so
    # the caller parses clean JSON. Estimate usage from the FULL `result` (what
    # the model generated), not the extracted substring. Non-json_mode results
    # (prose seats) pass through untouched. Validation is unchanged (see
    # _extract_json_result: presentation cleanup only, never a JSON repair).
    content = _extract_json_result(result) if req.json_mode else result
    usage, estimated = _subscription_usage(payload, req.prompt, result)
    # Forensics: the CLI's own fields (total_cost_usd is the API-equivalent, kept
    # as a CROSS-CHECK only — usd_charged is 0.0 on this lane, set by cost_fields
    # off cfg.lane, never off this number). session_id aids log correlation.
    native = {
        "_claude_cli": {
            "session_id": payload.get("session_id"),
            "total_cost_usd": payload.get("total_cost_usd"),
            "subtype": payload.get("subtype"),
            "bin_source": source,
            "token_source": "estimated" if estimated else "reported",
        }
    }
    raw = _openai_shaped(usage, content, "stop", native)
    if estimated:
        # Label the shadow so a ledger reader never mistakes an estimated
        # subscription-lane shadow for a metered one (cost_fields propagates it).
        raw["usage"]["_token_source"] = "estimated"
    return LaneResponse(content=content, usage=usage, finish_reason="stop", raw=raw)


# Provider registry. Keyed by provider-lane so B2 registers "anthropic:api"
# and B3 registers "anthropic:subscription" without touching this dispatch.
# openai is always the api lane, so its key is just "openai".
_PROVIDERS: Dict[str, Provider] = {
    "openai": _openai_provider,
    "anthropic:api": _anthropic_provider,               # B2
    "anthropic:subscription": _subscription_provider,   # B3
}


def registered_lanes(provider: str) -> Tuple[str, ...]:
    """The lanes `provider` has a registered implementation for, DERIVED from
    the dispatch registry above — so this can never drift from what `chat`
    will actually accept.

    NL-155 fix loop 1 (QA F-3, 2026-08-14). The doctor was partitioning seats
    by SUBTRACTION — "anthropic seats that are not api-lane seats" — which put
    every seat on an unimplemented lane (`NEWSLENS_LANE=sbscription`) into the
    subscription bucket and printed a cheerful INFO about a machine that cannot
    run a single step. Subtraction is the wrong shape for a partition whose
    third cell (invalid) is reachable from a typo in a env var. Note the
    registry's openai key is the bare provider name (openai lives only on the
    api lane), which is why the lane is read as `key.partition(":")[2] or
    "api"` rather than by splitting on a required colon."""
    lanes = {key.partition(":")[2] or "api"
             for key in _PROVIDERS if key.split(":", 1)[0] == provider}
    return tuple(sorted(lanes))


def _provider_key(cfg: SeatConfig) -> str:
    # openai lives only on the api lane; forcing it onto another lane is an
    # unavailable combo and must fail loud (never a silent api call).
    if cfg.provider == "openai" and cfg.lane == "api":
        return "openai"
    return f"{cfg.provider}:{cfg.lane}"


def _select_provider(cfg: SeatConfig) -> Provider:
    provider = _PROVIDERS.get(_provider_key(cfg))
    if provider is None:
        # Name the seat's ACTUAL SEATS default (provider/model on the api lane) as
        # the fix — not a stale hard-coded "gpt-4o default", which is what this
        # message used to say and which stopped being true the moment any seat
        # left openai. The default is READ from the table for exactly that
        # reason: naming a model in this string is how it goes stale — the
        # parenthetical here named rank/editor/script as Haiku until NL-147, two
        # seat batches after they stopped being. The api lane (openai +
        # anthropic) is implemented; the only unregistered lane is the claude -p
        # subscription lane (B3).
        default = SEATS[cfg.seat]
        raise LaneUnavailable(
            f"seat '{cfg.seat}' resolves to provider='{cfg.provider}' "
            f"lane='{cfg.lane}', which has no registered implementation. "
            f"Registered lanes: openai/api, anthropic/api, anthropic/"
            f"subscription. Fix: unset NEWSLENS_LANE / NEWSLENS_LANE_"
            f"{cfg.seat.upper()} to use the seat's default "
            f"({default.provider}/{default.model} on the {default.lane} lane) "
            f"— note openai runs ONLY on the api lane."
        )
    return provider


def chat(req: LaneRequest) -> LaneResponse:
    """Dispatch a completion to the request's resolved provider/lane. Raises
    LaneUnavailable (fail-loud) when the lane has no implementation."""
    return _select_provider(req.cfg)(req)


def effective_seat(seat: str,
                   env: Optional[Dict[str, str]] = None) -> Tuple[SeatConfig, Optional[str]]:
    """The transport-ready seat config after the principal-armed SINGLE FALL
    (B3-D2). Resolves the seat; if its lane is unavailable AT THE GATE
    (check_lane class — an unregistered lane, or a subscription seat whose
    `claude` binary won't resolve) AND NEWSLENS_LANE_FALLBACK=api is armed AND
    the seat's api lane is actually available, returns (api_cfg, reason) — ONE
    labeled fall. Otherwise raises LaneUnavailable (fail-loud preserved).

    `reason` is a short machine tag for the ledger label 'api(fallback:<reason>)'
    and the disclosed run-log warning. Rules (design transcript §5.1 failure
    semantics): never silent; never a fall that isn't armed; never
    api->subscription; and if BOTH the subscription lane AND the api lane are
    dead, dies loud on the ORIGINAL (subscription) error. The fall is a
    check_lane-class (availability/config) event only — a transport error
    mid-call is NOT a fall, it retries the original bytes like any 5xx."""
    env = os.environ if env is None else env
    cfg = resolve_seat(seat, env)
    try:
        # NL-156: the SAME env the seat was resolved from also resolves the
        # binary. These two lines used to read different worlds.
        check_lane(cfg, env)
        return cfg, None
    except LaneUnavailable as sub_exc:
        # Fall ONLY a GENUINE subscription lane — one whose subscription provider
        # is registered but merely UNAVAILABLE (anthropic's claude -p with a
        # missing/unresolvable binary) — and only when the principal armed it. An
        # openai seat forced to 'subscription' (e.g. a global NEWSLENS_LANE=
        # subscription hitting the writer/analyst/state seats) has NO subscription
        # provider at all: that is a config error that must DIE LOUD, never be
        # silently rescued onto openai:api (which would mask the misconfig and
        # spend on openai while the operator believes they set subscription).
        sub_registered = f"{cfg.provider}:subscription" in _PROVIDERS
        if cfg.lane == "subscription" and sub_registered and fallback_armed(env):
            api_cfg = replace(cfg, lane="api")
            try:
                check_lane(api_cfg, env)
            except LaneUnavailable:
                raise sub_exc      # both lanes dead -> die loud on the original
            return api_cfg, "subscription_unavailable"
        raise


def fallback_lane_label(reason: Optional[str], lane: str) -> str:
    """The ledger's lane label. A normal row is just the lane; a fallen row is
    'api(fallback:<reason>)' so the durable record shows the fall provenance —
    never a bare 'api' that hides real API spend the subscription lane avoided."""
    return lane if not reason else f"{lane}(fallback:{reason})"


def check_lane(cfg: SeatConfig, env: Optional[Dict[str, str]] = None) -> None:
    """Preflight: raise LaneUnavailable (fail-loud, named fix) if the seat's
    resolved lane has no registered provider. A caller runs this ONCE per step
    BEFORE any transport or retry, so a config error never sleeps, never
    retries, and — the D1 close — never lets one seat's transport run while a
    different seat's lane is what the ledger records: the preflighted seat is
    the seat the ledger attributes and the lane the bytes ride.

    `env` (NL-156, 2026-08-14) is the mapping the BINARY is resolved from, and
    it must be the same mapping the SEAT was resolved from. It used to be
    neither passed nor accepted: `resolve_claude_bin()` read os.environ while
    the caller had already resolved `cfg` out of some other env, so this gate
    answered a question about a world its caller was not in. Two concrete
    harms, both observed rather than theorised:

      * PRODUCTION — the doctor. `check_llm_lanes(env)` builds `env` from
        `load_effective_env()` (.env values under os.environ), resolves each
        seat from it, then preflighted the binary from os.environ. A principal
        who set NEWSLENS_CLAUDE_BIN in .env WITHOUT exporting it got a "LLM
        lanes" section judging a binary that env never named, while the
        "Subscription lane" section right below it (doctor.py's
        resolve_claude_bin(env) call) judged the one it did. Two sections of
        one report, disagreeing about the same machine.
      * QA — the swallowed probe. Two 08-13 fall-over probes passed
        NEWSLENS_CLAUDE_BIN in the mapping handed to `effective_seat` and had
        to ALSO monkeypatch the process env to make the fall fire; both
        docstrings conceded the mapping entry was inert. A probe that must
        reach around the seam it is probing cannot witness that seam break.

    Default None => os.environ, so the callers that legitimately have no env in
    hand (generate's stage preflight, memory_core's state gate) are unchanged.

    B3: for a subscription-lane seat the binary must ALSO resolve here (pure
    filesystem check, no spawn) — a missing/misconfigured CLI is a config
    error, not a transient one, so it dies at the gate naming the install fix
    rather than being retried into a GenerateError inside the transport loop.
    This is what makes the FIX-1 stage-boundary preflight (analyst/state) and
    the per-step gate consistent: 'lane unavailable' for the subscription lane
    means BOTH the provider is registered AND its binary is present."""
    _select_provider(cfg)
    if cfg.lane == "subscription":
        bin_path, reason = resolve_claude_bin(env)
        if bin_path is None:
            raise LaneUnavailable(
                f"seat '{cfg.seat}' is on the claude -p subscription lane but "
                f"{reason}. Or flip this seat to the api fall-over lane: set "
                f"NEWSLENS_LANE_{cfg.seat.upper()}=api (needs ANTHROPIC_API_KEY)."
            )


# ---------------------------------------------------------------------------
# Config resolution from env (the B2/B3 plug; behaviour-neutral in B1)
# ---------------------------------------------------------------------------

def resolve_seat(seat: str, env: Optional[Dict[str, str]] = None) -> SeatConfig:
    """The effective SeatConfig after env overrides. With no env set, returns
    the SEATS default (gpt-4o/openai/api) — behaviour unchanged.

    Overrides (all optional; documented in .env.example):
      NEWSLENS_LANE          global lane override (api | subscription)
      NEWSLENS_LANE_<SEAT>   per-seat lane override (wins over the global)
      NEWSLENS_MODEL_<SEAT>  per-seat MODEL override — the BATTERY HARNESS surface
                             (§5.1): the ~07-24 blind battery A/Bs the writer seat
                             across Opus / Fable 5 / Sonnet by setting
                             NEWSLENS_MODEL_WRITER; unset in normal operation. Only
                             the model string is swapped — provider/lane/thinking/
                             effort/sampling/prices stay the seat's, so the arm is
                             a controlled single-variable change. usd_shadow then
                             prices at the SEAT's table (a battery arm's real
                             billing lives in the CLI/api usage, cross-checked).

    A lane override to a lane with no registered provider does NOT fail here —
    it fails loud at call time (chat -> LaneUnavailable) — so the doctor can
    REPORT an unavailable-lane config without making a live call.
    """
    base = SEATS[seat]
    env = os.environ if env is None else env
    lane = (
        env.get(f"NEWSLENS_LANE_{seat.upper()}")
        or env.get("NEWSLENS_LANE")
        or base.lane
    ).strip()
    model = (env.get(f"NEWSLENS_MODEL_{seat.upper()}") or "").strip() or base.model
    if lane == base.lane and model == base.model:
        return base
    return replace(base, lane=lane, model=model)


def seat_is_openai(seat: str, env: Optional[Dict[str, str]] = None) -> bool:
    """True iff `seat` resolves to the OpenAI provider (gpt-4o) — i.e. it needs
    OPENAI_API_KEY. A″ (2026-07-17, keyless-OpenAI audit): the legacy per-stage
    'OPENAI_API_KEY not set -> refuse' checks were written when every seat was
    gpt-4o.

    THE LIVE ROSTER (NL-155 truth-edit, 2026-08-14): `synthesis` is the ONLY
    openai seat, and it still has no live call site — so `seat_is_openai` is
    False for every seat the pipeline actually calls. All seven of
    rank/editor/script/analyst/writer/state/follow_altitude are anthropic, and
    the OpenAI key is INERT for them (passed as the openai offline-test seam
    value, ignored by the anthropic providers).

    This paragraph said "only `state` (and `synthesis`, no live call site yet)
    is openai" until 2026-08-14 — written at the 07-17 audit and outlived by
    ENG-M0 (2026-08-06), which moved `state` to Opus 4.8 on the subscription
    lane. generate.py's keyless-OpenAI arm around the state preflight already
    describes the seat correctly and goes quiet; this docstring was the last
    place still naming `state` as the openai seat.

    So a caller requires the key ONLY when `seat_is_openai(seat)` — a
    keyless-OpenAI run is healthy today. Provider is fixed per seat (env
    overrides change only lane/model), so this is False for the anthropic seats
    regardless of NEWSLENS_MODEL_/LANE_."""
    return resolve_seat(seat, env).provider == "openai"


def fallback_armed(env: Optional[Dict[str, str]] = None) -> bool:
    """NEWSLENS_LANE_FALLBACK=api — the principal-armed opt-in for one fall to
    the api lane when the subscription lane is unavailable. B1 reads/reports
    it; the fall itself needs a second lane (B2/B3)."""
    env = os.environ if env is None else env
    return (env.get("NEWSLENS_LANE_FALLBACK") or "none").strip().lower() == "api"


# ---------------------------------------------------------------------------
# Cost attribution — the shadow ledger keys (JSON, additive, no migration)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TOKEN-BAND ALARMS — Rook's regression armor (eng-6 §9, carried from eng-4 §5.4)
# ---------------------------------------------------------------------------
# The thinking seam rests on ONE environment variable that a `claude` CLI
# release could stop honoring without telling anyone. If that happens the seats
# go quietly back to spending 20 minutes an edition on deliberation, and the
# only symptom is a slower night. These bands are the detector: each is sized
# off the seat's MEASURED off-arm output (eng-6 §3.1) with headroom, and each
# is blown 3-11x by the taxed arm — so the alarm cannot half-fire.
#
#   seat    off-arm observed   band     taxed arm (what trips it)
#   editor  3,277-3,875        >6,000   22,384
#   script  1,111-1,639        >4,000   15,929-17,047
#   state     349-  378        >1,200   13,465-14,509
#
# WARN-GRADE, never a gate: a band trip means "investigate the transport", not
# "this call is wrong". The call's own validators decide correctness.
#
# WHY HERE: cost_fields is the ONE place every seat's (cfg, usage) pair meets,
# on every lane and every caller — generate.call_llm, memory_core's state
# rewrite, analysis, follow_altitude. A per-caller copy would be four copies
# that drift, and a new call site would silently miss the armor. (eng-4 C3
# proposed follow_altitude.py's own cost_sink block; one chokepoint covers that
# seat and every other for the same line count.)
#
# FREE HISTORICAL BASELINE, already on disk: generation_log.jsonl carries
# usd_shadow per step, and usd_shadow / the seat's out-rate IS an output-token
# count. The day a band fires, nine days of per-step history are available to
# diff against without adding a table.
_TOKEN_BANDS: Dict[str, int] = {
    # RE-MEASURED 2026-08-06 (ENG-M0). THE DETECTOR'S PREMISE CHANGED and the
    # bands had to be re-founded, not merely re-scaled. As the block above
    # explains, these existed to catch a `claude` release quietly ceasing to
    # honor MAX_THINKING_TOKENS=0. For editor/script/state that env var IS NO
    # LONGER SENT — those seats left _THINKING_OFF_SUB_SEATS — so the original
    # failure mode is structurally gone for them and a band sized against it
    # would be measuring nothing. The bands are kept and re-sized to a DIFFERENT,
    # still-live question: "is this seat emitting far more than it was measured
    # to emit?" — which catches a model/CLI regression, a prompt blow-up, or an
    # effort-knob mistake.
    #
    #   seat    post-flip ceiling (provenance)        band    still trips the
    #                                                         taxed Haiku arm?
    #   editor  5,867 MEASURED (Opus adaptive, n=2)  >10,000  yes (22,384-28,772)
    #   script  4,670 MEASURED (Opus adaptive, n=1)  >10,000  yes (15,929-17,047)
    #   state   2,000 MEASURED (Opus adaptive, n=1)   >4,000  yes (13,465-14,509)
    #   rank   17,425 MEASURED (Sonnet adaptive, n=6) >30,000 NEW — see below
    #
    # THE STATE BAND WAS BROKEN AND THE PROBE CAUGHT IT. Its first cut derived a
    # 525-token ceiling and set the band at 1,500; the seat's real thinking-on
    # call emits 2,000, so the alarm would have fired on EVERY normal state
    # rewrite — the precise failure the block above forbids ("normal variance
    # cannot fire it"). A band that always fires is not armor, it is noise that
    # trains the reader to ignore the one trip that matters. 4,000 is 2x the
    # measured call and still 3.4x under the taxed arm it exists to catch.
    # Each band is ~1.7-2.9x its seat's post-flip ceiling, so normal variance
    # cannot fire it; and each is still blown 2-9x by the old taxed arm, so the
    # ORIGINAL detection power is retained for free rather than traded away.
    "editor": 10000,
    "script": 10000,
    "state": 4000,
    # rank JOINS the armor. It never had a band (it was never a thinking-off
    # seat), but post-flip it is the seat with the largest output budget, and
    # 30,000 sits deliberately BELOW ranking.MAX_COMPLETION_TOKENS (36,000): on
    # the api lane the alarm therefore fires BEFORE the hard cap would truncate,
    # turning a silent truncation-death into a warned one.
    "rank": 30000,
    # NL-99 (eng-4 §5.4): an honest resolver answer is 51-61 tokens — a
    # three-field JSON object. 200 is ~3.3x that and ~1/70th of the taxed
    # 14,000-token arm, so the signal is unambiguous in both directions.
    # UNTOUCHED by ENG-M0: this is the one seat still on the thinking-off
    # mechanism, so for follow_altitude the ORIGINAL premise still holds exactly.
    "follow_altitude": 200,
}


def _check_token_band(cfg: SeatConfig, completion_tokens: int) -> None:
    """One comparison per billed attempt. Fires only on the SUBSCRIPTION lane:
    the api transport has always honored cfg.thinking, so a band trip there
    would mean something else entirely and this alarm would be lying about the
    cause."""
    band = _TOKEN_BANDS.get(cfg.seat)
    if band is None or cfg.lane != "subscription" or completion_tokens <= band:
        return
    print(
        f"⚠ token-band alarm: seat '{cfg.seat}' emitted {completion_tokens:,} "
        f"output tokens on the subscription lane (band >{band:,}). The seat "
        f"declares thinking=None; this is the shape of extended thinking "
        f"coming back — check that MAX_THINKING_TOKENS=0 still suppresses it "
        f"in this `claude` CLI version. Warn only; the call was not changed.",
        file=sys.stderr,
    )


def cost_fields(cfg: SeatConfig, usage: Optional[Dict], *,
                fallback_reason: Optional[str] = None) -> Dict:
    """The lane/shadow ledger keys for one billed attempt, added ALONGSIDE the
    existing `{step, attempt, prompt_tokens, completion_tokens, usd}` entry
    (the legacy `usd` stays == usd_charged for back-compat).

    `usage` is the provider-native usage dict the cost_sink already holds — the
    OpenAI shape for the gpt-4o seats, and the OpenAI-shaped dict the anthropic
    provider synthesises for the Claude lane (so this reader is lane-agnostic).
    usd_shadow is computed from the seat's QA-pinned price table; usd_charged ==
    usd_shadow on the api lane and 0.0 on the subscription lane (B3).

    Cache tokens are RECORDED (both cache_read and, for the Claude lane,
    cache_creation) but DELIBERATELY NOT discounted from usd_shadow — even in
    B4, where cache_control lands (see LaneRequest.system / _anthropic_provider).
    The transcript's law is "the ~0.1x cache-read assumption is MEASURED, not
    assumed": B4 WIRES the cache surface and lets cache_read go nonzero so the
    hit rate is measured on live/battery runs, but a MONEY GUARD must never
    under-count on an unverified hit — at current prefix sizes some prefixes sit
    below the model cache minimum (Opus 4096 / Sonnet 2048 tokens) and may not
    cache at all. So usd_shadow stays the conservative undiscounted figure (the
    budget cap over-counts, the safe direction) and no cost test moves; the
    discount is a follow-up once the measured hit rate justifies it.
    """
    usage = usage or {}
    pt = usage.get("prompt_tokens") or 0
    ct = usage.get("completion_tokens") or 0
    _check_token_band(cfg, ct)   # Rook's regression armor; warn only
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0
    creation = usage.get("cache_creation_tokens") or 0
    shadow = round(
        pt / 1e6 * cfg.usd_per_mtok_in + ct / 1e6 * cfg.usd_per_mtok_out, 6
    )
    # usd_charged == usd_shadow on the api lane; 0.0 on the subscription lane
    # (flat-rate — no per-call bill). Budget caps bind on usd_shadow in BOTH
    # lanes (Onna's law), so callers accumulate shadow, not charged.
    charged = shadow if cfg.lane == "api" else 0.0
    fields = {
        "model": cfg.model,
        # B3-D2: a fallen row is labeled 'api(fallback:<reason>)' so the durable
        # ledger shows the fall provenance (never a bare 'api' hiding real API
        # spend the subscription lane avoided). fallback_reason is None on every
        # normal row, so no existing ledger value moves.
        "lane": fallback_lane_label(fallback_reason, cfg.lane),
        "cache_read_tokens": cached or 0,
        "cache_creation_tokens": creation,
        "usd_shadow": shadow,
        "usd_charged": round(charged, 6),
    }
    # B3: the subscription provider LABELS a shadow computed from estimated
    # (not CLI-reported) token counts — carry the label into the ledger so a
    # reader never mistakes an estimate for a metered figure (never fake
    # precision). Absent on every metered row.
    if usage.get("_token_source") == "estimated":
        fields["usd_shadow_estimated"] = True
    return fields
