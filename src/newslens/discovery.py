"""Tier-2 discovery: one capped Perplexity Sonar call per run (milestone 2).

COLD SEAM until the principal grants PERPLEXITY_API_KEY: with no key present
this module builds no request and touches no socket — it reports itself as
skipped and the run proceeds RSS-only. The Sonar reliability spike (Rook's
dissent, DECISIONS.md 2026-07-02) stays gated on the key; when it lands, the
spike is one command: `scripts/sonar_spike`.

Guardrails (spec §A tier 2 + ENGINEERING.md cost rules):
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
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional
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


def _store_results(
    con: sqlite3.Connection, results: List[dict], now_iso: str
) -> int:
    """search_results -> source_items rows (source_type='sonar'), idempotent
    per (url, UTC fetch-day) like RSS rows. raw_excerpt stays NULL: we have
    title/url/date for these, not source text, and we don't fabricate."""
    day = now_iso[:10]
    stored = 0
    with con:
        for result in results[:MAX_DISCOVERY_ITEMS]:
            url = (result.get("url") or "").strip()
            title = (result.get("title") or "").strip()
            if not url.startswith(("http://", "https://")) or not title:
                continue
            outlet = urlparse(url).netloc or "unknown"
            existing = con.execute(
                "SELECT id FROM source_items WHERE url = ? AND date(fetched_at) = ?",
                (url, day),
            ).fetchone()
            if existing is not None:
                continue  # already known today (RSS beat us to it, or re-run)
            con.execute(
                "INSERT INTO source_items"
                " (source_type, outlet, url, title, published_at, fetched_at,"
                "  raw_excerpt, wire_syndication_flag)"
                " VALUES ('sonar', ?, ?, ?, ?, ?, NULL, 0)",
                (outlet, url, title[:500], result.get("date"), now_iso),
            )
            stored += 1
    return stored


def run_discovery(
    con: sqlite3.Connection,
    cfg: "config.SourcesConfig",
    env: Optional[dict] = None,
    report=None,
    now_iso: Optional[str] = None,
) -> str:
    """Mutates report.discovery_* and returns the status string.

    Skip states build NO request (the zero-network-when-keyless rule covers
    the pipeline, not just the doctor)."""
    import os

    src_env = env if env is not None else os.environ
    now_iso = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def finish(status: str, items: int = 0) -> str:
        if report is not None:
            report.discovery_status = status
            report.discovery_items = items
        return status

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
    stored = _store_results(con, results, now_iso)
    usage = payload.get("usage") or {}
    tokens = usage.get("total_tokens")
    return finish(
        f"ok — 1 Sonar call, {stored} discovered item(s) stored"
        + (f", {tokens} tokens (~${tokens / 1_000_000 * SONAR_USD_PER_MTOK:.4f})" if tokens else ""),
        items=stored,
    )
