"""NewsLens command-line interface.

Milestone 2 exposes three commands: `migrate`, `doctor`, `ingest`.
Later milestones add the remaining pipeline verbs — generate (M5), read/listen
(M7, consumption-event logging for the day-30 falsifier; v1 is on-demand only
per DECISIONS.md 2026-07-03) — listed here so the shape of the CLI is visible,
but deliberately not stubbed: an unimplemented command should not exist yet
rather than exist and lie.
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
    ingest_p = sub.add_parser(
        "ingest",
        help="pull enabled sources into source_items (idempotent per UTC fetch-day); "
        "adds the capped Sonar discovery call when PERPLEXITY_API_KEY is set",
    )
    ingest_p.add_argument(
        "--no-discovery",
        action="store_true",
        help="skip the Sonar discovery call even if a key is present (RSS only)",
    )
    rank_p = sub.add_parser(
        "rank",
        help="cluster + rank ingested items into the day's story budget "
        "(top 1-5, corroboration-labeled; writes the briefings row). "
        "Separate from `ingest` on purpose: pull and editorial pass are "
        "independently re-runnable; M5's `generate` will chain them.",
    )
    rank_p.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="briefing date to write (default: today, local). Candidate items "
        "come from the recency window at run time: since your last briefing, "
        "capped at 14 days",
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

    if args.command == "rank":
        import re as _re

        from . import config, ranking

        if args.date and not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            print(f"--date must be YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
            return 2
        config.load_env()
        try:
            report = ranking.run_rank(date=args.date)
        except ranking.RankingError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except config.SourcesParseError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:  # CLI boundary: loud, human-readable, nonzero
            print(f"rank failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        print(f"story budget for {report.date} — {len(report.slots)} of 5 slots filled")
        print(
            f"  (from {report.item_count} items -> {report.cluster_count} clusters; "
            f"override pool {report.override_pool_size}, "
            f"fired: {'yes' if report.override_fired else 'no'})"
        )
        print(
            f"  candidate window: {report.window_days:g}d ({report.window_basis}); "
            f"ingested history: {report.history_days:g}d"
        )
        for s in report.slots:
            tags = ", ".join(t["name"] for t in s.matched_tags) or "—"
            mem = (" | threads: " + ", ".join(s.matched_memory)) if s.matched_memory else ""
            fa = " | followed analyst" if s.followed_analyst else ""
            print(f"\n  {s.slot}. {s.story_title}")
            print(f"     {s.summary}")
            print(
                f"     [{s.corroboration_label}] world {s.world_impact}/10, "
                f"personal {s.personal_score:.2f} | tags: {tags}{mem}{fa}"
            )
            if s.override:
                print(f"     >> {s.override_label}")
        print(f"\n  Note: {report.caveat}")
        for warning in report.warnings:
            print(f"  ⚠ {warning}")
        usd = report.token_usage and ranking.usage_to_usd(report.token_usage)
        if usd:
            print(
                f"  cost: {report.token_usage.get('prompt_tokens')}+"
                f"{report.token_usage.get('completion_tokens')} tokens ≈ ${usd:.4f} "
                "(logged to briefings.token_cost + ranking_runs)"
            )
        return 0

    if args.command == "ingest":
        from . import config, ingest

        config.load_env()  # .env keys visible to the discovery seam
        try:
            report = ingest.run_ingest(with_discovery=not args.no_discovery)
        except config.SourcesParseError as exc:
            print(str(exc), file=sys.stderr)  # the polite refusal, verbatim
            return 1
        except Exception as exc:  # CLI boundary: loud, human-readable, nonzero
            print(f"ingest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        print(
            f"ingest: {len(report.succeeded)} of {report.attempted} sources ok — "
            f"{report.items_new} new item(s), {report.items_updated} updated, "
            f"{report.items_skipped} skipped (missing url/title)"
        )
        for warning in report.warnings:
            print(f"  ⚠ {warning}")
        if report.degradation_message:
            print(f"  ⚠ {report.degradation_message}")
            for name, reason in sorted(report.failed.items()):
                print(f"      ✗ {name}: {reason}")
        print(f"  discovery: {report.discovery_status}")
        if not report.any_success:
            print("ingest failed: no source could be fetched this run", file=sys.stderr)
            return 1
        return 0

    parser.error(f"unknown command: {args.command}")  # unreachable; argparse guards
    return 2


if __name__ == "__main__":
    sys.exit(main())
