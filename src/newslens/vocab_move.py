"""vocab_move.py — NL-17 M3: the twin-move PROPOSAL and the YAML-side XOR door.

WHAT THIS IS. Eight concepts live in BOTH vocabularies today: they are granular
tags in `sources.yaml` and they are (or could be) followed actors. Acceptance
criterion (a) says one concept lives in one vocabulary. This module proposes the
resolution and refuses to perform it.

WHAT IT WILL NOT DO, AND THE RESTRAINT IS THE POINT: it does not edit
`sources.yaml`. Not a byte, not ever, not behind a flag. That file is the
principal's — he hand-edits it, his edits are standing local state, and an
agent rewriting it is the one move nobody sanctioned (ENG council :109: "his
approval gate before any byte of his file changes"). So this emits a PROPOSED
diff, an insurance backup, and the reverse script, and stops. Applying is his.

IT ALSO WRITES NO LEDGER ROW, and that is the same restraint wearing different
clothes. Remy's law (ENG R2 :141): "the vocabulary_moves ledger row IS the
atomic switch" — rank derives BOTH effects (tag suppressed, entity steer-
eligible) from that one row. So writing a row IS performing the move, whatever
the YAML still says. A proposal that ledgered itself would be an application.

THE CLASSIFICATION (ENG council :99, and it is HIS to bless item by item):

  entity-shaped, MOVE tag -> entity
    OPEC+ · Federal Reserve · ECB · Strait of Hormuz · China-Taiwan
  condition-shaped, STAY tags
    Credit Default Risk · Recession Risk · Stagflation
      — these are not actors. They are conditions the world can be in, their
        threads sit at storyline altitude carrying zero entity weight, and
        tag+storyline coexistence is today's lawful state of record. Moving
        them would take their weight away and give it to nothing.

  SEQUENCING (ENG R2 :143, accepted shape): pilot-3 (Fed · ECB · OPEC+) first,
  then the rest inside a BOUNDED <=7-day window after the pilot gate. Past that
  bound Rook's objection re-arms and the rest goes one-shot.

  NAMED HONESTLY: `China-Taiwan` is enumerated entity-shaped by the council but
  has NO honest kind in the closed org/place/person vocabulary — it is a
  relation between two actors, not an actor. This module REPORTS that rather
  than forcing a fit, which is the same refusal `entities.kind_for_class` makes
  at the settle door. His bless resolves it; a fabricated kind would not.
"""

from __future__ import annotations

import datetime as _dt
import sys
import difflib
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import entities, steering


MOVE = "move"
STAY = "stay"
TIER_PILOT = "pilot"
TIER_WINDOW = "window"

# (concept, disposition, proposed kind or "", tier) — the council's enumeration
# verbatim. Widening this list is a council/checkpoint act, not an edit.
TWINS = (
    ("Federal Reserve",     MOVE, "org",   TIER_PILOT),
    ("ECB",                 MOVE, "org",   TIER_PILOT),
    ("OPEC+",               MOVE, "org",   TIER_PILOT),
    ("Strait of Hormuz",    MOVE, "place", TIER_WINDOW),
    # No honest kind — see the module header. Proposed for his ruling, NOT filed.
    ("China-Taiwan",        MOVE, "",      TIER_WINDOW),
    ("Credit Default Risk", STAY, "",      ""),
    ("Recession Risk",      STAY, "",      ""),
    ("Stagflation",         STAY, "",      ""),
)

PILOT_WINDOW_DAYS = 7


def classify(cfg) -> List[Dict]:
    """Each enumerated twin against the tag vocabulary as it stands today.

    `present` is what makes the proposal honest on a file that has moved: he
    edits sources.yaml, and a concept he already removed must not be proposed
    for removal again.
    """
    broad = {t.casefold() for t in (cfg.interests_broad or [])}
    granular = {t.casefold() for t in (cfg.interests_granular or [])}
    out = []
    for concept, disposition, kind, tier in TWINS:
        key = concept.casefold()
        level = ("granular" if key in granular
                 else "broad" if key in broad else "")
        note = ""
        if disposition == MOVE and not kind:
            note = ("no honest kind in the closed org/place/person vocabulary "
                    "— a relation between actors is not an actor; HIS RULING, "
                    "never a forced fit")
        elif level == "broad":
            # A domain tag carries HALF weight (0.5); an entity carries 1.0.
            note = ("domain-level tag: moving it RAISES the weight 0.5 -> 1.0, "
                    "which is inside the ceiling but NOT replacement-neutral")
        elif not level:
            note = "not in the tag vocabulary today — nothing to remove"
        out.append({"concept": concept, "disposition": disposition,
                    "kind": kind, "tier": tier, "level": level,
                    "present": bool(level), "note": note})
    return out


