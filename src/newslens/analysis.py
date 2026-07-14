"""The Analyst's retrieval leg — full-text fetch of cluster-linked articles
(M9 milestone 1; the analysis call itself lands at milestone 2).

Mirrors discovery.py's guard shape: structural boundaries stated here, held
in code, degraded LOUDLY — never silently. The sourcing posture is the
principal's 2026-07-06 ruling (DECISIONS.md), binding, four boundaries:

  * TIER-SCOPED: only `tier in {full, cautious}` outlets are ever fetched.
    headline_only (paywalled by the principal's own source list — Bloomberg,
    WaPo) and reference_only (NYT: referenced-never-fetched, an M2-era
    structural ruling) are excluded BEFORE any socket opens; exclusions are
    still recorded, because silently skipping sources is the second silent
    ceiling Ada's thread warned about.
  * ROBOTS-RESPECTING: robots.txt is fetched (once per host per run, cached)
    and honored for our user agent before any article fetch. Unreachable
    robots (network error / 5xx) = DENY, per RFC 9309's conservative
    reading; absent robots (404) = allow, per the same convention.
  * ATTRIBUTED: every fetch attempt returns a FetchRecord (url, source,
    outcome, chars) — milestone 2's brief manifest persists them; nothing
    is read that can't be pointed at afterward.
  * SINGLE-USER PACED: fetches are sequential with a polite delay; there is
    no parallelism, no retry storm (one attempt per URL per run), and every
    read is byte-capped through net.py's shared opener discipline.

Extraction is a stdlib heuristic BY DECISION (Pax's position, engineering
2026-07-06): no dependency until measurement demands one. Every fetch is
instrumented (outcome / extracted chars / per-source success) because the
week-1 extraction success rate IS the pre-registered decision input — a
rate under 30% brings the extraction-dep question forward (DECISIONS.md
revisit clause).

ZERO LLM spend lives in this module. It reads pages; it never calls models.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.robotparser
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import net

ANALYSIS_UA = "NewsLens/0.1 (personal news briefing prototype; single-user analyst fetch)"
MAX_ARTICLE_BYTES = 2_000_000
FETCH_TIMEOUT_S = 15
POLITE_DELAY_S = 1.0          # between consecutive network fetches, any host
CRAWL_DELAY_CEILING_S = 10.0  # cap on honored Crawl-delay (M9-M1 gate: a
                              # hostile robots.txt must not wedge the run)
MIN_EXTRACT_CHARS = 700       # length floor: below this, extraction "succeeded" at nothing
PAYWALL_NEAR_CHARS = 2 * MIN_EXTRACT_CHARS  # short text + marker = paywall-suspected
MAX_LINK_DENSITY = 0.5        # anchor-text share above this = nav/shell, not prose
ANALYST_FETCH_TIERS = {"full", "cautious"}

# Outcome vocabulary (closed; the dispatch's instrumentation contract).
OK = "ok"
ROBOTS_DENIED = "robots-denied"
PAYWALL_SUSPECTED = "paywall-suspected"
EMPTY = "empty"
ERROR = "error"
TIER_EXCLUDED = "tier-excluded"
OUTCOMES = (OK, ROBOTS_DENIED, PAYWALL_SUSPECTED, EMPTY, ERROR, TIER_EXCLUDED)

# Case-insensitive markers of a subscription wall. Matched against the WHOLE
# page text (walls often live outside the extracted article node).
PAYWALL_MARKERS = (
    "subscribe to continue",
    "subscribe to read",
    "subscription required",
    "to continue reading",
    "sign in to keep reading",
    "already a subscriber",
    "this content is for subscribers",
    "create a free account to continue",
    "unlock this article",
)


@dataclass
class FetchRecord:
    """One row of the week-1 readout. Everything the dispatch asked
    instrumented: outcome, extracted size, per-source attribution."""
    url: str
    source_name: str
    tier: str
    outcome: str
    chars: int = 0
    elapsed_s: float = 0.0
    detail: str = ""
    title: str = ""
    text: str = ""          # extracted article text (ok outcomes only)
    attempted: bool = True  # False for tier-excluded (no socket was opened)


@dataclass
class ExtractResult:
    text: str
    title: str
    method: str            # "article-tag" | "paragraphs"
    link_density: float
    page_text: str         # full-page visible text (paywall marker scan)

    @property
    def chars(self) -> int:
        return len(self.text)


# ---------------------------------------------------------------------------
# Extraction — stdlib heuristic (Pax: boring first, measure, then decide)
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    """Single pass over the document, collecting paragraph text twice-scoped:
    paragraphs inside an <article> element, and all body paragraphs. The
    chooser prefers the article scope when it carries enough text.

    Content inside script/style/template/svg/iframe/noscript is DROPPED WITH
    THE TAG (an injection payload in a <script> never reaches the text);
    nav/header/footer/aside/form content is treated as chrome, not prose.
    Visible text is preserved verbatim otherwise — sanitizing CONTENT is the
    validator's job downstream (M2), and extraction hiding a hostile string
    would blind that validator (Rook's fixture demand pins this).
    """

    DROP = {"script", "style", "noscript", "template", "svg", "iframe",
            "head", "select", "option"}
    CHROME = {"nav", "header", "footer", "aside", "form", "figure", "button"}
    VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr",
            "area", "base", "col", "embed", "track", "param"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._drop_depth = 0
        self._chrome_depth = 0
        self._article_depth = 0
        self._p_depth = 0
        self._a_depth = 0
        self._in_title = False
        self._title_done = False
        self._buf: List[str] = []
        self.title = ""
        self.article_paras: List[str] = []
        self.all_paras: List[str] = []
        self.page_chunks: List[str] = []
        self.anchor_chars = 0
        self.text_chars = 0

    # -- tag walk ------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.VOID:
            return
        if tag in self.DROP:
            self._drop_depth += 1
            return
        if tag in self.CHROME:
            self._chrome_depth += 1
        if tag == "article":
            self._article_depth += 1
        if tag == "title" and not self._title_done:
            # FIRST title element only: the document title lives in <head>
            # before any content; later <title>s are SVG-icon accessibility
            # labels ("Visit our Facebook page") — chrome, not identity.
            self._in_title = True
        if tag == "a":
            self._a_depth += 1
        if tag == "p":
            self._p_depth += 1
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        if tag in self.DROP:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if tag in self.CHROME:
            self._chrome_depth = max(0, self._chrome_depth - 1)
        if tag == "article":
            self._article_depth = max(0, self._article_depth - 1)
        if tag == "title":
            self._in_title = False
            self._title_done = True
        if tag == "a":
            self._a_depth = max(0, self._a_depth - 1)
        if tag == "p":
            self._p_depth = max(0, self._p_depth - 1)
            para = _WS_RE.sub(" ", " ".join(self._buf)).strip()
            self._buf = []
            if not para:
                return
            if self._chrome_depth == 0:
                self.all_paras.append(para)
                if self._article_depth > 0:
                    self.article_paras.append(para)

    def handle_data(self, data: str) -> None:
        # title first: <title> lives inside <head>, which is otherwise a
        # DROP subtree — the title branch must win that ordering.
        if self._in_title:
            self.title += data
            return
        if self._drop_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        self.page_chunks.append(text)
        self.text_chars += len(text)
        if self._a_depth > 0:
            self.anchor_chars += len(text)
        if self._p_depth > 0 and self._chrome_depth == 0:
            self._buf.append(data)


def extract_article_text(html_text: str) -> ExtractResult:
    """Best-effort article body from raw HTML. Never raises on malformed
    markup (HTMLParser is forgiving by design); the caller judges the result
    against the length floor and link density."""
    parser = _TextExtractor()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        # A page broken enough to crash even the tolerant parser yields
        # whatever was collected before the crash — the floor judges it.
        pass
    article = "\n\n".join(parser.article_paras)
    everything = "\n\n".join(parser.all_paras)
    if len(article) >= MIN_EXTRACT_CHARS:
        text, method = article, "article-tag"
    else:
        text, method = everything, "paragraphs"
    density = (parser.anchor_chars / parser.text_chars) if parser.text_chars else 1.0
    return ExtractResult(
        text=text,
        title=_WS_RE.sub(" ", parser.title).strip(),
        method=method,
        link_density=density,
        page_text=" ".join(parser.page_chunks),
    )


def _decode(body: bytes) -> str:
    """Charset from the document if it says, utf-8 with replacement if not —
    a garbled accent never fails a fetch."""
    head = body[:2048].decode("ascii", "ignore").lower()
    m = re.search(r'charset=["\']?([a-z0-9_\-]+)', head)
    if m:
        try:
            return body.decode(m.group(1), "replace")
        except LookupError:
            pass
    return body.decode("utf-8", "replace")


def classify_extraction(res: ExtractResult) -> Tuple[str, str]:
    """(outcome, detail) for a fetched page, per the closed vocabulary."""
    page_lower = res.page_text.lower()
    marker = next((mk for mk in PAYWALL_MARKERS if mk in page_lower), None)
    if marker and res.chars < PAYWALL_NEAR_CHARS:
        return PAYWALL_SUSPECTED, f"marker {marker!r} with only {res.chars} chars extracted"
    if res.chars < MIN_EXTRACT_CHARS:
        return EMPTY, (f"{res.chars} chars extracted (floor {MIN_EXTRACT_CHARS}); "
                       f"link density {res.link_density:.2f}")
    if res.link_density > MAX_LINK_DENSITY:
        return EMPTY, (f"link density {res.link_density:.2f} exceeds "
                       f"{MAX_LINK_DENSITY} — navigation shell, not prose")
    return OK, f"method {res.method}"


# ---------------------------------------------------------------------------
# robots.txt — fetched once per host per run, honored, conservative on error
# ---------------------------------------------------------------------------

FetchFn = Callable[..., bytes]


class RobotsCache:
    """Per-host robots verdicts for one run. `fetch` is injectable so the
    offline suite never opens a socket."""

    def __init__(self, fetch: FetchFn = net.fetch_bytes) -> None:
        self._fetch = fetch
        self._parsers: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._denied_hosts: Dict[str, str] = {}

    def allows(self, url: str) -> Tuple[bool, str]:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if not host or parsed.scheme not in ("http", "https"):
            return False, "not an http(s) URL"
        if host in self._denied_hosts:
            return False, self._denied_hosts[host]
        if host not in self._parsers:
            self._load(parsed.scheme, host)
            if host in self._denied_hosts:
                return False, self._denied_hosts[host]
        rp = self._parsers.get(host)
        if rp is None:
            return True, "no robots.txt (404) — allowed by convention"
        if rp.can_fetch(ANALYSIS_UA, url):
            return True, "robots.txt allows"
        return False, f"robots.txt disallows this path for our agent on {host}"

    def delay_for(self, url: str) -> float:
        """Politeness delay before fetching this URL: the host's stated
        Crawl-delay clamped to [POLITE_DELAY_S, CRAWL_DELAY_CEILING_S]
        (M9-M1 gate ruling — respect stated delays; never let a hostile
        robots wedge the run). A host not yet consulted returns the floor:
        its robots loads with the fetch that follows, so stated delays bind
        from the second same-host attempt — over-simple, never under-polite."""
        host = urlparse(url).netloc.lower()
        rp = self._parsers.get(host)
        if rp is None:
            return POLITE_DELAY_S
        try:
            raw = rp.crawl_delay(ANALYSIS_UA)
        except Exception:
            return POLITE_DELAY_S
        if raw is None:
            return POLITE_DELAY_S
        return max(POLITE_DELAY_S, min(float(raw), CRAWL_DELAY_CEILING_S))

    def _load(self, scheme: str, host: str) -> None:
        robots_url = f"{scheme}://{host}/robots.txt"
        try:
            body = self._fetch(robots_url, timeout=FETCH_TIMEOUT_S,
                               cap=512_000, user_agent=ANALYSIS_UA)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 or exc.code == 410:
                self._parsers[host] = None  # absent robots = allow (RFC 9309)
            else:
                # 401/403/5xx: the site is answering and not saying yes —
                # conservative deny for this run (RFC 9309 unreachable rule).
                self._denied_hosts[host] = (
                    f"robots.txt unreadable (HTTP {exc.code}) — denying "
                    "conservatively this run")
            return
        except Exception as exc:
            self._denied_hosts[host] = (
                f"robots.txt unreachable ({type(exc).__name__}) — denying "
                "conservatively this run")
            return
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(_decode(body).splitlines())
        except Exception:
            self._parsers[host] = None  # unparseable robots = no rules stated
            return
        self._parsers[host] = rp


# ---------------------------------------------------------------------------
# The fetch loop — sequential, polite, attributed
# ---------------------------------------------------------------------------

def tier_allows_fetch(tier: str) -> bool:
    return tier in ANALYST_FETCH_TIERS


def fetch_article(url: str, source_name: str, tier: str,
                  robots: RobotsCache,
                  fetch: FetchFn = net.fetch_bytes) -> FetchRecord:
    """One attributed fetch attempt. Never raises; every path returns a
    record with a closed-vocabulary outcome."""
    rec = FetchRecord(url=url, source_name=source_name, tier=tier, outcome=ERROR)
    if not tier_allows_fetch(tier):
        rec.outcome = TIER_EXCLUDED
        rec.attempted = False
        rec.detail = (f"tier {tier!r} is outside the analyst's fetch scope "
                      "(principal ruling 2026-07-06) — Sonar/background only")
        return rec
    allowed, why = robots.allows(url)
    if not allowed:
        rec.outcome = ROBOTS_DENIED
        rec.detail = why
        return rec
    t0 = time.monotonic()
    try:
        body = fetch(url, timeout=FETCH_TIMEOUT_S, cap=MAX_ARTICLE_BYTES,
                     user_agent=ANALYSIS_UA)
    except Exception as exc:
        rec.elapsed_s = round(time.monotonic() - t0, 2)
        rec.outcome = ERROR
        code = getattr(exc, "code", None)
        rec.detail = f"HTTP {code}" if code else f"{type(exc).__name__}: {exc}"
        return rec
    rec.elapsed_s = round(time.monotonic() - t0, 2)
    res = extract_article_text(_decode(body))
    rec.title = res.title
    outcome, detail = classify_extraction(res)
    rec.outcome, rec.detail = outcome, detail
    if outcome == OK:
        rec.text = res.text
        rec.chars = res.chars
    else:
        rec.chars = res.chars
    return rec


def fetch_cluster_articles(
    items: List[Dict[str, str]],
    robots: Optional[RobotsCache] = None,
    fetch: FetchFn = net.fetch_bytes,
    sleep: Callable[[float], None] = time.sleep,
) -> List[FetchRecord]:
    """Fetch a cluster's linked articles: items are dicts with url /
    source_name / tier (the caller reads them off source_items). Sequential
    by construction; a polite delay separates consecutive NETWORK attempts —
    the host's stated Crawl-delay when one exists, clamped to
    [POLITE_DELAY_S, CRAWL_DELAY_CEILING_S] (tier exclusions cost no delay;
    a cached robots denial still pays the delay — over-polite by design,
    never under). Duplicate URLs are fetched once."""
    robots = robots or RobotsCache(fetch=fetch)
    records: List[FetchRecord] = []
    seen: set = set()
    did_network = False
    for item in items:
        url = (item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        if urlparse(url).scheme not in ("http", "https"):
            records.append(FetchRecord(
                url=url, source_name=item.get("source_name", ""),
                tier=item.get("tier", ""), outcome=ERROR, attempted=False,
                detail="not an http(s) URL"))
            continue
        will_attempt = tier_allows_fetch(item.get("tier", ""))
        if will_attempt and did_network:
            sleep(robots.delay_for(url))
        rec = fetch_article(url, item.get("source_name", ""),
                            item.get("tier", ""), robots, fetch=fetch)
        if rec.attempted:
            did_network = True
        records.append(rec)
    return records


def fetch_stats(records: List[FetchRecord]) -> Dict:
    """The week-1 readout seed: outcome counts, success rate over ATTEMPTED
    fetches (tier exclusions are policy, not extraction failures), and
    per-source success — feeds the pre-registered <30% dep decision."""
    by_outcome: Dict[str, int] = {}
    per_source: Dict[str, Dict[str, int]] = {}
    for r in records:
        by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1
        s = per_source.setdefault(r.source_name or "(unknown)",
                                  {"ok": 0, "attempted": 0})
        if r.attempted:
            s["attempted"] += 1
            if r.outcome == OK:
                s["ok"] += 1
    attempted = sum(1 for r in records if r.attempted)
    ok = by_outcome.get(OK, 0)
    return {
        "records": len(records),
        "attempted": attempted,
        "ok": ok,
        "success_rate": round(ok / attempted, 3) if attempted else None,
        "by_outcome": by_outcome,
        "per_source": per_source,
        "total_chars": sum(r.chars for r in records if r.outcome == OK),
    }


# ===========================================================================
# M9 milestone 2 — the analysis call + citation checking
# ===========================================================================
# The organ itself. Contract: workspace/debates/2026-07-06--newslens--
# content.md §5 (binding); the borrowed-inference rule is the principal's
# 2026-07-06 ruling, verbatim in DECISIONS.md: the analyst collects and
# condenses the SOURCE WRITERS' takes with receipts — it never renders its
# own inference, in either rendering.
#
# Trust mechanics, code-owned end to end:
#   * the model cites ONLY keys from an offered source map; the manifest,
#     provenance tiers, and the source table are COMPUTED here, never
#     model-claimed;
#   * a citation outside the manifest = fabrication = HARD REJECT of the
#     brief (both consumers; writer degrades to today's excerpt behavior);
#   * quotes must be verbatim substrings of retrieved material;
#   * own-inference effects ("mechanism-inference" or any unlisted basis)
#     are DROPPED with disclosure — the enumerated-repair discipline: the
#     artifact never carries own-voice inference, the brief survives;
#   * reader-facing copy says "cited", never "verified" (Sten's law).
#
# Spend: ANALYSIS_MODEL behind the one-constant seam (fallback rung:
# gpt-4o-mini — one diff, documented, QA-pinned like RANK/WRITER_MODEL).
# Ladder under the $0.25 cap, cheapest first: Sonar background is skipped
# before synthesis; remaining stories' briefs are skipped before anything
# touches the briefing itself; routine derating raises an escalation flag
# in the run log, never absorbed silently.

import sqlite3
from datetime import datetime, timezone

ANALYSIS_MODEL = "gpt-4o"          # strongest available on the held key
                                   # (engineering 2026-07-06 §4); fallback
                                   # rung: gpt-4o-mini (one-diff revert)
ANALYSIS_USD_IN_PER_MTOK = 2.50
ANALYSIS_USD_OUT_PER_MTOK = 10.00
ANALYSIS_MAX_TOKENS = 1400
ANALYSIS_TIMEOUT_S = 90
SONAR_EST_USD = 0.012              # measured spike ~$0.007 + headroom
WORD_BUDGETS = {"full": 700, "medium": 400}
ALLOWED_EFFECT_BASES = ("attributed", "mechanical", "historical-pattern")
BRIEF_SECTIONS = ("pinned_facts", "ledger", "mechanism", "effects",
                  "arc", "unknowns", "watch")
QUOTE_MIN_CHARS = 12
ABSTRACT_MECHANISM_RE = re.compile(
    r"\b(tensions|dynamics|landscape|geopolitical situation)\b", re.I)


class BriefRejected(ValueError):
    """Hard-reject class: the brief is discarded for BOTH consumers."""


@dataclass
class StoryAnalysis:
    slot: int
    tier: str
    outcome: str            # ok | rejected | skipped-budget | skipped-thin |
                            # demoted-quick | failed
    detail: str = ""
    cost_usd: float = 0.0
    fetch_ok: int = 0
    fetch_attempted: int = 0
    sonar_status: str = "skipped"
    warnings: List[str] = field(default_factory=list)
    brief: Optional[Dict] = None


# ---------------------------------------------------------------------------
# Source map — the closed citation vocabulary, code-owned
# ---------------------------------------------------------------------------

def build_source_map(fetch_records: List[FetchRecord],
                     cluster_items: List[Dict],
                     sonar_results: List[Dict],
                     prior_briefings: List[Dict]) -> Dict[str, Dict]:
    """key -> {kind, outlet, title, url, retrieved_at, text}. Keys: S# full
    text fetched this run; C# cluster item (title + feed excerpt); R# Sonar
    result; P# prior briefing. This dict IS the retrieval manifest."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    sources: Dict[str, Dict] = {}
    fetched_urls = set()
    n = 1
    for r in fetch_records:
        if r.outcome == OK and r.text:
            sources[f"S{n}"] = {"kind": "cluster-full-text", "outlet": r.source_name,
                                "title": r.title or "(untitled)", "url": r.url,
                                "retrieved_at": now, "text": r.text}
            fetched_urls.add(r.url)
            n += 1
    n = 1
    for it in cluster_items:
        if it.get("url") in fetched_urls:
            continue  # full text supersedes its own excerpt
        sources[f"C{n}"] = {"kind": "cluster-excerpt", "outlet": it.get("outlet", ""),
                            "title": it.get("title", ""), "url": it.get("url", ""),
                            "retrieved_at": it.get("fetched_at", ""),
                            "text": it.get("raw_excerpt") or ""}
        n += 1
    cluster_urls = fetched_urls | {(it.get("url") or "").strip()
                                    for it in cluster_items}
    n = 1
    for res in sonar_results:
        url = (res.get("url") or "").strip()
        if not url:
            continue
        if url in cluster_urls:
            # BUG12-adjacent gap (QA-frozen, dispatch-ordered): the same
            # article reachable as S# and R# lets one source wear two keys
            # — and waste material budget. The cluster key wins.
            continue
        sources[f"R{n}"] = {"kind": "retrieved", "outlet": _outlet_of(url),
                            "title": res.get("title", ""), "url": url,
                            "retrieved_at": now,
                            "text": res.get("snippet") or res.get("title") or ""}
        n += 1
    n = 1
    for pb in prior_briefings:
        sources[f"P{n}"] = {"kind": "prior-briefing", "outlet": "NewsLens (prior edition)",
                            "title": f"briefing {pb.get('date')}", "url": "",
                            "retrieved_at": pb.get("date", ""),
                            "text": pb.get("text") or ""}
        n += 1
    return sources


def _outlet_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def render_source_map(sources: Dict[str, Dict]) -> str:
    lines = []
    for key in sorted(sources, key=_key_sort):
        s = sources[key]
        lines.append(f"[{key}] {s['outlet']} — {s['title']} ({s['kind']})")
    return "\n".join(lines) or "(none)"


def render_material(sources: Dict[str, Dict], budget_chars: int = 24_000) -> str:
    """Full texts first (the whole point), then excerpts/results, byte-capped
    so a long article can't blow the context.

    P-RESERVATION (M2 gate residual 3): prior-briefing material gets a
    budget slice RESERVED before the S/R/C spend — on a many-source day the
    old shared budget exhausted before P rendered, the model could not cite
    P-keys it never saw, and the arc-integrity lint then dropped the arc
    with a misattributing disclosure. The reservation is budget, not
    position: assembly order stays S, R, C, P."""
    def _entry(key: str, share_cap: int) -> Optional[str]:
        s = sources[key]
        text = (s.get("text") or "").strip()
        if not text:
            return None
        chunk = text[:min(len(text), share_cap)]
        return f"--- [{key}] {s['outlet']} — {s['title']} ---\n{chunk}"

    order = sorted(sources, key=lambda k: ({"S": 0, "R": 1, "C": 2, "P": 3}
                                           .get(k[0], 9), _key_sort(k)))
    p_keys = [k for k in order if k[0] == "P" and (sources[k].get("text") or "").strip()]
    p_total = sum(len((sources[k].get("text") or "").strip()) for k in p_keys)
    reserve = min(p_total, budget_chars // 6)

    p_parts: List[str] = []
    p_used = 0
    for key in p_keys:
        share = max(600, reserve // max(1, len(p_keys)))
        entry = _entry(key, share)
        if entry is None:
            continue
        if p_used + len(entry) > reserve and p_parts:
            break
        p_parts.append(entry)
        p_used += len(entry)

    remainder = budget_chars - p_used
    parts: List[str] = []
    used = 0
    src_keys = [k for k in order if k[0] != "P"]
    for key in src_keys:
        text = (sources[key].get("text") or "").strip()
        if not text:
            continue
        # BUG15 (header-room trim, QA's option a): the old break-on-overflow
        # starved real articles — a single long source rendered an EMPTY
        # (or P-only) material block, and two-source days dropped the
        # second outlet with half the budget unused. Because the SCR gate
        # has already passed by here, an invisible article's REAL keys
        # remained citable: fake receipts with code-supplied keys. Now each
        # entry is trimmed to the room that actually remains (minus its own
        # header) and admitted whenever its 1200-char floor share fits —
        # the material block is never empty of article text while a fetched
        # article exists.
        s = sources[key]
        header = f"--- [{key}] {s['outlet']} — {s['title']} ---\n"
        share = min(len(text), max(1200, remainder // max(1, len(src_keys))))
        room = remainder - used - len(header)
        if room < min(1200, len(text)):
            continue  # no room for even the floor — skip, never a sliver
        chunk_len = min(share, room)
        parts.append(header + text[:chunk_len])
        used += len(header) + chunk_len
    return "\n\n".join(parts + p_parts)


def _key_sort(k: str):
    # kind priority (full text first — it's the point), then number
    return ({"S": 0, "C": 1, "R": 2, "P": 3}.get(k[0], 9),
            int(k[1:]) if k[1:].isdigit() else 0)


# ---------------------------------------------------------------------------
# Validation — deterministic, code-owned (the receipts machinery)
# ---------------------------------------------------------------------------

def _norm_ws(s: str) -> str:
    return _WS_RE.sub(" ", s or "").strip().lower()


# BUG11: chat models emit curly marks inside JSON to dodge escaping, and
# real article HTML carries curly apostrophes. Symmetric glyph
# normalization (both the candidate quote AND the corpus) can only repair
# glyph-variant matches, never manufacture one; curly-pair DETECTION can
# only catch more fabrications. Direction-safe by construction.
_GLYPHS = {"\u201c": '"', "\u201d": '"', "\u201e": '"',
           "\u2018": "'", "\u2019": "'", "\u201a": "'",
           "\u2013": "-", "\u2014": "-"}


def _norm_glyphs(s: str) -> str:
    for k, v in _GLYPHS.items():
        s = s.replace(k, v)
    return s


_QUOTE_RE = re.compile(
    r'["\u201c]([^"\u201c\u201d]{%d,})["\u201d]' % QUOTE_MIN_CHARS)
_INLINE_KEY_RE = re.compile(r"\[([SCRP]\d+)\]")


def _cites_of(entry: Dict) -> List[str]:
    out = []
    for c in entry.get("cites") or []:
        if isinstance(c, str):
            out.append(c.strip().strip("[]"))
    return out


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"))}
_MONTH_DAY_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{1,2})\b", re.I)


def _same_referent_dates(a_val: str, b_val: str, briefing_date: str) -> bool:
    """Editor F2 (fix-loop item 7): 'July 8' vs 'Wednesday' when July 8 IS
    that Wednesday is the same referent, not a discrepancy. Deterministic:
    resolve month-day mentions against the briefing year and compare the
    weekday named on the other side (within ±10 days of the edition — the
    news window where a bare weekday is meaningful)."""
    try:
        base = datetime.strptime(briefing_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return False

    def dates_in(text):
        out = []
        for m in _MONTH_DAY_RE.finditer(text or ""):
            month, day = _MONTHS[m.group(1).lower()], int(m.group(2))
            for year in (base.year, base.year + (1 if month < base.month else 0)):
                try:
                    d = datetime(year, month, day)
                except ValueError:
                    continue
                if abs((d - base).days) <= 10:
                    out.append(d)
        return out

    def weekdays_in(text):
        low = (text or "").lower()
        return [i for i, w in enumerate(_WEEKDAYS) if w in low]

    a_dates, b_dates = dates_in(a_val), dates_in(b_val)
    a_wd, b_wd = weekdays_in(a_val), weekdays_in(b_val)
    for d in a_dates:
        if d.weekday() in b_wd:
            return True
    for d in b_dates:
        if d.weekday() in a_wd:
            return True
    return any(da.date() == db.date() for da in a_dates for db in b_dates)


def compute_provenance(cites: List[str], sources: Dict[str, Dict]) -> str:
    """CODE-computed provenance tier (contract §5.1.2) — never model-claimed."""
    cluster_outlets = {sources[c]["outlet"] for c in cites
                       if c in sources and c[0] in "SC"}
    retrieved = [c for c in cites if c in sources and c[0] == "R"]
    if len(cluster_outlets) >= 2:
        return f"cluster-corroborated ({len(cluster_outlets)} outlets)"
    if len(cluster_outlets) == 1:
        return "cluster-single"
    if retrieved:
        return f"retrieved-single ({sources[retrieved[0]]['outlet']})"
    # Rook's loop mitigation (NL-63, engineering council 2026-07-10): a claim
    # cited ONLY to prior-briefing (P) keys is OUR OWN prior coverage — label
    # it honestly so the self-reference loop stays visible, never laundered as
    # external "stable-background". P still earns ZERO corroboration (it is not
    # in cluster_outlets/retrieved above); this only fixes the display class.
    if any(c in sources and c[0] == "P" for c in cites):
        return "prior-coverage"
    return "stable-background"


def _require_str(value, where: str) -> str:
    """BUG10: the model author is an adversary; every text field is checked
    before regex/join. Numbers and other scalars reject naming the section
    — never an AttributeError/TypeError escaping a paid validation."""
    if not isinstance(value, str):
        raise BriefRejected(
            f"{where} is not text (got {type(value).__name__}) — malformed "
            "model output")
    return value


# --- NL-12: pinned-fact dedupe + chronological ordering (validator-grade) ----
# Principal amendment 2026-07-09: "Facts must be chronological and deduplicated"
# (evidence: a 07-06 OPEC brief rendered two identical pinned facts; the
# validator carried no near-dup check). This is a VALIDATOR obligation, not a
# render-time transform — the renderer stays dumb glue and the archive's stored
# rows keep their persisted order (validators run at generation time only).

# Collapse threshold: near-identical only. Normalized-exact always collapses;
# above _PIN_DUP_JACCARD the two facts are treated as the same fact. Kept high
# deliberately — distinct-but-parallel facts ("Outlet one/two/three reports…",
# token-Jaccard ~0.71) must survive; a false collapse silently deletes a
# checkable claim, the exact failure mode this guard exists to prevent.
_PIN_DUP_JACCARD = 0.9
# Set-Jaccard is word-order-blind: "Iran sanctions US officials…" and "US
# sanctions Iran officials…" share an IDENTICAL token set (1.0) yet are opposite
# claims — over-merge would delete one and re-attach its cites to the survivor.
# So collapse also requires an order-sensitive agreement: token-BIGRAM Jaccard
# >= this gate, ALONGSIDE the set gate above. A permutation shares few adjacent
# pairs (~0.4) and survives; a true near-duplicate (one differing mid-token)
# still shares nearly all bigrams (>=0.8) and still collapses.
_PIN_DUP_BIGRAM_JACCARD = 0.8
_ABS_DATE_RES = (
    # YYYY-MM-DD
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
     lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
    # Month D, YYYY  /  Month D YYYY
    (re.compile(r"\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b",
                re.I),
     lambda m: (int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))),
    # D Month YYYY
    (re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\.?\s+(\d{4})\b",
                re.I),
     lambda m: (int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))),
    # Month YYYY  (day unknown -> 0, sorts before dated days that month)
    (re.compile(r"\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{4})\b", re.I),
     lambda m: (int(m.group(2)), _MONTHS[m.group(1).lower()], 0)),
)


def _norm_fact(text: str) -> str:
    return _norm_ws(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()))


def _bigrams(tokens: List[str]) -> set:
    """Adjacent token pairs — the order-sensitive signal set-Jaccard is blind to.
    A word-order permutation (identical token SET) shares few bigrams; a true
    near-duplicate (one differing mid-token) shares nearly all of them."""
    return set(zip(tokens, tokens[1:]))


def _fact_date_key(text: str):
    """First ABSOLUTE date a fact carries, as a sortable (y, m, d), or None.
    Bare weekdays and lone years are deliberately NOT dates — too ambiguous to
    reorder on ('Tuesday' has no chronology; '2026' no month)."""
    for rx, build in _ABS_DATE_RES:
        m = rx.search(text or "")
        if m:
            return build(m)
    return None


def _dedup_and_order_pinned(pinned: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """(1) Collapse near-duplicate pinned facts, merging their cites so no
    provenance is lost, disclosing each collapse as a warning. (2) Order facts
    that carry absolute dates chronologically, leaving undated facts in place
    (dated facts fill the slots dated facts already held — stable for the rest).
    Deterministic; no string similarity beyond the high-threshold dup check."""
    warnings: List[str] = []
    kept: List[Dict] = []
    for p in pinned:
        norm = _norm_fact(p.get("fact", ""))
        tlist = norm.split()
        toks = set(tlist)
        bg = _bigrams(tlist)
        hit = None
        for k in kept:
            if norm and norm == k["_norm"]:
                hit = k
                break
            ktoks = k["_toks"]
            if toks and ktoks:
                jac = len(toks & ktoks) / len(toks | ktoks)
                if jac < _PIN_DUP_JACCARD:
                    continue
                # Set gate cleared — now the order-sensitive gate. Bigram Jaccard
                # separates a genuine near-duplicate (one differing mid-token,
                # ~0.8+) from a word-order permutation (identical set, few shared
                # adjacent pairs, ~0.4): only the former collapses. Empty-bigram
                # facts (0-1 tokens) score 0.0 and fall through to survive —
                # under-merge is the safe direction.
                kbg = k["_bg"]
                bjac = (len(bg & kbg) / len(bg | kbg)) if (bg and kbg) else 0.0
                if bjac >= _PIN_DUP_BIGRAM_JACCARD:
                    hit = k
                    break
        if hit is not None:
            for c in _cites_of(p):
                if c not in hit["cites"]:
                    hit["cites"].append(c)
            warnings.append(
                "pinned fact collapsed as near-duplicate (cites merged): "
                f"{(p.get('fact', '') or '')[:70]!r}")
        else:
            kept.append({"fact": p.get("fact", ""), "cites": _cites_of(p),
                         "_norm": norm, "_toks": toks, "_bg": bg})

    dated = [(i, _fact_date_key(k["fact"])) for i, k in enumerate(kept)]
    dated = [(i, d) for i, d in dated if d is not None]
    if len(dated) >= 2:
        slots = [i for i, _ in dated]                       # original positions
        chrono = sorted(dated, key=lambda t: t[1])          # dated, oldest first
        ordered = list(kept)
        for slot, (orig_i, _d) in zip(slots, chrono):
            ordered[slot] = kept[orig_i]
        kept = ordered

    return ([{"fact": k["fact"], "cites": k["cites"]} for k in kept], warnings)


def validate_brief(raw: Dict, sources: Dict[str, Dict], tier: str,
                   corpus: str, briefing_date: str = "") -> Tuple[Dict, List[str]]:
    """Returns (clean brief with computed furniture, warnings). Raises
    BriefRejected for the hard classes: missing sections, fabricated
    citation keys, quotes that aren't verbatim substrings of retrieved
    material, an uncitable pinned-facts section."""
    if not isinstance(raw, dict):
        raise BriefRejected("brief is not a JSON object")
    missing = [s for s in BRIEF_SECTIONS if s not in raw]
    if missing:
        raise BriefRejected(f"missing mandatory section(s): {', '.join(missing)}")
    warnings: List[str] = []
    corpus_norm = _norm_ws(_norm_glyphs(corpus))

    def check_cites(cites: List[str], where: str) -> None:
        for c in cites:
            if c not in sources:
                raise BriefRejected(
                    f"fabricated citation {c!r} in {where} — not in the "
                    "retrieval manifest")

    def check_quotes(text: str, where: str) -> None:
        for q in _QUOTE_RE.findall(text or ""):
            if _norm_ws(_norm_glyphs(q)) not in corpus_norm:
                raise BriefRejected(
                    f"quote in {where} is not a verbatim substring of "
                    f"retrieved material: \"{q[:60]}...\"")

    # pinned facts: 3-6, each cited (hard: at least 1, each cited)
    pinned = raw.get("pinned_facts") or []
    if not isinstance(pinned, list) or not pinned:
        raise BriefRejected("pinned_facts empty — nothing verified to build on")
    for i, p in enumerate(pinned):
        if not isinstance(p, dict):
            raise BriefRejected(
                f"pinned fact {i+1} malformed (not an object) — malformed "
                "model output")
        cites = _cites_of(p)
        if not cites:
            raise BriefRejected(f"pinned fact {i+1} carries no citation")
        check_cites(cites, f"pinned fact {i+1}")
        check_quotes(_require_str(p.get("fact", ""), f"pinned fact {i+1}"),
                     f"pinned fact {i+1}")
    if not (3 <= len(pinned) <= 6):
        warnings.append(f"pinned_facts count {len(pinned)} outside the 3-6 band")

    # ledger: every entry cited (or a two-sided discrepancy); provenance COMPUTED
    ledger_out = []
    for i, e in enumerate(raw.get("ledger") or []):
        if not isinstance(e, dict):
            continue
        if e.get("discrepancy"):
            side_cites = {}
            for side in ("a", "b"):
                sd = e.get(side)
                if not isinstance(sd, dict):
                    raise BriefRejected(
                        f"discrepancy entry {i+1} side {side!r} malformed "
                        "(not an object) — malformed model output")
                sc = _cites_of(sd)
                if not sc:
                    raise BriefRejected(
                        f"discrepancy entry {i+1} side {side!r} uncited — "
                        "both values need both sources")
                check_cites(sc, f"discrepancy {i+1}.{side}")
                side_cites[side] = sc
            # BUG12: identical cite sets = one source wearing two hats —
            # there is no second source, so no cross-source discrepancy
            # (ADR-0012's one-sided class).
            if set(side_cites["a"]) == set(side_cites["b"]):
                raise BriefRejected(
                    f"discrepancy entry {i+1} cites the identical source on "
                    "both sides — one-sided, not a cross-source discrepancy")
            a_val = str((e.get("a") or {}).get("value", ""))
            b_val = str((e.get("b") or {}).get("value", ""))
            # Gate residual 2: the trust promise holds artifact-wide —
            # discrepancy side values carry quoted material too.
            check_quotes(a_val, f"discrepancy {i+1}.a value")
            check_quotes(b_val, f"discrepancy {i+1}.b value")
            # Editor F2/G2 (fix item 7): same-referent dates are not a
            # discrepancy — drop the entry, disclosed (repair class).
            if briefing_date and _same_referent_dates(a_val, b_val, briefing_date):
                warnings.append(
                    f"false discrepancy dropped: {a_val!r} and {b_val!r} "
                    "resolve to the same day (Editor F2 same-referent rule)")
                continue
            # D1 (M3 gate): `note` typed at the boundary like every field
            # (BUG-10 law) — non-str note dropped as a disclosed repair
            # (garnish, not substance); a str note is quote-checked because
            # M3's Unresolved section makes it reader-visible (Gate residual 2
            # applies artifact-wide). Side values persist str()-coerced so no
            # non-str scalar can launder a repr into the rendered register.
            raw_note = e.get("note", "")
            note = raw_note if isinstance(raw_note, str) else ""
            if note is not raw_note:
                warnings.append(f"discrepancy {i+1} note dropped: not text "
                                f"(got {type(raw_note).__name__})")
            check_quotes(note, f"discrepancy {i+1} note")
            ledger_out.append({"discrepancy": True,
                               "a": {**e["a"], "value": a_val},
                               "b": {**e["b"], "value": b_val},
                               "note": note})
            continue
        cites = _cites_of(e)
        check_cites(cites, f"ledger entry {i+1}")
        check_quotes(_require_str(e.get("claim", ""), f"ledger entry {i+1}"),
                     f"ledger entry {i+1}")
        prov = compute_provenance(cites, sources)
        if prov == "stable-background":
            warnings.append(
                f"ledger entry {i+1} uncited — carried as stable-background "
                "(explain-lane class); it renders with that label")
        ledger_out.append({"claim": e.get("claim", ""), "cites": cites,
                           "provenance": prov})

    if not ledger_out:
        # Gate residual 1 (instrumentation, not a gate): the ledger is the
        # organ's distinguishing output under the borrowed-inference ruling
        # — zero attributed takes must be countable by diagnose; usefulness
        # RULINGS wait for the week (Editor's lane).
        warnings.append("ledger empty — no attributed takes (facts + "
                        "mechanism only); week-1 usefulness read material")

    # mechanism: present-tense prose; inline keys validated; tripwire warn
    mechanism = str(raw.get("mechanism") or "").strip()
    if not mechanism:
        raise BriefRejected("mechanism section empty")
    check_cites(_INLINE_KEY_RE.findall(mechanism), "mechanism")
    check_quotes(mechanism, "mechanism")
    if ABSTRACT_MECHANISM_RE.search(mechanism):
        warnings.append(
            "mechanism leans on a banned abstract noun (contract §5.1.3) — "
            "flagged for the Editor's review")

    # effects: borrowed-inference rule, structural — drop own-voice bases.
    # Editor F4 (fix item 9): modal/hedged text under basis=mechanical is
    # "an own-voice forecast in a trench coat" — same drop path. Editor G3
    # (fix item 8): a dated take older than the edition re-bases to
    # historical-pattern with its date shown, never a take on today.
    effects_out = []
    dropped = 0
    modal_re = re.compile(r"\b(may|might|could|likely)\b", re.I)
    base_dt = None
    if briefing_date:
        try:
            base_dt = datetime.strptime(briefing_date, "%Y-%m-%d")
        except ValueError:
            base_dt = None
    for e in raw.get("effects") or []:
        if not isinstance(e, dict):
            continue
        basis = str(e.get("basis") or "").strip()
        if basis not in ALLOWED_EFFECT_BASES:
            dropped += 1
            continue
        cites = _cites_of(e)
        if not cites:
            dropped += 1
            continue  # an effect without receipts is a take — same class
        effect_text = e.get("effect", "")
        if not isinstance(effect_text, str):
            dropped += 1
            continue
        if basis == "mechanical" and modal_re.search(effect_text):
            dropped += 1
            warnings.append(
                "basis lint (Editor F4): hedged text under basis=mechanical "
                f"dropped — {effect_text[:60]!r} is an inference from a "
                "calendar fact, held by no named writer")
            continue
        check_cites(cites, "effects")
        check_quotes(effect_text, "effects")
        holder = e.get("holder", "") if isinstance(e.get("holder", ""), str) else ""
        take_date = str(e.get("take_date") or "")[:10]
        if basis == "attributed" and base_dt is not None and take_date:
            try:
                t_dt = datetime.strptime(take_date, "%Y-%m-%d")
            except ValueError:
                t_dt = None
            if t_dt is not None and (base_dt - t_dt).days > 7:
                basis = "historical-pattern"
                holder = f"{holder} ({take_date})" if holder else take_date
                warnings.append(
                    "recency re-basis (Editor G3): attributed take dated "
                    f"{take_date} predates the edition — rendered as "
                    "historical-pattern with its date shown")
        out_e = {"effect": effect_text, "basis": basis, "holder": holder,
                 "cites": cites}
        if take_date:
            out_e["take_date"] = take_date
        effects_out.append(out_e)
    if dropped:
        warnings.append(
            f"borrowed-inference enforcement: dropped {dropped} effect(s) "
            "with own-voice or uncited basis (principal ruling 2026-07-06)")

    # arc: optional; cites validated when present. Editor item 10: the
    # delta verdict is consumed MECHANICALLY downstream, so an arc that
    # cites no prior-briefing key while one exists is dropped (disclosed)
    # — a wrong delta propagates by design; no delta degrades safely.
    arc = raw.get("arc")
    if isinstance(arc, dict):
        arc_cites = _cites_of(arc)
        check_cites(arc_cites, "arc")
        # NL-63 (memory architecture, ruling A): the arc now feeds the delta
        # LEDGER, anchored to EXTERNAL evidence (Rook's loop guard) — not the
        # writer's mechanical P-callback. The old Editor-item-10 rule ("drop
        # unless it cites P") is retired: an arc anchored to today's S/C/R
        # sources is the DESIRED shape. Only a fully-uncited arc is dropped.
        # The two-clause SIGNIFICANCE fields (what_happened + significance) are
        # new claim-carriers on the trust surface, so their quotes are checked
        # like every other rendered field; `what_changed` stays as the legacy
        # single-clause fallback (unquoted, as before).
        for fld in ("what_happened", "significance"):
            v = arc.get(fld)
            if isinstance(v, str) and v:
                check_quotes(v, f"arc.{fld}")
        if not arc_cites:
            warnings.append(
                "arc dropped: carries no citation — a delta must trace to its "
                "evidence (NL-63 ledger contract)")
            arc = None
        elif arc.get("delta") not in ("advances", "reverses", "merely-matches"):
            warnings.append(f"arc delta {arc.get('delta')!r} outside the verdict "
                            "vocabulary — carried, flagged")
    else:
        arc = None

    # unknowns: first-class; specific-shape enforced softly, banned class hard
    unknowns = [u for u in (raw.get("unknowns") or []) if isinstance(u, dict)]
    for i, u in enumerate(unknowns):
        for f_name in ("question", "why_material", "would_resolve"):
            _require_str(u.get(f_name, ""), f"unknown {i+1} {f_name}")
        for f_name in ("question", "why_material", "would_resolve"):
            check_quotes(u.get(f_name, ""), f"unknown {i+1} {f_name}")
        q = _norm_ws(u.get("question", ""))
        if "unclear how this will unfold" in q or q in ("", "unknown"):
            raise BriefRejected(
                "generic unknown (the banned §5.1.6 class) — zero-information "
                "sentence in epistemic costume")
    if not (1 <= len(unknowns) <= 3):
        warnings.append(f"unknowns count {len(unknowns)} outside the 1-3 band")

    watch = [w for w in (raw.get("watch") or []) if isinstance(w, dict)]
    for i, w in enumerate(watch):
        _require_str(w.get("observable", ""), f"watch {i+1} observable")
        check_quotes(w.get("observable", ""), f"watch {i+1} observable")
        if isinstance(w.get("settles"), str):
            check_quotes(w["settles"], f"watch {i+1} settles")
    if not (2 <= len(watch) <= 4):
        warnings.append(f"watch count {len(watch)} outside the 2-4 band")

    # word budget (editorial ceiling — warn, never reject)
    prose = " ".join(
        [p.get("fact", "") for p in pinned]
        + [e.get("claim", "") for e in ledger_out if not e.get("discrepancy")]
        + [mechanism]
        + [e["effect"] for e in effects_out]
        + [u.get("question", "") + " " + u.get("why_material", "")
           + " " + u.get("would_resolve", "") for u in unknowns]
        + [w.get("observable", "") for w in watch])
    words = len(prose.split())
    budget = WORD_BUDGETS.get(tier, 400)
    if words > budget:
        warnings.append(f"brief runs {words} words against the {budget}-word "
                        f"{tier} ceiling — Editor's eye at day-14")

    # NL-12: dedupe near-identical pinned facts (cites merged) + order the
    # dated ones chronologically — validator-grade, disclosed as warnings.
    pinned_clean, pin_warnings = _dedup_and_order_pinned(pinned)
    warnings.extend(pin_warnings)

    # source table: CODE-BUILT from cited keys only
    used: List[str] = []
    def collect(cites):
        for c in cites:
            if c not in used:
                used.append(c)
    for p in pinned_clean:
        collect(p["cites"])
    for e in ledger_out:
        if e.get("discrepancy"):
            collect(_cites_of(e["a"])); collect(_cites_of(e["b"]))
        else:
            collect(e["cites"])
    collect(_INLINE_KEY_RE.findall(mechanism))
    for e in effects_out:
        collect(e["cites"])
    if arc:
        collect(_cites_of(arc))
    # Gate residual 2 tail: notes_for_writer flows into writer material
    # where the fact-subset chain treats it as given — it cannot stay
    # quote-exempt.
    notes = str(raw.get("notes_for_writer") or "")[:300]
    check_quotes(notes, "notes_for_writer")

    source_table = [
        {"key": k, "outlet": sources[k]["outlet"], "title": sources[k]["title"],
         "url": sources[k]["url"], "retrieved_at": sources[k]["retrieved_at"],
         "kind": sources[k]["kind"]}
        for k in sorted(used, key=_key_sort)]

    clean = {
        "pinned_facts": pinned_clean,
        "ledger": ledger_out,
        "mechanism": mechanism,
        "effects": effects_out,
        "arc": arc,
        "unknowns": unknowns[:3],
        "watch": watch[:4],
        "sources": source_table,
        "notes_for_writer": notes,
    }
    return clean, warnings


# ---------------------------------------------------------------------------
# The call, the loop, the ladder
# ---------------------------------------------------------------------------

def _analysis_chat(key: str, prompt: str) -> Dict:
    """One-retry synthesis call on the ANALYSIS_MODEL seam (mirrors
    generate._chat's shape; separate so the writer path stays untouched
    until M3)."""
    body = {
        "model": ANALYSIS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": ANALYSIS_MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "User-Agent": ANALYSIS_UA})
    with urllib.request.urlopen(req, timeout=ANALYSIS_TIMEOUT_S) as resp:
        return json.load(resp)


def call_analysis_model(key: str, prompt: str) -> Tuple[Dict, float]:
    """(parsed JSON, cost USD). One retry on network/parse failure, then
    raises — the caller's ladder turns that into a disclosed no-brief.

    BUG13: the returned cost accumulates EVERY attempt that returned usage
    — attempt 1 that completed HTTP (tokens paid) and then failed
    truncation/parse still spent real money against the $0.25 cap, and the
    log must carry real spend (BUG-6 money-honesty class)."""
    last: Exception = RuntimeError("unreachable")
    total_cost = 0.0
    for attempt in (1, 2):
        try:
            payload = _analysis_chat(key, prompt)
            usage = payload.get("usage") or {}
            total_cost += (
                usage.get("prompt_tokens", 0) / 1e6 * ANALYSIS_USD_IN_PER_MTOK
                + usage.get("completion_tokens", 0) / 1e6 * ANALYSIS_USD_OUT_PER_MTOK)
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError(f"truncated at {ANALYSIS_MAX_TOKENS} tokens")
            return json.loads(choice["message"]["content"]), total_cost
        except Exception as exc:  # noqa: BLE001 — one retry for the whole class
            last = exc
            if attempt == 1:
                time.sleep(1.0)
    raise last


def estimate_synthesis_usd(prompt: str) -> float:
    est_in = len(prompt) / 4
    return (est_in / 1e6 * ANALYSIS_USD_IN_PER_MTOK
            + ANALYSIS_MAX_TOKENS / 1e6 * ANALYSIS_USD_OUT_PER_MTOK)


def _sonar_verify(key: str, story_title: str, claims: List[str]) -> Tuple[List[Dict], float, str]:
    """One Sonar verification call per depth story (discovery's call shape).
    Returns (results, cost, status). Failure degrades, never raises."""
    from . import discovery, paths
    if not key:
        return [], 0.0, "skipped — no PERPLEXITY_API_KEY"
    try:
        template = (paths.PROMPTS_DIR / "analysis_sonar.txt").read_text(encoding="utf-8")
        prompt = template.format(story_title=story_title,
                                 claims="\n".join(f"- {c}" for c in claims[:5]))
    except Exception as exc:
        return [], 0.0, f"failed — sonar prompt did not render ({type(exc).__name__})"
    try:
        payload = discovery.call_sonar(key, prompt)
    except Exception as exc:
        code = getattr(exc, "code", None)
        return [], SONAR_EST_USD, f"failed — {type(exc).__name__}{f' {code}' if code else ''}"
    usage = payload.get("usage") or {}
    tokens = usage.get("total_tokens", 0)
    cost = tokens / 1e6 * discovery.SONAR_USD_PER_MTOK
    results = payload.get("search_results") or []
    return results[:8], cost, f"ok — {len(results[:8])} results"


def _cluster_items_for_slot(con: sqlite3.Connection, slot: Dict,
                            cfg) -> List[Dict]:
    ids = slot.get("item_ids") or []
    if not ids:
        return []
    tier_by_outlet = {s.name: s.tier for s in cfg.sources}
    rows = con.execute(
        f"SELECT outlet, url, title, raw_excerpt, fetched_at FROM source_items"
        f" WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return [{"outlet": r["outlet"], "url": r["url"], "title": r["title"],
             "raw_excerpt": r["raw_excerpt"], "fetched_at": r["fetched_at"],
             "source_name": r["outlet"],
             "tier": tier_by_outlet.get(r["outlet"], "full")} for r in rows]


def persist_brief(con: sqlite3.Connection, date: str, slot: int, tier: str,
                  status: str, brief: Optional[Dict], reject_reason: str,
                  cost: float, header: Dict,
                  sources: Optional[Dict[str, Dict]] = None) -> int:
    """Returns the brief row id. Retrieved material persists alongside
    (analysis_retrieval, fix-loop item 11): hand-traces — including the
    day-14 protocol's — must never depend on re-fetching a page that can
    change or rot. ~15-40KB per brief at one-reader scale."""
    doc = {"header": header, "brief": brief}
    with con:
        cur = con.execute(
            "INSERT INTO analysis_briefs (date, slot, tier, status,"
            " brief_json, reject_reason, model, cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (date, slot, tier, status, json.dumps(doc, ensure_ascii=False),
             reject_reason or None, ANALYSIS_MODEL, round(cost, 6)))
        brief_id = cur.lastrowid
        for key in sorted(sources or {}, key=_key_sort):
            s = sources[key]
            con.execute(
                "INSERT INTO analysis_retrieval (brief_id, key, kind, outlet,"
                " title, url, retrieved_at, text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (brief_id, key, s.get("kind", ""), s.get("outlet", ""),
                 s.get("title", ""), s.get("url", ""),
                 s.get("retrieved_at", ""), s.get("text", "")))
    return brief_id


