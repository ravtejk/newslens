"""RSS ingestion (milestone 2): principal's sources -> source_items rows.

THE INGESTION CONTRACT (binding; reviewer finding 5 made explicit):

  * THE ITEM IDENTITY KEY IS THE URL, ACROSS ALL DAYS (NL-142, 2026-08-06 —
    the principal's option-(c) ruling on the pool-cap escalation). One URL is
    one row, forever. Re-seeing a URL on any later day UPDATES that row; it
    never inserts a second one.
    WHY THIS CHANGED: the key used to be (url, UTC fetch-day), so every item
    still sitting in a feed re-inserted EVERY day. A feed's daily contribution
    was therefore its DEPTH (<= MAX_ITEMS_PER_FEED), not its publishing
    cadence — measured 249 of 563 rows on 2026-08-02 were same-URL re-inserts
    of 2026-08-01. That machine pinned the ranking pool at its 550 cap on 27
    of 48 runs and evicted the top of the reader's file every single day (the
    2026-08-06 run evicted 24: all 20 Bloomberg Markets + 4 Bloomberg
    Politics). Keying on the URL alone ends it.
  * RECENCY ANCHOR: `fetched_at` IS THE FIRST SIGHTING AND NEVER MOVES.
    This is the load-bearing half — get it wrong and the fix recreates the
    disease. A re-fetch may update the mutable fields (title/excerpt/
    published_at/outlet/wire flag, which upstream edits) but it must NEVER
    refresh `fetched_at`, or an item that merely lingers in a feed would stay
    permanently "recent" and the 14-day candidate window would never age
    anything out. An item is exactly as old as the first time we saw it. The
    UPDATE below therefore does not name `fetched_at`, and when historical
    duplicate rows exist for a URL we bind to the EARLIEST (MIN(id)) — the
    true first sighting, not the newest copy. `ranking.py`'s "developed =
    fetch time, first-seen" law finally means what it says.
  * IDEMPOTENT PER RUN — the law's intent survives and STRENGTHENS. Re-running
    ingest is still safe and still duplicates nothing; the guarantee simply
    widens from "safe within one UTC day" to "safe on any day". The old
    same-day promise is a strict subset of the new all-days one.
  * FETCH-DAY IS STILL THE UTC CALENDAR DAY where it is used for reporting and
    for the surviving UNIQUE(url, date(fetched_at)) index — which the URL key
    can never violate, since a second row for a known URL is never inserted at
    all. (`briefings.date`, by contrast, is principal-LOCAL — two different
    clocks, on purpose.)
  * Tiers: only enabled, non-reference_only sources with a URL are fetched
    (config.Source.fetchable). reference_only outlets are NEVER fetched.
    headline_only sources are fetched like any RSS feed — the tier is a
    downstream promise (titles/summaries + linkout only), not a fetch change.
  * Graceful degradation: one bad feed never kills the run. Per-source
    failures are collected and surfaced in IngestReport.degradation_message —
    the visible "N of M sources unavailable" line the spec's QA case demands.
    The run only fails outright if EVERY source fails or none are enabled.
  * No scraping: what the feed returns is all we take. Excerpts come from the
    feed's own summary/description, tags stripped, truncated.
  * Every fetch has a timeout and a per-source visible failure path.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import config, db, net

FEED_TIMEOUT_S = 20          # WaPo's feeds measured 8-10s in the M2 sweep; headroom
MAX_ITEMS_PER_FEED = 20      # per feed per run — bounds a run at 20 × enabled feeds (64-fetchable template after NL-142b's CNN disable ≈ 1280)
MAX_EXCERPT_CHARS = 1500
USER_AGENT = net.USER_AGENT  # ONE fetch identity, shared with the doctor (net.py)

# NL-142 item 3 — THE STALENESS TOOTH (slate-land gate charter R-E).
# A feed can answer HTTP 200 with perfectly valid RSS and still be an ARCHIVE:
# frozen years ago, never updated again. Nothing caught that. `check_feed_urls`
# asks "does it resolve", the zero-entry warning asks "did it parse to
# anything" — a feed serving twenty pristine 2023 stories passes both.
# Four instances were already on record when this landed (CNN Entertainment
# 2022-12-06, CNN Sport 2023-04-16, Yahoo Entertainment, NPR Shots), and the
# NL-142 measurement pass found a FIFTH live in the principal's own file and
# in the shipped template: `CNN` (cnn_topstories.rss), frozen at 2023-04-25,
# 20 items a day, 380 rows in the real DB across 20 distinct URLs, zero
# genuinely-new items on every measured ingest day.
#
# THRESHOLD, derived not guessed. The gate chartered 45-60d; 60 is chosen
# because the widest-spanning HEALTHY feed measured in the slate probes is
# CIDRAP — Antimicrobial Stewardship, whose 20 items spanned 55.93 days on
# 2026-08-06. A 45-day threshold would cry wolf on a legitimately low-cadence
# specialist feed; 60 clears it with real margin while still catching an
# archive frozen for years by three orders of magnitude. WARNING, never a
# failure: a quiet feed is the reader's call to make, not ours.
STALE_FEED_DAYS = 60

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class ParsedItem:
    url: str
    title: str
    published_at: Optional[str]  # UTC ISO-8601 when the feed provides it
    excerpt: Optional[str]


@dataclass
class IngestReport:
    """One run's outcome. `degradation_message` is the contract line."""

    attempted: int = 0
    succeeded: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)  # source name -> reason
    items_new: int = 0
    items_updated: int = 0
    items_skipped: int = 0  # entries missing url/title
    discovery_status: str = "not attempted"
    discovery_items: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def degradation_message(self) -> Optional[str]:
        """The visible partial-failure line (spec §E M2 / ENGINEERING.md
        'degrade gracefully and say so in the output')."""
        if not self.failed:
            return None
        names = ", ".join(sorted(self.failed))
        return (
            f"{len(self.failed)} of {self.attempted} sources unavailable this run: "
            f"{names} — briefing inputs come from the remaining "
            f"{len(self.succeeded)}"
        )

    @property
    def any_success(self) -> bool:
        return len(self.succeeded) > 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
    )


