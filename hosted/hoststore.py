"""THE HOST'S MEMORY — bundles as files, one SQLite ledger. Nothing else.

NL-163 Stage-A M2, per the engineering adjudication 2026-08-29 §Q2:
"Storage: bundles as files (/data/streams/<stream>/<date>.json + latest
pointer, atomic tmp+rename), one SQLite (ledger.db: push receipts, read
events)."

THE HOST KNOWS ONLY BUNDLES. There is no product schema here — no stories, no
threads, no follow state, no memory. The Mac is the only machine that
understands what an edition is made of; this service stores a document it was
handed, remembers when it arrived, and remembers when it was read. That is the
entire model, and it is deliberate: every field this file does not have is a
field that cannot drift from the Mac's version of it.

WHY FILES AND NOT A TABLE. A bundle is an immutable document of 100-200KB.
Files give us atomic replace, `cat` as a debugger, a volume snapshot as a
backup, and a restore story that is `newslens push --date` from the Mac's own
artifacts. A blob column would give us none of that and a migration besides.

WHY ONE DATABASE ANYWAY. Receipts and read events are append-only rows that
have to be counted and joined by date — the one thing files are bad at.

Standard library only, on purpose: this module is the half of the service that
does not need Flask, so the suite can exercise every rule in it whether or not
the web dependency is installed.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# The date component of every path and route. Validated everywhere it is
# accepted (Rook's enumeration item 1: no traversal, ever) — a stream or date
# that fails this never reaches the filesystem.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STREAM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

LATEST_NAME = "latest"
LEDGER_NAME = "ledger.db"


class StoreError(RuntimeError):
    """A refusal with a reason. The routes turn these into honest statuses."""


def utc_now_iso() -> str:
    """Host receipt time, ISO-8601 Z. THE OFFLINE STAMP'S CLOCK (Onna, Q4):
    `pushed at HH:MM` is when the edition LANDED here, not when the Mac says it
    sent it — the Mac cannot know the second half of a network hop."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def valid_date(date: str) -> bool:
    """A real calendar date in the ruled shape.

    The shape check is the traversal guard; the CALENDAR check is why
    `2026-13-99` is not an edition. Without it a shape-valid nonsense file
    would be listed in the archive and then raise inside the weekday lookup
    while rendering — a 500 where a 404 is the honest answer."""
    if not DATE_RE.match(date or ""):
        return False
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def valid_stream(stream: str) -> bool:
    return bool(STREAM_RE.match(stream or ""))


def _scratch(path: Path) -> Path:
    """A scratch name for `path` that NO OTHER WRITER CAN BE USING.

    Four properties, each load-bearing: the same DIRECTORY as the target (so
    the `os.replace` is a rename within one filesystem, which is the only kind
    that is atomic); a leading DOT and a `.tmp` suffix, never `.json` (so
    `dates()` cannot read a write in flight as an edition); and a pid+random
    tail (so two writers never share the inode).

    The shared name this replaced was measured at 16, 17 and 20 server errors
    per 48 concurrent same-date pushes by three separate hands."""
    return path.with_name(f".{path.name}.{os.getpid()}-{os.urandom(4).hex()}.tmp")


