"""SQLite connection helper + migration runner.

Stdlib-only by design: the doctor script validates that the schema applies
cleanly *before* `pip install` has happened, so nothing here may import
third-party packages.

Migration model (deliberately boring):
  * migrations/ holds numbered .sql files applied in lexicographic order.
  * Applied filenames are recorded in schema_migrations.
  * Each .sql file carries its own BEGIN/COMMIT and must be safe to re-apply
    (IF NOT EXISTS everywhere) — the record insert is not atomic with the
    script, and re-running `newslens migrate` must always be harmless
    (ENGINEERING.md idempotency rule).

Read/write discipline (QA fix loop 1): migrate() is the ONLY writer in this
module. The question-shaped functions (applied_migrations, pending_migrations)
are read-only by construction — they never create the database file, its
parent directories, or any table. The doctor depends on this: a health check
must not mutate the state it is diagnosing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional, Union
from urllib.request import pathname2url

from . import paths

PathLike = Union[str, Path]


def connect(db_path: Optional[PathLike] = None) -> sqlite3.Connection:
    """Open (creating parent dirs if needed) with foreign keys enforced.

    PRAGMA foreign_keys is per-connection in SQLite, so every code path must
    come through here or FK constraints silently stop being enforced.
    """
    path = Path(db_path) if db_path is not None else paths.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def connect_readonly(db_path: Optional[PathLike] = None) -> sqlite3.Connection:
    """Open an EXISTING database read-only (SQLite URI mode=ro).

    Never creates the file or parent dirs; any write attempt on this
    connection fails loudly. Raises sqlite3.OperationalError if the file
    cannot be opened at all.
    """
    path = Path(db_path) if db_path is not None else paths.DB_PATH
    con = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _ensure_migrations_table(con: sqlite3.Connection) -> None:
    # Writes — for migrate()'s use only; the query API below must stay read-only.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )


def migration_files(migrations_dir: Optional[PathLike] = None) -> List[Path]:
    d = Path(migrations_dir) if migrations_dir is not None else paths.MIGRATIONS_DIR
    if not d.is_dir():
        raise FileNotFoundError(
            f"migrations directory not found: {d} — run from the prototype checkout"
        )
    return sorted(p for p in d.glob("*.sql") if p.is_file())


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def applied_migrations(con: sqlite3.Connection) -> List[str]:
    """Read-only: an empty/pre-migration database simply has nothing applied."""
    if not _table_exists(con, "schema_migrations"):
        return []
    rows = con.execute("SELECT filename FROM schema_migrations ORDER BY filename")
    return [r["filename"] for r in rows]


def pending_migrations(
    db_path: Optional[PathLike] = None, migrations_dir: Optional[PathLike] = None
) -> List[str]:
    """Names of migration files not yet applied to the database at db_path.

    Read-only by construction: a missing database file means everything is
    pending — it is NOT created as a side effect of asking the question.
    """
    files = [p.name for p in migration_files(migrations_dir)]
    path = Path(db_path) if db_path is not None else paths.DB_PATH
    if not path.exists():
        return files
    con = connect_readonly(path)
    try:
        done = set(applied_migrations(con))
    finally:
        con.close()
    return [name for name in files if name not in done]


def migrate(
    db_path: Optional[PathLike] = None, migrations_dir: Optional[PathLike] = None
) -> List[str]:
    """Apply all pending migrations in order. Returns the filenames applied.

    Safe to call repeatedly (idempotent). Errors propagate: a failing
    migration must be loud, never half-recorded.
    """
    con = connect(db_path)
    try:
        con.isolation_level = None  # autocommit; each script manages its own transaction
        _ensure_migrations_table(con)
        done = set(applied_migrations(con))
        ran: List[str] = []
        for path in migration_files(migrations_dir):
            if path.name in done:
                continue
            con.executescript(path.read_text(encoding="utf-8"))
            con.execute(
                "INSERT INTO schema_migrations (filename) VALUES (?)", (path.name,)
            )
            ran.append(path.name)
        return ran
    finally:
        con.close()


def table_names(con: sqlite3.Connection) -> List[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r["name"] for r in rows]
