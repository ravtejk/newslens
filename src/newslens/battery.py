"""battery.py — the ~07-24 writer-register battery's one-command runner (B4).

The blind battery compares the WRITER seat across model arms (Opus 4.8 vs Fable 5
vs Sonnet 5) on the SAME variant-A narrative prompt — the register-target-spec
prompt (prompts/narrative_variant_a.txt), for a date that ALREADY has a briefing
row (rank + analysis briefs on file). This runner produces one narrative artifact
per arm, plus a per-arm cost/usage/cache manifest, so the artifacts can be read
blind and scored (Data's shape-vs-feel hooks, register-spec §7 checklist).

Design (dispatch B4 item 7 — "design minimal; LIVE calls, principal-executed,
cap-checked, dry-run default"):
  * READ-ONLY on the record: opens the DB via db.connect_readonly() and only
    SELECTs the existing briefing inputs — it can NEVER write the briefing of
    record, mutate the ledger, or refresh rank. Artifacts land in a separate
    battery output tree, never data/briefings/.
  * DRY-RUN DEFAULT: without --run it makes ZERO live LLM calls and ZERO writes.
    It loads the inputs, builds the variant-A prompt, and prints the plan — the
    arms, each arm's estimated cost, the cumulative, and the seat config — so the
    principal sees exactly what a --run would spend BEFORE spending it.
  * PRINCIPAL-EXECUTED / CAP-CHECKED: --run makes the real Opus/Sonnet/Fable
    calls. The battery's TOTAL spend is bounded by BUDGET_CAP_USD_PER_RUN (the
    same guard the pipeline uses): an arm whose estimate would push the running
    total over the cap is SKIPPED and disclosed, never silently spent. Raise the
    cap in .env for more or pricier arms.
  * RETRY SPEND: the cap gate prices each arm's SINGLE pre-call estimate, but a
    live arm can take call_llm's one corrected retry on a malformed/truncated
    draft — so an arm's WORST-CASE spend is ~2x its printed estimate. Size the
    cap (or the arm count) with that headroom in mind.
  * The model swap rides NEWSLENS_MODEL_WRITER (the seam's battery override) — so
    every arm is a single-variable change off the same writer seat (same lane,
    thinking, effort, sampling); only the model string differs.

This module is import-safe and offline-testable (the dry-run path and arg parsing
need no key and no network); scripts/battery is the thin launcher.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import analysis, config, db, generate, llm, paths, ranking

# The default arms — the writer models the ~07-24 blind battery A/Bs. Override
# with --arms. Each is a bare model string passed to NEWSLENS_MODEL_WRITER.
DEFAULT_ARMS = ("claude-opus-4-8", "claude-sonnet-5", "claude-fable-5")

# Per-model prices (USD per MTok in/out) for the battery's OWN estimate and
# real-cost figures. The seam's usd_shadow prices every arm at the writer seat's
# table (Opus $5/$25) because NEWSLENS_MODEL_WRITER swaps ONLY the model string,
# not the prices — so a Fable/Sonnet arm's seam shadow is Opus-priced. This map
# is how the battery reports each arm at ITS real rate (the honest cost), kept
# distinct from the seam shadow in the manifest. Unknown models fall back to the
# writer seat's own prices.
_ARM_PRICES: Dict[str, Tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _arm_prices(model: str) -> Tuple[float, float]:
    w = llm.SEATS["writer"]
    return _ARM_PRICES.get(model, (w.usd_per_mtok_in, w.usd_per_mtok_out))


def _arm_estimate(prompt: str, model: str) -> float:
    """The pre-call estimate for one arm, priced at the ARM's real rate. Same
    ~3.5 chars/token input heuristic as generate._est_cost; the output leg
    prices the full NARRATIVE_MAX_TOKENS ceiling (pessimistic — the real bill is
    thinking + prose, usually less)."""
    pin, pout = _arm_prices(model)
    return (len(prompt) / 3.5 / 1e6) * pin + (
        generate.NARRATIVE_MAX_TOKENS / 1e6) * pout


def _load_narrative_prompt(con, date: str, variant: str) -> Tuple[str, Dict]:
    """Build the variant-A narrative prompt for `date` from the EXISTING record
    (read-only): the same inputs the live narrative pass uses — load_briefing_
    inputs + briefs_by_slot from latest_valid_brief. Raises generate.GenerateError
    if there is no briefing row / no slots (the runner refuses, never fabricates)."""
    inputs = generate.load_briefing_inputs(con, date)
    briefs_by_slot: Dict[int, Optional[Dict]] = {}
    for s in inputs["slots"]:
        n = int(s["slot"])
        doc = analysis.latest_valid_brief(con, date, n)
        if doc:
            briefs_by_slot[n] = doc
    inputs["briefs_by_slot"] = briefs_by_slot
    prompt = generate.build_narrative_prompt(date, variant, inputs)
    return prompt, inputs


def _shape_check(inputs: Dict):
    """A draft validator matching the live narrative pass's contract (one story
    per slot, the JSON object shape) — a caught failure takes call_llm's one
    corrected retry, exactly as the record path does."""
    n_slots = len(inputs["slots"])

    def check(content: str) -> None:
        payload = json.loads(content)
        if not isinstance(payload, dict) or not isinstance(
                payload.get("stories"), list):
            raise ValueError("draft must be a JSON object with a `stories` list")
        if len(payload["stories"]) != n_slots:
            raise ValueError(
                f"{len(payload['stories'])} draft stories for {n_slots} slots")
    return check


def _run_arm(key: str, prompt: str, inputs: Dict, model: str, lane: str,
             out_dir: Path) -> Dict:
    """One live arm: set NEWSLENS_MODEL_WRITER=<model> AND NEWSLENS_LANE_WRITER=
    <lane>, call the writer through the seam (adaptive thinking / effort xhigh —
    the writer seat, only the model + lane swapped), render the prose, and write
    the artifacts. item E (2026-07-17): the lane is a per-arm variable now (the
    writer defaults to subscription), so a lane arm can compare the SAME model on
    api vs subscription — keyed model+lane, never confounded with the model A/B.
    Returns a manifest dict (also written to disk)."""
    prev_model = os.environ.get("NEWSLENS_MODEL_WRITER")
    prev_lane = os.environ.get("NEWSLENS_LANE_WRITER")
    os.environ["NEWSLENS_MODEL_WRITER"] = model
    os.environ["NEWSLENS_LANE_WRITER"] = lane
    sink: List[Dict] = []
    t0 = datetime.now(timezone.utc)
    try:
        content, usage = generate.call_llm(
            key, prompt, "narrative", generate.NARRATIVE_MAX_TOKENS,
            generate.NARRATIVE_TEMPERATURE, True,
            validate=_shape_check(inputs), cost_sink=sink)
    finally:
        for var, prev in (("NEWSLENS_MODEL_WRITER", prev_model),
                          ("NEWSLENS_LANE_WRITER", prev_lane)):
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()

    draft = json.loads(content)
    prose = generate.assemble_narrative(inputs["date"] if "date" in inputs
                                        else "", "A", draft["stories"], inputs)
    pin, pout = _arm_prices(model)
    real_usd = round((usage.get("prompt_tokens", 0) / 1e6) * pin
                     + (usage.get("completion_tokens", 0) / 1e6) * pout, 6)
    # The seam shadow (Opus-priced by the override) — kept for the cross-check.
    # On the subscription lane usd_charged is $0; usd_shadow is always API-priced.
    shadow = llm.cost_fields(llm.resolve_seat("writer", {"NEWSLENS_LANE_WRITER": lane}),
                             usage)
    manifest = {
        "arm": model,
        "lane": lane,
        "usd_charged_seam": shadow["usd_charged"],   # 0.0 on the subscription lane
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cache_read_tokens": usage.get("prompt_tokens_details", {}).get(
            "cached_tokens") if isinstance(usage.get("prompt_tokens_details"),
                                           dict) else 0,
        "cache_creation_tokens": usage.get("cache_creation_tokens", 0),
        "usd_real_at_arm_price": real_usd,
        "usd_shadow_seam": shadow["usd_shadow"],   # Opus-priced (override caveat)
        "elapsed_s": round(elapsed, 1),
        "attempts": len(sink),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "narrative.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "narrative.md").write_text(prose, encoding="utf-8")
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="battery",
        description="Produce per-model narrative artifacts for the blind writer "
                    "battery. Dry-run by default; --run makes LIVE calls.")
    p.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                   help="edition date with an existing briefing row "
                        "(default: today, local)")
    p.add_argument("--arms", default=",".join(DEFAULT_ARMS),
                   help="comma-separated writer model ids to compare "
                        f"(default: {','.join(DEFAULT_ARMS)})")
    p.add_argument("--lanes", default="api",
                   help="comma-separated lanes (api,subscription) to run each arm "
                        "on (default: api — the controlled model-A/B lane). A LANE "
                        "ARM is a paired same-model comparison: pass ONE --arms "
                        "model with --lanes api,subscription. To avoid a lane "
                        "confound, multiple models AND multiple lanes is refused.")
    p.add_argument("--out", default=None, metavar="DIR",
                   help="battery output root (default: <DATA_DIR>/battery)")
    p.add_argument("--variant", default="A", choices=["A", "B"],
                   help="narrative variant prompt (default: A, the live voice; "
                        "the battery measures A's register)")
    p.add_argument("--run", action="store_true",
                   help="make the LIVE Opus/Sonnet/Fable calls (default: dry-run "
                        "— plan + estimates only, ZERO calls, ZERO writes). An "
                        "arm can take one corrected retry, so worst-case spend "
                        "≈ 2x the printed estimate; the cap prices the single "
                        "pre-call estimate")
    args = p.parse_args(argv)

    # The battery is a real, principal-run entrypoint (like cli.main / doctor.
    # main): it reads the real record (DB read-only) and the real sources.yaml to
    # build the prompt, and writes artifacts under DATA_DIR/battery. Sanction the
    # guarded paths for this process (the incident guard, paths.py). A sandboxed
    # run (env overrides set) resolves those overrides regardless — redirection
    # outranks sanction — so the offline tests stay hermetic.
    paths.allow_real_paths()
    config.load_env()
    env = os.environ
    date = args.date or ranking.local_today()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    lanes = [l.strip() for l in args.lanes.split(",") if l.strip()]
    cap = config.budget_cap_usd_per_run(env)
    out_root = Path(args.out) if args.out else (paths.DATA_DIR / "battery")

    # item E (2026-07-17) confound guard: the battery A/Bs ONE variable at a time.
    # Multiple models compare MODELS (one lane); multiple lanes compare LANES (one
    # model). A models x lanes grid confounds the two axes — refuse it (exit 2).
    _bad_lanes = [l for l in lanes if l not in ("api", "subscription")]
    if _bad_lanes:
        print(f"battery: unknown lane(s) {', '.join(_bad_lanes)} — use api and/or "
              "subscription.", file=sys.stderr)
        return 2
    if len(arms) > 1 and len(lanes) > 1:
        print("battery: refused — comparing multiple models AND multiple lanes at "
              "once confounds the model A/B with the lane arm. Run a model A/B "
              "(many --arms, one --lanes) OR a lane arm (one --arms, --lanes "
              "api,subscription), never both.", file=sys.stderr)
        return 2
    # DEF-1 (QA C+E pass): the confound guard above counts LENGTHS, so duplicate
    # inputs slip it and two arms would plan onto ONE <date>/<model>__<lane>/
    # dir — the second silently overwrites the first. A duplicate (model, lane)
    # is a user error; REFUSE exit 2 (the guard's existing grammar), naming it.
    dup_arms = [m for m in dict.fromkeys(arms) if arms.count(m) > 1]
    dup_lanes = [l for l in dict.fromkeys(lanes) if lanes.count(l) > 1]
    if dup_arms or dup_lanes:
        which = ", ".join([f"model {m}" for m in dup_arms]
                          + [f"lane {l}" for l in dup_lanes])
        print(f"battery: refused — duplicate arm input ({which}) would plan two "
              "runs onto one <date>/<model>__<lane>/ artifact dir and silently "
              "overwrite. Each (model, lane) arm must be unique.", file=sys.stderr)
        return 2

    # Read-only: the record is never mutated by the battery. FIX-3 (B4-D4): an
    # absent/unopenable DB (mode=ro on a nonexistent file, or a fresh DATA_DIR)
    # raises sqlite3.OperationalError — refuse cleanly (exit 1, zero transport),
    # never a stack trace, exactly like the missing-briefing-row refusal below.
    try:
        con = db.connect_readonly()
    except sqlite3.OperationalError as exc:
        print(f"battery: refused — cannot open the record read-only ({exc}); run "
              f"`newslens generate` for {date} first", file=sys.stderr)
        return 1
    try:
        prompt, inputs = _load_narrative_prompt(con, date, args.variant)
    except generate.GenerateError as exc:
        print(f"battery: refused — {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    kind = ("lane arm" if len(lanes) > 1 else "model A/B")
    print(f"NewsLens writer battery — {date} (variant {args.variant}) — {kind}")
    print(f"  prompt ~{len(prompt)} chars; budget cap ${cap:.2f}/run")
    print(f"  arms ({len(arms)}): {', '.join(arms)}; lanes: {', '.join(lanes)}")
    w = llm.SEATS["writer"]
    print(f"  writer seat: {w.provider}, thinking={w.thinking}, "
          f"effort={w.effort}, max_tokens={generate.NARRATIVE_MAX_TOKENS}")

    # The arm set is the (model, lane) pairs — a clean N-models x 1-lane A/B or a
    # 1-model x lanes arm (the confound grid was refused above). Cumulative cap
    # gate (dry-run and live share the same arithmetic). item E: the dry-run
    # discloses BOTH lanes' plans + costs — api bills usd_real; subscription is
    # $0 CHARGED (usd_shadow still recorded, the honest compute cost).
    # NAMED DIVERGENCE (gate FIX-1, 2026-07-17): generate's edition cap binds on
    # SHADOW (Onna's law, generate.py:1947 + DECISIONS 2026-07-17) — this gate
    # DELIBERATELY binds on CHARGED dollars instead: the battery is a
    # principal-invoked bounded experiment, so the cap bounds real spend while
    # each subscription arm's shadow is disclosed per-arm (QA-proven no smuggle
    # path — a sub arm's $0 derives from the lane at cost_fields, never from the
    # plan). The semantic is on the ship-ratification list; Onna's law stays
    # edition-scoped.
    cumulative = 0.0
    planned: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str]] = []
    for model in arms:
        for lane in lanes:
            est = _arm_estimate(prompt, model)   # tokens are lane-independent
            charged = 0.0 if lane == "subscription" else est
            cost_note = (f"est ${est:.4f} shadow, $0 CHARGED (subscription)"
                         if lane == "subscription" else f"est ${est:.4f}")
            if cumulative + charged > cap:
                skipped.append((model, lane))
                print(f"    - {model} [{lane}]: {cost_note} -> SKIP (cumulative "
                      f"charged ${cumulative + charged:.4f} would exceed the "
                      f"${cap:.2f} cap)")
                continue
            cumulative += charged
            planned.append((model, lane))
            print(f"    - {model} [{lane}]: {cost_note} "
                  f"(cumulative charged ${cumulative:.4f})")
    print(f"  planned {len(planned)} arm(s), skipped {len(skipped)}; "
          f"est total charged ${cumulative:.4f}")

    if not args.run:
        print("  DRY RUN — no calls made, no files written. Re-run with --run "
              "to execute (api arms need ANTHROPIC_API_KEY; subscription arms "
              "need the claude CLI logged in).")
        return 0

    key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if any(lane == "api" for _, lane in planned) and not key:
        print("battery: --run has api arm(s) that need ANTHROPIC_API_KEY — set it "
              "in .env, then re-run (subscription arms use the claude CLI instead).",
              file=sys.stderr)
        return 1
    # DEF-2 (QA C+E pass): the subscription half of the key gate. An unresolvable
    # CLI on a subscription arm is a CONFIG error, so gate it ONCE upfront (FIX-1
    # philosophy — config errors kill before spend, like the falsifier's --run
    # lane preflight), never per-arm failures in the loop. check_lane is the same
    # binary gate every seat's transport hits; run it on the writer's
    # subscription lane before any arm.
    if any(lane == "subscription" for _, lane in planned):
        try:
            llm.check_lane(llm.resolve_seat(
                "writer", {"NEWSLENS_LANE_WRITER": "subscription"}))
        except llm.LaneUnavailable as exc:
            print(f"battery: --run has subscription arm(s) but the claude CLI is "
                  f"unavailable — {exc}", file=sys.stderr)
            return 1

    run_dir = out_root / date
    print(f"  writing artifacts under {run_dir}/")
    inputs = dict(inputs, date=date)   # assemble_narrative reads inputs['date']
    ok = 0
    for model, lane in planned:
        # item E: artifact dirs keyed model+lane so a lane arm's two runs never
        # overwrite each other and a model A/B stays one-dir-per-model.
        arm_dir = run_dir / f"{model.replace('/', '_')}__{lane}"
        try:
            m = _run_arm(key, prompt, inputs, model, lane, arm_dir)
            print(f"    + {model} [{lane}]: {m['completion_tokens']} out tok, "
                  f"real ${m['usd_real_at_arm_price']:.4f} "
                  f"(charged ${m['usd_charged_seam']:.4f}), "
                  f"cache_read {m['cache_read_tokens']}, {m['elapsed_s']}s "
                  f"-> {arm_dir}/")
            ok += 1
        except Exception as exc:  # noqa: BLE001 — one arm's failure is disclosed
            print(f"    ! {model} [{lane}]: FAILED ({type(exc).__name__}: {exc}) — "
                  "disclosed, other arms continue", file=sys.stderr)
    print(f"battery: {ok}/{len(planned)} arms produced; artifacts under "
          f"{run_dir}/. Read blind; the record was never touched.")
    return 0 if ok else 1


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