def utc_fetch_day(now_iso: Optional[str] = None) -> str:
    """The UTC calendar day used as the dedupe boundary (see module contract)."""
    return (now_iso or utc_now_iso())[:10]


def strip_html(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()


def fetch_feed_bytes(url: str, timeout: int = FEED_TIMEOUT_S) -> bytes:
    """Shared opener (308-following) + hard byte cap — see net.py. A feed that
    exceeds the cap is a loud per-source failure, not an unbounded read."""
    return net.fetch_bytes(url, timeout=timeout)


def _entry_published_iso(entry) -> Optional[str]:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None) or (
            entry.get(attr) if hasattr(entry, "get") else None
        )
        if parsed:
            try:
                return time.strftime("%Y-%m-%dT%H:%M:%SZ", parsed)  # struct_time is UTC
            except (TypeError, ValueError):
                continue
    return None


def parse_entries(raw: bytes) -> Tuple[List[ParsedItem], int]:
    """Feed bytes -> ParsedItems (capped), plus how many entries were skipped
    for missing url/title. Uses feedparser: mature RSS/Atom/RDF handling is
    exactly the wheel not to reinvent (ADR-0003)."""
    import feedparser  # third-party; ingest only runs post-install

    parsed = feedparser.parse(raw)
    if parsed.get("bozo") and not parsed.entries:
        exc = parsed.get("bozo_exception")
        raise ValueError(f"feed did not parse: {exc or 'unknown parse error'}")
    items: List[ParsedItem] = []
    skipped = 0
    for entry in parsed.entries[:MAX_ITEMS_PER_FEED]:
        url = (entry.get("link") or "").strip()
        title = strip_html(entry.get("title") or "").strip()
        if not url.startswith(("http://", "https://")) or not title:
            skipped += 1
            continue
        summary = entry.get("summary") or entry.get("description") or ""
        excerpt = strip_html(summary)[:MAX_EXCERPT_CHARS] or None
        items.append(
            ParsedItem(
                url=url,
                title=title[:500],
                published_at=_entry_published_iso(entry),
                excerpt=excerpt,
            )
        )
    return items, skipped


