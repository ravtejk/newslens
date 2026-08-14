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

import json
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

# NL-139 (NL-133 gate R-B): the topic LENGTH clamp. CONTEXT_CAP bounds how
# MANY topics reach a prompt; nothing bounded how LONG one could be, and a
# topic is the most leveraged string in the analysis prompt — each char costs
# 30 chars of prompt at the worst renderable shape (CONTEXT_CAP lines in the
# memory_context block, plus one prior-briefing P-key title per thread, both
# capped at 15). Measured against `analysis.brief_bound_chars`, IN BOTH
# REGIMES — labelled, because they are different numbers and the first version
# of this comment quoted only the first as if it were operative (QA F-5, fix
# loop 1):
#   * OBSERVED-MAXIMA regime (other fields at their pre-clamp observed maxima):
#     ceiling 112, first breach 113.
#   * AT-THE-CLAMPS regime (the SHIPPED one): ceiling 83, first breach 84.
#     RE-BASED IN NL-142 FIX LOOP 2 — it read 100/101 when "the clamps" meant
#     the vendor clamps only. NL-142 clamped the ingest-owned title and outlet
#     too, and the NL-142 gate (F-G0) then found the vendor host renders as an
#     R-key label on the same map line, so the shipped worst case is now
#     NL-142's: 78,516 chars against a 78,621 bound. A topic costs 30 chars of
#     prompt per char (CONTEXT_CAP memory_context lines plus one P-key title
#     each), so 105 chars of slack is THREE topic characters.
#
# 80 is the value: 25% above 64, the all-time maximum topic length across the
# founder's memory table, and 3 chars inside the SHIPPED ceiling — it was
# quoted as 20, and before fix loop 1 as 32. The invariant is now genuinely
# close to a knife-edge, which is a fact about the margin rather than about
# this clamp (NL-133's own construction rule: floor from observed data,
# ceiling from the bound, land inside both — still satisfied, barely). Pinned
# executable at tests/test_nl139_byte_clamps.py::
# test_the_documented_at_the_clamps_ceilings_are_the_real_ones.
#
# TRUNCATED, and LOUDLY — this is the opposite call from the vendor clamps in
# analysis.py. A thread name is the reader's own words, rendered back to him in
# memory.md, the Following spine and every thread page; silently storing 80
# characters of an 84-character name he wrote is the product renaming his
# thread. So every truncation is disclosed: `SyncResult.truncated_topics` on
# the file-sync path (surfaced by summary_lines like every other sync
# disclosure) and the 'added-truncated' outcome on the verb path.
#
# Clamped at the DOOR, not at the INSERT. Both entry points normalise before
# they look anything up, because `add_thread` resolves tombstones and
# existing rows by `lower(topic)` and `plan_import` keys the file diff by
# casefold — clamping later would make the lookup miss the row the insert
# creates, and every re-sync would insert the same thread again.
TOPIC_MAX_CHARS = 80

VALID_STATUSES = ("active", "dormant", "dismissed_user")


def clamp_topic(topic: str) -> Tuple[str, bool]:
    """NL-139: (stored_name, was_truncated) — the ONE place a thread name is
    shortened, so every door shortens it identically.

    FIVE doors call it (the inventory grew in fix loop 1 — see door 5):

      1. `parse_file`               memory.md, the principal's own file
      2. `add_thread`               the CLI/UI verb
      3. `add_thread_at_altitude`   the follow-from-story picker (keys on a
                                    HEADLINE, so the likeliest to fire)
      4. `cli.py`'s memory handler  runs its own INSERT, not add_thread
      5. `move_follow_altitude`     the RENAME lane — the tree's only
                                    `UPDATE memory SET topic`, and the one the
                                    MODEL can drive (the background settle)

    Doors 1-4 are the `INSERT INTO memory` sites; door 5 is why an
    insert-only inventory is not the whole perimeter.

    Each calls it BEFORE any casefold/lower() lookup it does — that ordering is
    the whole design, not a detail: a clamp applied after the lookup would
    create a row under one name and search for it under another.

    THE CALLER PERIMETER IS PART OF THE CONTRACT (fix loop 1, QA finding F-1):
    a caller that compares, echoes, or unfollows by a name it did NOT put
    through this function is speaking a different key from storage, and the
    divergence is SILENT — it reads as "no such thread" rather than as an
    error. `server._topic_arg` is the door-side clamp for every topic-keyed
    HTTP endpoint for exactly that reason.

    `.rstrip()` because a cut mid-space leaves a trailing blank that would
    then differ from the same name typed again."""
    if len(topic) <= TOPIC_MAX_CHARS:
        return topic, False
    return topic[:TOPIC_MAX_CHARS].rstrip(), True

