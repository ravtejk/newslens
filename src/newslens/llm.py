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
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Tuple

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

# Claude Haiku 4.5 pricing (USD per MTok) — the QA-pinned rows for the seats B2
# flips to the Claude API lane (rank/editor/script). Shadow math for those
# seats now reads THESE per-seat prices from the seat table, never a global
# GPT-4o constant (dispatch B2: "per-seat prices, not a global constant").
HAIKU_USD_PER_MTOK_IN = 1.00
HAIKU_USD_PER_MTOK_OUT = 5.00

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
    # The Claude 4.6+ family — Opus 4.8 (writer) and Sonnet 5 (analyst) — REJECTS
    # them with a 400; Haiku 4.5 and GPT-4o still accept them. False => the
    # anthropic api provider OMITS temperature (never a 400 on the flipped
    # seats); the Haiku/openai seats keep sampling=True so their request bytes
    # are byte-unchanged from B2/B1 (the pinned body tests do not move).
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
# per seat). Timeouts match today's per-call-site values (rank & analyst 90s,
# the writer family 120s, state 60s) — B2 changes model/provider/lane/price,
# NOT timeouts (Haiku is faster than GPT-4o, so the existing headroom holds).
_GPT4O_API = dict(
    provider="openai", model="gpt-4o", lane="api",
    usd_per_mtok_in=GPT4O_USD_PER_MTOK_IN,
    usd_per_mtok_out=GPT4O_USD_PER_MTOK_OUT,
)

# B2 (approved Option C): the three cheapest/most-validated seats flipped to the
# Claude lane on Haiku 4.5. thinking/effort stay None — these are mechanical
# single-turn completions (rank clustering, editorial tightening, TTS-script
# adaptation), not reasoning work, so no thinking param is sent (dispatch B2).
# _HAIKU_API is retained as the REGISTERED ALTERNATIVE (the api fall-over lane
# a seat reaches via NEWSLENS_LANE_<SEAT>=api or NEWSLENS_LANE_FALLBACK=api) and
# the rollback target (flip a row back to **_HAIKU_API, or **_GPT4O_API).
_HAIKU_API = dict(
    provider="anthropic", model="claude-haiku-4-5", lane="api",
    usd_per_mtok_in=HAIKU_USD_PER_MTOK_IN,
    usd_per_mtok_out=HAIKU_USD_PER_MTOK_OUT,
)

# B3 (subscription-lane mandate, DECISIONS 2026-07-16): the anthropic-provider
# seats DEFAULT to the `claude -p` subscription lane — subscription is ALWAYS
# the priority, the API lane is the registered fall-over (NEWSLENS_LANE_<SEAT>=
# api, or the principal-armed NEWSLENS_LANE_FALLBACK=api). Same model + prices
# as _HAIKU_API (shadow is API-priced regardless of lane); only the transport
# and usd_charged change (usd_charged == 0.0 on the subscription lane).
_HAIKU_SUB = dict(
    provider="anthropic", model="claude-haiku-4-5", lane="subscription",
    usd_per_mtok_in=HAIKU_USD_PER_MTOK_IN,
    usd_per_mtok_out=HAIKU_USD_PER_MTOK_OUT,
)

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

# B4: the analyst seat is Sonnet 5. 2026-07-17 (item C): flipped onto the
# subscription lane with the writer's (field-proven edition 7: analyst Sonnet on
# subscription, green end-to-end). Same truncation-gap caveat as the writer — the
# analyst has hard validate_brief teeth, and on the subscription lane (no
# max_tokens) those teeth, not a length-finish, are what catch a truncated brief.
# adaptive thinking on, effort "high". sampling=False: Sonnet 5 rejects
# temperature. Shadow uses standard $3/$15 (API-priced regardless of lane). The
# api lane is the registered fall-over (NEWSLENS_LANE_ANALYST=api).
_SONNET_ANALYST_SUB = dict(
    provider="anthropic", model="claude-sonnet-5", lane="subscription",
    usd_per_mtok_in=SONNET_USD_PER_MTOK_IN,
    usd_per_mtok_out=SONNET_USD_PER_MTOK_OUT,
    thinking="adaptive", effort="high", sampling=False,
)

