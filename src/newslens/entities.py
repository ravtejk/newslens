"""entities.py — NL-17 M1: ENTITY IDENTITY, and the mint-or-match door.

WHAT THIS MODULE OWNS. One question, asked at exactly one place: the settle just
named an actor — is that an actor we already know, and if not, does it earn a
row? Everything about entity identity that is not SQL lives here, so there is
one implementation of the kind vocabulary, one of the alias format, and one of
the match rule. (The BUG-1 lesson, stated as architecture: a second copy of a
rule is how the two copies disagree.)

THE LAW THIS DOOR ENFORCES (product council 2026-08-08 §5.1, migration 0024's
header): NO PROVISIONAL ROWS, EVER. A row is minted only by a SUCCESSFUL settle
— never at follow time, never at render time, never speculatively, never to
fill a column. The reason is the principal's own 2026-08-07 amendment (i): junk
identities are the thing being killed, and a placeholder entity is a junk
identity with a primary key. So this module has exactly one write path
(mint_or_match), it is called from exactly one caller (the settle door in
server._api_follow_settle), and it REFUSES — returning None — every time the
settle's answer does not carry a real, classifiable actor.

WHY REFUSING IS THE INTERESTING PART. The resolver names an actor and a class
in words ("Volkswagen (company)"). Our kind vocabulary is three values —
org/place/person — enforced by a CHECK in 0024. Most classes map. Some do not:
"(fund-withdrawal story)" is a storyline's parenthetical, not an actor's; a
class we have never seen is a class we cannot honestly file. In both cases the
answer is the same and it is NOT to guess: mint nothing, leave memory.entity_id
NULL, log 'settled_none' with the unmapped class in `detail`. The thread is
still correctly named; it simply has no entity yet. That is a first-class
terminal state (0024's header), not a failure — and the count of unmapped
classes is reported at the checkpoint so the principal can see whether three
kinds is too few, rather than discovering it as silently mis-filed rows.

ALIASES ACCRETE, NEVER REWRITE. An entity's alias set is the set of surface
forms that have ever resolved to it. Matching is over canonical_name PLUS every
alias, case-insensitively, so "VW" keeps finding Volkswagen once it has found it
once. A match ADDS any new surface form it arrived under and removes nothing:
an alias that once pointed here must keep pointing here, or an old thread's
identity silently re-points when a new name shows up. Stored "\n"-joined in one
TEXT column (0024's cut, ratified unchanged) and parsed ONLY by the two helpers
below — no render, no query, and no caller ever splits that string itself.

SCALE, STATED HONESTLY. match() is a linear scan over `entities`. That table is
bounded by the reader's own settled follows (his live record: 69 memory rows),
so a scan is the boring correct answer at this size and the alias column cannot
be indexed anyway. If the table ever reaches a size where this matters, the fix
is a normalized entity_aliases table with its own index — a migration, not a
rewrite of this module's interface.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

from .follow_altitude import split_qualifier

ALIAS_SEPARATOR = "\n"

# The CLOSED kind vocabulary — the same three values 0024's CHECK enforces.
# Held here as well so Python-side callers fail on the vocabulary rather than on
# an IntegrityError from the database, and so a fourth kind cannot be introduced
# by a caller passing a string: the migration and this tuple must BOTH change.
KINDS: Tuple[str, ...] = ("org", "place", "person")

# The resolver's class words -> our three kinds. The first five are the classes
# prompts/follow_altitude.txt actually instructs the model to emit
# (company / person / agency / organization / place); the rest are the
# unsurprising synonyms of those same five. THIS TABLE IS DELIBERATELY SHORT AND
# EXPLICIT. Every entry is a class we can defend filing under its kind; there is
# no stemming, no fuzzy match, and no "else: org" arm — an unrecognized class
# returns None and mints nothing, which is the whole point (see module header).
# Adding a class is a one-line diff a reviewer can evaluate; a heuristic is not.
KIND_BY_CLASS: Dict[str, str] = {
    # --- org ---
    "company": "org",
    "agency": "org",
    "organization": "org",
    "organisation": "org",
    "institution": "org",
    "bank": "org",
    "central bank": "org",
    "regulator": "org",
    "government": "org",
    "ministry": "org",
    "party": "org",
    "union": "org",
    "alliance": "org",
    # --- place ---
    "place": "place",
    "country": "place",
    "state": "place",
    "region": "place",
    "city": "place",
    "territory": "place",
    # --- person ---
    "person": "person",
    "official": "person",
    "politician": "person",
    "executive": "person",
}


def kind_for_class(class_word: str) -> Optional[str]:
    """Map the resolver's lowercase class parenthetical to a 0024 kind, or None.

    None is a RULING, not a failure to try: it says "this is not an actor we can
    file", and its caller mints nothing. Case- and whitespace-insensitive; a
    class the table does not carry returns None even when it looks actor-ish,
    because looking actor-ish is exactly the judgement a lookup table exists to
    refuse to make.
    """
    return KIND_BY_CLASS.get((class_word or "").strip().lower())


# ---------------------------------------------------------------------------
# The alias column: one format, two functions, no other reader
# ---------------------------------------------------------------------------

def split_aliases(stored: str) -> List[str]:
    """The stored alias column -> the alias list. Blank-safe and order-stable;
    duplicates and empties are dropped so a malformed column degrades to the
    names it does carry rather than to an exception."""
    return [a.strip() for a in (stored or "").split(ALIAS_SEPARATOR) if a.strip()]


def join_aliases(aliases: List[str]) -> str:
    """The alias list -> the stored column. Order-stable, case-insensitively
    deduped (the FIRST spelling of a name wins, so an entity's alias set does
    not churn its own bytes every time a differently-cased form arrives)."""
    out: List[str] = []
    seen = set()
    for a in aliases:
        a = (a or "").strip()
        if not a or a.casefold() in seen:
            continue
        seen.add(a.casefold())
        out.append(a)
    return ALIAS_SEPARATOR.join(out)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get(con: sqlite3.Connection, entity_id: int) -> Dict:
    """One entity row as a dict, {} when absent or on a pre-0024 DB."""
    try:
        row = con.execute(
            "SELECT id, canonical_name, kind, aliases, created_at FROM entities"
            " WHERE id = ?", (entity_id,)).fetchone()
    except sqlite3.OperationalError:
        return {}
    return dict(row) if row else {}


def match(con: sqlite3.Connection, name: str) -> Dict:
    """Find the entity a surface form resolves to — canonical_name OR any alias,
    case-insensitively. {} when nothing matches or on a pre-0024 DB.

    THE SHARED-ENTITY DEDUP LANDS HERE (ENG-M1 cut: "shared-entity dedup =
    mint-or-match at the resolver door"). Two threads that settle onto the same
    actor find the same row through this function, which is what makes them one
    entity rather than two — and it is why the M1b rider's ~$0.005 re-resolve
    per shared-entity tap buys identity instead of a duplicate.

    Canonical names are checked first, in ONE indexed query (0024's unique index
    on lower(canonical_name)); only if that misses do we scan aliases. The
    common case therefore does not pay for the scan.
    """
    needle = (name or "").strip()
    if not needle:
        return {}
    try:
        row = con.execute(
            "SELECT id, canonical_name, kind, aliases, created_at FROM entities"
            " WHERE lower(canonical_name) = lower(?)", (needle,)).fetchone()
        if row is not None:
            return dict(row)
        fold = needle.casefold()
        for r in con.execute(
                "SELECT id, canonical_name, kind, aliases, created_at"
                " FROM entities ORDER BY id"):
            if any(a.casefold() == fold for a in split_aliases(r["aliases"])):
                return dict(r)
    except sqlite3.OperationalError:
        return {}
    return {}


def threads_for(con: sqlite3.Connection, entity_id: int) -> List[Dict]:
    """Every memory row pointing at this entity (the shared-entity view the M2
    XOR doors and the checkpoint report both read). Read-only; [] on a pre-0024
    DB."""
    try:
        rows = con.execute(
            "SELECT id, topic, status, altitude FROM memory"
            " WHERE entity_id = ? ORDER BY id", (entity_id,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# THE DOOR
# ---------------------------------------------------------------------------

def mint_or_match(con: sqlite3.Connection, *, disclosure: str,
                  primary_entity: str = "") -> Tuple[Optional[int], str]:
    """THE MINT-OR-MATCH DOOR. Returns `(entity_id, detail)`.

    Called with the settle's own answer — the compact qualifier disclosure
    ("Volkswagen (company)") and the resolver's primary_entity. Returns:

      (id, "")            an existing entity matched, or a new one minted.
      (None, "<reason>")  NOTHING was minted, and `reason` names why in machine
                          terms for the 0025 event's detail column.

    THE FOUR REFUSALS, all of which land as 'settled_none' upstream:
      * no disclosure at all                     -> "no-disclosure"
      * a BARE name with no class parenthetical   -> "no-class"
        (the prompt's own grammar: a bare disclosure is a DESCRIPTIVE STORYLINE
        — "Volkswagen job cuts" — and a storyline is not an actor. Minting an
        entity called "Volkswagen job cuts" would be exactly the junk identity
        this door exists to refuse.)
      * a class outside KIND_BY_CLASS             -> "unmapped-kind: <class>"
      * an empty canonical name after the split   -> "no-name"
        BACKSTOP, AND UNREACHABLE THROUGH TODAY'S GRAMMAR — stated plainly so
        nobody reads its absence from the tests as a gap. split_qualifier strips
        before it splits and only splits on " (", so the head can never come
        back empty: a disclosure that would produce one ("(company)") has no
        " (" at all and lands in the no-class arm above instead. It stays
        because the alternative to a guard here is an entity minted under the
        empty name if that grammar ever changes, and an empty canonical_name
        would take the unique index with it.

    ALIAS ACCRETION ON MATCH: when the row is found, any surface form we arrived
    under (the disclosure's name and the resolver's primary_entity) that is not
    already the canonical name or an alias is APPENDED. Nothing is ever removed
    and canonical_name is never rewritten — an entity's established identity does
    not move because a later story spelled it differently.

    Writes ride the caller's transaction (`with con:` in the caller), matching
    memory.py's convention: the settle's entity write and its memory write
    commit together or not at all.
    """
    name, class_word = split_qualifier(disclosure or "")
    if not (disclosure or "").strip():
        return None, "no-disclosure"
    if not class_word:
        # bare disclosure == descriptive storyline (prompt grammar). Not an actor.
        return None, "no-class"
    kind = kind_for_class(class_word)
    if kind is None:
        return None, f"unmapped-kind: {class_word.strip().lower()}"
    name = (name or "").strip() or (primary_entity or "").strip()
    if not name:
        return None, "no-name"

    # arrival forms, most-canonical first — the order aliases accrete in.
    arrivals = [name]
    pe = (primary_entity or "").strip()
    if pe:
        arrivals.append(pe)

    for arrival in arrivals:
        found = match(con, arrival)
        if found:
            eid = found["id"]
            existing = split_aliases(found["aliases"])
            known = {found["canonical_name"].casefold()}
            known.update(a.casefold() for a in existing)
            added = [a for a in arrivals if a.casefold() not in known]
            if added:
                con.execute(
                    "UPDATE entities SET aliases = ? WHERE id = ?",
                    (join_aliases(existing + added), eid))
            return eid, ""

    cur = con.execute(
        "INSERT INTO entities (canonical_name, kind, aliases) VALUES (?, ?, ?)",
        (name, kind, join_aliases([a for a in arrivals if a != name])))
    return int(cur.lastrowid), ""
