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

from . import llm, net

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
    already_networked: bool = False,
) -> List[FetchRecord]:
    """Fetch a cluster's linked articles: items are dicts with url /
    source_name / tier (the caller reads them off source_items). Sequential
    by construction; a polite delay separates consecutive NETWORK attempts —
    the host's stated Crawl-delay when one exists, clamped to
    [POLITE_DELAY_S, CRAWL_DELAY_CEILING_S] (tier exclusions cost no delay;
    a cached robots denial still pays the delay — over-polite by design,
    never under). Duplicate URLs are fetched once.

    `already_networked` (NL-127): the caller has ALREADY opened a socket in this
    story's run, so the first attempt here pays its delay too. DEEPEN runs a
    second pass over the same story moments after the cluster pass; without this
    the two passes would be polite within themselves and rude at the seam. The
    default is the historical behaviour and every existing caller keeps it."""
    robots = robots or RobotsCache(fetch=fetch)
    records: List[FetchRecord] = []
    seen: set = set()
    did_network = bool(already_networked)
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
# Ladder under the run cap, cheapest first: Sonar background is skipped
# before synthesis; remaining stories' briefs are skipped before anything
# touches the briefing itself; routine derating raises an escalation flag
# in the run log, never absorbed silently. (The cap was $0.25 when these
# thresholds were shaped; it is $1.50 since B4 — config.py:59. The rungs'
# PRICES and the ordering contract that binds them live at the LADDER
# PRICING block below — ordering ruling 2026-08-01, NL-130.)

import sqlite3
from datetime import datetime, timedelta, timezone

# B4 (R-B4a): the analyst seat flipped to Claude Sonnet 5 on the Claude API lane
# (adaptive thinking, effort high). These names DERIVE from SEATS["analyst"]
# ("derive from SEATS or die" — the ranking.RANK_MODEL:61-63 precedent;
# llm.SEATS["analyst"] KeyErrors loudly if the seat vanishes) so the persisted
# `model` label, estimate_synthesis_usd, and the shadow all track the seat.
# REVERT to GPT-4o = flip SEATS["analyst"] back to **_GPT4O_API in llm.py.
ANALYSIS_MODEL = llm.SEATS["analyst"].model
ANALYSIS_USD_IN_PER_MTOK = llm.SEATS["analyst"].usd_per_mtok_in
ANALYSIS_USD_OUT_PER_MTOK = llm.SEATS["analyst"].usd_per_mtok_out
# B4: Sonnet 5 runs adaptive thinking (effort high) and thinking BILLS AS OUTPUT
# and counts against max_tokens — 1,400 (a ~700-word brief on GPT-4o) would
# length-finish inside the thinking block, tripping validate_brief's truncation
# retry on every slot. 6,000 leaves ~5k of thinking headroom above the ~950-token
# brief. On-the-wire ceiling, not the bill (output = thinking + brief). The
# finish_reason=="length" guard still fires here (an api-lane property the
# subscription lane lacks) so a genuine over-run is a caught retry, never a
# silently truncated brief. Revert-to-GPT-4o would drop this to ~1,400.
ANALYSIS_MAX_TOKENS = 6000
ANALYSIS_TIMEOUT_S = 90
SONAR_EST_USD = 0.012              # measured spike ~$0.007 + headroom
# ---------------------------------------------------------------------------
# LADDER PRICING — ORDERING RULING 2026-08-01 (NL-130), principal-approved
# ("Approved as converged, both riders in, build NL-130").
#
# The two rungs used to be priced by two DIFFERENT estimators: rung 1 against
# the output term alone ($0.09 -> a $0.102 line) and rung 2 against the full
# estimate. At production prompt sizes the full estimate is ~$0.119, so a band
# existed in which Sonar was PAID and the brief it feeds was then skipped —
# spend on a verification whose only consumer never ran (and `_sonar_verify`
# charges SONAR_EST_USD even on a FAILED call, :2241). The ruling closes that
# band by CONSTRUCTION: rung 1 now prices the brief's affordability with a
# call-time UPPER BOUND built from the same artifact rung 2 prices, so Sonar
# only ever runs when the brief is still fundable AFTER paying for Sonar. M9's
# order (2026-07-06, config.py:41-48 — "Sonar degrades first") is PRESERVED,
# not repealed: the band [bound, bound + SONAR_EST_USD) still skips Sonar while
# the brief runs. A slot that cannot fund its brief at all skips WHOLE, before
# rung 1 — no orphan verification spend.
#
# RIDER (a) — SUNSET CONDITION. This ladder has never fired live (0
# `derating: true`, 0 `skipped-budget` across 28 logged runs as of 2026-08-01).
# If derating still has not fired live by the promised cap tune-down decision
# (config.py:57-58, "TUNE DOWN once measured spend lands"), the org proposes
# replacing this ladder with a plain slot-skip + escalate.
# RIDER (b) — M9 clause (2) ("lower-tier briefs degrade before the lead's") is
# a CROSS-slot ordering this per-slot ladder still does not implement; the
# importance x deficit allocator is deferred, and this block is its future home.
#
# `render_material`'s hard budget, named ONCE so both rungs price the same
# number (rung 1's bound reads it here; `render_material` takes it as its
# default). Never re-hardcode it at a second address — two addresses drifting
# apart is the class this ruling exists to end.
MATERIAL_BUDGET_CHARS = 24_000
# Everything else that renders into the analysis prompt beside the template and
# the material block: the source map, memory_context, the scalars. MEASURED,
# not guessed — full derivation in the NL-130 build record (2026-08-01):
#   * real production maximum source map, reconstructed from all 52 persisted
#     `analysis_retrieval` manifests: 10,295 chars at 56 keys (2026-07-20);
#   * that same 56-key shape re-rendered with every title and outlet at its
#     all-time observed maximum (183 / 41 chars) AND every key sharing one
#     outlet — render_source_map's O(n^2) sibling-list worst case: 31,615;
#   * twice that key count (116) at the same maxima, diverse outlets: 35,927;
#   * memory_context <= CONTEXT_CAP(15) topics = 229 chars; story_title <= 108;
#     summary <= 226; the four scalars <= 32 (all observed maxima, real data).
# 40,000 dominates every one of those. It is an ALLOWANCE, not a proof-bound:
# the sibling list is quadratic in key count, so NO constant bounds it for an
# unbounded cluster. That is exactly why exceeding it is a DISCLOSED event
# rather than a silent one — see the ORDERING INVARIANT VIOLATED warning at
# rung 2, which is this constant's own falsifier tripwire. Over-counting is the
# safe direction for a money guard (config.py:57-58 states that doctrine for
# the cap itself); the price is that Sonar skips slightly earlier under
# exhaustion, which is precisely the order M9 rules.
#
# NL-133 2026-08-02 — ALLOWANCE -> BOUND (NL-130 gate ruling R-B / C-4, "the
# structural end of the quadratic-source-map class"). The quadratic's INPUT is
# now bounded upstream: `ranking.MAX_CLUSTER_ITEMS` caps items per cluster and
# `validate_payload` de-duplicates matched_memory (the P-key route into the
# same sibling list). With those two, the worst prompt this code could build
# measured 77,164 chars against a 78,621-char bound — a real ceiling, not a
# dominating guess. (That 77,164 is NL-133-ERA. NL-139 raised two of its
# inputs from observations to clamps and the same construction measures
# 77,780 today; the ladder is re-measured in ranking.py's MAX_CLUSTER_ITEMS
# block, which was stale on the same numbers until NL-142 corrected it.) It is conditional on FIELD lengths, not on cluster shape:
# fields far past their all-time maxima could still breach, and that residue
# had THREE owners, none of them the ranker — ingest (feed titles, all-time
# max 183), `_sonar_verify` (vendor titles/snippets/urls: count-clamped to 8,
# NOT byte-clamped — 8 titles averaging >=366 chars breach cap 48 by 7), and
# memory (topic inserts carry no length clamp).
#
# NL-139 2026-08-02 (NL-133 gate ruling R-B — the chartered byte-clamp) CLOSED
# TWO OF THE THREE. `_sonar_verify` now truncates vendor titles at
# SONAR_TITLE_MAX_CHARS and refuses a result whose URL host exceeds
# SONAR_HOST_MAX_CHARS (below); `memory.TOPIC_MAX_CHARS` clamps topic inserts.
#
# NL-142 2026-08-13 (NL-138/139 gate R-E-5) CLOSES THE THIRD — and CORRECTS
# this comment's own enumeration of it. Re-measured against the current tree
# (15,007 source_items, up from the 11,198 NL-133 swept), the ingest residue
# had FOUR rendered length inputs, not two, and one of them was named nowhere:
#
#   (i)   the FEED title — NOT unclamped, as this comment said. It is clamped
#         at ingest.STORED_TITLE_MAX_CHARS (500), a storage number never
#         derived against this bound: at 500 the worst map renders 93,228
#         chars against the 78,621 bound. A clamp 305 chars past the ceiling
#         is a storage guard wearing a bound's clothes.
#   (ii)  the S-key title, which is NOT the feed title at all. It is
#         `extract_article_text`'s reading of the FETCHED PAGE's <title>
#         (see `fetch_article` -> `rec.title` -> build_source_map's S# key),
#         bounded by nothing but MAX_ARTICLE_BYTES = 2,000,000. This is the
#         largest hole of the four and no record named it.
#   (iii) the article URL HOST, which renders as the outlet identity.
#   (iv)  the outlet NAME. This comment said it "is not in this residue class
#         at all" because it is `source.name` from the principal's own
#         sources.yaml. That is true of the RSS door ONLY: `discovery.
#         _store_results` writes `urlparse(url).netloc` — a remote-authored
#         host — into the same column (discovery.py, the sonar door), so a
#         source_items row CAN carry a vendor string in `outlet`.
#
# All four are closed at the RENDER door rather than at ingest, by
# MAP_TITLE_MAX_CHARS and MAP_LABEL_BUDGET_CHARS (below). The bound is a
# property of the prompt this module builds, so clamping where the prompt is
# built makes it structural over EVERY input — including the 15,007 rows
# already in the founder DB, which a write-door clamp could not reach without
# a migration (and the NL-142 charter makes a migration a stop).
#
# AND A FIFTH DOOR ONTO THE SAME LINE, found by the NL-142 SHIP GATE (F-G0,
# fix loop 2) after the first landing claimed these four closed. Owner (iii),
# the article-URL host, does not only reach the prompt as an S/C key's outlet
# identity: a Sonar result whose URL sits on the CLUSTER'S OWN host mints an
# R key (`build_source_map`, "outlet": _outlet_of(url)) that joins the same
# sibling list — and R labels were outside this budget, bounded only by
# NL-139's SONAR_HOST_MAX_CHARS = 253. Measured through the real
# `clamp_sonar_results`, 8 kept / 0 dropped: 81,972 chars against the 78,621
# bound, over by 3,351. One shared result breaches by 334; the onset is a
# 44-char shared host, which is an ordinary long subdomain. The close is the
# scope of the budget, not a new clamp: both render guards now read "SCR"
# (`render_source_map`, `_material_header`). See "POST-CLAMP WORST CASE"
# below for what that does to every number in this block.
#
# COVERAGE BOUND, stated so a green run is never over-read (QA F-2, fix loop
# 1). "Closed at the render door" means closed for THIS prompt — the two
# renderers `brief_bound_chars` integrates over, and no others. The
# ingest-owned title and outlet reach a MODEL through FIVE renderers:
#
#   BOUNDED by NL-142 — the analyst prompt:
#     1. `render_source_map`  — the citable-key list (title clamp on S/C
#        keys, label budget on S/C/R: every key kind whose outlet string is
#        remote-authored, widened in fix loop 2 for F-G0).
#     2. `_material_header`   — the same clamps on the same key, so one
#        key can no longer carry two outlet strings in one prompt (fix loop
#        1, QA F-1), on the same "SCR" scope (fix loop 2).
#   NOT BOUNDED — outside this charter, which is PROMPT_MARGIN_CHARS-scoped:
#     3. `render_writer_view`'s SOURCES block (this module, the `parts.append`
#        at the end of that function, called from generate.py:1194) — clamped
#        title (it reads the map dict), but RAW outlet and the WHOLE url.
#     4. generate.py:1198 and :1209 — the writer prompt's cluster-item and
#        source-item lines: the raw ingest.STORED_TITLE_MAX_CHARS title and
#        the raw outlet, read straight off source_items.
#     5. `ranking.render_items_block` (ranking.py:787-790) — the ranker
#        prompt, the same two raw fields.
#
# 3-5 are the WRITER and RANKER prompts. Neither has a `brief_bound_chars`
# analog — that function is ANALYSIS-scoped by its own docstring — so there
# is no bound there for these fields to be inside or outside of, and their
# state is NOT a defect in what NL-142 landed. It is a scope line: a green
# NL-142 suite says these fields cannot blow the ANALYST prompt; it does not
# say they can no longer reach a model unclamped. Whether the writer and
# ranker prompts get bounds of their own is a charter question and is
# returned to the gate, not answered here — a fix loop that minted a second
# prompt bound would be legislating mid-flight.
#
# POST-CLAMP WORST CASE, RE-DERIVED IN FIX LOOP 2 (the F-G0 close moved it;
# the first landing's 78,012 / 609 slack was measured on a worst-case model
# that could not see the sharing branch). Measured through the real
# constructors at MAX_CLUSTER_ITEMS=48 with every clamped field AT its clamp:
# 78,516 chars against the same 78,621 bound — 105 slack. The binding shape
# is now the SHARING branch (the 8 Sonar keys on the cluster's own host, one
# 56-key sibling list), which is worth ~3,384 chars of sibling text more than
# two separate lists; the old binding branch (Sonar keys on a maximal host of
# their own) collapsed from 78,012 to 74,556 once their labels entered the
# budget. Worst over both key branches too (S 78,516 / C 78,420).
#   * 9,792-case sweep, host and outlet moved INDEPENDENTLY across
#     0..2,000 x the sharing count 0..8 x both key branches: 0 breaches,
#     max 78,516, material block 24,000 on the nose.
#   * The same sweep at the pre-fix tree: breaching from a 44-char shared
#     host up, worst 81,972.
# Derivation in research/2026-08-02--nl138-build.md, 2026-08-13--nl142-build
# .md and 2026-08-13--nl142-fixloop2.md; the arithmetic lives in
# tests/test_nl139_byte_clamps.py and tests/test_nl142_ingest_bounds.py,
# derived from these constants.
#
# THE MARGIN IS EXHAUSTED, AND THAT IS THIS BATCH'S HONEST FINDING — MORE SO
# after fix loop 2, not less. Every rendered ingest field costs 48 chars of
# prompt per char (one render per key line, at the cap) and the label budget
# now costs 56 (48 cluster lines + 8 R lines). 105 slack therefore buys TWO
# characters on MAP_TITLE_MAX_CHARS or ONE on MAP_LABEL_BUDGET_CHARS —
# measured, not divided: ceilings 191 and 75, first breaches 192 and 76
# (tests/test_nl142_ingest_bounds.py walks both per run). NL-133's own
# clamp-design rule — floor from observed data, ceiling from the bound, land
# strictly inside both — is satisfiable for these clamps only barely. The
# earlier "609 slack buys twelve characters" reading of this paragraph is
# withdrawn: it priced a worst case 3,456 chars cheaper than the machine's.
# Raising this margin to buy more does not work either: at
# +911 chars the bound passes cap+2 and `test_nl133_cluster_item_cap.py::
# test_raising_the_cap_without_the_margin_breaks_this_test_first` goes red,
# whose own message prescribes re-deriving MAX_CLUSTER_ITEMS. (+911, not the
# +910 this comment first shipped — QA F-3, fix loop 1. Measured: at +910 the
# bound is 79,531 against a cap+2 prompt of 79,532 and the pin still BINDS;
# +911 makes them equal and the pin goes red. The number is now pinned at
# `test_nl142_qa_findings.py::test_the_margin_delta_that_unbinds_the_cap_pin
# _is_911`, so a future re-derivation of the cap or the material budget
# cannot let this prose drift the way ranking.py's did.) Widening any of
# these clamps is therefore a COUPLED decision over margin + cap, not a
# one-line edit — measured, not asserted; see the build record.
#
# Raising this constant is bound-safe but NOT money-free —
# bound_usd rises with it, so more slots skip under exhaustion (the coupling
# test's failure message says the same); LOWERING it, or raising
# MAX_CLUSTER_ITEMS, breaks the bound —
# tests/test_nl133_cluster_item_cap.py holds the coupling from the constants.
PROMPT_MARGIN_CHARS = 40_000

# --- NL-139 vendor byte-clamps (NL-133 gate R-B) ----------------------------
# Sonar returns SOMEONE ELSE'S bytes. `_sonar_verify` has always clamped the
# COUNT (8 results); these clamp the LENGTH, which is what the bound above
# actually integrates over. Each value is derived against that bound, and each
# names which side of the honest-degradation line it lands on.
#
# TITLE — TRUNCATED, silently. It renders once per R key in `render_source_map`
# (8 chars of prompt per char of title) and once in the material header. The
# all-time observed maximum for ANY title in this product is 183 (11,198
# ingested items); 200 sits above that, so the clamp cannot bite a headline
# this product has ever seen, and it costs the bound 8*(200-183) = 136 chars.
# Silent because the truncated string is read by the analysis MODEL off a
# citable-key list, not by the reader: no user-meaningful claim is shortened,
# and a warning per over-long vendor headline would be noise in the one
# channel that exists for repairs the reader's edition actually inherits.
# CEILINGS, IN BOTH REGIMES — labelled, because they are different numbers and
# reading the wrong one over-budgets a future re-derivation (QA F-5, fix loop
# 1; the first version of this comment quoted only the first as if operative):
#   * OBSERVED-MAXIMA regime (every OTHER field at its pre-clamp observed max —
#     how the NL-133 gate measured): ceiling 365. At 366 the bound breaks by 7
#     chars, the gate's recorded breach, reproduced in the pin. Unmoved by
#     NL-142: that regime is NL-133's own harness, whose ladder reproduces
#     char-for-char at both trees (48 -> 77,780 / 49 -> 78,651 / 50 -> 79,532).
#   * AT-THE-CLAMPS regime (the SHIPPED one): ceiling 213, first breach 214,
#     headroom 13. RE-BASED IN NL-142 FIX LOOP 2 — it read 276/277/76 when
#     "the other clamped fields" meant the vendor and memory clamps only.
#     NL-142 clamped the INGEST fields too, and the NL-142 gate then found the
#     vendor host renders as an R-key label on the same line (F-G0), so the
#     shipped worst case is now NL-142's: ingest at ITS clamps, the Sonar keys
#     on the cluster's own host. The clamp is FOUR TIMES closer to its ceiling
#     than this comment used to say. Pinned at test_nl139_byte_clamps.py::
#     test_the_documented_at_the_clamps_ceilings_are_the_real_ones.
SONAR_TITLE_MAX_CHARS = 200
# HOST — the result is DROPPED, and the drop is disclosed in the status line.
# 253 is the DNS maximum length of a hostname (RFC 1035/1123 octet limit) — a
# derived external ceiling, not a taste call, and one no REAL host can exceed.
# So this clamp only ever fires on a URL whose host is not a hostname at all.
# DROPPED rather than truncated because truncating a URL does not shorten it,
# it CHANGES it: a half-URL is a citation to somewhere else, and the map key
# it builds would carry a fabricated outlet identity — one that `_outlet_id`,
# `outlet_index` and the reader-facing "retrieved-single (%s)" provenance
# string would then all quote. Refusing one of eight vendor results and saying
# so is the honest move.
#
# THE BOUND NO LONGER DEPENDS ON THIS CONSTANT (NL-142 fix loop 2, gate F-G0).
# It used to: the URL's PATH never renders, but its HOST did, twice on every R
# line plus once on every sibling line, at 16 chars of prompt per char of host
# — and this comment documented ceilings for it (observed-maxima 329/330,
# at-the-clamps 291/292, headroom 38). That same 16-per-char cost is what let
# a Sonar result ON THE CLUSTER'S OWN HOST carry the prompt to 81,972 against
# a 78,621 bound through a door no enumeration had named. The close puts R-key
# LABELS inside MAP_LABEL_BUDGET_CHARS, and a budget does not care how long
# the string it truncates was, so there is now NO host length that breaches
# this bound: the ceilings above are RETIRED, not re-measured, and the pin
# asserts their absence (test_nl139_byte_clamps.py::test_the_sonar_host_is_
# inside_the_label_budget_and_dropped_past_the_dns_max) so nobody re-derives
# one from a stale comment. The DNS argument in the paragraph above is what
# this value rests on now — and it is not licence to raise it: a longer value
# admits a host that is not a hostname, which is the identity defect, not a
# byte-count one.
SONAR_HOST_MAX_CHARS = 253
# SNIPPET — TRUNCATED at the material budget, and stated plainly: THE PROMPT
# BOUND NEVER DEPENDED ON THIS. Snippets reach the prompt only as `text`, and
# `render_material` water-fills the whole material block into
# MATERIAL_BUDGET_CHARS, so no snippet of any length can add one rendered char
# past that. What this clamp bounds is what we HOLD and PERSIST — the
# analysis_retrieval row and the in-process dict — against a vendor response
# that hands back megabytes. A snippet longer than the entire material budget
# is bytes that are provably never read, which is what makes the budget itself
# the derived value here rather than a number chosen for looking round.
SONAR_SNIPPET_MAX_CHARS = MATERIAL_BUDGET_CHARS
# The COUNT clamp, named (it was the bare literal 8 at three addresses: this
# module, the NL-133 derivation comment, and the arithmetic pin). The worst
# case integrates over it exactly like the length clamps do, so it belongs in
# the same block and the pins read it from here.
SONAR_MAX_RESULTS = 8

