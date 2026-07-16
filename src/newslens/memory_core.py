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

# ---------------------------------------------------------------------------
# Provenance grades — migration 0014, the poisoned-antecedent bound (HSR
# baseline §5.1(2); engineering council 2026-07-17, Ruling 1; Content-council
# addendum 2026-07-17). A delta's grade lives in the append-only side table
# thread_delta_provenance; ABSENCE of a row = record-established (the honest
# default for an organically-written delta). Two grades are NON-LICENSING: a
# 'source-echo' or 'external-synthesis' row can never license a bare repetition
# word through has_predating_antecedent, no matter how old it gets — that is the
# whole bound. The grade is surfaced on every ledger dict (LEFT JOIN in
# ledger_for_thread) but ONLY has_predating_antecedent acts on it; the row still
# appears in state regen / timelines / writer context (Ruling 1: 0014 bounds
# LICENSING, not the row's existence).
PROVENANCE_SOURCE_ECHO = "source-echo"
PROVENANCE_RECORD_ESTABLISHED = "record-established"
PROVENANCE_READER_EXPLICIT = "reader-explicit"
PROVENANCE_EXTERNAL_SYNTHESIS = "external-synthesis"
PROVENANCE_VALUES = (
    PROVENANCE_SOURCE_ECHO, PROVENANCE_RECORD_ESTABLISHED,
    PROVENANCE_READER_EXPLICIT, PROVENANCE_EXTERNAL_SYNTHESIS,
)
# The grades that DO NOT license a repetition-word antecedent (the read-site
# exclusion set). record-established and reader-explicit license as before.
PROVENANCE_NON_LICENSING = frozenset(
    {PROVENANCE_SOURCE_ECHO, PROVENANCE_EXTERNAL_SYNTHESIS})


