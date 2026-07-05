"""Ranking + corroboration (milestone 3): the editor's story budget.

Pipeline position (spec §B steps 2-3): ingested source_items -> LLM-assisted
clustering & scoring -> deterministic selection -> corroboration labels ->
briefings row (story_slots + corroboration_labels + token_cost) + a
ranking_runs instrumentation row.

DIVISION OF JUDGMENT (ADR-0004): the LLM decides only what is genuinely
semantic — which items are the same story, which of the principal's tags a
story actually matches, and a world-impact score with a one-sentence reason.
Everything above that layer is deterministic, inspectable code: tag weights
(topic 1.0 / domain 0.5 — taxonomy contract §B rule 4), the followed-analyst
boost, slot selection, the urgency override gate and its cap, and
corroboration counting. "Why did this story rank?" must always be answerable
from stored data, not from a model's mood.

THE URGENCY OVERRIDE (taxonomy contract §E, Kass's dissent binding):
  * Pool: clusters with ZERO personal signal (no tag, no memory, no followed
    source). Followed-analyst content is never override material — it already
    carries a personal signal.
  * Bar: world_impact >= OVERRIDE_THRESHOLD (8/10 = "global systemic
    consequence... not merely widely covered"). Cap: at most 1 of the 5 slots.
    The slot may go unfilled — that is a normal outcome, not a failure.
  * Label: every fired override carries OVERRIDE_LABEL_PREFIX + the model's
    reason, stored on the slot AND rendered in output, every time, no silent
    fallback.
  * Instrumented: every run (fired or not) appends a ranking_runs row with the
    pool size, threshold, and outcome — the day-14 recalibration reads these.

STRUCTURED-OUTPUT DISCIPLINE (ENGINEERING.md): the LLM response is validated
hard (shape, id existence, no cross-cluster id reuse, tag names/levels only
from the provided sets, score ranges). One retry total; then a visible
RankingError — never silent garbage downstream. Failed runs still log a
ranking_runs row with status=failed for the instrumentation trail.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import config, db, memory, paths

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"
LLM_TIMEOUT_S = 90
# 3000, raised from 1600 in M4: with memory threads in play, 12 clusters of
# title+summary+reason+matches measured right at the old cap and the model's
# JSON truncated mid-string on a live run (~$0.0018 worst case at 4o-mini
# rates — the budget guard scales with this constant automatically).
MAX_COMPLETION_TOKENS = 3000
PROMPT_FILE = "rank_select.txt"
USER_AGENT = "NewsLens/0.1 (personal news briefing prototype; ranking)"

# Recency rule (principal amendment 2026-07-04): candidate stories must have
# occurred/developed since the last briefing, or within the cap, whichever
# window is SHORTER. Principal gave 10-14 days; 14 chosen as the constant
# (ADR-0004 amendment). First-ever briefing defaults to the cap. "Developed"
# is measured by fetch time (first-seen) — published_at is too unreliable
# across feeds to anchor eligibility.
RECENCY_CAP_DAYS = 14
MAX_INPUT_ITEMS = 550       # most-recent cap so the prompt stays bounded
MAX_CLUSTERS = 12
MAX_SLOTS = 5

# Personal-impact weights (taxonomy contract §B rule 4 + §A followed_analyst).
# Deliberately code constants, not env vars: tuning them is a reviewed diff.
TOPIC_WEIGHT = 1.0
DOMAIN_WEIGHT = 0.5
MEMORY_WEIGHT = 1.0          # an active live thread matches at topic grade
FOLLOWED_BOOST = 0.35        # additive personal credit for followed writers
PERSONAL_SHARE = 0.55        # combined = 0.55*personal + 0.45*world/10
OVERRIDE_THRESHOLD = 8       # of 10 — "global systemic consequence"

OVERRIDE_LABEL_PREFIX = (
    "This story doesn't match your tagged interests, but we included it because "
)

# Standing caveat — rendered in every rank output AND stored, per the 07-02
# corroboration ruling ("caveat in the output, not just in docs").
CORROBORATION_CAVEAT = (
    "Corroboration counts distinct outlets in your source list; it does not "
    "detect uncredited wire-service reuse beyond the excluded domains listed, "
    "and a single well-sourced report is not automatically less reliable than "
    "several outlets repeating one wire story."
)

# gpt-4o-mini pricing (spec §B), for the pre-call budget estimate + cost log.
USD_PER_MTOK_IN = 0.15
USD_PER_MTOK_OUT = 0.60


class RankingError(RuntimeError):
    """Visible, handled ranking failure — the CLI prints it and exits 1."""


@dataclass
class RankedSlot:
    slot: int
    story_title: str
    summary: str
    item_ids: List[int]
    outlets: List[str]                 # distinct, as stored on the items
    matched_tags: List[Dict[str, str]]
    matched_memory: List[str]
    followed_analyst: bool
    personal_score: float
    world_impact: int
    combined_score: float
    override: bool
    override_label: Optional[str]
    corroboration_count: int
    corroboration_label: str
    wire_items_excluded: int
    # Lifecycle v2 (ADR-0006): dormant-thread matches are MATCH-ONLY — they
    # contribute nothing to any score; they exist so a slot-earning story can
    # auto-revive a thread. revived_threads is filled by persist() with the
    # pre-revival coverage date for the narrative's back-reference.
    matched_dormant: List[str] = field(default_factory=list)
    revived_threads: List[Dict] = field(default_factory=list)


@dataclass
class RankReport:
    date: str
    slots: List[RankedSlot] = field(default_factory=list)
    caveat: str = CORROBORATION_CAVEAT
    override_fired: bool = False
    override_pool_size: int = 0
    item_count: int = 0
    cluster_count: int = 0
    window_days: float = 0.0        # recency window actually applied
    window_basis: str = ""          # "since your last briefing" / cap
    history_days: float = 0.0       # how much ingested lookback really exists
    token_usage: Dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def local_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def candidate_window(
    con: sqlite3.Connection, target_date: str, now_utc: Optional[datetime] = None
) -> Dict:
    """The recency window (principal amendment 2026-07-04):
    window = min(time since the last briefing, RECENCY_CAP_DAYS).

    "Last briefing" excludes the row for target_date itself — an idempotent
    re-rank of the same date must use the window since the PREVIOUS briefing,
    not since its own prior version minutes ago. First-ever briefing (no
    prior row) defaults to the cap.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    cap_start = now_utc - timedelta(days=RECENCY_CAP_DAYS)
    row = con.execute(
        "SELECT MAX(generated_at) AS last_at FROM briefings WHERE date != ?",
        (target_date,),
    ).fetchone()
    last_at = row["last_at"] if row else None
    basis = "first briefing — full cap"
    start = cap_start
    if last_at:
        try:
            last_dt = datetime.strptime(last_at[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            last_dt = None
        if last_dt is not None:
            last_dt = min(last_dt, now_utc)  # clock-skew clamp
            if last_dt > cap_start:
                start, basis = last_dt, "since your last briefing"
            else:
                basis = f"{RECENCY_CAP_DAYS}d cap (last briefing is older)"
    days = round((now_utc - start).total_seconds() / 86400.0, 2)
    return {
        "start_iso": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "days": days,
        "basis": basis,
        "last_briefing_at": last_at,
        "cap_days": RECENCY_CAP_DAYS,
    }


def ingested_history_days(
    con: sqlite3.Connection, now_utc: Optional[datetime] = None
) -> float:
    """How far back ingested items actually go — the honesty half of the
    recency rule: RSS feeds carry limited history, so early runs may have far
    less lookback than the window requests, and the report must say so."""
    now_utc = now_utc or datetime.now(timezone.utc)
    row = con.execute("SELECT MIN(fetched_at) AS oldest FROM source_items").fetchone()
    if not row or not row["oldest"]:
        return 0.0
    try:
        oldest = datetime.strptime(row["oldest"][:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return 0.0
    return max(0.0, round((now_utc - oldest).total_seconds() / 86400.0, 2))


def gather_items(con: sqlite3.Connection, start_iso: str) -> List[sqlite3.Row]:
    """Items fetched inside the candidate window, newest first, capped.

    Cluster eligibility ("a cluster qualifies iff its NEWEST item is
    in-window — an old story with a fresh development qualifies") holds BY
    CONSTRUCTION here: every clusterable item is in-window, so every cluster's
    newest item is too. The old story qualifies through its fresh items; we
    don't resurrect out-of-window rows to prove a story's age."""
    rows = con.execute(
        "SELECT id, source_type, outlet, url, title, published_at, fetched_at,"
        " wire_syndication_flag FROM source_items WHERE fetched_at >= ?"
        " ORDER BY fetched_at DESC, id DESC LIMIT ?",
        (start_iso, MAX_INPUT_ITEMS),
    ).fetchall()
    return rows


def active_memory_topics(con: sqlite3.Connection) -> List[str]:
    """Ranker reads active memory rows exactly like tags (taxonomy contract
    §A): ACTIVE only, capped at the 15 most-recently-referenced (spec §B).
    Delegates to memory.active_context — one implementation of the cap."""
    return memory.active_context(con)


def build_prompt(
    date_local: str,
    items: List[sqlite3.Row],
    cfg: config.SourcesConfig,
    memory_topics: List[str],
    window_desc: str,
    dormant: Optional[List[str]] = None,
) -> str:
    template = (paths.PROMPTS_DIR / PROMPT_FILE).read_text(encoding="utf-8")
    # Ascending id order + an explicit [id=N] key: copying exact ids out of a
    # ~550-line list is where the model slips. Live M4 findings, in order:
    # invented near-miss ids (fixed by ascending sort + temp 0), then numbers
    # LIFTED FROM HEADLINES as ids ("Top Links 1151" -> id 115, deterministic
    # at temp 0) — the bracketed key makes the id token structurally
    # unmistakable. Presentation only; selection order is irrelevant.
    # Brackets are sanitized out of titles so a headline can never fabricate
    # an "[id=N]" token (M4 gate: closes the id-in-headline class outright,
    # including a hostile feed publishing literal id markers).
    items_block = "\n".join(
        f"[id={r['id']}] {r['outlet']} | "
        + r["title"].replace("[", "(").replace("]", ")")
        for r in sorted(items, key=lambda r: r["id"])
    )
    tag_lines = [f"- {name} (domain)" for name in cfg.interests_broad]
    tag_lines += [f"- {name} (topic)" for name in cfg.interests_granular]
    memory_block = (
        "\n".join(f"- {t}" for t in memory_topics) if memory_topics else "(none right now)"
    )
    dormant_block = (
        "\n".join(f"- {t}" for t in dormant) if dormant else "(none right now)"
    )
    return template.format(
        date_local=date_local,
        window_desc=window_desc,
        items_block=items_block,
        tags_block="\n".join(tag_lines),
        memory_block=memory_block,
        dormant_block=dormant_block,
        max_clusters=MAX_CLUSTERS,
    )


# ---------------------------------------------------------------------------
# LLM call + hard validation
# ---------------------------------------------------------------------------

def estimate_cost_usd(prompt: str, max_completion: int = MAX_COMPLETION_TOKENS) -> float:
    in_tokens = len(prompt) / 3.5  # conservative chars-per-token
    return (in_tokens / 1e6) * USD_PER_MTOK_IN + (max_completion / 1e6) * USD_PER_MTOK_OUT


def usage_to_usd(usage: Dict) -> float:
    return (usage.get("prompt_tokens", 0) / 1e6) * USD_PER_MTOK_IN + (
        usage.get("completion_tokens", 0) / 1e6
    ) * USD_PER_MTOK_OUT


def _post_chat(key: str, prompt: str) -> Dict:
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,  # exact-copy discipline for ids/tag names (M4 live finding)
            "max_tokens": MAX_COMPLETION_TOKENS,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_CHAT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_S) as resp:
        return json.load(resp)


def validate_payload(
    payload: object,
    known_ids: set,
    tag_levels: Dict[str, str],
    memory_topics: List[str],
    dormant_topics: Optional[List[str]] = None,
) -> List[Dict]:
    """Hard schema validation of the LLM's cluster payload. Raises ValueError
    with ALL problems found (not just the first) so a retry/report is
    actionable. Extra unknown keys are tolerated; everything we consume is
    checked. matched_dormant (lifecycle v2) validates against the provided
    dormant list — match-only; scoring never sees it."""
    problems: List[str] = []
    if not isinstance(payload, dict) or not isinstance(payload.get("clusters"), list):
        raise ValueError("payload must be a JSON object with a `clusters` list")
    clusters = payload["clusters"]
    if len(clusters) > MAX_CLUSTERS * 2:
        raise ValueError(f"{len(clusters)} clusters — far over the {MAX_CLUSTERS} cap; refusing")

    seen_ids: set = set()
    valid: List[Dict] = []
    memory_set = set(memory_topics)
    dormant_set = set(dormant_topics or [])
    for i, c in enumerate(clusters, start=1):
        where = f"cluster #{i}"
        if not isinstance(c, dict):
            problems.append(f"{where}: not an object")
            continue
        title = c.get("story_title")
        if not isinstance(title, str) or not title.strip():
            problems.append(f"{where}: story_title missing/empty")
        summary = c.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append(f"{where}: summary missing/empty")
        ids = c.get("item_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(x, int) for x in ids):
            problems.append(f"{where}: item_ids must be a non-empty list of integers")
            ids = []
        unknown = [x for x in ids if x not in known_ids]
        if unknown:
            problems.append(f"{where}: invented item_ids {unknown}")
        dupes = [x for x in ids if x in seen_ids]
        if dupes:
            problems.append(f"{where}: item_ids {dupes} already used by another cluster")
        seen_ids.update(ids)

        mtags = c.get("matched_tags", [])
        if not isinstance(mtags, list):
            problems.append(f"{where}: matched_tags must be a list")
            mtags = []
        clean_tags: List[Dict[str, str]] = []
        for t in mtags:
            if (
                not isinstance(t, dict)
                or t.get("name") not in tag_levels
                or t.get("level") != tag_levels.get(t.get("name"))
            ):
                problems.append(f"{where}: matched_tags entry {t!r} is not an exact listed tag")
            else:
                clean_tags.append({"name": t["name"], "level": t["level"]})

        mmem = c.get("matched_memory", [])
        if not isinstance(mmem, list) or not all(isinstance(m, str) for m in mmem):
            problems.append(f"{where}: matched_memory must be a list of strings")
            mmem = []
        bad_mem = [m for m in mmem if m not in memory_set]
        if bad_mem:
            problems.append(f"{where}: matched_memory {bad_mem} not in the provided threads")

        mdorm = c.get("matched_dormant", [])
        if not isinstance(mdorm, list) or not all(isinstance(m, str) for m in mdorm):
            problems.append(f"{where}: matched_dormant must be a list of strings")
            mdorm = []
        bad_dorm = [m for m in mdorm if m not in dormant_set]
        if bad_dorm:
            problems.append(
                f"{where}: matched_dormant {bad_dorm} not in the provided dormant threads"
            )

        impact = c.get("world_impact")
        if not isinstance(impact, (int, float)) or isinstance(impact, bool) or not 0 <= impact <= 10:
            problems.append(f"{where}: world_impact must be a number 0-10")
            impact = 0
        reason = c.get("world_impact_reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{where}: world_impact_reason missing/empty")
            reason = ""

        valid.append(
            {
                "story_title": (title or "").strip()[:300],
                "summary": (summary or "").strip()[:400],
                "item_ids": ids,
                "matched_tags": clean_tags,
                "matched_memory": [m for m in mmem if m in memory_set],
                "matched_dormant": [m for m in mdorm if m in dormant_set],
                "world_impact": int(round(float(impact))),
                "world_impact_reason": reason.strip()[:400],
            }
        )
    if problems:
        raise ValueError("; ".join(problems))
    return valid


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """OpenAI puts the actionable part (error.code + message) in the body —
    surface it instead of guessing from the status code (found live in the M3
    spot-check: a bare '429' reads as a key problem when it's really quota)."""
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        err = payload.get("error") or {}
        code = err.get("code") or err.get("type") or ""
        msg = (err.get("message") or "").strip()[:200]
        return ": ".join(x for x in (code, msg) if x)
    except Exception:  # body unreadable/not JSON — the status alone will do
        return ""


def _retry_after_seconds(exc: urllib.error.HTTPError, default: float = 10.0) -> float:
    """Clamped to finite [0, 20] — a hostile/garbage Retry-After (negative,
    nan, inf) must never reach time.sleep(), where it would raise outside the
    RankingError taxonomy and bypass BUG-6 logging (M3 review carryover)."""
    try:
        value = float(exc.headers.get("Retry-After", default))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < 0:
        return default
    return min(value, 20.0)


def repair_duplicate_ids(payload: object) -> Tuple[object, Dict]:
    """THE one disclosed deterministic repair (M3 fix loop 1, live finding):
    on real ~600-item days the model puts a story that straddles topics into
    two clusters, re-using its item_ids. Contract: keep the item's FIRST
    cluster assignment, drop later duplicates, count every drop, disclose in
    the run report AND ranking_runs.meta — never silent. A cluster emptied by
    the repair is dropped whole (and disclosed). ONLY this violation class is
    repaired: invented ids, re-leveled tags, ranges, empty fields etc. still
    hard-reject in validate_payload — which also RETAINS its own duplicate
    check as a backstop behind this repair (defense in depth).

    Shapes this function can't interpret pass through untouched for
    validate_payload to reject with its usual diagnosis.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("clusters"), list):
        return payload, {"repaired": 0}
    seen: set = set()
    dropped: List[Dict] = []
    emptied: List[str] = []
    new_clusters: List = []
    for idx, c in enumerate(payload["clusters"], start=1):
        if not isinstance(c, dict) or not isinstance(c.get("item_ids"), list) or not c["item_ids"]:
            new_clusters.append(c)  # not this repair's class — validator's problem
            continue
        label = (str(c.get("story_title") or f"cluster #{idx}"))[:80]
        kept: List = []
        for x in c["item_ids"]:
            if isinstance(x, int) and x in seen:
                dropped.append({"item_id": x, "dropped_from": label})
                continue
            if isinstance(x, int):
                seen.add(x)
            kept.append(x)  # non-ints kept for the validator to reject
        if kept:
            fixed = dict(c)
            fixed["item_ids"] = kept
            new_clusters.append(fixed)
        else:
            emptied.append(label)
    if not dropped:
        return payload, {"repaired": 0}
    info = {
        "repaired": len(dropped),
        "dropped": dropped[:20],
        "clusters_emptied": emptied,
    }
    return {**payload, "clusters": new_clusters}, info


def call_llm_validated(
    key: str,
    prompt: str,
    known_ids: set,
    tag_levels: Dict[str, str],
    memory_topics: List[str],
    repairs: Optional[Dict] = None,
    dormant_topics: Optional[List[str]] = None,
) -> Tuple[List[Dict], Dict]:
    """One call + ONE retry total, then a visible RankingError.

    Between parse and validation, repair_duplicate_ids fixes (and counts) the
    one repairable violation class; pass a dict as `repairs` to receive the
    returning attempt's repair info (an out-param so the (clusters, usage)
    return shape stays stable).

    Retryable: 5xx, timeouts/connection failures, transient 429 rate limits,
    malformed/failed-validation output (the spec'd path). NOT retryable:
    auth (401/403), insufficient_quota 429 (retrying spends nothing and fixes
    nothing — it needs the principal's billing action), other 4xx."""
    last_error = "unknown"
    backoff = 1.0
    usage: Dict = {}
    for attempt in (1, 2):
        try:
            response = _post_chat(key, prompt)
            usage = response.get("usage") or {}
            choice = response["choices"][0]
            if choice.get("finish_reason") == "length":
                # Name truncation precisely — "malformed JSON" hides the real
                # cause (live M4 finding: completions hit the token cap).
                raise ValueError(
                    "completion truncated at the max_tokens cap "
                    f"({MAX_COMPLETION_TOKENS}) — response unusable"
                )
            content = choice["message"]["content"]
            payload = json.loads(content)
            payload, repair_info = repair_duplicate_ids(payload)
            clusters = validate_payload(
                payload, known_ids, tag_levels, memory_topics, dormant_topics
            )
            if repairs is not None:
                repairs.clear()
                repairs.update(repair_info)
            return clusters, usage
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            if exc.code in (401, 403):
                raise RankingError(
                    f"OpenAI rejected the key (HTTP {exc.code}"
                    + (f"; {detail}" if detail else "")
                    + ") — regenerate at platform.openai.com/api-keys and update .env"
                ) from exc
            if exc.code == 429:
                if "insufficient_quota" in detail:
                    raise RankingError(
                        f"OpenAI account has no available quota ({detail}) — the "
                        "key is valid but can't spend; add credits / check "
                        "billing at platform.openai.com (the doctor's read-only "
                        "key check cannot catch this)"
                    ) from exc
                last_error = f"rate limited (HTTP 429{'; ' + detail if detail else ''})"
                backoff = _retry_after_seconds(exc)
            elif exc.code >= 500:
                last_error = f"HTTP {exc.code}" + (f" ({detail})" if detail else "")
            else:
                raise RankingError(
                    f"OpenAI rejected the ranking call (HTTP {exc.code}"
                    + (f"; {detail}" if detail else "") + ")"
                ) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # malformed JSON / failed validation — the spec'd retry-then-fail path
            last_error = f"malformed LLM output ({exc})"
        except Exception as exc:  # timeout / connection — network-shaped
            last_error = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
        if attempt == 1:
            time.sleep(backoff)
    raise RankingError(
        f"ranking call failed after one retry: {last_error} — no briefing row "
        "was written; re-run `newslens rank` (this failure is logged)"
    )


# ---------------------------------------------------------------------------
# Deterministic selection, override, corroboration
# ---------------------------------------------------------------------------

def personal_score(cluster: Dict, followed: bool) -> float:
    weights = [
        TOPIC_WEIGHT if t["level"] == "topic" else DOMAIN_WEIGHT
        for t in cluster["matched_tags"]
    ]
    if cluster["matched_memory"]:
        weights.append(MEMORY_WEIGHT)
    base = max(weights) if weights else 0.0
    if followed:
        base += FOLLOWED_BOOST
    return min(base, 1.0)


def combined_score(personal: float, world_impact: int) -> float:
    return round(PERSONAL_SHARE * personal + (1 - PERSONAL_SHARE) * (world_impact / 10.0), 4)


def corroborate(items: List[sqlite3.Row]) -> Tuple[int, str, int, List[str]]:
    """Distinct-outlet counting with wire exclusion (07-02 ruling).
    Counts distinct stored outlets of non-wire RSS items. Sonar-discovered
    items are citable but are NOT 'named outlets' (not in the principal's
    list). LWW attribution ruling (ADR-0004): a URL syndicated across our own
    feeds holds ONE outlet attribution per day, so it counts once —
    undercounting, the conservative direction for a trust label."""
    named = sorted({
        r["outlet"] for r in items
        if r["source_type"] == "rss" and not r["wire_syndication_flag"]
    })
    wire_excluded = len([r for r in items if r["wire_syndication_flag"]])
    count = len(named)
    if count == 0:
        label = "Sourced via wire syndication or discovery only — treat as a single source"
    elif count == 1:
        label = "Reported by 1 named outlet"
    else:
        label = f"Reported by {count} named outlets"
    if wire_excluded:
        label += f" (plus {wire_excluded} wire-syndicated item(s), excluded from the count)"
    return count, label, wire_excluded, named


def select_slots(
    clusters: List[Dict],
    items_by_id: Dict[int, sqlite3.Row],
    followed_outlets: set,
) -> Tuple[List[RankedSlot], Dict]:
    scored = []
    for c in clusters:
        cluster_items = [items_by_id[i] for i in c["item_ids"] if i in items_by_id]
        followed = any(r["outlet"] in followed_outlets for r in cluster_items)
        p = personal_score(c, followed)
        scored.append((c, cluster_items, followed, p, combined_score(p, c["world_impact"])))

    primaries = sorted(
        (s for s in scored if s[3] > 0), key=lambda s: s[4], reverse=True
    )
    zero_pool = sorted(
        (s for s in scored if s[3] == 0),
        key=lambda s: (s[0]["world_impact"], s[4]),
        reverse=True,
    )

    override_pick = None
    if zero_pool and zero_pool[0][0]["world_impact"] >= OVERRIDE_THRESHOLD:
        override_pick = zero_pool[0]

    take_primary = MAX_SLOTS - (1 if override_pick else 0)
    chosen = primaries[:take_primary] + ([override_pick] if override_pick else [])
    chosen.sort(key=lambda s: s[4], reverse=True)

    slots: List[RankedSlot] = []
    for n, (c, cluster_items, followed, p, comb) in enumerate(chosen, start=1):
        count, label, wire_excluded, named = corroborate(cluster_items)
        is_override = override_pick is not None and c is override_pick[0]
        reason = c["world_impact_reason"].rstrip(".") + "."
        slots.append(
            RankedSlot(
                slot=n,
                story_title=c["story_title"],
                summary=c["summary"],
                item_ids=c["item_ids"],
                outlets=named,
                matched_tags=c["matched_tags"],
                matched_memory=c["matched_memory"],
                followed_analyst=followed,
                personal_score=round(p, 3),
                world_impact=c["world_impact"],
                combined_score=comb,
                override=is_override,
                override_label=(OVERRIDE_LABEL_PREFIX + reason) if is_override else None,
                corroboration_count=count,
                corroboration_label=label,
                wire_items_excluded=wire_excluded,
                # match-only: never touched personal_score/selection above —
                # carried through so persist() can apply earned-slot revival
                matched_dormant=c.get("matched_dormant", []),
            )
        )
    override_slot = next((s.slot for s in slots if s.override), None)
    meta = {
        "override": {
            "pool_size": len(zero_pool),
            "threshold": OVERRIDE_THRESHOLD,
            "fired": override_pick is not None,
            # world-impact of the best zero-match candidate (named precisely —
            # M3 review cosmetic: the old key read like a combined score)
            "top_zero_match_world_impact": zero_pool[0][0]["world_impact"] if zero_pool else None,
            "story": override_pick[0]["story_title"] if override_pick else None,
            "reason": override_pick[0]["world_impact_reason"] if override_pick else None,
            "slot": override_slot,
        },
        "weights": {
            "topic": TOPIC_WEIGHT, "domain": DOMAIN_WEIGHT, "memory": MEMORY_WEIGHT,
            "followed_boost": FOLLOWED_BOOST, "personal_share": PERSONAL_SHARE,
        },
        "model": MODEL,
        "prompt_file": PROMPT_FILE,
    }
    return slots, meta


# ---------------------------------------------------------------------------
# Persistence (idempotent per date; prior version archived first)
# ---------------------------------------------------------------------------

def persist(con: sqlite3.Connection, report: RankReport, meta: Dict) -> List[Dict]:
    """Upsert the briefings row for the date. If one exists, its current state
    is archived to briefings_history BEFORE overwrite (the idempotent-re-run
    rule binds from the first overwritable briefing — ADR-0001, live now).
    Every run also appends a ranking_runs instrumentation row.

    Lifecycle v2: applies earned-slot auto-revival here — POST-selection by
    construction (only slots that already won on merits reach this function),
    which is the hard constraint's guarantee that dormant threads never boost
    their own revival. Returns the revived list [{topic, last_covered}]."""
    # Revival PREVIEW before serialization: capture each matched dormant
    # thread's previous coverage date so the slot JSON carries the
    # back-reference ("last covered <date>") for M5's narrative.
    revived_preview: Dict[str, Dict] = {}
    for s in report.slots:
        for topic in s.matched_dormant:
            key = topic.casefold()
            if key in revived_preview:
                continue
            row = con.execute(
                "SELECT m.topic, b.date AS last_covered FROM memory m"
                " LEFT JOIN briefings b ON b.id = m.last_referenced_briefing_id"
                " WHERE lower(m.topic) = lower(?) AND m.status = 'dormant'",
                (topic,),
            ).fetchone()
            if row is not None:
                revived_preview[key] = {
                    "topic": row["topic"], "last_covered": row["last_covered"]
                }
    for s in report.slots:
        s.revived_threads = [
            revived_preview[t.casefold()]
            for t in s.matched_dormant
            if t.casefold() in revived_preview
        ]

    story_slots = json.dumps([s.__dict__ for s in report.slots])
    corroboration = json.dumps(
        {
            "standing_caveat": report.caveat,
            "per_story": [
                {
                    "slot": s.slot,
                    "corroboration_count": s.corroboration_count,
                    "corroboration_label": s.corroboration_label,
                    "wire_items_excluded": s.wire_items_excluded,
                    "outlets": s.outlets,
                }
                for s in report.slots
            ],
        }
    )
    token_cost = json.dumps(
        {
            "steps": [
                {
                    "step": "rank_select",
                    "model": MODEL,
                    "prompt_tokens": report.token_usage.get("prompt_tokens"),
                    "completion_tokens": report.token_usage.get("completion_tokens"),
                    "usd": round(usage_to_usd(report.token_usage), 6),
                }
            ],
            "total_usd": round(usage_to_usd(report.token_usage), 6),
        }
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with con:
        existing = con.execute(
            "SELECT * FROM briefings WHERE date = ?", (report.date,)
        ).fetchone()
        if existing is not None:
            con.execute(
                "INSERT INTO briefings_history (briefing_id, date, story_slots,"
                " corroboration_labels, narrative_text, script_text,"
                " audio_file_path, token_cost, generated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    existing["id"], existing["date"], existing["story_slots"],
                    existing["corroboration_labels"], existing["narrative_text"],
                    existing["script_text"], existing["audio_file_path"],
                    existing["token_cost"], existing["generated_at"],
                ),
            )
            # New slots invalidate any narrative written for the OLD slots —
            # NULL the generation fields on re-rank (M3 gate review, NOTES
            # item 11; the archived history row above preserves them).
            con.execute(
                "UPDATE briefings SET story_slots = ?, corroboration_labels = ?,"
                " token_cost = ?, generated_at = ?, narrative_text = NULL,"
                " script_text = NULL, audio_file_path = NULL WHERE id = ?",
                (story_slots, corroboration, token_cost, now, existing["id"]),
            )
            briefing_id = existing["id"]
        else:
            cur = con.execute(
                "INSERT INTO briefings (date, story_slots, corroboration_labels,"
                " token_cost, generated_at) VALUES (?, ?, ?, ?, ?)",
                (report.date, story_slots, corroboration, token_cost, now),
            )
            briefing_id = cur.lastrowid
        # Continuity's spine: matched threads record which briefing referenced
        # them (drives the dormancy clock + most-recently-referenced cap).
        matched_threads = [t for s in report.slots for t in s.matched_memory]
        if matched_threads:
            memory.update_references(con, briefing_id, matched_threads)
        # Earned-slot auto-revival (dormant -> active, dated; never touches
        # dismissed_user — memory.revive_matched filters on status='dormant').
        dormant_matched = [t for s in report.slots for t in s.matched_dormant]
        revived = (
            memory.revive_matched(con, briefing_id, dormant_matched)
            if dormant_matched
            else []
        )
        if revived:
            meta["revivals"] = revived
        con.execute(
            "INSERT INTO ranking_runs (date, meta, token_usage) VALUES (?, ?, ?)",
            (
                report.date,
                json.dumps(
                    {
                        **meta,
                        "status": "ok",
                        "item_count": report.item_count,
                        "cluster_count": report.cluster_count,
                        "slots": len(report.slots),
                    }
                ),
                # SQL NULL for absent usage, matching log_failed_run (M3
                # review cosmetic — one convention, not two).
                json.dumps(report.token_usage) if report.token_usage else None,
            ),
        )
    return revived


def log_failed_run(con: sqlite3.Connection, date: str, error: str) -> None:
    """Failures are instrumentation too — the day-14 readout must see them."""
    with con:
        con.execute(
            "INSERT INTO ranking_runs (date, meta, token_usage) VALUES (?, ?, NULL)",
            (date, json.dumps({"status": "failed", "error": error[:500]})),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_rank(
    date: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
    cfg: Optional[config.SourcesConfig] = None,
    env: Optional[dict] = None,
) -> RankReport:
    import os

    src_env = env if env is not None else os.environ
    date = date or local_today()

    cfg = cfg if cfg is not None else config.load_sources()
    if cfg.problems:
        raise RankingError("sources.yaml has problems: " + "; ".join(cfg.problems))
    if not cfg.has_interests:
        raise RankingError(
            "no interests configured in sources.yaml — ranking needs your tags "
            "(the personal-impact axis has nothing to match without them)"
        )
    key = (src_env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RankingError(
            "OPENAI_API_KEY not set — get one at platform.openai.com/api-keys, "
            "then add to .env (ranking is an LLM step; there is no keyless mode)"
        )

    own_con = con is None
    if own_con:
        db.migrate()
        con = db.connect()
    try:
        # BUG-6 (M3 fix loop 1): EVERY RankingError raised once the date is
        # known and a connection exists logs an instrumentation row — the
        # day-14 readout must see runs dying on a misconfigured cap or a
        # broken prompt, not just LLM failures. Refusals that happen before
        # this point (no key / no interests / sources problems) have no
        # connection to log through, by construction.
        try:
            return _run_rank_body(con, date, cfg, src_env, key)
        except RankingError as exc:
            log_failed_run(con, date, str(exc))
            raise
    finally:
        if own_con:
            con.close()


def _run_rank_body(
    con: sqlite3.Connection,
    date: str,
    cfg: config.SourcesConfig,
    src_env,
    key: str,
) -> RankReport:
    """run_rank's post-connection body. Raises RankingError for every handled
    failure; the caller logs each one to ranking_runs (BUG-6)."""
    # Memory sync FIRST: memory.md is the source of truth at generation time
    # (spec §B, literally) — the principal's hand edits must be in the DB
    # before the context is pulled. A broken file is a loud, logged failure:
    # silently ignoring memory edits is the transparency surface's one
    # unforgivable bug.
    try:
        mem_sync = memory.sync_memory(con)
    except memory.MemorySyncError as exc:
        raise RankingError(str(exc)) from exc
    # Snapshot the file identity the sync just wrote: the post-run refresh at
    # the bottom must not clobber a hand-edit made DURING the ~90s LLM call
    # (M4 gate optional, adopted — see the guarded write below).
    try:
        mem_mtime = paths.MEMORY_FILE.stat().st_mtime_ns
    except OSError:
        mem_mtime = None

    window = candidate_window(con, date)
    history = ingested_history_days(con)
    items = gather_items(con, window["start_iso"])
    if not items:
        raise RankingError(
            f"no ingested items inside the candidate window "
            f"({window['days']}d, {window['basis']}) — if your last briefing "
            "was moments ago, nothing new has arrived since; otherwise run "
            "`newslens ingest` first"
        )
    memory_topics = active_memory_topics(con)
    # Lifecycle v2: dormant threads join the prompt as a MATCH-ONLY
    # vocabulary — zero scoring influence (personal_score never reads
    # matched_dormant); a match only matters after a story has already
    # earned its slot, when persist() applies the auto-revival.
    dormant = memory.dormant_topics(con)
    window_desc = f"the last {window['days']:g} day(s), {window['basis']}"

    try:
        prompt = build_prompt(date, items, cfg, memory_topics, window_desc, dormant)
    except OSError as exc:
        raise RankingError(f"cannot read prompts/{PROMPT_FILE} ({exc})") from exc
    except Exception as exc:  # noqa: BLE001 — principal-editable template:
        # ANY render error (KeyError from a typo'd {placeholder}, etc.)
        # must be a visible, named failure — same class-wide discipline as
        # discovery's BUG-3 fix.
        raise RankingError(
            f"prompts/{PROMPT_FILE} did not render "
            f"({type(exc).__name__}: {exc}) — check its {{placeholders}}"
        ) from exc

    cap = config.budget_cap_usd_per_run(src_env)
    est = estimate_cost_usd(prompt)
    if est > cap:
        raise RankingError(
            f"estimated ranking cost ${est:.4f} exceeds BUDGET_CAP_USD_PER_RUN "
            f"${cap:.2f} — aborting before the call (raise the cap in .env if "
            "this is intentional)"
        )

    tag_levels = {name: "domain" for name in cfg.interests_broad}
    tag_levels.update({name: "topic" for name in cfg.interests_granular})
    known_ids = {r["id"] for r in items}

    repair_sink: Dict = {}
    clusters, usage = call_llm_validated(
        key, prompt, known_ids, tag_levels, memory_topics,
        repairs=repair_sink, dormant_topics=dormant,
    )

    items_by_id = {r["id"]: r for r in items}
    followed_outlets = {s.name for s in cfg.followed_analyst_sources}
    slots, meta = select_slots(clusters, items_by_id, followed_outlets)
    meta["window"] = window
    meta["history_days"] = history
    if repair_sink.get("repaired"):
        # Disclosed repair (never silent): the warning renders in CLI output
        # and the detail persists in ranking_runs.meta.repairs.
        meta["repairs"] = repair_sink

    report = RankReport(
        date=date,
        slots=slots,
        item_count=len(items),
        cluster_count=len(clusters),
        window_days=window["days"],
        window_basis=window["basis"],
        history_days=history,
        token_usage=usage,
    )
    report.override_fired = meta["override"]["fired"]
    report.override_pool_size = meta["override"]["pool_size"]
    # Memory surfacing (spec §B: staleness is SURFACED, never silent; sync
    # edits are acknowledged so the principal knows the file was honored).
    report.warnings.extend(mem_sync.summary_lines())
    if repair_sink.get("repaired"):
        emptied = repair_sink.get("clusters_emptied") or []
        report.warnings.append(
            f"clustering repair: {repair_sink['repaired']} duplicate item "
            f"assignment(s) dropped (kept each item's first cluster"
            + (f"; {len(emptied)} cluster(s) emptied and removed" if emptied else "")
            + ") — details stored in ranking_runs.meta.repairs"
        )
    if history < window["days"]:
        # The honesty half of the recency rule: never imply a lookback
        # the ingested corpus doesn't actually have.
        report.warnings.append(
            f"candidate window: {window['days']:g}d ({window['basis']}); "
            f"ingested history available: {history:g}d — early runs see "
            "less than the window requests"
        )
    if len(items) == MAX_INPUT_ITEMS:
        report.warnings.append(
            f"item window hit the {MAX_INPUT_ITEMS}-item cap — oldest items in "
            "the window were not considered"
        )
    revived = persist(con, report, meta)
    if revived:
        # Every automatic transition is surfaced, dated, never silent
        # (lifecycle v2 contract).
        names = ", ".join(
            r["topic"] + (f" (last covered {r['last_covered']})" if r["last_covered"] else "")
            for r in revived
        )
        report.warnings.append(
            f"memory: {len(revived)} dormant thread(s) auto-revived by "
            f"slot-earning stories: {names} — see memory.md"
        )
    # memory.md must reflect THIS run's own effects (revivals, new reference
    # dates) immediately — not on the next run's sync. Render-only, and
    # GUARDED: if the file changed since the opening sync wrote it (a hand
    # edit during the LLM call), skip the refresh and say so — a transparency
    # surface never overwrites edits it hasn't read (M4 gate optional, adopted).
    try:
        current_mtime = paths.MEMORY_FILE.stat().st_mtime_ns
    except OSError:
        current_mtime = None
    if mem_mtime is not None and current_mtime != mem_mtime:
        report.warnings.append(
            "memory.md changed while this run was in flight — post-run refresh "
            "skipped to protect your edit; the next sync will reconcile it"
        )
    else:
        try:
            paths.MEMORY_FILE.write_text(memory.render_file(con), encoding="utf-8")
        except OSError as exc:  # non-fatal (opening sync validated
            # writability), but never silent:
            report.warnings.append(
                f"memory.md could not be refreshed after this run ({exc}) — it "
                "will catch up on the next sync"
            )
    return report