def analyst_slot3_tier(con: sqlite3.Connection, date: str) -> Optional[str]:
    """The slot-3 tier verdict, derived from PERSISTED rows — the single
    path both the fresh run and --no-refresh use (M3 gate item 2). Newest
    row wins: valid brief = medium; a demoted-quick verdict row = quick;
    a plain rejection or no row = no verdict (the writer's A2 fallback)."""
    row = con.execute(
        "SELECT status, reject_reason FROM analysis_briefs WHERE date = ?"
        " AND slot = 3 ORDER BY id DESC LIMIT 1", (date,)).fetchone()
    if row is None:
        return None
    if row["status"] == "valid":
        return "medium"
    if (row["reject_reason"] or "").startswith("demoted-quick"):
        return "quick"
    return None


def latest_valid_brief(con: sqlite3.Connection, date: str,
                       slot: int) -> Optional[Dict]:
    row = con.execute(
        "SELECT brief_json FROM analysis_briefs WHERE date = ? AND slot = ?"
        " AND status = 'valid' ORDER BY id DESC LIMIT 1", (date, slot)).fetchone()
    return json.loads(row["brief_json"]) if row else None


def analyze_story(con: sqlite3.Connection, date: str, slot_no: int,
                  slot: Dict, tier: str, cfg, openai_key: str,
                  pplx_key: str, remaining_usd: float,
                  memory_lines: List[str],
                  prior: List[Dict],
                  fetch: FetchFn = net.fetch_bytes,
                  chat=None, sonar=None,
                  sleep: Callable[[float], None] = time.sleep) -> StoryAnalysis:
    """One story through the whole organ: fetch -> sonar -> synthesize ->
    validate -> persist. Every failure path is a disclosed outcome; the
    ladder degrades cheapest-first (Sonar before synthesis, synthesis before
    anything downstream)."""
    from . import paths
    sa = StoryAnalysis(slot=slot_no, tier=tier, outcome="failed")
    chat = chat or call_analysis_model
    sonar = sonar or _sonar_verify

    items = _cluster_items_for_slot(con, slot, cfg)
    records = fetch_cluster_articles(items, fetch=fetch, sleep=sleep) if items else []
    sa.fetch_attempted = sum(1 for r in records if r.attempted)
    sa.fetch_ok = sum(1 for r in records if r.outcome == OK)

    # Ladder rung 1 (cheapest first): Sonar goes before synthesis money
    sonar_results: List[Dict] = []
    est_synth_probe = 0.05  # coarse pre-map probe; the real estimate follows
    if remaining_usd - SONAR_EST_USD < est_synth_probe:
        sa.sonar_status = "skipped — budget ladder (Sonar degrades first)"
        sa.warnings.append("derating: Sonar verification skipped under the cap")
    else:
        claims = [slot.get("story_title", "")] + \
                 [it.get("title", "") for it in items[:4]]
        sonar_results, s_cost, sa.sonar_status = sonar(
            pplx_key, slot.get("story_title", ""), claims)
        sa.cost_usd += s_cost
        remaining_usd -= s_cost

    # NL-63 item 3: thread-scoped P-material. When this slot's threads carry a
    # record, P becomes the thread's OWN prior coverage (dated ledger + state),
    # replacing the two generic 4KB narrative dumps — the fix for Content's
    # P1-cite proof. No thread record yet -> the generic `prior` stands (honest
    # cold-start / no-thread story). Zero new LLM spend; it re-allocates the
    # same P material budget.
    from . import memory_core
    slot_prior = memory_core.prior_for_slot(con, date, slot, prior)
    sources = build_source_map(records, items, sonar_results, slot_prior)

    # Slot-3 reconciliation, binding here (M2): the analyst holds the
    # medium-vs-quick call for slot 3 — thin material (which INCLUDES
    # no material) demotes it to quick, so the writer treats it as a
    # quick hit instead of a degraded medium. Checked before the
    # total-failure rule: for this slot, the tier call IS the outcome.
    if slot_no == 3 and tier == "medium" and sa.fetch_ok == 0 \
            and len(sonar_results) < 2:
        sa.outcome = "demoted-quick"
        sa.detail = ("slot-3 medium -> quick by analyst (thin material: no "
                     "full text, <2 retrieved results) — reconciliation "
                     "2026-07-06, confirmed M9-M1")
        # M3 gate item 2: the verdict is a binding contract, not a
        # refresh-path behavior — it persists as a rejected VERDICT row
        # (no brief was made; reject_reason carries the ruling) so
        # --no-refresh re-runs derive the same tier the live path ruled.
        persist_brief(con, date, slot_no, tier, "rejected", None,
                      f"demoted-quick: {sa.detail}", sa.cost_usd,
                      {"slot": slot_no, "tier": tier, "date": date,
                       "verdict": "demoted-quick", "model": ANALYSIS_MODEL},
                      sources=sources)
        return sa

    # Total-failure rule: never a model-memory brief
    if not any(k[0] in "SCR" for k in sources):
        sa.outcome = "skipped-thin"
        sa.detail = ("no retrievable material (fetch + Sonar + excerpts all "
                     "empty) — no brief; model-memory briefs are the cardinal "
                     "breach")
        return sa

    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    degraded = None
    if sa.fetch_ok == 0:
        degraded = ("no full-text extraction succeeded — brief built from "
                    "excerpts/retrieval only")
    # explicit placeholder replacement, NOT str.format: the template shows a
    # literal JSON example whose braces would read as format fields (the
    # discovery BUG-3 class), and the file stays principal-editable without
    # {{escape}} noise.
    prompt = _render_prompt(template, {
        "word_budget": str(WORD_BUDGETS.get(tier, 400)), "tier": tier,
        "date": date, "slot": str(slot_no),
        "story_title": slot.get("story_title", ""),
        "story_summary": slot.get("summary", ""),
        "memory_context": "\n".join(memory_lines)
                          or "(no tracked threads touch this story)",
        "source_map": render_source_map(sources),
        "material": render_material(sources)})

    est = estimate_synthesis_usd(prompt)
    if est > remaining_usd:
        sa.outcome = "skipped-budget"
        sa.detail = (f"synthesis estimate ${est:.3f} exceeds remaining budget "
                     f"${remaining_usd:.3f} — brief skipped, disclosed")
        sa.warnings.append("derating: analysis brief skipped under the cap "
                           "(escalation-flag class)")
        return sa

    try:
        raw, cost = chat(openai_key, prompt)
    except Exception as exc:
        sa.outcome = "failed"
        sa.detail = f"synthesis call failed after one retry ({type(exc).__name__}: {exc})"
        return sa
    sa.cost_usd += cost

    corpus = " ".join((s.get("text") or "") for s in sources.values())
    header = {
        "slot": slot_no, "tier": tier, "date": date,
        "manifest": {k: {"url": sources[k]["url"], "outlet": sources[k]["outlet"],
                         "kind": sources[k]["kind"]} for k in sorted(sources, key=_key_sort)},
        "fetch": {"ok": sa.fetch_ok, "attempted": sa.fetch_attempted},
        "sonar": sa.sonar_status,
        "degraded": degraded,
        "model": ANALYSIS_MODEL,
    }
    try:
        clean, warnings = validate_brief(raw, sources, tier, corpus,
                                         briefing_date=date)
    except BriefRejected as exc:
        sa.outcome = "rejected"
        sa.detail = str(exc)
        persist_brief(con, date, slot_no, tier, "rejected", None, str(exc),
                      sa.cost_usd, header, sources=sources)
        return sa
    except Exception as exc:  # noqa: BLE001 — BUG10 run-level belt: the
        # synthesis was PAID; a validator escape must be a disclosed,
        # logged outcome, never a crash that loses the run's record.
        sa.outcome = "rejected"
        sa.detail = (f"validator error on model output "
                     f"({type(exc).__name__}: {exc}) — treated as malformed")
        persist_brief(con, date, slot_no, tier, "rejected", None, sa.detail,
                      sa.cost_usd, header, sources=sources)
        return sa
    sa.warnings.extend(warnings)
    sa.outcome = "ok"
    sa.detail = (f"{len(clean['ledger'])} ledger entries, "
                 f"{len(clean['sources'])} cited sources")
    sa.brief = clean
    persist_brief(con, date, slot_no, tier, "valid", clean, "", sa.cost_usd,
                  header, sources=sources)
    return sa