def effective_provenance(entry: Dict) -> str:
    """The delta's provenance grade with the record-established default applied:
    absence of a mark (NULL `provenance` on the ledger dict) = an organically-
    written, record-grade delta. Callers get a real grade string, never None."""
    return entry.get("provenance") or PROVENANCE_RECORD_ESTABLISHED

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
            # NL-69 self-mark (migration 0014): classify BEFORE the insert, so
            # the antecedent search (strict before_date) reads only PRIOR rows
            # and never the delta we are about to write. A delta echoing a
            # continuity word the record cannot support is marked source-echo in
            # the SAME transaction, so a future backfill of the deltas-5-6 shape
            # self-marks and can never license the word it echoed. Deterministic,
            # no LLM (Rook's law).
            # Skip the classify entirely on a pre-0014 DB (separability): no
            # provenance table = nothing to mark.
            grade = (classify_delta_provenance(con, topic, happened, signif, date)
                     if _table_exists(con, "thread_delta_provenance") else None)
            with con:
                cur = con.execute(
                    "INSERT INTO thread_deltas (thread_id, briefing_id, brief_id,"
                    " edition_date, slot, verdict, what_happened, significance,"
                    " cites_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (tid, briefing_id, brief_id, date, int(n), verdict, happened,
                     signif, cites_json))
                if grade is not None:
                    con.execute(
                        "INSERT INTO thread_delta_provenance"
                        " (delta_id, provenance, reason) VALUES (?, ?, ?)",
                        (cur.lastrowid, grade,
                         "auto (0014 self-mark): carries a repetition word with "
                         "no predating antecedent on this thread — edition-day "
                         "source-echo diction"))
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


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    """Read-only existence check — lets 0014-aware read/write paths degrade
    gracefully on a DB migrated only through 0012/0013 (the separability
    contract): a missing thread_delta_provenance = nothing marked = every row
    record-established, the same as the row-absence default."""
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def ledger_for_thread(con: sqlite3.Connection, thread_id: int,
                      before_date: Optional[str] = None) -> List[Dict]:
    """The thread's dated ledger, oldest first. `before_date` (exclusive)
    returns only PRIOR coverage — the arc line's 'then' half and the
    thread-scoped P-material both need history strictly before today.

    NL-75 (Rook's gate): each row carries a `superseded_by` field — the id of a
    later, corrected delta (migration 0012's thread_delta_supersessions), or
    None. This is READ-SIDE surfacing only: rows are all returned, in order,
    unchanged; consumers decide what to do with a superseded row. State
    regeneration EXCLUDES superseded rows (a wrong delta must stop re-entering
    every future state — the whole point); the timeline SHOWS them struck. The
    LEFT JOIN is idempotent w.r.t. the old behavior when nothing is superseded.

    NL-69 (migration 0014): each row ALSO carries a `provenance` field — the
    grade from thread_delta_provenance, or None (= record-established default;
    see effective_provenance). Surfacing only, mirroring superseded_by: the row
    is unchanged and still returned. ONLY has_predating_antecedent acts on the
    grade (source-echo / external-synthesis rows do not license repetition
    words); every other consumer ignores it. Idempotent w.r.t. old behavior when
    no row is marked. On a DB migrated only THROUGH 0012/0013 (0014 not yet
    applied — the separability contract) the provenance JOIN degrades to a NULL
    column so the ledger read never dies: table-absence = record-established for
    every row, exactly the row-absence default."""
    prov_join = (" LEFT JOIN thread_delta_provenance p ON p.delta_id = d.id"
                 if _table_exists(con, "thread_delta_provenance") else "")
    prov_col = "p.provenance AS provenance" if prov_join else "NULL AS provenance"
    sql = (
        f"SELECT d.*, s.superseded_by AS superseded_by, {prov_col}"
        " FROM thread_deltas d"
        " LEFT JOIN thread_delta_supersessions s ON s.delta_id = d.id"
        f"{prov_join}"
        " WHERE d.thread_id = ?")
    params: Tuple = (thread_id,)
    if before_date:
        sql += " AND d.edition_date < ?"
        params = (thread_id, before_date)
    sql += " ORDER BY d.edition_date, d.id"
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def _live_entries(entries: List[Dict]) -> List[Dict]:
    """The ledger rows a REGENERATED read model may stand on: superseded rows
    dropped (Rook's gate — a wrong delta must not re-enter state forever)."""
    return [e for e in entries if not e.get("superseded_by")]


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
            "id": e.get("id"),
            "date": e["edition_date"],
            "human": human_date(e["edition_date"]),
            "what_happened": e["what_happened"],
            "significance": e.get("significance", ""),
            "briefing_id": e.get("briefing_id"),
            "verdict": e.get("verdict", ""),
            # NL-75 (Rook's gate): a superseded row STAYS in the timeline but is
            # rendered struck/annotated (the server strikes it) — the archive
            # tells the truth, including that a fact was later corrected. `id`
            # (D1) lets the render name the SUPERSEDING entry (by its date) from
            # the same row set without a second query.
            "superseded_by": e.get("superseded_by"),
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
        entries = _live_entries(ledger_for_thread(con, tid, before_date=date))
        last = entries[-1]["edition_date"] if entries else date
        scoped.append({"date": last, "text": text, "thread": topic})
    return scoped or generic_prior


def thread_record_text(con: sqlite3.Connection, thread_id: int, topic: str,
                       before_date: Optional[str] = None) -> str:
    """The thread's own record as prior-coverage prose for the analyst: its
    dated ledger lines + its standing state. Labeled 'per our prior coverage'
    so a P-only claim can never be laundered as external background.

    F1 (NL-75 QA): superseded (corrected-away) deltas are EXCLUDED here too —
    the writer surface already drops them via _live_entries (Rook's gate: a wrong
    fact must stop re-entering downstream prose); the analyst P-channel is the
    same downstream and must not re-anchor on a delta the record has corrected."""
    entries = _live_entries(ledger_for_thread(con, thread_id, before_date=before_date))
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


