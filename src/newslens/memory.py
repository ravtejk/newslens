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
  AUTOMATICALLY if a story that earns a briefing slot matches it.
  "(dismissed by you <date>)" marks your explicit dismissals — those never
  auto-revive. Keep the annotation with the line when you rearrange; a bare
  line under Inactive counts as dismissed by you.

  Lines match database rows by topic name (case-insensitive); renaming a
  topic dismisses the old thread and starts a new one. Topic names cannot
  contain " — ".
-->
"""

_DORMANT_ANN_RE = re.compile(r"\(dormant since (\d{4}-\d{2}-\d{2})[^)]*\)\s*$")
_DISMISSED_ANN_RE = re.compile(r"\(dismissed by you (\d{4}-\d{2}-\d{2})\)\s*$")
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

    @property
    def edits_applied(self) -> int:
        return (
            len(self.added) + len(self.notes_updated)
            + len(self.status_changed) + len(self.dismissed_by_deletion)
        )

    def summary_lines(self) -> List[str]:
        out: List[str] = []
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


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
# File render / parse
# ---------------------------------------------------------------------------

def render_file(con: sqlite3.Connection) -> str:
    rows = con.execute(
        "SELECT m.topic, m.status, m.principal_note, m.status_changed_at,"
        " m.last_referenced_briefing_id, b.date AS last_ref_date"
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
        return base(r) + f" (dismissed by you {when})"

    parts = [_HEADER.format(dormant_days=DORMANT_AFTER_DAYS)]
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
                elif _DISMISSED_ANN_RE.search(body):
                    status = "dismissed_user"
                    body = _DISMISSED_ANN_RE.sub("", body).rstrip()
                else:
                    status = "dismissed_user"  # bare line = explicit demotion
                # A demoted line may keep the active-section suffix — strip it
                # so it can't leak into the topic/note (M4 gate fix 2 class).
                body = _LASTREF_ANN_RE.sub("", body).rstrip()
            else:
                # M4 gate fix 2: the header tells the user to KEEP annotations
                # when rearranging — a line moved up to Active with its
                # "(dormant since …)" / "(dismissed by you …)" annotation is
                # REVIVAL INTENT. Strip the annotations (never let them leak
                # into the topic or note, never misread the move as a new
                # thread + a deletion of the real one).
                body = _DORMANT_ANN_RE.sub("", body).rstrip()
                body = _DISMISSED_ANN_RE.sub("", body).rstrip()
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
    now_utc = now_utc or datetime.now(timezone.utc)
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


def sync_memory(con: sqlite3.Connection) -> SyncResult:
    """The two-way sync: file -> DB (file wins), dormancy pass, then DB ->
    file in canonical form. Safe to call repeatedly; every mutation is
    reported in the SyncResult."""
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
        now = _utc_now_iso()
        rows = con.execute(
            "SELECT id, topic, status, principal_note FROM memory"
        ).fetchall()
        by_key = {r["topic"].casefold(): r for r in rows}
        seen_keys = set()
        with con:
            for e in entries:
                key = e["topic"].casefold()
                seen_keys.add(key)
                row = by_key.get(key)
                if row is None:
                    con.execute(
                        "INSERT INTO memory (topic, status, principal_note,"
                        " status_changed_at, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (e["topic"], e["status"], e["note"] or None, now, now, now),
                    )
                    result.added.append(e["topic"])
                    continue
                if (row["principal_note"] or "").strip() != e["note"]:
                    con.execute(
                        "UPDATE memory SET principal_note = ?, updated_at = ?"
                        " WHERE id = ?",
                        (e["note"] or None, now, row["id"]),
                    )
                    result.notes_updated.append(e["topic"])
                if row["status"] != e["status"]:
                    con.execute(
                        "UPDATE memory SET status = ?, status_changed_at = ?,"
                        " updated_at = ? WHERE id = ?",
                        (e["status"], now, now, row["id"]),
                    )
                    result.status_changed.append(
                        f"{e['topic']}: {row['status']}->{e['status']}"
                    )
            for key, row in by_key.items():
                if key not in seen_keys and row["status"] != "dismissed_user":
                    con.execute(
                        "UPDATE memory SET status = 'dismissed_user',"
                        " status_changed_at = ?, updated_at = ? WHERE id = ?",
                        (now, now, row["id"]),
                    )
                    result.dismissed_by_deletion.append(row["topic"])

    result.went_dormant = apply_dormancy(con)

    try:
        paths.MEMORY_FILE.write_text(render_file(con), encoding="utf-8")
        result.created_file = True
    except OSError as exc:
        raise MemorySyncError(f"cannot write memory.md ({exc})") from exc
    return result


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
