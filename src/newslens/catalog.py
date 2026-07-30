"""The topic catalog — the org-authored vocabulary the Commissioning offers.

NL-116 (ratified 2026-07-28): the catalog is a DATA FILE
(`templates/topic-catalog.yaml`), and this module is its only reader. Adding,
renaming or re-parenting a topic is a data edit plus a test run; no surface
carries its own copy of the list, so none can drift from another.

TWO LEVELS, matching sources.yaml exactly:

    domain -> interests.broad      (half-weight personal signal)
    topic  -> interests.granular   (full-weight personal signal)

The reader never sees or chooses that split — they pick names, and the level
is a property of the catalog. This module is also where "suggestions only" is
made MECHANICAL rather than promised: `level_of()` answers None for anything
the catalog does not contain, and the Commissioning write door refuses on that
answer, so a hand-crafted POST cannot mint a tag no one authored.

Stdlib-only at import time (db.py's rule): PyYAML is imported lazily inside
`load()`, so anything that merely imports this module stays import-cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths

DOMAIN = "domain"
TOPIC = "topic"

# The catalog level -> the sources.yaml storage key, and -> the spelling the
# shipped sources.yaml editor takes (server.topic_add's `level` argument, which
# maps "specific" to the file's `granular:` itself). Both mappings live HERE so
# no caller guesses. personas.py holds the same two adapters for the persona
# lane; they agree by construction and test_stage0_c1_catalog pins that.
LEVEL_TO_YAML_KEY = {DOMAIN: "broad", TOPIC: "granular"}
LEVEL_TO_EDITOR_LEVEL = {DOMAIN: "broad", TOPIC: "specific"}


class CatalogError(Exception):
    """The catalog file is missing, unreadable, or malformed.

    Loud on purpose. A silently-degraded catalog would render a picker that
    offers a stranger fewer topics than the product can rank, and nothing on
    screen would say so."""


@dataclass(frozen=True)
class Domain:
    name: str
    topics: Tuple[str, ...] = ()


@dataclass
class Catalog:
    domains: List[Domain] = field(default_factory=list)

    # -- shape ---------------------------------------------------------------
    @property
    def domain_count(self) -> int:
        return len(self.domains)

    @property
    def topic_count(self) -> int:
        return sum(len(d.topics) for d in self.domains)

    @property
    def entry_count(self) -> int:
        """Everything a reader can pick — both levels. The picker's own count
        line is derived from this, never from a number typed into copy."""
        return self.domain_count + self.topic_count

    def names(self) -> List[str]:
        out: List[str] = []
        for d in self.domains:
            out.append(d.name)
            out.extend(d.topics)
        return out

    # -- resolution ----------------------------------------------------------
    def level_of(self, name: str) -> Optional[str]:
        """DOMAIN / TOPIC / None. Case- and whitespace-insensitive, because a
        reader's pick arrives over HTTP and a browser is not a promise."""
        return self._index().get(_key(name), (None, None))[0]

    def canonical(self, name: str) -> Optional[str]:
        """The catalog's own spelling of `name`, or None. What gets WRITTEN to
        sources.yaml is always this, never the bytes the client sent."""
        return self._index().get(_key(name), (None, None))[1]

    def resolve(self, name: str) -> Optional[Tuple[str, str]]:
        """(canonical_name, level) or None — the one lookup the write door
        uses, so an unknown name and a mis-cased known name cannot take
        different paths."""
        hit = self._index().get(_key(name))
        return (hit[1], hit[0]) if hit else None

    def _index(self) -> Dict[str, Tuple[str, str]]:
        cached = getattr(self, "_idx", None)
        if cached is None:
            cached = {}
            for d in self.domains:
                cached[_key(d.name)] = (DOMAIN, d.name)
                for t in d.topics:
                    cached[_key(t)] = (TOPIC, t)
            object.__setattr__(self, "_idx", cached)
        return cached


def _key(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


# ---------------------------------------------------------------------------
# Loading + validation
# ---------------------------------------------------------------------------

# Structural characters the shipped sources.yaml editor refuses (server._bad_name
# — a name carrying one of these would change the file's meaning). Checked HERE,
# at load, so a catalog entry that could never be written is caught by the suite
# rather than by a reader whose first act fails.
_UNWRITABLE = (":", "\n", "\r")


def load(path: Optional[Path] = None) -> Catalog:
    """Parse and validate the catalog. Raises CatalogError on any malformation.

    Validation is strict because this file is a vocabulary contract: duplicate
    or unwritable names would produce a picker whose selections cannot round-
    trip into sources.yaml, which is the one thing the Commissioning must never
    do."""
    import yaml  # lazy: keeps import-time stdlib-only

    src = Path(path) if path is not None else paths.TOPIC_CATALOG
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"topic catalog unreadable at {src}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — any parse failure is one class
        raise CatalogError(f"topic catalog is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise CatalogError("topic catalog must be a mapping with `domains:`")
    raw_domains = raw.get("domains")
    if not isinstance(raw_domains, list) or not raw_domains:
        raise CatalogError("topic catalog: `domains` must be a non-empty list")

    seen: Dict[str, str] = {}
    domains: List[Domain] = []
    for i, entry in enumerate(raw_domains):
        where = f"domains[{i}]"
        if not isinstance(entry, dict):
            raise CatalogError(f"{where} must be a mapping with `name`")
        name = _clean(entry.get("name"))
        if not name:
            raise CatalogError(f"{where}: `name` is required")
        _check_name(name, where, seen)
        # `or []` would swallow a mapping or an empty string here — a malformed
        # `topics:` block would then read as "this domain has no topics", which
        # is a silently smaller picker and exactly the degrade this module
        # refuses. An ABSENT key is the only lawful way to say "none".
        raw_topics = entry.get("topics")
        if raw_topics is None:
            raw_topics = []
        if not isinstance(raw_topics, list):
            raise CatalogError(f"{where} ({name}): `topics` must be a list")
        topics: List[str] = []
        for j, t in enumerate(raw_topics):
            tname = _clean(t)
            if not tname:
                raise CatalogError(f"{where}.topics[{j}]: empty topic name")
            _check_name(tname, f"{where}.topics[{j}]", seen)
            topics.append(tname)
        domains.append(Domain(name=name, topics=tuple(topics)))
    return Catalog(domains=domains)


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _check_name(name: str, where: str, seen: Dict[str, str]) -> None:
    key = _key(name)
    if key in seen:
        raise CatalogError(
            f"{where}: {name!r} repeats {seen[key]!r} — a catalog name carries "
            "exactly one level, so a duplicate makes the reader's pick "
            "ambiguous")
    for ch in _UNWRITABLE:
        if ch in name:
            raise CatalogError(
                f"{where}: {name!r} cannot be written to sources.yaml (it "
                f"contains {ch!r})")
    if name.lstrip().startswith("#"):
        raise CatalogError(
            f"{where}: {name!r} cannot be written to sources.yaml (a name may "
            "not start with '#')")
    seen[key] = name