# --- NL-142 ingest byte-bounds (NL-138/139 gate R-E-5) ----------------------
# The INGEST-owned fields, clamped where they are RENDERED (build_source_map /
# render_source_map) rather than where they are stored. Three reasons, in
# order of weight:
#   1. STRUCTURAL OVER HISTORY. A write-door clamp bounds tomorrow's rows; the
#      bound has to hold over the 15,007 rows already written. Clamping at the
#      render door needs no migration to cover them.
#   2. ONE DOOR, NOT N. `source_items.title` has two writers today
#      (ingest.parse_entries, discovery._store_results) and the S-key title has
#      a third path that never touches source_items at all (the fetched page's
#      <title>). All three render through here.
#   3. NOTHING IS FABRICATED BY A SHORTER LABEL. What these clamp is
#      model-facing map furniture — the citable-key list's title and outlet
#      strings — not a URL and not the reader's stored headline. NL-139 made
#      this exact argument for SONAR_TITLE_MAX_CHARS; the URL half of its
#      argument (a truncated URL is a DIFFERENT URL, so DROP) does not apply,
#      because the URL never renders into this prompt at all. Only its host
#      does, as an outlet LABEL.
#
# SCOPE — AND THE SCOPE LINE THAT WAS WRONG. This block first shipped saying:
# "S and C keys — the ingest-owned ones. R keys keep NL-139's clamps and P keys
# are ours. Each residue owner clamps its own fields, so neither derivation
# moves underneath the other." The last clause is exactly what the NL-142 gate
# refuted (F-G0). The TITLE clamp is still S/C-scoped — R titles carry
# SONAR_TITLE_MAX_CHARS, P titles are ours. The LABEL BUDGET is not: an R key's
# outlet is a remote-authored URL host that renders on the same line as the
# cluster's, and can BE the cluster's, so it is inside this budget as of fix
# loop 2 ("SCR" at both render doors). P keys stay outside — their outlet is
# our own constant string. One consequence, stated because a later reader will
# look for it: NL-139's host derivation now sits UNDER this one, which is why
# the ceilings in the SONAR_HOST_MAX_CHARS block above are retired.
#
# TITLE — TRUNCATED, silently, same channel argument as SONAR_TITLE_MAX_CHARS.
# FLOOR: 183, the all-time maximum over 15,007 ingested items (p99.9 = 151;
# 22 items in the corpus exceed 150 and 1 exceeds 180). CEILING: 191, first
# breach 192, headroom 2 — RE-MEASURED IN FIX LOOP 2 at the true worst case
# (it read "CEILING: 195" against the worst case that could not see the
# sharing branch). 189 lands strictly inside both, and there are two
# characters above it, not six. Walked per run by
# tests/test_nl142_ingest_bounds.py::
# test_the_documented_ceiling_and_headroom_of_each_clamp_are_the_real_ones.
#
# STORAGE SIDE-EFFECT, INTENTIONAL AND NAMED (QA F-6, fix loop 1). Unlike the
# label budget, this clamp is applied INTO the map dict (`build_source_map`),
# not at the render call — and `persist_brief` writes that same dict. So
# `analysis_retrieval.title` now holds at most 189 chars where it used to
# hold up to ingest.STORED_TITLE_MAX_CHARS (500). That is a storage change,
# it was undisclosed in the first landing, and it is kept rather than undone:
# the dict IS the model's view, and a hand-trace that stored a longer title
# than the analyst was shown would be a record of a prompt we never built.
# NO MIGRATION: the clamp is write-time and existing rows are untouched —
# over 1,618 persisted rows the longest title is 159 and over 15,359
# source_items it is 183, so not one existing or foreseeable row is
# shortened. Pinned at `test_nl142_qa_findings.py::
# test_the_render_clamp_also_shortens_the_persisted_hand_trace_title`, so the
# hand-trace record's bound is a stated property rather than a side effect of
# where the clamp was placed. The reader's stored headline
# (`source_items.title`) is NOT touched by any of this.
MAP_TITLE_MAX_CHARS = 189
# OUTLET + OUTLET-IDENTITY — a JOINT budget, max-min fair, not two clamps.
# Both render on the same line (`[C1] <outlet> — <title> (...; outlet <id>)`),
# so the bound only ever integrates over their SUM: a 60-char source name
# beside an 8-char host costs exactly what a 34-char name beside a 34-char
# host costs. Two separate clamps would refuse the first shape for no reason
# the arithmetic can name. Shares come from `_water_fill`, the module's
# existing max-min allocator (NL-118 P0) — short field takes what it needs,
# surplus flows to the long one.
# FLOOR: 68 = 41 (longest outlet name in the founder DB, his own sources.yaml)
# + 27 (longest article URL host, over the same 15,007 rows). The floor is now
# also an R-KEY floor: an R key's demand is twice its host, and the longest
# host over 1,618 persisted retrieval rows is 34 — 68 of this 74 budget, the
# tightest real demand of any key kind, and still inside. Not one real row of
# any kind is shortened by this clamp.
# CEILING: 75, first breach 76, headroom 1 — RE-MEASURED IN FIX LOOP 2 at the
# true worst case (it read "CEILING: 80" against the worst case that could not
# see the sharing branch). The marginal cost is 56 chars of prompt per char of
# budget now: 48 cluster lines plus the 8 R lines this budget just took on.
# WHICH HOST, named because the two conventions differ by 4 and the floor is
# quoted in the wider one (fix loop 1): 27 is the RAW netloc. What this budget
# actually integrates over is `_outlet_id` -> `_outlet_of`, which lowercases
# and strips a leading "www." — that string maxes at 23 over 15,359 rows, so
# the operative S/C floor is 64 and the worst REAL S/C row demands 53 of the
# 74. The floor stays quoted at the conservative 68 — which fix loop 2 turned
# out to be the RIGHT number for a different reason: the R-key demand above is
# 68 exactly. Re-deriving the floor downward would buy headroom, and buying
# headroom here is the coupled margin+cap call this batch returned to the gate
# rather than a fix-loop edit.
# The GROUPING identity is deliberately NOT clamped — `_outlet_id` and
# `outlet_index` keep the full host, so two long hosts sharing a prefix can
# never collide into one outlet and deflate the corroboration count the model
# reads off "DISTINCT OUTLETS IN THIS MAP". Only the printed label shortens.
MAP_LABEL_BUDGET_CHARS = 74
# STORAGE, not the bound (R-E-3). `analysis_retrieval.url` persists the vendor
# URL whole, PATH included, and the path never renders into any prompt — so
# this clamp cannot change what the model sees, by construction: persist_brief
# runs after render_source_map on an already-built map. Observed maximum 223
# chars over 1,569 persisted rows. An over-long URL is stored TRUNCATED WITH
# ITS LENGTH NAMED rather than dropped or silently cut: a bare truncated URL
# would be a citation to somewhere else (NL-139's rule), while a string that
# says "[truncated, N chars]" is unmistakably not a link, and the hand-trace
# it serves keeps the host and path prefix that make the row identifiable.
RETRIEVAL_URL_MAX_CHARS = 512
_RETRIEVAL_URL_MARK = " …[truncated, %d chars]"
# ---------------------------------------------------------------------------
# LENGTH REGIME 2026-07-30, Spec-4(c) STEP 0 (NL-118 content leg, batch B):
# RE-BASED medium 400 -> 450 and full 700 -> 750, in the same change that puts
# `arc` inside `_prose_words`. The two halves are one change. The arc RENDERS
# (see the ARC block in `render_brief_for_writer`) and had never been in the
# number the ceiling binds — ~55 words, ~14% of a medium budget, invisible.
# Landing the arc alone would have tightened the real ceiling by ~14%
# overnight; landing the budgets alone would have genuinely widened it.
# Together they re-base the SAME behaviour onto an honest measurement: the
# observed ~487-word median prose plus ~55 words of arc is ~540, the new
# ceiling. Measurement catching up with the product, not more room for it.
#
# NL-118 RATIFICATION 2026-08-01, ruling clause (i) — medium 450 -> 542.
# This one is NOT measurement honesty and must not be read as more of batch B:
# it is a THROUGHPUT change, and it does give the medium tier more room. That
# is the ratified intent. Grounds on the record: the trip tax (briefs hitting
# the ceiling) fell 73% -> 17% and the second-over rate 40% -> 0%, rejections
# 3 -> 1, P85 sat at a fixed point, and density at the new budget was certified
# by HAND READ — the widening buys throughput without buying padding. The
# acknowledged cost is a small-n fixture ledger edge (median 17 vs 15) against
# the old ceiling; the redraft record is mixed, not protective.
#
# The FULL tier is deliberately untouched at 750: the evidence is medium-tier
# evidence, and re-basing both together (batch B's move) would extend a
# measured result to an unmeasured tier.
#
# EVERYTHING DOWNSTREAM DERIVES — nothing below is hand-edited to match:
#   ceiling = 542 x WORD_CEILING_FACTOR (1.2)  = 650   (was 540)
#   band    = 542 x DISCLOSURE_BAND_FACTOR (1.35) = 731   (was 607)
# Both factors stay exactly where they were. Re-basing by moving a factor
# instead of the budget would silently change the SHAPE of the two-tier
# contract (how far past budget is tolerated), which is a different and
# unratified change. If you are here to widen the ceiling, move this number.
WORD_BUDGETS = {"full": 750, "medium": 542}


def word_budget_for(tier: str) -> int:
    """The tier's prose budget; an unknown tier gets the MEDIUM budget.

    NL-131 (gate ruling R-2, 2026-07-31 → structural-kill form chosen by the
    NL-130 batch 2026-08-01). Three call sites used to spell the fallback as a
    literal — `WORD_BUDGETS.get(tier, 450)` — which meant every re-base of the
    medium budget had to be re-synced at three addresses or leave a stale
    number behind reading as "the medium default" (the f4a9c6c lockstep
    precedent is exactly that chore, recurring). Deriving the default FROM the
    dict ends the class: there is no second number to re-sync, ever.

    PRODUCTION-UNREACHABLE BOTH WAYS, so this is drift-hygiene and not a
    behaviour change: `run_analysis`'s loop is the only entry to `analyze_story`
    and it gates on `if tier not in ("full", "medium"): continue`, and every
    `validate_brief` call site sits inside `analyze_story` under that same
    tier. No test anywhere passes an unknown tier."""
    return WORD_BUDGETS.get(tier, WORD_BUDGETS["medium"])


# EC-9 (content round 2026-07-28, resolved by combination — Remy's hard
# threshold WITH Vera's retry instruction): the tier budget is a target that
# warns; budget × this factor is a ceiling that raises and buys ONE redraft.
# The FACTOR is deliberately untouched by step 0 — re-basing by moving the
# factor would be a different, unratified change. medium 450 -> 540,
# full 750 -> 900.
WORD_CEILING_FACTOR = 1.2
# Spec-4(c) STEP 2 — the disclosure band. Past the ceiling but within
# budget x this, the SHORTER of the two drafts ships WITH a disclosure rather
# than being discarded. It is not a second, looser ceiling: nothing is written
# TO this number, the model is never told it, and a brief only reaches it after
# it has already been told to come in shorter and failed. Falsifier on the
# record: disclosed-band briefs failing a G7 density read close the band.
DISCLOSURE_BAND_FACTOR = 1.35
ALLOWED_EFFECT_BASES = ("attributed", "mechanical", "historical-pattern")
BRIEF_SECTIONS = ("pinned_facts", "ledger", "mechanism", "effects",
                  "arc", "unknowns", "watch")
QUOTE_MIN_CHARS = 12
ABSTRACT_MECHANISM_RE = re.compile(
    r"\b(tensions|dynamics|landscape|geopolitical situation)\b", re.I)

# X4's honest degrade, as one named string instead of three literals.
#
# It is written as an all-caps imperative because its reader is the ANALYST:
# "do not treat this edition's index as this thread's record". That makes it a
# model-input directive, and NL-118 QA finding 2 showed it was not staying in
# the model's channel — `source_table` copied the P key's title verbatim into
# the PERSISTED brief, so `brief["sources"][].title` carried it, and the only
# thing keeping it off a reader's screen was NL-58's unrelated title rewrite
# in server.py: cross-file, incidental, and owned by nobody here.
#
# So the boundary is owned HERE now (`_persisted_source_row`): the suffix is
# stripped from the persisted title and the same fact is carried in plain
# reader-safe prose under its own key. The writer channel re-attaches it
# (`render_writer_view`), because §5.3 says the writer view carries
# degradation directives — the reader view is the one that must not.
DEGRADE_NO_SECTION = "NO SECTION OF THAT EDITION NAMES THIS STORY"
DEGRADE_TITLE_SUFFIX = " — " + DEGRADE_NO_SECTION
DEGRADE_RECORD_STATUS = ("this edition carried no section naming this story")


# ---------------------------------------------------------------------------
# NL-148 — THE FETCH-FAILURE CONTRACT (principal's ruling 2026-08-09)
# ---------------------------------------------------------------------------
#
# The ruling, and where each clause lives:
#
#   (1) full-text fetch fails for a prioritized story -> try that story's OTHER
#       cluster sources, inside the existing sourcing boundaries, no open-web
#       hunting. ALREADY TRUE AND CARRIED, not built here: `fetch_cluster_
#       articles` walks EVERY item `_cluster_items_for_slot` returns, which is
#       the whole cluster (`slot["item_ids"]`), and the four boundaries in this
#       module's header gate every one of them. Pinned as a carried invariant,
#       born green and labeled — see tests/test_nl148_fetch_failure.py.
#   (2) no cluster source yields text -> skip the story and PROMOTE the next
#       prioritized story into its place. **NOT BUILT — STOPPED AND RETURNED
#       TO THE PRINCIPAL AS A DESIGN DECISION.** Two findings, both measured,
#       neither resolvable by an implementer's pick:
#
#       (a) THERE IS NO "NO MATERIAL" STATE TO SKIP ON. `build_source_map`
#           mints a C# excerpt key for every cluster item that was not
#           fetched, so a prioritized story whose full-text fetch fails
#           completely still carries its feed excerpts and still builds a
#           brief — the `degraded` path below, labeled in the artifact and in
#           the reader's meta line. Skipping it means REMOVING a story that
#           today gets honest, disclosed, degraded treatment. The existing
#           `skipped-thin` branch is NOT that state: it needs the source map
#           to be empty of S/C/R keys, which an empty cluster produces and a
#           failed fetch never does.
#       (b) THE PROMOTE COLLIDES WITH THIS TABLE'S IDENTITY MODEL.
#           `analysis_briefs` is keyed (date, slot) with NO story identity
#           (see the NL-107 note below, which is that gap wearing a different
#           hat). Promoting a story "into its place" renumbers slots, and the
#           moment slot N names a different story, `latest_valid_brief(date,
#           N)` — the run's own reading — returns another story's analysis.
#           That is mis-attributed analysis, the cardinal breach class, and it
#           also breaks the ruled resume default, whose whole content is that
#           slot N means the same thing on the retry as it did on the run.
#
#       Skip-without-promote would strictly thin editions; skip-with-promote
#       needs an identity decision that costs a schema field. Both are his.
#   (3) every such skip disclosed at the BOTTOM of the briefing. NOT BUILT:
#       clause 3 discloses clause 2's skips, and there are none until clause 2
#       is ruled. A disclosure line wired to an unreachable trigger would be
#       furniture that can never render.
#   (4) systemic failure (nothing fetches at all) -> PAUSE generation at that
#       spot with a retry. BUILT: `SystemicFetchFailure` + the ruled sentence
#       in `FETCH_PAUSE_MESSAGE`, raised from `run_analysis` and rendered by
#       the Today failure panel's EXISTING retry button.
# The ruled sentence, VERBATIM (principal 2026-08-09). It is his words, not a
# paraphrase, and it is not reworded to suit any downstream grammar.
#
# WHERE IT RENDERS, MEASURED NOT ASSUMED: the Today failure panel prints
# `GEN_JOB.snapshot()["error"]` unfiltered (server.py, the state=='error' arm),
# so this reaches the reader verbatim beside the existing "Try again" button —
# which is the interactive surface clause 4 names.
#
# WHERE IT DOES NOT, DISCLOSED: the FOUNDING page (a stranger's first screen,
# before any edition exists) runs every run-sentence through
# `commissioning.unfit_for_readers`, an ALLOWLIST whose safe form is
# "<phase> failed: <plain words>" with <phase> drawn from
# generate.PROGRESS_LABELS. This sentence is a comma clause, not that form, so
# the predicate answers True and the founding panel OMITS it, falling back to
# its own generic text. Measured, not reasoned:
#     commissioning.unfit_for_readers(FETCH_PAUSE_MESSAGE) -> True
# That is a real gap on one surface and it is carried as a disclosed one
# rather than closed by either of the two moves available: rewording HIS
# sentence to fit a grammar, or widening a build-blocking safety seam (C1) as
# an unrequested rider mid-milestone. It is a checkpoint item.
FETCH_PAUSE_MESSAGE = "Fetch failed, please try again in a few minutes"

# ---------------------------------------------------------------------------
# NL-151 — CLAUSES 2/3 RULED (principal 2026-08-13). THREE LAWS.
# ---------------------------------------------------------------------------
#
#   L1  NO degraded (excerpt-only) coverage in the depth tier, EVER.
#   L2  a prioritized story whose fetches fail is SKIPPED, DISCLOSED at the
#       bottom of the briefing, and the next prioritized story is PROMOTED
#       into the depth treatment.
#   L3  degraded coverage is PERMITTED (not mandated) for In-Brief stories.
#
# WHAT NL-148 STOPPED ON, AND WHY IT NO LONGER STOPS. Its finding (a) — every
# unfetched cluster item still mints a C# excerpt key, so there is no
# "no material" state — REMAINS TRUE and its detector pin stays green. It was
# never the obstacle it looked like: it only proved the skip could not hang off
# SOURCE-MAP EMPTINESS. The trigger below reads FETCH OUTCOMES instead
# (`fetch_attempted`/`fetch_ok`, already computed, already in the run record),
# which is what the ruling's own words are about. Finding (b) — the promote
# renumbers slots and mis-attributes analysis — is avoided by TIER
# REASSIGNMENT: the depth TIER moves to the next-ranked story's slot while slot
# NUMBERING stays untouched, so `analysis_briefs` (date, slot) keeps meaning
# exactly what it meant and the resume-from-failed-slot default survives. No
# schema field, no migration, no renumbering.
#
# L1 IS TRUE BY CONSTRUCTION WHEN ARMED, NOT BY REVIEW. Two gates below return
# before any brief is synthesized, and `analyze_story` is only ever called for
# full/medium tiers — so under either live arm a `degraded` brief can no longer
# be minted at all. That is the strongest available form of "ever", and it is
# what the L1 invariant pin asserts (the pin arms the contract; it does not
# assert L1 of the OFF default, which is today's shipped behaviour and does not
# hold it). The `degraded` machinery is deliberately NOT deleted: it is the
# honest label if a future tier ladder analyses In-Brief stories (L3 permits
# exactly that), and deleting it would trade a live safety label for tidiness.
#
# THE GATES ALSO SAVE MONEY, which is why they sit above the ladder rather than
# beside the `degraded` mint. A slot that cannot lawfully hold depth coverage
# must not buy Sonar verification or a synthesis it will never publish — the
# $0.74 of billed-then-rejected briefs QA measured on one clause-4 pause is
# exactly this spend. Post-NL-151 a systemic-failure day costs $0 in the
# analysis stage and still pauses (clause 4 unchanged; see the composition pin).
#
# TWO OUTCOMES, AND THE SPLIT IS ABOUT HONESTY, NOT MECHANISM. Both are
# disqualified from depth by L1; only one is a FETCH FAILURE:
#   * FETCH_SKIP_OUTCOME     — sockets opened, nothing came back. This is L2's
#     subject, and it is the ONLY one that earns the principal's disclosure
#     sentence.
#   * NO_FETCHABLE_OUTCOME   — nothing was ever attempted, because every
#     cluster source sits outside the 2026-07-06 tier boundaries. Nothing
#     failed, so saying "Fetch failed" would be false; the run record carries
#     it, the reader does not. Same reasoning the clause-4 any/all split
#     already encodes (a slot that never opened a socket casts no vote).
#     MEASURED REACHABILITY, on the principal's own generation_log.jsonl: over
#     24 analysis stages / 72 prioritized slots, 71 got full text, 1 was
#     attempted-and-failed, and ZERO were never-attempted. This branch is
#     structurally reachable and has never been observed — it exists so L1's
#     "ever" is literally true, not because it is a live path.
FETCH_SKIP_OUTCOME = "skipped-fetch-failed"
NO_FETCHABLE_OUTCOME = "skipped-no-fetchable-sources"
# The outcomes that DISQUALIFY a slot from the depth tier and hand its tier to
# the next-ranked story. One name, so the stage walk and the pins cannot drift.
DEPTH_DISQUALIFYING = (FETCH_SKIP_OUTCOME, NO_FETCHABLE_OUTCOME)

# ---------------------------------------------------------------------------
# THE FORK — RULED 2026-08-14. ARM (ii), "demote to in brief".
# ---------------------------------------------------------------------------
#
# His three laws fixed what happens to the DEPTH TIER. They did not fix what
# happens to the SKIPPED STORY ITSELF, and his words carried two readings:
#
#   ARM "drop"      the story leaves the edition body entirely; the disclosure
#                   line at the bottom is its only trace.
#   ARM "in-brief"  the story lands as an In-Brief story with degraded/excerpt
#                   treatment — lawful under L3, which PERMITS (never mandates)
#                   degraded coverage there.
#
# HIS WORD, 2026-08-14: "(ii) demote to in brief". So `FETCH_SKIP_ARM` now
# defaults to IN_BRIEF and the contract is LIVE on every ordinary morning.
#
# WHAT THE DEFAULT FLIP TURNS ON, all of it built and inert since NL-151:
# Gates A and B above the ladder, the depth-tier walk, the clause-3 disclosure
# and the run record — plus NL-151b's propagation, which is what makes arming
# safe rather than a regression. Before the propagation existed, arming would
# have LEFT the failed slot in a depth position with "Analysis: unavailable —
# built from feed excerpts", breaking L1 with the change meant to enforce it.
# That is why NL-151 shipped OFF and why this flip rides with the vector work.
#
# OFF IS STILL CONSTRUCTIBLE and still pinned. It is no longer the default; it
# is the LEGACY arm — today-before-the-ruling, reachable so the armed pins
# measure a real difference and so `--no-refresh` recovery can be proven inert
# there. Nothing reads it from the environment: see the constant's note below.
#
# ARM "drop" WAS NOT BUILT and the constant is a placeholder, not a second
# behaviour. Arm (i) needs slot-IDENTITY plumbing (the filtered `story_slots`
# and server.py's `i + 1` brief lookup becoming `slot["slot"]`) which nobody
# wrote, because his ruling made it unnecessary. Setting the constant to
# "drop" therefore yields the IN-BRIEF propagation. That is stated in code
# (`generate.depth_arm_is_in_brief`) and pinned by name, so it cannot be
# discovered the hard way; retiring the constant or building (i) is his call.
FETCH_SKIP_ARM_OFF = "off"
FETCH_SKIP_ARM_IN_BRIEF = "in-brief"
FETCH_SKIP_ARM_DROP = "drop"
# NOT an env var (none may land without his checkpoint) and not config: a
# module constant the pins monkeypatch, so every arm is exercised at $0.
FETCH_SKIP_ARM = FETCH_SKIP_ARM_IN_BRIEF


def depth_skip_armed() -> bool:
    """Is the NL-151 depth-skip contract live? One reader, so the gates, the
    stage walk and the pins can never disagree about whether it is on."""
    return FETCH_SKIP_ARM != FETCH_SKIP_ARM_OFF


class SystemicFetchFailure(RuntimeError):
    """Clause 4: nothing fetched at all, so the edition cannot be built.

    A PAUSE, not a crash, and the distinction is the whole point: it is raised
    only when EVERY prioritized slot came back with no material, so there is no
    half-edition to publish. It deliberately escapes `run_generate`'s stage-wide
    degrade handler — degrading to feed excerpts here would publish an edition
    built on nothing while telling the reader everything was fine, which is the
    silent-thinning the contract exists to forbid.

    WHICH SLOTS HAVE A VOTE (FIX-1, gate Ruling A 2026-08-12): the verdict asks
    whether FETCHING is broken, so a slot that never opened a socket — every
    source tier-excluded by the 2026-07-06 boundaries — has no opinion and casts
    none. It cannot veto the pause (it used to, which let one excluded slot
    shield a whole-network outage), and it cannot cause one either: the pause
    needs at least one slot to have ACTUALLY TRIED and come back empty, so a
    policy-only day where nothing was ever attempted still never pauses.

    THE RULED DEFAULT HOLDS BY CONSTRUCTION — and it is NARROWER than "the retry
    is free" (QA F4, 2026-08-12; the overclaim is corrected here, the behaviour
    is not). What the principal ruled is that ALREADY-COMPLETED STORIES never
    re-run and never re-bill. This verdict cannot be true while any prioritized
    story produced a valid brief, so at the moment of the pause NO completed
    story exists, and the retry therefore re-runs only incomplete work — the
    guarantee holds with nothing to preserve.

    WHAT IT DOES NOT PROMISE: that the paused run spent nothing. With a
    reachable model, slots that lost every fetch can still be synthesized and
    then REJECTED at cost (QA measured $0.74 of billed-but-rejected briefs on
    one such pause), and a retry pays for those attempts again — they were never
    completed work. Pinned as this invariant rather than asserted in prose; see
    tests/test_nl148_fetch_failure.py.
    """


class BriefRejected(ValueError):
    """Hard-reject class: the brief is discarded for BOTH consumers."""


class BriefOverCeiling(BriefRejected):
    """EC-9's ceiling with teeth (NL-118 item 6). A SUBCLASS of BriefRejected
    on purpose: any caller that does not know about the retry still degrades
    exactly as it always did (a disclosed rejected row), and the one caller
    that does — `analyze_story` — catches this first and redrafts once."""

    def __init__(self, words: int, budget: int, ceiling: int):
        self.words, self.budget, self.ceiling = words, budget, ceiling
        super().__init__(
            f"brief runs {words} prose words against the {budget}-word budget "
            f"— past the {ceiling}-word ceiling (budget +"
            f"{round((WORD_CEILING_FACTOR - 1) * 100)}%)")


class BriefQuoteUnmarked(BriefRejected):
    """Spec-1's promotion (content round 2026-07-31, HIGH stakes).

    A quoted span that is neither verbatim nor lawfully MARKED. A SUBCLASS of
    BriefRejected for the same reason `BriefOverCeiling` is one: any caller
    that does not know about the redraft degrades exactly as it always did,
    and the one caller that does — `analyze_story` — catches this first and
    redrafts ONCE.

    The round explicitly REFUSED the bare one-line `raise`: a rejection that
    only says "no" turns a fixable editorial slip into a lost brief. This
    carries the span and the two lawful outs so the redraft can act.
    """

    def __init__(self, span: str, where: str):
        self.span, self.where = span, where
        super().__init__(
            f"quote in {where} is not a verbatim substring of retrieved "
            f"material and is not lawfully marked: \"{span[:60]}...\" — quote "
            "the exact words, or drop the quote marks and paraphrase with the "
            "citation")


# Spec-1's instructed redraft. Named outs, not a scolding: the model is told
# the two lawful moves, because "that quote is wrong" without them produces a
# second draft that drops the quotation and the attribution with it.
QUOTE_REDRAFT_INSTRUCTION = (
    "\n\nREDRAFT — ONE QUOTATION IN YOUR PREVIOUS ATTEMPT WAS NOT VERBATIM.\n"
    "In {where} you wrote: {span}\n"
    "Quote marks promise the source's EXACT words. That span is not a verbatim "
    "substring of the supplied material, and it carries no disclosed mark.\n"
    "Fix it in ONE of exactly two ways, keeping the citation either way:\n"
    "  1. QUOTE THE EXACT WORDS — copy them character for character from the "
    "material, or quote a SHORTER exact span. Disclosed adjustments are "
    "allowed: square brackets for an inserted or adjusted word, an ellipsis "
    "(...) for an omission that does not change the meaning.\n"
    "  2. DROP THE QUOTE MARKS and paraphrase the point in your own words, "
    "keeping the citation and the attribution.\n"
    "Never splice quotations from different sources into one span. Change "
    "NOTHING else about the brief.\n")


# The retry instruction is Vera's, verbatim in intent and recorded as the
# resolution of a real disagreement: a model told only "you are over" drops
# the LAST thing it wrote, and on the specimen page the last thing was the
# human stakes. So the redraft names what to cut.
REDRAFT_INSTRUCTION = (
    "\n\nREDRAFT — YOUR PREVIOUS ATTEMPT WAS OVER THE CEILING.\n"
    "That draft ran {words} prose words against a {budget}-word budget; the "
    "hard ceiling is {ceiling}. Write it again, shorter.\n"
    "You may come in WELL UNDER the budget — short is a success condition "
    "here, not a failure to be padded around.\n"
    "Do NOT remove specifics. Remove RESTATEMENT: a fact stated in "
    "pinned_facts does not need restating in the ledger, the mechanism, or "
    "an unknown. Cut the sentence that carries no numeral, no proper noun, "
    "no date and no quoted phrase of its own. Keep every named person, "
    "number, date and attribution chain from the draft you are replacing.\n")