def validate_state(state_text: str, ledger_dates: set) -> Tuple[str, List[str]]:
    """The write law, checkable. HARD-REJECT (StateRejected) the fabrication
    class: a sentence cited to a date that resolves to NO ledger entry (a past
    THIS thread never moved on). Returns (clean, warnings); an over-long
    paragraph warns (Content's <=5-sentence cap) but does not reject — length
    is editorial, a fabricated cite is a trust breach.

    M3 cites-fork decision (carried from the M2 gate; DECISIONS 2026-07-14):
    LEDGER-RESOLVED-ONLY. thread_state.cites_json persists ledger-resolved cites
    only — rewrite_state resolves the clean text against the thread's ledger
    dates alone (below, `cites = _resolve_cites(clean, ledger_dates)[0]`).
    Acceptance is narrowed to MATCH that persistence: a cite must resolve to a
    date THIS thread moved on (a ledger date). An edition date that is not a
    ledger date is the BUG-25 fabrication class (some other edition ran that
    day, but this thread did not move) — it is rejected here rather than
    accepted-then-silently-dropped by cites_json.

    NL-75 (M3 gate ruling 4): the inert `edition_dates` parameter is DROPPED.
    It was retained after the M3 cites-fork so QA call sites kept compiling
    (every caller passed an empty set); the wider `resolvable = ledger|edition`
    was headroom no consumer ever used (M3's receipt renderers read BRIEF-level
    source-key cites, not this surface). The 19 call sites are swept with this
    change."""
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
    all_entries = ledger_for_thread(con, thread_id)      # full ledger incl today
    if not all_entries:
        # M1 gate F3: day-one is NOT a budget event — its own label so
        # diagnose's outcome aggregation never conflates the two.
        res.outcome = "skipped-no-ledger"
        res.detail = "no ledger — nothing to synthesize (day-one)"
        return res
    # NL-75 (Rook's gate): a superseded delta is EXCLUDED from state
    # regeneration — the whole reason the link is machine-readable. The state
    # is synthesized from the live ledger only, so a corrected-away delta stops
    # re-entering every future state.
    entries = _live_entries(all_entries)
    if not entries:
        res.outcome = "skipped-no-ledger"
        res.detail = "every ledger entry is superseded — nothing live to synthesize"
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
        clean, warnings = validate_state(state_text, ledger_dates)
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


# ===========================================================================
# NL-75 rung (a): the ledger reaches the WRITER (Engineering's spike — the one
# missing hop). The analyst already receives thread state + deltas via M2's
# P-channel (prior_for_slot / thread_record_text); the writer got thread NAMES
# only (generate.py:578). These helpers give the writer prompt the standing
# state + last-N dated deltas, budgeted, so exemplar A's arc-compression is
# writable — "what began as X on <date> has become Y."
# ===========================================================================

DELTA_CONTEXT_N = 5   # last N ledger deltas the writer sees per thread (budget:
#                       ~5 dated lines + a <=5-sentence state ≈ few hundred
#                       input tokens/thread; Engineering priced rung (a) at
#                       ~+$0.005/edition — this is the token spend that buys it.


def writer_thread_context(con: sqlite3.Connection, topic: str,
                          before_date: str,
                          last_n: int = DELTA_CONTEXT_N) -> str:
    """Rung (a): the thread's memory formatted for the WRITER prompt — the
    standing state (or an explicit note of its ABSENCE) plus the last N ledger
    deltas WITH edition dates. Strictly-before the edition (a writer sees only
    PRIOR coverage; today's own delta is written after generation, never fed
    back — the same before_date discipline the analyst path uses). Superseded
    deltas are excluded (Rook's gate). Returns '' when the thread has no prior
    record so the caller renders nothing (day-one threads get no history block,
    matching render_today_arc's day-one silence)."""
    tid = resolve_thread_id(con, topic)
    if tid is None:
        return ""
    entries = _live_entries(ledger_for_thread(con, tid, before_date=before_date))
    state = latest_state(con, tid, before_date=before_date, strict=True)
    if not entries and not state:
        return ""
    lines: List[str] = [f"MEMORY — the record for thread {topic!r} (edition "
                        "history only; NEVER the reader's history):"]
    if state:
        lines.append(f"standing state (as of {human_date(state['as_of_date'])}): "
                     f"{state['state_text']}")
    else:
        lines.append("standing state: none on record yet — do not imply the "
                     "record holds one.")
    recent = entries[-last_n:]
    if recent:
        lines.append("this thread's record so far (edition dates are load-"
                     "bearing — build continuity from them in the sentence, "
                     "e.g. \"what began as X on Jul 5 had by Jul 10 become Y\"):")
        for e in recent:
            signif = f" — {e['significance']}" if e.get("significance") else ""
            lines.append(f"  * {human_date(e['edition_date'])}: "
                         f"{e['what_happened']}{signif}")
    return "\n".join(lines)