SEATS: Dict[str, SeatConfig] = {
    "rank":      SeatConfig("rank",      timeout_s=90,  timeout_sub_s=300, **_HAIKU_SUB),
    # item C (2026-07-17): writer/analyst on the subscription lane. timeout_sub_s
    # = the api-calibrated ceiling + a ~300s subscription lane tax (claude -p
    # subprocess spin-up + agentic-harness verbosity — the same absolute tax the
    # mechanical Haiku seats pay over their api timeouts). analyst 240->540,
    # writer 600->900 (Opus xhigh on the harness is the slowest path in the
    # system; edition 7 ran fine but uninstrumented — pad the tax generously).
    "analyst":   SeatConfig("analyst",   timeout_s=240, timeout_sub_s=540, **_SONNET_ANALYST_SUB),
    "writer":    SeatConfig("writer",    timeout_s=600, timeout_sub_s=900, **_OPUS_WRITER_SUB),
    "editor":    SeatConfig("editor",    timeout_s=120, timeout_sub_s=300, **_HAIKU_SUB),
    "script":    SeatConfig("script",    timeout_s=120, timeout_sub_s=300, **_HAIKU_SUB),
    # NL-17-M1 increment A (the altitude slice): the follow-altitude resolver
    # seat. A cheap mechanical single-turn classification (given a followed
    # thread, pick entity|storyline + the primary entity + a disclosure line) —
    # the same MODEL class as rank/editor/script (Haiku 4.5), but the ONE seat
    # whose code default is the API lane (**_HAIKU_API), not subscription.
    # RESOLVER LANE FIX (principal ruling 2026-07-20, DECISIONS "RESOLVER LANE
    # FIX"): unlike every batch seat this one runs INTERACTIVE, with a reader
    # WAITING on the follow-line. Diagnosis (Opus-seated eng round, 07-20) found
    # the feature 100% broken in production — the 12s subscription resolve wall vs
    # a real ~48s claude -p resolve => 4/4 source=degrade, ZERO source=auto commits
    # ever. The api lane resolves the SAME call in ~1.2s (measured, $0.0013). This
    # is the wrong-transport-for-an-interactive-path bug (Ada): latency-sensitive
    # and batch seats have opposite lane-optimization criteria, so this seat's code
    # default departs from the B4 all-subscription default. It uses the existing
    # ANTHROPIC_API_KEY; real charge is cents/month at a few follows/day.
    # ESCAPE HATCH (unchanged mechanism, inverted meaning): NEWSLENS_LANE_FOLLOW_
    # ALTITUDE=subscription forces it back onto claude -p (resolve_seat reads the
    # per-seat override regardless of the default); =api is redundant with the
    # default. thinking/effort stay None — not reasoning work; the caller's parse+
    # validate+corrected-retry law backstops the prompt-shaped JSON (rank's twin).
    # DELIBERATELY NOT in _STEP_PREFIX_SEAT: it is not a `generate` edition step
    # (its output is a REPORT, never edition state or a selection weight), so it
    # is reached only through follow_altitude.resolve_altitude, never
    # seat_for_step / generate.call_llm.
    # TIMEOUTS. api (default, the interactive path): timeout_s=8 — a healthy Haiku
    # round-trip is ~1.2s, so 8s is ~6.6x headroom; a hung provider still degrades
    # to the PROVEN this-story commit (exact copy) in a beat, never pinning the
    # reader. subscription (the explicit escape-hatch path ONLY): timeout_sub_s
    # raised 12 -> 45s. This knob is INERT on the default api path — there is no
    # automatic api->subscription fall (effective_seat forbids it, ~llm.py:994;
    # the only lane-replace in the tree is subscription->api). The subscription
    # lane is reached ONLY when explicitly forced (per-seat NEWSLENS_LANE_FOLLOW_
    # ALTITUDE=subscription or global NEWSLENS_LANE=subscription). Raised so an
    # explicitly-selected subscription resolve can actually complete (median ~14s,
    # tail ~48s) instead of re-degrading at the old 12s wall; an api-key failure
    # on the default path surfaces as an honest source=degrade, never a silent
    # subscription retry. The api-lane path's own timing is untouched.
    "follow_altitude": SeatConfig("follow_altitude", timeout_s=8, timeout_sub_s=45, **_HAIKU_API),
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
    # follow with no other module edit. timeout_sub_s=300 matches the other
    # mechanical Haiku seats (subprocess startup overhead). Ships with a MANDATORY
    # spot-check + pre-registered revert-if (DECISIONS 2026-07-17 "state seat
    # flips to Haiku/subscription; audio held" — the durable record): any
    # validate_state trip / photocopy-suspect flag / quality miss -> revert this
    # row to **_GPT4O_API in one clean diff (needs the OpenAI key restored) or
    # escalate to the battery's state arm.
    "state":     SeatConfig("state",     timeout_s=60,  timeout_sub_s=300, **_HAIKU_SUB),
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
    # ~0.1x. None (the Haiku/openai seats never set it) => the request bytes are
    # byte-unchanged. The subscription lane has NO cache_control surface, so its
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


def _anthropic_provider(req: LaneRequest) -> LaneResponse:
    """Claude API lane transport: a raw urllib POST to /v1/messages. The lane
    reads its OWN endpoint (ANTHROPIC_MESSAGES_URL) and IGNORES req.url — the
    callers pass the openai offline-test seam url, which does not apply here.
    Headers: x-api-key (the lane's own credential) + anthropic-version +
    content-type. max_tokens is REQUIRED by the Messages API. thinking/effort
    are sent only when the seat sets them (the Haiku seats leave both None).

    urllib is referenced through the `urllib.request` module (not a bound
    import) so the suite's `monkeypatch.setattr(urllib.request, "urlopen", …)`
    interception covers this path exactly as it covers the openai provider."""
    cfg = req.cfg
    body = {
        "model": cfg.model,
        "max_tokens": req.max_tokens,          # REQUIRED by the Messages API
        "messages": [{"role": "user", "content": req.prompt}],
    }
    # B4: the Claude 4.6+ family (Opus 4.8 writer, Sonnet 5 analyst) REJECTS
    # temperature with a 400 — omit it when the seat says so. The Haiku/openai
    # seats keep sampling=True, so `temperature` stays where it was (right after
    # `messages`), and their pinned request bytes do not move.
    if cfg.sampling:
        body["temperature"] = req.temperature
    # B4 prompt caching + json nudge. `system` is a list when a cacheable prefix
    # is present (cache_control:{ephemeral} on the big stable block, the json
    # nudge appended after it as its own volatile-free block); a plain STRING for
    # a Haiku json_mode seat with no prefix (byte-unchanged from B2). Render
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
    if cfg.thinking:                            # None on the Haiku seats -> omitted
        body["thinking"] = {"type": cfg.thinking}
    if cfg.effort:                             # None on the Haiku seats -> omitted
        body["output_config"] = {"effort": cfg.effort}
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
    with urllib.request.urlopen(request, timeout=cfg.timeout_s) as resp:
        native = json.load(resp)
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
    the conftest points NEWSLENS_CLAUDE_BIN at a non-existent sentinel.)"""
    env = os.environ if env is None else env
    override = (env.get("NEWSLENS_CLAUDE_BIN") or "").strip()
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override, "env"
        return None, (f"NEWSLENS_CLAUDE_BIN={override!r} is not an executable "
                      "file — fix the path or unset it to fall back to PATH")
    found = shutil.which("claude", path=env.get("PATH"))
    if found:
        return found, "path"
    if os.path.isfile(CLAUDE_BIN_DEFAULT) and os.access(CLAUDE_BIN_DEFAULT, os.X_OK):
        return CLAUDE_BIN_DEFAULT, "default"
    return None, (
        "the `claude` CLI could not be found — install it, then set "
        "NEWSLENS_CLAUDE_BIN, add it to PATH, or place it at "
        f"{CLAUDE_BIN_DEFAULT}"
    )


def _subscription_env(env: Dict[str, str]) -> Dict[str, str]:
    """The child process env — an allowlist with ANTHROPIC_API_KEY guaranteed
    absent (Rook #1). Defensive pop in case a future allowlist entry aliases it."""
    child = {k: env[k] for k in _SUBSCRIPTION_ENV_ALLOW if k in env}
    child.pop("ANTHROPIC_API_KEY", None)
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
    once, same law as a 5xx); a timeout SIGKILLs the child (subprocess.run) and
    surfaces as TimeoutError (also transport-shaped). LaneRequest.api_key /
    .url (the openai offline-test seam) are IGNORED — this lane owns its own
    auth (the logged-in CLI) and never makes an HTTP call of its own."""
    cfg = req.cfg
    bin_path, source = resolve_claude_bin()
    if bin_path is None:
        # Belt-and-suspenders: check_lane already resolved the binary at the
        # gate, so this only fires on a between-gate-and-call disappearance.
        raise LaneUnavailable(
            f"seat '{cfg.seat}' is on the claude -p subscription lane but "
            f"{source}"
        )
    args = [bin_path, *_SUBSCRIPTION_BASE_FLAGS, "--model", cfg.model]
    if cfg.effort:                              # None on the Haiku seats -> omitted
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
            env=_subscription_env(dict(os.environ)),
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
        raise RuntimeError(
            f"claude -p ({cfg.seat}) exited {proc.returncode}: "
            f"{(proc.stderr or '').strip()[:200]}"
        )
    try:
        payload = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"claude -p ({cfg.seat}) returned non-JSON stdout "
            f"({proc.stdout[:120]!r})"
        ) from exc
    if not isinstance(payload, dict) or payload.get("is_error"):
        raise RuntimeError(
            f"claude -p ({cfg.seat}) reported an error result: "
            f"{str(payload.get('result') if isinstance(payload, dict) else payload)[:200]}"
        )
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
        # the fix — not a stale "gpt-4o default" (rank/editor/script are Haiku on
        # the Claude api lane now). The api lane (openai + anthropic) is
        # implemented; the only unregistered lane is the claude -p subscription
        # lane (B3).
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
        check_lane(cfg)
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
                check_lane(api_cfg)
            except LaneUnavailable:
                raise sub_exc      # both lanes dead -> die loud on the original
            return api_cfg, "subscription_unavailable"
        raise


def fallback_lane_label(reason: Optional[str], lane: str) -> str:
    """The ledger's lane label. A normal row is just the lane; a fallen row is
    'api(fallback:<reason>)' so the durable record shows the fall provenance —
    never a bare 'api' that hides real API spend the subscription lane avoided."""
    return lane if not reason else f"{lane}(fallback:{reason})"


def check_lane(cfg: SeatConfig) -> None:
    """Preflight: raise LaneUnavailable (fail-loud, named fix) if the seat's
    resolved lane has no registered provider. A caller runs this ONCE per step
    BEFORE any transport or retry, so a config error never sleeps, never
    retries, and — the D1 close — never lets one seat's transport run while a
    different seat's lane is what the ledger records: the preflighted seat is
    the seat the ledger attributes and the lane the bytes ride.

    B3: for a subscription-lane seat the binary must ALSO resolve here (pure
    filesystem check, no spawn) — a missing/misconfigured CLI is a config
    error, not a transient one, so it dies at the gate naming the install fix
    rather than being retried into a GenerateError inside the transport loop.
    This is what makes the FIX-1 stage-boundary preflight (analyst/state) and
    the per-step gate consistent: 'lane unavailable' for the subscription lane
    means BOTH the provider is registered AND its binary is present."""
    _select_provider(cfg)
    if cfg.lane == "subscription":
        bin_path, reason = resolve_claude_bin()
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
    gpt-4o. Post-B4 only `state` (and `synthesis`, no live call site yet) is
    openai; rank/editor/script/analyst/writer/follow_altitude are anthropic and
    the OpenAI key is INERT for them (passed as the openai offline-test seam value,
    ignored by the anthropic providers). So a caller requires the key ONLY when
    `seat_is_openai(seat)` — a keyless-OpenAI run with all-anthropic seats is
    healthy. Provider is fixed per seat (env overrides change only lane/model), so
    this is False for the anthropic seats regardless of NEWSLENS_MODEL_/LANE_."""
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
