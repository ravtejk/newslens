"""NewsLens command-line interface.

Commands as of milestone 7: `migrate`, `doctor`, `ingest`, `rank`,
`memory` (list/add/dismiss/note), `generate` (the full on-demand briefing),
and `serve` (the local web UI). Consumption logging for the day-30 falsifier
shipped as server-side events (page view = read, episode play = listen) —
not as CLI verbs, by design: the UI is the consumption surface (ADR-0010).
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__


def main(argv: Optional[List[str]] = None) -> int:
    from . import paths
    from .memory_core import PROVENANCE_VALUES  # stdlib-only import; cheap
    paths.allow_real_paths()  # the real entrypoint (incident guard, 2026-07-14)
    parser = argparse.ArgumentParser(
        prog="newslens",
        description="NewsLens — memory-threaded daily news briefing (personal prototype).",
        epilog=(
            "The web UI is `newslens serve` (reads/listens are logged there). "
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
    memory_p = sub.add_parser(
        "memory",
        help="the live threads NewsLens tracks for you — list/add/dismiss/note. "
        "Same data as hand-editing memory.md; every verb syncs the file first "
        "and rewrites it after (taxonomy contract §F: explicit actions only, "
        "nothing is ever inferred from reading behavior)",
    )
    memory_sub = memory_p.add_subparsers(dest="memory_command", required=True)
    mem_list = memory_sub.add_parser("list", help="show threads")
    mem_list.add_argument(
        "--status", choices=["active", "dormant", "dismissed_user", "all"], default="all"
    )
    mem_add = memory_sub.add_parser("add", help="start tracking a thread")
    mem_add.add_argument("topic")
    mem_add.add_argument("--note", default="")
    mem_dismiss = memory_sub.add_parser(
        "dismiss", help="stop tracking a thread (kept for audit; excluded from context)"
    )
    mem_dismiss.add_argument("topic")
    mem_note = memory_sub.add_parser(
        "note",
        help="set the note the generation prompt reads verbatim — this is the "
        "explicit 'more/less like this' mechanism",
    )
    mem_note.add_argument("topic")
    mem_note.add_argument("text")
    mem_close = memory_sub.add_parser(
        "close",
        help="record that a thread reached its end (collect-now closure "
        "register, migration 0015) — the explicit-action lane (§F). Writes a "
        "dated closure fact; refuses a second closure on the same thread.",
    )
    mem_close.add_argument("topic")
    mem_close.add_argument("--reason", default="",
                           help="why the thread closed (stored on the record)")

    analyze_p = sub.add_parser(
        "analyze",
        help="M9: analysis briefs for the date's depth-tier stories "
             "(fetch + Sonar + cited synthesis; briefing record untouched)")
    analyze_p.add_argument("--date", default=None,
                           help="briefing date to analyze (default: latest)")

    sub.add_parser(
        "diagnose",
        help="read-only readout: the day-30 falsifier + generation record, "
             "self-caveating ($0, offline)")

    serve_p = sub.add_parser(
        "serve",
        help="local web UI at 127.0.0.1 (Today / Following / Archive)")
    serve_p.add_argument("--port", type=int, default=8484,
                         help="port to bind on localhost (default 8484)")

    gen_p = sub.add_parser(
        "generate",
        help="the full on-demand briefing (M5): ingest -> rank -> narrative -> "
        "podcast script; renders to stdout + a dated file under data/briefings/. "
        "Voice A is the voice of record (editorial review A1; alternation ended).",
    )
    gen_p.add_argument(
        "--date", default=None, metavar="YYYY-MM-DD",
        help="briefing date (default: today, local)",
    )
    gen_p.add_argument(
        "--variant", choices=["A", "B"], default=None,
        help="force a voice variant; forcing the retired variant (B) renders "
        "a clearly-labeled SAMPLE file and never touches the briefing of record "
        "(samples always skip the refresh chain)",
    )
    gen_p.add_argument(
        "--no-refresh", action="store_true",
        help="skip the ingest+rank chain and write from the existing briefing "
        "row (narrative-only iteration)",
    )
    gen_p.add_argument(
        "--no-threads", action="store_true",
        help="cold-start SAMPLE: render with thread/memory context emptied "
        "(tags kept) to a labeled file; the briefing of record is untouched",
    )

    mb_p = sub.add_parser(
        "memory-backfill",
        help="write the NL-63 memory moat (delta ledger + standing state) for an "
        "ALREADY-PUBLISHED edition whose memory pass never ran (a --no-refresh "
        "record completion). Reconstructs the pass from PERSISTED rows only; the "
        "edition's narrative/script are NEVER touched. Idempotent; refuses rather "
        "than fabricate when the source arc is unrecoverable.",
    )
    mb_p.add_argument(
        "--date", default=None, metavar="YYYY-MM-DD",
        help="edition date to backfill (default: today, local)",
    )
    mb_p.add_argument(
        "--force", action="store_true",
        help="NL-72 override: backfill even when a thread the pass would move "
        "already carries activity NEWER than the target date. Without --force "
        "the backfill REFUSES (stamping older-dated state built from future "
        "ledger entries poisons strict prior-coverage reads); --force proceeds "
        "and DISCLOSES the choice in a warning.",
    )

    mp_p = sub.add_parser(
        "memory-mark-provenance",
        help="SUPERVISED provenance mark on ONE ledger delta (migration 0014, "
        "the poisoned-antecedent bound). Keyed by a SELECT-verified delta id; "
        "prints the delta text it grades and asks nothing (run only with the "
        "principal's word). Refuses on an unknown or already-marked id. A "
        "source-echo / external-synthesis mark stops that delta from ever "
        "licensing a repetition-word antecedent; it never touches the delta's "
        "own text (append-only).",
    )
    mp_p.add_argument(
        "--delta-id", type=int, required=True, metavar="N",
        help="thread_deltas.id to mark (SELECT-verify against the real DB first)",
    )
    mp_p.add_argument(
        "--provenance", required=True,
        choices=list(PROVENANCE_VALUES),
        help="the grade to record",
    )
    mp_p.add_argument(
        "--reason", default="",
        help="the human/basis note stored with the mark",
    )

    rs_p = sub.add_parser(
        "memory-repair-state",
        help="NL-73: rewrite the standing state for threads whose latest LIVE "
        "delta postdates their state (the shape a failed state rewrite leaves — "
        "it otherwise self-heals only on the thread's next real move). Full-"
        "ledger regeneration per the write law, stamped at the latest delta's "
        "date; cap pre-checked; SPENDS (one state-rewrite LLM call per stale "
        "thread); refuses when nothing is stale.",
    )
    rs_group = rs_p.add_mutually_exclusive_group(required=True)
    rs_group.add_argument(
        "--thread-id", type=int, metavar="N",
        help="repair exactly this thread (memory.id)",
    )
    rs_group.add_argument(
        "--all", action="store_true",
        help="sweep and repair every stale thread",
    )

    mbase_p = sub.add_parser(
        "memory-baseline",
        help="NL-77: write the cold-start BACKGROUNDER (entry-zero baseline) for "
        "followed threads with an EMPTY ledger — the 'How we got here' founding "
        "floor, one analyst-model call each (~$0.01-0.02, external-synthesis, "
        "cite currency '(baseline, <date>)'). Cap pre-checked; SPENDS; refuses "
        "when nothing awaits; a refusal is honest (never fabricated). NOTE: the "
        "retroactive sweep is a principal checkpoint — thread renames/deletes "
        "land first.",
    )
    mbase_group = mbase_p.add_mutually_exclusive_group(required=True)
    mbase_group.add_argument(
        "--thread-id", type=int, metavar="N",
        help="write the baseline for exactly this thread (memory.id)",
    )
    mbase_group.add_argument(
        "--all", action="store_true",
        help="sweep every followed empty-ledger thread awaiting a baseline",
    )
    mbase_p.add_argument(
        "--date", default=None, metavar="YYYY-MM-DD",
        help="baseline as-of date (default: today, local; a pending intent's "
        "own date wins when one exists)",
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

    if args.command == "memory":
        return _memory_command(args)

    if args.command == "analyze":
        from . import analysis, db as db_mod

        db_mod.migrate()
        try:
            report = analysis.run_analysis(date=args.date)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"analysis — {report['date']} · model {report['model']} · "
              f"${report['total_usd']:.4f}")
        for s in report["per_story"]:
            print(f"  slot {s['slot']} ({s['tier']}): {s['outcome']} — "
                  f"{s['detail'][:100]} (fetch {s['fetch_ok']}/{s['fetch_attempted']},"
                  f" sonar: {s['sonar'][:40]}, ${s['cost_usd']:.4f})")
        for w in report["warnings"]:
            print(f"  ⚠ {w}")
        if report["derating"]:
            print("  !! DERATING under the cap — escalation-flag class "
                  "(never absorbed silently)")
        return 0

    if args.command == "diagnose":
        from . import diagnose

        # M8 gate residual 1: the verdict instrument is READ-ONLY — no
        # migrate, no file creation; a fresh/behind DB renders an honestly
        # empty readout instead of being mutated by its own measurement.
        print(diagnose.run_diagnose())
        return 0

    if args.command == "serve":
        from . import server

        return server.serve(port=args.port)

    if args.command == "generate":
        import re as _re
        from datetime import datetime as _dt

        from . import config, generate

        if args.date:
            ok_shape = bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date))
            if ok_shape:
                try:
                    _dt.strptime(args.date, "%Y-%m-%d")
                except ValueError:
                    ok_shape = False
            if not ok_shape:
                print(
                    f"--date must be YYYY-MM-DD (a real calendar date), "
                    f"got {args.date!r}", file=sys.stderr,
                )
                return 2
        config.load_env()

        def _progress(label: str, model):
            # NL-88: a terminal `generate` is a ~40-min run; print each phase
            # boundary as it happens so it's no longer a silent wait. To stderr,
            # so stdout stays the clean narrative artifact.
            tail = f" [{model}]" if model else ""
            print(f"  … {label}{tail}", file=sys.stderr, flush=True)

        try:
            rep = generate.run_generate(
                date=args.date,
                variant_override=args.variant,
                refresh=not args.no_refresh,
                no_threads=args.no_threads,
                progress=_progress,
            )
        except generate.GenerateError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except config.SourcesParseError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:  # CLI boundary: loud, human-readable, nonzero
            print(f"generate failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        print(rep.narrative_text)
        print()
        label = "SAMPLE (not the briefing of record)" if rep.sample else "briefing of record"
        print(f"[voice {rep.variant} — {label}]")
        if rep.ingest_summary:
            print(f"  ingest: {rep.ingest_summary}")
        print(
            f"  words: narrative {rep.narrative_words}, script {rep.script_words}"
            f" | continuity: {rep.continuity_status}"
        )
        for w in rep.warnings:
            print(f"  ⚠ {w}")
        total = sum(s.get("usd") or 0 for s in rep.steps)
        step_bits = ", ".join(
            f"{s['step']} ${s.get('usd') or 0:.4f}" for s in rep.steps
        )
        print(f"  cost this stage: {step_bits} = ${total:.4f}")
        print(f"  artifact: {rep.artifact_path}")
        return 0

    if args.command == "memory-backfill":
        import re as _re
        from datetime import datetime as _dt

        from . import config, generate

        if args.date:
            ok_shape = bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date))
            if ok_shape:
                try:
                    _dt.strptime(args.date, "%Y-%m-%d")
                except ValueError:
                    ok_shape = False
            if not ok_shape:
                print(
                    f"--date must be YYYY-MM-DD (a real calendar date), "
                    f"got {args.date!r}", file=sys.stderr,
                )
                return 2
        config.load_env()
        try:
            bf = generate.run_memory_backfill(date=args.date, force=args.force)
        except Exception as exc:  # CLI boundary: loud, human-readable, nonzero
            print(f"memory-backfill failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        if bf.refused:
            print(f"memory-backfill REFUSED for {bf.date}: {bf.reason}",
                  file=sys.stderr)
            return 1
        print(f"memory-backfill — {bf.date}")
        print(f"  cap ${bf.cap:.2f} | state-rewrite spend ${bf.memory_usd:.4f}")
        print(f"  deltas written: {bf.deltas_written}, threads moved: "
              f"{bf.threads_moved}, skipped: {bf.deltas_skipped}")
        for sr in bf.state_rewrites:
            print(f"    state[{sr['thread']}]: {sr['outcome']} — {sr['detail']}")
        for w in bf.warnings:
            print(f"  ⚠ {w}")
        return 0

    if args.command == "memory-mark-provenance":
        from . import db as db_mod, memory_core, paths as paths_mod

        # Does NOT auto-migrate: a data-touching migration on the real DB is a
        # separate principal checkpoint (run `newslens migrate` first). This
        # tool only writes ONE append-only mark row.
        if not paths_mod.DB_PATH.exists():
            print(f"no database at {paths_mod.DB_PATH} — run `newslens migrate` "
                  "first", file=sys.stderr)
            return 1
        con = db_mod.connect()
        try:
            has_table = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND "
                "name='thread_delta_provenance'").fetchone() is not None
            if not has_table:
                print("thread_delta_provenance is absent — migration 0014 has "
                      "not been applied; run `newslens migrate` first",
                      file=sys.stderr)
                return 1
            ok, msg, row = memory_core.mark_delta_provenance(
                con, args.delta_id, args.provenance, args.reason)
            if row is not None:
                # Print the graded delta text so the operator SEES what was
                # marked (dispatch: prints the delta text, asks nothing).
                print(f"delta {row['id']} — thread {row['thread_id']}, edition "
                      f"{row['edition_date']}, slot {row['slot']}:")
                print(f"  what_happened: {row['what_happened']}")
                if row['significance']:
                    print(f"  significance : {row['significance']}")
                print(f"  cites: {row['cites_json']}")
            if not ok:
                print(f"REFUSED: {msg}", file=sys.stderr)
                return 1
            print(msg)
            if args.reason:
                print(f"  reason: {args.reason}")
            return 0
        finally:
            con.close()

    if args.command == "memory-repair-state":
        from . import config, generate

        config.load_env()
        try:
            rep = generate.run_state_repair(
                thread_id=args.thread_id, all_threads=args.all)
        except Exception as exc:  # CLI boundary: loud, human-readable, nonzero
            print(f"memory-repair-state failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        if rep.refused:
            print(f"memory-repair-state — nothing to do: {rep.reason}")
            return 0
        print(f"memory-repair-state — cap ${rep.cap:.2f} | "
              f"state-rewrite spend ${rep.spent_usd:.4f}")
        for r in rep.repaired:
            print(f"  state[{r['thread']}]: {r['outcome']} (as of "
                  f"{r['as_of']}) — {r['detail']}")
        for w in rep.warnings:
            print(f"  ⚠ {w}")
        return 0

    if args.command == "memory-baseline":
        import re as _re
        from datetime import datetime as _dt

        from . import config, generate

        if args.date:
            ok_shape = bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date))
            if ok_shape:
                try:
                    _dt.strptime(args.date, "%Y-%m-%d")
                except ValueError:
                    ok_shape = False
            if not ok_shape:
                print(f"--date must be YYYY-MM-DD (a real calendar date), "
                      f"got {args.date!r}", file=sys.stderr)
                return 2
        config.load_env()
        try:
            rep = generate.run_baseline_backfill(
                thread_id=args.thread_id, all_threads=args.all, date=args.date)
        except Exception as exc:  # CLI boundary: loud, human-readable, nonzero
            print(f"memory-baseline failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        if rep.refused:
            print(f"memory-baseline — nothing to do: {rep.reason}")
            return 0
        print(f"memory-baseline — cap ${rep.cap:.2f} | "
              f"backgrounder spend ${rep.spent_usd:.4f}")
        for g in rep.generated:
            print(f"  baseline[{g['thread']}]: {g['outcome']} (as of "
                  f"{g['as_of']}) — {g['detail']}")
        for w in rep.warnings:
            print(f"  ⚠ {w}")
        return 0

    if args.command == "rank":
        import re as _re
        from datetime import datetime as _dt

        from . import config, ranking

        if args.date:
            # Shape first (strict zero-padding — strptime alone accepts
            # "2026-7-4"), then calendar truth (strptime rejects 2026-13-01,
            # which the regex and the DB's GLOB trigger both let through).
            ok_shape = bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date))
            if ok_shape:
                try:
                    _dt.strptime(args.date, "%Y-%m-%d")
                except ValueError:
                    ok_shape = False
            if not ok_shape:
                print(
                    f"--date must be YYYY-MM-DD (a real calendar date), "
                    f"got {args.date!r}",
                    file=sys.stderr,
                )
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

        print(
            f"story budget for {report.date} — {len(report.slots)} of "
            f"{ranking.MAX_SLOTS} slots filled"
        )
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


def _memory_command(args) -> int:
    """memory list/add/dismiss/note. Every verb: sync file->DB first (hand
    edits are never overwritten unseen), apply the verb, resync so memory.md
    reflects the result immediately."""
    from . import db, memory

    db.migrate()
    con = db.connect()
    try:
        try:
            sync = memory.sync_memory(con)
        except memory.MemorySyncError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for line in sync.summary_lines():
            print(f"  ⚠ {line}")

        if args.memory_command == "list":
            where = "" if args.status == "all" else " WHERE status = ?"
            params = () if args.status == "all" else (args.status,)
            rows = con.execute(
                "SELECT m.topic, m.status, m.principal_note, b.date AS ref_date"
                " FROM memory m LEFT JOIN briefings b"
                " ON b.id = m.last_referenced_briefing_id" + where +
                " ORDER BY m.status, m.id",
                params,
            ).fetchall()
            if not rows:
                print("no threads" + ("" if args.status == "all" else f" with status {args.status}"))
                return 0
            for r in rows:
                note = f" — {r['principal_note']}" if r["principal_note"] else ""
                ref = f" (last referenced: {r['ref_date']})" if r["ref_date"] else ""
                print(f"  [{r['status']}] {r['topic']}{note}{ref}")
            print(f"\n  ({len(rows)} thread(s); hand-edit memory.md any time — same data)")
            return 0

        topic = args.topic.strip()
        if not topic:
            print("topic must be non-empty", file=sys.stderr)
            return 2
        if memory.SEPARATOR in topic:
            print(f"topic may not contain {memory.SEPARATOR!r} (it separates "
                  "topic from note in memory.md)", file=sys.stderr)
            return 2
        row = con.execute(
            "SELECT id, status FROM memory WHERE lower(topic) = lower(?)", (topic,)
        ).fetchone()

        if args.memory_command == "add":
            now = memory._utc_now_iso()
            if row is not None:
                if row["status"] == "active":
                    print(f"already tracking {topic!r} (active)")
                    return 0
                with con:
                    con.execute(
                        "UPDATE memory SET status = 'active',"
                        " status_changed_at = ?, updated_at = ?"
                        " WHERE id = ?", (now, now, row["id"]),
                    )
                print(f"revived {topic!r} (was {row['status']})")
            else:
                with con:
                    con.execute(
                        "INSERT INTO memory (topic, status, principal_note,"
                        " status_changed_at, created_at, updated_at)"
                        " VALUES (?, 'active', ?, ?, ?, ?)",
                        (topic, args.note.strip() or None, now, now, now),
                    )
                print(f"now tracking {topic!r}")
            # NL-77 the intent gate (§F explicit action: this IS a follow). Record
            # that the thread WANTS a cold-start backgrounder — a 'pending'
            # baseline row, $0, NO LLM call here. Materialize it with
            # `newslens memory-baseline` (spend stays behind that explicit
            # command). Only for a cold start: a thread that already carries a
            # ledger record needs no founding floor.
            from . import memory_core, ranking
            tid = con.execute("SELECT id FROM memory WHERE lower(topic) = lower(?)",
                              (topic,)).fetchone()
            if tid is not None and not con.execute(
                    "SELECT 1 FROM thread_deltas WHERE thread_id = ? LIMIT 1",
                    (tid["id"],)).fetchone():
                if memory_core.write_baseline_intent(
                        con, tid["id"], ranking.local_today()) is not None:
                    print("  cold-start backgrounder queued — run "
                          "`newslens memory-baseline` to write it")
        elif args.memory_command == "dismiss":
            if row is None:
                print(f"no thread named {topic!r} — `newslens memory list` shows them",
                      file=sys.stderr)
                return 1
            with con:
                con.execute(
                    "UPDATE memory SET status = 'dismissed_user',"
                    " status_changed_at = ?, updated_at = ?"
                    " WHERE id = ?", (memory._utc_now_iso(), memory._utc_now_iso(), row["id"]),
                )
            print(f"dismissed {topic!r} — stays visible in memory.md, never auto-revives")
        elif args.memory_command == "note":
            if row is None:
                print(f"no thread named {topic!r} — add it first: "
                      f"newslens memory add \"{topic}\"", file=sys.stderr)
                return 1
            with con:
                con.execute(
                    "UPDATE memory SET principal_note = ?, updated_at = ?"
                    " WHERE id = ?",
                    (args.text.strip() or None, memory._utc_now_iso(), row["id"]),
                )
            print(f"note set on {topic!r} — the generation prompt reads it verbatim")
        elif args.memory_command == "close":
            from . import memory_core, ranking

            edition_date = ranking.local_today()
            ok, msg, _cid = memory_core.close_thread(
                con, topic, args.reason, edition_date)
            if not ok:
                print(msg, file=sys.stderr)
                return 1
            print(f"{msg} — recorded as a dated closure fact (the thread page "
                  "renders it when the closure feature ships)")

        # RENDER-ONLY refresh — a trailing full sync would re-read the file
        # written by the OPENING sync (which predates this verb) and file-wins
        # would clobber the verb's own change (M4 amendment fix: a fresh
        # `memory add` isn't in that file and would be dismissed-by-deletion
        # instantly; a fresh note would revert).
        memory.write_memory_file(con)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