@dataclass
class StoryAnalysis:
    slot: int
    tier: str
    outcome: str            # ok | rejected | skipped-budget | skipped-thin |
                            # demoted-quick | failed |
                            # skipped-fetch-failed | skipped-no-fetchable-sources
                            #   (NL-151 gates A/B; armed-only — see FETCH_SKIP_ARM)
    detail: str = ""
    cost_usd: float = 0.0     # usd_CHARGED — real money; persisted as-is
    # NL-95: usd_SHADOW — what this slot would cost at api prices. The edition
    # cap binds THIS (Onna's law); nothing persists it as money.
    shadow_usd: float = 0.0
    fetch_ok: int = 0
    fetch_attempted: int = 0
    sonar_status: str = "skipped"
    warnings: List[str] = field(default_factory=list)
    brief: Optional[Dict] = None
    # Ordering ruling 2026-08-01 (NL-130), Onna's instrumentation clause: the
    # synthesis ESTIMATE and the rung-1 BOUND, persisted per slot in the
    # generation_log so the promised cap tune-down (config.py:57-58) has
    # est-vs-actual data to decide on instead of a re-derivation. `est_usd`
    # stays None on every path that never renders a prompt (slot-atomic floor,
    # skipped-thin, demoted-quick) — a null there means "never priced", which
    # is a different fact from "priced at zero".
    est_usd: Optional[float] = None
    bound_usd: Optional[float] = None
    # C-5 (principal ruling (a), 2026-08-06): True when the slot-atomic budget
    # floor fired AND the free slot-3 demoted-quick verdict row was persisted
    # anyway. Distinguishes "skipped-budget, reader still got a verdict" from
    # "skipped-budget, reader fell back to A2" in the generation_log — the two
    # used to be indistinguishable because only the second existed.
    slot3_verdict_under_floor: bool = False
    # NL-148: the story this slot was handed, carried so the run's own record
    # can name it. `analysis_briefs` is keyed (date, slot) with NO story
    # identity, and a report that re-derived the name from a slot number would
    # name whichever story holds that slot at read time.
    story_title: str = ""
    # NL-127 charter rider: ONE ROW PER SONAR URL DEEPEN considered, with the
    # outcome and its detail. Production's `header.fetch = {ok, attempted}` is
    # an aggregate that drops per-URL reasons — the gap that forced the
    # 2026-08-24 Bucket-B mini to re-measure failures that had already been
    # observed once. Empty on every story DEEPEN did not fire on, which is a
    # different fact from "fired and found nothing" (`deepen_stats` reads the
    # ledger; a run that never fired has no stats line at all).
    deepen_ledger: List[Dict] = field(default_factory=list)


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
    # NL-118 item 2: the model has never seen WHEN anything was published —
    # `source_items.published_at` stops at the DB. Carry it onto every key
    # (the fetch record has no date of its own; the cluster item it came from
    # does, joined here on URL) so `render_material` can dateline the header.
    published_by_url = {(it.get("url") or "").strip(): it.get("published_at")
                        for it in cluster_items if it.get("published_at")}
    fetched_urls = set()
    n = 1
    for r in fetch_records:
        if r.outcome == OK and r.text:
            sources[f"S{n}"] = {"kind": "cluster-full-text", "outlet": r.source_name,
                                # NL-142: this title is the FETCHED PAGE's
                                # <title> (extract_article_text), not the feed
                                # title — remote HTML bounded only by
                                # MAX_ARTICLE_BYTES until this clamp.
                                "title": clamp_map_title(r.title) or "(untitled)",
                                "url": r.url,
                                "retrieved_at": now, "text": r.text,
                                "published_at": published_by_url.get(
                                    (r.url or "").strip(), "")}
            fetched_urls.add(r.url)
            n += 1
    n = 1
    for it in cluster_items:
        if it.get("url") in fetched_urls:
            continue  # full text supersedes its own excerpt
        sources[f"C{n}"] = {"kind": "cluster-excerpt", "outlet": it.get("outlet", ""),
                            # NL-142: the feed title, from either source_items
                            # writer (ingest.parse_entries / discovery).
                            "title": clamp_map_title(it.get("title", "")),
                            "url": it.get("url", ""),
                            "retrieved_at": it.get("fetched_at", ""),
                            "published_at": it.get("published_at") or "",
                            "text": it.get("raw_excerpt") or ""}
        n += 1
    cluster_urls = cluster_url_set(fetch_records, cluster_items)
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
                            # the Search API returns a `date` per result and we
                            # discarded it; kept now as a dateline (item 2)
                            "published_at": _iso_day(res.get("date")),
                            "text": res.get("snippet") or res.get("title") or "",
                            # NL-127: this key's text is a FETCHED PAGE, not the
                            # ≤303-char vendor locator. `kind` deliberately does
                            # NOT move — it is a reader-facing label AND an
                            # exact-match discriminator at server.py:4693/5278,
                            # and it renders on every source-map line inside the
                            # prompt's margin. The flag is a separate field, so
                            # it reaches the reader (the deep view's source row)
                            # and the receipts without touching either.
                            "deepened": bool(res.get("deepened"))}
        n += 1
    n = 1
    for pb in prior_briefings:
        title = f"briefing {pb.get('date')}"
        if pb.get("thread"):
            title += f" — thread: {pb['thread']}"
        elif pb.get("matched") is False:
            # X4's honest degrade: the model must not read an unrelated
            # story's coverage as this thread's record.
            title += DEGRADE_TITLE_SUFFIX
        sources[f"P{n}"] = {"kind": "prior-briefing", "outlet": "NewsLens (prior edition)",
                            "title": title, "url": "",
                            "retrieved_at": pb.get("date", ""),
                            "published_at": _iso_day(pb.get("date")),
                            "text": pb.get("text") or ""}
        n += 1
    # NL-118 item 4: outlet multiplicity, computed HERE at prompt-build (it
    # existed only at render time, downstream of the model that needed it).
    for oid, keys in outlet_index(sources).items():
        for key in keys:
            sources[key]["outlet_id"] = oid
            sources[key]["outlet_keys"] = list(keys)
    return sources


def cluster_url_set(fetch_records: List[FetchRecord],
                    cluster_items: List[Dict]) -> set:
    """Every URL this story's CLUSTER already accounts for — the set the R-mint
    above dedupes against, so one article can never wear two keys.

    Factored out for NL-127: DEEPEN has to know the same set BEFORE
    `build_source_map` runs (fetching a URL whose R key will never be minted
    buys a page nothing can cite), and two copies of this expression would drift
    the day one of them learns something the other does not.
    """
    return ({r.url for r in fetch_records if r.outcome == OK and r.text}
            | {(it.get("url") or "").strip() for it in cluster_items})


def _outlet_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def clamp_map_title(title: object) -> str:
    """NL-142 (gate R-E-5): the ingest-owned title clamp, at the render door.

    Public for the same reason `clamp_sonar_results` is: the arithmetic pin
    drives it directly with a hostile string, and the clamp is the thing being
    proven. Applied to S and C keys only — R keys carry NL-139's vendor clamp
    and each residue owner clamps its own fields."""
    return (title or "")[:MAP_TITLE_MAX_CHARS]


def clamp_map_labels(outlet: str, outlet_id: str) -> Tuple[str, str]:
    """The joint outlet-label budget (NL-142). Returns the two strings as they
    RENDER — max-min fair shares of MAP_LABEL_BUDGET_CHARS, so a long name
    beside a short host is never refused for a reason the bound cannot name.

    Reuses `_water_fill` rather than restating the allocation: one allocator,
    two callers (material shares and these labels)."""
    outlet, outlet_id = outlet or "", outlet_id or ""
    if len(outlet) + len(outlet_id) <= MAP_LABEL_BUDGET_CHARS:
        return outlet, outlet_id
    shares = _water_fill([len(outlet), len(outlet_id)], MAP_LABEL_BUDGET_CHARS)
    return outlet[:shares[0]], outlet_id[:shares[1]]


def _outlet_id(s: Dict) -> str:
    """One identity per OUTLET across key kinds (NL-118 item 4). The same
    newsroom reaches the map as a feed name on S/C keys ("NPR") and as a host
    on R keys ("npr.org"); counting those as two outlets would inflate every
    corroboration count the model is about to modulate confidence against.
    The URL host is the identity where a URL exists, the outlet string
    otherwise (prior-briefing keys, which carry no URL)."""
    host = _outlet_of(s.get("url") or "")
    return host or (s.get("outlet") or "").strip().lower()


def outlet_index(sources: Dict[str, Dict]) -> Dict[str, List[str]]:
    """outlet identity -> the keys carrying it, in map order."""
    out: Dict[str, List[str]] = {}
    for key in sorted(sources, key=_key_sort):
        out.setdefault(_outlet_id(sources[key]), []).append(key)
    return out


def render_source_map(sources: Dict[str, Dict]) -> str:
    """The citable-key list — now carrying each key's DATELINE and its
    OUTLET MULTIPLICITY (NL-118 items 2 + 4).

    Outlet counts existed only at RENDER time (`server.py:_facts_outlet_count`,
    computed from the finished brief's cites), so the model that wrote the
    claims never saw them and could not modulate confidence to corroboration.
    They are computed at prompt-build now, by `build_source_map`, and shown
    here: keys sharing an outlet are named on each other's line, and the map's
    distinct-outlet total closes the line. The model can then count DISTINCT
    outlets behind any cite set with no arithmetic we have to trust."""
    by_outlet = outlet_index(sources)
    lines = []
    for key in sorted(sources, key=_key_sort):
        s = sources[key]
        oid = _outlet_id(s)
        bits = [s["kind"]]
        dateline = _dateline_of(s)
        if dateline:
            bits.append(f"published {dateline}")
        sibs = [k for k in by_outlet.get(oid, []) if k != key]
        # NL-142: the ingest-owned keys' two labels share one budget. `oid`
        # above is the UNCLAMPED identity and stays that way — `by_outlet` and
        # the DISTINCT-OUTLETS total are computed from it, so shortening the
        # printed label can never merge two outlets or deflate a corroboration
        # count. Only these two strings shorten.
        #
        # SCOPE IS "SCR", NOT "SC" (fix loop 2, gate F-G0 — the finding that
        # BLOCKED this batch). An R key's `outlet` IS its URL host
        # (`_outlet_of`, build_source_map), bounded by NL-139 at
        # SONAR_HOST_MAX_CHARS = 253 rather than by this budget — and the
        # R-mint dedupes on the exact URL, so a Sonar result on the CLUSTER'S
        # OWN host mints an R key that joins the cluster's sibling list and
        # renders 2 x 253 label chars on every one of the 8 R lines. Measured
        # end-to-end through the real `clamp_sonar_results` at the pre-fix
        # tree: 81,972 chars against the 78,621 bound, over by 3,351; ONE
        # shared result at 253 breaches by 334; the onset is a 44-char shared
        # host. The budget therefore has to cover every key kind whose label
        # is remote-authored. P keys stay raw: their outlet is our own
        # constant string.
        #
        # WHAT DOES NOT MOVE: the map DICT. `compute_provenance` reads
        # `sources[c]["outlet"]` for S/C corroboration counting AND prints an
        # R key's dict outlet into the reader-facing "retrieved-single (%s)"
        # tier string — so a dict-level clamp would deflate a trust tier on
        # one door and shorten a host the READER sees on the other. Both
        # clamps stay at render doors; `tests/test_nl142_gate_pin.py` holds
        # that line.
        #
        # THE OTHER DIRECTION, disclosed here because the build record cited
        # these lines for it and they said only the reassuring half (QA F-4,
        # fix loop 1): two DISTINCT outlets sharing a 74-char prefix would
        # print the SAME label on two lines. Grouping is unaffected — the
        # identity above is whole — so the DISTINCT-OUTLETS total the model
        # reads stays correct, and the failure direction is conservative: the
        # two keys are NOT marked SAME OUTLET, so the model counts two, which
        # is the truth. Unreachable on the founder's corpus, which is the
        # honest bound (measured over 15,359 rows: worst outlet+identity
        # demand 53 chars against a 74 budget; 57 by the un-stripped-netloc
        # convention the FLOOR above is quoted in). NOT unreachable in
        # general — two FQDNs may share a 74-char prefix, since a DNS label
        # runs to 63 and an FQDN to 253.
        outlet_label, oid_label = (
            clamp_map_labels(s["outlet"], oid) if key[0] in "SCR" else (s["outlet"], oid))
        bits.append(f"outlet {oid_label}"
                    + (f" — SAME OUTLET as {', '.join(sibs)}" if sibs
                       else " — 1 key"))
        lines.append(f"[{key}] {outlet_label} — {s['title']} ({'; '.join(bits)})")
    if not lines:
        return "(none)"
    outlets = {o for o in by_outlet if o}
    lines.append(f"DISTINCT OUTLETS IN THIS MAP: {len(outlets)}. A claim's "
                 "outlet count is the number of DISTINCT outlets among the "
                 "keys you cite — keys marked SAME OUTLET count once.")
    return "\n".join(lines)


