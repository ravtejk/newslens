"""Tier-2 discovery: one capped Perplexity Sonar call per run (milestone 2).

PAUSED BY RULING (principal 2026-07-25, DECISIONS.md "SONAR RULED: discovery
PAUSED"). `run_discovery` now reports itself PAUSED and builds no request on
the default path — key or no key. Everything below still works and still holds
its contract; it is reachable only through the explicit NL-102 testing opt-in
(config.discovery_enabled / NEWSLENS_DISCOVERY_ENABLED=1). The correctness
fixes in this module are deliberately live even while paused: the unpause path
must be safe on the day it is taken, not fixed on that day.

COLD SEAM until the principal grants PERPLEXITY_API_KEY: with no key present
this module builds no request and touches no socket — it reports itself as
skipped and the run proceeds RSS-only. The Sonar reliability spike (Rook's
dissent, DECISIONS.md 2026-07-02) stays gated on the key; when it lands, the
spike is one command: `scripts/sonar_spike` (dry-run by default since NL-97).

Guardrails (spec §A tier 2 + ENGINEERING.md cost rules):
  * PAUSE FIRST. The pause is checked before the key, before the prompt,
    before the budget — so a paused run reports the RULING, not a missing key.
  * ONE call per run. On a retryable failure (timeout / 5xx) exactly ONE
    retry; then the run degrades to RSS-only and says so.
  * Budget-guarded structurally: estimated call cost is checked against
    config.budget_cap_usd_per_run BEFORE the request. At Sonar-base pricing
    ($1/M tokens each way) a single capped call is ~$0.001 — the guard exists
    so a future prompt/model change cannot silently outgrow the cap.
  * Faithfulness: rows written to source_items come from Sonar's
    `search_results` (title + url + date per result) — real, attributable
    web sources. The generated answer TEXT is not a source and is never
    stored as a source excerpt; it is surfaced in the run report only.
    (Whether ranking needs it persisted is a milestone-3 question — ADR-0003.)
  * USABILITY GATE (NL-101, 2026-07-25). The answer text is now READ — never
    stored — as the retrieval set's own usability signal. When Sonar's answer
    DISCLAIMS its results ("I can't reliably answer this from the provided
    results alone…"), the whole set is dropped: a vendor that says its own
    retrieval failed is a degrade, and this product degrades loudly. Rows also
    pass a structural class filter (sitemap/feed files, homepages, section and
    author listings, AV pages, social posts) — every one of those classes was
    observed entering source_items as news (engineering-5.md §3.2/§3.4a/§9).
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from . import config, paths

PERPLEXITY_CHAT_URL = "https://api.perplexity.ai/chat/completions"
DISCOVERY_TIMEOUT_S = 30
DISCOVERY_MAX_TOKENS = 700          # answer budget; keeps the call summarization-scale
MAX_DISCOVERY_ITEMS = 8             # search_results rows stored per run
SONAR_USD_PER_MTOK = 1.0            # Sonar base model, both directions (spec §A)
PROMPT_FILE = "discovery_query.txt"
USER_AGENT = "NewsLens/0.1 (personal news briefing prototype; discovery)"


def build_prompt(cfg: "config.SourcesConfig") -> str:
    """Render the versioned prompt (prompts are code — ENGINEERING.md)."""
    template = (paths.PROMPTS_DIR / PROMPT_FILE).read_text(encoding="utf-8")
    outlet_names = ", ".join(s.name for s in cfg.fetchable_sources) or "none"
    interests = ", ".join(cfg.interests_broad + cfg.interests_granular)
    return template.format(
        today_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        outlets=outlet_names,
        interests=interests,
    )


def estimate_cost_usd(prompt: str, max_tokens: int = DISCOVERY_MAX_TOKENS) -> float:
    """Conservative pre-call estimate: chars/3 input tokens + full answer
    budget, priced at Sonar base rates both ways."""
    est_tokens = len(prompt) / 3 + max_tokens
    return est_tokens / 1_000_000 * SONAR_USD_PER_MTOK


def _post_once(key: str, body: bytes, timeout: int) -> dict:
    req = urllib.request.Request(
        PERPLEXITY_CHAT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def call_sonar(key: str, prompt: str, timeout: int = DISCOVERY_TIMEOUT_S) -> dict:
    """One call, at most one retry, and only on retry-able failures
    (timeout / connection / 5xx). 4xx is never retried — a bad request or bad
    key doesn't get better by asking twice, and pay-as-you-go billing makes
    blind retries a cost leak (Rook's runaway-retry flag, spec §A)."""
    body = json.dumps(
        {
            "model": "sonar",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": DISCOVERY_MAX_TOKENS,
        }
    ).encode("utf-8")
    try:
        return _post_once(key, body, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            return _post_once(key, body, timeout)  # the single retry
        raise
    except (urllib.error.URLError, TimeoutError, OSError):
        return _post_once(key, body, timeout)  # the single retry


# ---------------------------------------------------------------------------
# NL-101 — the usability gate. Two predicates, both PURE and offline.
# ---------------------------------------------------------------------------
# (a) THE ANSWER'S OWN DISCLAIMER. Sonar returns an answer alongside its
# retrieval set; when retrieval fails, the answer says so in the first clause.
# The shipped code discarded the answer and stored the set anyway — for ~3
# weeks, on the majority of live calls (5 of 8 interests, engineering-5.md §9).
# Each pattern below is anchored to a RECORDED specimen; none is invented.
# Patterns are matched against the answer's opening region only (see
# _DISCLAIMER_SCAN_CHARS): a disclaimer is a preamble, while a legitimate
# answer's later paragraphs may quote a source saying something similar.
#
# WHICH WAY AMBIGUITY FALLS: toward DROPPING. A false positive costs one run's
# discovery recall on a feature contributing 0.41% of citations; a false
# negative is the shipped bug — junk in the news database, in the paid
# vocabulary window, presented to the ranker as news.
# THE ANCHOR RULE (gate MEDIUM-1, 2026-07-26 — QA found it, the gate confirmed
# 8/8). The first cut made the first-person anchor OPTIONAL, so any pattern
# could match a NEWS SUBJECT rather than the model's own voice: "The safety
# board said it cannot determine the cause…", "Regulators do not see enough
# progress…", "Election results are dominated by turnout…" — all ordinary
# openings of a WORKING discovery answer, all wrongly read as the vendor
# disclaiming its own retrieval, all silently dropping a usable set.
#
# Every pattern below must now carry ONE of two anchors, and it is the anchor
# that makes the signal mean what its label says:
#   (A) MODEL-VOICED FIRST PERSON — a mandatory `i\s+`. Only the answering
#       model can disclaim the answering model's retrieval.
#   (B) AN EXPLICIT RETRIEVAL SUBJECT — "search results", "search set",
#       "result set", "retrieval set", or a tail naming where the shortfall
#       is ("… in these results"). Bare "results" is NOT such a subject:
#       election results, quarterly results and lab results are all news.
_RETRIEVAL_SUBJECT = (r"(?:search\s+results|search\s+set|result\s+set|"
                      r"retrieval\s+set|provided\s+results|"
                      r"returned\s+results|available\s+results)")
# "…in these results", "…from the provided search results", "…among the sources"
_RETRIEVAL_TAIL = (r"(?:in|from|among|within|across)\s+"
                   r"(?:these|those|the|this|its|your|our)?\s*"
                   r"(?:provided\s+|given\s+|available\s+|returned\s+|"
                   r"search\s+|indexed\s+)*"
                   r"(?:results?|result\s+set|retrieval\s+set|search\s+set|"
                   r"sources|search)")

_DISCLAIMER_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # "I can't reliably answer this from the provided results alone: …"
    # (engineering-5.md §3.4a, verbatim I3 specimen). ANCHOR A — the `i\s+` is
    # MANDATORY: "the commission says it cannot identify the source" is news.
    ("cannot-answer-from-results",
     re.compile(r"\bi\s+(?:can(?:'|’)?t|cannot|can\s+not|am\s+unable\s+to|"
                r"couldn(?:'|’)?t)\s+(?:reliably\s+|confidently\s+|"
                r"accurately\s+)?(?:answer|identify|determine|report|name|"
                r"provide)\b", re.I)),
    # "…from the provided results alone…" (same specimen, second signal).
    # ANCHOR B by construction — "the provided results" IS the subject.
    ("from-the-provided-results-alone",
     re.compile(r"\bfrom\s+the\s+(?:provided|given|available|returned)\s+"
                r"(?:search\s+)?results\s+alone\b", re.I)),
    # "I don't see enough current, directly sourced local-transit reporting in
    # these results…" (engineering-5.md §9 arm-A). ANCHOR A — mandatory `i\s+`.
    # "Regulators do not see enough progress on grid interconnection" is news.
    ("not-enough-material",
     re.compile(r"\bi\s+(?:do(?:n(?:'|’)?t|\s+not)\s+(?:see|find)|"
                r"cannot\s+see|can(?:'|’)?t\s+(?:see|find))\s+enough\b",
                re.I)),
    # The same shortfall stated impersonally. ANCHOR B — it only counts when
    # the sentence says WHERE the material is missing: "There are not enough
    # shelter beds for displaced residents" is news; "there are not enough
    # local-transit items in these results" is a disclaimer.
    ("not-enough-in-the-retrieval-set",
     re.compile(r"\b(?:there\s+(?:is|are)\s+(?:not|no)\b[^.]{0,40}?enough\b|"
                r"does\s+not\s+(?:include|contain)\s+enough\b)"
                r"[^.]{0,90}?\b" + _RETRIEVAL_TAIL + r"\b", re.I)),
    # "…does not include enough non-mainstream, hyperlocal … reporting"
    # ANCHOR B by construction — "results" is the grammatical subject here,
    # immediately adjacent, not an incidental noun elsewhere in the sentence.
    ("results-do-not-include-enough",
     re.compile(r"\bresults?\s+(?:do(?:es)?\s+not|don(?:'|’)?t)\s+"
                r"(?:include|contain|cover)\s+enough\b", re.I)),
    # "the search results are dominated by broad market and Middle East
    # coverage…" (engineering-5.md §9 arm-C). ANCHOR B — bare "results" DROPPED
    # from the alternation: "Election results are dominated by turnout in the
    # northern provinces" is a news sentence, and it fired here.
    ("retrieval-set-dominated-by",
     re.compile(r"\b" + _RETRIEVAL_SUBJECT + r"\s+(?:are|is|were|was)\s+"
                r"dominated\s+by\b", re.I)),
    # The empty-return shape the parity probe also recorded. ANCHOR B — the
    # subject is the search itself.
    ("no-relevant-results",
     re.compile(r"\bno\s+(?:relevant|current|recent|usable)?\s*"
                r"(?:search\s+)?results?\b[^.]{0,30}?\b(?:found|returned|"
                r"available|surfaced)\b", re.I)),
]
_DISCLAIMER_SCAN_CHARS = 600


def answer_disclaims(answer: Optional[str]) -> Optional[str]:
    """The label of the first disclaimer signal in `answer`, else None.

    Returns the LABEL (not a bool) so the run report can name which signal
    fired — a degrade the principal can audit, not a silent drop."""
    if not answer:
        return None
    head = answer.strip()[:_DISCLAIMER_SCAN_CHARS]
    for label, pattern in _DISCLAIMER_PATTERNS:
        if pattern.search(head):
            return label
    return None


def _answer_text(payload: dict) -> str:
    """Sonar's generated answer, read for its usability signal only. NEVER
    persisted — the faithfulness rule in the module docstring is unchanged."""
    try:
        return (payload["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


# (b) THE STRUCTURAL URL CLASSES. Every class below was OBSERVED entering
# source_items as news: a Bloomberg sitemap XML, bloomberg.com/ (homepage),
# /markets (section), /podcasts/series/… and youtube.com/watch (AV),
# advisorperspectives.com/firm/… and moneycontrol.com/author/… (listings),
# facebook.com + instagram.com posts (engineering-5.md §3.2 and §9(b)). Live
# production data: 50% of his three weeks of stored sonar rows are not stories.
#
# HONEST SCOPE, stated once: this filter catches the STRUCTURAL classes only.
# It cannot catch the EDITORIAL-QUALITY class — SEO aggregators like ts2.tech,
# stale-but-well-formed articles, listicles *about* underreported news. Those
# were the parity study's other findings, and they are why discovery is paused
# rather than merely filtered. That is the whole residual: the structural
# classes below are closed, including under the dated rescue.
_FEED_SUFFIXES = (".xml", ".rss", ".atom", ".json")
_SOCIAL_HOSTS = frozenset({
    "facebook.com", "instagram.com", "twitter.com", "x.com", "t.co",
    "threads.net", "tiktok.com", "linkedin.com", "pinterest.com",
    "mastodon.social", "bsky.app",
})
_AV_HOSTS = frozenset({
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
    "soundcloud.com", "spotify.com", "podcasts.apple.com",
})
# Listing segments split in two (gate MEDIUM-2, 2026-07-26), because a date in
# the path means opposite things on either side of the line.
#
# PURE INDEX — a page whose job is to list other pages. A date here narrows the
# INDEX ("everything we published on 16 July"), it does not make the page a
# story, so these are NEVER rescued by a date.
_INDEX_SEGMENTS = frozenset({
    "author", "authors", "byline", "contributor", "contributors", "firm",
    "tag", "tags", "topic", "topics", "category", "categories", "section",
    "sections", "sitemap", "sitemaps", "feed", "feeds", "rss", "search",
    "archive", "archives",
})
# DATED FORMAT — a segment that names a FORM of coverage. Undated it is a hub
# ("/live", "/world/series/middle-east-crisis"); dated it is one piece of
# coverage in that form ("/world/live/2026/jun/09/<headline>"). These ARE
# rescued by a date, and only by a date.
_DATED_FORMAT_SEGMENTS = frozenset({
    "live", "video", "videos", "watch", "series", "shows", "podcast",
    "podcasts", "streams",
})
# A single-segment path that is one of these is a SECTION FRONT, not a story.
_SECTION_FRONTS = frozenset({
    "news", "markets", "market", "business", "technology", "tech", "world",
    "us", "uk", "politics", "opinion", "sports", "sport", "finance",
    "economy", "economics", "health", "science", "climate", "energy",
    "companies", "industries", "latest", "home", "index", "trending",
    "headlines", "briefing", "newsletters", "explainers",
})

# THE DATED RESCUE — added after the first dry run against the principal's real
# store (which is exactly what a dry-run-first tool is for), then NARROWED by
# gate MEDIUM-2 after the first version leaked.
#
# The finding that created it: the naive segment rule flagged three Guardian
# LIVE BLOGS as listings, because "live" is a listing word —
#
#   /world/live/2026/jun/09/middle-east-crisis-iran-israel-us-…
#
# — and all three had been CITED by shipped briefings. A dated live blog is not
# a page of links; it is how a serious outlet covers a breaking story.
#
# The defect in the first fix: I let a ≥3-word TAIL SLUG rescue too, and a
# headline-shaped slug is exactly what listing pages have. That reopened the
# whole class — `/tag/israel-iran-conflict`, `/author/jane-marie-smith`,
# `/world/series/middle-east-crisis` all walked back through (12/12 on the
# gate's specimens; 5 live Guardian /world/series/ rows in his own store).
#
# SO: ONLY A DATE RESCUES, and only on the DATED-FORMAT segments. The slug
# heuristic is GONE — not weakened, removed. A date cannot rescue a pure index
# segment, because a dated index page is a day's archive, still a page of
# links. And nothing rescues past the HOST rules or a feed file: a
# youtube.com/watch URL with a beautiful dated slug is still a platform page,
# not an outlet's reporting.
_DATED_PATH = re.compile(
    r"/(?:19|20)\d{2}/"
    r"(?:0?[1-9]|1[0-2]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"[a-z]*/(?:[0-3]?\d)(?:/|$)", re.I)


def _has_dated_path(path: str) -> bool:
    """A /YYYY/MM/DD/ or /YYYY/mon/DD/ component — the publication stamp an
    outlet puts on one piece of coverage, not on a hub."""
    return bool(_DATED_PATH.search(path + "/"))


def _host_of(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


def url_reject_reason(url: str) -> Optional[str]:
    """None if this URL may enter source_items as news; else a REASON label.

    Pure, offline, deterministic — no HEAD request, no network. (A live
    HEAD-liveness gate was considered and deliberately NOT built here: the
    parity probe measured 37 of 69 Sonar URLs returning 403 bot-blocks, so a
    naive liveness gate would drop more real articles than dead ones. See the
    build report / NL-102.)"""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "not-http"
    parsed = urlparse(url)
    host = _host_of(url)
    if not host:
        return "no-host"
    path = (parsed.path or "/").rstrip("/") or "/"
    lowered = path.lower()

    if lowered.endswith(_FEED_SUFFIXES):
        return "feed-or-sitemap-file"
    root_host = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
    if host in _SOCIAL_HOSTS or root_host in _SOCIAL_HOSTS:
        return "social-post"
    if host in _AV_HOSTS or root_host in _AV_HOSTS:
        return "audio-video-page"
    if path == "/":
        return "homepage"
    segments = [s for s in lowered.split("/") if s]
    # PRECEDENCE (gate MEDIUM-2). Index first and unrescuable — a dated index
    # page is a day's archive, still a page of links.
    if any(seg in _INDEX_SEGMENTS for seg in segments):
        return "listing-or-section-page"
    # Then the dated formats, where the DATE (and nothing else) decides.
    if any(seg in _DATED_FORMAT_SEGMENTS for seg in segments):
        return None if _has_dated_path(lowered) else "listing-or-section-page"
    if len(segments) == 1 and segments[0] in _SECTION_FRONTS:
        return "section-front"
    return None


def _store_results(
    con: sqlite3.Connection, results: List[dict], now_iso: str
) -> Tuple[int, Dict[str, int]]:
    """search_results -> source_items rows (source_type='sonar'), idempotent
    per URL across all days, exactly like RSS rows. raw_excerpt stays NULL: we
    have title/url/date for these, not source text, and we don't fabricate.

    NL-142 (2026-08-06): this is the TWIN of ingest.upsert_item and moves with
    it. Keying on (url, UTC day) here would leave discovery re-inserting the
    same URLs daily after ingest stopped — half a dedupe is no dedupe. Same
    law, same reason. Discovery only ever INSERTS (it has no update branch),
    so there is no path here through which a re-sighting could move an
    existing row's fetched_at: the first sighting stands by construction.

    NL-101: returns (stored, {reject_reason: count}) so the caller can degrade
    LOUDLY — a dropped row is reported, never silently swallowed."""
    stored = 0
    dropped: Dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    with con:
        for result in results[:MAX_DISCOVERY_ITEMS]:
            url = (result.get("url") or "").strip()
            title = (result.get("title") or "").strip()
            if not title:
                drop("no-title")
                continue
            reason = url_reject_reason(url)
            if reason is not None:
                drop(reason)
                continue
            outlet = urlparse(url).netloc or "unknown"
            existing = con.execute(
                "SELECT id FROM source_items WHERE url = ? LIMIT 1",
                (url,),
            ).fetchone()
            if existing is not None:
                drop("already-known")
                continue  # already known (RSS beat us to it, or any re-run)
            con.execute(
                "INSERT INTO source_items"
                " (source_type, outlet, url, title, published_at, fetched_at,"
                "  raw_excerpt, wire_syndication_flag)"
                " VALUES ('sonar', ?, ?, ?, ?, ?, NULL, 0)",
                (outlet, url, title[:500], result.get("date"), now_iso),
            )
            stored += 1
    return stored, dropped


def _dropped_phrase(dropped: Dict[str, int]) -> str:
    """'2 dropped (feed-or-sitemap-file 1, homepage 1)' — or '' when clean."""
    real = {k: v for k, v in dropped.items() if k != "already-known"}
    if not real:
        return ""
    detail = ", ".join(f"{k} {v}" for k, v in sorted(real.items()))
    return f", {sum(real.values())} dropped ({detail})"


def run_discovery(
    con: sqlite3.Connection,
    cfg: "config.SourcesConfig",
    env: Optional[dict] = None,
    report=None,
    now_iso: Optional[str] = None,
) -> str:
    """Mutates report.discovery_* and returns the status string.

    Skip states build NO request (the zero-network-when-keyless rule covers
    the pipeline, not just the doctor). The PAUSE is the first of them, and it
    is checked before the key: a paused run must report the RULING, not a
    missing credential (that is the difference between a decision and a
    to-do)."""
    import os

    src_env = env if env is not None else os.environ
    now_iso = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def finish(status: str, items: int = 0) -> str:
        if report is not None:
            report.discovery_status = status
            report.discovery_items = items
        return status

    if not config.discovery_enabled(src_env):
        # THE PAUSE (2026-07-25). No key read, no prompt render, no budget
        # math, no socket — the whole metered path is unreachable from here.
        return finish(f"paused — {config.DISCOVERY_PAUSE_REASON}; RSS-only run")

    key = (src_env.get("PERPLEXITY_API_KEY") or "").strip()
    if not key:
        return finish(
            "skipped — PERPLEXITY_API_KEY not set (RSS-only run; the Sonar "
            "reliability spike is still gated on the key)"
        )
    if not cfg.has_interests:
        return finish(
            "skipped — no interests configured in sources.yaml (discovery asks "
            "'what matters to ME beyond my outlets'; it needs your tags)"
        )

    try:
        prompt = build_prompt(cfg)
    except OSError as exc:
        return finish(f"failed — cannot read prompts/{PROMPT_FILE} ({exc}); RSS-only run")
    except Exception as exc:  # noqa: BLE001 — deliberate: see below
        # The prompt file is principal-editable and build_prompt is a pure
        # local operation (file read + str.format), so ANY render error —
        # KeyError {typo}, AttributeError {outlets.upper()}, TypeError
        # {interests[x]}, UnicodeDecodeError, ... — must degrade like every
        # other discovery failure, not kill the run (BUG-3, class-wide per
        # M2 review finding 1).
        return finish(
            f"failed — prompts/{PROMPT_FILE} did not render "
            f"({type(exc).__name__}: {exc}); check its {{placeholders}} "
            f"and encoding; RSS-only run"
        )

    cap = config.budget_cap_usd_per_run(src_env)
    est = estimate_cost_usd(prompt)
    if est > cap:
        return finish(
            f"aborted — estimated discovery cost ${est:.4f} exceeds "
            f"BUDGET_CAP_USD_PER_RUN ${cap:.2f}; RSS-only run"
        )

    try:
        payload = call_sonar(key, prompt)
    except Exception as exc:
        reason = getattr(exc, "code", None) or getattr(exc, "reason", None) or exc
        return finish(
            f"failed — Sonar call unsuccessful after one retry "
            f"({type(exc).__name__}: {reason}); degraded to RSS-only for this run"
        )

    results = payload.get("search_results") or []
    usage = payload.get("usage") or {}
    tokens = usage.get("total_tokens")
    cost_phrase = (
        f", {tokens} tokens (~${tokens / 1_000_000 * SONAR_USD_PER_MTOK:.4f})"
        if tokens else ""
    )

    # NL-101 GATE — the answer's own usability signal, checked BEFORE any row
    # is written. The call is already paid for at this point; what this
    # prevents is a set the vendor itself calls unusable entering the news
    # database and the paid vocabulary window.
    signal = answer_disclaims(_answer_text(payload))
    if signal is not None:
        return finish(
            f"degraded — Sonar's own answer disclaims its retrieval set "
            f"({signal}); {len(results)} result(s) discarded unstored, "
            f"RSS-only for this run{cost_phrase}",
            items=0,
        )

    stored, dropped = _store_results(con, results, now_iso)
    return finish(
        f"ok — 1 Sonar call, {stored} discovered item(s) stored"
        + _dropped_phrase(dropped)
        + cost_phrase,
        items=stored,
    )


# ---------------------------------------------------------------------------
# RETRO-CLEAN (NL-101's second half) — the rows already in the database.
# ---------------------------------------------------------------------------
# The gate above stops NEW junk. These rows are the ~3 weeks of junk the gate
# was missing: live classification of his own store found HALF of all stored
# sonar rows are not stories (engineering-5.md §3.2a).
#
# WHAT THIS CAN AND CANNOT IDENTIFY — stated plainly, because it bounds the
# tool. The answer text was NEVER persisted (by design), so a historical row
# cannot be traced back to whether ITS call's answer disclaimed the set. The
# disclaimed-set rows are therefore not individually identifiable in
# retrospect; what IS identifiable is the structural junk class the same
# specimens exhibit (sitemaps, homepages, sections, AV, listings, social).
# This tool removes THAT class. It is a floor on the cleanup, not the whole of
# it, and calling it "the disclaimed-set cleanup" would overclaim.
#
# WHY A DRY-RUN-DEFAULT CLI VERB AND NOT A MIGRATION: a migration runs
# automatically inside `newslens migrate` with no preview and no consent —
# deleting a reader's rows on an upgrade is the opposite of the 0018 house
# pattern, which is ADDITIVE-ONLY and explicitly says "rollback = stop reading
# the column; nothing to undo". Deletion needs eyes on it first.
#
# CITED ROWS ARE NEVER TOUCHED. A row referenced by any shipped briefing's
# story_slots is retained even when its URL is junk: a shipped briefing's
# citation must keep resolving. Those rows are REPORTED, not deleted.

_REFERENCING_TABLES = ("briefings", "briefings_history")


def referenced_source_item_ids(con: sqlite3.Connection) -> Set[int]:
    """Every source_items id cited by a shipped briefing (live or archived).

    briefings.story_slots is JSON: [{"item_ids": [...], ...}, ...] — the same
    field analysis._cluster_items_for_slot, generate and the server read."""
    out: Set[int] = set()
    for table in _REFERENCING_TABLES:
        try:
            rows = con.execute(f"SELECT story_slots FROM {table}").fetchall()
        except sqlite3.Error:
            continue  # table absent on an older schema — nothing to protect
        for row in rows:
            try:
                slots = json.loads(row["story_slots"] or "[]")
            except (ValueError, TypeError):
                continue
            if not isinstance(slots, list):
                continue
            for slot in slots:
                if not isinstance(slot, dict):
                    continue
                for item_id in slot.get("item_ids") or []:
                    if isinstance(item_id, int):
                        out.add(item_id)
    return out


def scan_discovery_rows(con: sqlite3.Connection) -> Dict[str, List[dict]]:
    """Classify every stored discovery row. Read-only; writes nothing.

    Returns {"removable": [...], "cited": [...], "kept": [...]} where each row
    is {id, url, title, fetched_at, reason}. `cited` rows are junk-classed but
    referenced by a briefing, so they are retained on purpose."""
    referenced = referenced_source_item_ids(con)
    buckets: Dict[str, List[dict]] = {"removable": [], "cited": [], "kept": []}
    rows = con.execute(
        "SELECT id, url, title, fetched_at FROM source_items"
        " WHERE source_type = 'sonar' ORDER BY id"
    ).fetchall()
    for row in rows:
        reason = url_reject_reason(row["url"] or "")
        entry = {
            "id": row["id"], "url": row["url"], "title": row["title"],
            "fetched_at": row["fetched_at"], "reason": reason,
        }
        if reason is None:
            buckets["kept"].append(entry)
        elif row["id"] in referenced:
            buckets["cited"].append(entry)
        else:
            buckets["removable"].append(entry)
    return buckets


def clean_discovery_rows(con: sqlite3.Connection,
                         apply: bool = False) -> Dict[str, object]:
    """The retro-clean. DRY RUN BY DEFAULT — `apply=False` deletes nothing.

    Returns the scan plus {"applied": bool, "deleted": n}. Deletion is one
    transaction over an explicit id list; nothing is deleted by predicate, so
    the rows removed are exactly the rows the dry run printed."""
    scan = scan_discovery_rows(con)
    removable = scan["removable"]
    deleted = 0
    if apply and removable:
        ids = [entry["id"] for entry in removable]
        with con:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                con.execute(
                    "DELETE FROM source_items WHERE id IN "
                    f"({','.join('?' * len(chunk))})",
                    chunk,
                )
                deleted += len(chunk)
    return {**scan, "applied": bool(apply), "deleted": deleted}