def has_predating_antecedent(con: sqlite3.Connection, topic: str,
                             subject_units: set, edition_date: str) -> bool:
    """The Forward-Claim antecedent rule (Content rule 3), POISONED-ANTECEDENT
    hardened (HSR baseline finding 1, BINDING). A repetition word — reinstated,
    resumed, renewed, again — is only licensed by a ledger antecedent that
    PREDATES the edition being written. A row dated == the edition (the 07-14
    same-day backfill that merely echoed edition-day source diction) does NOT
    establish the antecedent; the well is poisoned before the pump is installed.

    The mechanism IS strict before_date: today's own rows are never in the
    search set, so a naive antecedent-check can never 'find' delta 5 and
    validate a future "reinstated." `subject_units` are the salient content
    words of the repetition's subject (e.g. {'blockade'}); an antecedent must
    mention at least one, so an unrelated prior delta cannot license the word.
    A superseded prior delta does not count (Rook's gate — excluded).

    NL-69 (migration 0014, BINDING): a delta MARKED non-licensing
    ('source-echo' or 'external-synthesis') is excluded from the search set even
    when it predates the edition. From 2026-07-17 the 07-14 rows genuinely
    predate, so strict before_date no longer protects; only the provenance mark
    keeps deltas 5-6 from licensing the word they echoed. Unmarked rows default
    to record-established and license as before."""
    tid = resolve_thread_id(con, topic)
    if tid is None:
        return False
    prior = _live_entries(ledger_for_thread(con, tid, before_date=edition_date))
    # The provenance bound: drop non-licensing grades (source-echo /
    # external-synthesis) before ANY licensing decision, including the
    # no-discriminator branch below.
    prior = [e for e in prior
             if effective_provenance(e) not in PROVENANCE_NON_LICENSING]
    wanted = {u.lower() for u in subject_units if u}
    if not wanted:
        return bool(prior)   # no subject discriminator — any predating history counts
    for e in prior:
        hay = f"{e.get('what_happened','')} {e.get('significance','')}".lower()
        if any(u in hay for u in wanted):
            return True
    return False


# ---------------------------------------------------------------------------
# The repetition-word machinery — Content rule iii, poisoned-antecedent
# hardened. It lives HERE (with has_predating_antecedent) rather than in
# generate.py because it is the antecedent-licensing surface the read-site
# (generate.repetition_antecedent_findings) and the write-side self-mark
# (migration 0014, below) both stand on; generate imports both names. A single
# source of truth for "what is a repetition word" — no LLM anywhere in this
# surface (Rook's determinism law).
#
# Continuity/repetition diction that PRESUPPOSES a prior state. Word-boundary
# matched so "again" never fires inside "against". The spec lexicon reads
# 'reinstated, again, resumed, renewed, re-imposed, once more, for the Nth
# time'; news copy hyphenates the re- stems freely (HSR §5.1(4) found the
# unhyphenated sibling), so the re- forms accept an optional hyphen; the
# for-the-Nth-time class is a bounded alternative.
_REPETITION_RE = re.compile(
    r"\b(re-?instat(?:e|es|ed|ing)|re-?impos(?:e|es|ed|ing)|re-?imposition|"
    r"renew(?:s|ed|ing)?|resum(?:e|es|ed|ing)|re-?open(?:s|ed|ing)?|"
    r"restor(?:e|es|ed|ing)|once more|back on|consecutive|again|"
    r"for the (?:second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"\d+(?:st|nd|rd|th)) time)\b", re.I)