def proposed_yaml(source_text: str, movers: Sequence[str]) -> str:
    """The sources.yaml this proposal WOULD produce — returned as a string, to
    be diffed and shown. Never written over his file by this module.

    Line-scoped and conservative: only an interests list item whose value is
    exactly a moved concept is dropped. Anything it cannot match confidently it
    LEAVES, and `classify` has already reported what was present — so a silent
    partial edit is not reachable.
    """
    wanted = {m.casefold() for m in movers}
    kept: List[str] = []
    for line in source_text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].split("#", 1)[0].strip().strip("'\"")
            if value.casefold() in wanted:
                continue
        kept.append(line)
    return "".join(kept)


def diff_for(source_text: str, proposed: str, path: str) -> str:
    return "".join(difflib.unified_diff(
        source_text.splitlines(keepends=True), proposed.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))


def backup(sources_file: Path, out_dir: Path,
           now: Optional[_dt.datetime] = None) -> Path:
    """The timestamped insurance copy (ENG :109). A COPY — the original is not
    touched, moved, or renamed."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"sources.yaml.pre-move-{now:%Y%m%dT%H%M%SZ}"
    shutil.copy2(sources_file, dest)
    return dest


HEREDOC_DELIMITER = "NEWSLENS_REVERSE_SQL"


def _sql_literal(value: str) -> str:
    """One SQL string literal, apostrophes doubled.

    Closes the SQL layer ONLY — that distinction is the whole of gate finding
    G-2. An escaped literal is still just text, and where that text lands
    decides whether it is safe; see `reverse_script` for the shell half.

    REFUSES newlines and control characters rather than escaping them. They
    cannot occur in an org/place/person name, and a concept carrying one could
    otherwise emit a line that terminates the heredoc early and turns the rest
    of the SQL into shell. Raising at GENERATION time makes that a loud source
    bug in a reviewed diff, never a silent property of an emitted script."""
    text = str(value)
    bad = [c for c in text if c in "\n\r\x00" or ord(c) < 32]
    if bad:
        raise ValueError(
            f"concept {text!r} contains a control character "
            f"({bad[0]!r}) — refused rather than escaped; a concept is a name")
    return "'" + text.replace("'", "''") + "'"


def _sh_single_quote(value: str) -> str:
    """One POSIX-sh single-quoted word. `'` closes, escapes, reopens.

    Used for the two PATHS the script interpolates. They are org-generated
    (a timestamped backup name, `paths.SOURCES_FILE`), so this is not closing a
    live hole — it is refusing to leave a second unquoted seam behind after
    G-1 proved what the first one cost."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def reverse_script(backup_path: Path, sources_file: Path,
                   movers: Sequence[str]) -> str:
    """The undo, shipped WITH the proposal so he reads it before blessing.

    Restores the YAML lines from the backup and marks the ledger rows reversed —
    and a reversal is a NEW ROW naming the row it undoes (`reversal_of`), never
    a mutated one, because 0026 is append-only and `steering.live_moves` derives
    liveness from exactly that shape.

    TWO SEAMS, CLOSED SEPARATELY — and the history is worth keeping because the
    first fix closed one of them and claimed both.

    SEAM 1, SQL (QA fix loop 1, F-1, HIGH). The original draft joined concepts
    with Python `repr` (`'Federal Reserve', 'ECB'`) and fed a shell loop
    variable into SQL UNQUOTED: `sh` word-splitting left the commas attached,
    `WHERE concept = Federal Reserve,` is a syntax error, and `set -eu` aborted
    the FIRST-EVER run before the YAML restore — a 0% success rate on the
    insurance artifact, invisible because the pin asserted emitted TEXT instead
    of executing it. Closed by `_sql_literal` + one atomic `INSERT…SELECT` over
    an `IN` list.

    SEAM 2, SHELL (gate finding G-1, MED). Closing seam 1 was NOT "no
    interpolation seam left to get wrong" — that claim was an overclaim, and the
    gate disproved it by attack rather than by reading. A SQL-escaped literal is
    still text, and that text was landing inside a DOUBLE-QUOTED shell word
    (`sqlite3 "$DB" "…IN ('$concept')…"`), where `sh` command substitution is
    live: a concept of `` T`touch PWNED`X `` or `A$(touch PWNED)B` executed the
    shell, and one containing `"` broke the SQL string outright. Closed here by
    emitting the statement through a **quoted heredoc** (`<<'DELIM'`), which
    disables every form of shell expansion inside the body — substitution,
    variable, and glob alike — so no concept can reach a shell context at all.
    `_sql_literal` additionally REFUSES control characters, which is what stops
    a concept from emitting a line that closes the heredoc early.

    WHAT IS AND IS NOT GUARANTEED, stated at the precision the last comment
    lacked: the emitted script is safe for ANY concept string that is a name —
    apostrophes, quotes, unicode, SQL metacharacters, backticks and `$()` all
    reverse cleanly or refuse loudly. It is not a general-purpose sanitiser for
    arbitrary bytes, and it does not need to be: concepts reach here only from
    the hardcoded `TWINS` tuple, so a hostile value requires a reviewed source
    edit. Both statements are now pinned by executing tests, not asserted.
    """
    if not movers:
        in_list = "NULL"          # matches nothing; the script stays valid
    else:
        in_list = ", ".join(_sql_literal(m) for m in movers)
    listed = " · ".join(movers) if movers else "(nothing)"
    delim = HEREDOC_DELIMITER
    return f'''#!/bin/sh
# NL-17 twin-move REVERSE script — generated with the proposal, never run by it.
#
# Undoes, in the order that keeps the invariant true at every instant:
#   1. the ledger (a moved concept scores as a tag again the moment its move
#      row is reversed — this is the switch, so it goes FIRST)
#   2. the YAML (cosmetic once the ledger is reversed)
#
# The SQL below rides a QUOTED heredoc (<<'{delim}'), so the shell performs NO
# expansion inside it — no command substitution, no variables, no globbing. The
# concept literals are SQL-escaped at generation time. Both seams closed: SQL by
# the escaping, shell by the quoted delimiter (gate G-1).
set -eu

DB="${{NEWSLENS_DB_PATH:-{sources_file.parent}/data/newslens.db}}"

# 1. Reverse every live move for the moved concepts, in ONE statement.
#    APPEND-ONLY: a reversal is a new row pointing at the row it undoes, and the
#    NOT IN guard makes a second run a no-op rather than a double reversal.
sqlite3 "$DB" <<'{delim}'
INSERT INTO vocabulary_moves
    (concept, from_vocab, to_vocab, entity_id, blessed_by, reversal_of, note)
  SELECT concept, to_vocab, from_vocab, entity_id, 'reverse-script', id,
         'reversed by nl17-vocabulary-move reverse script'
    FROM vocabulary_moves
   WHERE concept IN ({in_list})
     AND reversal_of IS NULL
     AND id NOT IN (SELECT reversal_of FROM vocabulary_moves
                     WHERE reversal_of IS NOT NULL);
{delim}

# 2. Restore the tag lines from the insurance backup taken with the proposal.
#    Paths are org-generated, and single-quoted anyway — G-1's lesson is that a
#    seam nobody can reach today is still a seam.
cp {_sh_single_quote(str(backup_path))} {_sh_single_quote(str(sources_file))}

echo {_sh_single_quote("reversed: " + listed)}
echo {_sh_single_quote("sources.yaml restored from " + backup_path.name)}
'''


