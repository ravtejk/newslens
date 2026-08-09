"""backfill.py — NL-17 M1 item 8: the PROPOSED entity backfill list.

WHAT THIS IS. Migration 0024 gives every thread a nullable `entity_id`. It
arrives NULL for all of them, which is honest but incomplete: some of the
principal's existing follows were settled at entity altitude long before this
table existed and already carry the resolver's answer in their 0019 disclosure
columns. This instrument reads those columns and proposes which threads should
point at which entities.

WHAT THIS IS NOT, AND THE DISTINCTION IS THE WHOLE POINT: it does not apply
anything, and there is no flag that makes it. HIS MEMORY, HIS BLESS (ENG-M1
cut; product council §5.4). Application is M4, after he has read the list. So
this module has no write path to the record at all — the only thing it writes is
its own report — and adding one is a decision someone has to make on purpose
rather than a flag someone can pass by accident.

  * READ-ONLY against the record, via db.connect_readonly (ENGINEERING.md's
    no-real-state-writes rule; the falsifier instrument's precedent).
  * $0 AND ZERO MODEL CALLS. Classification is DETERMINISTIC — it re-reads
    answers the resolver already gave and stored. Re-resolving every thread would
    cost money to re-derive facts we already hold, and would make the proposal
    unreproducible: run it twice, get two lists.
  * The proposal is MACHINE-APPLYABLE (a JSON list of explicit
    thread_id -> entity {canonical_name, kind} pairs), so blessing it is a small
    reviewable script and not a re-derivation.

THE BUCKETS, and the third one is the one Kass's ratification demand is about:

  entity      the thread carries an entity-altitude disclosure whose class maps
              to org/place/person. Proposed: mint-or-match, point the thread.
  no-entity   the thread is a storyline, a story-seeded thread, or an unmigrated
              row. Proposed: NOTHING — entity_id stays NULL, which is a
              first-class terminal state ("storyline thread, no broader
              concept"), not a gap. COUNTED, because the product council routed
              the terminal-none count to the principal as the evidence for
              ratifying the case-(a) reading: if terminal-none dominates his
              live record, Kass's dissent flips the ruling.
  unmappable  the thread says "entity" but its class is outside our three kinds.
              Named, NEVER forced into a fit. This bucket existing at all is the
              answer to "is three kinds enough?", and it is his to read.

Classification never invents. A thread with no stored disclosure is not guessed
at from its title — that is what a resolver is for, and running one here would
be spending his money on a list he has not agreed to yet.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import config, db, entities, follow_altitude, paths


BUCKET_ENTITY = "entity"
BUCKET_NONE = "no-entity"
BUCKET_UNMAPPABLE = "unmappable-kind"

# THE M1-BLESSED LIST — the apply path's whole authority, and its ceiling.
#
# DECISIONS [2026-08-08] "M1 CHECKPOINT RULED" item 4: "the bless covers the
# LIST (1 entity proposal + no-entity buckets). No apply path exists in code
# (gate-verified structurally and by mutation), so application is a build item
# riding M2/M3 with the bless in hand." M3 builds that path (CoS sequencing,
# 2026-08-08). The module docstring's "application is M4" predates the
# resequence; the bless it describes is the one enforced here.
#
# WHY A PINNED TUPLE AND NOT "apply whatever the proposal says". The proposal is
# recomputed from a LIVE record at apply time, and the record moves — he follows
# things. A proposal computed next week may carry entity items nobody blessed,
# and "he blessed the backfill" would silently launder them in. So the blessed
# list is named here, item by item, and an entity item that is not on it is
# REFUSED BY NAME rather than applied: a different list is a different bless,
# which is a checkpoint, not a flag. Nothing here can grow the list — only the
# principal's next ruling can, and that edits this tuple in a reviewable diff.
#
# (thread_id, canonical_name, kind) — the exact triple the classifier proposes.
BLESSED_ENTITY_ITEMS = (
    (19, "Federal Reserve", "org"),
)

# THE NO-ENTITY BUCKET SPLITS THREE WAYS, and the split is not bookkeeping — it
# is the difference between evidence and noise for the question the principal is
# being asked to rule on.
#
# Kass's ratification demand (product council §6) is about TERMINAL-NONE: threads
# whose settle RAN and found no broader concept. If those dominate the live
# record, his dissent flips the case-(a) reading. But a thread that PREDATES the
# resolver never ran a settle at all — it is not terminal-none, it is
# unexamined, and counting it as evidence would inflate exactly the number the
# ruling turns on. Reported separately, always.
SUB_STORYLINE = "settled-storyline"     # terminal-none: examined, no actor
SUB_SEEDED = "story-seeded"             # terminal-none: examined, nothing resolved
SUB_UNMIGRATED = "unmigrated"           # NOT evidence: never examined
# QA F-10. A story-seeded row's altitude alone cannot say WHY it is still at
# seed, and the two reasons are opposite kinds of evidence:
SUB_UNEXAMINED = "settle-failed"        # NOT evidence: the settle never ran to an answer
SUB_LOW = "settled-low"                 # examined, but UNCONFIDENT — not terminal-none


def classify_thread(row: Dict, outcomes: Optional[List[str]] = None) -> Dict:
    """One memory row -> its proposal. Pure, deterministic, no I/O, no model.

    `outcomes` is that thread's settle history, newest first, when 0025 exists.
    QA F-10: WITHOUT it, every `narrow` row was filed as terminal-none — but a
    thread whose only settle FAILED was never examined at all, and counting it
    inflates Kass's number in exactly the direction this instrument's own
    docstrings promise to avoid. An unconfident settle (`settled_low`) is the
    same problem from the other side: it looked and found two things, which is
    not "found nothing".

    When the history is absent (a pre-0025 record — which is the state his own
    database was in when this list was first produced) the altitude reading is
    all there is, and the row says so in its reason rather than pretending to
    knowledge it does not have.
    """
    topic = row.get("topic") or ""
    altitude = (row.get("altitude") or "").strip()
    disclosure = (row.get("disclosure") or "").strip()
    primary = (row.get("primary_entity") or "").strip()
    base = {"thread_id": row.get("id"), "topic": topic, "status": row.get("status"),
            "altitude": altitude or "(unmigrated)", "disclosure": disclosure}

    if altitude != "entity":
        # storyline / narrow / unmigrated. The reason AND the sub-bucket are
        # recorded per row, because the aggregate "no-entity" number is not the
        # one the ratification question turns on — see the SUB_* notes above.
        sub, reason = {
            "storyline": (SUB_STORYLINE,
                          "settled as a storyline — a story is not an actor"),
            "narrow": (SUB_SEEDED,
                       "story-seeded; no broader concept resolved"),
            "": (SUB_UNMIGRATED,
                 "unmigrated — predates the follow-altitude resolver"),
        }.get(altitude, (SUB_UNMIGRATED,
                         f"altitude {altitude!r} is not an entity rung"))
        if altitude == "narrow":
            last = (outcomes or [None])[0]
            if last is None:
                reason += " (no settle history on record — pre-0025; " \
                          "read from the altitude column alone)"
            elif last == "settle_failed":
                sub = SUB_UNEXAMINED
                reason = ("the settle never ran to an answer (case (b)) — "
                          "NEVER EXAMINED, so not terminal-none evidence")
            elif last == "settled_low":
                sub = SUB_LOW
                reason = ("examined but unconfident — named candidates it would "
                          "not stand behind; not 'found nothing'")
        return dict(base, bucket=BUCKET_NONE, sub_bucket=sub, reason=reason,
                    proposed_entity=None)

    name, class_word = follow_altitude.split_qualifier(disclosure)
    name = (name or "").strip() or primary
    if not disclosure or not class_word:
        return dict(base, bucket=BUCKET_NONE, sub_bucket=SUB_UNMIGRATED,
                    proposed_entity=None,
                    reason="entity altitude but no class parenthetical stored — "
                           "nothing to file a kind from, and guessing one is the "
                           "junk-identity failure this milestone exists to refuse")
    kind = entities.kind_for_class(class_word)
    if kind is None:
        return dict(base, bucket=BUCKET_UNMAPPABLE, sub_bucket="",
                    proposed_entity=None,
                    reason=f"class {class_word!r} is outside org/place/person")
    if not name:
        return dict(base, bucket=BUCKET_NONE, sub_bucket=SUB_UNMIGRATED,
                    proposed_entity=None,
                    reason="no canonical name after the qualifier split")
    aliases = [a for a in (primary,) if a and a.casefold() != name.casefold()]
    return dict(base, bucket=BUCKET_ENTITY, sub_bucket="", reason="",
                proposed_entity={"canonical_name": name, "kind": kind,
                                 "aliases": aliases})


def build_proposal(con: sqlite3.Connection) -> Dict:
    """The whole proposal, read-only. Threads in id order (stable across runs —
    a proposal that reorders itself is a proposal nobody can diff)."""
    # THE PRE-0024 CASE IS THE NORMAL ONE, not an error, and getting this wrong
    # made the instrument useless on the record it exists for. This list is the
    # INPUT to the checkpoint that sanctions 0024, and 0024 applies on the
    # principal's next server restart — so at the moment he most needs to read
    # the proposal, `entity_id` does not exist yet. Detect the column instead of
    # assuming it; migrating his record to make the query work would be exactly
    # the real-state write this instrument is built to avoid.
    cols = {r[1] for r in con.execute("PRAGMA table_info(memory)")}
    has_entity_id = "entity_id" in cols
    projection = ("id, topic, status, altitude, disclosure, primary_entity"
                  + (", entity_id" if has_entity_id else ""))
    try:
        rows = con.execute(
            f"SELECT {projection} FROM memory ORDER BY id").fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"backfill: refused — this database predates the follow-altitude "
            f"columns ({exc}); run `newslens migrate` first")

    # QA F-10: the settle history, when 0025 exists on this record. Newest
    # first; {} on a pre-0025 database, which the classifier reads as "no
    # history" and says so per row rather than inferring examination it cannot
    # see. Read in ONE query rather than per row — this is a report, but it is a
    # report over the principal's whole memory table.
    history: Dict[int, List[str]] = {}
    try:
        for r in con.execute(
                "SELECT thread_id, outcome FROM follow_settle_events"
                " ORDER BY id DESC"):
            history.setdefault(r["thread_id"], []).append(r["outcome"])
    except sqlite3.OperationalError:
        history = {}

    items = [classify_thread(dict(r), history.get(r["id"])) for r in rows]
    counts: Dict[str, int] = {BUCKET_ENTITY: 0, BUCKET_NONE: 0,
                              BUCKET_UNMAPPABLE: 0}
    subs: Dict[str, int] = {SUB_STORYLINE: 0, SUB_SEEDED: 0, SUB_UNMIGRATED: 0,
                            SUB_UNEXAMINED: 0, SUB_LOW: 0}
    for it in items:
        counts[it["bucket"]] += 1
        if it["bucket"] == BUCKET_NONE:
            subs[it["sub_bucket"]] += 1
    # TERMINAL-NONE IS STRICT (QA F-10 + the fix loop's settled_low split): the
    # settle RAN and found nothing broader. A failed settle never examined the
    # thread; an unconfident one found two things. Neither is "found nothing",
    # and both used to be counted as if they were.
    terminal_none = subs[SUB_STORYLINE] + subs[SUB_SEEDED]
    examined = (counts[BUCKET_ENTITY] + counts[BUCKET_UNMAPPABLE]
                + terminal_none + subs[SUB_LOW])

    # shared-entity dedup, PREVIEWED: how many distinct actors the entity bucket
    # collapses to. This is the number that says whether the table is doing any
    # work — 12 threads collapsing to 12 entities means nothing is shared.
    by_name: Dict[str, List[int]] = {}
    for it in items:
        pe = it.get("proposed_entity")
        if pe:
            by_name.setdefault(pe["canonical_name"].casefold(), []).append(
                it["thread_id"])
    shared = {n: ids for n, ids in by_name.items() if len(ids) > 1}

    already = (sum(1 for r in rows if dict(r).get("entity_id") is not None)
               if has_entity_id else 0)
    unmapped_classes = sorted({
        it["reason"].split("'")[1] for it in items
        if it["bucket"] == BUCKET_UNMAPPABLE and "'" in it["reason"]})

    return {
        "generated_for": "NL-17 M1 checkpoint — PROPOSED, not applied",
        "threads_total": len(items),
        "counts": counts,
        "no_entity_sub_buckets": subs,
        # THE NUMBER THE RULING TURNS ON, and its honest denominator: threads
        # whose settle actually RAN, not the whole table.
        "terminal_none_count": terminal_none,
        "examined_threads": examined,
        "terminal_none_share_of_examined": (
            round(terminal_none / examined, 3) if examined else None),
        "schema_0024_applied": has_entity_id,
        "already_pointing_at_an_entity": already,
        "distinct_entities_proposed": len(by_name),
        "shared_entities": {n: ids for n, ids in sorted(shared.items())},
        "unmapped_classes_seen": unmapped_classes,
        "items": items,
        "application_note": (
            "NOTHING HERE IS APPLIED. Each entity-bucket item is a "
            "thread_id -> entity pair the principal blesses (or edits) at the "
            "M1 checkpoint; application is M4. The no-entity count is the "
            "evidence the product council routed to him for ratifying the "
            "case-(a) terminal reading — if it dominates, the ruling flips."),
    }


def apply_proposal(con: sqlite3.Connection, proposal: Dict) -> Dict:
    """Apply the M1-BLESSED entity items. Returns a receipt. Writes, on purpose.

    THE ONLY WRITE PATH THIS MODULE HAS EVER HAD, and everything about it is
    narrow by construction:

      * It touches `memory.entity_id` and mints through the existing
        `entities.mint_or_match` door — NOTHING ELSE. No topic, no status, no
        altitude, no disclosure, no delta, no settle event.
      * It applies ONLY items on BLESSED_ENTITY_ITEMS. An entity-bucket item
        that is not blessed is refused BY NAME and counted; it is not an error
        and it is not applied.
      * The no-entity and unmappable buckets are NEVER written. Their proposal
        IS "do nothing" — `entity_id` staying NULL is the terminal state, not a
        gap to be filled, so there is no row for this function to touch.
      * Idempotent: a thread already pointing at the entity the bless names is
        counted `already` and re-written by nothing. A thread pointing at a
        DIFFERENT entity is a CONFLICT — refused, never re-pointed, because
        silently moving a thread's identity is the one thing an append-only
        alias discipline exists to prevent.
      * One transaction. The mint and the pointer commit together or not at all
        (entities.mint_or_match's own convention: "writes ride the caller's
        transaction").

    NO SETTLE EVENT IS WRITTEN, and that is a correctness call rather than an
    omission: 0025's vocabulary describes what a SETTLE found, and this is not a
    settle — it is a re-read of answers a settle already gave and stored. Writing
    'settled_entity' rows here would forge history that `build_proposal` itself
    reads back (the F-10 sub-bucket split keys on those outcomes), so the
    instrument would corrupt its own evidence on the next run.

    REFUSES OUTRIGHT (raises) when migration 0024 has not been applied: with no
    `entity_id` column there is nothing to point, and 0024 lands on the
    principal's next server restart.
    """
    if not proposal.get("schema_0024_applied"):
        raise SystemExit(
            "backfill --apply: refused — migration 0024 is not on this record "
            "yet (no memory.entity_id column). It applies on the next server "
            "restart; re-run --apply after that.")

    blessed = {(t, n, k) for t, n, k in BLESSED_ENTITY_ITEMS}
    blessed_threads = {t for t, _n, _k in BLESSED_ENTITY_ITEMS}
    applied: List[Dict] = []
    already: List[Dict] = []
    conflicts: List[Dict] = []
    unblessed: List[Dict] = []
    refusals: List[Dict] = []

    proposed_entity_threads = set()
    with con:                     # one transaction for the whole application
        for it in proposal["items"]:
            if it["bucket"] != BUCKET_ENTITY:
                continue          # no-entity / unmappable: nothing to write
            pe = it["proposed_entity"]
            triple = (it["thread_id"], pe["canonical_name"], pe["kind"])
            proposed_entity_threads.add(it["thread_id"])
            if triple not in blessed:
                unblessed.append({**it, "triple": triple})
                continue
            row = con.execute(
                "SELECT entity_id, disclosure, primary_entity FROM memory"
                " WHERE id = ?", (it["thread_id"],)).fetchone()
            eid, detail = entities.mint_or_match(
                con, disclosure=row["disclosure"],
                primary_entity=row["primary_entity"])
            if eid is None:
                # The door refused. It is the SAME door the settle uses, so a
                # refusal here means the stored disclosure cannot honestly mint
                # an identity — report it, never route around it.
                refusals.append({**it, "reason": detail})
                continue
            current = row["entity_id"]
            if current is not None and current != eid:
                conflicts.append({**it, "current_entity_id": current,
                                  "blessed_entity_id": eid})
                continue
            if current == eid:
                already.append({**it, "entity_id": eid})
                continue
            con.execute("UPDATE memory SET entity_id = ? WHERE id = ?",
                        (eid, it["thread_id"]))
            applied.append({**it, "entity_id": eid})

    missing = sorted(blessed_threads - proposed_entity_threads)
    return {
        "applied": applied, "already": already, "conflicts": conflicts,
        "unblessed": unblessed, "door_refusals": refusals,
        # A blessed thread the classifier no longer proposes as an entity: its
        # stored disclosure changed under the bless. Named, never re-derived.
        "blessed_but_no_longer_proposed": missing,
        "blessed_total": len(BLESSED_ENTITY_ITEMS),
    }


def _print_apply_report(r: Dict) -> None:
    print("  --- APPLIED ---")
    print(f"  blessed items: {r['blessed_total']}"
          f"  |  applied {len(r['applied'])}"
          f"  |  already pointing {len(r['already'])}")
    for it in r["applied"]:
        pe = it["proposed_entity"]
        print(f"    [{it['thread_id']:>3}] {it['topic']!r}  ->  entity "
              f"{it['entity_id']} {pe['canonical_name']} ({pe['kind']})")
    for it in r["already"]:
        print(f"    [{it['thread_id']:>3}] {it['topic']!r}  ->  already "
              f"entity {it['entity_id']} (idempotent, nothing written)")
    for it in r["unblessed"]:
        pe = it["proposed_entity"]
        print(f"    REFUSED-UNBLESSED [{it['thread_id']:>3}] {it['topic']!r} "
              f"-> {pe['canonical_name']} ({pe['kind']}): proposed now, but not "
              f"on the M1-blessed list. A different list is a different bless.")
    for it in r["conflicts"]:
        print(f"    CONFLICT [{it['thread_id']:>3}] {it['topic']!r}: points at "
              f"entity {it['current_entity_id']}, bless names "
              f"{it['blessed_entity_id']} — NOT re-pointed.")
    for it in r["door_refusals"]:
        print(f"    DOOR-REFUSED [{it['thread_id']:>3}] {it['topic']!r}: "
              f"{it['reason']}")
    for tid in r["blessed_but_no_longer_proposed"]:
        print(f"    BLESSED-BUT-GONE [{tid:>3}]: no longer classifies as an "
              f"entity — its stored disclosure moved under the bless.")


def _state_note(item: Dict) -> str:
    """The thread's follow state, rendered for a blesser.

    LOUD FOR THE ONE THAT BITES: `dismissed_user` is not a lifecycle state, it
    is "he stopped following this" — and an entity pointer on such a thread is
    inert by construction (`steering.watched_entities` excludes it). `dormant`
    is a lifecycle state and still steers, so it is shown but not shouted.
    """
    status = (item.get("status") or "").strip() or "unknown"
    if status == "dismissed_user":
        return ("[DISMISSED — he stopped following this; an entity pointer "
                "here steers NOTHING]")
    return f"[{status}]"


def _print_report(p: Dict, applying: bool = False) -> None:
    c = p["counts"]
    print("NewsLens NL-17 M1 — proposed entity backfill (READ-ONLY, PROPOSED)")
    print(f"  threads: {p['threads_total']}"
          + (f"  |  already pointing at an entity: "
             f"{p['already_pointing_at_an_entity']}"
             if p["schema_0024_applied"] else
             "  |  migration 0024 NOT YET APPLIED to this record (expected — "
             "it applies on the next server restart)"))
    print(f"  entity bucket:      {c[BUCKET_ENTITY]}"
          f"  -> {p['distinct_entities_proposed']} distinct entities")
    s = p["no_entity_sub_buckets"]
    print(f"  no-entity bucket:   {c[BUCKET_NONE]}"
          f"   (entity_id stays NULL — a terminal state, not a gap)")
    print(f"      settled-storyline {s[SUB_STORYLINE]}"
          f"  ·  story-seeded {s[SUB_SEEDED]}"
          f"  ·  settled-low {s[SUB_LOW]} (examined, unconfident)")
    print(f"      NOT evidence: unmigrated {s[SUB_UNMIGRATED]}"
          f"  ·  settle-failed {s[SUB_UNEXAMINED]} (never examined)")
    print(f"  TERMINAL-NONE (Kass's falsifier): {p['terminal_none_count']}"
          f" of {p['examined_threads']} examined"
          + (f" = {p['terminal_none_share_of_examined']:.0%}"
             if p["terminal_none_share_of_examined"] is not None else ""))
    print(f"  unmappable kind:    {c[BUCKET_UNMAPPABLE]}"
          + (f"   classes seen: {', '.join(p['unmapped_classes_seen'])}"
             if p["unmapped_classes_seen"] else ""))
    if p["shared_entities"]:
        print("  shared entities (the dedup this table exists for):")
        for name, ids in p["shared_entities"].items():
            print(f"    - {name}: threads {ids}")
    print("  --- proposal ---")
    for it in p["items"]:
        # THE FOLLOW STATE IS PART OF THE PROPOSAL, and leaving it out cost a
        # milestone. NL-17 M3 (2026-08-09): the single entity item on the
        # M1-blessed list was thread 19 `Federal Reserve`, which the principal
        # had DISMISSED the previous day. This report printed topic and proposed
        # entity but not status, so the bless was given without that in view —
        # and the resulting pointer is inert, because `steering.watched_entities`
        # excludes `dismissed_user` threads. A blesser cannot weigh what the
        # report does not show.
        state = _state_note(it)
        if it["bucket"] == BUCKET_ENTITY:
            pe = it["proposed_entity"]
            print(f"    [{it['thread_id']:>3}] {it['topic']!r} {state}"
                  f"  ->  {pe['canonical_name']} ({pe['kind']})")
        else:
            print(f"    [{it['thread_id']:>3}] {it['topic']!r} {state}"
                  f"  ->  {it['bucket']}: {it['reason']}")
    if not applying:
        print("  NOTHING WAS APPLIED — the record was opened read-only. "
              "Applying the M1-BLESSED items is the separate, explicit "
              "`--apply`.")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="nl17-backfill",
        description="Classify existing threads into entity / no-entity buckets "
                    "and emit the PROPOSED backfill list. Read-only, $0, zero "
                    "model calls by default; --apply writes the M1-BLESSED "
                    "items and nothing else.")
    p.add_argument("--out", default=None, metavar="DIR",
                   help="write proposal.json here (default: print only — this "
                        "instrument does not write into DATA_DIR by default, "
                        "because a proposal is not record state)")
    p.add_argument("--apply", action="store_true",
                   help="APPLY the M1-BLESSED entity items (DECISIONS "
                        "2026-08-08 item 4): mint-or-match each blessed "
                        "entity and point its thread at it. Touches "
                        "memory.entity_id and the entities table ONLY. An "
                        "entity item that is not on the blessed list is "
                        "refused by name, never applied. The no-entity "
                        "buckets are never written — their proposal is to do "
                        "nothing. Idempotent; refuses on a conflicting "
                        "pointer rather than re-pointing a thread.")
    args = p.parse_args(argv)

    paths.allow_real_paths()
    from . import profiles
    active_profile, refusal = profiles.resolve_entrypoint_profile()
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    for line in profiles.redirection_warnings(active_profile):
        print(f"warning: {line}", file=sys.stderr)
    config.load_env()

    # THE HANDLE IS CHOSEN BY THE FLAG, and the default is still the read-only
    # one. Without --apply this module cannot write even if it wanted to: the
    # connection itself would refuse (the M1 two-guarantee posture is intact for
    # every invocation that has not asked, in words, to apply).
    apply_report = None
    if args.apply:
        con = db.connect()
        try:
            proposal = build_proposal(con)
            apply_report = apply_proposal(con, proposal)
        finally:
            con.close()
    else:
        try:
            con = db.connect_readonly()
        except sqlite3.OperationalError as exc:
            print(f"backfill: refused — cannot open the record read-only "
                  f"({exc}); run `newslens generate` first", file=sys.stderr)
            return 1
        try:
            proposal = build_proposal(con)
        finally:
            con.close()

    _print_report(proposal, applying=args.apply)
    if apply_report is not None:
        _print_apply_report(apply_report)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "proposal.json").write_text(
            json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  proposal: {out / 'proposal.json'}")
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