class HostStore:
    def __init__(self, root):
        self.root = Path(root)

    # -- paths ------------------------------------------------------------

    def stream_dir(self, stream: str) -> Path:
        if not valid_stream(stream):
            raise StoreError(f"not a stream name: {stream!r}")
        return self.root / "streams" / stream

    def edition_path(self, stream: str, date: str) -> Path:
        if not valid_date(date):
            raise StoreError(f"not an edition date: {date!r}")
        return self.stream_dir(stream) / f"{date}.json"

    # -- bundles ----------------------------------------------------------

    def write_edition(self, stream: str, date: str, raw: bytes) -> None:
        """Atomic tmp+rename, the same law the Mac's artifact write follows.

        A reader mid-request must never catch a half-written document: on this
        machine the only reader is a Flask worker serving a phone, and a
        truncated edition would render as a broken page rather than an error.

        THE SCRATCH NAME IS PER-WRITER, and that is what makes the sentence
        above true when two writers land the same date at once — the shipped
        image runs `gunicorn --workers 2`, and the push client's own
        retry-after-timeout is a second concurrent PUT with no second human
        involved. Concurrent same-date writers each publish a COMPLETE
        document; the last `os.replace` wins. Through one shared
        `<date>.json.tmp` they did not: writer B's `open(...,"wb")` truncated
        the inode A had already fsynced, A's replace published B's half-written
        bytes, and A's own replace then died on a file B had unlinked."""
        path = self.edition_path(stream, date)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = _scratch(path)
        try:
            with open(tmp, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(tmp), str(path))
        finally:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass

    def read_edition(self, stream: str, date: str) -> Optional[Dict]:
        try:
            raw = self.edition_path(stream, date).read_text(encoding="utf-8")
        except (OSError, StoreError):
            return None
        try:
            bundle = json.loads(raw)
        except ValueError:
            return None
        return bundle if isinstance(bundle, dict) else None

    def edition_sha(self, stream: str, date: str) -> Optional[str]:
        bundle = self.read_edition(stream, date)
        return (bundle or {}).get("content_sha256")

    def dates(self, stream: str) -> List[str]:
        """Every edition this stream holds, oldest first.

        THE ARCHIVE IS EXACTLY THIS LIST and nothing else. Editions published
        before the bundle existed are not here and will not be reconstructed
        (the no-seeding ruling): day one is honestly empty."""
        try:
            names = os.listdir(self.stream_dir(stream))
        except (OSError, StoreError):
            return []
        out = [n[:-5] for n in names
               if n.endswith(".json") and valid_date(n[:-5])]
        return sorted(out)

    # -- the latest pointer -----------------------------------------------

    def set_latest(self, stream: str, date: str) -> None:
        """Pointer file, atomically replaced. It only ever moves FORWARD: a
        re-push of an older date (the `newslens push --date` retry path) must
        not make yesterday the paper of record.

        Per-writer scratch name for the same reason as `write_edition`: two
        workers landing the same morning both move this pointer, and on one
        shared `latest.tmp` the second to arrive deleted the first's file out
        from under it."""
        current = self.latest(stream)
        if current and current > date:
            return
        path = self.stream_dir(stream) / LATEST_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = _scratch(path)
        try:
            tmp.write_text(date, encoding="utf-8")
            os.replace(str(tmp), str(path))
        finally:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass

    def latest(self, stream: str) -> Optional[str]:
        """The pointer, VERIFIED against the file it points at.

        A pointer naming a date whose bundle is gone (a half-restored volume,
        a hand-deleted file) would otherwise serve a 404 as the front page; the
        directory listing is the honest fallback."""
        try:
            date = (self.stream_dir(stream) / LATEST_NAME).read_text(
                encoding="utf-8").strip()
        except (OSError, StoreError):
            date = ""
        if valid_date(date) and self.edition_path(stream, date).exists():
            return date
        dates = self.dates(stream)
        return dates[-1] if dates else None

    # -- the ledger -------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.root / LEDGER_NAME), timeout=5.0)
        con.row_factory = sqlite3.Row
        # WAL so a read never blocks the push that is landing behind it.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS push_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream TEXT NOT NULL,
                date TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                bytes INTEGER NOT NULL,
                pushed_at TEXT NOT NULL,
                outcome TEXT NOT NULL)""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS read_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream TEXT NOT NULL,
                date TEXT NOT NULL,
                kind TEXT NOT NULL,
                user_id TEXT NOT NULL,
                ts TEXT NOT NULL)""")
        con.commit()
        return con

    def record_push(self, stream: str, date: str, sha: str, size: int,
                    outcome: str, pushed_at: Optional[str] = None) -> str:
        """One row per accepted push. `outcome` is stored, not derived, so the
        operator can tell a first arrival from a replacement months later."""
        stamp = pushed_at or utc_now_iso()
        con = self.connect()
        try:
            con.execute(
                "INSERT INTO push_receipts (stream, date, content_sha256, "
                "bytes, pushed_at, outcome) VALUES (?, ?, ?, ?, ?, ?)",
                (stream, date, sha, size, stamp, outcome))
            con.commit()
        finally:
            con.close()
        return stamp

    def pushed_at(self, stream: str, date: str) -> Optional[str]:
        """When this edition LANDED — the first arrival of the bytes now on
        disk. A no-op re-push does not move it (nothing landed), and a genuine
        replacement does (a different document arrived at a different time)."""
        con = self.connect()
        try:
            row = con.execute(
                "SELECT pushed_at FROM push_receipts WHERE stream = ? AND "
                "date = ? AND outcome != 'unchanged' ORDER BY id DESC LIMIT 1",
                (stream, date)).fetchone()
        finally:
            con.close()
        return row["pushed_at"] if row else None

    def record_read(self, stream: str, date: str, kind: str, user_id: str) -> None:
        """One row per AUTHENTICATED EDITION GET (adjudication Q6).

        THE THREE LIMITATIONS, on the record and unfixable at this layer:
        (i) the Mac's `consumption_events` stays dark for phone reads, so
        ADR-0010 §3's day-30 instrument undercounts once phone reading starts;
        (ii) an edition served by the service worker from the cache never
        reaches this machine and is invisible here — offline mornings do not
        appear in this table; (iii) these are reads on a rented host.
        There is deliberately NO client event API: an inbound write channel is
        the thing Stage B's vocabulary pin exists to prevent."""
        con = self.connect()
        try:
            con.execute(
                "INSERT INTO read_events (stream, date, kind, user_id, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (stream, date, kind, user_id, utc_now_iso()))
            con.commit()
        finally:
            con.close()

    def counts(self) -> Dict[str, int]:
        con = self.connect()
        try:
            return {
                "push_receipts": con.execute(
                    "SELECT COUNT(*) AS n FROM push_receipts").fetchone()["n"],
                "read_events": con.execute(
                    "SELECT COUNT(*) AS n FROM read_events").fetchone()["n"],
            }
        finally:
            con.close()


def weekday_name(date: str) -> str:
    """`Sunday` for 2026-08-23 — the §7 stamp's day word, computed from the
    edition date rather than trusted from the envelope."""
    return time.strftime("%A", time.strptime(date, "%Y-%m-%d"))
