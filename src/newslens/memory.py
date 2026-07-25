"""Memory: live threads + the memory.md transparency surface (milestone 4,
lifecycle v2 per the principal amendment finalized 2026-07-04 — ADR-0006).

THE LIFECYCLE (three states, one automatic transition each way):

  active ──(unreferenced by briefings for 14d)──> dormant
  dormant ──(a story EARNS a briefing slot on its own merits and matches
             the thread)──> active   (auto-revival, with a dated back-
             reference for the narrative)
  any ──(principal verb, file move to Inactive, or deleted line)──> dismissed_user

  * dormant threads have NO ranking influence and are OUT of the prompt's
    thread list; they are match-only for revival (see ranking.py — the story
    wins its slot first, the dormant match is applied post-selection).
  * dismissed_user NEVER auto-revives: explicit intent wins. It stays
    VISIBLE in memory.md ("dismissed by you <date>"); revival is explicit
    only (`memory add` or moving the line back to Active).
  * Every automatic transition appears DATED in memory.md — nothing silent.

memory.md is the transparency surface, taken literally: hand-editable, read
as SOURCE OF TRUTH at generation time, principal edits written back to
SQLite. Two sections: Active / Inactive. Inactive lines carry annotations —
"(dormant since <date>, last covered <date>)" vs "(dismissed by you <date>)"
— which the parser reads back, so a rendered dormant line round-trips as
dormant while a BARE line moved to Inactive (or a deleted line) means
dismissed_user. File-wins semantics otherwise unchanged; an unparseable file
is a LOUD stop with the file left untouched.

The DB keeps full history; Inactive renders complete, sorted by recency —
no pruning at personal scale (growth note in the amendment).

Topic names may not contain the " — " separator (documented in the file
header; the em-dash split keeps parsing forgiving and line-based).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import paths

DORMANT_AFTER_DAYS = 14    # active -> dormant when unreferenced this long
CONTEXT_CAP = 15           # spec §B: N most-recently-referenced active rows
DORMANT_MATCH_CAP = 40     # dormant topics offered for match-only revival
SEPARATOR = " — "          # topic/note split in file lines (em-dash, spaced)

VALID_STATUSES = ("active", "dormant", "dismissed_user")

# The taxonomy contract's §C live-thread list (14 threads; the 5 marked
# "acute twin" also hold a standing topic tag in sources.yaml — the thread
# tracks the CURRENT acute instantiation and should be renamed to the
# specific live event when one exists). Seeded only by first-run bootstrap.
SEED_THREADS: List[Tuple[str, str]] = [
    ("Iran War", ""),
    ("Ceasefire", ""),
    ("Ukraine War", ""),
    ("Government Shutdown", ""),
    ("DHS Funding", ""),
    ("ROAD to Housing Act", ""),
    ("Congressional Gridlock", ""),
    ("Helium Shortage", ""),
    ("Redemption Gates", "folds under the Private Credit tag; tracked here while a specific redemption-gate event is live"),
    ("Strait of Hormuz", "acute twin of the standing tag — rename to the specific live event when one exists"),
    ("China-Taiwan", "acute twin of the standing tag — rename to the specific live event when one exists"),
    ("Credit Default Risk", "acute twin of the standing tag — rename to the specific live event when one exists"),
    ("Recession Risk", "acute twin of the standing tag — rename to the specific live event when one exists"),
    ("Stagflation", "acute twin of the standing tag — rename to the specific live event when one exists"),
]

_HEADER = """# NewsLens memory — the live threads it's tracking for you
<!--
  Edit this file freely; NewsLens reads it as the SOURCE OF TRUTH at every
  run and writes your changes back to its database.

    * change a note ......... edit the text after the " — "
    * stop tracking ......... move the line under "Inactive" or simply
                              delete it — either way it is recorded as
                              dismissed by you, and it will NOT come back on
                              its own (only threads that went dormant
                              automatically can auto-revive)
    * start tracking ........ add "- Topic — optional note" under Active
    * revive ................ move a line back under Active (or `newslens
                              memory add "<topic>"`)

  Inactive annotations are meaningful: "(dormant since <date>, ...)" marks a
  thread that idled out after {dormant_days} days unreferenced — it revives
  AUTOMATICALLY if a story that earns a briefing slot matches it. The three
  stopped forms say WHO stopped it, and never claim more than we can prove:
  "(dismissed by you <date>)" = you used a stop/unfollow button or command;
  "(removed from your memory.md <date>)" = this sync inferred it from an
  edit to this file; "(dismissed <date>)" = a thread stopped before NewsLens
  recorded provenance. None of the three ever auto-revive. Keep the
  annotation with the line when you rearrange; a bare line under Inactive
  counts as a stop you made in this file.

  Lines match database rows by topic name (case-insensitive); renaming a
  topic dismisses the old thread and starts a new one. Topic names cannot
  contain " — ".

  Threads you DELETE (or rename) are remembered as deleted: an old copy of
  this file can never resurrect them. To really bring one back, ask for it
  by name — `newslens memory add "<topic>"`.
