"""llm.py — the provider seam (B1 of the depth-architecture build).

One module owns LLM transport + lane selection + token/cost attribution so
that adding a provider (B2: Claude API lane) or a transport (B3: the
`claude -p` subscription lane) is a plug HERE, not surgery across the three
historical call sites (generate.call_llm, ranking.call_llm_validated,
analysis.call_analysis_model).

B1 SCOPE — PURE REFACTOR (acceptance bar: existing suite green, unchanged):
  * Only the "openai" provider is registered; every seat resolves to gpt-4o
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
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional

# The OpenAI chat endpoint (the seam's single copy — ranking.OPENAI_CHAT_URL
# and analysis's inline literal both named this same URL before B1).
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# GPT-4o pricing (USD per MTok) — the QA-pinned price table for the shadow
# ledger. Matches the per-file constants the three call sites carry today
# (ranking.RANK_USD_*, generate.WRITER_USD_*, analysis.ANALYSIS_USD_*); B2
# adds the Claude seats' rows when it flips models. usd_shadow is computed
# from these, so a lane flip never forks the cost dashboard (Onna's law).
GPT4O_USD_PER_MTOK_IN = 2.50
GPT4O_USD_PER_MTOK_OUT = 10.00


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


# The seat table — code constants (the one-constant-seam precedent, one row
# per seat). Every seat expresses the CURRENT stack as its default:
# gpt-4o / openai / api lane. Timeouts match today's per-call-site values
# EXACTLY (rank & analyst 90s, the writer family 120s) so routing through the
# seam changes no behaviour.
_GPT4O_API = dict(
    provider="openai", model="gpt-4o", lane="api",
    usd_per_mtok_in=GPT4O_USD_PER_MTOK_IN,
    usd_per_mtok_out=GPT4O_USD_PER_MTOK_OUT,
)

SEATS: Dict[str, SeatConfig] = {
    "rank":      SeatConfig("rank",      timeout_s=90,  **_GPT4O_API),
    "analyst":   SeatConfig("analyst",   timeout_s=90,  **_GPT4O_API),
    "writer":    SeatConfig("writer",    timeout_s=120, **_GPT4O_API),
    "editor":    SeatConfig("editor",    timeout_s=120, **_GPT4O_API),
    "script":    SeatConfig("script",    timeout_s=120, **_GPT4O_API),
    # synthesis has no live call site yet (B6 builds it); it is declared here
    # so the seat table is the whole roster the design named, not a subset.
    "synthesis": SeatConfig("synthesis", timeout_s=120, **_GPT4O_API),
}

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
    """The seat a generate step's ledger entry is labelled with. Defaults to
    the writer seat for any unrecognised step (all writer-family seats are
    identical gpt-4o in B1, so a default is behaviour-neutral)."""
    for prefix, seat in _STEP_PREFIX_SEAT:
        if step.startswith(prefix):
            return seat
    return "writer"


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


# Provider registry. Keyed by provider-lane so B2 registers "anthropic:api"
# and B3 registers "anthropic:subscription" without touching this dispatch.
# openai is always the api lane, so its key is just "openai".
_PROVIDERS: Dict[str, Provider] = {
    "openai": _openai_provider,
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
        raise LaneUnavailable(
            f"seat '{cfg.seat}' resolves to provider='{cfg.provider}' "
            f"lane='{cfg.lane}', which has no registered implementation in "
            f"this milestone. Fix: unset NEWSLENS_LANE / NEWSLENS_LANE_"
            f"{cfg.seat.upper()} to use the gpt-4o api default — the Claude "
            f"API lane lands in B2 and the subscription lane in B3."
        )
    return provider


def chat(req: LaneRequest) -> LaneResponse:
    """Dispatch a completion to the request's resolved provider/lane. Raises
    LaneUnavailable (fail-loud) when the lane has no implementation."""
    return _select_provider(req.cfg)(req)


def check_lane(cfg: SeatConfig) -> None:
    """Preflight: raise LaneUnavailable (fail-loud, named fix) if the seat's
    resolved lane has no registered provider. A caller runs this ONCE per step
    BEFORE any transport or retry, so a config error never sleeps, never
    retries, and — the D1 close — never lets one seat's transport run while a
    different seat's lane is what the ledger records: the preflighted seat is
    the seat the ledger attributes and the lane the bytes ride."""
    _select_provider(cfg)


# ---------------------------------------------------------------------------
# Config resolution from env (the B2/B3 plug; behaviour-neutral in B1)
# ---------------------------------------------------------------------------

def resolve_seat(seat: str, env: Optional[Dict[str, str]] = None) -> SeatConfig:
    """The effective SeatConfig after env overrides. With no env set, returns
    the SEATS default (gpt-4o/openai/api) — behaviour unchanged.

    Overrides (all optional; documented in .env.example):
      NEWSLENS_LANE          global lane override (api | subscription)
      NEWSLENS_LANE_<SEAT>   per-seat lane override (wins over the global)

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
    if lane == base.lane:
        return base
    return replace(base, lane=lane)


def fallback_armed(env: Optional[Dict[str, str]] = None) -> bool:
    """NEWSLENS_LANE_FALLBACK=api — the principal-armed opt-in for one fall to
    the api lane when the subscription lane is unavailable. B1 reads/reports
    it; the fall itself needs a second lane (B2/B3)."""
    env = os.environ if env is None else env
    return (env.get("NEWSLENS_LANE_FALLBACK") or "none").strip().lower() == "api"


# ---------------------------------------------------------------------------
# Cost attribution — the shadow ledger keys (JSON, additive, no migration)
# ---------------------------------------------------------------------------

def cost_fields(cfg: SeatConfig, usage: Optional[Dict]) -> Dict:
    """The lane/shadow ledger keys for one billed attempt, added ALONGSIDE the
    existing `{step, attempt, prompt_tokens, completion_tokens, usd}` entry
    (the legacy `usd` stays == usd_charged for back-compat).

    `usage` is the provider-native OpenAI usage dict the cost_sink already
    holds. usd_shadow is computed from the seat's QA-pinned price table;
    usd_charged == usd_shadow on the api lane and 0.0 on the subscription
    lane (B3). Cache-read tokens are recorded but NOT discounted from
    usd_shadow in B1 (so the value equals today's `usd` exactly — no cost
    test moves); B2 applies the cache discount when caching is engineered.
    """
    usage = usage or {}
    pt = usage.get("prompt_tokens") or 0
    ct = usage.get("completion_tokens") or 0
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") if isinstance(details, dict) else 0
    shadow = round(
        pt / 1e6 * cfg.usd_per_mtok_in + ct / 1e6 * cfg.usd_per_mtok_out, 6
    )
    charged = shadow if cfg.lane == "api" else 0.0
    return {
        "model": cfg.model,
        "lane": cfg.lane,
        "cache_read_tokens": cached or 0,
        "usd_shadow": shadow,
        "usd_charged": round(charged, 6),
    }