def _water_fill(demands: List[int], budget: int) -> List[int]:
    """Max-min fair shares: each entry gets min(demand, λ) for the largest λ
    the budget affords, so surplus from short entries RE-FLOWS to the long
    ones instead of evaporating (NL-118 P0).

    The old allocator computed one average share and handed it to every
    entry: a 303-byte Sonar stub reserved a ~1,371-char slice and spent 303
    of it, while BBC's 6,622-char article was cut at that same 1,371 and
    14,446 chars of the 24,000 budget went unspent. Ascending-demand greedy
    is the textbook water-filling construction: settle the cheapest demand
    first, then re-divide what is left among those still thirsty."""
    n = len(demands)
    out = [0] * n
    if n == 0 or budget <= 0:
        return out
    remaining = budget
    left = n
    for i in sorted(range(n), key=lambda j: demands[j]):
        take = min(demands[i], remaining // left)
        out[i] = take
        remaining -= take
        left -= 1
    return out


# Never a sliver (BUG15's rule, restated for the water-fill): a source that
# has to be TRUNCATED below this is dropped instead of shredded. Sources
# whose whole text fits are never affected.
MATERIAL_MIN_SHARE = 400
_MATERIAL_SEAM = 2   # the "\n\n" between rendered entries


def _iso_day(raw: object) -> str:
    text = str(raw or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-" \
            and text[:4].isdigit() and text[5:7].isdigit() and text[8:10].isdigit():
        return text[:10]
    return ""


def _dateline_of(s: Dict) -> str:
    """The absolute PUBLICATION date for a source's material header, as
    YYYY-MM-DD, or "" when we do not hold one.

    ANNOTATION ONLY — the corpus text itself is never rewritten (Rook's rail:
    `check_quotes` validates model quotes as verbatim substrings of the corpus
    WE assemble, so editing the corpus would let the model quote our own edit
    and pass the fabrication check).

    `retrieved_at` is NOT a fallback for article keys: it is when WE fetched,
    which on every S/C/R key is today. Stamping today onto a three-day-old
    article would be a fabricated dateline wearing our own header — strictly
    worse than no dateline. The one exception is a prior-briefing key, where
    the "outlet" is us and the retrieved_at IS that edition's publication
    date."""
    day = _iso_day(s.get("published_at"))
    if day:
        return day
    if (s.get("kind") or "") == "prior-briefing":
        return _iso_day(s.get("retrieved_at"))
    return ""


def _material_header(key: str, s: Dict) -> str:
    # NL-142 fix loop 1 (QA F-1): the SAME label clamp `render_source_map`
    # applies, applied here. This header and the map line render one key's
    # outlet into ONE analyst prompt, and the map is introduced to the model
    # as the closed citation vocabulary — a header naming a different outlet
    # for the same key is a coherence defect, measured at 37 chars in the map
    # line against 2,000 in the header. F-1b is the behavioural half: an
    # unclamped header is charged to MATERIAL_BUDGET_CHARS and
    # `render_material`'s pop-loop sheds keys to pay for it, so a hostile
    # outlet used to deliver HALF the material at the same byte count (22
    # keys vs 44 at the clamps). The batch's identity claim was therefore a
    # LENGTH identity only; with this it holds for the content too.
    #
    # WHY HERE AND NOT IN `build_source_map`: `compute_provenance` builds its
    # corroboration set out of the raw `sources[c]["outlet"]` values, so a
    # dict-level clamp could merge two outlets and DEFLATE a corroboration
    # count — the exact failure `_outlet_id` stays unclamped to avoid,
    # arriving through the other door. Both clamps live at render doors and
    # neither touches an identity.
    #
    # SCOPE "SCR" (fix loop 2, gate F-G0): the same widening as
    # `render_source_map`'s guard, kept in step with it deliberately — the two
    # doors render the same key's outlet into the same prompt, and a key kind
    # added to one and not the other re-opens QA's F-1 exactly. R keys carry a
    # remote-authored host as their outlet; only P keys (our own constant) are
    # outside the budget now.
    dateline = _dateline_of(s)
    stamp = f" (published {dateline})" if dateline else ""
    outlet = (clamp_map_labels(s["outlet"], _outlet_id(s))[0]
              if key[0] in "SCR" else s["outlet"])
    return f"--- [{key}] {outlet} — {s['title']}{stamp} ---\n"


def render_material(sources: Dict[str, Dict],
                    budget_chars: int = MATERIAL_BUDGET_CHARS) -> str:
    """Full texts first (the whole point), then excerpts/results, byte-capped
    so a long article can't blow the context.

    The default is the NAMED constant (ordering ruling 2026-08-01, NL-130):
    rung 1's affordability bound prices this same number, so the two rungs can
    never be priced off two different material budgets.

    P-RESERVATION (M2 gate residual 3): prior-briefing material gets a
    budget slice RESERVED before the S/R/C spend — on a many-source day the
    old shared budget exhausted before P rendered, the model could not cite
    P-keys it never saw, and the arc-integrity lint then dropped the arc
    with a misattributing disclosure. The reservation is budget, not
    position: assembly order stays S, R, C, P.

    WATER-FILL (NL-118 P0): inside both the P slice and the S/R/C remainder,
    shares are max-min fair — every entry gets min(its demand, λ), λ as large
    as the budget affords. Short entries take exactly what they need and the
    surplus pours into the long ones. Measured on brief 50's frozen corpus:
    BBC 1,371 -> 6,622 (full), PBS 1,371 -> 5,721 (full), P2 dropped -> shown,
    material block 9,554 -> 21,095 chars."""
    def _entry(key: str, share_cap: int) -> Optional[str]:
        s = sources[key]
        text = (s.get("text") or "").strip()
        if not text or share_cap <= 0:
            return None
        return _material_header(key, s) + text[:min(len(text), share_cap)]

    def _demand(key: str) -> int:
        return len((sources[key].get("text") or "").strip())

    order = sorted(sources, key=lambda k: ({"S": 0, "R": 1, "C": 2, "P": 3}
                                           .get(k[0], 9), _key_sort(k)))
    p_keys = [k for k in order if k[0] == "P" and _demand(k)]
    p_total = sum(_demand(k) for k in p_keys)
    reserve = min(p_total, budget_chars // 6)

    p_parts: List[str] = []
    if p_keys:
        overhead = (sum(len(_material_header(k, sources[k])) for k in p_keys)
                    + _MATERIAL_SEAM * (len(p_keys) - 1))
        p_demands = [_demand(k) for k in p_keys]
        if reserve - overhead > 0:
            p_shares = _water_fill(p_demands, reserve - overhead)
        else:
            # A reserve smaller than one header: admit the FIRST prior only,
            # capped at the reserve — residual 3's first-entry admission. A
            # P-key the model never sees is a P-key it cannot cite.
            p_shares = [min(p_demands[0], reserve)] + [0] * (len(p_keys) - 1)
        for key, share in zip(p_keys, p_shares):
            entry = _entry(key, share)
            if entry is not None:
                p_parts.append(entry)
    p_used = (sum(len(x) for x in p_parts)
              + _MATERIAL_SEAM * max(0, len(p_parts) - 1))

    remainder = budget_chars - p_used - (_MATERIAL_SEAM if p_parts else 0)
    src_keys = [k for k in order if k[0] != "P" and _demand(k)]
    # BUG15's contract, preserved under the new math: trim to the room that
    # actually remains rather than breaking on first overflow, so the material
    # block is never empty of article text while a fetched article exists.
    # Here the trim is the water-fill level; the drop loop below only ever
    # sheds the LOWEST-priority keys (C/R tail first — `order` is S,R,C), and
    # only when the budget cannot give a truncated source more than a sliver.
    shares: Dict[str, int] = {}
    keys = list(src_keys)
    while keys:
        overhead = (sum(len(_material_header(k, sources[k])) for k in keys)
                    + _MATERIAL_SEAM * (len(keys) - 1))
        alloc = _water_fill([_demand(k) for k in keys], remainder - overhead)
        cut = [a for k, a in zip(keys, alloc) if a < _demand(k)]
        if remainder - overhead > 0 and (not cut or min(cut) >= MATERIAL_MIN_SHARE):
            shares = dict(zip(keys, alloc))
            break
        keys.pop()

    parts = [e for e in (_entry(k, shares.get(k, 0)) for k in keys)
             if e is not None]
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

# Spec-1's NORMALIZATION PRECONDITION (content round 2026-07-31, batch B).
# Unicode FORMAT characters carry no glyph: a reader cannot see them and
# neither should the verbatim check. Brief 49 is the receipt — its source
# reads "United \u2060States maintains" (a WORD JOINER after the space), so a
# perfectly faithful quotation of it was graded a fabrication. Written as
# ESCAPES on purpose: a literal here is a line no reviewer could check.
# Symmetric on both sides, so by BUG11's own argument this can only repair a
# glyph-variant match and can never manufacture one.
_FORMAT_CHARS = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad")
_NBSP = "\u00a0"


def _norm_glyphs(s: str) -> str:
    for k, v in _GLYPHS.items():
        s = s.replace(k, v)
    for ch in _FORMAT_CHARS:
        s = s.replace(ch, "")
    return s.replace(_NBSP, " ")


# NL-118 QA finding 3 — BUG11's successor, same argument, one more class.
# BUG11 normalised whitespace, case and curly glyphs on BOTH sides because
# those differences are not fabrications. Boundary punctuation is that same
# class and was left out: American style pulls the sentence's comma INSIDE the
# closing mark, so a correctly-sourced fragment arrives as `damaged
# permanently,` against a corpus reading `...has been damaged permanently.`
# and the WHOLE BRIEF is rejected over a comma (observed live, 2026-07-30
# redraft probe run 2).
#
# Direction-safe by construction, and the asymmetry is the proof: the trim is
# applied to the CANDIDATE ONLY and never to the corpus, so it can delete
# false rejections and cannot manufacture a match for invented INTERIOR text
# — which is the fabrication the check exists to catch. Interior punctuation
# is deliberately untouched: "Mr. Modi" -> "Mr Modi" is a real edit to a
# quotation and stays a rejection.
_BOUNDARY_PUNCT = " \t\r\n,.;:!?…"


def _strip_boundary_punct(s: str) -> str:
    """Sentence furniture at a quoted fragment's two edges, removed."""
    return s.strip(_BOUNDARY_PUNCT)


_QUOTE_RE = re.compile(
    r'["\u201c]([^"\u201c\u201d]{%d,})["\u201d]' % QUOTE_MIN_CHARS)
_INLINE_KEY_RE = re.compile(r"\[([SCRP]\d+)\]")
_WORD_CH_RE = re.compile(r"[A-Za-z0-9]")


def _quoted_spans_by_family(text: str, min_chars: int = 0
                            ) -> Tuple[List[str], List[str]]:
    """(double-quoted spans, single-quoted spans) — see `_quoted_spans`.

    The families are returned apart for reporting and for the one asymmetry
    that remains: since Spec-1's promotion (2026-07-31) both families are
    enforced alike by marks-lawfulness in every reader-reachable field, and
    the split only matters in the exempt `notes_for_writer` channel, where a
    single-quoted non-match is disclosed while a double-quoted one keeps its
    hard reject (the Gate residual 2 ordered pin) — see `check_quotes`.
    """
    dbl, sgl = _scan_quoted(text)
    return ([q for q in dbl if len(q) >= min_chars],
            [q for q in sgl if len(q) >= min_chars])


def _quoted_spans(text: str, min_chars: int = 0) -> List[str]:
    """Every quoted run in `text`, BOTH delimiter families.

    NL-118 fix leg, found while repairing the eval's D5 checker: `_QUOTE_RE`
    matches only the double-quote family, and the analyst writes its briefs as
    JSON string values \u2014 where a single mark needs no escaping and is
    therefore what the model reaches for. So `check_quotes` iterated an EMPTY
    list on essentially every real brief and the verbatim promise was never
    tested. Measured on the founder DB (read-only, 2026-07-30): roughly 90 quoted
    spans across the 43 persisted briefs; the old regex saw 7 (the family
    split is field-set-dependent, so counts are approximate by design). The
    spans it never saw include real, correctly-cited quotations the promise
    silently skipped — brief 50's Al Jazeera headline quote among them
    (real, not fabricated; gate rider R1). The gap this closes is COVERAGE.

    A plain single-quote regex does not work, which is why this is a scanner
    and not one more pattern: the apostrophe in `can't` would close the span
    early. The rule that separates a DELIMITER from an APOSTROPHE is position,
    not glyph \u2014 an apostrophe between two word characters is interior
    (`can't`, `Modi's`); one that is not is a candidate delimiter. An opener
    must additionally be followed by a word character, which is what keeps the
    plural possessive ("the parents' group") from opening a span.

    Direction: this can only find MORE quotes to check. It cannot excuse one.
    """
    dbl, sgl = _scan_quoted(text)
    return [q for q in dbl + sgl if len(q) >= min_chars]


def _scan_quoted(text: str) -> Tuple[List[str], List[str]]:
    s = _norm_glyphs(text or "")
    out: List[str] = []
    single: List[str] = []

    marks = [m.start() for m in re.finditer(r'"', s)]
    for i in range(0, len(marks) - 1, 2):
        span = s[marks[i] + 1:marks[i + 1]]
        if span.strip():
            out.append(span)

    delims = []
    for m in re.finditer(r"'", s):
        i = m.start()
        prev = s[i - 1] if i else ""
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if _WORD_CH_RE.match(prev or "") and _WORD_CH_RE.match(nxt or ""):
            continue
        delims.append(i)
    j = 0
    while j < len(delims):
        o = delims[j]
        nxt = s[o + 1] if o + 1 < len(s) else ""
        if not _WORD_CH_RE.match(nxt or ""):
            j += 1
            continue
        for k in range(j + 1, len(delims)):
            c = delims[k]
            if s[c - 1].isspace():
                continue
            span = s[o + 1:c]
            if span.strip():
                single.append(span)
            j = k + 1
            break
        else:
            break
    return out, single


_BRACKET_RE = re.compile(r"\[[^\[\]]*\]")
_BRACKET_KEEP = re.compile(r"\[([^\[\]]*)\]")
_ELLIPSIS_RE = re.compile(r"\.\.\.+|\u2026")


def _source_texts(sources: Dict[str, Dict]) -> List[str]:
    """One haystack PER SOURCE — title+text, mirroring `verbatim_corpus`'s
    prior-briefing carve (our own headlines never become quotable material).

    Per-source is the whole point of Spec-1's ellipsis rule: two true
    fragments from two different outlets joined by an ellipsis is a quotation
    of a sentence nobody wrote, and checking against the CONCATENATED corpus
    would wave it through."""
    out = []
    for key in sorted(sources, key=_key_sort):
        s = sources[key]
        parts = []
        if s.get("kind") != "prior-briefing":
            parts.append(s.get("title") or "")
        parts.append(s.get("text") or "")
        out.append(_norm_ws(_norm_glyphs(" ".join(p for p in parts if p))))
    return out


def _fragments_in_order(frags: List[str], hay: str) -> bool:
    pos = 0
    for f in frags:
        i = hay.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


def _marks_lawful(span: str, sources: Dict[str, Dict],
                  min_chars: int = QUOTE_MIN_CHARS) -> bool:
    """Spec-1 mechanics 2 and 3 — is this span lawful UNDER THE MARKS?

    Called only for a span that already failed the plain verbatim check; this
    is the additional lawfulness the canon grants, never a replacement for it.

      * BRACKETS: the bracket-content-INLINED form matches, OR the fragments
        left when bracketed segments are DELETED match in order.
      * ELLIPSIS: split on the marks; every fragment verbatim, IN SOURCE
        ORDER, within ONE source; at least one fragment >= QUOTE_MIN_CHARS so
        an ellipsis cannot shred a span into unrecognisable confetti and call
        the result a quotation.
    """
    norm = _norm_ws(_norm_glyphs(span))
    forms = [norm]
    if "[" in norm and "]" in norm:
        forms.append(_BRACKET_KEEP.sub(lambda m: m.group(1), norm))
        forms.append(_BRACKET_RE.sub(" ", norm))
    haystacks = _source_texts(sources)
    for form in forms:
        frags = [_strip_boundary_punct(_norm_ws(p))
                 for p in _ELLIPSIS_RE.split(form)]
        frags = [f for f in frags if f]
        if not frags or not any(len(f) >= min_chars for f in frags):
            continue
        for hay in haystacks:
            if _fragments_in_order(frags, hay):
                return True
    return False


def _arc_prose(arc) -> str:
    """The arc's RENDERED words, counted the way the ARC block renders them:
    the NL-63 two-clause shape (`what_happened` — `significance`) when present,
    else the legacy single-clause `what_changed` fallback. Counting fields the
    reader never sees would be as dishonest as counting none of them."""
    if not isinstance(arc, dict):
        return ""
    if arc.get("what_happened"):
        return f"{arc.get('what_happened', '')} {arc.get('significance', '')}"
    return arc.get("what_changed", "") or ""


def _prose_words(pinned, ledger_out, mechanism, effects_out, unknowns,
                 watch, arc=None) -> int:
    """The brief's prose word count — the figure EC-9's ceiling binds.

    Spec-4(c) STEP 0 (batch B): `arc` is now IN this number. It renders on the
    reader's page and was the one prose carrier the ceiling could not see, so
    ~14% of a medium budget was being spent invisibly. The tier budgets
    re-based in the same change (`WORD_BUDGETS`) so the behaviour is unmoved
    and only the measurement is honest.

    `arc` defaults to None so that a caller measuring a brief that legitimately
    has no arc reads the same number it always did — the arc is optional in the
    schema and a dropped arc must not be counted as zero-by-accident.
    """
    prose = " ".join(
        [p.get("fact", "") for p in pinned]
        + [e.get("claim", "") for e in ledger_out if not e.get("discrepancy")]
        + [mechanism]
        + [e["effect"] for e in effects_out]
        + [u.get("question", "") + " " + u.get("why_material", "")
           + " " + u.get("would_resolve", "") for u in unknowns]
        + [w.get("observable", "") for w in watch]
        + [_arc_prose(arc)])
    return len(prose.split())


def verbatim_corpus(sources: Dict[str, Dict]) -> str:
    """The material a quotation is checked against: everything we retrieved
    AND showed the model.

    It used to be the body text alone — but `render_material` puts each
    source's HEADLINE in its material header ("--- [C5] Al Jazeera — <title>
    (published ...) ---"), so the model is shown titles and quotes from them.
    Al Jazeera's 2026-07-26 headline IS a quotation ("New education minister
    can't bring my dead daughter back"), brief 50 quoted it with a citation to
    the right key, and a body-only corpus calls that fabrication. Widening the
    corpus to the titles is what keeps the single-quote fix (`_quoted_spans`)
    from converting legitimate headline quotation into lost briefs.

    Prior-briefing keys are deliberately EXCLUDED: their titles are OUR text,
    not an outlet's, and Rook's rail says the model must never get to quote
    NewsLens's own words back as retrieved material — which is exactly what
    including the X4 degrade directive here would allow.
    """
    parts: List[str] = []
    for key in sorted(sources, key=_key_sort):
        s = sources[key]
        if s.get("kind") != "prior-briefing":
            parts.append(s.get("title") or "")
        parts.append(s.get("text") or "")
    return " ".join(p for p in parts if p)


def _persisted_source_row(key: str, s: Dict) -> Dict:
    """One row of the brief's PERSISTED source table — the reader boundary.

    NL-118 QA finding 2, owned. This row is what `brief["sources"]` stores and
    therefore what every current and future renderer reads; the prompt-side
    map (`build_source_map`) is a different artifact and keeps its all-caps
    directive. The X4 degrade suffix is stripped here and re-expressed as
    reader-safe prose under `record_status`, so containment no longer depends
    on a downstream file happening to overwrite the title (server.py's NL-58
    branch, which this batch neither owns nor tests).

    Additive on the wire: rows keep every key they have ever had, and
    `record_status` appears only on a degraded prior-briefing row.
    """
    title = s["title"]
    row = {"key": key, "outlet": s["outlet"], "title": title,
           "url": s["url"], "retrieved_at": s["retrieved_at"],
           "kind": s["kind"]}
    # NL-127: the reader's half of DEEPEN. Additive, and present only on a row
    # that was actually deepened, so no historical row's meaning moves and no
    # `kind` comparison anywhere downstream changes shape.
    if s.get("deepened"):
        row["deepened"] = True
    if title.endswith(DEGRADE_TITLE_SUFFIX):
        row["title"] = title[:-len(DEGRADE_TITLE_SUFFIX)]
        row["record_status"] = DEGRADE_RECORD_STATUS
    return row


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


def _resolve_near(month: int, day: int, base: datetime) -> Optional[datetime]:
    """Resolve a bare month+day to the calendar year that lands it NEAREST the
    edition date (a bare 'July 12' in a July edition is this year's; a bare
    'January 3' read in July is next January, not seven months back)."""
    best = None
    for yr in (base.year - 1, base.year, base.year + 1):
        try:
            d = datetime(yr, month, day)
        except ValueError:
            continue
        if best is None or abs((d - base).days) < abs((best - base).days):
            best = d
    return best


_YEAR_TAIL_RE = re.compile(r"^\s*,?\s*(\d{4})\b")
_STALE_PAST_WINDOW_DAYS = 90


def _sentence_has_stale_date(sentence: str, base: datetime) -> bool:
    for m in _MONTH_DAY_RE.finditer(sentence or ""):
        month, day = _MONTHS[m.group(1).lower()], int(m.group(2))
        year_m = _YEAR_TAIL_RE.match(sentence[m.end():])
        if year_m:
            # An explicit year is the writer's own referent — resolved
            # verbatim, never nearest-year (gate fix, QA item-4 edge b).
            try:
                d = datetime(int(year_m.group(1)), month, day)
            except ValueError:
                continue
            if d.date() < base.date():
                return True
            continue
        d = _resolve_near(month, day, base)
        # Asymmetric window (gate fix, QA item-4 edge a): in forward-looking
        # text a bare month-day months behind the edition means NEXT year,
        # not a stale reference — only the recent-past window strips.
        if (d is not None and d.date() < base.date()
                and (base - d).days <= _STALE_PAST_WINDOW_DAYS):
            return True
    return False


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def strip_stale_watch(text: str, edition_date: str) -> Tuple[str, List[str]]:
    """NL-68 item 4: forward-looking watch / what-could-follow material carrying
    a month-name+day date EARLIER than the edition date is a defect (the live
    07-14 'talks on July 12' shipped in the July-14 edition). Drop any SENTENCE
    whose date resolves to before the edition; keep the rest. Best-effort by
    design — dateless or unparseable text is returned untouched (no false teeth,
    per the dispatch). Returns (cleaned_text, [stripped sentences])."""
    try:
        base = datetime.strptime((edition_date or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return text, []
    if not text or not text.strip():
        return text, []
    kept, stripped = [], []
    for s in _SENTENCE_SPLIT_RE.split(text.strip()):
        (stripped if _sentence_has_stale_date(s, base) else kept).append(s)
    if not stripped:
        return text, []
    return " ".join(kept).strip(), stripped


_NUM_TOKEN_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
                "k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9, "tn": 1e12}


def _numeric_referents(value: str) -> frozenset:
    """The set of numeric magnitudes a discrepancy side actually asserts, scale-
    words applied ('1.2 billion' -> 1.2e9, '$1,200' -> 1200). Empty when the side
    carries no number. Rounding to 4 significant figures folds '19.8' and '19.80'
    but keeps genuinely different figures apart."""
    text = (value or "").lower()
    out = set()
    for m in _NUM_TOKEN_RE.finditer(text):
        try:
            n = float(m.group(0).replace(",", ""))
        except ValueError:
            continue
        tail = text[m.end():m.end() + 12]
        for word, mult in _SCALE_WORDS.items():
            if re.match(r"\s*(?:%s)\b" % re.escape(word), tail):
                n *= mult
                break
        out.add(float(f"{n:.4g}"))
    return frozenset(out)


_NUMERIC_NOISE = frozenset(_SCALE_WORDS) | {
    "percent", "pct", "about", "approximately", "approx", "around", "roughly",
    "nearly", "circa", "est", "estimated", "some"}


def _residual_words(value: str) -> Tuple[str, ...]:
    """The meaning-bearing words of a discrepancy side once its numbers, scale
    words, units and pure hedges are removed — what remains distinguishes '20%
    up' from '20% down' (residual 'up' vs 'down') while '20%' and 'about 20
    percent' collapse to the same empty residual."""
    text = _NUM_TOKEN_RE.sub(" ", (value or "").lower())
    return tuple(sorted(w for w in re.findall(r"[a-z]+", text)
                        if w not in _NUMERIC_NOISE))


def same_referent_numbers(a_val: str, b_val: str) -> bool:
    """NL-68 item 5 (raise the discrepancy bar): two 'contested' figures that are
    really the SAME number worn differently — '20%' vs 'about 20 percent',
    '$1.2B' vs '$1.2 billion', '1,200' vs '1200' — are paraphrase/rounding noise,
    not a substantive contradiction. True only when BOTH sides carry exactly ONE
    number each (BUG-36: multi-number sides can swap number–noun pairings —
    '20 dead, 50 injured' vs '50 dead, 20 injured' — which set-comparison cannot
    see; multi-referent sides always keep the row), the numeric magnitudes match
    exactly (4-sig-fig rounding folded), AND the residual meaning-bearing words
    are identical — so '20% up' vs '20% down' (same number, opposite direction)
    and '20% closed' vs '20% open' are NEVER folded.
    Conservative by construction: any doubt keeps the row."""
    a_nums, b_nums = _numeric_referents(a_val), _numeric_referents(b_val)
    if len(a_nums) != 1 or len(b_nums) != 1 or a_nums != b_nums:
        return False
    return _residual_words(a_val) == _residual_words(b_val)


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
                   corpus: str, briefing_date: str = "",
                   ceiling_factor: float = None) -> Tuple[Dict, List[str]]:
    """Returns (clean brief with computed furniture, warnings). Raises
    BriefRejected for the hard classes: missing sections, fabricated
    citation keys, quotes that aren't verbatim substrings of retrieved
    material, an uncitable pinned-facts section.

    `ceiling_factor` exists for exactly ONE caller — Spec-4(c) step 2's
    disclosure band, which re-validates an already-redrafted brief against
    budget x DISCLOSURE_BAND_FACTOR. It defaults to None (meaning
    WORD_CEILING_FACTOR) so no other call site can widen the ceiling by
    accident, and every other check in this function is unchanged by it."""
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

    def verbatim(q: str) -> Tuple[bool, bool]:
        """(matches, matched_only_after_trimming_boundary_punctuation)."""
        norm = _norm_ws(_norm_glyphs(q))
        if norm in corpus_norm:
            return True, False
        # NL-118 QA finding 3: boundary punctuation is not fabrication.
        # Candidate side only (see _strip_boundary_punct).
        trimmed = _strip_boundary_punct(norm)
        return (bool(trimmed) and trimmed in corpus_norm), True

    def check_quotes(text: str, where: str, exempt: bool = False) -> None:
        # ONE loop, BOTH families, enforced by MARKS-LAWFULNESS. The
        # editorial call the old comment here deferred has been MADE:
        # content round 2026-07-31 (Spec-1), canon + D4 ratified by the
        # principal the same day. History, kept because it explains the
        # shape: `_QUOTE_RE` only ever saw double quotes (7 spans across 43
        # founder briefs where the scanner sees 92), and hard-rejecting
        # everything the scanner newly saw would have destroyed 5 real
        # briefs whose spans carry standard editorial marks (`maintain[s]`,
        # `but... advancing`). The canon separates the two populations the
        # old policy could not: marked-and-faithful spans are LAWFUL in both
        # families (`_marks_lawful`), unmarked alteration REJECTS in both —
        # as `BriefQuoteUnmarked`, one instructed redraft, never the bare
        # raise the round refused. The one asymmetry left is the narrowed
        # `exempt` channel below (see the pin comment inside this loop).
        dbl, sgl = _quoted_spans_by_family(text or "", QUOTE_MIN_CHARS)
        for q, family in ([(x, "double") for x in dbl]
                          + [(x, "single") for x in sgl]):
            ok, trimmed = verbatim(q)
            if ok:
                if trimmed:
                    warnings.append(
                        f"quote in {where} matched after trimming boundary "
                        f"punctuation: \"{q[:60]}\" (BUG11 boundary rule — "
                        "interior text is verbatim)")
                continue
            if _marks_lawful(q, sources):
                continue          # canon (a)/(b): a DISCLOSED adjustment
            # SPEC-1 vs AN ORDERED GATE PIN — the pin wins, minimally.
            # Spec-1 scope says "`notes_for_writer` exempt from reject". The
            # Gate residual 2 ordered pin says the opposite in this file's own
            # words at the call site below: notes_for_writer "cannot stay
            # quote-exempt", because it flows into writer material where the
            # fact-subset chain treats it as given. A spec clause does not
            # silently un-pin a gate-ordered assertion, so the exemption is
            # narrowed to the MINIMUM that satisfies both: it covers the
            # SINGLE family — the one Spec-1's promotion newly enforces, and
            # the one its own acceptance case lives in (founder brief 40's
            # span is single-quoted; it carries no double-quoted span at all).
            # The DOUBLE family keeps the hard reject it already had here.
            # Nothing Spec-1 asked for is lost: marks-lawfulness still governs
            # both families in every field. Flagged for the gate to ratify or
            # reverse.
            if exempt and family == "single":
                warnings.append(
                    f"single-quoted material in {where} is not a verbatim "
                    f"substring of retrieved material: '{q[:60]}' — "
                    "DISCLOSED, not rejected (internal channel, never "
                    "rendered to the reader)")
                continue
            raise BriefQuoteUnmarked(q, where)

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
            # NL-68 item 5 (raise the bar): a figure and its paraphrase/rounding
            # restatement ('20%' vs 'about 20 percent') is not a contradiction —
            # drop it here so new briefs never persist the noise (the render-side
            # guard cleans already-persisted editions).
            if same_referent_numbers(a_val, b_val):
                warnings.append(
                    f"false discrepancy dropped: {a_val!r} and {b_val!r} "
                    "restate the same figure (NL-68 item 5 same-referent rule)")
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

    # watch: EC-1 (NL-118 item 5) — `watch` was the only forward-looking array
    # with no citation slot, and 102/102 watch items across the whole DB were
    # uncited by construction: the field had nowhere to put a receipt. It has
    # one now, and an item that cannot be keyed is DROPPED, not softened —
    # the identical rule `effects` has carried since 2026-07-06. `basis` rides
    # the same three-value enum and is validated, not yet enforced (the drop
    # rail the dispatch names is cites; a basis rule would reach past it).
    watch_in = [w for w in (raw.get("watch") or []) if isinstance(w, dict)]
    watch: List[Dict] = []
    watch_dropped = 0
    for i, w in enumerate(watch_in):
        _require_str(w.get("observable", ""), f"watch {i+1} observable")
        check_quotes(w.get("observable", ""), f"watch {i+1} observable")
        if isinstance(w.get("settles"), str):
            check_quotes(w["settles"], f"watch {i+1} settles")
        cites = _cites_of(w)
        if not cites:
            watch_dropped += 1
            continue
        check_cites(cites, f"watch {i+1}")
        basis = str(w.get("basis") or "").strip()
        if basis and basis not in ALLOWED_EFFECT_BASES:
            warnings.append(f"watch {i+1} basis {basis!r} outside the "
                            "attributed/mechanical/historical-pattern "
                            "vocabulary — carried, flagged")
        out_w = {"observable": w.get("observable", ""),
                 "settles": w.get("settles", ""), "cites": cites}
        if basis:
            out_w["basis"] = basis
        watch.append(out_w)
    if watch_dropped:
        warnings.append(
            f"watch citation enforcement (EC-1): dropped {watch_dropped} "
            "uncited watch item(s) — a forward-looking claim with no receipt "
            "is a prediction in our own voice")
    if not (2 <= len(watch) <= 4):
        warnings.append(f"watch count {len(watch)} outside the 2-4 band")

    # word budget — EC-9 (NL-118 item 6). The ceiling used to WARN and the
    # warning was not even persisted, so brief 50 ran 636 words against a 400
    # budget (+59%) and nothing happened. Over budget still only warns (the
    # budget is a target, and a 410-word brief is not a defect); over
    # budget × WORD_CEILING_FACTOR raises, and `analyze_story` turns that into
    # ONE retry carrying the content round's instruction — come in short, cut
    # restatement, never specifics.
    # Step 0: `arc` is passed here AFTER the arc-drop/verdict block above, so a
    # dropped arc (uncited — set to None there) is correctly counted as zero
    # words. The brief is measured as it will RENDER, not as it arrived.
    words = _prose_words(pinned, ledger_out, mechanism, effects_out,
                         unknowns, watch, arc)
    budget = word_budget_for(tier)   # NL-131 structural kill (was `.get(tier, 450)`)
    ceiling = int(budget * (WORD_CEILING_FACTOR if ceiling_factor is None
                            else ceiling_factor))
    if words > ceiling:
        raise BriefOverCeiling(words, budget, ceiling)
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
    for w in watch:                      # EC-1: watch now carries receipts
        collect(w["cites"])
    if arc:
        collect(_cites_of(arc))
    # Gate residual 2 tail: notes_for_writer flows into writer material
    # where the fact-subset chain treats it as given — it cannot stay
    # quote-exempt.
    notes = str(raw.get("notes_for_writer") or "")[:300]
    # Spec-1 SCOPE, as narrowed by the pin above: `notes_for_writer` never
    # renders to the reader, so its SINGLE-quoted misses stay disclosed-not-
    # rejected and never cost a brief (worked span: founder brief 40). A
    # DOUBLE-quoted fabrication here still rejects — Gate residual 2. Every
    # reader-reachable prose field — including `arc` — is enforced.
    check_quotes(notes, "notes_for_writer", exempt=True)

    source_table = [_persisted_source_row(k, sources[k])
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

# B4 prompt caching: analysis_brief.txt is [static instructions] then [per-slot
# data]. Everything before this line is byte-identical across EVERY analyst call
# (every slot, every edition) — the cleanest "shared-briefs prefix" the transcript
# names: an edition's 6-7 slot calls run seconds apart, so slots 2..N read the
# cached instruction block within the 5-minute TTL. Split there for the anthropic
# seat; caching engages only once the prefix clears Sonnet's 2,048-token cache
# minimum (measured on live/battery runs, per "measured, not assumed").
_ANALYST_CACHE_SENTINEL = "\nWord budget for all prose fields combined:"


# FIX-1 (B4-D1, the ranking._ACTIVE_RANK twin): the analyst seat is resolved
# through effective_seat ONCE per analysis operation and threaded to EVERY
# reader — _analysis_chat's TRANSPORT, call_analysis_model's cost_fields ledger,
# and run_analysis's report-lane label — via this stage-scoped global. Before
# this, _analysis_chat re-resolved (resolve_seat) independently of the gate/
# cost_fields resolution: a `claude` binary that flapped mid-stage (a CLI
# reinstall/upgrade) could FORK the transport lane from the ledger/report lane —
# the D1 lie via a new door, now that the analyst is a Claude seat (B4). One
# resolution closes it: every reader rides the same (cfg, reason), so a flap
# dies loud or is labeled truthfully, never silently. Published by the OUTERMOST
# of generate's stage-entry preflight / run_analysis / call_analysis_model;
# nested calls reuse it (own-scope teardown).
_ACTIVE_ANALYST: Optional[Tuple["llm.SeatConfig", Optional[str]]] = None


def _effective_analyst() -> Tuple["llm.SeatConfig", Optional[str]]:
    """The active (cfg, fallback_reason) for the current analysis operation —
    the stage-scoped resolution if one is published, else a fresh effective_seat
    (a direct _analysis_chat outside call_analysis_model — the signature-test
    path; ranking._effective_rank's twin)."""
    if _ACTIVE_ANALYST is not None:
        return _ACTIVE_ANALYST
    return llm.effective_seat("analyst")


def _publish_analyst() -> Tuple["llm.SeatConfig", Optional[str]]:
    """Resolve the analyst seat ONCE (effective_seat = gate + the principal-armed
    fall) and publish it for the whole stage. Raises LaneUnavailable on a
    misconfig — the stage-entry kill FIX-1 (B3) preserved, now via the SAME
    resolution the transport rides. The caller owns teardown (_clear_analyst)."""
    global _ACTIVE_ANALYST
    _ACTIVE_ANALYST = llm.effective_seat("analyst")
    return _ACTIVE_ANALYST


def _clear_analyst() -> None:
    global _ACTIVE_ANALYST
    _ACTIVE_ANALYST = None


def _analysis_chat(key: str, prompt: str) -> Dict:
    """One-retry synthesis call on the ANALYSIS_MODEL seam. Transport delegates
    to the provider seam (llm.py): analyst seat = Claude OPUS 4.8 / subscription
    (api is the registered fall-over) / timeout 240s api, 720s subscription
    (llm.SEATS["analyst"]), adaptive thinking at effort high. temperature is
    OMITTED by the provider (Opus 4.8 rejects it with a 400 — sampling=False; the
    0.2 passed here is ignored, exactly as it was under Sonnet 5). Returns the
    OpenAI-shaped .raw so call_analysis_model's parse/retry law is untouched.
    Keeps its signature: it is a monkeypatch target.

    ENG-M0 (2026-08-06): the seat moved Sonnet 5 -> Opus 4.8. Nothing in THIS
    function changed — that is the derive-from-SEATS contract working: model,
    prices, thinking, effort, sampling and both timeouts all arrive from the seat
    row, so the analyst's out-rate $15 -> $25 (and the brief bound $0.149 ->
    $0.248 that follows from it) needed no edit here.

    B4: the static instruction block rides a cache_control system prefix (the
    per-slot data stays the volatile user prompt); openai (a revert) sends the
    whole prompt as one user message unchanged (provider-gated split).

    FIX-1 (B4-D1): the cfg is the stage's ONE published resolution
    (_effective_analyst), never a fresh resolve_seat — so the transport rides the
    SAME seat the gate checked and the ledger/report lane record."""
    cfg, _ = _effective_analyst()
    system = None
    prompt_body = prompt
    if cfg.provider == "anthropic":
        idx = prompt.find(_ANALYST_CACHE_SENTINEL)
        if idx > 0:
            system, prompt_body = prompt[:idx], prompt[idx:]
    return llm.chat(
        llm.LaneRequest(
            cfg=cfg,
            prompt=prompt_body,
            temperature=0.2,
            max_tokens=ANALYSIS_MAX_TOKENS,
            json_mode=True,
            user_agent=ANALYSIS_UA,
            api_key=key,
            system=system,
        )
    ).raw


def call_analysis_model(key: str, prompt: str) -> Tuple[Dict, float, float]:
    """(parsed JSON, usd_CHARGED, usd_SHADOW). One retry on network/parse
    failure, then raises — the caller's ladder turns that into a disclosed
    no-brief.

    NL-95 (Stage-0 M2): DUAL-TRACK, not a meaning flip. The two figures answer
    two different questions and both callers need both:
      * usd_charged is REAL MONEY. It is what lands in analysis_briefs.cost_usd
        and thread_baselines.cost_usd, and what the failed-run money record
        sums. On the subscription lane it is 0.00.
      * usd_shadow is what the run WOULD cost at api prices, and it is what
        edition-scoped CAPS bind (Onna's law). On the subscription lane it is
        the only non-zero figure — so a cap that decremented by charged never
        decremented at all, and the whole degradation ladder was inert.
    Flipping the single return to shadow was rejected precisely because the
    same float is persisted downstream as charged money; both are returned so
    neither consumer has to guess.

    BUG13 applies to BOTH accumulators: an attempt that completed HTTP (tokens
    paid) and then failed truncation/parse accumulates charged AND shadow, and
    the log must carry real spend (BUG-6 money-honesty class).

    B2 (dispatch item 4): both accumulators DERIVE from ONE
    llm.cost_fields(cfg, usage) call per attempt — not the module
    ANALYSIS_USD_* constants — so the analyst cost path re-prices
    automatically and the two figures can never be computed from different
    resolutions. B4: the analyst is Claude Sonnet 5 (SEATS["analyst"]).

    FIX-1 (B4-D1): the ONE resolution the transport, this cost_fields ledger, and
    run_analysis's report lane all ride is published on _ACTIVE_ANALYST
    (effective_seat = gate + armed fall). When a stage (generate/run_analysis)
    already published it, this reuses it; a direct call owns + tears down its own
    scope. The fallback_reason rides cost_fields so a fallen row is labeled
    api(fallback:…). The RETURN ARITY moved at NL-95 (2 -> 3); the deliberate
    re-pin lives at tests/test_b1_llm_seam_qa.py, and it is still a monkeypatch
    target (ADR-0014 §2, test_signatures_preserved)."""
    global _ACTIVE_ANALYST
    _own = _ACTIVE_ANALYST is None
    if _own:
        # Direct call (no stage scope): resolve ONCE here — the fail-loud gate
        # (effective_seat raises LaneUnavailable on a misconfig, before any
        # transport/retry) and the published resolution both attempts + the
        # cost ledger ride (transport-seat == ledger-seat, the D1 close).
        _publish_analyst()
    analyst_cfg, analyst_fb = _ACTIVE_ANALYST
    last: Exception = RuntimeError("unreachable")
    total_charged = 0.0
    total_shadow = 0.0
    try:
        for attempt in (1, 2):
            try:
                payload = _analysis_chat(key, prompt)
                usage = payload.get("usage") or {}
                # ONE cost_fields call feeding BOTH accumulators — two calls
                # could disagree if the resolution moved between them.
                cf = llm.cost_fields(
                    analyst_cfg, usage, fallback_reason=analyst_fb)
                total_charged += cf["usd_charged"]
                total_shadow += cf["usd_shadow"]
                choice = payload["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise ValueError(f"truncated at {ANALYSIS_MAX_TOKENS} tokens")
                return (json.loads(choice["message"]["content"]),
                        total_charged, total_shadow)
            except Exception as exc:  # noqa: BLE001 — one retry for the whole class
                last = exc
                if attempt == 1:
                    time.sleep(1.0)
        raise last
    finally:
        if _own:
            _clear_analyst()


def estimate_synthesis_usd_chars(prompt_chars: int) -> float:
    """The synthesis estimate as a function of PROMPT LENGTH alone.

    Split out by the ordering ruling 2026-08-01 (NL-130) so rung 1 can price a
    prompt that does not exist yet — the bound is a char count — through the
    exact same arithmetic rung 2 prices the real prompt with. One estimator,
    two call shapes: that identity is what makes `bound >= est` a property of
    char counts rather than a coincidence of two formulas."""
    est_in = prompt_chars / 4
    return (est_in / 1e6 * ANALYSIS_USD_IN_PER_MTOK
            + ANALYSIS_MAX_TOKENS / 1e6 * ANALYSIS_USD_OUT_PER_MTOK)


def estimate_synthesis_usd(prompt: str) -> float:
    return estimate_synthesis_usd_chars(len(prompt))


def brief_bound_chars(template: str) -> int:
    """Call-time UPPER BOUND on the analysis prompt this template can build.

    template + the material block's hard budget + the measured margin for
    everything else (source map, memory_context, scalars). `template` is READ
    AT CALL TIME, never pinned: the prompt file is principal-editable with no
    test run between his edit and the next generate, so any static length is a
    stale line waiting to happen (the 4.33-char margin the NL-118 land
    measured was that class expressing itself, not bad luck)."""
    return len(template) + MATERIAL_BUDGET_CHARS + PROMPT_MARGIN_CHARS


def brief_bound_usd(template: str) -> float:
    """What rung 1 must be able to afford before it may spend on Sonar."""
    return estimate_synthesis_usd_chars(brief_bound_chars(template))


def clamp_sonar_results(results: List[Dict]) -> Tuple[List[Dict], int, int]:
    """NL-139 (NL-133 gate R-B): the vendor byte-clamp. Returns
    (kept, n_title_truncated, n_dropped_bad_host).

    Sonar's `search_results` are a remote party's bytes reaching a prompt whose
    length is a MONEY guard (see PROMPT_MARGIN_CHARS). The count clamp has
    always been here; this is the length half. Public rather than private so
    the arithmetic pin can drive it directly with a hostile payload — the
    clamp is the thing being proven, and proving it through a live Sonar call
    is not a test anyone can run.

    Order matters: the count clamp applies FIRST, so a vendor cannot spend our
    per-result budget on results 9..N and it cannot push a good result out of
    the window by padding earlier ones with junk hosts. That means a dropped
    bad-host result costs a slot rather than promoting the next one — the
    conservative direction for a bound, and it keeps the returned count an
    honest report of what the vendor actually gave us in-window."""
    kept: List[Dict] = []
    truncated = dropped = 0
    for res in results[:SONAR_MAX_RESULTS]:
        url = (res.get("url") or "").strip()
        if len(urlparse(url).netloc) > SONAR_HOST_MAX_CHARS:
            # Not a hostname (the DNS octet limit is 253) — see the constant.
            dropped += 1
            continue
        title = res.get("title") or ""
        if len(title) > SONAR_TITLE_MAX_CHARS:
            truncated += 1
        snippet = res.get("snippet") or ""
        kept.append({**res, "url": url,
                     "title": title[:SONAR_TITLE_MAX_CHARS],
                     "snippet": snippet[:SONAR_SNIPPET_MAX_CHARS]})
    return kept, truncated, dropped


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
    # NL-139: count clamp + byte clamp, in that order (see clamp_sonar_results).
    kept, truncated, dropped = clamp_sonar_results(results)
    # NL-142 rider R-E-3: a result with NO URL was counted here and then
    # silently skipped by `build_source_map` ("if not url: continue"), so
    # `ok — N results` over-reported, and — the part that is not cosmetic —
    # the slot-3 demotion gate reads that same count (`len(sonar_results) < 2`
    # in analyse_slot), so an empty-URL result could hold a slot at medium on
    # material the model never received. Dropped here, at the one place that
    # owns the count and the status line. The MAP is byte-identical either way,
    # which is what makes this safe to do at the count rather than at the map.
    #
    # THE READER-VISIBLE HALF, as a CONSCIOUS FLIP rather than a side effect
    # (QA F-5, fix loop 1): this changes a TIER in the founder's edition, not
    # just a status string. A slot 3 whose two Sonar results included one
    # URL-less result used to be written as a MEDIUM brief and is now
    # DEMOTED TO QUICK. That is the correct direction — `build_source_map`
    # always skipped the URL-less result (`if not url: continue`), so the
    # material behind the medium tier was never there — but it is a change the
    # reader sees, and it is taken deliberately. Pinned END-TO-END, both arms,
    # through this function rather than an injected seam:
    # `test_nl142_qa_findings.py::
    # test_the_empty_url_drop_flips_slot_three_from_medium_to_quick`.
    no_url = [r for r in kept if not (r.get("url") or "").strip()]
    if no_url:
        kept = [r for r in kept if (r.get("url") or "").strip()]
    # Honest degradation rides the channel this function already has — the
    # status string, persisted on the brief header (`sa.sonar_status`) and
    # rendered in the run report. A DROPPED result is named because a
    # verification source went missing; a truncated TITLE is named as a count
    # only, because the shortened string is model-facing map furniture and
    # naming each one would put vendor noise in a repairs channel the reader's
    # edition inherits.
    note = ""
    if dropped:
        note += (f", {dropped} dropped — URL host over "
                 f"{SONAR_HOST_MAX_CHARS} chars")
    if no_url:
        note += f", {len(no_url)} dropped — no URL (never citable)"
    if truncated:
        note += f", {truncated} title(s) truncated at {SONAR_TITLE_MAX_CHARS}"
    return kept, cost, f"ok — {len(kept)} results{note}"


# ===========================================================================
# NL-127 DEEPEN — the R# lane (principal's ruling 2026-08-24)
# ===========================================================================
#
# WHAT THIS IS, and what it deliberately is NOT.
#
# The ratified 2026-07-31 design said DEEPEN would "fetch full text of URLs the
# corpus already holds but never fetched", over the CLUSTER rows. The 2026-08-14
# scout falsified that premise against this file: `_cluster_items_for_slot`
# returns every `item_ids` entry and `fetch_cluster_articles` walks all of them,
# so the pipeline already ATTEMPTS every cluster URL it holds. That stage is
# DEAD and is not built here.
#
# The genuinely never-fetched pool is the R# rows minted below by
# `build_source_map`: a Sonar `search_results` entry reaches the analyst as its
# ≤303-char vendor SNIPPET and nothing has ever opened the page. Measured on the
# principal's own DB (mode=ro, editions ≥ 2026-08-01): 322 R# rows over 80
# distinct hosts, mean text 279 chars, and the long tail — independent.co.uk,
# kyivindependent.com, elpais, jpost, themoscowtimes, wsj — is outlets his feed
# set does not carry at all. That is the "stop discarding paid answers" rider,
# and it is the ONLY surviving lane. His ruling 2026-08-24 (DECISIONS, item 2)
# authorized exactly it.
#
# THE TIER LAW IS HIS, VERBATIM: "Sonar-result fetches obey the SAME outlet-tier
# law as cluster fetches". That is why the refusal below is not a new rule but a
# call into `fetch_article`, which returns TIER_EXCLUDED with the 2026-07-06
# ruling named in its own detail string. A Reuters/NYT/AP/Wikipedia/Bloomberg/
# WaPo/FT/Economist R# row is refused by the same line that refuses a cluster
# item, and the refusal is RECORDED (see the ledger) so "they stayed
# unfetchable" is a receipt rather than a claim.
#
# WHAT THE ROW COSTS: $0 in model spend — no seat, no Sonar, no prompt. One
# network GET per surviving URL, single attempt. Bucket-B measured 34 in-run
# retries rescuing 0 outcomes (research/2026-08-24--nl127-bucketB.md), so there
# is no retry ladder here and building one would be building against measurement.
#
# WHY IT CANNOT MOVE THE PROMPT BOUND, which is a MONEY guard (NL-133/139/142).
# DEEPEN replaces one field — an R key's `text` — and nothing else. It does not
# touch the R title (NL-139's vendor clamp), the R outlet (a URL host inside
# NL-142's label budget), or the `kind` string (rendered on every source-map
# line AND compared by exact equality at server.py:4693/5278). `text` reaches
# the prompt only through `render_material`, which water-fills the whole block
# into MATERIAL_BUDGET_CHARS — the property `SONAR_SNIPPET_MAX_CHARS` is already
# derived from ("no snippet of any length can add one rendered char past that").
# The deepened text is clamped to that same constant on substitution, so the
# HELD/PERSISTED bound is unchanged too. `brief_bound_chars` is therefore
# byte-identical with DEEPEN on and off, and the 105 chars of margin slack the
# scout measured — the slack his [:9] ruling declined to spend — is not touched.

DEEPEN_ARM_ON = "on"
DEEPEN_ARM_OFF = "off"
# NOT an env var (none may land without his checkpoint) and not config: a module
# constant the pins monkeypatch, so both arms are exercised at $0. Same form as
# FETCH_SKIP_ARM above, for the same reason.
#
# THIS LANE SHIPS INERT (gate ruling R-1, 2026-08-24), on NL-151's precedent at
# :1165 above — "that is why NL-151 shipped OFF" — which is the directly
# on-point case: a built, pinned, measured lane landed OFF and was armed later
# by his own explicit word. The arm is HIS fork and nobody else's, because
# armed-at-commit means the committed bytes open live network GETs on his very
# next generate before he has spoken. OFF is not a stub: `deepen_sonar_results`
# returns at `deepen_armed()` before it reads a single result, which is why the
# OFF arm is byte-identical to the pre-NL-127 tree (proven twice by independent
# three-world probes, QA + gate §A.4). Arming it is THIS ONE LINE plus one
# suite leg. Every pin that exercises the lane arms itself explicitly (the
# `armed` fixture in tests/test_nl127_deepen.py), so this default is the
# shipped default in every test that does not ask for the other one.
# ARMED 2026-08-25 on his word ("arm deepen", DECISIONS same date) — the one
# line the inert ship was built for. From this commit his generates open ~2
# real $0 GETs/edition on trigger-firing stories (honest UA, robots-honored,
# single attempt, receipts in the brief header + generation_log + deep view).
DEEPEN_ARM = DEEPEN_ARM_ON

# Network attempts per fired story. Bucket-B's OK fetches ran 1,254–19,368 chars
# (median ~3,300), so three pages is ~10k chars of prose against a story that
# fired only because it holds under 14,400 — enough to fill the deficit, not
# enough to displace the cluster's own reporting. Wall clock ~3s/attempt plus
# 1s politeness, with a rare one-off 15s robots tarpit per hostile host.
DEEPEN_MAX_URLS = 3
# THE DEFICIT DENOMINATOR IS DERIVED, not chosen: below the material budget the
# block cannot be filled with real prose at all, so 8 vendor stubs are occupying
# room a page could hold; above it the budget is already oversubscribed and a
# new full text only displaces reporting we already have.
DEEPEN_TEXT_FLOOR = MATERIAL_BUDGET_CHARS
# IMPORTANCE = the depth tier, which IS the editorial judgement the ranker
# already made and the one value `analyze_story` is handed. A tier absent here
# scores 0 and can never fire (today `analyze_story` only ever runs at
# full/medium; L3 could one day analyse In-Brief stories, and this is the guard
# that keeps DEEPEN out of that tier until someone rules it in).
DEEPEN_TIER_WEIGHT = {"full": 1.0, "medium": 0.5}
# THE ONE CALIBRATED CONSTANT IN THIS BLOCK — disclosed as such. The charter
# says "importance x deficit trigger WITH REAL GATING (the experiment's encoding
# reduces to deficit-only — don't repeat it)". Real gating means each conjunct
# must be able to block a story the other would pass, and it is checked against
# HIS corpus, not against intuition. At 0.40 a `full` story fires on deficit
# ≥ 0.40 (holding < 14,400 chars) and a `medium` story needs ≥ 0.80 (holding
# < 4,800). Measured on editions 2026-08-01..24 (42 briefs, analysis_retrieval,
# mode=ro), the separating pair is real and adjacent:
#   2026-08-03 slot 1 full   held 10,202  deficit 0.575  -> FIRES
#   2026-08-06 slot 3 medium held  9,989  deficit 0.584  -> DOES NOT
# Two all-but-identical deficits, opposite verdicts, decided by importance
# alone: that is the property the experiment's encoding lacked. The deficit
# conjunct bites in the other direction on the same corpus (2026-08-01 slot 1,
# full, held 38,200 -> 0.0 -> does not fire). Firing rate over the window:
# 3/14 full-tier and 4/28 medium-tier stories, ~0.5 stories per edition.
DEEPEN_TRIGGER = 0.40

# Non-network dispositions, kept OUTSIDE the `OUTCOMES` vocabulary on purpose:
# that tuple is the fetch layer's closed contract and `fetch_stats` counts over
# it. These three name decisions taken BEFORE the fetch layer is asked, and they
# only ever appear in the DEEPEN ledger.
DEEPEN_SKIP_HELD = "skipped-already-held"
DEEPEN_SKIP_SHAPE = "skipped-url-shape"
DEEPEN_SKIP_BUDGET = "skipped-url-budget"

# The tier an R# host resolves to when no source in sources.yaml claims it —
# the SAME default `_cluster_items_for_slot` applies to an unrecognised cluster
# outlet (`tier_by_outlet.get(r["outlet"], "full")`). Stated as a constant
# because it is the load-bearing half of "the same outlet-tier law": most Sonar
# results are outlets he does not subscribe to, and an allowlist reading would
# have reduced the fetchable pool from ~224 rows to ~76 — nearly all of them on
# the hosts Bucket-B measured as deterministically dead.
DEEPEN_UNKNOWN_TIER = "full"

# Public-suffix guard for `_registrable`. Without it `feeds.bbci.co.uk` and
# `independent.co.uk` both collapse to "co.uk" and one outlet inherits the
# other's tier. It is NOT a complete PSL, and here is what that actually costs,
# stated as the machine behaves rather than as the first draft of this comment
# claimed (it said an unlisted suffix "fails to shorten, the host stays whole";
# QA falsified that against the function itself, 2026-08-24 F-6):
#
#   `_registrable("feeds.dawn.com.pk")` -> "com.pk"
#
# An UNLISTED multi-part suffix DOES shorten — to the two-label tail, which is
# the suffix itself. `outlet_tiers_by_host`'s guard below rejects only LISTED
# suffixes, so a restricted source whose feed sits on one of these mints a
# SUFFIX-WIDE index key ("com.pk") carrying that source's tier and label, and
# the tripwire stays mute because a host was, technically, derived.
#
# The direction of that failure is OVER-BLOCK plus MISLABEL, never escape:
# every `*.com.pk` host inherits the restricted tier and reads in the ledger
# under the restricted outlet's name. The dangerous direction is CLOSED — a
# restricted host under that suffix still matches the suffix-wide key and is
# still refused. There is no live specimen (0 rows in the measured window), so
# per gate R-3 the guard line itself rides the next tier-index-touching batch
# rather than invalidating this ship's taken receipts; this comment is here so
# nobody re-derives the false version from it.
#
# The one direction that would be unsafe (a restricted-tier source that no host
# can be derived for AT ALL) is reported by `outlet_tiers_by_host` rather than
# absorbed.
_MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "co.kr", "co.in", "co.nz",
    "co.za", "co.il", "com.au", "net.au", "org.au", "com.br", "com.cn",
    "com.hk", "com.mx", "com.sg", "com.tr", "com.tw", "com.ar",
})

# HOST COMPLETION FOR HIS HOSTLESS OUTLETS — the one hand-written table here,
# and it is scoped so it cannot become policy of its own. A `reference_only`
# source needs no `rss_url` (config.py:157), so his four — Associated Press,
# Reuters, The New York Times, Wikipedia — reach this module as a NAME and
# nothing else, while their R# rows arrive as hosts (89 of 322 rows in the
# measured window: reuters.com 66, nytimes.com 11, apnews.com 6,
# en.wikipedia.org 6). Without this mapping those rows resolve to
# DEEPEN_UNKNOWN_TIER and his ruling's own parenthetical ("the ~76
# reference_only/headline_only rows stay unfetchable") is breached on the first
# run. Keyed by the NAME AS HE WROTE IT: delete the source from sources.yaml and
# the entry goes inert, so this is host completion for outlets he lists, never
# an independent block list. Every other restricted source he has carries a feed
# URL and is derived, not tabled.
_HOSTLESS_SOURCE_HOSTS = {
    "associated press": ("apnews.com",),
    "reuters": ("reuters.com",),
    "the new york times": ("nytimes.com",),
    "wikipedia": ("wikipedia.org",),
}

# ALIAS COMPLETION FOR HIS RESTRICTED OUTLETS (gate ruling R-2, 2026-08-24,
# closing QA's F-5). A restricted outlet reached through its OWN official
# shortener or alternate registrable breaches his 2026-07-06 tier ruling by the
# back door: the shared opener follows redirects, so `https://reut.rs/xyz`
# lands on reuters.com after the tier decision has already been taken on the
# string "reut.rs" — which no source claims, so it resolves to
# DEEPEN_UNKNOWN_TIER and is fetched. QA confirmed all eight of these resolve
# FETCHABLE against the pre-fix bytes; the base rate is 0 of 634 all-time R#
# URLs (Sonar returns canonical URLs), so this is a LATENT class, not a live
# one — but the URL shapes are vendor-controlled, not ours, and a vendor can
# start emitting them any morning without telling us.
#
# SAME PROPERTY AS THE TABLE ABOVE, deliberately: keyed by THE NAME AS HE WROTE
# IT, so deleting the source from sources.yaml makes the row inert. This is
# host completion for outlets HE lists, never a deny table of our own — no new
# vocabulary and no new refusal path. The rows merge into the same `hosts` list
# the derived and hostless hosts use, so the refusal that fires is the EXISTING
# one: `fetch_article` returning TIER_EXCLUDED with the 2026-07-06 ruling in
# its own detail string, no socket opened.
#
# He carries TWO Bloomberg rows and TWO Washington Post rows, so each alias is
# keyed under BOTH names: the alias then stays live while either row survives
# and goes inert only when the outlet leaves his file entirely — which is the
# same moment its canonical host stops being restricted.
#
# `on.ft.com` needs no row: the Financial Times carries a feed on `ft.com`, so
# `_registrable("on.ft.com")` -> "ft.com" already matches the derived key.
_ALIAS_SOURCE_HOSTS = {
    "the new york times": ("nyti.ms", "nyt.com"),
    "associated press": ("apne.ws", "ap.org"),
    "reuters": ("reut.rs",),
    "bloomberg markets": ("bloom.bg",),
    "bloomberg politics": ("bloom.bg",),
    "washington post — world": ("wapo.st",),
    "washington post — business": ("wapo.st",),
    "the economist": ("econ.st",),
}

# Prose-extractability prefilter (Bucket-B, 2026-08-24). A page in one of these
# forms carries caption text, not an article: measured 113–482 chars against the
# 700-char MIN_EXTRACT_CHARS floor, deterministic across a spaced retry, and
# 12 of the 58 sampled URLs (21%) were this class. Skipping them costs zero
# prose and saves a GET.
#
# NOT `discovery.url_reject_reason`, and the reason is specific: that predicate
# asks "is this URL NEWS?" and deliberately RESCUES a dated live blog or video
# page (`_DATED_FORMAT_SEGMENTS` + `_DATED_PATH`), because a dated Guardian live
# blog is real coverage worth citing. This predicate asks a different question —
# "will an HTML extractor find prose here?" — and for that the date is
# irrelevant: the scout's marquee specimen is a DATED Al Jazeera
# `/video/newsfeed/2026/7/26/...` page holding 116 chars. Two questions, two
# predicates. Its HOST rules are reused verbatim, because "youtube.com is a
# platform page" is the same fact in both directions (9 youtube.com + 2
# facebook.com rows in the measured window).
_DEEPEN_PROSE_LESS_SEGMENTS = frozenset({
    "video", "videos", "video-clips", "videoclips", "liveblog", "live-blog",
    "live-news", "live-updates", "gallery", "galleries", "photos", "photo",
    "podcast", "podcasts", "audio", "watch", "listen",
})


def deepen_armed() -> bool:
    """Is the NL-127 DEEPEN lane live? One reader, so the trigger, the ledger
    and the pins can never disagree about whether it is on."""
    return DEEPEN_ARM != DEEPEN_ARM_OFF


def _registrable(host: str) -> str:
    """The site-identity form of a hostname: the registrable domain under a
    guarded public suffix, lowercased, port and trailing dot stripped.

    Feed hosts and article hosts are rarely the same string —
    `feeds.bloomberg.com` vs `bloomberg.com`, `rss.cnn.com` vs `cnn.com`,
    `search.cnbc.com` vs `cnbc.com` — and Bloomberg is `headline_only`, so an
    exact-host index would leak the two outlets his ruling names first.
    """
    host = (host or "").strip().lower().split(":")[0].strip(".")
    labels = [l for l in host.split(".") if l]
    if len(labels) < 2:
        return host
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:]) if len(labels) >= 3 else host
    return ".".join(labels[-2:])


def outlet_tiers_by_host(cfg) -> Tuple[Dict[str, Tuple[str, str]], List[str]]:
    """host -> (tier, outlet name), plus the names of restricted-tier sources
    no host could be derived for.

    THE SECOND RETURN VALUE IS THE TRIPWIRE, and it is why this function hands
    back two things instead of one. The failure mode that would break his ruling
    quietly is a `reference_only`/`headline_only` source whose host this index
    cannot produce: its R# rows would fall through to DEEPEN_UNKNOWN_TIER and be
    fetched. Today the list is empty by measurement (his 11 restricted sources:
    7 carry feed URLs, 4 are in `_HOSTLESS_SOURCE_HOSTS`) — so it is reported as
    a run WARNING rather than absorbed, and it goes non-empty the moment he adds
    a hostless restricted outlet this module has never seen.

    MOST RESTRICTIVE WINS on a shared host: the decision this index serves is
    binary (`tier_allows_fetch`), so if any source on a host is outside the
    fetch tiers the host is outside them.
    """
    by_host: Dict[str, Tuple[str, str]] = {}
    unresolved: List[str] = []
    for s in (getattr(cfg, "sources", None) or []):
        name = (getattr(s, "name", "") or "").strip()
        tier = getattr(s, "tier", "") or ""
        hosts: List[str] = []
        rss_url = getattr(s, "rss_url", None)
        if rss_url:
            reg = _registrable(urlparse(rss_url).netloc)
            if "." in reg and reg not in _MULTI_LABEL_SUFFIXES:
                hosts.append(reg)
        hosts.extend(_HOSTLESS_SOURCE_HOSTS.get(name.casefold(), ()))
        if not hosts:
            if not tier_allows_fetch(tier):
                unresolved.append(name or "(unnamed source)")
            continue
        # Aliases merge AFTER the tripwire check, never before it: an alias must
        # be able to ADD a host to an outlet that already resolved, and must
        # never be able to SILENCE the "no host could be derived" warning by
        # resolving an outlet whose canonical host is still missing. Same list,
        # same `by_host` write, same refusal downstream.
        hosts.extend(_ALIAS_SOURCE_HOSTS.get(name.casefold(), ()))
        for h in hosts:
            prev = by_host.get(h)
            if prev is None or (tier_allows_fetch(prev[0])
                                and not tier_allows_fetch(tier)):
                by_host[h] = (tier, name)
    return by_host, unresolved


def tier_for_url(url: str, by_host: Dict[str, Tuple[str, str]]) -> Tuple[str, str]:
    """(tier, outlet label) for a Sonar result URL under HIS tier law.

    Exact host first, then the registrable domain, then the unknown default —
    the same default an unrecognised cluster outlet gets. The label is the
    outlet's own name when he lists it (so the ledger reads "Reuters", not
    "reuters.com") and the bare host otherwise.
    """
    host = _outlet_of(url)
    if not host:
        return "", ""
    for candidate in (host, _registrable(host)):
        hit = by_host.get(candidate)
        if hit is not None:
            return hit
    return DEEPEN_UNKNOWN_TIER, host


def prose_unlikely(url: str) -> str:
    """"" if this URL may be worth a GET; else the reason it is not.

    Pure, offline, deterministic — no network, no HEAD, no date rescue.
    """
    from . import discovery
    parsed = urlparse(url or "")
    host = _outlet_of(url)
    if not host:
        return ""
    root = _registrable(host)
    if host in discovery._SOCIAL_HOSTS or root in discovery._SOCIAL_HOSTS:
        return "social-post"
    if host in discovery._AV_HOSTS or root in discovery._AV_HOSTS:
        return "audio-video-page"
    segments = [s for s in (parsed.path or "").lower().split("/") if s]
    for seg in segments:
        if seg in _DEEPEN_PROSE_LESS_SEGMENTS:
            return f"prose-less page form (/{seg}/)"
    return ""


def deepen_deficit(held_chars: int) -> float:
    """How far this story's CLUSTER full text falls short of the material
    budget, normalised to [0, 1]. 0 = the budget is already coverable with
    reporting we fetched ourselves; 1 = nothing was extracted at all."""
    if DEEPEN_TEXT_FLOOR <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (max(0, held_chars) / DEEPEN_TEXT_FLOOR)))


