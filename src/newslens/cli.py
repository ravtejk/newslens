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
    # Stage-0 M1: the profile dimension. Global (before the verb) because it
    # selects WHICH WORLD every verb then operates on — database, corpus,
    # spend log, memory.md. `default` is the founder's own world, unchanged
    # and in place; every other profile lives under profiles/<name>/.
    parser.add_argument(
        "--profile", default=None, metavar="NAME",
        help="which reader's world to operate on (default: 'default', the "
        "founder's own). Overrides NEWSLENS_PROFILE. The profile must exist — "
        "see `newslens profile create`",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    migrate_p = sub.add_parser(
        "migrate",
        help="create/upgrade the local SQLite database (idempotent; safe to re-run)",
    )
    migrate_p.add_argument(
        "--all-profiles", action="store_true", dest="all_profiles",
        help="migrate EVERY profile's database, not just the active one",
    )

    profile_p = sub.add_parser(
        "profile",
        help="multi-reader profiles: create/list. Each profile owns its own "
        "database, corpus, spend log, memory.md and sources.yaml; nothing is "
        "shared and nothing is inherited",
    )
    profile_sub = profile_p.add_subparsers(dest="profile_command", required=True)
    prof_create = profile_sub.add_parser(
        "create",
        help="provision a brand-new profile: fresh fully-migrated database, "
        "empty memory.md, its own copy of the source catalog. Seeds nothing "
        "and copies no other reader's state",
    )
    prof_create.add_argument("name")
    profile_sub.add_parser("list", help="every profile and its honest status")
    # NL-132-B: teardown that is not `rm -rf` typed beside the founder's world.
    prof_delete = profile_sub.add_parser(
        "delete",
        help="permanently remove one profile's whole world (database, corpus, "
        "artifacts, spend ledger, memory.md, sources.yaml). DRY RUN unless "
        "--confirm repeats the slug exactly; refuses the founder's own world",
    )
    prof_delete.add_argument("name")
    prof_delete.add_argument(
        "--confirm", default=None, metavar="SLUG",
        help="type the profile's slug again to actually delete it. Without it "
        "this prints what WOULD be removed and changes nothing",
    )
    # The catalog-refresh verb. A profile's sources.yaml is a COPY of the org
    # template frozen at create time, so a profile made before a slate landed
    # never sees it (DECISIONS 2026-08-06 ④: fresh1 ranked 385 items against
    # health interests it had no feeds for). This is how a reader adopts the
    # update — by typing it, never by the org editing their file.
    prof_refresh = profile_sub.add_parser(
        "refresh-catalog",
        help="adopt org source-catalog ADDITIONS into an existing profile's "
        "sources.yaml. DRY RUN unless --apply. Adds only: never edits, "
        "re-enables, disables or removes anything already in that file, and "
        "never touches interests or settings. Refuses the founder's own catalog",
    )
    prof_refresh.add_argument("name")
    prof_refresh.add_argument(
        "--apply", action="store_true",
        help="actually write the additions. Without it this prints the delta "
        "and changes nothing",
    )
    prof_refresh.add_argument(
        "--skip", action="append", default=[], metavar="NAME",
        help="decline one offered source by name (repeatable). With --apply "
        "the decline is remembered, so that feed is never offered again",
    )

    sub.add_parser(
        "doctor",
        help="health check: env, keys, schema, sources (exit 0 = ready for a real run)",
    )
    ingest_p = sub.add_parser(
        "ingest",
        help="pull enabled sources into source_items (idempotent per UTC "
        "fetch-day). Tier-2 Sonar discovery is PAUSED by ruling (2026-07-25) — "
        "runs are RSS-only and say so; no metered call is made even with a key "
        "present",
    )
    ingest_p.add_argument(
        "--no-discovery",
        action="store_true",
        help="skip the Sonar discovery call (redundant while discovery is "
        "paused; kept so the flag keeps meaning if it is ever unpaused)",
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
    # NL-81: the interactive reconciliation surface. Every memory verb already
    # syncs first; this makes the sync itself addressable so a REFUSAL has a
    # named exit the refusal copy can point at.
    mem_sync = memory_sub.add_parser(
        "sync",
        help="reconcile memory.md against the database and rewrite it. Refuses "
        "(changing NOTHING on either side) if the file is not the one this "
        "database last wrote — a restored backup or a reconstruction can "
        "otherwise undo deletions you made",
    )
    mem_sync.add_argument(
        "--accept-file", action="store_true", dest="accept_file",
        help="import an out-of-date memory.md anyway — YOUR explicit file-wins "
        "call. Deleted/renamed threads are still not resurrected, and "
        "dismissals this applies are still attributed to the file, not to you",
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

    # NL-146 — SCHEDULED GENERATION. Four subcommands and no `install`: the org
    # never installs the launchd agent (dispatch 2026-08-13, law), so what ships
    # is a renderer, an instruction printer, an honest status readout, and the
    # entry launchd itself calls.
    sched_p = sub.add_parser(
        "schedule",
        help="scheduled generation (macOS launchd): render the agent, print the "
             "install steps you run yourself, read its honest status")
    sched_sub = sched_p.add_subparsers(dest="schedule_cmd", required=True)
    sched_sub.add_parser(
        "plist",
        help="print the launchd agent to stdout and NOTHING else — safe to "
             "redirect into ~/Library/LaunchAgents/")
    sched_sub.add_parser(
        "install-instructions",
        help="print the exact commands to install, pause, re-hour or remove the "
             "schedule (your hands run them)")
    sched_sub.add_parser(
        "status",
        help="is the agent file there, at what hour, is it paused, and what did "
             "the last scheduled fire do")
    sched_sub.add_parser(
        "run",
        help="ONE scheduled fire — what launchd calls at the scheduled hour. "
             "Declines quietly if the kill switch is set or today's edition is "
             "already published; otherwise generates, retrying only a systemic "
             "fetch failure and never past one run's budget")

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

    dc_p = sub.add_parser(
        "discovery-clean",
        help="NL-101 retro-clean: find the historical tier-2 discovery rows "
        "that were never news (sitemap/feed files, homepages, section and "
        "author listings, AV pages, social posts) and remove them. DRY RUN BY "
        "DEFAULT — prints the plan and changes nothing; --apply performs the "
        "deletion. Rows cited by a shipped briefing are RETAINED and reported, "
        "never deleted. $0, offline, no LLM call.",
    )
    dc_p.add_argument(
        "--apply", action="store_true",
        help="actually delete the rows the dry run listed (default: dry run)",
    )
    dc_p.add_argument(
        # NAMED --show, NOT --limit (gate ruling 2026-07-26). It was always
        # display-only, but "--apply --limit 3" reads like "delete three" and
        # deletes all of them — the gate's own run did exactly that. A flag
        # that sits next to --apply must not look like it bounds --apply.
        "--show", type=int, default=20, metavar="N", dest="show",
        help="how many example rows to PRINT per bucket (default 20). Display "
        "only — it never bounds what --apply deletes; the COUNTS above the "
        "lists are always complete",
    )

    args = parser.parse_args(argv)

    # --- Stage-0 M1: resolve the active profile BEFORE any verb runs --------
    # Every guarded path this process resolves from here on belongs to this
    # profile. An unknown name is refused rather than created: db.connect()
    # makes parent directories, so `--profile alcie` would otherwise mint a
    # silent empty world and bury a reader's writes in it.
    from . import profiles

    try:
        # set_profile(None) CLEARS any pin left by an earlier in-process call,
        # so a second main() without --profile re-resolves from the
        # environment instead of inheriting the last caller's reader.
        active_profile = paths.set_profile(args.profile)
        if args.command != "profile" and active_profile != paths.DEFAULT_PROFILE:
            profiles.require_exists(active_profile)
    except (paths.ProfileError, profiles.ProfileMissingError) as exc:
        print(f"profile: {exc}", file=sys.stderr)
        return 2
    for line in profiles.redirection_warnings(active_profile):
        print(f"warning: {line}", file=sys.stderr)

    if args.command == "profile":
        return _profile_command(args)

    if args.command == "migrate":
        from . import db

        try:
            if args.all_profiles:
                ran_all = profiles.migrate_all()
            else:
                # db_path named explicitly, not left to whatever DB_PATH
                # currently means: `migrate` is the one verb that CREATES a
                # database, so pointing it at the wrong profile's world would
                # mint state rather than merely read it.
                ran_all = {active_profile:
                           db.migrate(db_path=profiles.db_path_for(active_profile))}
        except Exception as exc:  # CLI boundary: loud, human-readable, nonzero
            print(f"migrate failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        for slug, ran in ran_all.items():
            label = "" if len(ran_all) == 1 else f"[{slug}] "
            if ran:
                print(f"{label}applied {len(ran)} migration(s): {', '.join(ran)}")
            else:
                print(f"{label}database already up to date — nothing to apply")
            print(f"{label}database: {profiles.db_path_for(slug)}")
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
            # NL-127: the DEEPEN row, printed only where the lane fired. Reads
            # as "pages/sockets over URLs considered", then the prose it bought
            # and the tier refusals it recorded — the two numbers his 2026-08-24
            # ruling is about.
            d = s.get("deepen")
            if d:
                print(f"    deepen: {d['ok']}/{d['attempted']} fetched of "
                      f"{d['considered']} Sonar URLs · {d['chars']} chars · "
                      f"{d['excluded']} tier-excluded")
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

    if args.command == "schedule":
        from . import config, generate, schedule

        config.load_env()

        if args.schedule_cmd == "plist":
            # STDOUT IS THE FILE. Nothing else may print here — the documented
            # step is `newslens schedule plist > ~/Library/LaunchAgents/...`,
            # and one stray banner line makes an unparseable plist that launchd
            # rejects with a message about the file, not about us. Errors go to
            # stderr, where a redirect cannot swallow them.
            try:
                sys.stdout.write(schedule.render_plist())
            except (schedule.ScheduleError, ValueError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            return 0

        if args.schedule_cmd == "install-instructions":
            try:
                print(schedule.install_instructions())
            except ValueError as exc:
                print(f"{exc} — fix it in .env", file=sys.stderr)
                return 1
            return 0

        if args.schedule_cmd == "status":
            for line in schedule.status_lines():
                print(line)
            return 0

        # `schedule run` — the entry launchd fires.
        def _sched_progress(label: str, model):
            # To stderr, which is the ONLY stream the plist keeps
            # (StandardErrorPath). A 6am run nobody watched is readable
            # afterwards or it is not readable at all.
            tail = f" [{model}]" if model else ""
            print(f"  … {label}{tail}", file=sys.stderr, flush=True)

        def _runner(**kw):
            return generate.run_generate(progress=_sched_progress, **kw)

        try:
            result = schedule.run_scheduled(runner=_runner)
        except Exception as exc:  # noqa: BLE001 — the unattended CLI boundary
            # NOT swallowed and NOT silent: an unexpected exception here is a
            # BUG, and the one reader it has is StandardErrorPath at 6am. It
            # gets the loud line and a non-zero exit, exactly like the
            # interactive `generate` boundary below. Decided outcomes take the
            # other door.
            print(f"schedule run crashed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        # One line, to stderr, so a successful scheduled run's stdout stays the
        # clean narrative artifact the interactive verb also produces.
        print(f"schedule: {result['outcome']} "
              f"(attempts {result['attempts']}, "
              f"${result['charged_usd']:.4f} charged)", file=sys.stderr)
        # EXIT 0 ON EVERY DECIDED OUTCOME, INCLUDING FAILURE. A failed generate,
        # a paused schedule and a same-day no-op are decisions this code made on
        # purpose and recorded; the ladder above is the only retry policy this
        # feature has, and a non-zero exit would invite launchd to grow a second
        # one. A CRASH is not a decision and keeps its 1 (above).
        return 0

    if args.command == "generate":
        import re as _re
        from datetime import datetime as _dt

        from . import config, generate, schedule

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
                # NL-146: he typed this verb — the one caller for which the word
                # "interactive" is literally true.
                trigger=schedule.TRIGGER_INTERACTIVE,
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
        # NL-95: spent_usd is now the SHADOW figure (what the cap binds), so
        # this printer states both rather than labelling a shadow number
        # "spend". On the api lane they are the same number.
        print(f"memory-baseline — cap ${rep.cap:.2f} | backgrounder charged "
              f"${rep.charged_usd:.4f} · shadow vs cap ${rep.spent_usd:.4f}")
        for g in rep.generated:
            print(f"  baseline[{g['thread']}]: {g['outcome']} (as of "
                  f"{g['as_of']}) — {g['detail']}")
        for w in rep.warnings:
            print(f"  ⚠ {w}")
        return 0

    if args.command == "rank":
        import re as _re
        from datetime import datetime as _dt

        from . import config, labels, ranking

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
                # NL-138 (ruling ④): was `s.override_label` — the stored
                # prefix + the ranker's prose reason. Both are gone; the rank
                # CLI shows the same code-owned tag form every reader surface
                # shows, read from labels.py at call time.
                print(f"     >> {labels.WHY_CHOSEN_BECAUSE} "
                      f"{labels.WHY_WORLD_NEWS}")
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

    if args.command == "discovery-clean":
        return _discovery_clean_command(args)

    parser.error(f"unknown command: {args.command}")  # unreachable; argparse guards
    return 2


def _discovery_clean_command(args) -> int:
    """`newslens discovery-clean [--apply]` — NL-101's retro-clean.

    DRY RUN BY DEFAULT. The dry run and the apply run share ONE classifier
    (discovery.scan_discovery_rows), so what --apply deletes is exactly what
    the dry run listed — there is no second, drifted predicate."""
    from . import db, discovery

    con = db.connect()
    try:
        result = discovery.clean_discovery_rows(con, apply=bool(args.apply))
    finally:
        con.close()

    removable = result["removable"]
    cited = result["cited"]
    kept = result["kept"]
    total = len(removable) + len(cited) + len(kept)
    show = max(0, int(getattr(args, "show", 20) or 0))   # display only

    print(f"discovery-clean: {total} stored tier-2 (sonar) row(s) examined")
    print(f"  {len(kept)} look like articles — untouched")
    print(f"  {len(cited)} junk-classed but CITED by a shipped briefing — "
          f"retained (a shipped citation must keep resolving)")
    verb = "deleted" if result["applied"] else "would delete"
    print(f"  {len(removable)} junk-classed and uncited — {verb}")

    for label, rows in (("removable", removable), ("cited", cited)):
        if not rows:
            continue
        print(f"\n  {label}:")
        for entry in rows[:show]:
            print(f"    [{entry['reason']}] {entry['url']}")
        if len(rows) > show:
            print(f"    … and {len(rows) - show} more "
                  f"(--show {len(rows)} to see them all)")

    if result["applied"]:
        print(f"\nAPPLIED — {result['deleted']} row(s) deleted.")
    else:
        print("\nDRY RUN — nothing was changed. Re-run with --apply to delete "
              "the rows listed above.")
    return 0


def _human_bytes(n: float) -> str:
    """Display only — the exact byte count is on the DeletionPlan."""
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _print_deletion_plan(plan) -> None:
    print(f"  root:      {plan.root}")
    print(f"  contents:  {plan.files} file(s), {plan.dirs} directory(ies), "
          f"{_human_bytes(plan.bytes)}")
    print(f"  world:     {plan.status.line()}")


def _profile_command(args) -> int:
    """`newslens profile create|list|delete` — Stage-0 M1, delete NL-132-B.

    Never touches any profile but the one named. `create` refuses over an
    existing directory and refuses the founder's own `default` outright;
    `delete` refuses the founder STRUCTURALLY (resolved paths, so a symlink
    named anything else cannot get past it) and is a dry run until the slug is
    typed back — the same confirmation shape `discovery-clean` uses (:292-307),
    with the slug standing in for --apply because this one has no undo."""
    from . import paths, profiles

    if args.profile_command == "delete":
        try:
            plan = profiles.deletion_plan(args.name)
        except (paths.ProfileError, profiles.ProfileMissingError,
                profiles.ProfileDeleteRefused) as exc:
            print(f"profile delete: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:                       # CLI boundary: loud
            print(f"profile delete failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1

        if args.confirm is None:
            print(f"profile delete: {plan.slug!r} — DRY RUN, nothing was "
                  "changed\n")
            _print_deletion_plan(plan)
            print("\nThis removes that reader's whole world permanently — "
                  "database, corpus,\nartifacts, spend ledger, memory.md and "
                  "sources.yaml. There is no undo.\nTo do it, type the slug "
                  "back:\n")
            print(f"  newslens profile delete {plan.slug} "
                  f"--confirm {plan.slug}")
            return 0

        if args.confirm != plan.slug:
            print(f"profile delete: refusing — the confirmation must be this "
                  f"profile's own slug, typed back exactly. Expected "
                  f"`--confirm {plan.slug}`, got `--confirm {args.confirm}`. "
                  "Nothing was changed.", file=sys.stderr)
            return 2

        try:
            removed = profiles.delete(args.name, confirm=args.confirm)
        except (paths.ProfileError, profiles.ProfileMissingError,
                profiles.ProfileDeleteRefused) as exc:
            print(f"profile delete: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:                         # partial removal is real
            print(f"profile delete failed: {type(exc).__name__}: {exc} — the "
                  f"directory may be partially removed; inspect "
                  f"{plan.root}", file=sys.stderr)
            return 1
        if removed.survivors:
            # QA F-3 (2026-08-02): a live serve on this profile re-creates the
            # world on its very next request — the server opens the DB per
            # request rather than holding a handle — so the tree can be back
            # before rmtree's caller gets a line out. Saying "(gone)" over a
            # directory that is on disk is exactly the silent-success this
            # milestone exists to remove, so the verb reports what it SAW.
            print(f"profile delete: removed profile {removed.slug!r} — but "
                  "THE DIRECTORY IS BACK\n")
            print(f"  root:      {removed.root}  (ON DISK AGAIN)")
            print(f"  removed:   {removed.files} file(s), {removed.dirs} "
                  f"directory(ies), {_human_bytes(removed.bytes)}")
            print("  on disk now:")
            for path in removed.survivors:
                print(f"    {path}")
            print("\n  The removal itself succeeded; something re-created the "
                  "tree immediately\n  afterwards. The known cause is a live "
                  f"`newslens --profile {removed.slug} serve`\n  (or "
                  f"`scripts/reader-serve {removed.slug}`) still running: it "
                  "opens that profile's\n  database per request, so the next "
                  "request re-mints an empty world.\n  Stop that serve, then "
                  "run this again.", file=sys.stderr)
            print("  founder:   untouched — your data/, memory.md and "
                  "sources.yaml were never in this profile's tree")
            return 1
        print(f"profile delete: removed profile {removed.slug!r}\n")
        print(f"  root:      {removed.root}  (gone — re-checked, not assumed)")
        print(f"  removed:   {removed.files} file(s), {removed.dirs} "
              f"directory(ies), {_human_bytes(removed.bytes)}")
        print("  founder:   untouched — your data/, memory.md and sources.yaml "
              "were never in this profile's tree")
        # The durability limit, said out loud (QA F-3, and the fix-loop live
        # reproduction that sharpened it): the re-stat above is honest about
        # THIS INSTANT, and the zombie is triggered by the next REQUEST to a
        # live serve, not by the delete — so a serve that is idle right now
        # will re-create an empty world minutes later and the check above
        # cannot see it. Refusing to delete a served profile needs a lockfile
        # in every profile root; until then the operator gets the sentence
        # instead of a false absolute.
        print(f"  note:      a still-running serve for {removed.slug} — its "
              "NEXT request re-creates an empty\n             world there "
              "(the server opens the database per request). Stop it, then "
              "`newslens profile list`.")
        return 0

    if args.profile_command == "refresh-catalog":
        return _profile_refresh_catalog(args)

    if args.profile_command == "create":
        try:
            st = profiles.create(args.name)
        except (paths.ProfileError, profiles.ProfileExistsError,
                FileNotFoundError) as exc:
            print(f"profile create: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:                       # CLI boundary: loud
            print(f"profile create failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        print(f"created profile {st.slug!r}")
        print(f"  root:      {st.root}")
        print(f"  database:  {st.db_path} (fully migrated, empty)")
        print("  memory.md: 0 bytes — nothing followed, nothing inherited")
        print(f"  sources:   {paths.profile_layout(st.slug)['SOURCES_FILE']}"
              " (org catalog; interests EMPTY by design)")
        print("\nNext: choose this reader's interest tags in that sources.yaml, "
              "then run:")
        print(f"  newslens --profile {st.slug} doctor")
        print(f"  newslens --profile {st.slug} generate")
        return 0

    if args.profile_command == "list":
        active = paths.current_profile()
        for st in profiles.inventory():
            mark = "*" if st.slug == active else " "
            print(f"{mark} {st.line()}")
            for problem in st.problems:
                print(f"    ! {problem}")
        for stray in profiles.stray_directories():
            why = ("the founder's profile IS this checkout — nothing reads "
                   "this directory" if stray == paths.DEFAULT_PROFILE
                   else "not a valid profile name")
            print(f"  ? {stray}/ — {why}; ignored")
        print("\n* = active profile (--profile / NEWSLENS_PROFILE)")
        return 0

    print(f"unknown profile command: {args.profile_command}", file=sys.stderr)
    return 2


def _profile_refresh_catalog(args) -> int:
    """`newslens profile refresh-catalog <name> [--apply] [--skip NAME]`.

    Dry run by default, like `profile delete` and `discovery-clean`. The
    printed delta is the whole product of a dry run: the reader decides from
    it, which is what makes the first refresh of a pre-ledger profile safe
    (see catalog_refresh's first-run honesty note)."""
    from . import catalog_refresh, paths, profiles

    try:
        plan = catalog_refresh.plan(args.name, skip=args.skip)
    except (paths.ProfileError, profiles.ProfileMissingError,
            catalog_refresh.RefreshRefused,
            catalog_refresh.RefreshMalformed) as exc:
        print(f"profile refresh-catalog: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                           # CLI boundary: loud
        print(f"profile refresh-catalog failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    print(f"profile refresh-catalog: {plan.slug!r}")
    print(f"  their catalog:  {plan.sources_file}")
    print(f"  org catalog:    {plan.template_file} "
          f"(sha256:{plan.template_digest[:12]})")

    if plan.offers:
        print(f"\n  WOULD ADD {len(plan.offers)} source(s) the org catalog has "
              "and this profile does not:")
        for offer in plan.offers:
            print(f"    + {offer.summary}")
    else:
        print("\n  Nothing to add — this profile already carries every source "
              "in the org catalog\n  that it has not already answered.")

    for label, names in (
        ("skipped by --skip this run", plan.skipped),
        ("declined at an earlier refresh (never re-offered)",
         plan.previously_declined),
        ("adopted earlier and since deleted by this reader (never re-offered)",
         plan.reader_removed),
    ):
        if names:
            print(f"\n  {len(names)} {label}:")
            for n in names:
                print(f"    - {n}")

    # A --skip that matched nothing is accepted, but silence there reads as
    # "skipped and remembered" when it is neither (QA F-10) — most often it is
    # a typo in the name the reader meant to decline.
    if plan.skip_unmatched:
        print(f"\n  {len(plan.skip_unmatched)} --skip name(s) matched nothing "
              "this refresh would have offered\n  (already in this profile, "
              "already answered, or not in the org catalog) — no effect, and\n"
              "  nothing was remembered for them:")
        for n in plan.skip_unmatched:
            print(f"    ? {n}")

    if plan.reader_only:
        print(f"\n  {len(plan.reader_only)} source(s) in this profile that the "
              "org catalog does not have\n  (their own additions, or entries "
              "the org has since dropped) — left alone:")
        for n in plan.reader_only:
            print(f"    = {n}")

    if plan.divergences:
        print(f"\n  {len(plan.divergences)} DISAGREEMENT(S) with the org "
              "catalog on sources you already have.\n  Reported only — your "
              "file's line wins and nothing here is changed:")
        for d in plan.divergences:
            print(f"    ~ {d.name}: {d.field} — org says {d.template!r}, "
                  f"yours says {d.profile!r}")

    if not args.apply:
        if plan.is_noop:
            print("\nDRY RUN — nothing to do, and nothing was changed.")
            return 0
        print("\nDRY RUN — nothing was changed. To adopt the additions above:\n")
        skips = "".join(f" --skip {n!r}" for n in args.skip)
        print(f"  newslens profile refresh-catalog {plan.slug} --apply{skips}")
        print("\nSkip any you do not want with --skip 'Exact Name' — a skip is "
              "remembered,\nso that source is never offered again.")
        return 0

    try:
        plan = catalog_refresh.apply(plan)
    except catalog_refresh.RefreshMalformed as exc:
        print(f"profile refresh-catalog: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"profile refresh-catalog failed: {type(exc).__name__}: {exc} — "
              f"inspect {plan.sources_file}", file=sys.stderr)
        return 1

    if not plan.adopted_now and not plan.skipped:
        print("\nAPPLIED — nothing to add, so nothing was written.")
        return 0
    print(f"\nAPPLIED — added {len(plan.adopted_now)} source(s) to "
          f"{plan.sources_file}")
    print("  your interests, settings and every source you already had: "
          "untouched\n  (re-parsed and compared field by field before the "
          "write landed)")
    if plan.skipped:
        print(f"  declined and remembered: {', '.join(plan.skipped)}")
    if plan.mixed_line_endings:
        style = "CRLF" if plan.line_ending == "\r\n" else "LF"
        print(f"  note: your file mixed line endings; the merged file uses "
              f"{style} throughout\n        (the dominant one — a refresh does "
              "not get to pick a house style)")
    print(f"\nNext: `newslens --profile {plan.slug} doctor` re-checks every "
          "feed URL in that file.")
    return 0


def _memory_command(args) -> int:
    """memory sync/list/add/dismiss/note. Every verb: sync file->DB first (hand
    edits are never overwritten unseen), apply the verb, resync so memory.md
    reflects the result immediately.

    NL-81 — this is the INTERACTIVE surface, so a stale memory.md REFUSES here
    (Onna's split: the embedded call sites degrade instead, because throwing at
    3am over a stale file is worse than the disease). A refusal mutates neither
    side and exits nonzero with the two ways out. `list` is the one exception:
    it writes nothing at all, so it degrades to database state rather than
    denying the principal the very inspection that diagnoses the refusal."""
    from . import db, memory

    db.migrate()
    con = db.connect()
    try:
        try:
            sync = memory.sync_memory(
                con, accept_file=bool(getattr(args, "accept_file", False)))
        except memory.MemorySyncError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for line in sync.summary_lines():
            print(f"  ⚠ {line}")
        if sync.stale_refusal:
            if args.memory_command != "list":
                return 1
            print("  ⚠ showing DATABASE state only — memory.md was not imported "
                  "and was not rewritten")
        if args.memory_command == "sync":
            if sync.accepted_file:
                print("memory.md accepted over the database on your explicit "
                      "--accept-file; memory.md rewritten from the result")
            elif sync.bootstrapped:
                print("memory.md had no generation stamp and agreed with the "
                      "database — adopted and stamped")
            elif sync.edits_applied:
                print(f"applied {sync.edits_applied} edit(s) from memory.md; "
                      "memory.md rewritten from the result")
            else:
                print("memory.md already agrees with the database — nothing to "
                      "apply; memory.md rewritten in canonical form")
            return 0

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
        # NL-139 byte-clamp, DOOR 4 of 5 (memory.clamp_topic). This handler runs its
        # OWN INSERT rather than going through memory.add_thread, so it needs
        # its own clamp — and it needs it HERE, above the lower(topic) lookup,
        # for the reason clamp_topic's docstring gives: the row this verb
        # creates and the row it later finds must have one name. Every verb
        # below (add/dismiss/note/delete) reads `topic` from this point on, so
        # one clamp covers the handler. Disclosed on the spot: a CLI verb IS
        # the principal, and he should see that we stored something other than
        # what he typed before he wonders why `memory list` reads short.
        topic, _was_cut = memory.clamp_topic(topic)
        if _was_cut:
            print(f"  ⚠ thread name shortened to {memory.TOPIC_MAX_CHARS} "
                  f"characters — using {topic!r}")
        row = con.execute(
            "SELECT id, status FROM memory WHERE lower(topic) = lower(?)", (topic,)
        ).fetchone()

        if args.memory_command == "add":
            now = memory._utc_now_iso()
            # NL-81 §5.1 — THE LIFT LANE. `memory add` is the contracted
            # explicit way to bring back a thread the guard refuses to let a
            # file resurrect. The lift is appended (never an update, never a
            # delete) and it is disclosed: the principal should see that this
            # command overrode a deletion record, not just that it worked.
            lifted = memory.lift_tombstone(con, topic)
            if lifted is not None:
                past = ("deleted" if lifted["kind"] == "delete" else "renamed")
                print(f"  ⚠ {topic!r} was {past} on {lifted['when']} — bringing "
                      "it back on your explicit request (recorded)")
            if row is not None:
                if row["status"] == "active":
                    print(f"already tracking {topic!r} (active)")
                    return 0
                with con:
                    con.execute(
                        "UPDATE memory SET status = 'active',"
                        " status_changed_at = ?, updated_at = ?,"
                        " dismissed_via = NULL WHERE id = ?", (now, now, row["id"]),
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
                # NL-81 §5.4: a CLI verb IS the principal — this one keeps the
                # "(dismissed by you …)" copy, and it is the only class that
                # earns it.
                con.execute(
                    "UPDATE memory SET status = 'dismissed_user',"
                    " status_changed_at = ?, updated_at = ?,"
                    " dismissed_via = 'principal'"
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
