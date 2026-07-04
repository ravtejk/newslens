"""RSS ingestion (milestone 2): principal's sources -> source_items rows.

THE INGESTION CONTRACT (binding; reviewer finding 5 made explicit):

  * FETCH-DAY IS THE UTC CALENDAR DAY. `source_items.fetched_at` is UTC
    ISO-8601 and the idempotency key is UNIQUE(url, date(fetched_at)) — so the
    dedupe boundary is midnight UTC, NOT the principal's local midnight.
    A late-evening US-local run and the next morning's run can therefore land
    on different fetch-days and re-snapshot the same URL: understood behavior,
    not a bug. (`briefings.date`, by contrast, is principal-LOCAL — two
    different clocks, on purpose.)
  * Idempotent per (url, UTC fetch-day): re-running ingest the same UTC day
    updates the existing snapshot row in place (title/excerpt/published_at may
    have been edited upstream); it never duplicates. `fetched_at` of the
    original snapshot is preserved on update so the row stays on its day.
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
MAX_ITEMS_PER_FEED = 20      # per feed per run; ~30 enabled feeds => bounded volume
MAX_EXCERPT_CHARS = 1500
USER_AGENT = net.USER_AGENT  # ONE fetch identity, shared with the doctor (net.py)

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
    """Insert or same-UTC-day update. Returns 'new' or 'updated'.

    SELECT-then-write instead of ON CONFLICT: the dedupe key lives in an
    expression index (date(fetched_at)) and this app is single-writer, so the
    boring readable form wins (ADR-0003). fetched_at is preserved on update.
    """
    day = utc_fetch_day(now_iso)
    row = con.execute(
        "SELECT id FROM source_items WHERE url = ? AND date(fetched_at) = ?",
        (item.url, day),
    ).fetchone()
    if row is not None:
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


def ingest_source(
    con: sqlite3.Connection, source: "config.Source", now_iso: str
) -> Tuple[int, int, int]:
    """Fetch + upsert one source, transactionally. Returns (new, updated, skipped).
    Raises on failure — the caller owns the degrade-gracefully decision."""
    raw = fetch_feed_bytes(source.rss_url)
    items, skipped = parse_entries(raw)
    with con:  # one transaction per source: a failed feed leaves no half-writes
        counts = {"new": 0, "updated": 0}
        for item in items:
            counts[upsert_item(con, source, item, now_iso)] += 1
    return counts["new"], counts["updated"], skipped


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
                new, updated, skipped = ingest_source(con, source, now_iso)
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

        if with_discovery:
            from . import discovery  # local import: keeps ingest importable alone

            discovery.run_discovery(con, cfg, env=env, report=report, now_iso=now_iso)
    finally:
        if own_con:
            con.close()
    return report