def upsert_item(
    con: sqlite3.Connection,
    source: "config.Source",
    item: ParsedItem,
    now_iso: str,
) -> str:
    """Insert once per URL, ever; update in place on every later sighting.
    Returns 'new' or 'updated'.

    SELECT-then-write instead of ON CONFLICT: this app is single-writer and
    the boring readable form wins (ADR-0003, ADR-0022). `now_iso` is used ONLY
    as the fetched_at of a genuinely new row — never to move an existing one.

    ORDER BY id LIMIT 1 is not decoration. Rows written before NL-142 can have
    several copies of one URL (one per day it was re-inserted); binding to the
    EARLIEST is what makes fetched_at the first sighting rather than the most
    recent one. The later copies are inert leftovers that age out of the
    candidate window on their own — see ADR-0022 on why no migration deletes
    them.
    """
    row = con.execute(
        "SELECT id FROM source_items WHERE url = ? ORDER BY id LIMIT 1",
        (item.url,),
    ).fetchone()
    if row is not None:
        # THE RECENCY ANCHOR IS NOT IN THIS COLUMN LIST, ON PURPOSE. Adding
        # `fetched_at = ?` here would make every lingering feed item eternally
        # fresh and re-create the exact defect NL-142 removed. Mutable fields
        # only; the first sighting stands.
        con.execute(
            "UPDATE source_items SET outlet = ?, title = ?, published_at = ?,"
            " raw_excerpt = ?, wire_syndication_flag = ? WHERE id = ?",
            (
                source.name,
                item.title,
                item.published_at,
                item.excerpt,
                1 if source.wire_syndication else 0,
                row["id"],
            ),
        )
        return "updated"
    con.execute(
        "INSERT INTO source_items"
        " (source_type, outlet, url, title, published_at, fetched_at,"
        "  raw_excerpt, wire_syndication_flag)"
        " VALUES ('rss', ?, ?, ?, ?, ?, ?, ?)",
        (
            source.name,
            item.url,
            item.title,
            item.published_at,
            now_iso,
            item.excerpt,
            1 if source.wire_syndication else 0,
        ),
    )
    return "new"


def feed_staleness(
    items: List[ParsedItem], now_iso: Optional[str] = None
) -> Dict[str, object]:
    """How old is this feed's NEWEST item? The staleness tooth's measurement.

    Pure function over already-parsed items so it is testable without a
    network and reusable by the doctor. Returns:
      total     — items parsed
      dated     — how many carried a published_at at all
      newest    — the newest published_at seen, or None
      age_days  — days from `newest` to now, or None when undeterminable
      dateless  — True when the feed parsed items but dated NONE of them

    DATELESS IS NOT STALE. Some feeds legitimately publish no dates at all
    (Fierce Healthcare ships 20 undated items); calling those stale would be a
    fabricated claim about content we cannot date. They get their own
    informational flag and are exempt from the age threshold — the caller
    decides how to say so."""
    total = len(items)
    dates = sorted(i.published_at for i in items if i.published_at)
    out: Dict[str, object] = {
        "total": total,
        "dated": len(dates),
        "newest": dates[-1] if dates else None,
        "age_days": None,
        "dateless": total > 0 and not dates,
    }
    if not dates:
        return out
    now = _parse_iso_utc(now_iso) if now_iso else datetime.now(timezone.utc)
    newest = _parse_iso_utc(dates[-1])
    if now is None or newest is None:
        return out
    out["age_days"] = round((now - newest).total_seconds() / 86400.0, 2)
    return out