def run_analysis(date: Optional[str] = None, con=None, env: Optional[dict] = None,
                 fetch: FetchFn = net.fetch_bytes, chat=None, sonar=None,
                 sleep: Callable[[float], None] = time.sleep,
                 already_spent: float = 0.0,
                 tiers_override: Optional[List[str]] = None) -> Dict:
    """The M2 stage, standalone: depth-tier stories of the date's ranked
    slots -> analysis briefs. M3 wires this between rank and write; the
    contract here (slots in, briefs + report out) is that seam."""
    import os
    from . import config, db, memory as memory_mod, paths, ranking
    config.load_env()  # .env before reading keys (analyze runs standalone)
    src_env = env if env is not None else os.environ
    openai_key = (src_env.get("OPENAI_API_KEY") or "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY not set — the analysis call is an "
                           "LLM step; there is no keyless mode")
    pplx_key = (src_env.get("PERPLEXITY_API_KEY") or "").strip()
    own_con = con is None
    con = con or db.connect()
    try:
        date = date or ranking.local_today()
        row = con.execute("SELECT * FROM briefings WHERE date = ?", (date,)).fetchone()
        if row is None:
            row = con.execute(
                "SELECT * FROM briefings ORDER BY date DESC LIMIT 1").fetchone()
            if row is None:
                raise RuntimeError("no ranked briefing to analyze — run "
                                   "`newslens generate` (or rank) first")
            date = row["date"]
        slots = json.loads(row["story_slots"] or "[]")
        # tiers: the generation log's recorded tiers for the date; positional
        # default when absent (pre-M7 rows)
        tiers = tiers_override[:len(slots)] if tiers_override \
            else _tiers_for(date, len(slots))
        cfg = config.load_sources()
        cap = config.budget_cap_usd_per_run(src_env)
        # M3: when generate hosts this stage, its prior spend rides in so
        # ONE cap governs the whole run (the ladder still degrades analysis
        # before the writer — analysis runs first and leaves headroom).
        spent = float(already_spent)
        memory_lines = memory_mod.active_context(con)
        prior = _prior_briefing_material(con, date)
        report = {"ts": datetime.now(timezone.utc).isoformat(),
                  "stage": "analysis", "date": date, "status": "ok",
                  "model": ANALYSIS_MODEL, "per_story": [], "total_usd": 0.0,
                  "derating": False, "warnings": []}
        for i, (slot, tier) in enumerate(zip(slots, tiers), start=1):
            if tier not in ("full", "medium"):
                continue
            sa = analyze_story(con, date, i, slot, tier, cfg, openai_key,
                               pplx_key, cap - spent, memory_lines, prior,
                               fetch=fetch, chat=chat, sonar=sonar, sleep=sleep)
            spent += sa.cost_usd
            report["per_story"].append({
                "slot": sa.slot, "tier": sa.tier, "outcome": sa.outcome,
                "detail": sa.detail, "cost_usd": round(sa.cost_usd, 6),
                "fetch_ok": sa.fetch_ok, "fetch_attempted": sa.fetch_attempted,
                "sonar": sa.sonar_status})
            report["warnings"].extend(sa.warnings)
            if any(w.startswith("derating:") for w in sa.warnings):
                report["derating"] = True
        report["total_usd"] = round(spent - already_spent, 6)
        if not report["per_story"]:
            report["status"] = "no-depth-stories"
        else:
            # Passing fix (disclosed in the fix-loop report): 'ok' when every
            # story failed was a lie of summary. ok / partial / failed now
            # reflect the outcomes ('demoted-quick' counts as a decision
            # made, not a failure).
            good = {"ok", "demoted-quick"}
            n_good = sum(1 for s in report["per_story"] if s["outcome"] in good)
            report["status"] = ("ok" if n_good == len(report["per_story"])
                                else "partial" if n_good else "failed")
        _append_log(report)
        return report
    finally:
        if own_con:
            con.close()