# D6 (NL-75 QA, the HIGH one — HSR §5.1(2)): the antecedent SUBJECT must
# discriminate the repetition's OBJECT (the thing being re-X'd), not echo the
# whole sentence. On a thread with ANY real prior history a mundane shared word
# (the thread-topic word 'strait' living in a genuine prior row) would license
# the repetition word with ZERO prior blockade on record. Scope the subject to a
# bounded window AFTER the match, minus the thread topic's own words.
_SUBJECT_WINDOW_CHARS = 64


def _repetition_subject_units(sentence: str, match: "re.Match",
                              topics: List[str]) -> set:
    """The salient units of the repetition's object: a bounded window AFTER the
    match (the noun phrase being re-X'd), minus the thread topics' own salient
    words. Falls back to the full sentence (still minus topic words) only when
    the window yields no units, so a trailing repetition word ("prices rose
    again.") still has a subject rather than licensing on nothing."""
    topic_words: set = set()
    for t in topics:
        topic_words.update(_salient_units(t))
    window = sentence[match.end():match.end() + _SUBJECT_WINDOW_CHARS]
    units = [u for u in _salient_units(window) if u not in topic_words]
    if not units:
        # Gate FIX-1 (milestone review): the full-sentence fallback excludes the
        # match's OWN units — a sentence-final "again"/"resumed" must not become
        # its own subject and license off any prior row carrying the word (or a
        # superstring: "again" substring-matches "against").
        match_units = set(_salient_units(match.group(0)))
        units = [u for u in _salient_units(sentence)
                 if u not in topic_words and u not in match_units]
    return set(units)


# ===========================================================================
# NL-69 (migration 0014): the self-marking write path + the supervised mark.
# ===========================================================================

def classify_delta_provenance(con: sqlite3.Connection, topic: str,
                              what_happened: str, significance: str,
                              edition_date: str) -> Optional[str]:
    """The DETERMINISTIC self-mark decision for a freshly-written delta (no LLM
    — Rook's law). Returns 'source-echo' when the delta merely echoed a
    continuity word the record cannot yet support; None when it is
    record-grade (the write path leaves it unmarked → record-established).

    A delta is source-echo iff BOTH hold, checked on what_happened AND
    significance:
      (a) it carries a repetition/continuity word (_REPETITION_RE), AND
      (b) that word's OBJECT has NO predating record-established antecedent on
          this thread (has_predating_antecedent False as of edition_date).

    This is exactly the read-site's own rule iii applied to the delta's OWN
    text: a delta whose continuity diction would trip the poisoned-antecedent
    flag if it appeared in prose is marked source-echo, so it can never itself
    become the false antecedent for the SAME word later. It catches the HSR
    deltas 5-6 shape ('reinstated a naval blockade' on a thread with no prior
    blockade) even though those rows also carry a P1 cite — the honest signal
    is the unsupported continuity word, not the cite set. A fresh-event delta
    (no repetition word) or a record-backed continuity ('resumed' with a real
    prior antecedent) is left unmarked. Conservative by construction: under-
    marking is recoverable by the supervised command; over-marking would
    silently refuse a legitimate future antecedent, so we mark only the clearest
    poison shape.

    Every _REPETITION_RE match in each clause is checked — a record-backed
    first word must not shadow an unsupported second (gate FIX-1).

    Absence of a repetition word is the common case and returns None fast."""
    for text in (what_happened or "", significance or ""):
        for m in _REPETITION_RE.finditer(text):
            units = _repetition_subject_units(text, m, [topic])
            # No discriminating object units → this match never decides either
            # way (the read-site's conservative default for an empty subject);
            # later matches in the same clause still get their own check.
            if not units:
                continue
            if not has_predating_antecedent(con, topic, units, edition_date):
                return PROVENANCE_SOURCE_ECHO
    return None