def _parse_iso_utc(value: str) -> Optional[datetime]:
    """Lenient ISO-8601 -> aware UTC datetime; None when unparseable.

    Feeds are not trustworthy about format, and a date we cannot parse must
    degrade to 'undeterminable' rather than to a wrong age."""
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def stale_feed_warning(source_name: str, staleness: Dict[str, object]) -> Optional[str]:
    """The run-log line for a feed whose newest item is past the threshold."""
    age = staleness.get("age_days")
    if age is None or not isinstance(age, float) or age <= STALE_FEED_DAYS:
        return None
    newest = staleness.get("newest") or "unknown"
    return (
        f"{source_name}: feed is ENABLED but its newest item is {age:.0f} days old "
        f"(published {str(newest)[:10]}) — it fetches and parses fine, so nothing "
        f"else flags it, but it may be a frozen archive rather than a live feed. "
        f"Check the rss_url, or disable it if the outlet retired the feed"
    )


def ingest_source(
    con: sqlite3.Connection, source: "config.Source", now_iso: str
) -> Tuple[int, int, int, Dict[str, object]]:
    """Fetch + upsert one source, transactionally.
    Returns (new, updated, skipped, staleness).
    Raises on failure — the caller owns the degrade-gracefully decision."""
    raw = fetch_feed_bytes(source.rss_url)
    items, skipped = parse_entries(raw)
    staleness = feed_staleness(items, now_iso)
    with con:  # one transaction per source: a failed feed leaves no half-writes
        counts = {"new": 0, "updated": 0}
        for item in items:
            counts[upsert_item(con, source, item, now_iso)] += 1
    return counts["new"], counts["updated"], skipped, staleness


def run_ingest(
    con: Optional[sqlite3.Connection] = None,
    cfg: Optional[config.SourcesConfig] = None,
    env: Optional[dict] = None,
    with_discovery: bool = True,
) -> IngestReport:
    """The milestone-2 pipeline stage: RSS tier-1 pull + (when a key exists)
    the capped Sonar tier-2 discovery call. Raises SourcesParseError for the
    polite-refusal states; degrades per-source for everything else."""
    cfg = cfg if cfg is not None else config.load_sources()
    sources = config.require_active_sources(cfg)  # polite refusal lives here

    own_con = con is None
    if own_con:
        db.migrate()  # idempotent; ingest must work on a fresh clone
        con = db.connect()

    report = IngestReport(attempted=len(sources))
    report.warnings.extend(cfg.warnings)
    now_iso = utc_now_iso()
    try:
        for source in sources:
            try:
                new, updated, skipped, staleness = ingest_source(
                    con, source, now_iso
                )
            except Exception as exc:  # per-source seam: degrade, never die
                reason = getattr(exc, "reason", None) or getattr(exc, "code", None) or exc
                report.failed[source.name] = f"{type(exc).__name__}: {reason}"
                continue
            report.succeeded.append(source.name)
            report.items_new += new
            report.items_updated += updated
            report.items_skipped += skipped
            if new + updated + skipped == 0:
                # M2 QA observation 2: well-formed HTML at an rss_url parses
                # "successfully" with zero entries forever — a silent hole
                # unless the run report says so.
                report.warnings.append(
                    f"{source.name}: fetched and parsed but yielded 0 entries — "
                    "rss_url may point at an HTML page or an empty feed "
                    "(scripts/doctor's feed-shape check can confirm)"
                )
            # NL-142 item 3: the runtime half of the staleness tooth. The
            # zero-entry warning above catches an EMPTY feed; this catches a
            # FULL one that stopped moving years ago. Both are silent successes
            # to every other check in the pipeline.
            _stale = stale_feed_warning(source.name, staleness)
            if _stale:
                report.warnings.append(_stale)

        if with_discovery:
            from . import discovery  # local import: keeps ingest importable alone

            discovery.run_discovery(con, cfg, env=env, report=report, now_iso=now_iso)
    finally:
        if own_con:
            con.close()
    return report
