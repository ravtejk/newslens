"""newslens diagnose — the readout instrument (milestone 8).

Read-only, $0, offline: computes the day-14/day-30 readouts from the
instrumentation the product has been accumulating (consumption_events in
SQLite + data/generation_log.jsonl). The day-14 diagnostic literally runs
this command; the day-30 falsifier is its first section.

This instrument SELF-CAVEATS. The three recorded caveats (NOTES-M2 items
21a-c, from the M7 gate) print WITH the falsifier number, every time —
a number that needs a footnote must never travel without it:
  a. artifact-file reads bypass capture (UI-only capture was the design
     ruling — reading data/briefings/<date>.md in a terminal is invisible);
  b. the one-page architecture makes "briefing rendered" == "app opened",
     so open-days measures app-opens, per ADR-0010 §3's own definition;
  c. events during the construction period (through CONSTRUCTION_END_UTC)
     are implementer-demo / CoS / QA verification traffic, not principal
     reads. The recorded instance: the disclosed synthetic reads on
     2026-07-05. The usage-window count starts after the cutover.
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import db, paths

# Last calendar day (UTC) of construction. Every consumption event at or
# before this day is org traffic (demos, gate verification, QA probes) —
# not the principal reading their briefing. NOTES-M2 21c records the
# specific known instance; the cutover generalizes it so late construction
# probes can't inflate the usage readout.
CONSTRUCTION_END_UTC = "2026-07-06"

WINDOW_DAYS = 14

_EDITOR_RE = re.compile(r"editor: (\d+) -> (\d+) words \((\d+)% tighter\)")

# Warning-line buckets: mechanical substring classification of the pipeline's
# own disclosure vocabulary. Anything unmatched prints under "other".
_BUCKETS: List[Tuple[str, str]] = [
    ("hedge-ratio", "hedge-ratio warns (editor forensics, 18a)"),
    ("repair", "disclosed deterministic repairs (M3 class)"),
    ("tag shape", "tag-shape tolerance disclosures"),
    ("tolerance", "other tolerance disclosures"),
    ("dedup", "dedup disclosures"),
    ("merged", "cluster merges"),
    ("banned strings", "script banned-string scrubs"),
    ("item window hit", "ingest window cap hits"),
    ("[KNOB", "band/knob advisories (warn-only)"),
    ("framing", "framing-variety advisories (A7)"),
]


def _load_entries() -> Tuple[List[Dict], int]:
    """(parsed entries, malformed-line count)."""
    log = paths.DATA_DIR / "generation_log.jsonl"
    if not log.exists():
        return [], 0
    entries, bad = [], 0
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(e, dict):
            entries.append(e)
        else:
            bad += 1
    return entries, bad


def _events(con) -> List[Dict]:
    try:
        rows = con.execute(
            "SELECT date, kind, occurred_at FROM consumption_events"
            " ORDER BY occurred_at").fetchall()
    except Exception:
        return []
    return [{"date": r["date"], "kind": r["kind"],
             "day": (r["occurred_at"] or "")[:10]} for r in rows]


def _fmt_usd(v: float) -> str:
    return f"${v:.2f}" if v >= 0.10 else f"${v:.3f}"


def run_diagnose(now_utc: Optional[datetime] = None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    out: List[str] = []
    push = out.append

    push(f"NewsLens diagnose · {now_utc.astimezone().strftime('%Y-%m-%d %H:%M %Z')}")
    push("data: consumption_events (SQLite) + data/generation_log.jsonl · "
         "read-only · $0")
    push("")

    # ---------------- The falsifier ----------------
    # M8 gate residual 1: READ-ONLY connection — the instrument must never
    # create or mutate what it measures; a fresh install reads as empty.
    try:
        con = db.connect_readonly()
        try:
            ev = _events(con)
        finally:
            con.close()
    except Exception:
        ev = []  # no DB yet — honest empty readout
    cutoff_day = (now_utc.strftime("%Y-%m-%d"))
    window_start = (now_utc.timestamp() - WINDOW_DAYS * 86400)
    window_start_day = datetime.fromtimestamp(
        window_start, tz=timezone.utc).strftime("%Y-%m-%d")
    # M8 gate residual 5: STRICT lower bound = the 14 days ending today
    # (day-granular twin of events.trailing_open_days' timestamp cutoff;
    # errs conservative — the partial boundary day is excluded, so this
    # can undercount vs events.py, never inflate).
    in_window = [e for e in ev if window_start_day < e["day"] <= cutoff_day]
    open_days = sorted({e["day"] for e in in_window})
    usage_days = sorted({e["day"] for e in in_window
                         if e["day"] > CONSTRUCTION_END_UTC})

    push("THE FALSIFIER — unprompted opens "
         f"(day-30 metric: trailing-{WINDOW_DAYS}-day distinct open days)")
    push(f"  trailing {WINDOW_DAYS} days: {len(open_days)} distinct open "
         f"day(s) — {len(usage_days)} in the usage window, "
         f"{len(open_days) - len(usage_days)} construction-period")
    for day in open_days:
        reads = sum(1 for e in in_window if e["day"] == day and e["kind"] == "read")
        listens = sum(1 for e in in_window if e["day"] == day and e["kind"] == "listen")
        tag = "  [construction — not principal reads]" \
            if day <= CONSTRUCTION_END_UTC else ""
        push(f"    {day}: {reads} read(s), {listens} listen(s){tag}")
    if not open_days:
        push("    no consumption events in the window")
    push("  caveats — recorded at the M7 gate (NOTES-M2 21a-c); they ride "
         "with the number:")
    push("    a. UI-only capture BY DESIGN: reading data/briefings/<date>.md "
         "directly (terminal,")
    push("       editor) bypasses capture entirely. If that becomes the "
         "habit, this undercounts")
    push("       real usage — ask the principal before reading a low number "
         "as abandonment.")
    push("    b. one-page architecture: rendering the page IS opening the "
         "app, so this measures")
    push("       app-opens (ADR-0010 §3's own definition) — not stories "
         "read, not scroll depth.")
    push(f"    c. events through {CONSTRUCTION_END_UTC} are construction "
         "traffic — the disclosed")
    push("       synthetic reads on 2026-07-05 (implementer demo + CoS/QA "
         "verification), not")
    push("       principal reads. The usage window starts after the cutover.")
    if usage_days:
        push(f"  usage-window readout: {len(usage_days)} open day(s) — "
             f"{', '.join(usage_days)}")
    else:
        push("  usage-window readout: no data yet — the window opens after "
             f"{CONSTRUCTION_END_UTC}")
    push("")

    # ---------------- Generation record ----------------
    entries, bad_lines = _load_entries()
    real = [e for e in entries if e.get("date") and e.get("status")]
    samples = [e for e in real if e.get("sample")]
    record = [e for e in real if not e.get("sample")]
    ok = [e for e in record if e.get("status") == "ok"]
    failed = [e for e in record if e.get("status") != "ok"]
    malformed = (len(entries) - len(real)) + bad_lines

    push("GENERATION RECORD (generation_log.jsonl)")
    push(f"  entries: {len(entries)} — record runs {len(record)} "
         f"({len(ok)} ok / {len(failed)} failed) · labeled samples "
         f"{len(samples)} (never the record) · malformed/other {malformed}")
    latest_day = max((e.get("ts", ""))[:10] for e in entries) if entries else "—"
    if latest_day and latest_day <= CONSTRUCTION_END_UTC:
        push(f"  period: all construction (latest {latest_day}); "
             "usage-window runs: none yet")

    priced = [e for e in real if isinstance(e.get("total_usd"), (int, float))]
    if priced:
        total = sum(e["total_usd"] for e in priced)
        latest_priced = priced[-1]["total_usd"]
        push(f"  cost: {_fmt_usd(total)} across {len(priced)} priced runs "
             f"(construction incl. dev loops) · latest full run "
             f"{_fmt_usd(latest_priced)} · audio $0 (kokoro, local)")

    tiered = [e for e in record if e.get("tiers")]
    if tiered:
        counts: Dict[str, int] = {}
        for e in tiered:
            for t in e["tiers"]:
                counts[t] = counts.get(t, 0) + 1
        dist = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        push(f"  tiers (recorded on {len(tiered)} of {len(record)} record "
             f"runs): {dist}")
    else:
        push("  tiers: not yet recorded on any record run (field ships "
             "from late M6)")

    framed = [e for e in record if e.get("framings")]
    if framed:
        fcounts: Dict[str, int] = {}
        for e in framed:
            for f in e["framings"]:
                if f:
                    fcounts[f] = fcounts.get(f, 0) + 1
        dist = " · ".join(f"{k!r} {v}" for k, v in sorted(fcounts.items()))
        push(f"  framings (A7 readout, NOTES 20; on {len(framed)} run(s)): "
             f"{dist} — alternation needs a multi-day read")
    else:
        push("  framings: not yet recorded (A7 field ships from late M6)")

    over_known = [e for e in record if "override_rendered" in e]
    if over_known:
        fired = sum(1 for e in over_known if e.get("override_rendered"))
        push(f"  override: fired in {fired} of {len(over_known)} runs "
             "recording the field (hard cap: 1 of 5 slots)")

    editor_pcts, hedge_warns = [], 0
    bucket_counts: Dict[str, int] = {}
    for e in real:
        # the editor line lives in the entry's own `editor` field (M6+);
        # newer entries ALSO mirror it into warnings — parse the field,
        # skip the mirror, so each pass counts once.
        m = _EDITOR_RE.search(str(e.get("editor") or ""))
        if m:
            editor_pcts.append(int(m.group(3)))
        for w in (e.get("warnings") or []):
            if _EDITOR_RE.search(w):
                continue
            if "hedge-ratio" in w:
                hedge_warns += 1
            for needle, label in _BUCKETS:
                if needle in w:
                    bucket_counts[label] = bucket_counts.get(label, 0) + 1
                    break
            else:
                bucket_counts["other"] = bucket_counts.get("other", 0) + 1
    if editor_pcts:
        n_ed = len(editor_pcts)
        push(f"  editor: {n_ed} pass{'es' if n_ed != 1 else ''} recorded · tightening "
             f"median {int(statistics.median(editor_pcts))}% "
             f"(min {min(editor_pcts)}%, max {max(editor_pcts)}%) · "
             f"hedge-ratio warns: {hedge_warns}"
             + (" — check qualifier stripping" if hedge_warns else ""))
    if bucket_counts:
        push("  disclosure/warning buckets (all entries, construction "
             "noise included):")
        for label, n in sorted(bucket_counts.items(), key=lambda kv: -kv[1]):
            push(f"    {n:>3} × {label}")

    push("")
    push("Interpretation guardrails: construction-period numbers describe "
         "the org building the")
    push("product, not the principal using it. The day-14 read wants the "
         "usage-window lines above")
    push("plus the principal's own account — this instrument counts opens; "
         "it cannot see value.")
    return "\n".join(out)
