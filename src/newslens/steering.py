"""steering.py — NL-17 M2: THE ENTITY STEERING MACHINERY, BUILT DARK.

WHAT THIS MODULE OWNS. Everything that turns "the reader follows an actor" into
"that actor's stories are guaranteed a look at ranking time", and nothing else.
It is the one place that reads `vocabulary_moves`, the one place that matches
entity aliases against the intake, and the one place that decides which
concepts contribute weight. `ranking.py` calls it and applies what it returns;
the two are not allowed to each hold a copy of the rule (the BUG-1 lesson).

DARK IS A LAW HERE, NOT A DEFAULT. Every steering EFFECT — tag suppression,
entity weight, look injection — is reachable only through
`SteeringState.armed`, which is `settings.threads_steer_selection`
(config.py:203, default False, never true on a live run). At `armed=False` the
state is `INERT`: two empty frozensets, no injection, and `ranking.personal_score`
takes byte-identical branches to pre-NL-17-M2 HEAD. What DOES run dark is
OBSERVATION — the alias match and the per-entity receipts — because a receipt
is a record of what was true, not an effect on what happens. That distinction is
the whole reason the dark era produces evidence instead of silence, and it is
the record NL-14 starved for want of.

-----------------------------------------------------------------------------
THE WEIGHT-ATOMIC LAW (engineering R2.1, Remy's correction to the council's own
M2/M3 ordering) — the reason this module exists as a single derivation.

Atomicity across a database and a hand-editable YAML file is not available: two
stores, no shared commit. So the invariant moves to where a transaction does
exist. THE `vocabulary_moves` ROW IS THE ATOMIC SWITCH. `derive()` reads that
one row and produces BOTH of its effects together — the concept's tag
contribution suppressed AND its entity made weight-bearing — or it produces
NEITHER. There is no code path that returns one without the other, because
they are two fields of one returned object built in one loop.

BOTH-OR-NEITHER, ENFORCED, AND WHICH WAY IT FAILS. A live move whose entity is
not steer-eligible right now (nobody follows it, or the follow sits at
storyline altitude) would suppress a tag with nothing to replace it: zero weight
in BOTH vocabularies, which is precisely the NL-14 starvation this program was
chartered to cure. That move is therefore NOT APPLIED — the tag keeps its
contribution — and the skip is recorded in `integrity` for the run's meta and a
loud warning. Degrading toward "the reader keeps the coverage he had" is the
only safe direction; degrading quietly is not an option at all.

RUTH'S BLOCKING CONDITION, STRUCTURALLY (product R2, "where we still disagree",
item 1): the tag-drop scopes to LIVE-MOVED concepts only. `suppressed_tags` is
built exclusively from applied `vocabulary_moves` rows, so an UNMOVED twin —
Hormuz and China-Taiwan during the sequenced 3-then-5 window — keeps its tag
weight by construction. There is no code here that can drop a tag nobody moved.

-----------------------------------------------------------------------------
REPLACEMENT, NOT ADDITION (the ceiling theorem). `ENTITY_WEIGHT` enters
`ranking.personal_score`'s existing `max()` pool. max() never sums, so a moved
concept scores exactly what its tag scored (both are 1.0 at topic level) and no
score becomes reachable that a plain tag could not already produce. That is why
Ada's +0.1 tie-break was rejected on the record (product R2 item 2): the promise
is "a guaranteed look, never a guaranteed slot", and an edge would make it
unprovable. COUNT-ONCE is structural for the same reason — `ENTITY_WEIGHT` is
appended AT MOST ONCE per cluster no matter how many entities matched it.

ONE HONEST ASYMMETRY, NAMED. Replacement is byte-neutral for a TOPIC-level tag
(TOPIC_WEIGHT 1.0 -> ENTITY_WEIGHT 1.0). A DOMAIN-level tag (0.5) that moves to
the entity vocabulary would score 1.0 after the move — a promotion, not a
replacement. It stays inside the ceiling theorem (1.0 is reachable by any topic
tag), but it is not neutral, so `derive()` flags it in `integrity` as
`level-promotion` rather than letting it pass as a no-op. None of the five
enumerated twins is domain-level today; the flag exists so a future bless cannot
make that promotion silently.

-----------------------------------------------------------------------------
THE MATCH IS DETERMINISTIC AND CODE-OWNED. Model-judged entity matching is the
named disease (a model deciding what counts as "the Fed" is exactly the
un-auditable step this whole program replaces). `match_items` is a pure
function over (item id, title) pairs and a frozen set of surface forms, with
word-boundary semantics and no stemming, no fuzz, no synonyms. Its resistance to
alias injection is STRUCTURAL rather than defensive: the forms come only from
`entities` rows, and the only writer of those is M1's mint-or-match door behind
a successful settle — a headline cannot add a surface form to the table it is
being matched against.

NO CALENDAR CAN MINT A LOOK. Look candidacy is derived from ONE source: items
present in this run's intake that matched an entity's forms. There is no
schedule, no event table, and no arm by which a date could produce a candidate
(Uma's pool-signal ruling, product R2 item 5 — a dormant Fed gets its
FOMC-morning look from POOL PRESSURE, not from a calendar that can go stale).
An entity with `seen == 0` gets a receipt and nothing else, forever.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

# NO MODULE-LEVEL `from .entities import split_aliases`, and this is load-bearing.
# The import graph already carries follow_altitude -> ranking (the resolver reuses
# the ranker's helpers) and entities -> follow_altitude. Adding ranking -> steering
# closes a cycle: ranking -> steering -> entities -> follow_altitude -> ranking.
# Python tolerates that only for whichever module happens to be imported first;
# `import newslens.follow_altitude` and `import newslens.server` both died with
# "cannot import name 'split_qualifier' from partially initialized module"
# (measured, not feared). `split_aliases` is therefore imported INSIDE
# surface_forms — one function-local import, the same idiom server.py uses for
# memory_core — which keeps 0024's law intact (entities owns the ONLY parser of
# the alias column) without making this module's import order matter.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The weight an entity match contributes to the max() pool. Deliberately equal
# to ranking.TOPIC_WEIGHT: replacement neutrality is a property of this number
# being the same one, not a coincidence to be re-derived. Held here rather than
# imported from ranking to keep this module free of that import cycle; the
# equality is PINNED (tests/test_nl17_m2_steering.py::test_entity_weight_equals_topic_weight).
ENTITY_WEIGHT = 1.0

# Reserved looks: at most this many clusters per run may be MINTED by the look
# machinery. ≤2 of ranking.MAX_CLUSTERS' 12, and the cap itself does not move
# (engineering :110) — MAX_CLUSTERS is coupled to PROMPT_MARGIN_CHARS through
# NL-133's arithmetic pin, so raising it is a money-guard change, not a tuning
# knob. An entity that the model ALREADY clustered costs zero reserve; the
# reserve is spent only on omissions.
#
# KASS'S NARROWED DISSENT LIVES HERE (product R2 item 3). At N≈20 follows, three
# entities with hard pool items can contend for two slots, and the third gets
# NOTHING. His adopted conditions are met by `allocate_looks` (an explicit,
# published allocation rule) and by the `seen>0 ∧ look=0` receipt being a
# first-class recorded state rather than an absence. His fourth condition — "the
# cap flexes before the promise weakens" — is a FUTURE LEVER, not exercised in
# this milestone: it means raising THIS constant, which is a reviewed diff, and
# it is deliberately not coupled to MAX_CLUSTERS so that flexing the reserve
# never re-opens NL-133.
ENTITY_LOOK_RESERVE = 2

# Surface forms shorter than this are not matched. Two characters keeps real
# short names ("EU", "UK", "G7") while refusing single letters, which would
# match somewhere in nearly every headline.
MIN_FORM_CHARS = 2

# The receipt text for a followed entity with no qualifying items this run.
# Uma's condition (product R2 item 5): the internal word "dormant" never reaches
# a reader surface for a followed actor — quiet renders as quiet. This string is
# the substrate NL-18 will render; it says WATCHED, which is true every run.
WATCHED_NO_ITEMS_NOTE = "watched — 0 qualifying items"

# The altitude that earns entity weight. 'storyline' is the other rung and it
# contributes ZERO (NL-17 criterion (c): generic threads stay zero). The
# closed set lives in follow_altitude.ALTITUDES; this is the single member of
# it that steers.
STEERING_ALTITUDE = "entity"

# memory rows in this status are not follows and never produce a look.
DISMISSED = "dismissed_user"


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    """One applied-or-skipped `vocabulary_moves` row, already de-reversed."""
    id: int
    concept: str
    from_vocab: str
    to_vocab: str
    entity_id: Optional[int]
    blessed_at: str
    blessed_by: str
    note: str


@dataclass(frozen=True)
class Watched:
    """A followed entity, with everything a look and a receipt need.

    `forms` is canonical_name + aliases, deduped case-insensitively, in accretion
    order. `steers` is the altitude gate resolved once: true iff at least one
    non-dismissed memory row pointing at this entity sits at entity altitude.
    """
    id: int
    canonical_name: str
    kind: str
    forms: Tuple[str, ...]
    steers: bool
    statuses: Tuple[str, ...]
    topics: Tuple[str, ...]


@dataclass(frozen=True)
class SteeringState:
    """The whole steering decision for ONE run, derived once.

    Everything downstream reads THIS object. `armed` is the dark law; the two
    frozensets are the weight-atomic pair; `integrity` is the degrade-loud
    channel (never a dead run, never a silent skip).
    """
    armed: bool = False
    suppressed_tags: FrozenSet[str] = frozenset()       # casefolded concept names
    tag_entity: Dict[str, int] = field(default_factory=dict)  # casefolded tag -> entity id
    weighted_entities: FrozenSet[int] = frozenset()
    watched: Tuple[Watched, ...] = ()
    moves: Tuple[Move, ...] = ()
    integrity: Tuple[Dict, ...] = ()

    def by_id(self, entity_id: int) -> Optional[Watched]:
        for w in self.watched:
            if w.id == entity_id:
                return w
        return None


INERT = SteeringState()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def live_moves(con: sqlite3.Connection) -> List[Move]:
    """Every `vocabulary_moves` row that has NOT been reversed, id order.

    A reversal is a NEW ROW naming the row it undoes (`reversal_of`) — the
    ledger is append-only (0026's triggers, 0004's pattern), so "reversed" can
    never be a mutable column. Liveness is therefore DERIVED, here, once.

    A reversal row is itself never live: it records the undo, it is not a move
    into the entity vocabulary. Only `to_vocab='entity'` rows can steer, which
    makes the filter below both the liveness rule and the direction rule.

    [] on a pre-0026 database — same degradation contract as entities.get/match,
    so a tree whose migration has not been applied ranks exactly as it did
    before this module existed.
    """
    try:
        rows = con.execute(
            "SELECT id, concept, from_vocab, to_vocab, entity_id, blessed_at,"
            " blessed_by, reversal_of, note FROM vocabulary_moves ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    reversed_ids = {r["reversal_of"] for r in rows if r["reversal_of"] is not None}
    return [
        Move(id=r["id"], concept=r["concept"], from_vocab=r["from_vocab"],
             to_vocab=r["to_vocab"], entity_id=r["entity_id"],
             blessed_at=r["blessed_at"], blessed_by=r["blessed_by"],
             note=r["note"])
        for r in rows
        if r["id"] not in reversed_ids
        and r["reversal_of"] is None
        and r["to_vocab"] == "entity"
    ]


def watched_entities(con: sqlite3.Connection) -> List[Watched]:
    """Every entity the reader follows, id order — the look/receipt universe.

    POOL-SIGNAL, NOT THREAD-LIST (Onna's dissolution of the dormancy question,
    engineering R1): a DORMANT thread still counts. Dormancy is the standing
    prompt's lifecycle, and excluding dormant follows here would make a followed
    actor silently asleep at its own event — the behaviour Uma's acceptance
    forbids. Only `dismissed_user` is out, because that is an explicit "stop
    following", not a lifecycle state.

    `steers` requires altitude='entity' on at least one live row for the entity.
    A storyline-altitude follow that happens to carry an entity_id is WATCHED
    (it gets receipts) and WEIGHTLESS (criterion (c)).
    """
    try:
        rows = con.execute(
            "SELECT e.id AS id, e.canonical_name AS canonical_name, e.kind AS kind,"
            " e.aliases AS aliases, m.status AS status, m.altitude AS altitude,"
            " m.topic AS topic"
            " FROM entities e JOIN memory m ON m.entity_id = e.id"
            " WHERE m.status != ? ORDER BY e.id, m.id",
            (DISMISSED,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    grouped: Dict[int, Dict] = {}
    for r in rows:
        g = grouped.setdefault(r["id"], {
            "canonical_name": r["canonical_name"], "kind": r["kind"],
            "aliases": r["aliases"], "steers": False,
            "statuses": [], "topics": [],
        })
        if (r["altitude"] or "") == STEERING_ALTITUDE:
            g["steers"] = True
        g["statuses"].append(r["status"])
        g["topics"].append(r["topic"])
    return [
        Watched(id=eid, canonical_name=g["canonical_name"], kind=g["kind"],
                forms=surface_forms(g["canonical_name"], g["aliases"]),
                steers=g["steers"],
                statuses=tuple(dict.fromkeys(g["statuses"])),
                topics=tuple(dict.fromkeys(g["topics"])))
        for eid, g in sorted(grouped.items())
    ]


def surface_forms(canonical_name: str, stored_aliases: str) -> Tuple[str, ...]:
    """canonical_name + aliases -> the match vocabulary for one entity.

    `split_aliases` is the ONLY parser of the stored column (0024's law), so it
    is called here rather than re-implemented. Forms are deduped
    case-insensitively (first spelling wins, matching `join_aliases`) and forms
    under MIN_FORM_CHARS or carrying no alphanumeric are dropped — a form that
    matches everything is not identity, it is noise.
    """
    from .entities import split_aliases   # function-local: see the import note
    out: List[str] = []
    seen: set = set()
    for form in [canonical_name] + split_aliases(stored_aliases):
        form = (form or "").strip()
        if len(form) < MIN_FORM_CHARS or not any(ch.isalnum() for ch in form):
            continue
        if form.casefold() in seen:
            continue
        seen.add(form.casefold())
        out.append(form)
    return tuple(out)


# ---------------------------------------------------------------------------
# THE DERIVATION — one row in, both effects out
# ---------------------------------------------------------------------------

def derive(moves: Sequence[Move], watched: Sequence[Watched],
           *, armed: bool, tag_levels: Optional[Dict[str, str]] = None) -> SteeringState:
    """The weight-atomic derivation. PURE — no DB, no clock, no I/O.

    Returns a SteeringState whose `suppressed_tags` and `weighted_entities` were
    built in ONE pass so that no caller can obtain one without the other.

    * `armed=False` returns INERT-shaped state (both sets empty, no integrity
      events): the dark law, expressed as an early return rather than as a
      condition every consumer has to remember.
    * `weighted_entities` = every WATCHED entity at entity altitude. An entity
      minted by a settle that was never a sources.yaml tag has no move row and
      is still weight-bearing — the ledger governs the TAG side of a move, not
      an entity's right to exist.
    * `suppressed_tags` = the concepts of live moves whose entity is in
      `weighted_entities`. A move whose entity is not weight-bearing is SKIPPED
      WHOLE and recorded (see the module header on both-or-neither).
    """
    if not armed:
        return INERT
    weighted = frozenset(w.id for w in watched if w.steers)
    suppressed: Dict[str, int] = {}
    integrity: List[Dict] = []
    for mv in moves:
        eid = mv.entity_id
        if eid is None or eid not in weighted:
            # NEVER suppress here: that is the zero-in-both-vocabularies state.
            integrity.append({
                "kind": "move-not-applied",
                "move_id": mv.id,
                "concept": mv.concept,
                "entity_id": eid,
                "reason": ("moved concept's entity is not weight-bearing "
                           "(unfollowed, dismissed, or storyline altitude) — "
                           "tag contribution KEPT"),
            })
            continue
        key = mv.concept.casefold()
        if key in suppressed and suppressed[key] != eid:
            # Two live moves claim the same concept for different entities. The
            # ledger cannot be read as one truth, so neither is applied.
            integrity.append({
                "kind": "concept-contested",
                "move_id": mv.id, "concept": mv.concept,
                "entity_id": eid, "other_entity_id": suppressed[key],
                "reason": "two live moves claim this concept — neither applied",
            })
            del suppressed[key]
            continue
        if (tag_levels or {}).get(mv.concept) == "domain":
            integrity.append({
                "kind": "level-promotion",
                "move_id": mv.id, "concept": mv.concept, "entity_id": eid,
                "reason": ("domain-level tag (0.5) moved to entity weight (1.0) "
                           "— inside the ceiling, NOT replacement-neutral"),
            })
        suppressed[key] = eid
    return SteeringState(
        armed=True,
        suppressed_tags=frozenset(suppressed),
        tag_entity=dict(suppressed),
        weighted_entities=weighted,
        watched=tuple(watched),
        moves=tuple(moves),
        integrity=tuple(integrity),
    )


def for_run(con: sqlite3.Connection, *, armed: bool,
            tag_levels: Optional[Dict[str, str]] = None) -> SteeringState:
    """The per-run entry point: read the two tables, derive once.

    NOTE the asymmetry, and it is deliberate: `watched` is read even when NOT
    armed, because receipts are observation and observation runs dark. Only the
    DERIVATION is gated. The returned state at armed=False therefore carries a
    watched list with empty effect sets — which is exactly "we saw, we did
    nothing", the dark era's whole record.
    """
    watched = watched_entities(con)
    if not armed:
        return SteeringState(armed=False, watched=tuple(watched))
    return derive(live_moves(con), watched, armed=True, tag_levels=tag_levels)


# ---------------------------------------------------------------------------
# THE MATCH — pure, deterministic, code-owned
# ---------------------------------------------------------------------------

def _boundary_pattern(forms: Sequence[str]) -> Optional["re.Pattern"]:
    """One compiled alternation for one entity's surface forms, or None.

    Boundaries are LOOKAROUNDS on alphanumerics, applied only at an end where
    the form itself is alphanumeric — `\\b` is wrong for names like "OPEC+",
    whose trailing '+' is a non-word character, so `\\b` would demand a word
    character after it and never match. Longest form first so an alternation
    like (Fed|Federal Reserve) cannot match the short arm inside the long name.
    """
    parts = []
    for form in sorted(forms, key=len, reverse=True):
        pat = re.escape(form)
        if form[:1].isalnum():
            pat = r"(?<![0-9A-Za-z])" + pat
        if form[-1:].isalnum():
            pat = pat + r"(?![0-9A-Za-z])"
        parts.append(pat)
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


def match_items(items: Iterable[Tuple[int, str]],
                watched: Sequence[Watched]) -> Dict[int, List[int]]:
    """(item id, title) pairs x entities -> {entity id: [item ids]}. PURE.

    Item order is PRESERVED from the input, which `ranking.gather_items` hands
    over newest-first — so `result[eid][0]` is the entity's freshest item, and
    the look injection and the allocation tie-break can both rely on that
    without re-sorting.

    Matching is over the TITLE only. Not the outlet (a Reuters story is not a
    story about Reuters), not the excerpt (not shown to the ranker, so a match
    there would steer on text the model never saw).
    """
    patterns = [(w.id, _boundary_pattern(w.forms)) for w in watched]
    hits: Dict[int, List[int]] = {eid: [] for eid, _ in patterns}
    for item_id, title in items:
        text = title or ""
        if not text:
            continue
        for eid, pat in patterns:
            if pat is not None and pat.search(text):
                hits[eid].append(item_id)
    return hits


# ---------------------------------------------------------------------------
# LOOKS — allocation, injection, receipts
# ---------------------------------------------------------------------------

def allocate_looks(candidates: Sequence[Dict], reserve: int = ENTITY_LOOK_RESERVE
                   ) -> Tuple[List[Dict], List[Dict]]:
    """THE PUBLISHED ALLOCATION RULE (Kass's adopted condition). PURE.

    `candidates` are entities with pool items that NO model cluster covered —
    the only ones that can spend reserve. Ordered by, in strict precedence:

      1. POOL-SIGNAL STRENGTH, descending — how many of this run's items name
         the actor. Kass named this acceptable explicitly; it is also the only
         ordering key that is a property of the DAY rather than of the reader's
         history, so it cannot drift into a popularity ratchet.
      2. FRESHEST ITEM, descending id — a tie on volume goes to the actor whose
         newest item arrived later.
      3. ENTITY ID, ascending — a total order, so the rule is deterministic
         even when the first two tie exactly. Never random, never dict order.

    Returns (granted, denied). EVERY denied entry is a `seen>0 ∧ look=0` receipt
    and the caller must emit it; that state is first-class, not an absence.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (-c["seen"], -c["newest_item_id"], c["entity_id"]),
    )
    return ordered[:max(0, reserve)], ordered[max(0, reserve):]


def annotate(clusters: List[Dict], hits: Dict[int, List[int]],
             state: SteeringState) -> List[Dict]:
    """Stamp every cluster with the entities it matched. Returns count-once events.

    TWO ROUTES INTO ONE FIELD, and that is the count-once mechanism:
      * ALIAS — the cluster holds an item whose title matched the entity's forms.
      * REPLACEMENT — the cluster matched a tag that a live move suppressed. The
        model saying "this story is about Federal Reserve" IS evidence the
        concept is present, so the moved concept's weight arrives through its
        entity instead of through the dropped tag. Without this route a moved
        concept whose alias the headline spelled differently would score ZERO,
        which is the starvation the move was supposed to end.

    `matched_entities` is a de-duplicated id list; `ranking.personal_score`
    appends ENTITY_WEIGHT at most once for the whole list, so no arrangement of
    routes or entities can compound.
    """
    by_item: Dict[int, List[int]] = {}
    for eid, item_ids in hits.items():
        for iid in item_ids:
            by_item.setdefault(iid, []).append(eid)
    overlaps: List[Dict] = []
    for c in clusters:
        found: List[int] = []
        for iid in c.get("item_ids") or []:
            for eid in by_item.get(iid, ()):
                if eid not in found:
                    found.append(eid)
        dropped: List[str] = []
        for t in c.get("matched_tags") or []:
            key = (t.get("name") or "").casefold()
            if key in state.suppressed_tags:
                dropped.append(t.get("name") or "")
                eid = state.tag_entity.get(key)
                if eid is not None and eid not in found:
                    found.append(eid)
        c["matched_entities"] = sorted(found)
        if dropped:
            overlaps.append({
                "cluster": (c.get("story_title") or "")[:80],
                "tags_replaced": dropped,
                "entities": sorted(found),
            })
    return overlaps


def build_look(entity: Watched, item_ids: Sequence[int],
               items_by_id: Dict[int, object]) -> Dict:
    """A reserved look, minted deterministically. NO MODEL, NO FABRICATION.

    The sanctioned fallback shape (engineering :121, adopted when the alternative
    — asking the ranker to score an injected candidate honestly — was left as an
    open compliance question). Every field is either copied from a real row or a
    stated floor:

      story_title   the NEWEST matching item's real headline, verbatim. We are
                    not in a position to write an editorial title for a cluster
                    the model declined to form, and a real headline is more
                    faithful than a composed one.
      summary       "" — deliberately empty. Downstream renders fall back to the
                    narrative lede; inventing a summary here would be exactly
                    the fabrication this module refuses.
      world_impact  0 — the CONSERVATIVE FLOOR, and the honest one: nothing
                    scored this cluster's global consequence, so it claims none.
                    A look is a guaranteed LOOK, never a guaranteed SLOT
                    (product R2 item 2); wi=0 means it competes on personal
                    weight alone and loses every contest against a real story
                    the model DID rate.
    """
    ids = list(item_ids)
    newest = ids[0] if ids else None
    row = items_by_id.get(newest) if newest is not None else None
    title = ""
    if row is not None:
        try:
            title = row["title"] or ""
        except (TypeError, KeyError, IndexError):
            title = ""
    return {
        "story_title": (title.strip() or entity.canonical_name)[:300],
        "summary": "",
        "item_ids": ids,
        "matched_tags": [],
        "matched_memory": [],
        "matched_dormant": [],
        "world_impact": 0,
        "matched_entities": [entity.id],
        "look_injected": True,
        "look_entity_id": entity.id,
    }


def receipts(state: SteeringState, hits: Dict[int, List[int]],
             looks: Dict[int, str], selected: Dict[int, Dict],
             outranked: Dict[int, List[Dict]],
             notes: Optional[Dict[int, str]] = None) -> List[Dict]:
    """One row per WATCHED entity, EVERY run, including seen=0.

    NL-18's substrate and the record NL-14 starved without (engineering :110;
    Onna's "quiet must render", product R2 §R2.2). The seven-run empty streak
    becomes structurally impossible here: absence is a written state with a
    note, not a missing row.

    `look` is 0/1 and carries its SOURCE — 'model' when the ranker already
    formed a cluster over the entity's items (costs no reserve), 'injected' when
    the reserve minted one, '' when there was no look at all. A row with
    seen>0 and look=0 is the contention receipt Kass required.
    """
    out: List[Dict] = []
    for w in state.watched:
        seen_ids = hits.get(w.id) or []
        src = looks.get(w.id, "")
        row = {
            "entity_id": w.id,
            "name": w.canonical_name,
            "kind": w.kind,
            "steers": w.steers,
            "status": ",".join(w.statuses),
            "seen": len(seen_ids),
            "look": 1 if src else 0,
            "look_source": src,
            "selected": 1 if w.id in selected else 0,
            "outranked_by": outranked.get(w.id, []),
        }
        if w.id in selected:
            row["slot"] = selected[w.id]["slot"]
        if not seen_ids:
            row["note"] = WATCHED_NO_ITEMS_NOTE
        elif not src:
            # `seen>0 ∧ look=0` — Kass's first-class contention state. It ALWAYS
            # carries a reason; an unexplained one would be the silence his
            # condition exists to forbid.
            row["note"] = (notes or {}).get(
                w.id, f"seen but no look — reserve exhausted "
                      f"({ENTITY_LOOK_RESERVE} per run)")
        out.append(row)
    return out
