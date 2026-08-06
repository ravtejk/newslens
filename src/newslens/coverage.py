"""Coverage state — can this reader's sources actually serve the topics she picks?

NL-135 Q1 (ruled 2026-08-02). The Commissioning picker offers 66 names. Until
this module existed, nothing on the page knew whether the reader's own source
list contained anything that covers them, and on 2026-08-02 a reader picked
nine health topics against a catalog with no health outlets and learned the
answer from a one-story edition.

THE LAW THIS MODULE IMPLEMENTS — never fabricate abundance or coverage:

    ABSENCE is provable and is asserted with confidence. Zero mapped feeds for
    a name means no source in the list has that beat, and the copy says so.

    PRESENCE is INCLUSION, never sufficiency. `served` counts feeds; it does
    NOT promise items, matches, or a full briefing. Mapped feeds are not items
    and items are not matches. No caller may render a count as a promise, and
    per-name positive claims stop at the DOMAIN grain — a Technology feed
    proves nothing about Micromobility.

The state is COMPUTED AT RENDER, from the shipped map plus the reader's own
sources, and is never persisted. That is deliberate: a grade cached into a
profile at commissioning would be wrong the day the org approves a feed slate,
and every existing reader would carry the stale badge until a migration fixed
it. Nothing to migrate here — the answer is recomputed every time it is asked.

THREE STATES per name, and the reader-facing claim each one licenses:

    UNSERVED     no fetchable source covers it -> assert absence, confidently
    HEADLINES    covered, but every covering source is headline_only -> a
                 LIMITATION disclosure (weakens; never asserts sufficiency),
                 so it is safe at both grains. The Systemic Risk credit topics
                 are its first customer: Bloomberg, FT and WSJ all carry that
                 beat and all three are titles-and-summaries.
    SERVED       at least one full-content source covers it -> inclusion only

WHAT THIS MODULE DELIBERATELY DOES NOT KNOW: whether those feeds actually
emitted anything today, whether the ranker matched it, or whether the reader
will see a story. Those are measurements, and measurements need ingest history
a fresh profile does not have. A measured per-topic upgrade is the honest
future; it is not this.

Stdlib-only at import time (db.py's rule): PyYAML is imported lazily inside
`load()`, exactly as catalog.py does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from . import catalog, paths

# The three states, in the order of how much they license a surface to say.
UNSERVED = "unserved"
HEADLINES = "headlines"
SERVED = "served"


class CoverageError(Exception):
    """The map is missing, unreadable, malformed, or names something the topic
    catalog does not contain.

    Loud on purpose, for the same reason CatalogError is: a silently-degraded
    map renders confident ABSENCE claims over beats that are in fact covered,
    which is the one failure this mechanism exists to prevent."""


@dataclass(frozen=True)
class FeedCoverage:
    name: str
    covers: Tuple[str, ...] = ()
    note: str = ""


@dataclass
class CoverageMap:
    feeds: List[FeedCoverage] = field(default_factory=list)

    def _index(self) -> Dict[str, FrozenSet[str]]:
        """folded feed name -> the catalog names it covers, folded."""
        cached = getattr(self, "_idx", None)
        if cached is None:
            cached = {_key(f.name): frozenset(_key(c) for c in f.covers)
                      for f in self.feeds}
            object.__setattr__(self, "_idx", cached)
        return cached

    def knows(self, feed_name: str) -> bool:
        return _key(feed_name) in self._index()

    def covers(self, feed_name: str) -> FrozenSet[str]:
        return self._index().get(_key(feed_name), frozenset())


@dataclass(frozen=True)
class NameState:
    """One catalog name's answer. `sources` is the covering feed names in the
    reader's own file — kept so a caller can show its work, never so a caller
    can promise stories."""
    name: str
    level: str
    state: str
    sources: Tuple[str, ...] = ()

    @property
    def served_count(self) -> int:
        return len(self.sources)

    @property
    def unserved(self) -> bool:
        return self.state == UNSERVED


@dataclass(frozen=True)
class CoverageState:
    """The whole page's answer, computed live. `unclassified` is the honesty
    valve: feeds the reader added by hand that the shipped map has never heard
    of. They are NOT counted as covering anything (we do not know what they
    cover), so a surface asserting absence must disclose that they exist —
    otherwise "no source covers this" is a claim made over sources we did not
    read."""
    by_name: Dict[str, NameState] = field(default_factory=dict)
    unclassified: Tuple[str, ...] = ()

    def of(self, name: str) -> Optional[NameState]:
        return self.by_name.get(_key(name))

    def unserved_among(self, names) -> List[str]:
        """Which of these picks nothing in the list covers. The consequence
        line's number, and the only counting this module does on a reader's
        own picks."""
        out = []
        for n in names:
            st = self.of(n)
            if st is not None and st.unserved:
                out.append(st.name)
        return out


def _key(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


# ---------------------------------------------------------------------------
# Loading + validation
# ---------------------------------------------------------------------------

def load(path: Optional[Path] = None,
         cat: Optional[catalog.Catalog] = None) -> CoverageMap:
    """Parse and validate the map. Raises CoverageError on any malformation.

    Validation is strict for one reason: every name in this file is a name the
    picker will or will not put a badge on, and a typo ("Public health " with a
    stray key, a renamed domain) would quietly move a beat into the UNSERVED
    column. Better a red suite than a confident lie on a stranger's screen."""
    import yaml  # lazy: keeps import-time stdlib-only

    src = Path(path) if path is not None else paths.FEED_COVERAGE
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoverageError(f"feed coverage map unreadable at {src}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — any parse failure is one class
        raise CoverageError(f"feed coverage map is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise CoverageError("feed coverage map must be a mapping with `feeds:`")
    raw_feeds = raw.get("feeds")
    if not isinstance(raw_feeds, list) or not raw_feeds:
        raise CoverageError("feed coverage map: `feeds` must be a non-empty list")

    cat = cat if cat is not None else catalog.load()
    known = {_key(n) for n in cat.names()}

    seen: Dict[str, str] = {}
    feeds: List[FeedCoverage] = []
    for i, entry in enumerate(raw_feeds):
        where = f"feeds[{i}]"
        if not isinstance(entry, dict):
            raise CoverageError(f"{where} must be a mapping with `name`")
        for key in entry:
            if key not in ("name", "covers", "note"):
                raise CoverageError(f"{where}: unknown key `{key}`")
        name = _clean(entry.get("name"))
        if not name:
            raise CoverageError(f"{where}: `name` is required")
        if _key(name) in seen:
            raise CoverageError(
                f"{where}: {name!r} repeats {seen[_key(name)]!r} — one entry "
                "per feed, or the map disagrees with itself")
        seen[_key(name)] = name
        # An ABSENT key and an EMPTY list mean the same deliberate thing here
        # ("this feed covers nothing in the catalog"), but a non-list is a
        # malformation: `covers: Public Health` would iterate as characters.
        raw_covers = entry.get("covers")
        if raw_covers is None:
            raw_covers = []
        if not isinstance(raw_covers, list):
            raise CoverageError(f"{where} ({name}): `covers` must be a list")
        covers: List[str] = []
        for j, c in enumerate(raw_covers):
            cname = _clean(c)
            if not cname:
                raise CoverageError(f"{where}.covers[{j}]: empty name")
            if _key(cname) not in known:
                raise CoverageError(
                    f"{where} ({name}): {cname!r} is not in the topic catalog "
                    "— a name that does not exist covers nothing, and the "
                    "beat it was meant to claim would read as UNSERVED")
            covers.append(cname)
        feeds.append(FeedCoverage(name=name, covers=tuple(covers),
                                  note=_clean_note(entry.get("note"))))
    return CoverageMap(feeds=feeds)


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _clean_note(value) -> str:
    return str(value or "").strip()


# ---------------------------------------------------------------------------
# The computation
# ---------------------------------------------------------------------------

def serving(cfg, cmap: CoverageMap, cat: catalog.Catalog,
            name: str) -> List:
    """The reader's own FETCHABLE sources that cover `name`.

    Fetchable is the whole eligibility rule: a reference_only outlet is cited
    and never fetched, a disabled one never runs, so neither can serve a beat
    however well the map describes them. `Source.fetchable` is the single
    definition of that and this asks it rather than re-deriving it.

    THE RESOLUTION RULE (the map's header states it too):
      * a DOMAIN is served by feeds listing the domain, plus feeds listing any
        of its topics — a source for Oil Markets is a source in Global Trade;
      * a TOPIC is served by feeds listing that topic, and ONLY when no feed
        does at all does it inherit its domain's feeds. Beat beats inheritance
        — which is how the Systemic Risk credit topics come out headlines-only
        (Bloomberg/FT/WSJ carry that beat) while the domain does not (CNBC and
        Chartbook carry the domain in full text).
    """
    level = cat.level_of(name)
    if level is None:
        return []
    fetchable = [s for s in cfg.sources if s.fetchable]
    key = _key(name)

    if level == catalog.DOMAIN:
        domain = next((d for d in cat.domains if _key(d.name) == key), None)
        wanted = {key} | {_key(t) for t in (domain.topics if domain else ())}
        return [s for s in fetchable if cmap.covers(s.name) & wanted]

    direct = [s for s in fetchable if key in cmap.covers(s.name)]
    if direct:
        return direct
    parent = next((d for d in cat.domains
                   if any(_key(t) == key for t in d.topics)), None)
    if parent is None:
        return []
    pkey = _key(parent.name)
    return [s for s in fetchable if pkey in cmap.covers(s.name)]


def state(cfg, cmap: Optional[CoverageMap] = None,
          cat: Optional[catalog.Catalog] = None) -> CoverageState:
    """Every catalog name's state against THIS reader's file. Read-only, and
    computed from scratch each call — see the module docstring on why nothing
    here is ever written into a profile."""
    cat = cat if cat is not None else catalog.load()
    cmap = cmap if cmap is not None else load(cat=cat)

    by_name: Dict[str, NameState] = {}
    for d in cat.domains:
        for nm, lvl in [(d.name, catalog.DOMAIN)] + [(t, catalog.TOPIC)
                                                     for t in d.topics]:
            srcs = serving(cfg, cmap, cat, nm)
            if not srcs:
                st = UNSERVED
            elif all(s.tier == "headline_only" for s in srcs):
                st = HEADLINES
            else:
                st = SERVED
            by_name[_key(nm)] = NameState(name=nm, level=lvl, state=st,
                                          sources=tuple(s.name for s in srcs))
    unknown = tuple(s.name for s in cfg.sources
                    if s.fetchable and not cmap.knows(s.name))
    return CoverageState(by_name=by_name, unclassified=unknown)
