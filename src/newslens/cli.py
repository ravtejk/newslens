"""NewsLens command-line interface.

Milestone 1 exposes exactly two commands: `migrate` and `doctor`.
Later milestones add the pipeline verbs — generate (M5), read/listen (M7,
consumption-event logging for the day-30 falsifier) — listed here so the
shape of the CLI is visible, but deliberately not stubbed: an unimplemented
command should not exist yet rather than exist and lie.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="newslens",
        description="NewsLens — memory-threaded daily news briefing (personal prototype).",
        epilog=(
            "Coming in later milestones: generate (M5), read/listen (M7). "
            "Health check: run `newslens doctor` (or scripts/doctor) any time."
        ),
    )
    parser.add_argument("--version", action="version", version=f"newslens {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "migrate",
        help="create/upgrade the local SQLite database (idempotent; safe to re-run)",
    )
    sub.add_parser(
        "doctor",
        help="health check: env, keys, schema, sources (exit 0 = ready for a real run)",
    )

    args = parser.parse_args(argv)

    if args.command == "migrate":
        from . import db, paths

        try:
            ran = db.migrate()
        except Exception as exc:  # CLI boundary: loud, human-readable, nonzero
            print(f"migrate failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        if ran:
            print(f"applied {len(ran)} migration(s): {', '.join(ran)}")
        else:
            print("database already up to date — nothing to apply")
        print(f"database: {paths.DB_PATH}")
        return 0

    if args.command == "doctor":
        from .doctor import run_doctor

        return run_doctor()

    parser.error(f"unknown command: {args.command}")  # unreachable; argparse guards
    return 2


if __name__ == "__main__":
    sys.exit(main())
