# ADR-0014: The provider seam (B1) — llm.py behind the three call sites

**Status:** accepted (depth-architecture build, milestone B1)
**Date:** 2026-07-16
**Spec:** Engineering council transcript
workspace/debates/2026-07-16--newslens--engineering-3.md §5.1/§5.6;
DECISIONS.md 2026-07-16 "depth architecture rulings" + "APPROVED".

## Context

The depth architecture flips seats from GPT-4o onto Claude (B2 API lane, B3
`claude -p` subscription lane) and adds new seats. The transport + retry + cost
law lived three times — `generate.call_llm` / `generate._chat`,
`ranking.call_llm_validated` / `ranking._post_chat`,
`analysis.call_analysis_model` / `analysis._analysis_chat` — each carrying
hard-won behavioral law (corrected retry, per-attempt cost_sink, append-only
artifacts). A per-file model flip would fork that law further. B1 introduces one
seam so B2/B3 are a plug, not surgery — with ZERO behavior change this
milestone (every seat still GPT-4o; the existing suite green, unchanged, is the
acceptance bar).

## Decisions

### 1. `src/newslens/llm.py` is a leaf module owning transport + lane + cost

It imports nothing from generate/ranking/analysis (those import `llm`), so there
is no cycle. It holds the `SeatConfig` schema, the `SEATS` table (code
constants — the one-constant-seam precedent generalised to one row per seat),
the lane interface (`LaneRequest → LaneResponse` with `Usage` + cost
attribution), the provider registry, env-based lane resolution, and the shadow
ledger helper. Pure stdlib (urllib) — the deliberate zero-SDK posture holds.

### 2. The three transports keep their signatures and delegate their bodies

`_chat` / `_post_chat` / `_analysis_chat` are the suite's monkeypatch targets
(250+ stubs on `_chat` alone), so they keep their exact signatures and delegate
to `llm.chat(...)`. Each caller's retry/validation orchestration is UNTOUCHED —
the seam owns only transport + provider selection. Requests are byte-faithful:
model/timeout from the resolved `SeatConfig`, temperature/max_tokens/json_mode
passed through, the historical User-Agent per caller, and the endpoint injected
via `LaneRequest.url` so the established offline-test seam
(`ranking.OPENAI_CHAT_URL`, patched at a loopback fake server) keeps working
without touching any test.

### 3. Seats express the CURRENT stack as their default

Every seat in `SEATS` is `openai` / `gpt-4o` / `api`, with timeouts matching
today exactly (rank & analyst 90s, the writer family 120s). A guard test pins
this so B2's model/lane flips are deliberate, never accidental. `synthesis` is
declared (no live call site until B6) so the table is the whole roster the
design named.

### 4. Fail-loud lane selection; provider registry keyed on (provider, lane)

The registry key is `openai` for the api lane and `provider:lane` otherwise, so
B2 registers `anthropic:api` and B3 `anthropic:subscription` without touching
dispatch. A seat resolved (via `NEWSLENS_LANE` / `NEWSLENS_LANE_<SEAT>`) to a
lane with no registered provider raises `LaneUnavailable` naming the fix — never
a silent wrong-lane call (DECISIONS.md: fail-loud default). `NEWSLENS_LANE_
FALLBACK=api` is read/reported now; the fall itself needs a second lane
(B2/B3). A lane owns its own env/credentials (`LaneRequest` carries the
credential; a provider decides its use) so B3 can strip `ANTHROPIC_API_KEY` from
its subprocess.

### 5. Shadow ledger keys, additive, no migration

`llm.cost_fields(cfg, usage)` adds `{model, lane, cache_read_tokens, usd_shadow,
usd_charged}` alongside the existing `{step, attempt, prompt_tokens,
completion_tokens, usd}` cost_sink entry (legacy `usd` retained ==
usd_charged). `usd_shadow` is always computed from the seat's QA-pinned price
table; `usd_charged` == shadow on the api lane, 0.0 on the subscription lane
(B3). Budget caps bind on shadow in both lanes. Cache-read tokens are recorded
but NOT discounted from shadow in B1 (so the value equals today's `usd`
exactly — no cost test moves); B2 applies the discount when caching is
engineered. `briefings.token_cost` is free-form JSON — additive keys, no
migration.

## Alternatives considered

- **Unify the three retry laws into one `complete()`** (transcript §5.1's fuller
  shape): rejected for B1 — the three laws genuinely differ (analysis has a
  blind retry with no cost_sink; the others corrected retry), so unifying is a
  behavioral change that would violate the green-unchanged bar. The seam owns
  transport; retry-law unification is deferred (and may never be worth it).
- **`llm` reads `ranking.OPENAI_CHAT_URL` directly:** rejected — creates an
  import cycle. The endpoint is injected via the request instead.

## Consequences / known gaps (for the gate)

- `memory_core._default_state_chat` (STATE_MODEL=gpt-4o) is a FOURTH transport
  site of identical shape, outside both the three named signatures and the
  six-seat table. B1 does NOT route it. **Gate ruling (2026-07-16, BINDING on
  B2's milestone contract):** the state/memory seat JOINS the seam in B2 —
  SEATS row + check_lane gate + shadow-ledger keys; "memory_core
  byte-unchanged" stops being an acceptance property after B2; the state
  seat's post-B3 default lane is a spend path and goes in B2's principal
  checkpoint. A spend path outside the seam forks the cost dashboard and,
  post-B3, silently bills OpenAI while the run claims the subscription lane —
  the D1 class at org level.
- The analyst path (`call_analysis_model`) uses a float cost accumulator, not a
  cost_sink, so it does not yet carry the lane/shadow keys. B2 migrates it when
  it flips analyst → Haiku.
- **Gate FIX-1 — the pipeline-level fail-loud asymmetry, disclosed.** Rank and
  writer lane misconfigs kill the run raw at the CLI boundary (LaneUnavailable
  is structurally unswallowable by the degrade arms). An analyst-only misconfig
  (`NEWSLENS_LANE_ANALYST`) is instead swallowed per-slot by `analyze_slot`'s
  broad except (analysis.py:1638-1642) and per-baseline by
  `generate_thread_baseline`'s broad except: every such outcome is a disclosed
  $0 "failed" row, the edition ships with depth absent, and the doctor FAILs
  the lane line — honest degradation, but degradation, not death. B2 (which
  migrates the analyst cost path) owns the ruling on whether the analyst joins
  gate-kills-run semantics.
- `generate._chat` transports narrative/editor/script all as the `writer` seat
  (identical gpt-4o in B1); B4 must thread the per-step seat through when the
  writer seat moves to Opus. Marked at the call site. **Gate FIX-2 — the B4
  rider is BINDING:** when `_chat` transports on the per-step seat_cfg, (a) the
  GenerateError-wrapped carve-out becomes structurally impossible, (b) the
  exhaustive sweep's `residual_possible` acceptance arm is DELETED — flipped so
  a seat-threading regression FAILS the sweep rather than passing it — and
  (c) the stray 1.0s pre-fail backoff sleep on that path dies with it.