def deepen_score(tier: str, held_chars: int) -> float:
    """importance x deficit. See DEEPEN_TRIGGER for the measured gating proof."""
    return DEEPEN_TIER_WEIGHT.get(tier, 0.0) * deepen_deficit(held_chars)


def deepen_sonar_results(
    sonar_results: List[Dict],
    tier: str,
    held_chars: int,
    cfg,
    cluster_urls: Optional[set] = None,
    robots: Optional[RobotsCache] = None,
    fetch: FetchFn = net.fetch_bytes,
    sleep: Callable[[float], None] = time.sleep,
    already_networked: bool = False,
) -> Tuple[List[Dict], List[Dict], List[str]]:
    """Turn ≤303-char Sonar locators into pages, under his tier law.

    Returns (results, ledger, warnings). `results` is a NEW list — the input
    dicts are never mutated — in which a rescued entry's `snippet` is the
    fetched article text; every other entry is passed through unchanged, so
    `len(sonar_results)` (which the slot-3 demotion rule reads) is invariant.

    `ledger` is the charter rider: ONE ROW PER URL considered, carrying the
    outcome and its detail. Production persists only `header.fetch =
    {ok, attempted}` and drops per-URL reasons, which is precisely why the
    Bucket-B mini had to re-measure failures that had already happened.
    """
    passthrough = [dict(r) for r in (sonar_results or [])]
    if not passthrough or not deepen_armed():
        return passthrough, [], []
    if deepen_score(tier, held_chars) < DEEPEN_TRIGGER:
        return passthrough, [], []

    by_host, unresolved = outlet_tiers_by_host(cfg)
    warnings: List[str] = []
    if unresolved:
        warnings.append(
            "deepen: no host could be derived for restricted source(s) "
            + ", ".join(sorted(unresolved))
            + " — their Sonar results would be fetched under the unknown-outlet "
            "default, which the 2026-08-24 tier ruling forbids")

    held = cluster_urls or set()
    # Rows are keyed by Sonar RANK so the ledger reads in the order the vendor
    # ranked its answers — which is also the order the fetch budget is spent in,
    # and therefore the order a reader needs to audit "why not this one".
    rows: Dict[int, Dict] = {}
    plan: List[Tuple[int, Dict]] = []      # (index into passthrough, item)
    budget = DEEPEN_MAX_URLS
    for i, res in enumerate(passthrough):
        url = (res.get("url") or "").strip()
        if not url:
            continue                        # never citable; already dropped upstream
        row = {"url": url, "rank": i + 1, "outlet": "", "outcome": "",
               "detail": "", "chars": 0, "elapsed_s": 0.0}
        rows[i] = row
        if url in held:
            # `build_source_map` mints no R key for these — the cluster key wins
            # — so fetching one would buy a page nothing can cite.
            row.update(outcome=DEEPEN_SKIP_HELD,
                       detail="the cluster already holds this URL")
            continue
        shape = prose_unlikely(url)
        if shape:
            row.update(outcome=DEEPEN_SKIP_SHAPE, detail=shape)
            continue
        url_tier, label = tier_for_url(url, by_host)
        row["outlet"] = label
        if tier_allows_fetch(url_tier):
            if budget <= 0:
                row.update(outcome=DEEPEN_SKIP_BUDGET,
                           detail=f"past DEEPEN_MAX_URLS={DEEPEN_MAX_URLS} "
                                  "for this story (Sonar rank order)")
                continue
            budget -= 1
        # Tier-excluded URLs are NOT filtered out here and do not spend budget:
        # they go through `fetch_article`, which refuses them without opening a
        # socket and writes the 2026-07-06 ruling into its own detail string.
        # That refusal IS the receipt his ruling asked for.
        plan.append((i, {"url": url, "source_name": label, "tier": url_tier}))

    if plan:
        records = fetch_cluster_articles(
            [item for _, item in plan], robots=robots, fetch=fetch, sleep=sleep,
            already_networked=already_networked)
        by_url = {r.url: r for r in records}
        for i, item in plan:
            rec = by_url.get(item["url"])
            if rec is None:                 # unreachable today (dedupe upstream)
                continue
            rows[i].update(outcome=rec.outcome, detail=rec.detail,
                           chars=rec.chars, elapsed_s=rec.elapsed_s)
            if rec.outcome == OK and rec.text:
                # ONE FIELD MOVES. The clamp is the same constant the vendor
                # snippet already carried, so what we HOLD and PERSIST keeps its
                # bound, and what we RENDER was never bounded by it anyway.
                passthrough[i]["snippet"] = rec.text[:SONAR_SNIPPET_MAX_CHARS]
                passthrough[i]["deepened"] = True
                rows[i]["chars"] = len(passthrough[i]["snippet"])
    return passthrough, [rows[i] for i in sorted(rows)], warnings