def mark_delta_provenance(con: sqlite3.Connection, delta_id: int,
                          provenance: str, reason: str = ""):
    """Supervised provenance mark (0014) — the tool behind
    `newslens memory-mark-provenance`. Returns (ok, message, delta_row).

    Refuses (ok=False, NO write) on: an unknown provenance value; a delta id
    that does not exist; a delta already marked (append-only — a mark is a dated
    fact, never rewritten; the PK is the DB-level backstop). On success writes
    exactly one row inside a transaction and returns the delta row so the caller
    can print the text it graded. The supervision is external: this asks
    nothing — the CoS runs it only with the principal's word (decision B)."""
    if provenance not in PROVENANCE_VALUES:
        return (False,
                f"unknown provenance {provenance!r} — one of "
                f"{', '.join(PROVENANCE_VALUES)}", None)
    row = con.execute(
        "SELECT id, thread_id, edition_date, slot, what_happened, significance,"
        " cites_json FROM thread_deltas WHERE id = ?", (delta_id,)).fetchone()
    if row is None:
        return (False,
                f"no thread_delta with id {delta_id} — SELECT-verify the id "
                "against the real DB first", None)
    existing = con.execute(
        "SELECT provenance, reason, marked_at FROM thread_delta_provenance"
        " WHERE delta_id = ?", (delta_id,)).fetchone()
    if existing is not None:
        return (False,
                f"delta {delta_id} is already marked {existing['provenance']!r} "
                f"(marked_at {existing['marked_at']}) — append-only, a mark is "
                "never rewritten", row)
    with con:
        con.execute(
            "INSERT INTO thread_delta_provenance (delta_id, provenance, reason)"
            " VALUES (?, ?, ?)", (delta_id, provenance, reason))
    return (True, f"marked delta {delta_id} provenance={provenance}", row)


# ===========================================================================
# NL-75: the expiry register (Content Forward-Claim Rules item 2). A watch-for
# is a ledger-adjacent object (observable, due-date when parseable, status);
# at the next edition an expired watch-for must CONVERT — RESOLVED / UNANSWERED
# / SUPERSEDED — never re-shipped, never silently dropped. Persisted in
# watch_items (migration 0013), append-only: a conversion is a NEW row.
# ===========================================================================

def parse_due_date(text: str, edition_date: str) -> Optional[str]:
    """The FIRST resolvable date in a watch-for's prose, as YYYY-MM-DD. ISO
    forms resolve to themselves; a human 'Month D' resolves to the edition's
    YEAR (a briefing's forward-looking date reads in the edition's calendar).
    Returns None when the observable names no parseable date — a dateless
    watch-for is tracked but does not auto-expire by date (Content: "due-date
    when parseable"). Known limitation: a December edition naming a January
    date resolves to the wrong year; flagged, out of scope for the loop."""
    m = _ISO_RE.search(text or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    dm = _MONTH_DAY_RE.search(text or "")
    if dm:
        year = (edition_date or "")[:4]
        if not (year.isdigit() and len(year) == 4):
            return None
        mon = _MONTH_NUM[dm.group(1).lower()]
        return f"{year}-{mon:02d}-{int(dm.group(2)):02d}"
    return None


def persist_watch_items(con: sqlite3.Connection, date: str,
                        briefing_id: Optional[int], stories: List[Dict],
                        slots: List[Dict]) -> int:
    """Write each story's watch_for as an OPEN watch-item (the promise). One
    per (briefing, slot); idempotent by that key (append-only — a re-gen finds
    it on file and writes nothing, never UPDATEs). thread_id links the item to
    the slot's matched thread when there is one, so accountability is
    thread-scoped where a thread exists. Returns the count written."""
    slot_by_n = {int(s["slot"]): s for s in slots}
    written = 0
    for story, slot in zip(stories, slots):
        n = int(slot["slot"])
        observable = (story.get("watch_for") or "").strip()
        if not observable:
            continue
        if con.execute(
                "SELECT 1 FROM watch_items WHERE briefing_id IS ? AND slot = ?"
                " AND kind = 'open' LIMIT 1", (briefing_id, n)).fetchone():
            continue
        topics = [t for t in (slot_by_n.get(n, {}).get("matched_memory") or []) if t]
        tid = resolve_thread_id(con, topics[0]) if topics else None
        due = parse_due_date(observable, date)
        with con:
            con.execute(
                "INSERT INTO watch_items (thread_id, briefing_id, slot,"
                " edition_date, kind, observable, due_date)"
                " VALUES (?, ?, ?, ?, 'open', ?, ?)",
                (tid, briefing_id, n, date, observable, due))
        written += 1
    return written


def expired_unconverted_watch_items(con: sqlite3.Connection, topic: str,
                                    today_edition: str) -> List[Dict]:
    """Open watch-items for the thread whose parseable due-date has PASSED
    relative to today's edition and that no conversion row has closed yet.
    These are the accountability debts the next edition MUST convert (Content
    rule 2). Dateless open items are excluded (they can't auto-expire by date;
    a later edition converts them explicitly)."""
    tid = resolve_thread_id(con, topic)
    if tid is None:
        return []
    rows = con.execute(
        "SELECT w.* FROM watch_items w WHERE w.thread_id = ? AND w.kind = 'open'"
        " AND w.due_date IS NOT NULL AND w.due_date < ?"
        " AND NOT EXISTS (SELECT 1 FROM watch_items c WHERE c.converts = w.id)"
        " ORDER BY w.due_date, w.id", (tid, today_edition)).fetchall()
    return [dict(r) for r in rows]


def record_watch_conversion(con: sqlite3.Connection, open_item: Dict, date: str,
                            briefing_id: Optional[int], kind: str,
                            note: str) -> None:
    """Close an expired watch-item with a conversion row (a NEW row — the
    register is append-only). `kind` in resolved|unanswered|superseded.

    D2 (NL-75 QA): AT MOST ONE conversion row may close an open item. The read
    filter (expired_unconverted_watch_items) already dedups ACROSS editions, but
    within ONE run the same thread can ride two slots and reach here twice for a
    single promise — a per-slot loop then writes duplicate (and potentially
    contradictory: 'resolved' + 'unanswered') rows, double-counting Data's
    conversion-rate metric. Dedup at record time: if a conversion row already
    closes this open item, write nothing (the append-only table needs no schema
    change for the skip)."""
    if kind not in ("resolved", "unanswered", "superseded"):
        raise ValueError(f"conversion kind {kind!r} is not a conversion state")
    if con.execute("SELECT 1 FROM watch_items WHERE converts = ? LIMIT 1",
                   (open_item["id"],)).fetchone():
        return
    with con:
        con.execute(
            "INSERT INTO watch_items (thread_id, briefing_id, slot, edition_date,"
            " kind, observable, converts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (open_item.get("thread_id"), briefing_id, open_item.get("slot"),
             date, kind, note, open_item["id"]))


