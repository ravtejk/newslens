"""The memory core — the moat build (NL-63 M1).

The THREAD is the remembered object. Two per-thread records, DB-only (they do
NOT enter memory.md's sync surface — engineering ruling 2026-07-10):

  * thread_deltas — the append-only delta LEDGER ("how we got here"). One
    two-clause SIGNIFICANCE entry per edition that MOVED the thread
    (advances|reverses only). Written at generation time from the analyst's
    already-validated arc field — Pax's economy: the field the pipeline
    computed and discarded every run becomes the write path, ~$0 new spend.
    NO BACKFILL, ever (Sten's law — the refusal is the trust case).

  * thread_state — the standing STATE ("where this stands"), a ≤5-sentence
    paragraph under Content's WRITE LAW: rewritten ONLY on advance/reverse;
    every sentence cited to a dated edition; regenerated from LEDGER + today's
    material, NEVER from the prior state text (Kass/Nova's anti-photocopier
    construction); diff-logged; stale-but-honest on failure.

Three renders, all fed from here:
  * the Today arc line (then -> now -> difference), gated by Sten's kill-test
    AS CODE and Kass's reversion law AS CODE;
  * the deep view's "story so far" timeline (deterministic from the ledger);
  * the Following dossier state card (standing state + last-delta line).

This module owns the mechanics. The renders live in server.py; the write path
is wired from generate.py after the analysis pass.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# The state-rewrite LLM seam (the ONLY new spend; per thread, advance/reverse
# days only). Behind a one-constant seam like ANALYSIS_MODEL / WRITER_MODEL —
# documented one-diff fallback rung: gpt-4o-mini.
# ---------------------------------------------------------------------------
STATE_MODEL = "gpt-4o"
STATE_USD_IN_PER_MTOK = 2.50
STATE_USD_OUT_PER_MTOK = 10.00
STATE_MAX_TOKENS = 400
STATE_TIMEOUT_S = 60
STATE_MAX_SENTENCES = 5           # Content write law: <=5 sentences
STATE_UA = "NewsLens/0.1 (single-user; thread-state rewrite)"

VERDICTS_THAT_MOVE = ("advances", "reverses")   # merely-matches writes nothing

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
               "Oct", "Nov", "Dec"]
# Both full names and 3-letter abbreviations resolve — the ledger and the
# retro-mock write dates as "Jul 10"; a state may write "July 10". Longest
# alternation first so "march" wins over "mar".
_MONTH_NUM: Dict[str, int] = {}
for _i, _full in enumerate(_MONTHS, 1):
    _MONTH_NUM[_full] = _i
    _MONTH_NUM[_full[:3]] = _i
_MONTH_ALT = "|".join(sorted(_MONTH_NUM, key=len, reverse=True))
_MONTH_DAY_RE = re.compile(r"\b(" + _MONTH_ALT + r")\.?\s+(\d{1,2})\b", re.I)
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_WS_RE = re.compile(r"\s+")


class StateRejected(ValueError):
    """Hard-reject class for a state rewrite (fabrication surface). The prior
    state stays, rendered stale-but-honest — never a fabricated regeneration."""


# ---------------------------------------------------------------------------
# Small date helpers (local — no cross-module coupling)
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def is_calendar_date(s: str) -> bool:
    try:
        datetime.strptime((s or "")[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def human_date(iso: str) -> str:
    """'2026-07-05' -> 'Jul 5'. Falls back to the raw string if unparseable."""
    try:
        d = datetime.strptime((iso or "")[:10], "%Y-%m-%d")
    except ValueError:
        return iso or ""
    return f"{_MONTH_ABBR[d.month - 1]} {d.day}"


_PAREN_RE = re.compile(r"\(([^)]*)\)")


# M1 gate F (year-anchor, DEADLINE-class): a state paragraph's human-form cite
# ("(Jul 10)") carries no year, so it must resolve against the thread's ACTUAL
# edition dates — never a hardcoded base year. From 2027 a `base_year=2026`
# assumption mismatched every human-form cite against the ledger and bricked the
# state surface. Resolution is now year-agnostic: a "Month D" form resolves to
# the unique year Y with Y-MM-DD in the resolvable set; a form matching MULTIPLE
# years is AMBIGUOUS and fails closed (the safe direction — reject, never guess).
def _resolve_cites(text: str, resolvable: set) -> Tuple[set, set, set]:
    """Resolve every PARENTHETICAL edition cite in `text` against `resolvable`
    (a set of ISO edition dates). ISO cites ('2026-07-10') resolve to themselves
    (year explicit). Human-form cites ('Jul 10', 'July 10') resolve YEAR-
    AGNOSTICALLY to the unique year present in `resolvable`. A bare in-prose date
    (a scheduled talks date, a toll figure's day) is CONTENT, not a citation, and
    is ignored — only parentheticals date a sentence (the write law).

    Returns (resolved, unresolved, ambiguous):
      * resolved   — ISO dates that map into `resolvable`;
      * unresolved — ISO cites not in `resolvable` (fabrication class) and human
                     forms whose (month, day) matches NO year (labeled 'md:MM-DD');
      * ambiguous  — human forms whose (month, day) matches >1 year in
                     `resolvable` — the fail-closed class ('MM-DD')."""
    resolved: set = set()
    unresolved: set = set()
    ambiguous: set = set()
    by_md: Dict[Tuple[str, str], set] = {}
    for iso in resolvable:
        by_md.setdefault((iso[5:7], iso[8:10]), set()).add(iso[:4])
    for m in _PAREN_RE.finditer(text or ""):
        blob = m.group(1)
        for im in _ISO_RE.finditer(blob):
            iso = f"{im.group(1)}-{im.group(2)}-{im.group(3)}"
            (resolved if iso in resolvable else unresolved).add(iso)
        for dm in _MONTH_DAY_RE.finditer(blob):
            mon = _MONTH_NUM[dm.group(1).lower()]
            mm, dd = f"{mon:02d}", f"{int(dm.group(2)):02d}"
            years = by_md.get((mm, dd), set())
            if len(years) == 1:
                resolved.add(f"{next(iter(years))}-{mm}-{dd}")
            elif len(years) > 1:
                ambiguous.add(f"{mm}-{dd}")
            else:
                unresolved.add(f"md:{mm}-{dd}")
    return resolved, unresolved, ambiguous


def _has_edition_cite(text: str, resolvable: set) -> bool:
    """True when `text` carries at least one parenthetical date cite in any
    parseable form (resolved, ambiguous, or unresolved) — the per-sentence
    warn asks 'is there a cite at all', not 'does it resolve'."""
    resolved, unresolved, ambiguous = _resolve_cites(text, resolvable)
    return bool(resolved or unresolved or ambiguous)


# ---------------------------------------------------------------------------
# Thread identity
# ---------------------------------------------------------------------------

def resolve_thread_id(con: sqlite3.Connection, topic: str) -> Optional[int]:
    """memory.id for a thread name (case-insensitive), or None. Dismissed
    threads never take a delta (explicit intent won; the record is dormant)."""
    row = con.execute(
        "SELECT id FROM memory WHERE lower(topic) = lower(?)"
        " AND status != 'dismissed_user'", (topic,)).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# 1. The delta ledger — Pax's economy (persist the validated arc)
# ---------------------------------------------------------------------------

@dataclass
class DeltaWriteReport:
    written: List[Dict] = field(default_factory=list)     # {thread, date, verdict}
    skipped: List[str] = field(default_factory=list)      # reasons
    # The threads whose ledger THIS PASS MOVED — i.e. a delta was NEWLY written
    # this pass. Drives the (paid) state rewrite. Live-contact fix #4: an
    # idempotent skip (delta already on file) NO LONGER moves the thread, so a
    # repeat pass on an already-written edition rewrites no state and bills
    # nothing — the self-limiting property that makes the "any persisted run
    # writes the moat" gate (incl. --no-refresh record runs, re-runs) safe.
    moved_thread_ids: List[int] = field(default_factory=list)  # newly-written -> state rewrite

    def summary(self) -> str:
        return (f"ledger: {len(self.written)} delta(s) written"
                + (f", {len(self.skipped)} skipped" if self.skipped else ""))


def _arc_two_clause(arc: Dict) -> Tuple[str, str]:
    """The SIGNIFICANCE two-clause shape (Uma): (what_happened, significance).
    New arcs emit both; a legacy arc carrying only `what_changed` degrades to
    (what_changed, '') — it still records, but with no significance clause."""
    happened = (arc.get("what_happened") or arc.get("what_changed") or "").strip()
    signif = (arc.get("significance") or "").strip()
    return happened, signif


def _external_cites(arc: Dict) -> List[str]:
    """The arc's cites that anchor OUTSIDE our own prose (S/R/C) — Rook's loop
    mitigation: history stays anchored to external sources, not just P-keys."""
    cites = []
    for c in arc.get("cites") or []:
        if isinstance(c, str):
            k = c.strip().strip("[]")
            if k and k[0] in "SRC":
                cites.append(k)
    return cites


def write_deltas_for_edition(
    con: sqlite3.Connection, date: str, briefing_id: Optional[int],
    briefs_by_slot: Dict[int, Optional[Dict]], slots: List[Dict],
) -> DeltaWriteReport:
    """Pax's economy: each analyzed slot's VALIDATED arc becomes a ledger entry
    for every thread the slot matched. Gates (the trust story):
      * verdict in {advances, reverses} — merely-matches writes nothing;
      * two-clause shape present (what_happened at minimum);
      * >=1 EXTERNAL cite (S/R/C) — a P-only arc is self-reference, refused;
      * idempotent — one entry per (thread, edition), so re-generation never
        double-writes (append-only: we never UPDATE, we just don't duplicate).
    Returns a report; moved_thread_ids feeds the state-rewrite pass.
    """
    report = DeltaWriteReport()
    slot_by_n = {int(s["slot"]): s for s in slots}
    for n, doc in sorted((briefs_by_slot or {}).items()):
        brief = (doc or {}).get("brief") if isinstance(doc, dict) else None
        # briefs_by_slot may hold the {header, brief} doc or the bare brief
        if brief is None and isinstance(doc, dict) and "arc" in doc:
            brief = doc
        if not brief:
            continue
        arc = brief.get("arc")
        slot = slot_by_n.get(int(n)) or {}
        threads = [t for t in (slot.get("matched_memory") or []) if t]
        if not isinstance(arc, dict):
            if threads:
                report.skipped.append(f"slot {n}: no arc (thread(s) {threads} did not move)")
            continue
        verdict = str(arc.get("delta") or "").strip()
        if verdict not in VERDICTS_THAT_MOVE:
            report.skipped.append(f"slot {n}: verdict {verdict!r} does not move the ledger")
            continue
        happened, signif = _arc_two_clause(arc)
        if not happened:
            report.skipped.append(f"slot {n}: arc carries no 'what happened' clause")
            continue
        # BUG-28: a NEW-shape arc (what_happened present) MUST carry its
        # significance clause — a one-clause entry ('strikes occurred' with an
        # empty significance) is the banned changelog class (Uma's two-clause
        # rule). Only a LEGACY arc (what_changed, no what_happened) degrades to
        # an empty significance, and only so archived briefs stay replayable.
        is_new_shape = bool((arc.get("what_happened") or "").strip())
        if is_new_shape and not signif:
            report.skipped.append(
                f"slot {n}: new-shape arc has no significance clause — a "
                "one-clause changelog entry is refused (Uma's two-clause rule)")
            continue
        ext = _external_cites(arc)
        if not ext:
            report.skipped.append(
                f"slot {n}: arc cites no external source (S/R/C) — self-reference "
                "refused (Rook's loop guard)")
            continue
        cites_json = json.dumps(_all_cites(arc), ensure_ascii=False)
        brief_id = _latest_valid_brief_id(con, date, int(n))
        for topic in threads:
            tid = resolve_thread_id(con, topic)
            if tid is None:
                report.skipped.append(f"slot {n}: thread {topic!r} not resolvable")
                continue
            if _delta_exists(con, tid, date, happened, int(n)):
                # Live-contact fix #4: an idempotent skip does NOT move the
                # thread. The ledger is unchanged, so its state already reflects
                # this delta — re-firing the paid state rewrite on every re-run
                # was pure waste (and would re-bill a --no-refresh record re-run,
                # breaking the gate's self-limiting guarantee). moved_thread_ids
                # now means "the ledger MOVED this pass" (newly-written only).
                report.skipped.append(f"{topic}: delta for {date} already on file (idempotent)")
                continue
            with con:
                con.execute(
                    "INSERT INTO thread_deltas (thread_id, briefing_id, brief_id,"
                    " edition_date, slot, verdict, what_happened, significance,"
                    " cites_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (tid, briefing_id, brief_id, date, int(n), verdict, happened,
                     signif, cites_json))
            report.written.append({"thread": topic, "thread_id": tid,
                                    "date": date, "verdict": verdict})
            if tid not in report.moved_thread_ids:
                report.moved_thread_ids.append(tid)
    return report


def _all_cites(arc: Dict) -> List[str]:
    out = []
    for c in arc.get("cites") or []:
        if isinstance(c, str) and c.strip():
            out.append(c.strip().strip("[]"))
    return out


def _delta_exists(con: sqlite3.Connection, thread_id: int, date: str,
                  what_happened: str, slot: Optional[int] = None) -> bool:
    """Idempotency by WRITING SLOT (primary) with an event-clause fallback.

    BUG-27: a sanctioned-split day writes TWO distinct same-day developments for
    one thread (the strikes at slot 1 AND the diplomatic track at slot 3) — so a
    (thread, edition) key alone made the second delta impossible. Keying on the
    slot lets both land (they come from DIFFERENT slots — one arc per brief per
    slot) while a regeneration re-analyzes the SAME slots and dedups cleanly.

    M1 gate F (regen-dedup): keying on what_happened ALONE only caught IDENTICAL
    regenerations; a same-day full refresh that REPHRASED the arc slipped a
    duplicate delta onto the ledger (and thus the timeline/state prompt). The
    slot key closes that hole — a rephrased re-run of slot N still matches
    (thread, date, N). The what_happened clause stays as the fallback for
    seeds/legacy rows whose slot is NULL (never regenerated, so no rephrase
    risk), preserving BUG-27's guarantee for them."""
    if slot is not None and con.execute(
            "SELECT 1 FROM thread_deltas WHERE thread_id = ? AND edition_date = ?"
            " AND slot = ? LIMIT 1", (thread_id, date, slot)).fetchone():
        return True
    return con.execute(
        "SELECT 1 FROM thread_deltas WHERE thread_id = ? AND edition_date = ?"
        " AND what_happened = ? LIMIT 1",
        (thread_id, date, what_happened)).fetchone() is not None


def _latest_valid_brief_id(con: sqlite3.Connection, date: str,
                           slot: int) -> Optional[int]:
    row = con.execute(
        "SELECT id FROM analysis_briefs WHERE date = ? AND slot = ?"
        " AND status = 'valid' ORDER BY id DESC LIMIT 1", (date, slot)).fetchone()
    return row["id"] if row else None


def ledger_for_thread(con: sqlite3.Connection, thread_id: int,
                      before_date: Optional[str] = None) -> List[Dict]:
    """The thread's dated ledger, oldest first. `before_date` (exclusive)
    returns only PRIOR coverage — the arc line's 'then' half and the
    thread-scoped P-material both need history strictly before today."""
    if before_date:
        rows = con.execute(
            "SELECT * FROM thread_deltas WHERE thread_id = ? AND edition_date < ?"
            " ORDER BY edition_date, id", (thread_id, before_date)).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM thread_deltas WHERE thread_id = ?"
            " ORDER BY edition_date, id", (thread_id,)).fetchall()
    return [dict(r) for r in rows]


def _ledger_integrity(entries: List[Dict]) -> Tuple[bool, str]:
    """Kass's reversion law, checkable: an entry is corrupt if it lacks a
    real edition date, an event clause, or a parseable cite list. A single
    corrupt entry reverts the thread's arc to a bare citation line."""
    for e in entries:
        if not is_calendar_date(e.get("edition_date", "")):
            return False, f"entry has a non-calendar edition date {e.get('edition_date')!r}"
        if not (e.get("what_happened") or "").strip():
            return False, "entry has no 'what happened' clause"
        try:
            cites = json.loads(e.get("cites_json") or "[]")
            if not isinstance(cites, list):
                raise ValueError
        except ValueError:
            return False, "entry's cites are unparseable"
    return True, "ok"


# ---------------------------------------------------------------------------
# 4. The Today arc render — then -> now -> difference + kill-test + reversion
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset("""
the a an and or but of to in on at for with from by as is are was were be been
being it its this that these those has have had will would could may might can
not no over under into out up down about after before while during amid its his
her their our your they them then today than more most much very just also so
""".split())


def _salient_units(text: str) -> List[str]:
    """The kill-test's checkable units of a past fact: numbers, and distinctive
    content words (len>=5, not stopwords). Deterministic and conservative —
    under-counts (misses a fact) rather than over-claims (fabricates memory).

    BUG-22: units are normalized on their EDGES symmetrically with the haystack
    (see _absent_from) — a sentence-final period fused onto a number ('12.') and
    a possessive suffix ('khamenei's') are tokenizer artifacts, never distinct
    facts, and must not read as 'absent' when today's story states the bare form.
    Internal punctuation (a decimal point) is preserved."""
    units: List[str] = []
    for tok in re.findall(r"\d[\d,\.]*", text or ""):
        t = tok.replace(",", "").rstrip(".")   # drop sentence-final period; keep internal decimals
        if t:
            units.append(t)
    for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", text or ""):
        lw = re.sub(r"'s?$", "", w.lower())     # strip possessive 's / trailing apostrophe
        lw = lw.strip("'-")                       # strip any edge apostrophes/hyphens
        if len(lw) >= 5 and lw not in _STOPWORDS:
            units.append(lw)
    seen, out = set(), []
    for u in units:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _absent_from(units: List[str], today_text: str) -> List[str]:
    # BUG-22: normalize the haystack on the same edges the units were normalized
    # on (commas and apostrophes stripped) so the comparison is symmetric —
    # 'khamenei' (from 'khamenei's') matches today's 'Khamenei's' either way.
    hay = (today_text or "").lower().replace(",", "").replace("'", "")
    return [u for u in units if u not in hay]


@dataclass
class ArcLine:
    kind: str                 # "arc" | "reverted" | None-never (absent -> None returned)
    text: str
    prior_date: str = ""      # navigable prior edition (openEdition), when present
    disclosure: str = ""      # reversion / integrity note


def render_today_arc(con: sqlite3.Connection, thread_id: int, topic: str,
                     today_text: str, today_date: str) -> Optional[ArcLine]:
    """Sten's kill-test AS CODE, then -> now -> difference, moving-day fix
    (lead with the then-sentences), Kass's reversion law AS CODE.

    Returns None (renders NOTHING) when the thread is day-one, has no prior
    coverage, or the line would tell you nothing (every past unit already in
    today's story). Returns a reverted bare-citation ArcLine on a ledger-
    integrity failure. Otherwise the composed arc line.
    """
    all_entries = ledger_for_thread(con, thread_id)
    prior = [e for e in all_entries if e["edition_date"] < today_date]
    today_entries = [e for e in all_entries if e["edition_date"] == today_date]
    if not prior:
        return None                       # day-one thread gets NO arc, ever
    # BUG-24: integrity examines the ENTIRE ledger, not just the lexically-prior
    # slice — a corrupt edition_date that sorts AFTER today ('garbage-date')
    # otherwise lands in neither prior nor today and never gets checked, letting
    # a normal arc render over a corrupt record. The reversion verdict must not
    # depend on where the garbage happens to sort.
    ok, why = _ledger_integrity(all_entries)
    if not ok:
        last = prior[-1]["edition_date"]
        return ArcLine(
            kind="reverted",
            text=f"Still following {topic} — last covered {human_date(last)}.",
            prior_date=last if is_calendar_date(last) else "",
            disclosure=f"the thread's continuity line is showing a bare citation "
                       f"because its record failed an integrity check ({why})")
    # Kill-test: the line must carry >=1 concrete past fact ABSENT from today.
    # BUG-23: the units are those of the clause the line will actually RENDER
    # (the 'then' — significance when present, else what_happened), never the
    # union of both clauses. A novel fact in an unrendered clause must never
    # license a rendered clause the reader already read in today's story.
    last_prior = prior[-1]
    then = last_prior.get("significance") or last_prior["what_happened"]
    absent = _absent_from(_salient_units(then), today_text)
    if not absent:
        return None                       # tells-me-nothing: suppress

    prior_hd = human_date(last_prior["edition_date"])
    now = today_entries[0]["what_happened"] if today_entries else ""
    diff = today_entries[0].get("significance", "") if today_entries else ""
    n = len([e for e in all_entries if e["edition_date"] <= today_date])
    texture = f"{_ordinal(n)} entry on this thread." if n >= 2 else ""

    # Moving-day fix (retro-mock §4): lead with the then (the state's past)
    # before today's turn. Deterministic then -> now -> difference.
    line = f"When we last covered this ({prior_hd}), {_decap_article(then).rstrip('.')}."
    if now:
        line += f" Today, {_decap_article(now).rstrip('.')}"
        line += f" — {_decap_article(diff).rstrip('.')}." if diff else "."
    if texture:
        line += f" {texture}"
    return ArcLine(kind="arc", text=line,
                   prior_date=last_prior["edition_date"]
                   if is_calendar_date(last_prior["edition_date"]) else "")


_LEAD_ARTICLES = frozenset((
    "The", "A", "An", "This", "That", "These", "Those", "It", "One", "Some"))


def _decap_article(s: str) -> str:
    """Lowercase the leading word ONLY when it is a determiner/article — so a
    clause folds mid-sentence ('The dispute' -> 'the dispute') without ever
    lowercasing a proper noun ('Iran', 'U.S.' stay capitalized)."""
    s = (s or "").strip()
    if not s:
        return s
    first = s.split(None, 1)[0]
    if first in _LEAD_ARTICLES:
        return s[0].lower() + s[1:]
    return s


def _ordinal(n: int) -> str:
    return {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth",
            6: "Sixth", 7: "Seventh"}.get(n, f"{n}th")


# ---------------------------------------------------------------------------
# 5. The "story so far" timeline — deterministic from the ledger
# ---------------------------------------------------------------------------

def timeline_rows(con: sqlite3.Connection, thread_id: int,
                  through_date: Optional[str] = None) -> List[Dict]:
    """Dated ledger rows for the deep view's flagship section, oldest first.
    `through_date` (inclusive) bounds it; never-re-lede is the caller's job
    (the deep view stops the timeline before today's own page)."""
    entries = ledger_for_thread(con, thread_id)
    if through_date:
        entries = [e for e in entries if e["edition_date"] <= through_date]
    out = []
    for e in entries:
        out.append({
            "date": e["edition_date"],
            "human": human_date(e["edition_date"]),
            "what_happened": e["what_happened"],
            "significance": e.get("significance", ""),
            "briefing_id": e.get("briefing_id"),
            "verdict": e.get("verdict", ""),
        })
    return out


# ---------------------------------------------------------------------------
# 3. Thread-scoped P-material — the thread's OWN record replaces the generic
#    two-edition dump (Content's P1-cite fix; Rook's external anchoring)
# ---------------------------------------------------------------------------

def prior_for_slot(con: sqlite3.Connection, date: str, slot: Dict,
                   generic_prior: List[Dict]) -> List[Dict]:
    """The analyst's P-material for a slot. When the slot's threads carry a
    record (ledger and/or state), P becomes the thread's OWN prior coverage —
    dated ledger lines + standing state — replacing the generic 4KB narrative
    dumps. When no thread has a record yet (the honest cold-start / no-thread
    story), the generic prior stands unchanged (non-regressive transition).
    """
    scoped: List[Dict] = []
    for topic in (slot.get("matched_memory") or []):
        tid = resolve_thread_id(con, topic)
        if tid is None:
            continue
        text = thread_record_text(con, tid, topic, before_date=date)
        if not text:
            continue
        entries = ledger_for_thread(con, tid, before_date=date)
        last = entries[-1]["edition_date"] if entries else date
        scoped.append({"date": last, "text": text, "thread": topic})
    return scoped or generic_prior


def thread_record_text(con: sqlite3.Connection, thread_id: int, topic: str,
                       before_date: Optional[str] = None) -> str:
    """The thread's own record as prior-coverage prose for the analyst: its
    dated ledger lines + its standing state. Labeled 'per our prior coverage'
    so a P-only claim can never be laundered as external background."""
    entries = ledger_for_thread(con, thread_id, before_date=before_date)
    # BUG-30: the analyst's prior-coverage bound is strictly-before for BOTH the
    # ledger (edition_date <) and the state (as_of_date <) — a same-day
    # regeneration must never feed run-1's as-of-today state back as run-2's
    # "prior coverage" (the P1-cite self-reference loop this milestone kills).
    state = latest_state(con, thread_id, before_date=before_date, strict=True)
    if not entries and not state:
        return ""
    lines = [f"PER OUR PRIOR COVERAGE OF THIS THREAD ({topic}):"]
    if state:
        lines.append(f"Standing state (as of {human_date(state['as_of_date'])}): "
                     f"{state['state_text']}")
    for e in entries:
        signif = f" — {e['significance']}" if e.get("significance") else ""
        lines.append(f"{human_date(e['edition_date'])}: {e['what_happened']}{signif}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. The standing state — Content's WRITE LAW, anti-photocopier, stale-honest
# ---------------------------------------------------------------------------

def latest_state(con: sqlite3.Connection, thread_id: int,
                 before_date: Optional[str] = None,
                 strict: bool = False) -> Optional[Dict]:
    """Newest state row for the thread (versioned; newest wins). `before_date`
    bounds the as-of date: INCLUSIVE (<=) by default — the render contract, a
    state 'as of today' is today's render — or STRICTLY-before (<) when
    strict=True (BUG-30: the analyst's prior-coverage path must never read a
    state synthesized from today's own delta on a same-day regeneration)."""
    if before_date:
        op = "<" if strict else "<="
        row = con.execute(
            "SELECT * FROM thread_state WHERE thread_id = ? AND as_of_date "
            + op + " ? ORDER BY id DESC LIMIT 1",
            (thread_id, before_date)).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM thread_state WHERE thread_id = ?"
            " ORDER BY id DESC LIMIT 1", (thread_id,)).fetchone()
    return dict(row) if row else None


_ABBR_PROTECT = (("U.S.A.", "U∙S∙A∙"), ("U.S.", "U∙S∙"),
                 ("U.N.", "U∙N∙"), ("U.K.", "U∙K∙"),
                 ("E.U.", "E∙U∙"))


def _sentences(text: str) -> List[str]:
    """Sentence split that survives domain abbreviations — 'U.S.' / 'U.N.' are
    not sentence ends. Protect them, split on real terminators, restore."""
    t = text or ""
    for a, b in _ABBR_PROTECT:
        t = t.replace(a, b)
    out = []
    for s in re.split(r"(?<=[.!?])\s+", t.strip()):
        for a, b in _ABBR_PROTECT:
            s = s.replace(b, a)
        if s.strip():
            out.append(s.strip())
    return out


def validate_state(state_text: str, ledger_dates: set,
                   edition_dates: Optional[set] = None) -> Tuple[str, List[str]]:
    """The write law, checkable. HARD-REJECT (StateRejected) the fabrication
    class: a sentence cited to a date that resolves to NO ledger entry (a past
    THIS thread never moved on). Returns (clean, warnings); an over-long
    paragraph warns (Content's <=5-sentence cap) but does not reject — length
    is editorial, a fabricated cite is a trust breach.

    M3 cites-fork decision (carried from the M2 gate; DECISIONS 2026-07-14):
    LEDGER-RESOLVED-ONLY. thread_state.cites_json persists ledger-resolved cites
    only — rewrite_state resolves the clean text against the thread's ledger
    dates alone (below, `cites = _resolve_cites(clean, ledger_dates)[0]`), and
    it already calls this with edition_dates=set(). Acceptance is narrowed to
    MATCH that persistence: a cite must resolve to a date THIS thread moved on
    (a ledger date). An edition date that is not a ledger date is the BUG-25
    fabrication class (some other edition ran that day, but this thread did not
    move) — it is rejected here rather than accepted-then-silently-dropped by
    cites_json. The wider `resolvable = ledger|edition` was headroom no consumer
    ever used: M3's first render consumers of receipts (The numbers / Unresolved
    / the In-Brief view) read BRIEF-level source-key cites, not this surface, so
    nothing needs the wider set. `edition_dates` is retained inert (every caller
    passes an empty set) so the QA call sites keep compiling; a follow-up may
    drop the parameter once those sites are updated."""
    text = (state_text or "").strip()
    if not text:
        raise StateRejected("empty state paragraph")
    resolvable = set(ledger_dates)
    resolved, unresolved, ambiguous = _resolve_cites(text, resolvable)
    if not (resolved or unresolved or ambiguous):
        # A parenthetical that carries digits but parses to no date form is a
        # cite we cannot read (e.g. 'Sept 10' — only 'Sep'/'September' parse);
        # that is a different diagnosis from a dateless parenthetical
        # ('(no editions)') or no cite at all. Both fail closed — the safe
        # direction — but say so accurately.
        dateish = any(re.search(r"\d", m.group(1))
                      for m in _PAREN_RE.finditer(text))
        if dateish:
            raise StateRejected(
                "state's parenthetical cite carries no date NewsLens can read "
                "(accepted forms: 'Jul 10', 'July 10', '2026-07-10') — fails "
                "closed")
        raise StateRejected("state carries no parenthetical edition cite — the "
                            "write law requires every sentence trace to a dated "
                            "edition, e.g. '(Jul 10)'")
    # M1 gate F (year-anchor): a human-form cite matching >1 year in the record
    # is ambiguous — fail closed rather than guess a year (the DEADLINE-class
    # fix: never silently pick 2026).
    if ambiguous:
        raise StateRejected(
            "state cite(s) resolve to more than one year in this thread's record "
            f"— ambiguous, fails closed: {', '.join(sorted(ambiguous))} "
            "(pin the year, e.g. '(2027-07-10)')")
    if unresolved:
        # ISO cites not in the record, and human forms matching NO edition —
        # both the fabrication class (a past the record never published). A
        # human-form miss ('md:07-08') renders as the reader wrote it ('Jul 8');
        # an ISO miss keeps its explicit year.
        def _name(u: str) -> str:
            if u.startswith("md:"):
                mm, dd = u[3:].split("-")
                return f"{_MONTH_ABBR[int(mm) - 1]} {int(dd)}"
            return human_date(u)
        bad = sorted(_name(u) for u in unresolved)
        raise StateRejected(
            "state cites date(s) with no ledger entry — fabrication "
            f"class: {', '.join(bad)}")
    warnings: List[str] = []
    # BUG-26: the write law says EVERY sentence traces to a dated edition. The
    # paragraph-level cite check above is the hard floor (fabrication class);
    # a sentence carrying NO parenthetical cite riding in on a cited neighbor is
    # a fabrication lane with no receipt — WARN it (Editor's-eye class, like the
    # length cap), not reject: the retro-mock's own state ends with an uncited
    # render-trailer sentence, so reject would fail the shipped quality bar.
    for sent in _sentences(text):
        if not _has_edition_cite(sent, resolvable):
            snippet = sent if len(sent) <= 70 else sent[:67] + "..."
            warnings.append(
                f"sentence carries no dated edition cite: {snippet!r} — every "
                "sentence must trace to a dated edition (Editor's eye)")
    n = len(_sentences(text))
    if n > STATE_MAX_SENTENCES:
        warnings.append(f"state runs {n} sentences over the {STATE_MAX_SENTENCES}"
                        "-sentence cap (Content write law) — Editor's eye")
    return text, warnings


def _state_diff(prior_text: str, new_text: str, prior_as_of: str) -> Dict:
    """Write law (c): diff-logged. Sentence-set diff vs the prior state."""
    prior = set(_sentences(prior_text))
    new = set(_sentences(new_text))
    return {"from_as_of": prior_as_of,
            "added": [s for s in _sentences(new_text) if s not in prior],
            "removed": [s for s in _sentences(prior_text) if s not in new]}


def estimate_state_usd(prompt: str) -> float:
    return (len(prompt) / 4 / 1e6 * STATE_USD_IN_PER_MTOK
            + STATE_MAX_TOKENS / 1e6 * STATE_USD_OUT_PER_MTOK)


def _default_state_chat(key: str, prompt: str) -> Tuple[Dict, float]:
    """Real state-rewrite call on the STATE_MODEL seam. One retry, then raises
    (the caller degrades stale-but-honest). Cost accumulates every paid
    attempt (money-honesty)."""
    import urllib.request
    body = {"model": STATE_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2, "max_tokens": STATE_MAX_TOKENS,
            "response_format": {"type": "json_object"}}
    total = 0.0
    last: Exception = RuntimeError("unreachable")
    import time
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json",
                         "User-Agent": STATE_UA})
            with urllib.request.urlopen(req, timeout=STATE_TIMEOUT_S) as resp:
                payload = json.load(resp)
            usage = payload.get("usage") or {}
            total += (usage.get("prompt_tokens", 0) / 1e6 * STATE_USD_IN_PER_MTOK
                      + usage.get("completion_tokens", 0) / 1e6 * STATE_USD_OUT_PER_MTOK)
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError(f"truncated at {STATE_MAX_TOKENS} tokens")
            return json.loads(choice["message"]["content"]), total
        except Exception as exc:  # noqa: BLE001 — one retry for the whole class
            last = exc
            if attempt == 1:
                time.sleep(1.0)
    # BUG-32: both attempts may have billed real usage before failing (e.g. the
    # model answered and the response tripped the truncation guard) — carry the
    # accrued total on the raised exception so rewrite_state records the spend
    # instead of silently discarding it (BUG-6 money-honesty class).
    try:
        last.usd_spent = total
    except Exception:  # noqa: BLE001 — best-effort; never mask the real failure
        pass
    raise last