# ---------------------------------------------------------------------------
# THE YAML-SIDE XOR WRITE DOOR
# ---------------------------------------------------------------------------

def yaml_door_collisions(con, cfg) -> List[Dict]:
    """Tag-vocabulary entries that collide with a LIVE entity's surface forms.

    Rook's condition (1) for the pilot shape (ENG R2 :139): "write-door XOR
    lands day one — no new twins can form, both doors, YAML-side by alias set."
    The follow-side door already exists (server.py's resolve XOR guard); this is
    the YAML side.

    DEGRADE-LOUD, NEVER A DEAD RUN (Rook's dissent, ENG :119, and the council's
    own rank-time rule). This RETURNS collisions for the doctor to shout about;
    it does not raise, and nothing here can fail a generate. The reason is not
    politeness: his file legitimately carries a tag whose concept is mid-move,
    and the ledger — not this function — is what decides whether the tag scores
    (steering.derive suppresses a moved concept only when its entity is actually
    weight-bearing). A hard refusal here would brick a lawful state.

    Reads the WATCHED set, not the raw entities table: an entity nothing follows
    (or a follow he dismissed) is not a live vocabulary member, and reporting it
    would manufacture a collision out of a row nobody uses.
    """
    try:
        watched = steering.watched_entities(con)
    except Exception:                                    # noqa: BLE001
        return []
    forms: Dict[str, str] = {}
    for w in watched:
        for form in w.forms:
            forms.setdefault(form.casefold(), w.canonical_name)
    out: List[Dict] = []
    for level, names in (("broad", cfg.interests_broad or []),
                         ("granular", cfg.interests_granular or [])):
        for name in names:
            owner = forms.get(name.casefold())
            if owner:
                out.append({"tag": name, "level": level, "entity": owner})
    return out