# FIRST-RUN SEEDING IS KILLED (Stage-0 M1, 2026-07-25; M0 finding F1 / RED-1).
#
# What stood here: SEED_THREADS, the taxonomy contract's §C list of 14 live
# threads (five carrying the principal's steering notes), planted into the
# `memory` table by seed_if_first_run() on any empty-table + absent-memory.md
# database. M0's cold-start pass proved mechanically that those 14 rows reach
# the PAID rank prompt's thread vocabulary, the writer's ACTIVE THREADS block
# WITH the steering notes, the rendered memory.md ("the live threads it's
# tracking for you"), and the Following spine — i.e. a second reader's day-one
# paper would claim a memory they never authored, steered by notes that are
# not theirs.
#
# Killed, not founder-gated: (a) the founder's own install cannot reach it
# anyway — his memory table has rows AND his memory.md exists, so the function
# has returned 0 for him since M4; (b) the only surviving caller shape is his
# own from-zero reinstall, where replanting a 2026-07-04 taxonomy over
# whatever he actually follows today would be wrong, not helpful; (c) a gate
# is a thing that can be tripped, and RED-1's contract is that a non-founder
# profile be structurally unable to trip it. First-run population is now the
# Stage-0 Commissioning (the reader's own first follow); `newslens profile
# create` provisions a 0-byte memory.md — a lawful true-zero start, defence in
# depth on top of this kill.
#
# The list is not lost: it lives in git history (last at fa26e45) and in
# adr/0005-m4-memory-decisions.md §2.

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
    """memory.md exists but cannot be safely interpreted. Loud on purpose.

    NL-17-M1c — THE PAYLOAD-CLASS DISCRIMINATOR (A7, build-blocking). `kind`
    names WHICH failure this is AT THE RAISE SITE, so the branch that renders
    the reader's reason is the branch that produced it. Without it the UI would
    have to substring-match the CLI sentence — and the string that gets matched
    is exactly the string a later copy pass is free to reword, which is how a
    refusal quietly starts rendering the wrong reason (or none).

    The message stays the CLI's sentence, unchanged: two registers, two strings,
    one condition (gate R5 — the register binds reader-facing UI only, and a
    61-word sentence naming `--accept-file` is a good CLI string and an unlawful
    UI one). labels.REFUSAL_MEM_* carry the UI-lane clauses, keyed by `kind`.
    """

    KINDS = ("unreadable", "unparseable", "unwritable")

    def __init__(self, *args, kind: str = "") -> None:
        super().__init__(*args)
        self.kind = kind


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
    # NL-139: file lines whose thread name exceeded TOPIC_MAX_CHARS and was
    # stored short. Rides the same never-silent contract as the guard lines
    # above — the name the reader gets back is not the name he typed, so the
    # sync says so, every time, whether or not the line also created a row.
    truncated_topics: List[str] = field(default_factory=list)
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
        # NL-139: a shortened thread name belongs in the NARROW channel too.
        # The server's follow/thread JSON surfaces exactly guard_lines(), and
        # "we stored a different name than you typed" is precisely the class
        # of fact that channel exists for.
        out.extend(self.truncated_topics)
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
    # NL-139: disclosure lines for file topics parse_file shortened to
    # TOPIC_MAX_CHARS. Deliberately NOT part of `is_empty`: a truncation is
    # something that happened to the FILE's reading, not a pending edit to the
    # database, and counting it as pending would flip a clean bootstrap into a
    # stale refusal on a file that agrees with the DB in every stored byte.
    truncated: List[str] = field(default_factory=list)

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
        # NL-139: collected for EVERY truncated line, not only the ones that
        # insert. A line whose long name clamps onto a row that already exists
        # creates no edit, but the file still said something the database did
        # not store — and the sync's own file rewrite is about to replace that
        # line with the short form.
        if e.get("truncated_from"):
            plan.truncated.append(
                f"memory: thread name shortened to {TOPIC_MAX_CHARS} "
                f"characters — stored {e['topic']!r}, memory.md said "
                f"{e['truncated_from']!r}")
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
            # NL-139 byte-clamp, DOOR 1 of 5 (see TOPIC_MAX_CHARS). BEFORE the
            # casefold key below and before plan_import diffs on that key, so
            # the clamped name is the one the whole import reasons about: the
            # row it creates and the row a re-sync finds are the same row.
            # Not a `problems` entry — an over-long thread name is a name, not
            # a malformed file, and `problems` refuses the WHOLE import, which
            # is the wrong trade for a file the principal hand-edits. The
            # original travels on the entry so the sync can say what it did.
            # Deliberately ahead of the duplicate check, not around it: two
            # long names that clamp to the same string ARE a duplicate now,
            # and it is the collision the file has to hear about.
            original = topic
            topic, was_cut = clamp_topic(topic)
            truncated_from = original if was_cut else ""
            key = topic.casefold()
            if key in seen:
                problems.append(
                    f"line {n}: duplicate topic {topic!r} (also under {seen[key]})"
                    + (f" — this line was shortened to {TOPIC_MAX_CHARS} chars"
                       f" from {truncated_from!r}" if truncated_from else "")
                )
                continue
            seen[key] = section
            entry = {"topic": topic, "note": note, "status": status}
            if truncated_from:
                entry["truncated_from"] = truncated_from
            entries.append(entry)
            continue
        problems.append(f"line {n}: unrecognized line {line[:60]!r}")
    if problems:
        raise MemorySyncError(
            "memory.md has problems — fix them (or delete the file to regenerate "
            "from the database): " + "; ".join(problems),
            kind="unparseable",
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
    # result.seeded stays 0, permanently: first-run seeding was killed in
    # Stage-0 M1 (see the SEED_THREADS obituary near the top of this module).
    # The field is kept so every caller, disclosure line and record that reads
    # "seeded" keeps reading a truthful zero instead of an AttributeError.

    if paths.MEMORY_FILE.exists():
        try:
            text = paths.MEMORY_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            raise MemorySyncError(
                f"memory.md exists but is not readable ({exc}) — fix its permissions",
                kind="unreadable",
            ) from exc
        entries = parse_file(text)
        verdict, detail = _check_stamp(con, text)
        plan = plan_import(con, entries)
        # NL-139: attached BEFORE the stale/bootstrap branching below, because
        # the truncation happened at parse time and is true on every path —
        # including the refusal path, where nothing else about the plan is
        # applied but the reader still deserves to know his file names were
        # read short.
        result.truncated_topics = list(plan.truncated)

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
        raise MemorySyncError(f"cannot write memory.md ({exc})",
                              kind="unwritable") from exc
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
    'already-active' | 'added-truncated'. `last_referenced_briefing_id` is the
    M7 follow-from-story seam: the edition the follow came from (CLI passes
    nothing).

    NL-139: 'added-truncated' is 'added' plus the disclosure that the stored
    name is shorter than the one handed in (see TOPIC_MAX_CHARS). It is a
    fourth outcome rather than a flag because every caller already branches on
    this return value, so a new state cannot be silently ignored by one of
    them — and the follow-from-story seam is exactly where an over-long name
    arrives, since it keys the thread on a HEADLINE."""
    now = _utc_now_iso()
    # NL-139 byte-clamp, DOOR 2 of 5 (see TOPIC_MAX_CHARS). FIRST — ahead of
    # lift_tombstone and the lower(topic) lookup below, both of which must
    # resolve against the name that will actually be stored. Clamping at the
    # INSERT instead would tombstone-lift one name and create another, and
    # would make the second call with the same long name miss its own row.
    topic, truncated = clamp_topic(topic)
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
    return "added-truncated" if truncated else "added"


# ---------------------------------------------------------------------------
# NL-17-M1b — the follow-altitude picker's persistence + Axel's instrument.
# STORED altitude vocabulary (0019): the two resolver rungs + 'narrow' (the
# just-this-story follow, a reader pick or the resolver-failure landing). ''
# is an unmigrated follow (pre-M1b) — renders bare, nothing fabricated.
# ---------------------------------------------------------------------------
STORED_ALTITUDES: Tuple[str, ...] = ("entity", "storyline", "narrow")
# NL-17 M1 — the altitudes a READER may choose. Narrower than STORED_ALTITUDES,
# and the gap is the principal's 2026-08-07 amendment (i): 'narrow' remains a
# STORED value (the seed writes it — that is the instant-commit law's landing,
# system-owned and auto-widenable) but is no longer a CHOOSABLE one. A
# reader-chosen narrow altitude forks a thread's vocabulary permanently, which
# is the junk-data class the amendment names; a seed that merely has not widened
# yet is the same bytes with the opposite lifecycle. The pick door
# (server._api_follow_at) validates against THIS tuple, so the banned row cannot
# be minted even by a request that no rendered control produced.
PICKABLE_ALTITUDES: Tuple[str, ...] = ("entity", "storyline")
# 'seed'    NL-17-M1c: the INSTANT commit. The tap writes a story-seeded thread
#           locally with nothing consulted — distinct from 'auto' (a settle
#           named it) and from 'pick' (the reader chose this rung deliberately).
# 'degrade' is now unreachable from the UI: a failed settle leaves the seeded
#           follow exactly as it was, so there is no separate degrade landing.
#           The value stays supported — historical rows carry it.
ALTITUDE_SOURCES: Tuple[str, ...] = ("auto", "pick", "degrade", "seed")


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
    # NL-139 byte-clamp, DOOR 3 of 5 (see clamp_topic). This one is LOAD-BEARING,
    # not defensive: add_thread stores the clamped name, and the lookup two
    # lines down keys on `name` — with an over-long headline (the seed path's
    # normal input) the unclamped lookup finds no row and `row["id"]` raises
    # TypeError on the follow-from-story door. Clamping here means the add,
    # the id lookup, the altitude columns and the commit event all name the
    # same thread.
    name, name_was_cut = clamp_topic(name)
    outcome = add_thread(
        con, name, last_referenced_briefing_id=last_referenced_briefing_id)
    # add_thread sees an ALREADY-clamped name and so reports a plain 'added';
    # the truncation happened here, and this door owes the same disclosure the
    # others give. Only the created-a-row case carries it: a revive resolves to
    # a row whose (short) name predates this call.
    if name_was_cut and outcome == "added":
        outcome = "added-truncated"
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
                         confidence: str = "", source: str = "pick",
                         log_correction: bool = True,
                         initiator: str = "principal") -> Optional[int]:
    """The SWITCH: the reader taps the other rung. The follow MOVES, never copies
    (mutation law) — the SAME memory row's topic + altitude columns are updated
    in place. Logs a 'correct' event against the prior commit (the reader changed
    the altitude) and a fresh 'commit' at the picked rung. Returns the surviving
    active thread_id, or None if no such row.

    log_correction=False — NL-17-M1c. The BACKGROUND SETTLE rides this same
    lane as a system-initiated re-aim, and a system naming a thread it seeded
    two seconds ago is not the reader correcting the system. Logging it would
    write a 'correct' at the same instant as its own 'commit', so every settled
    medium-confidence follow would self-report as "corrected within 24h" and
    push Axel's instrument toward its 0.2 flip threshold on traffic alone. The
    reader's own switch keeps logging it, which is the signal the flip wants.

    initiator='org' — NL-17-M1c gate F3, and it is the same fix one layer over.
    NL-81's tombstone log is APPEND-ONLY WITH NO EXPIRY: whatever it records is
    what the org will believe about this thread forever, and it is the log the
    lifecycle round will read to render "you stopped following" copy. Stamping
    a SYSTEM re-aim as `actor='principal'` says a person renamed the thread; on
    a settle-merge, `dismissed_via='principal'` says the reader dismissed a row
    they created two seconds earlier and never touched. Both are false
    attributions, accumulating from ship day. The initiator threads the truth
    through: the reader's lanes stay 'principal' byte-for-byte, and the settle
    lane says 'org' — the actor vocabulary the 0022 CHECK already admits — and
    leaves dismissed_via NULL, which is that column's ratified reading ("did a
    person's verb cause it": on the settle lane, none did).

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
    # NL-139 byte-clamp, DOOR 5 — THE RENAME LANE (fix loop 1). The original
    # door inventory (mine, and QA's re-derivation) enumerated `INSERT INTO
    # memory` and found four. This is the tree's only `UPDATE memory SET topic`
    # — a fifth way an unclamped name reaches the column the clamp exists to
    # bound, and the most exposed of the five: on the SETTLE lane `new_name` is
    # MODEL output (`follow_altitude.split_qualifier(res.disclosure)` via
    # server `_settle_onto`), which is precisely the remote-authored class
    # NL-139 is about.
    #
    # Clamped FIRST — ahead of the clash lookup, the rename tombstone's
    # comparison, and the successor key it records — so the name that is
    # STORED, the name a clash resolves against, and the key a stale file's old
    # name is blocked from re-inserting are all ONE key. Clamping after the
    # clash lookup would let two names that clamp together miss each other and
    # then collide on the 0005 unique index.
    new_name, _ = clamp_topic(new_name)
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
        if log_correction:
            _log_altitude_event(con, thread_id, row["topic"], "correct")
        # NL-81 §5.1 — THE RENAME TOMBSTONE. The old key stops resolving, so a
        # stale file carrying the OLD name would otherwise re-INSERT it as a
        # brand-new thread (07-17: the pre-rename "Volkswagen plans significant
        # job cuts" came back as its own row). Blocks re-INSERT of the old name
        # ONLY — no note-mapping across renames (banked v1 cut); the successor
        # key is carried so the disclosure can name the thread's current name.
        if row["topic"].casefold() != new_name.casefold():
            append_tombstone(con, topic=row["topic"], kind="rename",
                             actor=initiator, thread_id=thread_id,
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
            # The merged-away row retires. On a READER switch this arrived
            # through a principal verb (they tapped the other rung), so by the
            # §5.4 rule it is 'principal'. On the SETTLE lane no person's verb
            # caused it — the system merged a row the reader created seconds
            # earlier — so the column reads NULL (gate F3). NOTE for the held
            # Following round (M1b gate G2, binding carry-forward): merged-away
            # is NOT reader-dismissed — the forensic distinguisher stays the
            # 0020 event pair, not this column, which only answers "did a
            # person's verb cause it".
            con.execute(
                "UPDATE memory SET status = 'dismissed_user',"
                " status_changed_at = ?, updated_at = ?,"
                " dismissed_via = ? WHERE id = ?",
                (now, now,
                 "principal" if initiator == "principal" else None,
                 thread_id))
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


# --- NL-17 M1: SETTLE OUTCOMES + the entity column (0024/0025) --------------
# The settle's answer is recorded as an append-only EVENT, never as a flip of a
# memory column (product council 2026-08-08 §5 item 3). Two reasons, and both
# are load-bearing: a column can only ever say what happened LAST, and the
# retry bound needs to know what happened BEFORE.
SETTLE_OUTCOMES: Tuple[str, ...] = (
    "settled_entity",   # an actor was named AND minted/matched (entity_id set)
    "settled_none",     # the settle RAN and AFFIRMATIVELY found nothing broader
                        # — terminal, auto-widenable forever (0024 header, (a))
    "settled_low",      # the settle RAN and came back UNCONFIDENT, holding named
                        # candidates. The SAME silent surface as settled_none; a
                        # DIFFERENT fact, kept apart for two reasons (0025
                        # header): the terminal-none denominator the principal
                        # rules on stays clean, and this row's `detail` carries
                        # the candidates the management surfaces afford.
    "settle_failed",    # the settle could not run to an answer — case (b),
                        # and the ONLY outcome that licenses a retry
)
# THE BOUND: after a settle FAILS, exactly ONE re-settle is permitted. Expressed
# as a limit on CONSECUTIVE trailing failures rather than on the raw attempt
# counter, because the two lanes have different shapes: a failure streak must be
# capped, while a terminal-none thread stays widenable indefinitely (0024
# header) and would otherwise burn the counter and lock itself out of a genuine
# later failure retry. One successful run — of either outcome — clears the
# streak, which is the right reading: the thing being bounded is repeating a
# failure for free, not trying again on new evidence.
SETTLE_FAILURE_RETRIES = 1


def log_settle_outcome(con: sqlite3.Connection, thread_id: Optional[int],
                       topic: str, outcome: str, *, attempt: int = 1,
                       entity_id: Optional[int] = None,
                       detail: str = "") -> None:
    """Append one settle outcome (0025, append-only).

    Raises ValueError on an outcome outside SETTLE_OUTCOMES — FOUR values since
    the fix loop added `settled_low`. The vocabulary is closed in three places
    that must agree (0025's CHECK, SETTLE_OUTCOMES, and this guard), so a
    caller with a typo fails here rather than halfway through a settle
    transaction on an IntegrityError.

    `detail` is MACHINE diagnostics and never reader copy — cases (a) and (b)
    both render nothing on screen, and this column is what keeps that silence
    accountable. The ONE exception is `settled_low`, whose detail carries the
    candidates the management surfaces afford (0025's header says so, and
    encode/decode_settle_candidates are the only code that touches it).
    """
    if outcome not in SETTLE_OUTCOMES:
        raise ValueError(
            f"outcome must be one of {list(SETTLE_OUTCOMES)}, got {outcome!r}")
    con.execute(
        "INSERT INTO follow_settle_events (thread_id, topic, outcome, attempt,"
        " entity_id, detail, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (thread_id, topic, outcome, int(attempt), entity_id, detail,
         _utc_now_iso()))


def encode_settle_candidates(candidates: List[Dict], *,
                             reason: str = "low-confidence",
                             primary_entity: str = "") -> str:
    """The `settle_low` detail payload, composed in ONE place.

    A candidate is `{"altitude": "entity"|"storyline", "disclosure": "<compact
    qualifier name>"}` — the resolver's own two rungs, in its own grammar, with
    nothing added and nothing authored. Anything malformed is dropped rather than
    stored: a candidate the render cannot use is worse than one that is absent,
    because the absent one degrades to silence and the malformed one degrades to
    a broken sentence on a management surface."""
    clean = []
    for c in candidates or []:
        alt = str((c or {}).get("altitude") or "").strip()
        disc = str((c or {}).get("disclosure") or "").strip()
        if alt in PICKABLE_ALTITUDES and disc:
            clean.append({"altitude": alt, "disclosure": disc})
    return json.dumps({"reason": reason, "primary_entity": primary_entity,
                       "candidates": clean}, ensure_ascii=False)


def decode_settle_candidates(detail: str) -> List[Dict]:
    """THE ONLY PARSER of that payload — the 0018/0019 dumb-render law's
    requirement, met by construction: no renderer ever touches the column, they
    are handed a plain list. A malformed or legacy (non-JSON) detail degrades to
    [] rather than raising; this runs on a render path, and a management surface
    losing an affordance is recoverable while a 500 is not."""
    if not (detail or "").strip().startswith("{"):
        return []
    try:
        payload = json.loads(detail)
    except (ValueError, TypeError):
        return []
    out = []
    for c in (payload.get("candidates") or []) if isinstance(payload, dict) else []:
        if not isinstance(c, dict):
            continue
        alt = str(c.get("altitude") or "").strip()
        disc = str(c.get("disclosure") or "").strip()
        if alt in PICKABLE_ALTITUDES and disc:
            out.append({"altitude": alt, "disclosure": disc})
    return out


def settle_candidates(con: sqlite3.Connection,
                      thread_id: Optional[int]) -> List[Dict]:
    """The candidates a thread's LATEST unconfident settle left behind, or [].

    Reads the most recent `settled_low` row and stops — an older one describes a
    story this thread has since moved past, and offering last week's candidates
    beside this week's would be the surface claiming a choice nobody was offered.
    [] on a pre-0025 database, which is the same as "no affordance": the ruling's
    own degenerate case, and the shipped behaviour."""
    if thread_id is None:
        return []
    try:
        row = con.execute(
            "SELECT detail FROM follow_settle_events"
            " WHERE thread_id = ? AND outcome = 'settled_low'"
            " ORDER BY id DESC LIMIT 1", (thread_id,)).fetchone()
    except sqlite3.OperationalError:
        return []
    return decode_settle_candidates(row["detail"]) if row else []


def settle_attempts(con: sqlite3.Connection, thread_id: Optional[int]) -> int:
    """How many settle attempts this thread has on record (0 on a pre-0025 DB,
    which degrades to "never tried" — the safe direction: a missing log can
    license one attempt, never suppress one)."""
    if thread_id is None:
        return 0
    try:
        row = con.execute(
            "SELECT COALESCE(MAX(attempt), 0) AS n FROM follow_settle_events"
            " WHERE thread_id = ?", (thread_id,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["n"] or 0)


def settle_allowed(con: sqlite3.Connection,
                   thread_id: Optional[int]) -> bool:
    """MAY THIS THREAD'S SETTLE RUN? The ONE eligibility predicate — deliberately
    one, because a second copy of this rule is how the two copies disagree.

    Three cases, and the asymmetry between the last two IS the product ruling:

      no history          -> True. The settle at the tap, attempt 1.
      last = settled_none -> True, indefinitely. THE AUTO-WIDEN CASE (a): the
                             thread settled correctly and found no actor, so a
                             later story with new evidence gets a fresh attempt,
                             not a retry. Nothing is being repeated, so nothing
                             needs bounding.
      last = settle_failed-> True only while the TRAILING FAILURE STREAK is
                             within SETTLE_FAILURE_RETRIES. THE BOUND, case (b):
                             one failure earns one re-settle; the re-settle's own
                             failure ends it. Only failure is bounded, because
                             only failure can repeat for free.

    (`settled_entity` never reaches here — a settled thread's altitude is no
    longer 'narrow', and the settle door's mutation-law guard returns first.)

    Enforced SERVER-SIDE, at the door that can spend: a bound the client is
    trusted to honour is not a bound. A pre-0025 database degrades to True —
    the safe direction, since a missing log may license an attempt but must
    never silently suppress one.
    """
    if thread_id is None:
        return True
    try:
        rows = con.execute(
            "SELECT outcome FROM follow_settle_events WHERE thread_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (thread_id, SETTLE_FAILURE_RETRIES + 1)).fetchall()
    except sqlite3.OperationalError:
        return True
    streak = 0
    for r in rows:
        if r["outcome"] != "settle_failed":
            break
        streak += 1
    return streak <= SETTLE_FAILURE_RETRIES


def set_thread_entity(con: sqlite3.Connection, thread_id: int,
                      entity_id: Optional[int]) -> None:
    """Point a thread at its entity (0024's nullable FK). NULL is a first-class
    value here, not an erasure: "storyline thread, no broader concept (yet)".
    Rides the caller's transaction."""
    con.execute(
        "UPDATE memory SET entity_id = ?, updated_at = ? WHERE id = ?",
        (entity_id, _utc_now_iso(), thread_id))


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


def refresh_file_after_publish(
    con: sqlite3.Connection, revived: Optional[List[Dict]] = None
) -> Optional[str]:
    """Re-render memory.md after the promote applied an edition's memory
    effects (NL-108). Returns None on success, or a disclosure line explaining
    why the file was left alone.

    This exists because deferring the memory writes to publication opens a
    window the old ordering did not have. ranking.run_rank renders memory.md at
    the END of the rank stage — which is now BEFORE the transition applies. If
    the file were left showing the pre-publish state, it would still carry a
    matching generation stamp, so the next morning's sync would read it as
    lawful, and the file wins on status (plan_import): a thread this edition
    legitimately revived would be quietly pushed back to dormant by the very
    file that was supposed to mirror it. Publication moves the database, so
    publication must re-take the mirror.

    Two refusals, both inherited rather than invented:

      * NL-81 §5.2 — if the file is not the one this database last wrote, it is
        STALE and nothing may be written to it. Safe to decline: a stale file is
        refused by the next sync too, so it cannot revert anything.
      * M4 gate — a transparency surface never overwrites edits it has not read.
        `plan_import` is the same read-only pending-edit probe the bootstrap
        path uses; the ONLY difference it may legitimately show here is the
        file still calling this edition's revived threads dormant. Any other
        pending edit is a hand edit made while the run was in flight, and the
        file is left for the next sync to reconcile.

    PRECISION, so the next reader does not re-file it as a bug (NL-108 QA F-4):
    "a run that fails downstream leaves memory untouched" is BYTE-exact for the
    memory TABLE, and THREAD-STATE-exact — not byte-exact — for memory.md. A
    failed morning still moves exactly one line of the file: the
    `<!-- newslens-sync: gen=N -->` stamp, re-written by the opening sync and
    by the rank-stage render (pre-existing NL-81 machinery, untouched by
    NL-108). That is by design — the stamp is how the database claims
    authorship of the file it last wrote, and it is the very thing `_check_stamp`
    reads below to decide whether this refresh may write at all. Measured on a
    failed morning (QA probe P-stamp, 2026-08-14): the entire file delta is
    that one comment line, no thread line moves.
    """
    from . import paths

    def _left_alone(why: str) -> str:
        return (f"memory.md was not refreshed after publication — {why}. The "
                "database is correct and the next sync reconciles the file")

    try:
        text = paths.MEMORY_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return _left_alone(f"it could not be read ({exc})")
    verdict, detail = _check_stamp(con, text)
    if verdict != "lawful":
        return _left_alone(
            "it is not the file this database last wrote"
            + (f" ({detail})" if detail else ""))
    try:
        plan = plan_import(con, parse_file(text))
    except Exception as exc:                    # noqa: BLE001 — never crash a
        return _left_alone(f"it could not be parsed ({exc})")   # published run
    revived_keys = {(r.get("topic") or "").casefold() for r in (revived or [])}
    # A revival makes the file say 'dormant' where the database now says
    # 'active' — that one disagreement is this function's whole reason to run.
    unexpected = [
        s for s in plan.status_changes
        if not (s["topic"].casefold() in revived_keys
                and s["old"] == "active" and s["new"] == "dormant")
    ]
    if (plan.inserts or plan.note_updates or plan.dismissals or plan.blocked
            or unexpected):
        return _left_alone(
            "it changed while this run was in flight and the edit has not been "
            "read yet")
    try:
        write_memory_file(con)
    except OSError as exc:
        return _left_alone(f"it could not be written ({exc})")
    return None