-->
"""

_DORMANT_ANN_RE = re.compile(r"\(dormant since (\d{4}-\d{2}-\d{2})[^)]*\)\s*$")
_DISMISSED_ANN_RE = re.compile(r"\(dismissed by you (\d{4}-\d{2}-\d{2})\)\s*$")
# NL-81 §5.4: the mechanism copy (sync-inferred) and the neutral copy (legacy
# rows whose provenance predates 0022). parse_file reads all three back to
# dismissed_user, so an unchanged Inactive line produces zero writes and a
# round-trip can never launder 'file_sync' into 'principal'.
_DISMISSED_FILE_ANN_RE = re.compile(
    r"\(removed from your memory\.md (\d{4}-\d{2}-\d{2})\)\s*$")
_DISMISSED_NEUTRAL_ANN_RE = re.compile(r"\(dismissed (\d{4}-\d{2}-\d{2})\)\s*$")
_DISMISSED_ANN_RES = (_DISMISSED_ANN_RE, _DISMISSED_FILE_ANN_RE,
                      _DISMISSED_NEUTRAL_ANN_RE)
_LASTREF_ANN_RE = re.compile(r"\(last referenced: [^)]*\)\s*$")


class MemorySyncError(RuntimeError):
    """memory.md exists but cannot be safely interpreted. Loud on purpose."""


@dataclass
class SyncResult:
    created_file: bool = False
    seeded: int = 0
    added: List[str] = field(default_factory=list)
    notes_updated: List[str] = field(default_factory=list)
    status_changed: List[str] = field(default_factory=list)   # "topic: old->new"
    dismissed_by_deletion: List[str] = field(default_factory=list)
    went_dormant: List[str] = field(default_factory=list)
    # --- NL-81 guard disclosures (never silent) ---------------------------
    # Acts the file asked for that the guard did NOT perform. blocked_
    # resurrections: file lines for topics with a live delete/rename
    # tombstone. stale_refusal: the whole import was skipped because the file
    # failed the generation/identity precondition — when this is set, NEITHER
    # side was mutated (no import, no dormancy pass, no file rewrite).
    blocked_resurrections: List[str] = field(default_factory=list)
    stale_refusal: Optional[str] = None
    imported: bool = True
    accepted_file: bool = False       # --accept-file overrode a stale file
    bootstrapped: bool = False        # an unstamped file was adopted (5.2)
    generation: Optional[int] = None  # the stamp this call left on the file

    @property
    def edits_applied(self) -> int:
        return (
            len(self.added) + len(self.notes_updated)
            + len(self.status_changed) + len(self.dismissed_by_deletion)
        )

    def guard_lines(self) -> List[str]:
        """The NL-81 disclosures ONLY — what the guard refused to do. Callers
        that surface a narrow warning channel (the server's JSON response)
        use this; summary_lines() includes it plus the ordinary sync report."""
        out: List[str] = []
        if self.stale_refusal:
            out.append(self.stale_refusal)
        out.extend(self.blocked_resurrections)
        return out

    def summary_lines(self) -> List[str]:
        out: List[str] = []
        out.extend(self.guard_lines())
        if self.seeded:
            out.append(
                f"memory: first-run bootstrap seeded {self.seeded} threads from "
                "the taxonomy contract — review them in memory.md"
            )
        if self.edits_applied:
            bits = []
            if self.added:
                bits.append(f"{len(self.added)} added ({', '.join(self.added[:5])})")
            if self.notes_updated:
                bits.append(f"{len(self.notes_updated)} note(s) updated")
            if self.status_changed:
                bits.append(f"{len(self.status_changed)} status change(s)")
            if self.dismissed_by_deletion:
                bits.append(
                    f"{len(self.dismissed_by_deletion)} dismissed by deletion "
                    f"({', '.join(self.dismissed_by_deletion[:5])})"
                )
            out.append("memory.md edits applied: " + "; ".join(bits))
        if self.went_dormant:
            out.append(
                f"memory: {len(self.went_dormant)} thread(s) went dormant "
                f"(unreferenced {DORMANT_AFTER_DAYS}+ days): "
                + ", ".join(self.went_dormant)
                + " — they auto-revive if a slot-earning story matches them; "
                "see memory.md"
            )
        return out


def _utc_now() -> datetime:
    """Single clock for every stamp and staleness decision in this module —
    tests patch this one seam to freeze time."""
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _day(value: Optional[str]) -> str:
    return (value or "")[:10] or "unknown date"


# ---------------------------------------------------------------------------
# NL-81 — the sync resurrection guard (build contract: engineering-2 §5)
#
# Three mechanisms, all DB-resident (migration 0022):
#   1. TOMBSTONES — append-only delete/rename/lift records. The sync may never
#      re-INSERT a topic whose key carries a live delete/rename tombstone, not
#      even under --accept-file. Lift (an explicit `memory add`-class verb) is
#      how a tombstone is superseded; nothing is ever updated or removed.
#   2. GENERATION STAMP — a DB counter echoed into the file header on every
#      DB->file render. Import precondition: file gen == DB gen AND the pairing
#      identity matches. NEVER mtime: the 07-17 poisoned reconstruction was
#      freshly written (newest mtime, stale content), so a wall-clock signal
#      passes the exact file that caused the incident.
#   3. PROVENANCE — dismissed_via, so "(dismissed by you)" renders only for
#      acts that arrived through a principal verb surface.
# ---------------------------------------------------------------------------

TOMBSTONE_KINDS = ("delete", "rename", "lift")
TOMBSTONE_ACTORS = ("principal", "org", "sync")
# 'by you' is reserved for verb surfaces. See §5.4 and migration 0022 (c).
DISMISSED_VIA_VALUES = ("principal", "file_sync", "org")

_STAMP_RE = re.compile(
    r"<!--\s*newslens-sync:\s*gen=(\d+)\s+identity=([0-9a-fA-F]+)"
    r"\s+profile=(\S+)\s+rendered=(\S+)\s*-->")


def sync_state(con: sqlite3.Connection) -> Optional[Dict]:
    """The single sync_state row, or None on a pre-0022 database.

    None is not a hole: without a counter there is nothing to gate on, and a
    pre-0022 schema means this code is running against a database the migration
    has not reached yet. Pre-0022, the read/gate helpers degrade (_check_stamp
    reads 'lawful', stamp_line renders no stamp, tombstone lookups find nothing)
    so old copies stay inspectable; write and render surfaces (_apply_plan,
    render_file, the tombstone-writing verbs, dismiss_thread) require 0022 and
    raise sqlite3.OperationalError — deliberately loud, since a guard write that
    silently skipped its tombstone or provenance would reopen the incident's
    hole. Unreachable through product lanes, which all run db.migrate() first."""
    try:
        row = con.execute(
            "SELECT sync_generation, sync_identity, profile_slug"
            " FROM sync_state WHERE id = 1").fetchone()
    except sqlite3.OperationalError:      # table absent (pre-0022)
        return None
    if row is None:
        return None
    return {"generation": row["sync_generation"],
            "identity": row["sync_identity"],
            "profile": row["profile_slug"] or "-"}


def bump_generation(con: sqlite3.Connection) -> Optional[int]:
    """Advance the render counter. Called by write_memory_file ONLY, so that
    'the counter moved' and 'the file was rewritten' are the same event."""
    st = sync_state(con)
    if st is None:
        return None
    with con:
        con.execute(
            "UPDATE sync_state SET sync_generation = sync_generation + 1"
            " WHERE id = 1")
    return st["generation"] + 1


def stamp_line(con: sqlite3.Connection) -> str:
    """The header stamp for the CURRENT generation (no bump — write_memory_file
    owns the bump). Empty string on a pre-0022 database.

    An HTML comment on purpose: parse_file skips comments (see its comment
    arm), so a pre-NL-81 build reads a stamped file with no change in behavior
    — the rollback story is 'revert the code', nothing to unwind."""
    st = sync_state(con)
    if st is None:
        return ""
    return (f"<!-- newslens-sync: gen={st['generation']} "
            f"identity={st['identity']} profile={st['profile']} "
            f"rendered={_utc_now_iso()} -->\n")


def parse_stamp(text: str) -> Optional[Dict]:
    """Read the generation stamp out of a memory.md body, or None if unstamped."""
    m = _STAMP_RE.search(text)
    if m is None:
        return None
    return {"generation": int(m.group(1)), "identity": m.group(2).lower(),
            "profile": m.group(3), "rendered": m.group(4)}


def append_tombstone(con: sqlite3.Connection, *, topic: str, kind: str,
                     actor: str = "principal", thread_id: Optional[int] = None,
                     successor: str = "") -> None:
    """Append one tombstone row. CALLER MUST HOLD A TRANSACTION (the
    _log_altitude_event precedent): the tombstone and the state change it
    records commit together or not at all — a delete that committed without its
    tombstone is exactly the hole this closes."""
    if kind not in TOMBSTONE_KINDS:
        raise ValueError(f"kind must be one of {list(TOMBSTONE_KINDS)}, got {kind!r}")
    if actor not in TOMBSTONE_ACTORS:
        raise ValueError(f"actor must be one of {list(TOMBSTONE_ACTORS)}, got {actor!r}")
    con.execute(
        "INSERT INTO memory_tombstones (topic_key, topic, thread_id, kind,"
        " successor_key, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (topic.casefold(), topic, thread_id, kind,
         successor.casefold() or None, actor, _utc_now_iso()))


def _latest_tombstone(con: sqlite3.Connection, key: str):
    try:
        return con.execute(
            "SELECT topic, kind, successor_key, created_at FROM memory_tombstones"
            " WHERE topic_key = ? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    except sqlite3.OperationalError:      # pre-0022 database
        return None


def tombstone_block(con: sqlite3.Connection, topic: str) -> Optional[Dict]:
    """Is re-creating `topic` from the FILE blocked? None = free to import.

    Latest row wins: 'lift' (the explicit lane) supersedes, so a lifted key is
    free again. A rename chain a->b->c is walked iteratively with a visited-set
    cycle guard so the disclosure can name the thread's CURRENT name; the walk
    stops at the first key that still holds a live memory row."""
    key = topic.casefold()
    if con.execute("SELECT 1 FROM memory WHERE lower(topic) = ?",
                   (key,)).fetchone() is not None:
        # A live row wears this name — there is nothing to RE-create, so there
        # is nothing to block. (Reached by a rename cycle a->b->a, where the
        # oldest tombstone for `a` still says "renamed away" but the row came
        # home. The sync only consults this for keys with no row; the check is
        # here so the predicate is honest for every caller, including the lift
        # lane, which must not append a lift for a name that never left.)
        return None
    row = _latest_tombstone(con, key)
    if row is None or row["kind"] == "lift":
        return None
    out = {"topic": row["topic"], "kind": row["kind"],
           "when": _day(row["created_at"]), "current_name": None}
    if row["kind"] == "delete":
        return out
    seen = {key}
    succ = row["successor_key"]
    while succ and succ not in seen:
        seen.add(succ)
        live = con.execute(
            "SELECT topic FROM memory WHERE lower(topic) = ?", (succ,)).fetchone()
        if live is not None:
            out["current_name"] = live["topic"]
            break
        nxt = _latest_tombstone(con, succ)
        if nxt is None or nxt["kind"] == "lift":
            out["current_name"] = succ    # the successor key is free again
            break
        if nxt["kind"] == "delete":
            break                          # renamed, then deleted — gone
        succ = nxt["successor_key"]
    return out


def lift_tombstone(con: sqlite3.Connection, topic: str,
                   actor: str = "principal") -> Optional[Dict]:
    """THE EXPLICIT LANE. Record that a principal verb deliberately brought a
    tombstoned topic back. Returns the tombstone that was lifted, or None when
    the key was never tombstoned (then nothing is appended — `add` on a live
    topic writes no tombstone row). Wraps its own transaction: callers reach
    this before their own state change, on their own connection."""
    block = tombstone_block(con, topic)
    if block is None:
        return None
    with con:
        append_tombstone(con, topic=topic, kind="lift", actor=actor)
    return block


def _blocked_line(blocked: List[Dict]) -> str:
    """The refusal copy for tombstone-blocked file lines (contract §5.3). Names
    every blocked act and the exact command that would really bring it back."""
    n = len(blocked)
    kinds = {b["kind"] for b in blocked}
    what = ("previously deleted" if kinds == {"delete"}
            else "previously renamed" if kinds == {"rename"}
            else "previously deleted or renamed")
    bits = []
    for b in blocked:
        if b["kind"] == "delete":
            bits.append(f'"{b["topic"]}", deleted {b["when"]}')
        elif b["current_name"]:
            bits.append(f'"{b["topic"]}" was renamed — it lives on as '
                        f'"{b["current_name"]}"')
        else:
            bits.append(f'"{b["topic"]}" was renamed away {b["when"]} and is no '
                        "longer tracked")
    verb = "was" if n == 1 else "were"
    thread = "thread" if n == 1 else "threads"
    back = "it" if n == 1 else "them"
    lift = "; ".join(f'newslens memory add "{b["topic"]}"' for b in blocked)
    return (f"memory: {n} {thread} in memory.md {verb} {what} and {verb} NOT "
            f"re-imported ({'; '.join(bits)}) — to really bring {back} back: "
            f"{lift}")


_STALE_EXITS = (
    "to proceed: `newslens memory sync --accept-file` (explicit, file wins — "
    "deletion records and dismissal attribution still apply underneath it), or "
    "delete memory.md and let the next run regenerate it from the database")


def _stale_refusal_line(reason: str, detail: str, plan: "ImportPlan") -> str:
    would = plan.would_lines()
    body = ("; ".join(would) if would
            else "it would have changed nothing on the database side")
    return (f"memory: memory.md was NOT imported — {reason} ({detail}). "
            f"Nothing on either side was changed. It would have: {body}. "
            f"{_STALE_EXITS}")


@dataclass
class ImportPlan:
    """What the file WOULD do to the database — computed read-only, so the
    refusal copy is honest by construction (it describes the same plan the
    lawful path applies) and the bootstrap's 'zero pending edits' test is the
    same computation as the import itself."""
    inserts: List[Dict] = field(default_factory=list)
    note_updates: List[Dict] = field(default_factory=list)
    status_changes: List[Dict] = field(default_factory=list)
    dismissals: List[Dict] = field(default_factory=list)
    blocked: List[Dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.inserts or self.note_updates or self.status_changes
                    or self.dismissals or self.blocked)

    def would_lines(self) -> List[str]:
        out: List[str] = []
        if self.dismissals:
            out.append("dismiss " + ", ".join(
                repr(d["topic"]) for d in self.dismissals))
        if self.inserts or self.blocked:
            names = ", ".join(repr(i["topic"]) for i in self.inserts)
            # Name every blocked act: a refusal that says "2 blocked" without
            # saying WHICH two is a refusal the principal cannot act on.
            extra = (f" ({len(self.blocked)} blocked as deleted/renamed: "
                     + ", ".join(repr(b["topic"]) for b in self.blocked) + ")"
                     ) if self.blocked else ""
            out.append(f"re-create {names or 'nothing'}{extra}")
        if self.status_changes:
            out.append("change the status of " + ", ".join(
                repr(s["topic"]) for s in self.status_changes))
        if self.note_updates:
            out.append(f"update {len(self.note_updates)} note(s)")
        return out


def plan_import(con: sqlite3.Connection, entries: List[Dict]) -> ImportPlan:
    """Read-only diff of parsed file entries against the memory table. Writes
    nothing, so it is safe on the refusal path and safe as the bootstrap's
    pending-edit probe."""
    plan = ImportPlan()
    rows = con.execute(
        "SELECT id, topic, status, principal_note FROM memory").fetchall()
    by_key = {r["topic"].casefold(): r for r in rows}
    seen_keys = set()
    for e in entries:
        key = e["topic"].casefold()
        seen_keys.add(key)
        row = by_key.get(key)
        if row is None:
            block = tombstone_block(con, e["topic"])
            if block is not None:
                plan.blocked.append(block)
            else:
                plan.inserts.append(e)
            continue
        if (row["principal_note"] or "").strip() != e["note"]:
            plan.note_updates.append(
                {"id": row["id"], "topic": e["topic"], "note": e["note"]})
        if row["status"] != e["status"]:
            plan.status_changes.append(
                {"id": row["id"], "topic": e["topic"], "old": row["status"],
                 "new": e["status"]})
    for key, row in by_key.items():
        if key not in seen_keys and row["status"] != "dismissed_user":
            plan.dismissals.append({"id": row["id"], "topic": row["topic"]})
    return plan


# ---------------------------------------------------------------------------
# File render / parse
# ---------------------------------------------------------------------------

def render_file(con: sqlite3.Connection) -> str:
    """DB -> canonical memory.md, stamped with the CURRENT generation.

    Read-only: it does NOT bump the counter (write_memory_file does, right
    before calling this), so rendering for inspection can never invalidate the
    file on disk."""
    rows = con.execute(
        "SELECT m.topic, m.status, m.principal_note, m.status_changed_at,"
        " m.dismissed_via, m.last_referenced_briefing_id, b.date AS last_ref_date"
        " FROM memory m LEFT JOIN briefings b ON b.id = m.last_referenced_briefing_id"
        " ORDER BY m.id"
    ).fetchall()
    active = [r for r in rows if r["status"] == "active"]
    inactive = sorted(
        (r for r in rows if r["status"] in ("dormant", "dismissed_user")),
        key=lambda r: r["status_changed_at"] or "",
        reverse=True,  # amendment growth note: Inactive sorted by recency
    )

    def base(r) -> str:
        note = (r["principal_note"] or "").strip()
        return f"- {r['topic']}{SEPARATOR}{note}" if note else f"- {r['topic']}"

    def active_line(r) -> str:
        ref = f" (last referenced: {r['last_ref_date']})" if r["last_ref_date"] else ""
        return base(r) + ref

    def inactive_line(r) -> str:
        when = _day(r["status_changed_at"])
        if r["status"] == "dormant":
            covered = (
                f", last covered {r['last_ref_date']}" if r["last_ref_date"] else ""
            )
            return base(r) + f" (dormant since {when}{covered})"
        # NL-81 §5.4 — an annotation may claim principal agency ONLY when the
        # act arrived through a principal verb surface. Anything the sync
        # inferred is labeled by its MECHANISM; legacy rows (provenance
        # unrecoverable) get neutral copy. We never backfill agency.
        via = r["dismissed_via"]
        if via == "principal":
            return base(r) + f" (dismissed by you {when})"
        if via == "file_sync":
            return base(r) + f" (removed from your memory.md {when})"
        return base(r) + f" (dismissed {when})"

    parts = [_HEADER.format(dormant_days=DORMANT_AFTER_DAYS)]
    parts.append(stamp_line(con))
    parts.append("\n## Active threads\n")
    parts.extend(active_line(r) + "\n" for r in active)
    if not active:
        parts.append("(none — add one above, or via `newslens memory add`)\n")
    parts.append("\n## Inactive\n")
    parts.extend(inactive_line(r) + "\n" for r in inactive)
    if not inactive:
        parts.append("(none)\n")
    return "".join(parts)


def parse_file(text: str) -> List[Dict]:
    """memory.md -> [{topic, note, status}]. Forgiving with layout, loud with
    ambiguity. Under Inactive, the ANNOTATION decides the state: a rendered
    dormant line stays dormant; a "(dismissed by you ...)" line stays
    dismissed_user; a BARE line is a fresh principal demotion ->
    dismissed_user (explicit intent — documented in the header)."""
    entries: List[Dict] = []
    seen: Dict[str, str] = {}
    section: Optional[str] = None
    problems: List[str] = []
    in_comment = False
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if "<!--" in line:
            in_comment = "-->" not in line
            continue
        if in_comment:
            in_comment = "-->" not in line
            continue
        if not line or line.startswith("# ") or line.startswith("("):
            continue
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if heading.startswith("active threads"):
                section = "active"
            elif heading.startswith("inactive"):
                section = "inactive"
            else:
                problems.append(f"line {n}: unknown section heading {line[3:].strip()!r}")
                section = None
            continue
        if line.startswith("- "):
            if section is None:
                problems.append(f"line {n}: thread line before any section heading")
                continue
            body = line[2:].strip()
            status = "active"
            if section == "inactive":
                if _DORMANT_ANN_RE.search(body):
                    status = "dormant"
                    body = _DORMANT_ANN_RE.sub("", body).rstrip()
                else:
                    # All three stopped forms (by-you / mechanism / neutral)
                    # read back as dismissed_user — provenance lives in the DB
                    # column, not in the annotation, so a round-trip can never
                    # launder 'file_sync' into 'principal' (NL-81 §5.4).
                    status = "dismissed_user"
                    for rx in _DISMISSED_ANN_RES:
                        if rx.search(body):
                            body = rx.sub("", body).rstrip()
                            break
                    # else: a BARE line = a stop the principal made in this
                    # file. Explicit intent, but it arrived VIA the file, so it
                    # is provenanced 'file_sync' by the caller — "by you" stays
                    # reserved for verb surfaces (the file can be written by
                    # anyone, including the org: that IS the incident).
                # A demoted line may keep the active-section suffix — strip it
                # so it can't leak into the topic/note (M4 gate fix 2 class).
                body = _LASTREF_ANN_RE.sub("", body).rstrip()
            else:
                # M4 gate fix 2: the header tells the user to KEEP annotations
                # when rearranging — a line moved up to Active with its
                # "(dormant since …)" / "(dismissed by you …)" annotation is
                # REVIVAL INTENT. Strip the annotations (never let them leak
                # into the topic or note, never misread the move as a new
                # thread + a deletion of the real one). All three stopped forms
                # strip, not just the by-you one (NL-81).
                body = _DORMANT_ANN_RE.sub("", body).rstrip()
                for rx in _DISMISSED_ANN_RES:
                    body = rx.sub("", body).rstrip()
                body = _LASTREF_ANN_RE.sub("", body).rstrip()
            topic, _, note = body.partition(SEPARATOR)
            topic = topic.strip().strip("*").strip()
            note = note.strip()
            if not topic:
                problems.append(f"line {n}: empty topic")
                continue
            key = topic.casefold()
            if key in seen:
                problems.append(
                    f"line {n}: duplicate topic {topic!r} (also under {seen[key]})"
                )
                continue
            seen[key] = section
            entries.append({"topic": topic, "note": note, "status": status})
            continue
        problems.append(f"line {n}: unrecognized line {line[:60]!r}")
    if problems:
        raise MemorySyncError(
            "memory.md has problems — fix them (or delete the file to regenerate "
            "from the database): " + "; ".join(problems)
        )
    return entries


# ---------------------------------------------------------------------------
# Dormancy + sync
# ---------------------------------------------------------------------------

def apply_dormancy(
    con: sqlite3.Connection, now_utc: Optional[datetime] = None
) -> List[str]:
    """active -> dormant when now - max(created_at, last-referenced time,
    last status transition) > DORMANT_AFTER_DAYS.

    status_changed_at is in the basis (M4 gate fix 1): an EXPLICIT revival —
    file-move to Active or `memory add` — is a dated transition and must
    reset the 14d clock, or the revival self-reverts in the very sync that
    applied it (a dormant thread is by definition >14d unreferenced). Safe by
    construction: this scans active rows only; seeded rows have
    status_changed_at == created_at (no behavior change); auto-revived rows
    get a fresh reference anyway. Principal note edits still do NOT reset the
    clock (they move updated_at, which is deliberately not in the basis)."""
    now_utc = now_utc or _utc_now()
    cutoff = now_utc - timedelta(days=DORMANT_AFTER_DAYS)
    rows = con.execute(
        "SELECT m.id, m.topic, m.created_at, m.status_changed_at,"
        " b.generated_at AS ref_at"
        " FROM memory m LEFT JOIN briefings b ON b.id = m.last_referenced_briefing_id"
        " WHERE m.status = 'active'"
    ).fetchall()
    went_dormant: List[str] = []
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with con:
        for r in rows:
            basis = max(
                [
                    d
                    for d in (
                        _parse_ts(r["created_at"]),
                        _parse_ts(r["ref_at"]),
                        _parse_ts(r["status_changed_at"]),
                    )
                    if d
                ],
                default=None,
            )
            if basis is not None and basis < cutoff:
                con.execute(
                    "UPDATE memory SET status = 'dormant', status_changed_at = ?,"
                    " updated_at = ? WHERE id = ?",
                    (now_iso, now_iso, r["id"]),
                )
                went_dormant.append(r["topic"])
    return went_dormant


def seed_if_first_run(con: sqlite3.Connection) -> int:
    """Bootstrap ONLY when the memory table is empty AND memory.md absent —
    a migration replay or file edit can never resurrect dismissed threads."""
    count = con.execute("SELECT COUNT(*) AS c FROM memory").fetchone()["c"]
    if count or paths.MEMORY_FILE.exists():
        return 0
    now = _utc_now_iso()
    with con:
        for topic, note in SEED_THREADS:
            con.execute(
                "INSERT OR IGNORE INTO memory"
                " (topic, status, principal_note, status_changed_at,"
                "  created_at, updated_at)"
                " VALUES (?, 'active', ?, ?, ?, ?)",
                (topic, note or None, now, now, now),
            )
    return len(SEED_THREADS)


def _check_stamp(con: sqlite3.Connection, text: str):
    """The recency/pairing precondition (NL-81 §5.2). Returns
    (verdict, detail) where verdict is one of:

      'lawful'    — file gen == DB gen and the pairing identity matches, OR the
                    database predates 0022 (nothing to adjudicate).
      'bootstrap' — the file carries no stamp and the DB has never rendered one
                    (generation 0). The ONE-SHOT adoption path: adopt only if
                    the file has zero pending edits, else treat as stale.
      'stale'     — everything else. NEVER mtime: the file that caused the
                    07-17 incident was freshly written.
    """
    st = sync_state(con)
    if st is None:
        return "lawful", ""
    stamp = parse_stamp(text)
    if stamp is None:
        if st["generation"] == 0:
            return "bootstrap", ""
        return "stale", (
            f"it carries no NewsLens generation stamp; this database is at "
            f"generation {st['generation']}")
    if stamp["identity"] != st["identity"]:
        return "stale", (
            f"it belongs to a DIFFERENT NewsLens database — file identity "
            f"{stamp['identity'][:8]}… (profile {stamp['profile']}), this "
            f"database identity {st['identity'][:8]}… (profile {st['profile']})")
    if stamp["generation"] != st["generation"]:
        return "stale", (
            f"file generation {stamp['generation']}, database generation "
            f"{st['generation']}")
    return "lawful", ""


def sync_memory(con: sqlite3.Connection, *,
                accept_file: bool = False) -> SyncResult:
    """The two-way sync: file -> DB (file wins), dormancy pass, then DB ->
    file in canonical form. Safe to call repeatedly; every mutation is
    reported in the SyncResult.

    NL-81 — the file only wins if it is CURRENT. Order of operations matters:
    parse FIRST (an unreadable file is still the loudest, most actionable
    failure, and you cannot diff a file you cannot read), then the generation
    gate, then the tombstone-aware plan, then apply.

    On refusal NEITHER SIDE IS MUTATED — no import, no dormancy pass, no file
    rewrite — so the state stays exactly as recoverable as it was, and the
    caller decides what a refusal means: the interactive CLI aborts with the
    exits, the embedded call sites (ranking, server) degrade to DB state with a
    loud warning. This function itself never raises on staleness: throwing here
    would kill the 3am edition over a stale FILE, which is strictly worse than
    the disease.

    `accept_file` is the principal's explicit file-wins override. It overrides
    STALENESS ONLY — tombstone blocking and the attribution rule hold
    underneath it, always."""
    result = SyncResult()
    result.seeded = seed_if_first_run(con)

    if paths.MEMORY_FILE.exists():
        try:
            text = paths.MEMORY_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            raise MemorySyncError(
                f"memory.md exists but is not readable ({exc}) — fix its permissions"
            ) from exc
        entries = parse_file(text)
        verdict, detail = _check_stamp(con, text)
        plan = plan_import(con, entries)

        if verdict == "bootstrap":
            # ONE-SHOT adoption of the pre-guard live file: adopt only when it
            # agrees with the database (the 07-17 cleanup verified file-DB
            # agreement, so the live system should adopt cleanly). Any pending
            # edit and it is stale — no silent adoption of an unstamped file
            # that disagrees.
            if plan.is_empty:
                result.bootstrapped = True
                verdict = "lawful"
            else:
                verdict, detail = "stale", (
                    "it carries no NewsLens generation stamp and it disagrees "
                    "with the database")

        if verdict == "stale" and not accept_file:
            result.imported = False
            result.stale_refusal = _stale_refusal_line(
                "it is not the file this database last wrote", detail, plan)
            # Neither side mutated: no import, no dormancy, no render. The
            # ONLY effect of this call is the disclosure above.
            return result

        result.accepted_file = verdict == "stale" and accept_file
        _apply_plan(con, plan, result)

    result.went_dormant = apply_dormancy(con)

    try:
        result.generation = write_memory_file(con)
        result.created_file = True
    except OSError as exc:
        raise MemorySyncError(f"cannot write memory.md ({exc})") from exc
    return result


def _apply_plan(con: sqlite3.Connection, plan: ImportPlan,
                result: SyncResult) -> None:
    """Apply a planned import in ONE transaction. The blocked list is reported,
    never applied — the sync may not INSERT a tombstoned topic even here, on
    the --accept-file path (contract §5.3: not even under --accept-file)."""
    now = _utc_now_iso()
    with con:
        for e in plan.inserts:
            # A file line landing under Inactive arrived VIA the file, so it is
            # provenanced by its mechanism, never "by you" (§5.4).
            con.execute(
                "INSERT INTO memory (topic, status, principal_note,"
                " status_changed_at, created_at, updated_at, dismissed_via)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (e["topic"], e["status"], e["note"] or None, now, now, now,
                 "file_sync" if e["status"] == "dismissed_user" else None))
            result.added.append(e["topic"])
        for u in plan.note_updates:
            con.execute(
                "UPDATE memory SET principal_note = ?, updated_at = ?"
                " WHERE id = ?", (u["note"] or None, now, u["id"]))
            result.notes_updated.append(u["topic"])
        for s in plan.status_changes:
            # A move to Inactive (bare line or any stopped annotation) is
            # explicit intent, but it ARRIVES VIA THE FILE — 'file_sync'. A
            # move back to Active clears provenance: the row isn't stopped.
            con.execute(
                "UPDATE memory SET status = ?, status_changed_at = ?,"
                " updated_at = ?, dismissed_via = ? WHERE id = ?",
                (s["new"], now, now,
                 "file_sync" if s["new"] == "dismissed_user" else None,
                 s["id"]))
            result.status_changed.append(
                f"{s['topic']}: {s['old']}->{s['new']}")
        for d in plan.dismissals:
            # THE 07-17 MISATTRIBUTION: a file-absent row is dismissed by
            # INFERENCE from a file the org may well have written. Labeled by
            # mechanism; it renders "(removed from your memory.md …)".
            con.execute(
                "UPDATE memory SET status = 'dismissed_user',"
                " status_changed_at = ?, updated_at = ?,"
                " dismissed_via = 'file_sync' WHERE id = ?",
                (now, now, d["id"]))
            result.dismissed_by_deletion.append(d["topic"])
    if plan.blocked:
        result.blocked_resurrections.append(_blocked_line(plan.blocked))


# ---------------------------------------------------------------------------
# Ranking-facing surface
# ---------------------------------------------------------------------------

def active_context(con: sqlite3.Connection, cap: int = CONTEXT_CAP) -> List[str]:
    """ACTIVE threads only, most-recently-referenced first (spec §B cap).
    Never-referenced threads rank after referenced ones, newest first."""
    rows = con.execute(
        "SELECT topic FROM memory WHERE status = 'active'"
        " ORDER BY last_referenced_briefing_id IS NULL,"
        " last_referenced_briefing_id DESC, updated_at DESC, id DESC LIMIT ?",
        (cap,),
    ).fetchall()
    return [r["topic"] for r in rows]


def dormant_topics(con: sqlite3.Connection, cap: int = DORMANT_MATCH_CAP) -> List[str]:
    """Dormant threads offered to ranking for MATCH-ONLY revival detection.
    Zero scoring influence by construction (ranking ignores these matches in
    personal_score); dismissed_user is deliberately absent — it never
    auto-revives (ADR-0006)."""
    rows = con.execute(
        "SELECT topic FROM memory WHERE status = 'dormant'"
        " ORDER BY status_changed_at DESC, id DESC LIMIT ?",
        (cap,),
    ).fetchall()
    return [r["topic"] for r in rows]


def revive_matched(
    con: sqlite3.Connection, briefing_id: int, topics: List[str]
) -> List[Dict]:
    """Auto-revival (dormant -> active) for threads matched by stories that
    EARNED their slots. Captures each thread's previous coverage date BEFORE
    updating, so the narrative can say "last covered <date>". Never touches
    dismissed_user. Caller wraps in its own transaction."""
    now = _utc_now_iso()
    revived: List[Dict] = []
    for topic in set(topics):
        row = con.execute(
            "SELECT m.id, m.topic, b.date AS last_covered FROM memory m"
            " LEFT JOIN briefings b ON b.id = m.last_referenced_briefing_id"
            " WHERE lower(m.topic) = lower(?) AND m.status = 'dormant'",
            (topic,),
        ).fetchone()
        if row is None:
            continue
        con.execute(
            "UPDATE memory SET status = 'active', status_changed_at = ?,"
            " last_referenced_briefing_id = ?, updated_at = ? WHERE id = ?",
            (now, briefing_id, now, row["id"]),
        )
        revived.append({"topic": row["topic"], "last_covered": row["last_covered"]})
    return revived


def update_references(
    con: sqlite3.Connection, briefing_id: int, topics: List[str]
) -> int:
    """A briefing referenced these threads — record it (continuity's spine).
    Caller wraps in its own transaction."""
    now = _utc_now_iso()
    n = 0
    for topic in set(topics):
        cur = con.execute(
            "UPDATE memory SET last_referenced_briefing_id = ?, updated_at = ?"
            " WHERE lower(topic) = lower(?) AND status != 'dismissed_user'",
            (briefing_id, now, topic),
        )
        n += cur.rowcount
    return n


# ---------------------------------------------------------------------------
# Continuity context for M5's generate
# ---------------------------------------------------------------------------

def prior_briefing_context(
    con: sqlite3.Connection, for_date: str, max_chars: int = 1500
) -> Optional[Dict]:
    """Structured summary of the most recent briefing BEFORE for_date —
    2-3 sentences per story slot, built deterministically from slots data
    (narrative doesn't exist until M5). Bounded by construction: slots only,
    never full history (spec §B token-budget rule). Returns None when there
    is no prior briefing."""
    import json

    row = con.execute(
        "SELECT id, date, generated_at, story_slots FROM briefings"
        " WHERE date < ? ORDER BY date DESC LIMIT 1",
        (for_date,),
    ).fetchone()
    if row is None:
        return None
    try:
        slots = json.loads(row["story_slots"] or "[]")
    except ValueError:
        return None
    stories = []
    lines = [f"Your previous briefing ({row['date']}) covered:"]
    for s in slots[:5]:
        title = s.get("story_title", "")
        summary = s.get("summary", "")
        tags = [t.get("name") for t in s.get("matched_tags", []) if t.get("name")]
        threads = s.get("matched_memory", [])
        angle_bits = []
        if threads:
            angle_bits.append("thread: " + ", ".join(threads))
        if tags:
            angle_bits.append("tags: " + ", ".join(tags[:3]))
        angle = f" ({'; '.join(angle_bits)})" if angle_bits else ""
        stories.append(
            {
                "slot": s.get("slot"),
                "story_title": title,
                "summary": summary,
                "matched_tags": tags,
                "matched_memory": threads,
                "override": bool(s.get("override")),
            }
        )
        lines.append(f"{s.get('slot')}. {title} — {summary}{angle}")
    text_block = "\n".join(lines)
    if len(text_block) > max_chars:
        text_block = text_block[: max_chars - 1] + "…"
    return {
        "date": row["date"],
        "briefing_id": row["id"],
        "generated_at": row["generated_at"],
        "stories": stories,
        "text_block": text_block,
    }


# ---------------------------------------------------------------------------
# Shared verbs (M7): ONE code path for CLI and web UI
# ---------------------------------------------------------------------------
# Extracted from cli._memory_command so `newslens serve` mutates threads
# through exactly the machinery the CLI verbs use (M7 dispatch requirement),
# instead of a parallel SQL dialect that could drift. Protocol per verb:
# callers sync_memory() FIRST, apply the verb, then write_memory_file() —
# render-only, because a trailing full sync would clobber the verb's own
# change with the pre-verb file (M4 amendment fix).


def add_thread(con: sqlite3.Connection, topic: str, note: Optional[str] = None,
               last_referenced_briefing_id: Optional[int] = None) -> str:
    """Start tracking (or revive) a thread. Returns 'added' | 'revived' |
    'already-active'. `last_referenced_briefing_id` is the M7 follow-from-
    story seam: the edition the follow came from (CLI passes nothing)."""
    now = _utc_now_iso()
    # NL-81 §5.1/§5.3 — THE EXPLICIT LIFT LANE. add_thread is a principal verb
    # surface (the CLI's revive path, the UI create/follow door, every altitude
    # commit), so reaching a tombstoned name HERE is the deliberate "bring it
    # back" the guard exists to require. The lift supersedes the tombstone;
    # nothing is updated or removed. `add` on a live topic appends nothing.
    lift_tombstone(con, topic)
    row = con.execute(
        "SELECT id, status FROM memory WHERE lower(topic) = lower(?)", (topic,)
    ).fetchone()
    if row is not None:
        if row["status"] == "active":
            return "already-active"
        with con:
            if last_referenced_briefing_id is not None:
                # M7 gate finding 8: the follow-from-story seam stamps the
                # edition on the REVIVE branch too (ADR-0010 §5), not just on
                # insert — else "Last picked up" goes stale after a revive.
                con.execute(
                    "UPDATE memory SET status = 'active', status_changed_at = ?,"
                    " updated_at = ?, last_referenced_briefing_id = ?,"
                    " dismissed_via = NULL WHERE id = ?",
                    (now, now, last_referenced_briefing_id, row["id"]),
                )
            else:
                con.execute(
                    "UPDATE memory SET status = 'active', status_changed_at = ?,"
                    " updated_at = ?, dismissed_via = NULL WHERE id = ?",
                    (now, now, row["id"]),
                )
        return "revived"
    with con:
        con.execute(
            "INSERT INTO memory (topic, status, principal_note,"
            " last_referenced_briefing_id, status_changed_at, created_at,"
            " updated_at) VALUES (?, 'active', ?, ?, ?, ?, ?)",
            (topic, (note or "").strip() or None, last_referenced_briefing_id,
             now, now, now),
        )
    return "added"


# ---------------------------------------------------------------------------
# NL-17-M1b — the follow-altitude picker's persistence + Axel's instrument.
# STORED altitude vocabulary (0019): the two resolver rungs + 'narrow' (the
# just-this-story follow, a reader pick or the resolver-failure landing). ''
# is an unmigrated follow (pre-M1b) — renders bare, nothing fabricated.
# ---------------------------------------------------------------------------
STORED_ALTITUDES: Tuple[str, ...] = ("entity", "storyline", "narrow")
ALTITUDE_SOURCES: Tuple[str, ...] = ("auto", "pick", "degrade")


def _log_altitude_event(con: sqlite3.Connection, thread_id: Optional[int],
                        topic: str, kind: str, *, altitude: str = "",
                        confidence: str = "", source: str = "") -> None:
    """Append one row to the follow-altitude instrument (0020, append-only).
    An EXPLICIT follow act only (§F: a lawful write, never read-logging)."""
    con.execute(
        "INSERT INTO follow_altitude_events (thread_id, topic, kind, altitude,"
        " confidence, source, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (thread_id, topic, kind, altitude, confidence, source, _utc_now_iso()))


def _set_altitude_columns(con: sqlite3.Connection, thread_id: int, *,
                          altitude: str, primary_entity: str, disclosure: str,
                          alt_label: str, source: str) -> None:
    con.execute(
        "UPDATE memory SET altitude = ?, primary_entity = ?, disclosure = ?,"
        " alt_label = ?, altitude_source = ?, updated_at = ? WHERE id = ?",
        (altitude, primary_entity, disclosure, alt_label, source,
         _utc_now_iso(), thread_id))


def add_thread_at_altitude(con: sqlite3.Connection, name: str, *,
                           altitude: str, primary_entity: str = "",
                           disclosure: str = "", alt_label: str = "",
                           confidence: str = "", source: str = "auto",
                           origin_story: str = "",
                           last_referenced_briefing_id: Optional[int] = None
                           ) -> Tuple[str, int]:
    """Create (or revive) a follow AT A RESOLVED ALTITUDE — the picker's commit
    path (high/med auto-commit, a low/switch reader pick, or a resolver-failure
    narrow landing). Stores the 0019 disclosure columns and logs a 'commit'
    event for Axel's instrument. Returns (outcome, thread_id) where outcome is
    'added' | 'revived' | 'already-active' (add_thread's vocabulary).

    The topic key is `name` — the entity name, the storyline name, or (for a
    narrow follow) the headline. Following add_thread's lower(topic) uniqueness.

    origin_story (0021, FIX LOOP 1): the story this follow was BORN from (the
    reader-tapped card's canonical topic). Recorded so the origin card recognizes
    its altitude-renamed follow across reload/regenerate — a HIGH/MED commit
    stores the follow under the RESOLVER's name, which is not the story's title.
    Set ONCE: a later switch MOVES the follow's rung but keeps its birthplace, and
    a revive keeps the original origin (first wins). '' leaves name-only
    recognition unchanged (a manually-added / unmigrated thread has no origin).
    """
    if altitude not in STORED_ALTITUDES:
        raise ValueError(
            f"altitude must be one of {list(STORED_ALTITUDES)}, got {altitude!r}")
    if source not in ALTITUDE_SOURCES:
        raise ValueError(
            f"source must be one of {list(ALTITUDE_SOURCES)}, got {source!r}")
    outcome = add_thread(
        con, name, last_referenced_briefing_id=last_referenced_briefing_id)
    row = con.execute(
        "SELECT id FROM memory WHERE lower(topic) = lower(?)", (name,)).fetchone()
    thread_id = row["id"]
    with con:
        _set_altitude_columns(
            con, thread_id, altitude=altitude, primary_entity=primary_entity,
            disclosure=disclosure, alt_label=alt_label, source=source)
        if origin_story:
            # set-once: only fill an EMPTY origin — never rewrite a follow's
            # birthplace (a switch/revive lands here again, and the first origin
            # is the one the origin card keys recognition on).
            con.execute(
                "UPDATE memory SET origin_story = ? WHERE id = ? AND"
                " origin_story = ''", (origin_story, thread_id))
        _log_altitude_event(
            con, thread_id, name, "commit", altitude=altitude,
            confidence=confidence, source=source)
    return outcome, thread_id


def move_follow_altitude(con: sqlite3.Connection, thread_id: int, *,
                         new_name: str, altitude: str, primary_entity: str = "",
                         disclosure: str = "", alt_label: str = "",
                         confidence: str = "", source: str = "pick"
                         ) -> Optional[int]:
    """The SWITCH: the reader taps the other rung. The follow MOVES, never copies
    (mutation law) — the SAME memory row's topic + altitude columns are updated
    in place. Logs a 'correct' event against the prior commit (the reader changed
    the altitude) and a fresh 'commit' at the picked rung. Returns the surviving
    active thread_id, or None if no such row.

    COLLISION REVIVE-MERGE (FIX LOOP 2 R2): when `new_name` is already held by a
    DIFFERENT row, the plain topic UPDATE would hit the 0005 unique index — the
    silent-500 bug (a switch onto a DISMISSED holder's name). Instead the switch
    REVIVE-MERGES onto that holder, the CREATE path's revive precedent
    (add_thread): the holder becomes the active follow at the picked rung, the
    moved row retires, ONE active row remains. The moved follow's birthplace
    (origin_story) carries to the survivor ONLY when the survivor has none
    (set-once — never a rewrite of a birthplace). Events stay coherent — the
    'correct' is logged off the MOVED row (the reader corrected its altitude) and
    the 'commit' against the SURVIVOR — and the whole merge is ONE transaction, so
    any failure rolls back clean with no orphan correction event.
    """
    if altitude not in STORED_ALTITUDES:
        raise ValueError(
            f"altitude must be one of {list(STORED_ALTITUDES)}, got {altitude!r}")
    row = con.execute(
        "SELECT id, topic, origin_story FROM memory WHERE id = ?",
        (thread_id,)).fetchone()
    if row is None:
        return None
    now = _utc_now_iso()
    # a DIFFERENT row already holding new_name (case-insensitive — the 0005 key)?
    clash = con.execute(
        "SELECT id, status, origin_story FROM memory"
        " WHERE lower(topic) = lower(?) AND id != ?",
        (new_name, thread_id)).fetchone()
    with con:
        # the reader corrected the MOVED row's altitude (Axel's instrument).
        _log_altitude_event(con, thread_id, row["topic"], "correct")
        # NL-81 §5.1 — THE RENAME TOMBSTONE. The old key stops resolving, so a
        # stale file carrying the OLD name would otherwise re-INSERT it as a
        # brand-new thread (07-17: the pre-rename "Volkswagen plans significant
        # job cuts" came back as its own row). Blocks re-INSERT of the old name
        # ONLY — no note-mapping across renames (banked v1 cut); the successor
        # key is carried so the disclosure can name the thread's current name.
        if row["topic"].casefold() != new_name.casefold():
            append_tombstone(con, topic=row["topic"], kind="rename",
                             actor="principal", thread_id=thread_id,
                             successor=new_name)
        if clash is None:
            survivor_id = thread_id
            con.execute(
                "UPDATE memory SET topic = ?, updated_at = ? WHERE id = ?",
                (new_name, now, thread_id))
        else:
            # revive-merge onto the holder: it becomes the active follow, the
            # moved row retires — ONE active row for the name.
            survivor_id = clash["id"]
            con.execute(
                "UPDATE memory SET status = 'active', status_changed_at = ?,"
                " updated_at = ?, dismissed_via = NULL WHERE id = ?",
                (now, now, survivor_id))
            if row["origin_story"] and not clash["origin_story"]:
                # set-once: fill only an EMPTY survivor origin with the moved
                # follow's birthplace (never rewrite the survivor's own).
                con.execute(
                    "UPDATE memory SET origin_story = ? WHERE id = ?"
                    " AND origin_story = ''", (row["origin_story"], survivor_id))
            # The merged-away row retires. This arrived through a principal
            # verb (the reader tapped the other rung), so by the §5.4 rule it
            # is 'principal'. NOTE for the held Following round (M1b gate G2,
            # binding carry-forward): merged-away is NOT reader-dismissed —
            # the forensic distinguisher stays the 0020 event pair, not this
            # column, which only answers "did a person's verb cause it".
            con.execute(
                "UPDATE memory SET status = 'dismissed_user',"
                " status_changed_at = ?, updated_at = ?,"
                " dismissed_via = 'principal' WHERE id = ?",
                (now, now, thread_id))
        _set_altitude_columns(
            con, survivor_id, altitude=altitude, primary_entity=primary_entity,
            disclosure=disclosure, alt_label=alt_label, source=source)
        _log_altitude_event(
            con, survivor_id, new_name, "commit", altitude=altitude,
            confidence=confidence, source=source)
    return survivor_id


def record_altitude_correction(con: sqlite3.Connection, topic: str) -> bool:
    """Log that the reader CHANGED a follow's altitude (unfollow, or switch by
    name) — the correction Axel's instrument watches. Idempotent-safe to call
    alongside the state change; False if no such thread. (A dedicated 'correct'
    logger for the unfollow lane, which dismisses by topic; move_follow_altitude
    logs its own correction inline.)"""
    row = con.execute(
        "SELECT id, topic FROM memory WHERE lower(topic) = lower(?)", (topic,)
    ).fetchone()
    if row is None:
        return False
    with con:
        _log_altitude_event(con, row["id"], row["topic"], "correct")
    return True


def medium_correction_stats(con: sqlite3.Connection) -> Dict[str, object]:
    """Axel's OPERATOR-FACING count (no reader surface): of the MEDIUM-confidence
    AUTO commits, how many were corrected (altitude changed / unfollowed) within
    24h. The pre-registered flip is a HUMAN call on this ratio; this only makes
    it observable. Read-only aggregate over the append-only log (0020)."""
    medium_auto = con.execute(
        "SELECT COUNT(*) AS n FROM follow_altitude_events"
        " WHERE kind = 'commit' AND confidence = 'medium' AND source = 'auto'"
    ).fetchone()["n"]
    corrected = con.execute(
        "SELECT COUNT(*) AS n FROM follow_altitude_events c WHERE c.kind = 'commit'"
        "  AND c.confidence = 'medium' AND c.source = 'auto'"
        "  AND EXISTS (SELECT 1 FROM follow_altitude_events r"
        "              WHERE r.kind = 'correct' AND r.thread_id = c.thread_id"
        "                AND julianday(r.occurred_at) >= julianday(c.occurred_at)"
        "                AND julianday(r.occurred_at) - julianday(c.occurred_at)"
        "                    <= 1.0)"
    ).fetchone()["n"]
    ratio = (corrected / medium_auto) if medium_auto else 0.0
    return {"medium_auto_commits": medium_auto,
            "corrected_within_day": corrected,
            "ratio": round(ratio, 4),
            "flip_threshold": 0.2,
            "flip_would_trigger": bool(medium_auto and ratio >= 0.2)}


def dismiss_thread(con: sqlite3.Connection, topic: str, *,
                   via: str = "principal") -> bool:
    """dismissed_user: visible in memory.md, never auto-revives. False if
    no such thread.

    NL-81 §5.4: this is a PRINCIPAL VERB surface (the CLI verb, every UI
    unfollow/stop control), so it stamps dismissed_via='principal' and its
    lines keep the "(dismissed by you …)" copy. `via` exists so an org-lane
    caller can label itself honestly instead of borrowing the principal's
    agency."""
    if via not in DISMISSED_VIA_VALUES:
        raise ValueError(
            f"via must be one of {list(DISMISSED_VIA_VALUES)}, got {via!r}")
    row = con.execute(
        "SELECT id FROM memory WHERE lower(topic) = lower(?)", (topic,)
    ).fetchone()
    if row is None:
        return False
    now = _utc_now_iso()
    with con:
        con.execute(
            "UPDATE memory SET status = 'dismissed_user',"
            " status_changed_at = ?, updated_at = ?, dismissed_via = ?"
            " WHERE id = ?",
            (now, now, via, row["id"]),
        )
    return True


def set_note(con: sqlite3.Connection, topic: str, note: str) -> bool:
    """Set/clear the principal note (the generation prompt reads it
    verbatim). False if no such thread."""
    row = con.execute(
        "SELECT id FROM memory WHERE lower(topic) = lower(?)", (topic,)
    ).fetchone()
    if row is None:
        return False
    with con:
        con.execute(
            "UPDATE memory SET principal_note = ?, updated_at = ? WHERE id = ?",
            (note.strip() or None, _utc_now_iso(), row["id"]),
        )
    return True


def delete_thread(con: sqlite3.Connection, topic: str, *,
                  actor: str = "principal") -> Tuple[bool, str]:
    """M7 SOFT delete (ADR-0010): the thread row is removed from memory (and
    so from memory.md and every UI list), while past briefings stay immutable
    — their written references to the story are baked narrative text and no
    briefing row points at memory, so nothing dangles. This is deliberately
    NOT a redaction of history; it deletes the *tracking*, not the record.
    M7 gate ruling 2: delete is the product's only irreversible verb, so the
    dismissed-only rule is enforced HERE at the shared API — structurally,
    not by UI courtesy (ADR-0010 §4: "the stronger verb offered only from
    that state")."""
    row = con.execute(
        "SELECT id, topic, status FROM memory WHERE lower(topic) = lower(?)",
        (topic,)
    ).fetchone()
    if row is None:
        return False, "no thread with that topic"
    if row["status"] != "dismissed_user":
        return False, ("dismiss the thread first — delete is only offered on "
                       "stopped follows")
    with con:
        # NL-81 §5.1 — the tombstone is what now SURVIVES the hard DELETE. The
        # row is still hard-deleted (ADR-0010 stands); the deletion RECORD is
        # what a stale file can no longer contradict. Same transaction as the
        # DELETE: a delete that committed without its tombstone is the hole.
        append_tombstone(con, topic=row["topic"], kind="delete", actor=actor,
                         thread_id=row["id"])
        con.execute("DELETE FROM memory WHERE id = ?", (row["id"],))
    return True, "deleted"


def write_memory_file(con: sqlite3.Connection) -> Optional[int]:
    """Render-only refresh of memory.md after a verb (see protocol note), and
    THE ONE PLACE the generation counter advances (NL-81 §5.2).

    Bump-then-render, in that order, so the stamp the file carries is the
    counter the DB holds. If the write then fails, the DB is one generation
    ahead of the file — which reads as STALE on the next sync, i.e. the failure
    lands on the refuse side, never on the silently-import side. Returns the
    new generation (None on a pre-0022 database)."""
    from . import paths
    gen = bump_generation(con)
    paths.MEMORY_FILE.write_text(render_file(con), encoding="utf-8")
    return gen