def entity_kinds_for(concepts: Sequence[str]) -> Dict[str, Optional[str]]:
    """The kind each proposed mover would file under, or None where the closed
    vocabulary has no honest seat. Delegates to the settle door's own mapper so
    there is ONE kind vocabulary in the system."""
    return {c: entities.kind_for_class(k) if (k := _kind_hint(c)) else None
            for c in concepts}


def _kind_hint(concept: str) -> str:
    for name, disposition, kind, _tier in TWINS:
        if name.casefold() == concept.casefold() and disposition == MOVE:
            return {"org": "organization", "place": "place"}.get(kind, "")
    return ""


# ---------------------------------------------------------------------------
# The instrument
# ---------------------------------------------------------------------------

def build(cfg, source_text: str, sources_path: str, tier: str = TIER_PILOT
          ) -> Dict:
    """The whole proposal for one tier. Pure — no file writes, no DB."""
    rows = classify(cfg)
    movers = [r for r in rows
              if r["disposition"] == MOVE and r["present"]
              and (tier == "all" or r["tier"] == tier)]
    names = [r["concept"] for r in movers]
    proposed = proposed_yaml(source_text, names)
    return {
        "tier": tier,
        "rows": rows,
        "movers": movers,
        "mover_names": names,
        "blocked": [r for r in movers if not r["kind"]],
        "proposed_yaml": proposed,
        "diff": diff_for(source_text, proposed, sources_path),
        "lines_removed": len(source_text.splitlines())
                         - len(proposed.splitlines()),
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    from . import config, paths, profiles

    p = argparse.ArgumentParser(
        prog="nl17-vocabulary-move",
        description="PROPOSE the tag->entity twin moves: a sources.yaml diff, a "
                    "timestamped insurance backup, and the reverse script. "
                    "NEVER edits sources.yaml and NEVER writes a ledger row — "
                    "applying is the principal's approval act.")
    p.add_argument("--tier", default=TIER_PILOT,
                   choices=(TIER_PILOT, TIER_WINDOW, "all"),
                   help=f"which moves to propose (default: {TIER_PILOT} — "
                        f"Fed/ECB/OPEC+; the rest ride a bounded "
                        f"<={PILOT_WINDOW_DAYS}-day window after the pilot gate)")
    p.add_argument("--out", default=None, metavar="DIR",
                   help="write the backup + reverse script + proposed diff here "
                        "(default: print the diff only, touch no disk)")
    args = p.parse_args(argv)

    paths.allow_real_paths()
    active_profile, refusal = profiles.resolve_entrypoint_profile()
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    for line in profiles.redirection_warnings(active_profile):
        print(f"warning: {line}", file=sys.stderr)

    sources_file = Path(paths.SOURCES_FILE)
    source_text = sources_file.read_text(encoding="utf-8")
    cfg = config.load_sources(sources_file)
    prop = build(cfg, source_text, sources_file.name, tier=args.tier)

    print(f"NewsLens NL-17 — PROPOSED vocabulary moves (tier: {args.tier})")
    print(f"  sources file: {sources_file}  (NOT modified)")
    for r in prop["rows"]:
        mark = {MOVE: "MOVE", STAY: "STAY"}[r["disposition"]]
        scope = "" if r["tier"] in (args.tier, "") or args.tier == "all" \
            else "   [other tier]"
        print(f"    {mark} {r['concept']:22s} {r['level'] or 'absent':9s}"
              f"{scope}")
        if r["note"]:
            print(f"         ! {r['note']}")
    if prop["blocked"]:
        print("  BLOCKED — proposed to move but no honest kind; his ruling "
              "first:")
        for r in prop["blocked"]:
            print(f"    - {r['concept']}")
    print(f"  tag lines this tier would remove: {prop['lines_removed']}")
    print("  --- PROPOSED sources.yaml diff (apply by hand, or don't) ---")
    print(prop["diff"] or "    (no change)")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        b = backup(sources_file, out)
        rev = out / "reverse-move.sh"
        rev.write_text(reverse_script(b, sources_file, prop["mover_names"]),
                       encoding="utf-8")
        rev.chmod(0o755)
        (out / "proposed-sources.yaml").write_text(prop["proposed_yaml"],
                                                   encoding="utf-8")
        (out / "proposed.diff").write_text(prop["diff"], encoding="utf-8")
        print(f"  insurance backup: {b}")
        print(f"  reverse script:   {rev}")
        print(f"  proposed file:    {out / 'proposed-sources.yaml'}")
    print("  NOTHING WAS APPLIED: sources.yaml is untouched and no "
          "vocabulary_moves row was written. The ledger row IS the switch — "
          "writing one would BE the move.")
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
