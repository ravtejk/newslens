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