def deepen_stats(ledger: List[Dict]) -> Dict[str, int]:
    """Counts over a DEEPEN ledger. `ok` = pages that yielded prose.

    `attempted` COUNTS FETCH-LAYER OUTCOME ROWS, NOT SOCKETS — truthed here
    after QA falsified the original "sockets opened" wording (2026-08-24, F-7).
    The split it really makes is between decisions taken BEFORE the fetch layer
    is asked (tier refusals and the DEEPEN_SKIP_* dispositions, excluded) and
    rows the fetch layer itself returned (counted). Those are not the same set:
    `fetch_cluster_articles` at :438 turns a non-http(s) URL into an ERROR
    record with `attempted=False` and NO socket, and ERROR is in the tuple
    below, so such a row reads as "attempted" here. Measured, not reasoned —
    one `ftp://` URL on an unrecognised host yields
    `{"considered": 4, "attempted": 3, "ok": 0, ...}` with exactly one
    robots.txt GET on the whole ledger.

    That URL also SPENDS A BUDGET SLOT: the budget is decremented at :3580 on
    the tier verdict, which is taken before the scheme is ever inspected, so a
    garbage-scheme result on an unknown host costs one of DEEPEN_MAX_URLS.
    Base rate is zero — Sonar returns http(s) — and per gate R-4 the mechanics
    ride a later touch rather than this ship.
    """
    attempted = sum(1 for r in ledger
                    if r.get("outcome") in (OK, ROBOTS_DENIED, EMPTY, ERROR,
                                            PAYWALL_SUSPECTED))
    return {
        "considered": len(ledger),
        "attempted": attempted,
        "ok": sum(1 for r in ledger if r.get("outcome") == OK),
        "excluded": sum(1 for r in ledger
                        if r.get("outcome") == TIER_EXCLUDED),
        "chars": sum(int(r.get("chars") or 0) for r in ledger
                     if r.get("outcome") == OK),
    }


def _cluster_items_for_slot(con: sqlite3.Connection, slot: Dict,
                            cfg) -> List[Dict]:
    ids = slot.get("item_ids") or []
    if not ids:
        return []
    tier_by_outlet = {s.name: s.tier for s in cfg.sources}
    rows = con.execute(
        f"SELECT outlet, url, title, raw_excerpt, fetched_at, published_at"
        f" FROM source_items WHERE id IN ({','.join('?' * len(ids))})",
        ids).fetchall()
    return [{"outlet": r["outlet"], "url": r["url"], "title": r["title"],
             "raw_excerpt": r["raw_excerpt"], "fetched_at": r["fetched_at"],
             # NL-118 item 2: the dateline's source. Feeds may omit it; a
             # missing date renders as no dateline, never as a guess.
             "published_at": r["published_at"] or "",
             "source_name": r["outlet"],
             "tier": tier_by_outlet.get(r["outlet"], "full")} for r in rows]


def persist_brief(con: sqlite3.Connection, date: str, slot: int, tier: str,
                  status: str, brief: Optional[Dict], reject_reason: str,
                  cost: float, header: Dict,
                  sources: Optional[Dict[str, Dict]] = None) -> int:
    """Returns the brief row id. Retrieved material persists alongside
    (analysis_retrieval, fix-loop item 11): hand-traces — including the
    day-14 protocol's — must never depend on re-fetching a page that can
    change or rot.

    SIZE, truthed 2026-08-24 (QA F-12): ~15-40KB per brief was measured on
    briefs whose R# rows carry the vendor's <=303-char Sonar locator. A brief
    the NL-127 DEEPEN lane FIRED on persists the fetched PAGES instead — QA
    measured 1.2k-19k chars each, at most DEEPEN_MAX_URLS (3) per story — so a
    deepened brief runs materially larger than that range. It is still bounded:
    each substituted page is clamped to SONAR_SNIPPET_MAX_CHARS on the way in
    (:3602), so the ceiling is the vendor snippet bound, not the page's own.
    """
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
                 s.get("title", ""), clamp_stored_url(s.get("url", "")),
                 s.get("retrieved_at", ""), s.get("text", "")))
    return brief_id


def clamp_stored_url(url: object) -> str:
    """NL-142 rider R-E-3, STORAGE ONLY. Bounds what `analysis_retrieval.url`
    holds; cannot change what any prompt renders, because the URL's path never
    reaches a prompt (render_source_map shows the HOST as an outlet label,
    `_material_header` shows outlet + title, neither shows the URL) and because
    persist_brief runs on a map that has already been rendered.

    An over-long URL is TRUNCATED WITH ITS TRUE LENGTH NAMED, not dropped and
    not silently cut. A silently cut URL would be a citation to somewhere else
    — the reason NL-139 drops over-long Sonar hosts instead of truncating them
    — but the marker makes the stored string unmistakably not a link while the
    hand-trace keeps the host and path prefix that identify the row."""
    text = str(url or "")
    if len(text) <= RETRIEVAL_URL_MAX_CHARS:
        return text
    mark = _RETRIEVAL_URL_MARK % len(text)
    return text[:RETRIEVAL_URL_MAX_CHARS - len(mark)] + mark


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
    """The RUN's reading: newest valid brief for (date, slot), unbounded.

    Correct for a run reading its own work — the analysis stage has just
    written these rows and every later stage of that run must see them,
    including a `--no-refresh` completion finishing an interrupted regenerate.
    Every reader OUTSIDE the generating run wants coherent_valid_brief below
    (NL-107); this function is deliberately unchanged so run semantics are not
    silently narrowed."""
    row = con.execute(
        "SELECT brief_json FROM analysis_briefs WHERE date = ? AND slot = ?"
        " AND status = 'valid' ORDER BY id DESC LIMIT 1", (date, slot)).fetchone()
    return json.loads(row["brief_json"]) if row else None


def any_valid_brief(con: sqlite3.Connection, date: str) -> bool:
    """Did THIS date produce any valid analysis brief at all?

    The resume-cost invariant behind clause 4 (`SystemicFetchFailure`): the
    pause is only honest as a free retry if nothing billable completed before
    it, and this is the predicate that says so. Run-scoped and unbounded, the
    same reading `latest_valid_brief` uses and for the same reason."""
    return con.execute(
        "SELECT 1 FROM analysis_briefs WHERE date = ? AND status = 'valid'"
        " LIMIT 1", (date,)).fetchone() is not None


# ---------------------------------------------------------------------------
# NL-107 — which briefs belong to the edition of record. TWO REGIMES.
# ---------------------------------------------------------------------------
#
# `analysis_briefs` is append-only (migration 0009 — the forensic record) and
# keyed (date, slot) with no story identity, so "newest row wins" is the RUN's
# reading. For every consumer OUTSIDE the generating run — the server's deep
# view, the memory backfill, the prompt batteries — newest-wins is wrong the
# moment a RIVAL generation context writes briefs it never publishes: since
# NL-106 a failed regenerate leaves the old edition intact and readable, so the
# reader's stories would sit next to the dead run's "full picture".
#
# The thing to exclude is a RIVAL's brief — not a brief that merely postdates
# the promote. Those are not the same set, and the difference is live data:
#
#   2026-07-05 in the principal's archive is a published edition
#   (generated_at 22:11:45.000Z) whose only briefs were written the NEXT
#   MORNING, 07:04–07:45, when the analysis organ was first built and run
#   against an already-published edition. No later rank, no staging, no rival
#   ever existed. Those briefs ARE that edition's own. A pure time bound hid
#   two of the archive's 34 live (date, slot) panes and made that date's
#   backfill refuse — the gate reproduced it end-to-end (QA-1, ruling R1).
#
# So: the earlier premise that "a published edition's own briefs are at or
# before its stamp" is FALSE in general, and it is deleted rather than patched.
# What holds instead:
#
#   REGIME A — no rival staged (`rival_exists` False): newest valid wins, byte
#   for byte the pre-NL-107 read. Post-hoc analysis, first runs, every
#   post-promote date, and every pre-0023 database land here. Nothing to
#   disambiguate, so nothing is hidden.
#
#   REGIME B — a rival IS staged: the bound applies, because a staged row means
#   a run ranked against this readable edition and has not promoted. Its briefs
#   postdate the surviving edition's promote; the edition's own precede it. The
#   staged row's CONTENT is never read — only that it exists (see rival_exists).
#
# Regime B's evidence is sufficient because every run that can displace a
# readable edition stages before it analyses (ranking.py's stage arm fires
# exactly when narrative_text is present) and only a promote consumes the
# staged row (generate.py, the sole DELETE). Dead rival ⇒ the row persists;
# in-flight rival ⇒ the row is present; no rival ⇒ no row. Reading only the
# EXISTENCE bit is also immune to the INSERT-OR-REPLACE stamp advance: two
# consecutive failed regenerates move the staged stamp forward but the bit
# stays on, so the second dead run cannot re-admit the first one's briefs.
#
# No new state, no new table, no trigger: append-only stays untouchable and
# this is a read-side predicate over columns that already exist.
#
# REGIME B's STAMP HAS A ONE-SECOND ERROR BAR, and the bound carries it
# explicitly. BOTH writers of generated_at floor the milliseconds to zero —
# persist_generation at generate.py:2929 (the promote) and ranking.persist at
# ranking.py:1498 (rank time, the stamp a bodyless row carries) — while a
# brief's created_at carries real milliseconds (migration 0008). So the
# recorded stamp is not the promote moment: the promote happened somewhere in
# [stamp, stamp + 1s), and a brief written just before it can legitimately read
# up to 999ms LATER than the stamp that published it. An exact
# `created_at <= generated_at` compare therefore hides the run's OWN brief;
# measured, it takes 27 tests in this suite down. `stamp + 1s, exclusive` is
# the tightest bound that is sound given the flooring, and it is
# quantisation-free: a brief written δ before the promote passes for every δ,
# instead of passing or failing on whether the two writes happened to straddle
# a second boundary. What it costs: on the rival arm only, a brief written
# within one second AFTER a publish counts as published with it — and reaching
# that needs a rival's rank → ingest → fetch → analysis inside one second of
# the prior promote, which is minutes wide in reality.
_COHERENT_WITH_EDITION = (
    " AND status = 'valid' AND created_at < ?")

_NEWEST_VALID = " AND status = 'valid' ORDER BY id DESC LIMIT 1"

