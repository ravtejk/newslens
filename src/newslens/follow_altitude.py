"""follow_altitude.py — NL-17-M1 increment A: the altitude resolver + the
falsifier instrument ("the altitude slice").

WHAT THIS IS (and is NOT). The follow-altitude round (product-4, 2026-07-16)
ruled v1 a HYBRID: at follow time the system proposes ONE follow at the best
altitude, pre-selected and NAMED IN WORDS, with the other rung + "just this
story" one tap away. Kass's pre-registered falsifier gates that shape: dry-run
the auto-altitude pick over the principal's existing followed threads; if it
misidentifies the primary entity in more than ~1 in 5, v1 flips from
pre-selected-default to a BLANK picker.

This increment builds exactly two things and STOPS:
  1. the RESOLVER (resolve_altitude): given a followed thread, emit
     {altitude ∈ entity|storyline, primary_entity, disclosure naming the altitude
     in words, confidence} — TWO rungs only (industry/region rungs are
     hypotheses for the NL-17/18 taxonomy round, not scope);
  2. the FALSIFIER instrument (main / scripts/follow-altitude): dry-run the
     resolver over ALL currently-followed threads, read-only against the DB, and
     spend NOTHING without an explicit --run flag (battery's dry-run-default
     pattern). --run writes a per-thread report the PRINCIPAL reads to render the
     >1-in-5 verdict (there is no ground-truth oracle — the miss count is a human
     read of the picks, which is the whole point of a pre-registered falsifier).

Deliberately OUT OF SCOPE (mockup-gate + falsifier-verdict law): no UI code, no
migration, no schema change, no selection/ranking touch. The resolver's output
is a REPORT, never edition state and never a selection weight — NL-17 acceptance
(a)-(d) (one-vocabulary XOR, MOVES-never-copies, no-stacking, A6 steering stays
OFF behind the NL-14 gate) are untouched here: v1 mints one concept in one
vocabulary from day one because it stores NOTHING this increment. The two-rung
set (ALTITUDES) is the no-new-vocabulary tripwire — a third rung cannot enter
without a code change the QA pin flips red on.

SEAM (ADR-0014/0015/0016 law): the resolver's model call goes through the
`follow_altitude` seat in llm.SEATS (Haiku 4.5; the ONE seat whose code default
is the API lane — RESOLVER LANE FIX 2026-07-20 — because it is interactive and
reader-waiting: ~1.2s api vs a ~48s claude -p resolve; subscription is the
registered fall-over / airbag, forced via NEWSLENS_LANE_FOLLOW_ALTITUDE=
subscription). ONE effective_seat resolution
per call, threaded through the gate + both transport attempts + every cost row
(the B3-D6 fix); prompt-shaped JSON rides the corrected-retry law (rank's twin).
Read-only DB via db.connect_readonly; the instrument self-sanctions real paths
(paths.allow_real_paths) exactly like cli.main/doctor.main/battery.

Import-safe and offline-testable: the dry-run path and arg parsing need no key
and no network; scripts/follow-altitude is the thin launcher.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config, db, llm, memory_core, paths, ranking

FOLLOW_ALTITUDE_UA = (
    "NewsLens/0.1 (personal news briefing prototype; follow-altitude resolver)"
)

SEAT = "follow_altitude"

# v1 ships ENTITY + STORYLINE only (principal ruling 2026-07-17, DECISIONS "THE
# STORYLINE CORRECTION" — storyline is the thread/topic tier per product-4: the
# ongoing story at proper altitude, never the headline string). INDUSTRY and
# REGION stay deferred to the NL-17/18 taxonomy round as unproven. This tuple is
# the no-new-vocabulary tripwire: _validate REJECTS any altitude outside it, so a
# third rung cannot enter without a code change the QA pin flips red on.
ALTITUDES: Tuple[str, ...] = ("entity", "storyline")
CONFIDENCES: Tuple[str, ...] = ("high", "medium", "low")

RESOLVER_MAX_TOKENS = 400        # a ~4-field JSON object; headroom, never an essay
RESOLVER_TEMPERATURE = 0.0       # deterministic classification (Haiku sampling=True)

# The corrected-retry augmentation (generate.RETRY_CORRECTION_* twin): a rejected
# draft's retry ECHOES the exact validator ValueError so attempt 2 is steered at
# the rule that failed — never a byte-identical re-POST (rank run-28 precedent).
# Anchored to the ORIGINAL user block below, never compounding.
_RETRY_CORRECTION_PREFIX = "CORRECTION — your previous answer was rejected: "
_RETRY_CORRECTION_SUFFIX = (
    ". Fix exactly that and nothing else. Output ONLY the single JSON object "
    "described above — no prose, no code fences."
)


class AltitudeError(RuntimeError):
    """The resolver failed for one thread after one corrected retry. In the
    falsifier this is disclosed for that thread; the other threads continue."""


@dataclass(frozen=True)
class ThreadInput:
    """One followed thread's resolver input: the title, plus WHATEVER ledger/
    state context exists (both optional — a freshly-followed thread is title-only,
    which is exactly the follow-time case the picker must handle well)."""
    thread_id: Optional[int]
    topic: str
    state_text: Optional[str] = None
    recent_activity: Optional[str] = None


@dataclass(frozen=True)
class AltitudeResult:
    thread_id: Optional[int]
    topic: str
    altitude: str
    primary_entity: str
    disclosure: str
    confidence: str
    attempts: int
    usd_shadow: float
    usd_charged: float
    lane: str
    # NL-17-M1b build rider (design seam, 2026-07-18): the OTHER rung named in
    # words, compact-qualifier grammar ("Volkswagen job cuts" / "Volkswagen
    # (company)") — feeds the follow-line's "Instead" act and the low-confidence
    # picker's second option. A prompt-COMPATIBLE extension: an M1a-shaped answer
    # that omits it stays valid and this is "" — the UI then renders the lawful
    # worded fallback ("the ongoing story" / "the company"), never a bare symbol.
    alt_label: str = ""


# ---------------------------------------------------------------------------
# The compact qualifier grammar (M1b) — split a resolver disclosure/alt_label
# ("Volkswagen (company)" / "Volkswagen job cuts" / "Redemption Gates (fund-
# withdrawal story)") into (name, class) so the render can style the name bold
# and the class in the quiet parenthetical. This is the inverse of the grammar
# the prompt emits — a TOTAL, deterministic split of a controlled "name" or
# "name (class)" token, NOT prose-parsing (0018's dumb-render concern was
# authored prose in a JSON blob; this is a formatting split of a two-part name).
# ---------------------------------------------------------------------------

def split_qualifier(qname: str) -> Tuple[str, str]:
    """('Volkswagen (company)') -> ('Volkswagen', 'company'); a bare name ->
    (name, ''). Splits on the LAST ' (' that closes the string with ')'."""
    q = (qname or "").strip()
    if q.endswith(")") and " (" in q:
        head, _, tail = q.rpartition(" (")
        return head.strip(), tail[:-1].strip()
    return q, ""


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------

def _system_law() -> str:
    """The resolver's stable law prompt (prompts/follow_altitude.txt) — the
    system prefix (cache-eligible; the per-thread material is the volatile user
    block). Prompts are code (ENGINEERING.md): a versioned file read, never an
    inline string."""
    return (paths.PROMPTS_DIR / "follow_altitude.txt").read_text(encoding="utf-8")


def _thread_block(thread: ThreadInput) -> str:
    """The volatile per-thread user message. Title always; ledger/state context
    only when it exists."""
    lines = [f"THREAD TITLE: {thread.topic}"]
    if (thread.state_text or "").strip():
        lines.append(f"CURRENT STATE: {thread.state_text.strip()}")
    if (thread.recent_activity or "").strip():
        lines.append(f"MOST RECENT DEVELOPMENT: {thread.recent_activity.strip()}")
    lines.append("")
    lines.append("Return the JSON object for THIS thread.")
    return "\n".join(lines)


def _validate(content: str) -> Dict:
    """Parse + shape-check the resolver's JSON. Raises ValueError on any
    violation (the corrected-retry trigger). Pins the v1 contract:
      * altitude ∈ ALTITUDES — two rungs only (the no-new-vocabulary law);
      * primary_entity a non-empty string;
      * disclosure a non-empty string (Kass's clause: the altitude must be named
        in words — a default confirmed by one tap with NO disclosure line is
        silent inference rebuilt);
      * confidence ∈ CONFIDENCES.
    """
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("expected a single JSON object")
    altitude = payload.get("altitude")
    if altitude not in ALTITUDES:
        raise ValueError(
            f"altitude must be one of {list(ALTITUDES)} (two rungs only), "
            f"got {altitude!r}")
    primary = payload.get("primary_entity")
    if not isinstance(primary, str) or not primary.strip():
        raise ValueError("primary_entity must be a non-empty string")
    disclosure = payload.get("disclosure")
    if not isinstance(disclosure, str) or not disclosure.strip():
        raise ValueError(
            "disclosure must be a non-empty string naming the altitude in words")
    confidence = payload.get("confidence")
    if confidence not in CONFIDENCES:
        raise ValueError(
            f"confidence must be one of {list(CONFIDENCES)}, got {confidence!r}")
    # alt_label — the OTHER rung named in words (M1b). OPTIONAL by design: a
    # prompt-compatible extension, so an omitted/blank value is NOT a rejection
    # (M1a back-compat) — it degrades to "" and the UI renders the worded
    # fallback. A present value must be a real string (never a bare symbol);
    # anything else is coerced to the fallback rather than failing the whole
    # pick (the alternative is a convenience act, not the disclosure Kass's
    # clause guards).
    alt = payload.get("alt_label")
    alt_label = alt.strip() if isinstance(alt, str) else ""
    return {"altitude": altitude, "primary_entity": primary.strip(),
            "disclosure": disclosure.strip(), "confidence": confidence,
            "alt_label": alt_label}


def resolve_altitude(thread: ThreadInput, *, api_key: str = "",
                     env: Optional[Dict[str, str]] = None,
                     seat: Optional[Tuple["llm.SeatConfig", Optional[str]]] = None,
                     cost_sink: Optional[List[Dict]] = None,
                     retry_transport: bool = True) -> AltitudeResult:
    """Resolve one followed thread's altitude. ONE call + ONE corrected retry,
    then AltitudeError.

    `retry_transport` (NL-17-M1b FIX LOOP 2 R3): whether the provider TIMEOUT
    class consumes the retry. The BATCH falsifier keeps the M1a default (True):
    an unattended run rides out one transient by retrying the original. The
    INTERACTIVE follow-line (_api_follow_resolve) passes False — a reader is
    WAITING, so a timeout must degrade DIRECTLY after one window (≤ one timeout +
    epsilon), never spend a second 12s window + backoff pinning "Deciding…" at
    ~25s. Scoped to the timeout class only; every OTHER transport shape still
    retries once on both paths (they fail fast). The degrade copy and this-story
    commit are byte-identical either way.

    The seam resolution is THREADED (the generate/ranking one-resolution-per-run
    pattern, B3-D6): the seat is resolved ONCE — here via llm.effective_seat, or
    supplied pre-resolved by the falsifier run so an entire run rides one
    resolution — and the SAME cfg drives the gate, both transport attempts, and
    every cost row, so a mid-run binary flap can never fork the lane the ledger
    records from the lane the bytes ride.

    `cost_sink` (money honesty, generate.call_llm's law): every attempt that
    reaches the provider and returns usage records its full lane/shadow keys here
    BEFORE validation can reject it — a malformed attempt that still billed is
    never lost from the run's money record.
    """
    if seat is None:
        cfg, fb_reason = llm.effective_seat(SEAT, env)   # fail-loud gate
    else:
        cfg, fb_reason = seat
    system = _system_law()
    user = _thread_block(thread)
    next_user = user
    last_error = "unknown"
    backoff = 1.0
    for attempt in (1, 2):
        try:
            resp = llm.chat(llm.LaneRequest(
                cfg=cfg, prompt=next_user, temperature=RESOLVER_TEMPERATURE,
                max_tokens=RESOLVER_MAX_TOKENS, json_mode=True,
                user_agent=FOLLOW_ALTITUDE_UA, api_key=api_key,
                # The stable law rides `system` (cache_control on the anthropic
                # lane; folded inline on subscription — llm.py owns both). The
                # openai offline-test seam url is inert on the anthropic lane.
                system=system, url=ranking.OPENAI_CHAT_URL))
            raw = resp.raw
            usage = raw.get("usage") or {}
            fields = llm.cost_fields(cfg, usage, fallback_reason=fb_reason)
            if cost_sink is not None:
                entry = {"seat": SEAT, "attempt": attempt,
                         "prompt_tokens": usage.get("prompt_tokens"),
                         "completion_tokens": usage.get("completion_tokens"),
                         "usd": fields["usd_charged"]}
                entry.update(fields)
                cost_sink.append(entry)
            choice = raw["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError(
                    f"answer truncated at the resolver token cap "
                    f"({RESOLVER_MAX_TOKENS})")
            parsed = _validate(choice["message"]["content"])
            return AltitudeResult(
                thread_id=thread.thread_id, topic=thread.topic,
                altitude=parsed["altitude"],
                primary_entity=parsed["primary_entity"],
                disclosure=parsed["disclosure"], confidence=parsed["confidence"],
                attempts=attempt, usd_shadow=fields["usd_shadow"],
                usd_charged=fields["usd_charged"], lane=fields["lane"],
                alt_label=parsed["alt_label"])
        except urllib.error.HTTPError as exc:
            detail = ranking._http_error_detail(exc)
            who = "Anthropic" if cfg.provider == "anthropic" else "OpenAI"
            if exc.code in (401, 403):
                raise AltitudeError(
                    f"{who} rejected the key (HTTP {exc.code}"
                    + (f"; {detail}" if detail else "") + ")") from exc
            if exc.code == 429:
                last_error = (f"rate limited (HTTP 429"
                              f"{'; ' + detail if detail else ''})")
                backoff = ranking._retry_after_seconds(exc)
            elif exc.code >= 500:
                last_error = f"HTTP {exc.code}" + (f" ({detail})" if detail else "")
            else:
                raise AltitudeError(
                    f"{who} rejected the resolver call (HTTP {exc.code}"
                    + (f"; {detail}" if detail else "") + ")") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # malformed output / failed validation / truncation — the corrected
            # retry path. Echo the exact failure; anchor to the ORIGINAL user
            # block so a correction can never compound.
            last_error = f"invalid resolver output ({exc})"
            next_user = (user + "\n\n" + _RETRY_CORRECTION_PREFIX + str(exc)
                         + _RETRY_CORRECTION_SUFFIX)
        except TimeoutError as exc:
            # The provider TIMEOUT class (llm.py raises the builtin TimeoutError
            # when claude -p / the api call exceeds the seat's window). BATCH
            # (retry_transport=True): transport-shaped — retry the ORIGINAL, the
            # M1a semantics. INTERACTIVE (retry_transport=False): the reader is
            # waiting; the timeout must NOT consume the retry — degrade fires
            # DIRECTLY after one window (a second window + backoff is the ~25s
            # miss). Only the timeout class opts out; other transport shapes fall
            # through to the generic retry below.
            last_error = f"{type(exc).__name__}: {exc}"
            if not retry_transport:
                raise AltitudeError(
                    f"altitude resolution timed out for {thread.topic!r} "
                    f"(interactive, one window): {last_error}") from exc
        except Exception as exc:  # noqa: BLE001 — transport-shaped: retry ORIGINAL
            last_error = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
        if attempt == 1:
            time.sleep(backoff)
    raise AltitudeError(
        f"altitude resolution failed for {thread.topic!r} after one retry: "
        f"{last_error}")


# ---------------------------------------------------------------------------
# Read-only DB: the followed threads + their (optional) context
# ---------------------------------------------------------------------------

def followed_threads(con: sqlite3.Connection) -> List[Dict]:
    """The reader's currently-followed threads — memory rows that are NOT
    dismissed. status IN ('active','dormant') is the exact non-dismissed set
    (0006 migrated stale->dormant, dismissed->dismissed_user), the SAME predicate
    memory_core.threads_awaiting_baseline uses. Read-only; oldest id first
    (stable). Returns [{thread_id, topic, status}]."""
    rows = con.execute(
        "SELECT id, topic, status FROM memory "
        "WHERE status IN ('active', 'dormant') ORDER BY id").fetchall()
    return [{"thread_id": r["id"], "topic": r["topic"], "status": r["status"]}
            for r in rows]


def load_thread_input(con: sqlite3.Connection, row: Dict) -> ThreadInput:
    """Build the resolver input for a followed thread: title plus whatever
    ledger/state context exists (best-effort, read-only; both degrade to None on
    an empty-ledger cold-start thread — the common follow-time case). All reads
    go through memory_core's read-only helpers; table-absence on an older DB
    degrades to title-only rather than dying."""
    tid = row["thread_id"]
    state_text: Optional[str] = None
    recent: Optional[str] = None
    try:
        st = memory_core.latest_state(con, tid)
        if st and (st.get("state_text") or "").strip():
            state_text = st["state_text"].strip()
    except sqlite3.OperationalError:
        pass
    try:
        ledger = memory_core.ledger_for_thread(con, tid)
        if ledger and (ledger[-1].get("what_happened") or "").strip():
            recent = ledger[-1]["what_happened"].strip()
    except sqlite3.OperationalError:
        pass
    return ThreadInput(thread_id=tid, topic=row["topic"],
                       state_text=state_text, recent_activity=recent)


# ---------------------------------------------------------------------------
# Cost estimate (dry-run) — priced at the resolved seat's shadow table
# ---------------------------------------------------------------------------

def _estimate_usd(cfg: "llm.SeatConfig", system: str, thread: ThreadInput) -> float:
    """Per-thread usd_shadow estimate for the dry-run plan. ~3.5 chars/token
    input (generate._est_cost's ratio); the output leg prices the full
    RESOLVER_MAX_TOKENS ceiling (pessimistic — the real object is far shorter).
    Priced at the SEAT's table (Haiku $1/$5) regardless of lane — the shadow
    figure the budget cap binds on."""
    prompt_chars = len(system) + len(_thread_block(thread))
    return round((prompt_chars / 3.5 / 1e6) * cfg.usd_per_mtok_in
                 + (RESOLVER_MAX_TOKENS / 1e6) * cfg.usd_per_mtok_out, 6)


# ---------------------------------------------------------------------------
# The falsifier instrument (dry-run default; --run principal-executed)
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="follow-altitude",
        description="Dry-run the follow-altitude auto-picker over every "
                    "currently-followed thread (Kass's pre-registered "
                    "falsifier). Dry-run by default; --run makes LIVE calls.")
    p.add_argument("--out", default=None, metavar="DIR",
                   help="report output root (default: <DATA_DIR>/follow_altitude)")
    p.add_argument("--run", action="store_true",
                   help="make the LIVE resolver calls (default: dry-run — the "
                        "thread list + per-thread cost estimate only, ZERO "
                        "calls, ZERO writes). --run spends the disclosed cents "
                        "and writes the per-thread report.")
    args = p.parse_args(argv)

    # A real, principal-run entrypoint (cli.main/doctor.main/battery pattern):
    # it reads the real record (DB read-only). Sanction the guarded paths for
    # this process; a sandboxed run (NEWSLENS_DATA_DIR set) resolves the override
    # regardless — redirection outranks sanction — so the offline tests stay
    # hermetic.
    paths.allow_real_paths()
    config.load_env()
    env = os.environ
    cap = config.budget_cap_usd_per_run(env)
    out_root = Path(args.out) if args.out else (paths.DATA_DIR / "follow_altitude")

    # Read-only: the record is NEVER mutated. An absent/unopenable DB refuses
    # cleanly (exit 1, zero transport), never a stack trace (battery's FIX-3).
    try:
        con = db.connect_readonly()
    except sqlite3.OperationalError as exc:
        print(f"follow-altitude: refused — cannot open the record read-only "
              f"({exc}); run `newslens generate` first", file=sys.stderr)
        return 1
    try:
        followed = followed_threads(con)
        inputs = [load_thread_input(con, r) for r in followed]
    finally:
        con.close()   # closed BEFORE any network call — the DB is never held open

    # The seat to display + price. resolve_seat honors any env override (so the
    # principal sees the lane a --run would actually use) WITHOUT gating the
    # binary — the dry-run must work even if `claude` is not installed.
    cfg = llm.resolve_seat(SEAT, env)
    system = _system_law()

    by_status: Dict[str, int] = {}
    for r in followed:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    status_summary = ", ".join(f"{k}={v}" for k, v in sorted(by_status.items()))

    print("NewsLens follow-altitude falsifier (the altitude slice)")
    print(f"  followed threads: {len(followed)}"
          + (f" ({status_summary})" if status_summary else ""))
    print(f"  resolver seat: {cfg.provider}/{cfg.lane} lane, model {cfg.model}, "
          f"max_tokens={RESOLVER_MAX_TOKENS}; budget cap ${cap:.2f}/run")

    if not followed:
        print("  no followed threads — nothing to resolve. (Follow a thread, or "
              "run `newslens memory-baseline --all` context first.)")
        return 0

    # Cumulative cap gate (dry-run and --run share the arithmetic). At cents this
    # never fires; the guard is honest and matches the battery precedent.
    cumulative = 0.0
    planned: List[ThreadInput] = []
    skipped: List[str] = []
    for ti in inputs:
        est = _estimate_usd(cfg, system, ti)
        if cumulative + est > cap:
            skipped.append(ti.topic)
            print(f"    - {ti.topic!r}: est ${est:.5f} -> SKIP (cumulative "
                  f"${cumulative + est:.5f} would exceed the ${cap:.2f} cap)")
            continue
        cumulative += est
        planned.append(ti)
        ctx = []
        if ti.state_text:
            ctx.append("state")
        if ti.recent_activity:
            ctx.append("ledger")
        ctx_note = f" (+{'/'.join(ctx)} context)" if ctx else " (title only)"
        print(f"    - {ti.topic!r}{ctx_note}: est ${est:.5f} "
              f"(cumulative ${cumulative:.5f})")
    print(f"  planned {len(planned)} thread(s), skipped {len(skipped)}; "
          f"est total usd_shadow ${cumulative:.5f}")
    charged_note = ("$0 charged (subscription lane; shadow-priced above)"
                    if cfg.lane == "subscription"
                    else f"~${cumulative:.5f} charged (api lane)")
    print(f"  a --run would spend: {charged_note}")

    if not args.run:
        print("  DRY RUN — no calls made, no files written. Re-run with --run to "
              "execute the falsifier and write the report.")
        return 0

    # --run: gate the lane ONCE (fail-fast + the one run-scoped resolution every
    # per-thread call rides — the B3-D6 one-resolution-per-run law).
    try:
        run_seat = llm.effective_seat(SEAT, env)
    except llm.LaneUnavailable as exc:
        print(f"follow-altitude: --run refused — {exc}", file=sys.stderr)
        return 1
    run_cfg, _fb = run_seat
    api_key = (env.get("OPENAI_API_KEY") or "").strip()   # inert on the anthropic lane

    run_date = ranking.local_today()
    out_dir = out_root / run_date
    print(f"  writing the report under {out_dir}/ (record untouched, read-only)")
    cost_sink: List[Dict] = []
    results: List[AltitudeResult] = []
    failures: List[Tuple[str, str]] = []
    conf_counts: Dict[str, int] = {}
    for ti in planned:
        try:
            res = resolve_altitude(ti, api_key=api_key, env=env, seat=run_seat,
                                   cost_sink=cost_sink)
        except Exception as exc:  # noqa: BLE001 — one thread's failure is disclosed
            failures.append((ti.topic, f"{type(exc).__name__}: {exc}"))
            print(f"    ! {ti.topic!r}: FAILED ({type(exc).__name__}: {exc}) — "
                  "disclosed, other threads continue", file=sys.stderr)
            continue
        results.append(res)
        conf_counts[res.confidence] = conf_counts.get(res.confidence, 0) + 1
        print(f"    + {ti.topic!r} -> [{res.altitude}] {res.primary_entity} "
              f"(conf {res.confidence}, {res.attempts} attempt(s), "
              f"${res.usd_shadow:.5f} shadow)")
        print(f"        disclosure: {res.disclosure}")

    total_shadow = round(sum(e["usd_shadow"] for e in cost_sink), 6)
    total_charged = round(sum(e["usd_charged"] for e in cost_sink), 6)
    report = {
        "generated_at": ranking.local_today(),
        "seat": {"provider": run_cfg.provider, "lane": run_cfg.lane,
                 "model": run_cfg.model},
        "followed_total": len(followed),
        "resolved": len(results),
        "failed": len(failures),
        "confidence_counts": conf_counts,
        "usd_shadow_total": total_shadow,
        "usd_charged_total": total_charged,
        "results": [asdict(r) for r in results],
        "failures": [{"topic": t, "error": e} for t, e in failures],
        "cost_attempts": cost_sink,
        "verdict_note": (
            "Kass's falsifier: count the PRIMARY-ENTITY misses by hand. "
            ">1 in 5 wrong -> flip v1 from pre-selected default to a blank "
            "picker. Low-confidence picks are the ones to scrutinise first."),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"follow-altitude: {len(results)}/{len(planned)} threads resolved, "
          f"{len(failures)} failed; confidence {conf_counts}; "
          f"usd_shadow ${total_shadow:.5f}, usd_charged ${total_charged:.5f}.")
    print(f"  report: {out_dir}/report.json")
    print("  VERDICT IS A HUMAN READ: count primary-entity misses; >1 in 5 -> "
          "blank picker (product-4 falsifier). The record was never touched.")
    return 0 if results else 1


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