def render_state_prompt(topic: str, date: str, entries: List[Dict],
                        prompt_template: str) -> str:
    """Regenerated from the LEDGER + today (never the prior state — the
    anti-photocopier construction). The prompt sees only dated ledger lines."""
    lines = []
    for e in entries:
        signif = f" — {e['significance']}" if e.get("significance") else ""
        lines.append(f"- ({e['edition_date']}) {e['what_happened']}{signif}")
    ledger_block = "\n".join(lines) or "(no ledger entries — day-one thread)"
    out = prompt_template
    for k, v in {"topic": topic, "date": date, "ledger": ledger_block}.items():
        out = out.replace("{" + k + "}", v)
    return out


@dataclass
class StateRewriteResult:
    thread_id: int
    topic: str
    outcome: str              # written | stale | rejected | skipped-budget | failed
    detail: str = ""
    cost_usd: float = 0.0


def rewrite_state(con: sqlite3.Connection, thread_id: int, topic: str,
                  date: str, briefing_id: Optional[int], openai_key: str,
                  prompt_template: str, remaining_usd: float,
                  chat: Optional[Callable] = None) -> StateRewriteResult:
    """Content's WRITE LAW, end to end. LLM write on a trust surface:
      * regenerated from LEDGER + today, NEVER the prior state (photocopier);
      * every sentence cited to a dated edition; cites must resolve to ledger
        entries (hard-reject on fabrication class);
      * diff-logged; versioned (append-only row);
      * stale-but-honest on ANY failure — the prior state stays, no new row,
        the render discloses the staleness.
    """
    res = StateRewriteResult(thread_id=thread_id, topic=topic, outcome="failed")
    chat = chat or _default_state_chat
    entries = ledger_for_thread(con, thread_id)          # full ledger incl today
    if not entries:
        # M1 gate F3: day-one is NOT a budget event — its own label so
        # diagnose's outcome aggregation never conflates the two.
        res.outcome = "skipped-no-ledger"
        res.detail = "no ledger — nothing to synthesize (day-one)"
        return res
    prompt = render_state_prompt(topic, date, entries, prompt_template)
    est = estimate_state_usd(prompt)
    if est > remaining_usd:
        res.outcome = "skipped-budget"
        res.detail = (f"state estimate ${est:.4f} exceeds remaining ${remaining_usd:.4f}"
                      " — prior state kept, stale-but-honest")
        return res
    try:
        raw, cost = chat(openai_key, prompt)
    except Exception as exc:  # noqa: BLE001 — degrade stale-but-honest, never raise
        # BUG-32 (money honesty, BUG-6 class): a failed call may still have paid
        # for one or more attempts — the raised exception carries the accrued
        # total; record it even though the call ultimately failed.
        res.cost_usd = float(getattr(exc, "usd_spent", 0.0) or 0.0)
        res.outcome = "stale"
        res.detail = f"state call failed ({type(exc).__name__}: {exc}) — prior state kept"
        return res
    res.cost_usd = cost
    # BUG-31: the model author is an adversary; a non-string state field
    # degrades stale-but-honest (analysis._require_str precedent), never an
    # AttributeError (int.strip) escaping a paid validation and killing the run.
    state_text = raw.get("state") if isinstance(raw, dict) else None
    if not isinstance(state_text, str):
        res.outcome = "rejected"
        res.detail = ("state rewrite returned a non-string 'state' field "
                      f"({type(state_text).__name__}) — prior state kept, "
                      "stale-but-honest")
        return res
    # BUG-25: cites resolve against THIS thread's LEDGER dates only — never all
    # editions. A cite to a date this thread never moved on is the backfill
    # fabrication class even if some other edition ran that day (the prompt's
    # own rule 3: cite ONLY dates in the ledger). Today is always a ledger date
    # when a rewrite fires, so no legitimate state loses a cite.
    ledger_dates = {e["edition_date"] for e in entries}
    try:
        clean, warnings = validate_state(state_text, ledger_dates, set())
    except StateRejected as exc:
        res.outcome = "rejected"
        res.detail = f"state rejected ({exc}) — prior state kept, stale-but-honest"
        return res
    prior = latest_state(con, thread_id)
    diff = _state_diff((prior or {}).get("state_text", ""), clean,
                       (prior or {}).get("as_of_date", ""))
    # Store the RESOLVED ISO cites (year-agnostic against this thread's ledger).
    cites = sorted(_resolve_cites(clean, ledger_dates)[0])
    with con:
        con.execute(
            "INSERT INTO thread_state (thread_id, briefing_id, as_of_date,"
            " state_text, cites_json, diff_json, model, cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, briefing_id, date, clean, json.dumps(cites),
             json.dumps(diff, ensure_ascii=False), STATE_MODEL, round(cost, 6)))
    res.outcome = "written"
    res.detail = ("; ".join(warnings) if warnings else
                  f"{len(_sentences(clean))} sentence(s), {len(cites)} edition cite(s)")
    return res


def state_is_stale(state: Optional[Dict], today: str) -> Tuple[bool, str]:
    """Stale-but-honest disclosure input: a state whose as_of_date is not
    today is stale (the thread moved elsewhere, or a rewrite failed). The
    render shows 'as of <date>' either way; this flags the disclosure line."""
    if not state:
        return False, ""
    as_of = state.get("as_of_date", "")
    if as_of and as_of < today:
        return True, f"as of {human_date(as_of)}"
    return False, ""