_STAMP_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def rival_exists(con: sqlite3.Connection, date: str) -> bool:
    """Is a RIVAL generation context staged for `date` — a run that ranked
    against the live edition and has not promoted?

    EXISTENCE ONLY. The staged row's columns are never read here and staged
    content never reaches a reader surface; this returns one bit, and that bit
    only decides which of two honestly-persisted brief reads runs.

    FAIL-OPEN, deliberately — and this is the OPPOSITE arm from NL-106's FIX-1,
    which made the promote's staged read fail closed. The difference is what
    the read decides. FIX-1 gated a WRITE: an error swallowed there silently
    chose a write path and installed the banned mixture. This gates a READ
    between two persisted-brief queries, and the open answer is the pre-NL-107
    newest-valid read — it can only ever show a brief some real run really
    wrote, never staged content, never a lie.

    `no such table` in particular is positive evidence: on a pre-0023 database
    no rival CAN exist, because a re-rank of a readable edition fails loud
    there (ranking.py, pinned by NL-106's
    test_staging_fails_loud_when_the_table_is_missing_and_saves_the_edition).
    That is not hypothetical — the principal's live DB is pre-0023 until his
    next migrate, so this is the arm that runs on his data today, and it must
    render his archive exactly as it renders now. NL-106's
    test_reads_degrade_to_the_live_row_on_a_database_without_0023 takes the
    same stance for the mid-run readers."""
    try:
        return con.execute(
            "SELECT 1 FROM briefings_pending WHERE date = ? LIMIT 1",
            (date,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def publish_bound(published_at: str) -> str:
    """The exclusive upper bound on "written before this edition published":
    the recorded stamp plus its own one-second flooring error bar, in the same
    millisecond-ISO shape `created_at` uses so the comparison stays a plain
    string compare. An unparseable stamp (hand-edited or pre-ISO legacy) falls
    back to the raw value — the strictest reading, never a wider one."""
    try:
        t = datetime.strptime(published_at, _STAMP_FMT) + timedelta(seconds=1)
    except (TypeError, ValueError):
        return published_at
    return t.strftime(_STAMP_FMT)[:23] + "Z"


def coherent_valid_brief(con: sqlite3.Connection, date: str, slot: int,
                         published_at: Optional[str]) -> Optional[Dict]:
    """The valid brief for (date, slot) that belongs to the edition of record.

    Regime A (no rival staged): newest valid wins — the pre-NL-107 read.
    Regime B (a rival is staged): newest valid that predates this edition's
    publish stamp. None when no edition of record exists — nothing belongs to
    an edition that was never published."""
    if not published_at:
        return None
    if rival_exists(con, date):
        row = con.execute(
            "SELECT brief_json FROM analysis_briefs WHERE date = ? AND slot = ?"
            + _COHERENT_WITH_EDITION + " ORDER BY id DESC LIMIT 1",
            (date, slot, publish_bound(published_at))).fetchone()
    else:
        row = con.execute(
            "SELECT brief_json FROM analysis_briefs WHERE date = ? AND slot = ?"
            + _NEWEST_VALID, (date, slot)).fetchone()
    return json.loads(row["brief_json"]) if row else None


def coherent_valid_brief_id(con: sqlite3.Connection, date: str, slot: int,
                            published_at: Optional[str]) -> Optional[int]:
    """The row id of the brief coherent_valid_brief would return — so a ledger
    row citing a brief cites the SAME brief the reader is shown."""
    if not published_at:
        return None
    if rival_exists(con, date):
        row = con.execute(
            "SELECT id FROM analysis_briefs WHERE date = ? AND slot = ?"
            + _COHERENT_WITH_EDITION + " ORDER BY id DESC LIMIT 1",
            (date, slot, publish_bound(published_at))).fetchone()
    else:
        row = con.execute(
            "SELECT id FROM analysis_briefs WHERE date = ? AND slot = ?"
            + _NEWEST_VALID, (date, slot)).fetchone()
    return row["id"] if row else None


def analyze_story(con: sqlite3.Connection, date: str, slot_no: int,
                  slot: Dict, tier: str, cfg, openai_key: str,
                  pplx_key: str, remaining_usd: float,
                  memory_lines: List[str],
                  prior: List[Dict],
                  fetch: FetchFn = net.fetch_bytes,
                  chat=None, sonar=None,
                  sleep: Callable[[float], None] = time.sleep,
                  second_pass: Optional[bool] = None) -> StoryAnalysis:
    """One story through the whole organ: fetch -> sonar -> synthesize ->
    validate -> persist. Every failure path is a disclosed outcome; the
    ladder degrades cheapest-first (Sonar before synthesis, synthesis before
    anything downstream).

    `second_pass` (NL-118 item 7) opts into the gap_report synthesis over the
    same corpus. None = read the config flag, which is False."""
    from . import paths
    sa = StoryAnalysis(slot=slot_no, tier=tier, outcome="failed",
                       # NL-148: read once, here, from the slot this call was
                       # handed — the only place the story's identity and its
                       # slot number are known to belong together.
                       story_title=slot.get("story_title", ""))
    chat = chat or call_analysis_model
    sonar = sonar or _sonar_verify

    items = _cluster_items_for_slot(con, slot, cfg)
    # NL-127: the robots cache is built HERE, not inside the cluster fetch, so
    # DEEPEN's second pass over the same story reuses this story's verdicts
    # instead of re-asking every host. It is the difference between paying NPR's
    # measured 15.17s robots tarpit once and paying it twice.
    robots = RobotsCache(fetch=fetch)
    records = fetch_cluster_articles(items, robots=robots, fetch=fetch,
                                     sleep=sleep) if items else []
    sa.fetch_attempted = sum(1 for r in records if r.attempted)
    sa.fetch_ok = sum(1 for r in records if r.outcome == OK)

    # The template is READ HERE, above the sonar decision (ordering ruling
    # 2026-08-01, NL-130) — rung 1 cannot price the brief without it, and it is
    # a file read, so hoisting it costs nothing and spends nothing. The SAME
    # string renders the prompt below: one artifact, both rungs.
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    bound_usd = brief_bound_usd(template)
    sa.bound_usd = bound_usd

    # SLOT-ATOMIC FLOOR (ruling clause 2). A slot whose brief cannot be
    # afforded at ALL skips WHOLE, before rung 1 — paying Sonar here would buy
    # verification for a brief that will never run. Same disclosed exit as
    # rung 2: outcome, derating warning, escalation-flag class.
    #
    # C-5 RESOLVED — the principal's ruling (a), 2026-08-06: RESTORE THE FREE
    # VERDICT ROW. The preemption this comment used to describe (NL-130 gate
    # F-2, 2026-08-02) was real: the floor returned 64 lines above the slot-3
    # demotion, so under exhaustion slot 3 got NO demoted-quick row,
    # `analyst_slot3_tier` returned None, and the writer silently fell back to
    # A2. The corner is now CLOSED here, and the close is cheap because the
    # demotion verdict is FREE: its predicate reads two already-computed values
    # (sa.fetch_ok from the $0 article fetch; the Sonar result count, which is
    # necessarily EMPTY on this path because rung 1 has not run and will not)
    # and its persist is one SQLite INSERT at cost 0.0. No model call, no Sonar
    # call, no new spend of any kind — the row was only ever suppressed by
    # ordering, never by cost.
    #
    # WHY THE PRE-CHECK IS SCOPED TO THIS BLOCK AND NOT HOISTED ABOVE THE FLOOR.
    # `sonar_results` is [] until rung 1 runs, so an unconditionally-hoisted
    # predicate would read `len([]) < 2` as TRUE on the FUNDED path too and
    # demote every slot-3 medium with no full text BEFORE Sonar got its chance
    # to supply the two results that keep it medium. That would be a real
    # regression on the path that has the money. The exhausted path is the only
    # one where "no Sonar evidence" is already final, which is exactly why the
    # verdict is knowable here and nowhere earlier.
    #
    # TWO PROPERTIES, DELIBERATELY SPLIT (both wanted, they do not conflict):
    #   * the READER gets the verdict — the row persists, so
    #     `analyst_slot3_tier` returns "quick" and the writer treats slot 3 as a
    #     quick hit instead of falling back to A2;
    #   * the RUN still discloses the derate — `sa.outcome` stays
    #     "skipped-budget", NOT "demoted-quick", so the slot stays outside
    #     `good` (analysis.py `good = {"ok", "demoted-quick"}`) and the run
    #     summary still degrades ok -> partial with the derating warning.
    #     Setting the outcome to "demoted-quick" would restore the row by
    #     HIDING the exhaustion, which is the opposite of the ruling.
    if remaining_usd < bound_usd:
        if slot_no == 3 and tier == "medium" and sa.fetch_ok == 0:
            # The demotion verdict, persisted at $0. `sonar_results` is [] on
            # this path by construction, so the `< 2` conjunct of the live
            # predicate below is satisfied and not re-tested here.
            sa.slot3_verdict_under_floor = True
            verdict_detail = (
                "slot-3 medium -> quick by analyst (thin material: no full "
                "text, and the budget floor stopped this slot before any "
                "retrieval could be bought) — C-5 ruling (a) 2026-08-06: the "
                "verdict row is free and persists under exhaustion")
            persist_brief(con, date, slot_no, tier, "rejected", None,
                          f"demoted-quick: {verdict_detail}", 0.0,
                          {"slot": slot_no, "tier": tier, "date": date,
                           "verdict": "demoted-quick", "model": ANALYSIS_MODEL,
                           "under_budget_floor": True},
                          sources=build_source_map(records, items, [], []))
            sa.warnings.append(
                "slot 3: demoted-quick verdict persisted under the budget "
                "floor (free, deterministic) — the writer reads quick, not A2")
        sa.outcome = "skipped-budget"
        sa.sonar_status = "skipped — slot skipped whole (budget floor)"
        sa.detail = (f"slot skipped whole: remaining ${remaining_usd:.3f} is "
                     f"under the brief bound ${bound_usd:.3f} — no Sonar spend "
                     "for a brief that cannot run (ordering ruling 2026-08-01)")
        sa.warnings.append("derating: analysis brief skipped under the cap "
                           "(escalation-flag class)")
        return sa

    # NL-151 GATE A (L1 + L2) — THE FETCH-FAILURE SKIP. Sockets opened for this
    # prioritized story and nothing came back, so under L1 it cannot hold a
    # depth slot and under L2 it is skipped, disclosed, and its tier handed on.
    #
    # PLACEMENT IS LOAD-BEARING IN BOTH DIRECTIONS.
    #   * ABOVE the ladder (this line sits between the budget floor's return
    #     and Sonar rung 1): the slot buys nothing it cannot publish. Sonar
    #     verification and synthesis for a brief L1 forbids is pure waste, and
    #     it is the spend QA measured on a clause-4 pause.
    #   * BELOW the budget floor: the floor's C-5 free-verdict row (ruling (a),
    #     2026-08-06) is a decision the reader needs whatever the fetch did, and
    #     under exhaustion the run has no money to reach a brief anyway. Hoisting
    #     this gate over the floor would silently retire that row.
    #
    # The predicate is FETCH OUTCOMES, never source-map emptiness — NL-148's
    # finding (a) is that the source map is never empty here, and it still
    # isn't. `fetch_attempted` is what separates this from Gate B: it means the
    # network was actually asked.
    if depth_skip_armed() and sa.fetch_attempted and not sa.fetch_ok:
        sa.outcome = FETCH_SKIP_OUTCOME
        sa.detail = (
            f"no cluster source yielded full text ({sa.fetch_attempted} "
            "attempted, 0 extracted) — skipped out of the depth tier under L1 "
            "(no degraded coverage in the main slots), disclosed at the bottom "
            "of the briefing under L2, and the depth tier passes to the next "
            "prioritized story")
        return sa

    # NL-151 GATE B (L1 only) — NOTHING WAS EVER FETCHABLE. Gate A returned
    # immediately above for every slot that opened a socket, so a slot reaching
    # this line with `fetch_attempted == 0` never opened one: every cluster
    # source sits outside the 2026-07-06 tier boundaries. L1 disqualifies it
    # from depth exactly as it disqualifies a failed fetch — the reader cannot
    # tell the two apart, and "no degraded coverage in the depth tier, ever"
    # does not have a policy exemption.
    #
    # IT IS NOT DISCLOSED, and that is the honest choice rather than a gap.
    # L2's disclosure sentence is the principal's words and its subject is a
    # FETCH THAT FAILED; nothing failed here, so rendering it would state
    # something untrue about the run. Inventing a second reader-facing sentence
    # to cover an unobserved branch is the "frozen guarantee nothing can render"
    # mistake NL-148 refused, pointed the other way. The run record carries it.
    #
    # PLACEMENT IS LOAD-BEARING IN BOTH DIRECTIONS — the same two as Gate A,
    # which is the point of the gate ruling that put it here (R-A, 2026-08-13).
    #   * ABOVE the ladder. This block used to sit below Sonar rung 1, where a
    #     funded never-attempted slot paid for verification of a brief the gate
    #     then refused to mint — QA measured 3 Sonar calls / $0.06 on a 3-slot
    #     all-Gate-B day. The header's design law ("THE GATES ALSO SAVE MONEY,
    #     which is why they sit above the ladder") is now true of BOTH gates
    #     instead of Gate A alone. Same waste class the header itself cites.
    #   * BELOW the budget floor, for Gate A's C-5 reason exactly: the floor's
    #     free demoted-quick verdict row (ruling (a), 2026-08-06) is a decision
    #     the reader needs whatever the fetch did, and hoisting this gate over
    #     the floor would silently retire that ruled row.
    #
    # `items` IS THE CONJUNCT THE OLD POSITION GOT FOR FREE. Below the
    # total-failure rule, a slot with an EMPTY cluster was caught by
    # `skipped-thin` first and never reached this gate. Above that rule the
    # guard has to be written down: an empty cluster has nothing that could be
    # "unfetchable", so it falls through to `skipped-thin` exactly as it does
    # today. This gate's subject is unchanged — a slot that HAS material (C#
    # excerpt keys, per NL-148's still-true finding (a)) and may not use it at
    # this tier. The key is `not fetch_attempted` rather than `not fetch_ok`
    # because above the ladder the two stop being interchangeable: Gate A no
    # longer stands between this line and every attempted-and-failed slot by
    # position alone, so the never-attempted case is now stated, not inferred.
    #
    # ONE ARMED-ONLY CONSEQUENCE, MEASURED AND DELIBERATE. A never-attempted
    # slot-3 MEDIUM used to reach the slot-3 demotion rule below and leave as
    # `demoted-quick`, holding its depth slot with $0.02 of Sonar already spent;
    # above the ladder it leaves here as `skipped-no-fetchable-sources` at $0
    # and the tier passes on. That makes Gate B agree with Gate A, which already
    # preempts that same demotion for the attempted-and-failed slot — before the
    # hoist the two gates disagreed about this one slot. It does NOT widen L1
    # past the principal's scope: the predicate is still fetch outcomes only,
    # and whether L1 should reach further is his open call, not this block's.
    # At the OFF default `depth_skip_armed()` is False and this block is dead:
    # the reordering is zero-delta in production, measured rather than argued
    # (identical outcome/tier/spend vector on both sides of the hoist).
    if depth_skip_armed() and items and not sa.fetch_attempted:
        sa.outcome = NO_FETCHABLE_OUTCOME
        sa.detail = (
            "no cluster source was fetchable at all (every source outside the "
            "2026-07-06 tier boundaries; 0 attempted, 0 extracted) — skipped "
            "out of the depth tier under L1, NOT disclosed as a fetch failure "
            "because nothing was attempted, and the depth tier passes to the "
            "next prioritized story")
        return sa

    # Ladder rung 1 (cheapest first): Sonar goes before synthesis money
    sonar_results: List[Dict] = []
    sonar_ran = False
    s_cost = 0.0
    # FIX-2 (B4-D2) priced this probe off the analyst seat's OUTPUT ceiling
    # alone ($0.09). The ordering ruling 2026-08-01 (NL-130) replaces that with
    # `bound_usd` — the call-time upper bound on what rung 2 will price — so the
    # sonar line is `bound + SONAR_EST_USD` and sits ABOVE the brief line at
    # every prompt size the code can build, including after a principal edit to
    # the template. Consequences, by construction and not by calibration:
    #   * Sonar runs only when the brief is STILL fundable after paying for
    #     Sonar -> pay-then-skip is unreachable (given s_cost <= SONAR_EST_USD;
    #     a Sonar overcharge is caught by the invariant tripwire at rung 2);
    #   * the witnessing band [bound, bound + SONAR_EST_USD) — Sonar skipped,
    #     brief runs — exists at EVERY prompt size, width SONAR_EST_USD. That
    #     is M9's "verification degrades first", still standing.
    if remaining_usd - SONAR_EST_USD < bound_usd:
        sa.sonar_status = "skipped — budget ladder (Sonar degrades first)"
        sa.warnings.append("derating: Sonar verification skipped under the cap")
    else:
        sonar_ran = True
        claims = [slot.get("story_title", "")] + \
                 [it.get("title", "") for it in items[:4]]
        sonar_results, s_cost, sa.sonar_status = sonar(
            pplx_key, slot.get("story_title", ""), claims)
        sa.cost_usd += s_cost
        # NL-95: Sonar is a METERED api — charged == shadow by construction,
        # so the same figure lands on both tracks. `remaining_usd` is now
        # shadow-denominated, which is what the API-priced `bound_usd` above
        # and estimate_synthesis_usd below were always comparing against. The
        # ordering ruling does NOT touch the denomination (Onna's law).
        sa.shadow_usd += s_cost
        remaining_usd -= s_cost

    # NL-127 DEEPEN — the R# lane, HIS RULING 2026-08-24. $0 in model spend; one
    # GET per surviving URL, single attempt (Bucket-B: 34 retries rescued 0).
    #
    # PLACEMENT. Below rung 1, because DEEPEN's subject is the answers Sonar just
    # returned and there is nothing to deepen before they exist — a skipped or
    # empty Sonar leaves `sonar_results == []` and the call is a no-op by its own
    # first line. Above `build_source_map`, because the R# key's `text` IS the
    # thing DEEPEN changes and the map is where that key is minted; deepening
    # after the map would mean rewriting a key the corpus and the material block
    # have already been built from.
    #
    # WHAT IT CANNOT REACH FROM HERE, deliberately. `sa.fetch_ok` and
    # `sa.fetch_attempted` were computed above the gates and are NOT recomputed:
    # Gates A and B, the clause-4 systemic-failure verdict and the slot-3
    # demotion all read those two counters, and a DEEPEN success quietly raising
    # `fetch_ok` would move a reader-visible tier decision from a lane the
    # principal never ruled on. DEEPEN's own counters live in their own ledger.
    # `len(sonar_results)` is likewise invariant — the deepen call returns a list
    # of the same length — so the `< 2` conjunct below decides exactly what it
    # decided before.
    held_chars = sum(r.chars for r in records if r.outcome == OK)
    sonar_results, sa.deepen_ledger, deepen_warnings = deepen_sonar_results(
        sonar_results, tier=tier, held_chars=held_chars, cfg=cfg,
        cluster_urls=cluster_url_set(records, items), robots=robots,
        fetch=fetch, sleep=sleep,
        already_networked=bool(sa.fetch_attempted))
    sa.warnings.extend(deepen_warnings)

    # NL-63 item 3: thread-scoped P-material. When this slot's threads carry a
    # record, P becomes the thread's OWN prior coverage (dated ledger + state),
    # replacing the two generic 4KB narrative dumps — the fix for Content's
    # P1-cite proof. No thread record yet -> the generic `prior` stands (honest
    # cold-start / no-thread story). Zero new LLM spend; it re-allocates the
    # same P material budget.
    from . import memory_core
    slot_prior = memory_core.prior_for_slot(con, date, slot, prior)
    # X4 (NL-118 item 3): when no thread record exists, `prior_for_slot`
    # hands back the GENERIC editions — whole narratives now, so the cut to
    # this slot's own story happens here, where the story is known. A
    # thread-scoped prior (carries "thread") is already story-scoped and
    # passes through untouched.
    if slot_prior and not any(p.get("thread") for p in slot_prior):
        slot_prior = prior_material_for_story(slot_prior, slot)
        for p in slot_prior:
            if not p.get("matched"):
                sa.warnings.append(
                    f"prior-briefing {p.get('date')}: no section of that "
                    "edition names this story — head slice carried, and the "
                    "analyst is told it may not be this thread's record")
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
        # NL-148 — WHY CLAUSE 2 IS NOT WIRED HERE, and it is not a choice.
        # This branch is NOT the fetch-failure skip and cannot be made into
        # one: `build_source_map` mints a C# excerpt key for every cluster item
        # that was not fetched, so a slot holding ANY cluster item always
        # carries C-keys and this predicate is False no matter how completely
        # fetching failed. Measured on the shipped function, not reasoned:
        #   build_source_map([2 ERROR records], [2 items], [], []) -> ['C1','C2']
        #   not any(k[0] in "SCR" for k in sources)               -> False
        # What actually reaches here is a slot with no cluster items at all —
        # an empty cluster, not a failed fetch. Hanging the contract's skip
        # off this branch would have been enforcement over a dead path.
        return sa

    # NL-151 GATE B USED TO SIT HERE, below this rule. The gate ruling R-A
    # (2026-08-13) hoisted it above Sonar rung 1 so it stops paying for the
    # verification of a brief it then refuses; the empty-cluster slots that
    # this rule catches keep their `skipped-thin` outcome because the hoisted
    # gate carries an explicit `items` conjunct. See the block above rung 1.

    # `template` was read above the sonar decision (LADDER PRICING) and is
    # reused here verbatim — the artifact rung 1 priced IS the artifact rung 2
    # renders. Re-reading it would reopen the two-addresses class.
    #
    # NL-151: `degraded` is UNREACHABLE WHENEVER THE CONTRACT IS ARMED — Gates
    # A and B both return on `fetch_ok == 0`, and this function only ever runs
    # at full/medium tier. At the OFF default it stays exactly as live as it is
    # today, which is why today's pins on the "Analysis: unavailable" label are
    # untouched. It is kept, not deleted, because it is the correct label the
    # day an In-Brief tier is analysed (L3 PERMITS degraded coverage there), and
    # because the L1 invariant pin asserts this unreachability under each armed
    # arm rather than trusting it.
    degraded = None
    if sa.fetch_ok == 0:
        degraded = ("no full-text extraction succeeded — brief built from "
                    "excerpts/retrieval only")
    # explicit placeholder replacement, NOT str.format: the template shows a
    # literal JSON example whose braces would read as format fields (the
    # discovery BUG-3 class), and the file stays principal-editable without
    # {{escape}} noise.
    prompt = _render_prompt(template, {
        # NL-131 structural kill (was `.get(tier, 450)`)
        "word_budget": str(word_budget_for(tier)), "tier": tier,
        "date": date, "slot": str(slot_no),
        "story_title": slot.get("story_title", ""),
        "story_summary": slot.get("summary", ""),
        "memory_context": "\n".join(memory_lines)
                          or "(no tracked threads touch this story)",
        "source_map": render_source_map(sources),
        "material": render_material(sources)})

    # Ladder rung 2, unchanged in role: the post-render check against the
    # ACTUAL estimate. Under the ordering ruling 2026-08-01 it is DEFENCE IN
    # DEPTH — with a correct bound it can no longer fire alone, because the
    # floor and rung 1 already proved `remaining >= bound >= est`. If it does
    # fire after rung 1 let Sonar spend, rung 1's bound was WRONG: that is the
    # "margin proves unboundable" falsifier, and it escalates instead of
    # passing quietly (Onna's instrumentation clause).
    est = estimate_synthesis_usd(prompt)
    sa.est_usd = est
    if est > remaining_usd:
        sa.outcome = "skipped-budget"
        sa.detail = (f"synthesis estimate ${est:.3f} exceeds remaining budget "
                     f"${remaining_usd:.3f} — brief skipped, disclosed")
        sa.warnings.append("derating: analysis brief skipped under the cap "
                           "(escalation-flag class)")
        if sonar_ran:
            cause = (f"the call-time bound ${bound_usd:.5f} under-priced this "
                     f"prompt (est ${est:.5f}, {len(prompt)} chars vs the "
                     f"{brief_bound_chars(template)}-char bound) — "
                     "PROMPT_MARGIN_CHARS is too small"
                     if est > bound_usd else
                     f"Sonar charged ${s_cost:.5f} against its "
                     f"${SONAR_EST_USD} reserve")
            sa.warnings.append(
                "derating: ORDERING INVARIANT VIOLATED — Sonar was paid for a "
                "slot whose brief then skipped, which the 2026-08-01 ordering "
                f"ruling makes unreachable: {cause}")
        return sa

    try:
        # NL-95 tolerant unpack (memory_core.py's pattern): the default chat is
        # call_analysis_model, which returns (raw, charged, shadow); an
        # INJECTED 2-tuple chat (every offline test's seam) still works and its
        # shadow defaults to charged — the api-lane invariant.
        raw, cost, *rest = chat(openai_key, prompt)
        shadow = rest[0] if rest else cost
    except Exception as exc:
        sa.outcome = "failed"
        sa.detail = f"synthesis call failed after one retry ({type(exc).__name__}: {exc})"
        return sa
    sa.cost_usd += cost
    sa.shadow_usd += shadow

    corpus = verbatim_corpus(sources)
    header = {
        "slot": slot_no, "tier": tier, "date": date,
        "manifest": {k: {"url": sources[k]["url"], "outlet": sources[k]["outlet"],
                         "kind": sources[k]["kind"]} for k in sorted(sources, key=_key_sort)},
        "fetch": {"ok": sa.fetch_ok, "attempted": sa.fetch_attempted},
        "sonar": sa.sonar_status,
        "degraded": degraded,
        "model": ANALYSIS_MODEL,
    }
    # NL-127 charter rider — the per-URL receipts, persisted BESIDE the aggregate
    # rather than instead of it. `header.fetch` keeps the meaning every historical
    # brief_json row carries; this key appears only on a brief whose story DEEPEN
    # actually fired on, so no row's shape moves for a story it did not. No
    # schema field and no migration: `analysis_briefs.brief_json` is a document.
    if sa.deepen_ledger:
        header["deepen"] = {"stats": deepen_stats(sa.deepen_ledger),
                            "urls": sa.deepen_ledger}
    try:
        clean, warnings = validate_brief(raw, sources, tier, corpus,
                                         briefing_date=date)
    except BriefQuoteUnmarked as unmarked:
        # Spec-1's promotion, with the redraft the round made a CONDITION of
        # saying yes to it. Bounded exactly like the ceiling redraft: the
        # second attempt's validation is not caught here, so a second unmarked
        # quote is a disclosed rejected row, never a third call.
        sa.warnings.append(f"quote fidelity: {unmarked} — redrafting once")
        try:
            raw, cost, *rest = chat(
                openai_key,
                prompt + QUOTE_REDRAFT_INSTRUCTION.format(
                    span=unmarked.span, where=unmarked.where))
            shadow = rest[0] if rest else cost
        except Exception as exc:
            sa.outcome = "failed"
            sa.detail = (f"quote redraft call failed "
                         f"({type(exc).__name__}: {exc})")
            return sa
        sa.cost_usd += cost
        sa.shadow_usd += shadow
        try:
            clean, warnings = validate_brief(raw, sources, tier, corpus,
                                             briefing_date=date)
        except BriefRejected as exc:
            sa.outcome = "rejected"
            sa.detail = f"after quote redraft: {exc}"
            persist_brief(con, date, slot_no, tier, "rejected", None,
                          sa.detail, sa.cost_usd, header, sources=sources)
            return sa
        warnings = list(warnings) + [
            f"quote redraft applied: an unmarked quotation in "
            f"{unmarked.where} was rewritten; this brief is the redraft"]
    except BriefOverCeiling as over:
        # EC-9's teeth (NL-118 item 6): ONE redraft, same corpus, same map,
        # plus the instruction that names what to cut. Bounded by construction
        # — the redraft's own validation is not caught here, so a second
        # over-run is a disclosed rejected row, never a third call.
        sa.warnings.append(f"length ceiling: {over} — redrafting once")
        draft1 = raw                       # Step 2 needs the FIRST draft back
        try:
            raw, cost, *rest = chat(openai_key, prompt + REDRAFT_INSTRUCTION.format(
                words=over.words, budget=over.budget, ceiling=over.ceiling))
            shadow = rest[0] if rest else cost
        except Exception as exc:
            sa.outcome = "failed"
            sa.detail = (f"length redraft call failed "
                         f"({type(exc).__name__}: {exc})")
            return sa
        sa.cost_usd += cost
        sa.shadow_usd += shadow
        try:
            clean, warnings = validate_brief(raw, sources, tier, corpus,
                                             briefing_date=date)
        except BriefOverCeiling as over2:
            # Spec-4(c) STEP 2 — THE SURVIVING LOSS MODE (batch B item 5).
            # The redraft is instructed to come in shorter. It does not always
            # obey: measured on this batch's own iteration-1 runs, draft 603 ->
            # redraft 725 and draft 622 -> redraft 646. Both times the model
            # was told "write it again, shorter" and wrote it LONGER, and the
            # old code then threw away the shorter draft it already had and
            # rejected the story.
            #
            # So: take the SHORTER of the two drafts. If it is over the ceiling
            # but within budget x DISCLOSURE_BAND_FACTOR, ship it WITH the
            # disclosure. Beyond the band, the existing disclosed rejection is
            # unchanged. The band's falsifier is on the record: disclosed-band
            # briefs failing a G7 density read close the band.
            shorter, words2 = ((raw, over2.words) if over2.words < over.words
                               else (draft1, over.words))
            band = int(over.budget * DISCLOSURE_BAND_FACTOR)
            if words2 <= band:
                try:
                    clean, warnings = validate_brief(
                        shorter, sources, tier, corpus, briefing_date=date,
                        ceiling_factor=DISCLOSURE_BAND_FACTOR)
                except BriefRejected as exc:
                    sa.outcome = "rejected"
                    sa.detail = f"after length redraft: {exc}"
                    persist_brief(con, date, slot_no, tier, "rejected", None,
                                  sa.detail, sa.cost_usd, header,
                                  sources=sources)
                    return sa
                warnings = list(warnings) + [
                    f"length DISCLOSURE BAND (Spec-4(c) step 2): drafts ran "
                    f"{over.words} and {over2.words} words; the shorter "
                    f"({words2}) is over the {over.ceiling}-word ceiling but "
                    f"within the {band}-word band — shipped WITH this "
                    f"disclosure, not padded and not discarded"]
            else:
                sa.outcome = "rejected"
                sa.detail = (f"after length redraft: both drafts past the "
                             f"disclosure band ({over.words} and "
                             f"{over2.words} words, band {band})")
                persist_brief(con, date, slot_no, tier, "rejected", None,
                              sa.detail, sa.cost_usd, header, sources=sources)
                return sa
        except BriefRejected as exc:
            sa.outcome = "rejected"
            sa.detail = f"after length redraft: {exc}"
            persist_brief(con, date, slot_no, tier, "rejected", None,
                          sa.detail, sa.cost_usd, header, sources=sources)
            return sa
        else:
            warnings = list(warnings) + [
                f"length redraft applied: first draft {over.words} words > "
                f"{over.ceiling}-word ceiling; this brief is the redraft"]
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

    # NL-118 item 7 — the gap_report second synthesis pass. DEFAULT OFF
    # (config.SourcesConfig.gap_report_second_pass, sources.yaml `settings:`;
    # no new env var). Same corpus, same map, ZERO retrieval calls: the
    # gap_report is the hook a later sanctioned-retrieval leg consumes, and
    # without it a second pass cannot tell "not chased" from "chased and
    # absent" — which is how "veteran leader" ×4 happened.
    if second_pass is None:
        second_pass = bool(getattr(cfg, "gap_report_second_pass", False))
    if second_pass:
        clean, gap_report, pass2_warnings = _gap_report_pass(
            clean, sources, tier, corpus, date, openai_key, chat, sa)
        sa.warnings.extend(pass2_warnings)
        if gap_report is not None:
            header["gap_report"] = gap_report

    sa.outcome = "ok"
    sa.detail = (f"{len(clean['ledger'])} ledger entries, "
                 f"{len(clean['sources'])} cited sources")
    sa.brief = clean
    persist_brief(con, date, slot_no, tier, "valid", clean, "", sa.cost_usd,
                  header, sources=sources)
    return sa


def _gap_report_pass(clean: Dict, sources: Dict[str, Dict], tier: str,
                     corpus: str, date: str, openai_key: str, chat,
                     sa: StoryAnalysis) -> Tuple[Dict, Optional[List[Dict]], List[str]]:
    """Second synthesis over the SAME held corpus, with a mandatory
    gap_report. Returns (brief, gap_report or None, warnings).

    Fails SOFT by design: every failure path keeps pass 1's validated brief.
    A second pass that can lose a good brief is a downgrade wearing an
    improvement's name."""
    from . import paths
    warns: List[str] = []
    unknowns = [u.get("question", "") for u in clean.get("unknowns") or []]
    if not unknowns:
        return clean, None, ["gap-report pass skipped — the draft has no "
                             "open questions to chase"]
    try:
        template = (paths.PROMPTS_DIR / "analysis_gap_report.txt").read_text(
            encoding="utf-8")
    except OSError as exc:
        return clean, None, [f"gap-report pass skipped — prompt unreadable ({exc})"]
    prompt = _render_prompt(template, {
        # NL-131 structural kill (was `.get(tier, 450)`)
        "word_budget": str(word_budget_for(tier)), "tier": tier,
        "date": date,
        "draft_json": json.dumps(clean, ensure_ascii=False, indent=1),
        "source_map": render_source_map(sources),
        "material": render_material(sources)})
    try:
        raw2, cost, *rest = chat(openai_key, prompt)
        sa.cost_usd += cost
        sa.shadow_usd += rest[0] if rest else cost
    except Exception as exc:  # noqa: BLE001 — pass 1's brief survives
        return clean, None, [f"gap-report pass failed ({type(exc).__name__}: "
                             f"{exc}) — pass-1 brief kept"]
    try:
        clean2, warnings2 = validate_brief(raw2, sources, tier, corpus,
                                           briefing_date=date)
    except Exception as exc:  # noqa: BLE001 — includes BriefOverCeiling: a
        # redraft loop inside the optional pass is not worth a third call
        return clean, None, [f"gap-report pass rejected ({exc}) — pass-1 "
                             "brief kept"]

    gap_report = []
    for g in (raw2.get("gap_report") or []) if isinstance(raw2, dict) else []:
        if not isinstance(g, dict):
            continue
        cites = [c for c in _cites_of(g) if c in sources]
        gap_report.append({"question": str(g.get("question") or ""),
                           "answered": bool(g.get("answered")),
                           "cites": cites,
                           "note": str(g.get("note") or "")[:300]})
    if not gap_report:
        return clean, None, ["gap-report pass returned no gap_report — "
                             "pass-1 brief kept (the report IS the pass)"]
    # The engineering round's non-negotiable: a question marked UNANSWERED
    # must survive into unknowns, or the pass has quietly dropped the hole
    # instead of disclosing it.
    kept = {_norm_ws(u.get("question", "")) for u in clean2.get("unknowns") or []}
    for g in gap_report:
        if not g["answered"] and _norm_ws(g["question"]) not in kept:
            warns.append(
                "gap-report contract: question marked unanswered vanished "
                f"from unknowns — {g['question'][:80]!r}; pass-1 brief kept")
            return clean, gap_report, warns
        if g["answered"] and not g["cites"]:
            warns.append(
                "gap-report: question marked answered with no citable key — "
                f"{g['question'][:80]!r}; pass-1 brief kept")
            return clean, gap_report, warns
    answered = sum(1 for g in gap_report if g["answered"])
    warns.extend(warnings2)
    warns.append(f"gap-report second pass applied: {answered}/{len(gap_report)} "
                 "open questions answered from the held corpus, "
                 f"{len(gap_report) - answered} disclosed as absent")
    return clean2, gap_report, warns


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
    # A″ (2026-07-17): the analyst seat is Sonnet 5 (anthropic) since B4 — the
    # OpenAI key passed into analyze_story is INERT (the anthropic analyst seam
    # ignores it). Require it ONLY if the analyst seat resolves to openai (it never
    # does today); a keyless-OpenAI analysis with the anthropic analyst is HEALTHY
    # (was a stale blanket refusal). Perplexity (pplx_key, below) is a separate
    # credential the discovery/full-text paths gate on themselves.
    if llm.seat_is_openai("analyst", src_env) and not openai_key:
        raise RuntimeError("the analyst seat resolves to OpenAI (gpt-4o) but "
                           "OPENAI_API_KEY is not set — add it to .env")
    pplx_key = (src_env.get("PERPLEXITY_API_KEY") or "").strip()
    own_con = con is None
    con = con or db.connect()
    _own_analyst = False
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
        # NL-106: the staged selection wins, AFTER the date is resolved (the
        # latest-row fallback above resolves the date first, then this asks about
        # THAT date). A regenerate stages its new slots at rank time and leaves
        # the live row holding the reader's old edition, so mid-run this stage
        # must analyse what the run is about to publish, not what is published.
        # Nothing staged (the common case, and every pre-0023 database) reads
        # the live row exactly as before.
        pending = ranking.pending_selection(con, date)
        slots = json.loads(
            (pending["story_slots"] if pending is not None
             else row["story_slots"]) or "[]")
        # tiers: the generation log's recorded tiers for the date; positional
        # default when absent (pre-M7 rows)
        tiers = tiers_override[:len(slots)] if tiers_override \
            else _tiers_for(date, len(slots))
        cfg = config.load_sources()
        cap = config.budget_cap_usd_per_run(src_env)
        # M3: when generate hosts this stage, its prior spend rides in so
        # ONE cap governs the whole run (the ladder still degrades analysis
        # before the writer — analysis runs first and leaves headroom).
        #
        # NL-95 ENFORCEMENT FIX #1: this ladder accumulates usd_SHADOW. It used
        # to accumulate charged, which on the subscription lane is $0 — so
        # `cap - spent` never shrank, every slot saw the full cap, and the
        # degradation ladder this stage exists to run could not fire. The
        # figure passed in as already_spent is shadow-denominated too (the
        # generate-side `spent`, fixed at its own site).
        spent = float(already_spent)
        charged_total = 0.0
        memory_lines = memory_mod.active_context(con)
        prior = _prior_briefing_material(con, date)
        # FIX-1 (B4-D1): publish the analyst's ONE resolution for the whole stage
        # BEFORE building the report. effective_seat GATES — a raw LaneUnavailable
        # (unregistered lane / missing subscription binary) KILLS the stage here,
        # once, before the per-slot loop whose analyze_story broad except would
        # otherwise swallow it into $0 'failed' briefs (the FIX-1 (B3) stage-kill,
        # preserved) — AND applies the armed fall. When generate hosts the stage
        # it already published; this reuses it (own-scope). The report lane,
        # cost_fields, and _analysis_chat's transport all ride THIS (cfg, reason):
        # a mid-stage `claude` flap can no longer fork the transport from the
        # ledger/report — the D1 close.
        _own_analyst = _ACTIVE_ANALYST is None
        if _own_analyst:
            _publish_analyst()
        analyst_cfg, analyst_fb = _ACTIVE_ANALYST
        report = {"ts": datetime.now(timezone.utc).isoformat(),
                  "stage": "analysis", "date": date, "status": "ok",
                  "model": ANALYSIS_MODEL,
                  # the analyst spend is lane-attributed off the ONE resolution;
                  # a fallen stage records lane=api(fallback:…), never bare 'api'.
                  "lane": llm.fallback_lane_label(analyst_fb, analyst_cfg.lane),
                  "per_story": [], "total_usd": 0.0,
                  "derating": False, "warnings": []}
        # NL-151 — THE DEPTH-TIER WALK (L2's promote, without a promote).
        #
        # OFF (today, byte-for-byte): `tiers` is positional, the loop visits the
        # slots it names and skips the rest. ARMED: the depth tiers become a
        # QUEUE handed out in rank order, and a slot disqualified by Gate A or B
        # DOES NOT CONSUME ITS TIER — the next-ranked story inherits it. That is
        # "the next prioritized story is PROMOTED into the depth treatment"
        # expressed as tier movement, which is what keeps NL-148's finding (b)
        # from biting: SLOT NUMBERING NEVER CHANGES, so `analysis_briefs`
        # (date, slot) still means the story it always meant, `latest_valid_brief`
        # cannot return another story's analysis, and the ruled resume-from-
        # failed-slot default survives untouched. No schema field, no migration.
        #
        # WHAT DOES *NOT* HAND ITS TIER ON, and the line is his words: only
        # FETCH outcomes disqualify. A brief the model rejected, or one skipped
        # under the budget floor, keeps its slot in the depth tier exactly as
        # today — those are different failure classes with their own disclosed
        # ladders, and re-routing them here would be legislating past the
        # ruling. (The residual this leaves is named in the build report: a
        # rejected brief still shows the reader "Analysis: unavailable" in a
        # depth slot, which a broad reading of L1 would also forbid. That
        # reading is his to make, not this loop's.)
        #
        # The walk stops when the queue empties — an armed run analyses exactly
        # as many stories as it needs to FILL the depth tier, and no more.
        queue = ([t for t in tiers if t in ("full", "medium")]
                 if depth_skip_armed() else None)
        depth_tiers: List[Optional[str]] = [None] * len(slots)
        fetch_skipped: List[Dict] = []
        for i, slot in enumerate(slots, start=1):
            if queue is None:
                tier = tiers[i - 1] if i - 1 < len(tiers) else "quick"
                if tier not in ("full", "medium"):
                    continue
            else:
                if not queue:
                    break
                tier = queue[0]
            sa = analyze_story(con, date, i, slot, tier, cfg, openai_key,
                               pplx_key, cap - spent, memory_lines, prior,
                               fetch=fetch, chat=chat, sonar=sonar, sleep=sleep)
            spent += sa.shadow_usd          # the CAP ladder (NL-95 fix #1)
            charged_total += sa.cost_usd    # the MONEY record
            report["per_story"].append({
                "slot": sa.slot, "tier": sa.tier, "outcome": sa.outcome,
                "detail": sa.detail, "cost_usd": round(sa.cost_usd, 6),
                # NL-95: charged keeps the key it has always had (and the
                # meaning every historical jsonl row carries); shadow rides
                # BESIDE it under a new one. No row's meaning moves.
                "usd_shadow": round(sa.shadow_usd, 6),
                # Ordering ruling 2026-08-01 (NL-130): the estimate the ladder
                # priced this slot with, beside what it actually cost. `est_usd`
                # is null when no prompt was ever rendered. This is a
                # generation_log field, NOT a DB column — no migration.
                "est_usd": (None if sa.est_usd is None
                            else round(sa.est_usd, 6)),
                "bound_usd": (None if sa.bound_usd is None
                              else round(sa.bound_usd, 6)),
                "fetch_ok": sa.fetch_ok, "fetch_attempted": sa.fetch_attempted,
                # NL-148: the story's own name beside its fetch counters, so
                # the run's record can say WHICH prioritized story lost its
                # full text without re-deriving a name from a slot number.
                "story_title": sa.story_title,
                "sonar": sa.sonar_status,
                # NL-127: the DEEPEN row in the RUN REPORT — the surface the
                # principal reads after a generate. `null` on a story DEEPEN did
                # not fire on ("never asked" is a different fact from "asked and
                # got nothing"); the per-URL ledger rides the brief_json header.
                "deepen": (deepen_stats(sa.deepen_ledger)
                           if sa.deepen_ledger else None)})
            report["warnings"].extend(sa.warnings)
            if sa.fetch_attempted and not sa.fetch_ok:
                report["warnings"].append(
                    f"fetch: no cluster source yielded full text for slot "
                    f"{sa.slot} ({sa.story_title!r}) — {sa.fetch_attempted} "
                    "attempted, 0 extracted")
            if any(w.startswith("derating:") for w in sa.warnings):
                report["derating"] = True
            if queue is not None:
                if sa.outcome in DEPTH_DISQUALIFYING:
                    # The tier is NOT consumed: the next-ranked story inherits
                    # it on the following pass. `disclose` separates L2's
                    # subject (fetches that actually failed) from a slot that
                    # never opened a socket — only the former earns his
                    # sentence, and the flag is decided HERE, next to the
                    # outcome that justifies it, so no renderer has to
                    # re-derive intent from a string.
                    fetch_skipped.append({
                        "slot": sa.slot, "story_title": sa.story_title,
                        "outcome": sa.outcome,
                        "disclose": sa.outcome == FETCH_SKIP_OUTCOME})
                    continue
                depth_tiers[i - 1] = queue.pop(0)
            else:
                depth_tiers[i - 1] = tier
        # The per-slot tier vector this stage actually ran, full length, with
        # non-depth slots as "quick". The writer and the reader surfaces are
        # positional today (nine homes across four modules), so THIS is the
        # value the propagation will consume when an arm is ruled — reported
        # rather than applied, because applying it is the forked half.
        report["depth_tiers"] = [t or "quick" for t in depth_tiers]
        report["fetch_skipped"] = fetch_skipped
        report["depth_slots"] = [n for n, t in enumerate(depth_tiers, start=1)
                                 if t is not None]
        report["total_usd"] = round(charged_total, 6)          # REAL money
        report["total_usd_shadow"] = round(spent - already_spent, 6)  # the cap figure
        # NL-148 CLAUSE 4 — THE SYSTEMIC-FAILURE VERDICT. This stage REPORTS
        # it; `generate` acts on it. The split is deliberate and it is not a
        # style choice: the ruling says "pause GENERATION", which is an
        # orchestration decision over the whole pipeline, and this function is
        # also a directly-callable stage API that a dozen tests and the CLI
        # drive on their own. A stage that raised here would be deciding the
        # fate of a run it does not own.
        #
        # "Nothing fetches at all", read literally and measured on the fetch
        # layer itself. FOUR CONJUNCTS, each closing a state that is NOT this:
        #   * `per_story` non-empty — a day with no depth stories at all is
        #     'no-depth-stories', an editorial fact, not a fetch failure;
        #   * ANY prioritized slot ATTEMPTED at least one fetch. This is what
        #     keeps a POLICY-ONLY day out: if every slot's sources sit outside
        #     the analyst's fetch tiers, nothing opened a socket, and a run that
        #     never opened a socket has not discovered that the network is down
        #     (the principal's 2026-07-06 boundaries working, never a failure to
        #     fetch);
        #   * EVERY prioritized slot got zero extractions — nothing came back
        #     anywhere;
        #   * no valid brief exists for the date. If any prioritized story
        #     still produced a full-picture brief — Sonar carried it, or a
        #     re-run's earlier work stands — then an edition CAN be built and
        #     pausing would throw away work the reader could have had.
        #
        # THE any/all SPLIT IS FIX-1 (gate Ruling A, 2026-08-12), and it is a
        # correction, not a re-legislation. The shape used to be
        # `all(attempted and not ok)`, which reads a tier-excluded slot's
        # non-attempt as a VETO — and an excluded slot attempts nothing on every
        # day, healthy or dead. Measured (gate probe P-A, re-derived on the
        # final tree): slot 1 attempted-and-all-failed, slot 2 wholly excluded,
        # model down, zero valid briefs — genuinely nothing fetched anywhere —
        # and the verdict came back False. That made outage detection a function
        # of EDITORIAL CONFIGURATION. His sentence's subject is fetching, not
        # slot composition: a slot that never opened a socket carries zero
        # information about fetch health, so it may not vote either way. `any()`
        # asks "did we actually try?"; `all(not ok)` asks "did anything come
        # back?". Pinned both ways —
        # test_clause4_one_tier_excluded_slot_cannot_shield_a_network_outage
        # (mixed day must pause) and
        # test_clause4_tier_excluded_sources_are_policy_not_a_fetch_failure
        # (all-excluded day must not).
        #
        # The last conjunct is what makes the RULED DEFAULT hold by construction
        # rather than by a resume mechanic: "already-completed stories never
        # re-run and never re-bill" is safe because the verdict cannot be true
        # while a completed story exists — at the pause there is no completed
        # work for a retry to re-bill.
        # CORRECTED (QA F4, 2026-08-12): that is NOT "the retry is free". A
        # model that is up can still synthesize and REJECT briefs for slots
        # whose fetches all failed, at real cost ($0.74 measured on one such
        # pause), and the retry re-runs those attempts — incomplete work, which
        # the ruling never protected. See SystemicFetchFailure's docstring.
        #
        # HOW OFTEN THIS IS TRUE IN PRACTICE, measured on the principal's own
        # data/generation_log.jsonl rather than guessed: across the 19 recorded
        # analysis stages, ZERO would have tripped it, and 56 of 57 prioritized
        # slots (98.2%) got at least one full text. It is an outage signal, not
        # a normal day.
        report["fetch_systemic_failure"] = bool(
            report["per_story"]
            and any(s["fetch_attempted"] for s in report["per_story"])
            and all(not s["fetch_ok"] for s in report["per_story"])
            and not any_valid_brief(con, date))
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
        if _own_analyst:      # FIX-1 (B4-D1): teardown the stage's published seat
            _clear_analyst()


def _render_prompt(template: str, mapping: Dict[str, str]) -> str:
    for k, v in mapping.items():
        template = template.replace("{" + k + "}", v)
    return template


def positional_tiers(n: int) -> List[str]:
    """The A2 positional contract: 1 lead full, 2 medium, the rest quick.

    The expression this replaces was written out at four sites (here,
    generate's validator, and server's two tier derivations). It is the
    FALLBACK now rather than the rule — every consumer prefers the vector when
    one exists — so it gets one name, and a future amendment to the shape of
    the depth tier has one place to land."""
    return ["full" if i == 0 else "medium" if i <= 2 else "quick"
            for i in range(n)]


def _newest_log_entry(date: str, match) -> Optional[Dict]:
    """The LAST generation-log line for `date` satisfying `match`.

    One scanner for the two record-derived tier reads below, so they cannot
    drift on how the log is parsed (last-wins, unreadable lines skipped). A
    corrupt line is passed over rather than raising: the log is an append-only
    forensic record a crashed run can tear, and a tier lookup is not the place
    to turn that into a dead run."""
    from . import paths
    log = paths.DATA_DIR / "generation_log.jsonl"
    found: Optional[Dict] = None
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("date") == date and match(e):
                found = e
    return found


def _tiers_for(date: str, n: int) -> List[str]:
    e = _newest_log_entry(
        date, lambda e: not e.get("sample") and bool(e.get("tiers")))
    tiers = e["tiers"] if e else None
    if tiers and len(tiers) >= n:
        return tiers[:n]
    return positional_tiers(n)


def depth_tiers_from_record(date: str, n: int) -> List[str]:
    """The depth-tier vector this date's ANALYSIS STAGE actually ran, read back
    off the run record. `[]` when no armed stage has run for the date.

    WHY THIS EXISTS — the L1 hole the propagation would otherwise open, and it
    is a hole in the paths the principal uses by hand. `--no-refresh`, a
    sample, and the ledger backfill all skip the analysis stage, so there is no
    live `depth_tiers` for them to consume. Falling back to the positional A2
    vector there would put a fetch-failed story back into a depth slot with no
    brief behind it — a degraded lead, produced by the very machinery that
    exists to forbid one. So the vector is RECOVERED instead.

    THE STAGE ENTRY AND NOT THE RUN ENTRY, deliberately: a `--no-refresh`
    completing an interrupted regenerate has a stage entry (the stage ran, then
    the run died) and no run entry at all. `_tiers_for` above reads the run
    entry because its question is different — what did the last PUBLISHED
    edition look like.

    Not gated on the arm here. The gate belongs at the consumer
    (`generate.edition_tiers`), because this function is also how a reader or a
    probe asks the record a question, and a record read that lies depending on
    a module constant would be worse than no reader at all."""
    e = _newest_log_entry(
        date, lambda e: e.get("stage") == "analysis" and bool(e.get("depth_tiers")))
    if not e:
        return []
    tiers = list(e["depth_tiers"])
    return (tiers + ["quick"] * n)[:n]


# X4 (case file 2026-07-28) — the exact-4,000-char prior-briefing rows. The
# old head-truncation `narrative_text[:4000]` cut a WHOLE EDITION (16,582
# chars on 2026-07-25) at its first story, so a slot-2 thread's prior record
# reached the analyst as slot 1's coverage. Measured on the cockroach thread:
# compensat@5,140 Wangchuk@5,683 reform@7,254 "dropping police cases"@5,095
# — every one of them past the cut, and `instr()=0` in all four persisted P
# rows (briefs 47 + 50). The continuity leg LINKED the prior edition and FED
# a rendering missing its substance.
_PRIOR_SECTION_RE = re.compile(r"^\*\*(.+?)\*\*\s*$", re.M)


def split_briefing_sections(narrative: str) -> List[Tuple[str, str]]:
    """A briefing narrative -> [(headline, section_text)], preamble first
    under the empty headline. The rendered edition is `# NewsLens — <date>`,
    an index, then one `**Headline**` block per story separated by `---`."""
    text = narrative or ""
    marks = list(_PRIOR_SECTION_RE.finditer(text))
    if not marks:
        return [("", text)] if text.strip() else []
    out: List[Tuple[str, str]] = []
    head = text[:marks[0].start()].strip()
    if head:
        out.append(("", head))
    for i, m in enumerate(marks):
        stop = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(1).strip(), text[m.start():stop].strip()))
    return out