_CONV_SUPERSEDED = ("supersed", "moot", "overtaken", "overtook", "overrun",
                    "rendered irrelevant", "no longer relevant", "eclipsed",
                    "outpaced", "outrun", "made moot")
_CONV_UNANSWERED = ("without a mention", "went unmentioned", "unmentioned",
                    "no mention", "none of", "no word", "stayed silent",
                    "silent on", "unanswered", "did not say", "does not say",
                    "no outlet", "goes unmentioned", "came and gone", "no report")


def classify_conversion(observable: str, prose: str) -> Optional[str]:
    """How an expired observable was ADDRESSED in the edition's prose:
    'superseded' | 'unanswered' | 'resolved', or None when it was NOT addressed
    (the silent-drop violation Content rule 2 forbids). The writer produces the
    real conversion; this deterministic classifier catches the OMISSION (None)
    and instruments the outcome. Order: a supersession or an explicit silence
    is named as such; otherwise a referenced observable is 'resolved' (its
    outcome reported). Not-referenced-at-all is the failure."""
    low = (prose or "").lower()
    subject = [u for u in _salient_units(observable) if u]
    # Gate FIX-4 (milestone review): a numeric-only match must not close an
    # accountability debt — a bare "12" anywhere in a body (substring: "2012"
    # included) would resolve the Switzerland promise. Non-numeric units carry
    # the reference test; numeric-only observables keep numeric matching.
    non_numeric = [u for u in subject if not u[0].isdigit()]
    check = non_numeric or subject
    referenced = any(u in low for u in check) if check else False
    if not referenced:
        return None
    if any(k in low for k in _CONV_SUPERSEDED):
        return "superseded"
    if any(k in low for k in _CONV_UNANSWERED):
        return "unanswered"
    return "resolved"