def _render_prompt(template: str, mapping: Dict[str, str]) -> str:
    for k, v in mapping.items():
        template = template.replace("{" + k + "}", v)
    return template


def _tiers_for(date: str, n: int) -> List[str]:
    from . import paths
    log = paths.DATA_DIR / "generation_log.jsonl"
    tiers: Optional[List[str]] = None
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("date") == date and not e.get("sample") and e.get("tiers"):
                tiers = e["tiers"]
    if tiers and len(tiers) >= n:
        return tiers[:n]
    return ["full" if i == 0 else "medium" if i <= 2 else "quick"
            for i in range(n)]


def _prior_briefing_material(con: sqlite3.Connection, date: str,
                             cap: int = 2) -> List[Dict]:
    rows = con.execute(
        "SELECT date, narrative_text FROM briefings WHERE date < ?"
        " ORDER BY date DESC LIMIT ?", (date, cap)).fetchall()
    return [{"date": r["date"], "text": (r["narrative_text"] or "")[:4000]}
            for r in rows]


def _append_log(entry: Dict) -> None:
    from . import paths
    log = paths.DATA_DIR / "generation_log.jsonl"
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Writer-facing rendering (deterministic; the reader view is M3's surface)
# ---------------------------------------------------------------------------

def render_writer_view(brief: Dict) -> str:
    """One artifact, two renderings (§5.3): this is the WRITER's — all
    sections, degradation directives included. Deterministic template,
    never a second LLM pass."""
    parts = ["ANALYSIS BRIEF (cited material — the report lane's ledger)"]
    parts.append("\nPINNED FACTS:")
    for p in brief.get("pinned_facts", []):
        parts.append(f"  - {p['fact']} [{', '.join(p['cites'])}]")
    parts.append("\nLEDGER (claim [cites] — provenance):")
    for e in brief.get("ledger", []):
        if e.get("discrepancy"):
            a, b = e["a"], e["b"]
            parts.append(f"  - DISCREPANCY: {a.get('value')} "
                         f"[{', '.join(_cites_of(a))}] vs {b.get('value')} "
                         f"[{', '.join(_cites_of(b))}] — {e.get('note', '')}")
        else:
            parts.append(f"  - {e['claim']} [{', '.join(e['cites'])}] — "
                         f"{e['provenance']}")
    parts.append(f"\nMECHANISM (present tense): {brief.get('mechanism', '')}")
    if brief.get("effects"):
        parts.append("\nEFFECTS (source-attributed takes only — copy with basis, "
                     "never generate your own):")
        for e in brief["effects"]:
            parts.append(f"  - [{e['basis']}: {e.get('holder', '')}] {e['effect']} "
                         f"[{', '.join(e['cites'])}]")
    if brief.get("arc"):
        a = brief["arc"]
        # NL-63 two-clause shape (what_happened + significance), with the legacy
        # single-clause what_changed as the fallback.
        arc_body = (f"{a.get('what_happened', '')} — {a.get('significance', '')}"
                    if a.get("what_happened") else a.get("what_changed", ""))
        parts.append(f"\nARC: {a.get('delta')} — {arc_body} "
                     f"[{', '.join(_cites_of(a))}]")
    parts.append("\nUNKNOWNS (first-class):")
    for u in brief.get("unknowns", []):
        parts.append(f"  - {u.get('question')} | material because: "
                     f"{u.get('why_material')} | resolves via: {u.get('would_resolve')}")
    parts.append("\nWATCH:")
    for w in brief.get("watch", []):
        parts.append(f"  - {w.get('observable')} (settles: {w.get('settles')})")
    parts.append("\nSOURCES (cited, never 'verified'):")
    for s in brief.get("sources", []):
        parts.append(f"  [{s['key']}] {s['outlet']} — {s['title']} ({s['kind']})"
                     + (f" {s['url']}" if s["url"] else ""))
    if brief.get("notes_for_writer"):
        parts.append(f"\nNOTE FOR WRITER: {brief['notes_for_writer']}")
    return "\n".join(parts)