_STOPWORDS = {"the", "and", "for", "with", "from", "after", "over", "into",
              "its", "his", "her", "their", "are", "was", "were",
              "new", "first", "amid", "says", "said", "than", "that", "this",
              "more", "than", "again", "still", "under", "against", "about"}


def _story_terms(slot: Dict) -> List[str]:
    """Distinctive lowercase terms naming this story: title + thread topics.
    The SUMMARY is deliberately excluded — it is ranking's prose and drags in
    generic vocabulary that matches every section."""
    raw = " ".join([str(slot.get("story_title") or "")]
                   + [str(t) for t in (slot.get("matched_memory") or [])])
    terms = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", raw)}
    return sorted(t for t in terms if len(t) > 3 and t not in _STOPWORDS)


def _term_patterns(terms: List[str]) -> List[re.Pattern]:
    # word-START anchored so "force" reaches "forces"/"forced" but never
    # "enforcement"; inflection without a stemmer dependency
    return [re.compile(r"\b" + re.escape(t) + r"\w*", re.I) for t in terms]


def _relevance(section: Tuple[str, str], patterns: List[re.Pattern]) -> int:
    """Distinctive terms this section carries. A HEADLINE hit is worth three
    body hits: the section title is the story's own name, and body words
    ("forces", "protests") wander across an edition."""
    headline, body = section
    return sum(3 * bool(p.search(headline)) + bool(p.search(body))
               for p in patterns)


def _distinctive(sections: List[Tuple[str, str]],
                 patterns: List[re.Pattern]) -> List[re.Pattern]:
    """Drop terms that are common across THIS edition — a word appearing in a
    third of the day's stories identifies nothing. Self-calibrating, so the
    stoplist above stays short and no hand-tuned news vocabulary accretes."""
    stories = [s for s in sections if s[0]]
    if not stories:
        return patterns
    ceiling = max(1, len(stories) // 3)
    keep = [p for p in patterns
            if sum(1 for s in stories if p.search(s[1])) <= ceiling]
    return keep or patterns


def prior_material_for_story(priors: List[Dict], slot: Dict,
                             budget_chars: int = 4000,
                             index_chars: int = 700) -> List[Dict]:
    """X4's fix: give the analyst THIS STORY's prior coverage, not the first
    4,000 characters of an unrelated edition.

    Each prior edition is split into its story sections and scored against the
    story's distinctive terms; a section qualifies only when its own HEADLINE
    names one of them. Qualifying sections are kept WHOLE, best match first,
    and only the tail is cut if the per-edition budget runs out.

    Fact-preserving BY SELECTION, not by summarisation: no model call, no
    rewriting of our own prior prose — the writer's own words reach the
    analyst verbatim, which is what makes "we previously reported X" a
    quotable claim rather than a paraphrase of a paraphrase.

    An edition that did not carry this story returns its INDEX (the "in
    today's briefing" list) with `matched: False`, and `build_source_map`
    stamps that on the key's own title. That is the honest degrade: the old
    head slice fed an unrelated story's coverage into the thread's record
    channel, which is precisely how the 07-26 edition re-broke the 07-25
    edition's news as fresh (X1) while re-asking questions the record had
    answered (X2). "We did not cover this yesterday" is a fact the analyst
    can use; four thousand characters about oil prices is not."""
    out: List[Dict] = []
    for pb in priors:
        narrative = pb.get("text") or ""
        sections = split_briefing_sections(narrative)
        patterns = _distinctive(sections, _term_patterns(_story_terms(slot)))
        hits = [(s, _relevance(s, patterns)) for s in sections if s[0]]
        hits = [(s, score) for s, score in hits if score >= 3]  # headline hit
        hits.sort(key=lambda pair: -pair[1])
        if hits:
            body, used = [], 0
            for (_, section_text), _score in hits:
                if used and used + len(section_text) > budget_chars:
                    break
                body.append(section_text[:budget_chars - used])
                used += len(body[-1]) + 2
            out.append({"date": pb.get("date"), "text": "\n\n".join(body),
                        "matched": True})
        else:
            preamble = next((s[1] for s in sections if not s[0]), "")
            out.append({"date": pb.get("date"),
                        "text": preamble[:index_chars] or narrative[:index_chars],
                        "matched": False})
    return out


def _prior_briefing_material(con: sqlite3.Connection, date: str,
                             cap: int = 2) -> List[Dict]:
    """The last `cap` editions, WHOLE. Selection to the slot's own story
    happens per-slot in `prior_material_for_story` — this stage-level read
    cannot know which story it is feeding, and truncating here is what X4
    was."""
    rows = con.execute(
        "SELECT date, narrative_text FROM briefings WHERE date < ?"
        " ORDER BY date DESC LIMIT ?", (date, cap)).fetchall()
    return [{"date": r["date"], "text": r["narrative_text"] or ""}
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
        # §5.3: the WRITER view carries degradation directives. The persisted
        # title is reader-safe (_persisted_source_row), so the X4 directive is
        # re-attached here — this channel is exactly where it belongs.
        title = s["title"]
        if s.get("record_status") == DEGRADE_RECORD_STATUS:
            title += DEGRADE_TITLE_SUFFIX
        parts.append(f"  [{s['key']}] {s['outlet']} — {title} ({s['kind']})"
                     + (f" {s['url']}" if s["url"] else ""))
    if brief.get("notes_for_writer"):
        parts.append(f"\nNOTE FOR WRITER: {brief['notes_for_writer']}")
    return "\n".join(parts)
