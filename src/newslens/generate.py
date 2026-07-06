"""The writer (milestone 5): narrative generation + script adaptation.

Implements the Content Lead's contract
(workspace/debates/2026-07-05--newslens--content.md §5). The architectural
rule inherited from §5.7 and M3: FURNITURE IS CODE-OWNED. The model writes
only the per-story prose movements (headline / lede / why_it_matters /
watch_for, plus my_read on variant-B days) as validated JSON fields; code
assembles everything deterministic — title line, at-a-glance list, the
canonical override label, per-story meta-lines, the footer block (window
honesty line + standing caveat verbatim + variant stamp). Binding labels
never depend on a stochastic writer.

Voice variants (§5.2): strict daily alternation, A on even date-ordinals
(anchor: 2026-07-05, dogfood day 1, is A), computed — never model-chosen.
Forcing the off-parity variant produces a SAMPLE: rendered to a file, never
written to the briefings row, so alternation-of-record stays clean.

Chain semantics (ADR-0007): `generate` is end-to-end on-demand — by default
it runs ingest (fresh pull) then rank (fresh budget; idempotent, archived)
then writes. `--no-refresh` consumes the existing briefing row instead
(narrative-only iteration; also how the variant-B sample avoids re-ranking).

Script pass (§5.8): input is the assembled narrative text + structured label
data ONLY — never raw sources. The fact-subset rule and hedge preservation
are validated heuristically (§5.9 items 7-8: warn-grade, flagged for review);
mandatory disclosures (override spoken elements, revival dates, the frozen
spoken caveat and sign-off) are presence-checked hard.

Instrumentation (§5.10) is a state file, not a migration:
data/generation_log.jsonl — one append-only JSON line per generate attempt
(variant, sample, word counts, per-step costs, disclosure renders, failures).
M7's read/listen events join against it by date.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config, db, memory, paths, ranking

# Writer model (principal amendment 2026-07-05, DECISIONS.md): the writer
# passes (narrative A/B + script) run on GPT-4o — 4o-mini failed the content
# contract's pre-registered register-holding trigger on day 1 ("quality of
# analysis and prose was not good enough"). Ranking has its own seam
# (ranking.RANK_MODEL — up-tiered to gpt-4o 2026-07-05, mini documented as
# the fallback rung). One named constant per seam; the fallback ADR's next
# rung (Claude-class) would be a one-line change here.
WRITER_MODEL = "gpt-4o"
WRITER_USD_PER_MTOK_IN = 2.50
WRITER_USD_PER_MTOK_OUT = 10.00
LLM_TIMEOUT_S = 120
NARRATIVE_MAX_TOKENS = 2800
SCRIPT_MAX_TOKENS = 3200
NARRATIVE_TEMPERATURE = 0.3
SCRIPT_TEMPERATURE = 0.4

PROMPT_A = "narrative_variant_a.txt"
PROMPT_EDITOR = "editor_pass.txt"
EDITOR_MAX_TOKENS = 2800
EDITOR_TEMPERATURE = 0.2
PROMPT_B = "narrative_variant_b.txt"
PROMPT_SCRIPT = "script_adapt.txt"

BRIEFINGS_DIR_NAME = "briefings"
GENERATION_LOG_NAME = "generation_log.jsonl"

# Word bands [KNOB] — §5.1 totals / §5.8 script band. Warn-grade (§5.9 #9).
NARRATIVE_BAND = (900, 1300)               # 5-slot day; scaled by slot count
SCRIPT_BAND = (1600, 2000)
PER_SLOT_WORDS = {1: 320, 2: 220, 3: 220, 4: 140, 5: 140}
SCRIPT_SEGMENTS = {1: 500, 2: 350, 3: 350, 4: 200, 5: 200}
SCRIPT_OPEN_WORDS = 120
SCRIPT_OUTRO_WORDS = 120

# A1 (principal editorial review 2026-07-05): variant A is THE voice; B is
# retired and the alternation window ended early (alternation_end logged).
# The parity code below stays dormant for historical reproducibility.
ACTIVE_VOICE = "A"
# Variant anchor: A on EVEN toordinal — 2026-07-05 (dogfood day 1) is even.
VARIANT_A_PARITY = 0

# --- Canonical strings (contract §5.7 / §5.2 / §5.8; verbatim, frozen) -------
OVERRIDE_TEXT_LABEL = (
    "**Outside your interests:** this story matches none of the tags or "
    "threads steering your selection; it's here because {reason}"
)
WINDOW_LINE = (
    "Generated {timestamp}. Covers items fetched {start} → {end}. NewsLens "
    "sees only its configured sources within this window."
)
VARIANT_B_STAMP = (
    'Voice: B — includes the narrator\'s own analytical judgments, always '
    'labeled "My read."'
)
VARIANT_A_STAMP = "Voice: A."
SPOKEN_CAVEAT = (
    "The usual reminder: outlet counts measure independent pickup across "
    "your sources, not truth — one strong single-source report can beat five "
    "copies of the same wire story."
)
SIGNOFF = "That's your briefing."

# Banned-string scan (§5.9 #10) — lowercase substring matching.
BANNED_STRINGS = [
    "remains to be seen", "only time will tell", "time will tell",
    "could potentially", "bears watching",
    "canary in the coal mine", "perfect storm", "domino effect",
    "tip of the iceberg", "watershed moment", "game-changer",
    "see you tomorrow",
    "you read", "you skipped",
    "impact score", "/10",
]

# A3 warn-scans (principal's own examples; warn-grade — quotes are legal)
TRUISM_WARN_STRINGS = [
    "critical component of", "profound implications", "raises questions about",
    "remains to be seen", "underscores the importance", "highlights the importance",
    "strain household budgets", "far-reaching consequences",
]
MORALIZE_WARN_STRINGS = ["divisive", "controversial", "troubling", "worrisome"]
MECHANICAL_TRANSITIONS = ["turning to", "in economic news", "finally,"]

# A7 (Round 2): sanctioned framing menus — the writer declares a framing per
# movement to fit the story; validators check MEMBERSHIP, never fixed names.
WHY_FRAMINGS = (
    "Why it matters", "Why markets care", "The debate", "What's unknown",
    "The background", "The stakes", "What changed",
)
WATCH_FRAMINGS = (
    "Watch for", "What happens next", "The next test", "What would change this",
)

_WORD_RE = re.compile(r"\b\w+\b")
_NUM_RE = re.compile(r"\d[\d,.]*")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


class GenerateError(RuntimeError):
    """Visible, handled generation failure — CLI prints it and exits 1."""


@dataclass
class GenReport:
    date: str
    variant: str
    sample: bool = False
    no_threads: bool = False
    narrative_text: str = ""
    script_text: str = ""
    narrative_words: int = 0
    script_words: int = 0
    per_story_words: List[int] = field(default_factory=list)
    steps: List[Dict] = field(default_factory=list)   # per-step token costs
    warnings: List[str] = field(default_factory=list)
    artifact_path: str = ""
    ingest_summary: str = ""
    continuity_status: str = "none"   # ok | none | corrupt


def wc(text: str) -> int:
    return len(_WORD_RE.findall(text))


def variant_for(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return "A" if d.toordinal() % 2 == VARIANT_A_PARITY else "B"


def _spoken_date(date_str: str) -> Tuple[str, str]:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = d.strftime("%A")
    return weekday, f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _time_of_day() -> str:
    h = datetime.now().hour
    if h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    return "evening"


# ---------------------------------------------------------------------------
# LLM call (same error taxonomy as ranking's, different knobs per pass)
# ---------------------------------------------------------------------------

def _chat(key: str, prompt: str, max_tokens: int, temperature: float,
          json_mode: bool) -> Dict:
    body = {
        "model": WRITER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        ranking.OPENAI_CHAT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "NewsLens/0.1 (personal news briefing prototype; writer)",
        },
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_S) as resp:
        return json.load(resp)


def call_llm(key: str, prompt: str, step: str, max_tokens: int,
             temperature: float, json_mode: bool,
             validate=None) -> Tuple[str, Dict]:
    """One call + ONE retry total (network-shaped, truncation, or validation
    failure), then GenerateError. Returns (content, usage). `validate`
    raises ValueError to trigger the retry path."""
    last_error = "unknown"
    backoff = 1.0
    for attempt in (1, 2):
        try:
            response = _chat(key, prompt, max_tokens, temperature, json_mode)
            usage = response.get("usage") or {}
            choice = response["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError(
                    f"completion truncated at the {step} token cap ({max_tokens})"
                )
            content = choice["message"]["content"]
            if validate is not None:
                validate(content)
            return content, usage
        except urllib.error.HTTPError as exc:
            detail = ranking._http_error_detail(exc)
            if exc.code in (401, 403):
                raise GenerateError(
                    f"OpenAI rejected the key (HTTP {exc.code}"
                    + (f"; {detail}" if detail else "")
                    + ") — regenerate at platform.openai.com/api-keys"
                ) from exc
            if exc.code == 429 and "insufficient_quota" in detail:
                raise GenerateError(
                    f"OpenAI account has no available quota ({detail}) — add "
                    "credits / check billing at platform.openai.com"
                ) from exc
            if exc.code == 429:
                last_error = f"rate limited (HTTP 429{'; ' + detail if detail else ''})"
                backoff = ranking._retry_after_seconds(exc)
            elif exc.code >= 500:
                last_error = f"HTTP {exc.code}" + (f" ({detail})" if detail else "")
            else:
                raise GenerateError(
                    f"OpenAI rejected the {step} call (HTTP {exc.code}"
                    + (f"; {detail}" if detail else "") + ")"
                ) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            last_error = f"invalid {step} output ({exc})"
        except Exception as exc:  # timeout / connection — network-shaped
            last_error = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
        if attempt == 1:
            time.sleep(backoff)
    raise GenerateError(
        f"{step} failed after one retry: {last_error} — nothing was written; "
        "re-run `newslens generate` (this failure is logged)"
    )


def _est_cost(prompt: str, max_tokens: int) -> float:
    return (len(prompt) / 3.5 / 1e6) * WRITER_USD_PER_MTOK_IN + (
        max_tokens / 1e6
    ) * WRITER_USD_PER_MTOK_OUT


def _step_cost(usage: Dict) -> float:
    return (usage.get("prompt_tokens", 0) / 1e6) * WRITER_USD_PER_MTOK_IN + (
        usage.get("completion_tokens", 0) / 1e6
    ) * WRITER_USD_PER_MTOK_OUT


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

def load_briefing_inputs(con: sqlite3.Connection, date: str) -> Dict:
    row = con.execute(
        "SELECT * FROM briefings WHERE date = ?", (date,)
    ).fetchone()
    if row is None:
        raise GenerateError(
            f"no briefing row for {date} — generate the record first "
            "(a plain `newslens generate`), then request samples or "
            "narrative-only re-runs against it"
        )
    try:
        slots = json.loads(row["story_slots"] or "[]")
    except ValueError as exc:
        raise GenerateError(f"briefings.story_slots for {date} is corrupt: {exc}") from exc
    if not slots:
        raise GenerateError(
            f"the briefing row for {date} has no story slots — rank refused "
            "or produced nothing; re-run `newslens rank`"
        )
    items_by_slot: Dict[int, List[sqlite3.Row]] = {}
    for s in slots:
        ids = s.get("item_ids") or []
        marks = ",".join("?" for _ in ids) or "NULL"
        items_by_slot[s["slot"]] = con.execute(
            f"SELECT id, outlet, title, url, published_at, raw_excerpt,"
            f" source_type, wire_syndication_flag FROM source_items"
            f" WHERE id IN ({marks}) ORDER BY id",
            ids,
        ).fetchall() if ids else []

    threads = con.execute(
        "SELECT topic, principal_note FROM memory WHERE status = 'active'"
        " ORDER BY last_referenced_briefing_id IS NULL,"
        " last_referenced_briefing_id DESC, id LIMIT ?",
        (memory.CONTEXT_CAP,),
    ).fetchall()

    # Continuity, with the M4-gate mandated distinction: a prior row whose
    # slots JSON is corrupt is NOT the same as "no prior briefing" — silent
    # continuity loss is unacceptable in the product whose point is continuity.
    prior_row = con.execute(
        "SELECT id FROM briefings WHERE date < ? ORDER BY date DESC LIMIT 1",
        (date,),
    ).fetchone()
    prior_ctx = memory.prior_briefing_context(con, date)
    if prior_row is not None and prior_ctx is None:
        continuity_status = "corrupt"
    elif prior_ctx is not None:
        continuity_status = "ok"
    else:
        continuity_status = "none"

    window_meta = None
    run_row = con.execute(
        "SELECT meta, ran_at FROM ranking_runs WHERE date = ? AND"
        " json_extract(meta, '$.status') = 'ok' ORDER BY id DESC LIMIT 1",
        (date,),
    ).fetchone()
    if run_row:
        try:
            window_meta = {
                "window": json.loads(run_row["meta"]).get("window"),
                "ran_at": run_row["ran_at"],
            }
        except ValueError:
            window_meta = None

    try:
        corroboration = json.loads(row["corroboration_labels"] or "{}")
    except ValueError:
        corroboration = {}

    return {
        "row": row,
        "slots": slots,
        "items_by_slot": items_by_slot,
        "threads": threads,
        "prior_ctx": prior_ctx,
        "continuity_status": continuity_status,
        "window_meta": window_meta,
        "corroboration": corroboration,
    }


def _override_reason(slot: Dict) -> str:
    label = slot.get("override_label") or ""
    if label.startswith(ranking.OVERRIDE_LABEL_PREFIX):
        return label[len(ranking.OVERRIDE_LABEL_PREFIX):].strip()
    return label.strip() or "it cleared a high global-impact bar"


def _slot_budget_line(slot_n: int) -> str:
    # M5 gate finding 2: budget lines are tier-aware (A2) — the old lines
    # instructed movements the validator hard-rejects on quick tier.
    if slot_n == 1:
        return ("FULL tier — lede 2-4 sentences; why_it_matters 4-7 sentences; "
                "watch_for 1-2 sentences; ~350-450 words total")
    if slot_n == 2:
        return ("MEDIUM tier — lede 2-3 sentences; why_it_matters 3-5 sentences; "
                "watch_for 1-2 sentences; ~100-300 words total")
    if slot_n == 3:
        return ("MEDIUM tier (or QUICK if the story doesn't warrant depth) — "
                "medium: lede 2-3 sentences, why_it_matters 3-5 sentences, "
                "watch_for 1-2 sentences, ~100-300 words; quick: lede ONLY, "
                "1-3 sentences, ~40-80 words, NO why_it_matters or watch_for")
    return ("QUICK tier — lede ONLY, 1-3 sentences, ~40-80 words; "
            "NO why_it_matters or watch_for fields")


def build_narrative_prompt(date: str, variant: str, inputs: Dict) -> str:
    prompt_file = PROMPT_A if variant == "A" else PROMPT_B
    template = (paths.PROMPTS_DIR / prompt_file).read_text(encoding="utf-8")

    cfg = config.load_sources()
    tag_lines = [f"- {t} (broad)" for t in cfg.interests_broad]
    tag_lines += [f"- {t} (specific)" for t in cfg.interests_granular]

    thread_lines = []
    for t in inputs["threads"]:
        note = f"  [emphasis note, steer silently: {t['principal_note']}]" if t["principal_note"] else ""
        thread_lines.append(f"- {t['topic']}{note}")

    if inputs["continuity_status"] == "ok":
        prior_block = inputs["prior_ctx"]["text_block"] + (
            "\n(Callback rules apply: delta-only, max 2 optional callbacks.)"
        )
    elif inputs["continuity_status"] == "corrupt":
        prior_block = (
            "(A prior briefing exists but its record is unreadable — "
            "continuity is suspended for this run. Do not reference prior "
            "coverage.)"
        )
    else:
        prior_block = "(This is the first briefing — no prior coverage to reference.)"

    story_parts = []
    for s in inputs["slots"]:
        n = s["slot"]
        lines = [f"STORY {n} — budget: {_slot_budget_line(n)}"]
        lines.append(f"working title (rewrite it): {s.get('story_title', '')}")
        lines.append(f"what happened (one line): {s.get('summary', '')}")
        if s.get("world_impact_reason"):
            lines.append(
                f"ranking's significance seed (rephrase, never paste): {s['world_impact_reason']}"
            )
        tags = ", ".join(t["name"] for t in s.get("matched_tags", [])) or "(none)"
        threads_m = ", ".join(s.get("matched_memory", [])) or "(none)"
        lines.append(f"matched tags: {tags} | matched threads: {threads_m}")
        lines.append(f"corroboration: {s.get('corroboration_label', '')}")
        if s.get("corroboration_count") == 1:
            outlets = s.get("outlets") or []
            lines.append(
                f"SINGLE-OUTLET STORY — name the outlet in the lede prose: "
                f"{outlets[0] if outlets else 'the sole outlet'}"
            )
        if s.get("override"):
            lines.append(
                "OVERRIDE STORY — outside the reader's tags (the pipeline "
                "renders its own label; your lede may acknowledge naturally)"
            )
        for rv in s.get("revived_threads", []):
            if rv.get("last_covered"):
                lines.append(
                    f"REVIVAL (mandatory disclosure): thread {rv['topic']!r} — "
                    f"the lede's first two sentences MUST contain 'last covered "
                    f"{rv['last_covered']}' (date exactly as written here), a "
                    "one-clause prior summary, and what's new"
                )
        lines.append("source items (your REPORT lane for this story):")
        for it in inputs["items_by_slot"].get(n, []):
            excerpt = (it["raw_excerpt"] or "").strip()[:700]
            lines.append(f"  * [{it['outlet']}] {it['title']}")
            if excerpt:
                lines.append(f"    excerpt: {excerpt}")
        story_parts.append("\n".join(lines))

    weekday, human = _spoken_date(date)
    return template.format(
        date_line=f"{weekday}, {human}",
        tags_block="\n".join(tag_lines),
        threads_block="\n".join(thread_lines) or "(none)",
        prior_block=prior_block,
        stories_block="\n\n".join(story_parts),
    )


# ---------------------------------------------------------------------------
# Narrative validation + assembly (code owns the furniture)
# ---------------------------------------------------------------------------

def _outlet_token(outlet: str) -> str:
    """First significant token of an outlet display name, lowercased —
    "BBC News — World" -> "bbc"; "The Hill" -> "hill" (gate ride: a leading
    article is never the name a writer uses)."""
    for tok in re.split(r"[\s—-]+", outlet):
        if tok.lower() in ("the", "a", "an"):
            continue
        if len(tok) > 2 or tok.isupper():
            return tok.lower()
    return outlet.lower()


def _scan_banned(text: str) -> List[str]:
    low = text.lower()
    return [b for b in BANNED_STRINGS if b in low]


def validate_narrative_payload(
    payload: object, slots: List[Dict], variant: str
) -> Tuple[List[Dict], List[str]]:
    """Structural checks BLOCK (retry-then-fail); style checks warn.
    Mandatory disclosures (revival dates) block."""
    if not isinstance(payload, dict) or not isinstance(payload.get("stories"), list):
        raise ValueError("payload must be a JSON object with a `stories` list")
    stories = payload["stories"]
    if len(stories) != len(slots):
        raise ValueError(
            f"{len(stories)} stories returned for {len(slots)} slots — must match"
        )
    warnings: List[str] = []
    clean: List[Dict] = []
    for i, (s, slot) in enumerate(zip(stories, slots)):
        n = slot["slot"]
        if not isinstance(s, dict):
            raise ValueError(f"story {n}: not an object")
        tier = s.get("tier")
        # A2 sanity: the model proposes only story 3's tier; code enforces
        # the rest (lead is always full; 2 medium; 4+ quick).
        allowed = (
            ("full",) if i == 0 else
            ("medium",) if i == 1 else
            ("medium", "quick") if i == 2 else
            ("quick",)
        )
        if tier not in allowed:
            raise ValueError(
                f"story {n}: tier {tier!r} not allowed at this position "
                f"(expected one of {allowed})"
            )
        out = {"tier": tier}
        required = ("headline", "lede", "why_it_matters", "watch_for") \
            if tier in ("full", "medium") else ("headline", "lede")
        for fld in required:
            v = s.get(fld)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"story {n}: {fld} missing/empty (tier {tier})")
            out[fld] = v.strip()
        if tier in ("full", "medium"):
            # A7: declared framings, menu-membership enforced.
            wl = s.get("why_label")
            if wl not in WHY_FRAMINGS:
                raise ValueError(
                    f"story {n}: why_label {wl!r} not in the sanctioned menu"
                )
            xl = s.get("watch_label")
            if xl not in WATCH_FRAMINGS:
                raise ValueError(
                    f"story {n}: watch_label {xl!r} not in the sanctioned menu"
                )
            out["why_label"], out["watch_label"] = wl, xl
        if tier == "quick":
            for fld in ("why_it_matters", "watch_for"):
                if isinstance(s.get(fld), str) and s[fld].strip():
                    raise ValueError(
                        f"story {n}: quick hits carry no {fld} (A2)"
                    )
                out[fld] = None
        my_read = s.get("my_read")
        if variant == "A":
            if isinstance(my_read, str) and my_read.strip():
                raise ValueError(f"story {n}: variant A must not carry my_read")
            out["my_read"] = None
        else:
            if isinstance(my_read, str) and my_read.strip():
                # Code owns the label (§5.7): strip a model-written "My read:"
                # prefix so assembly never doubles it (M5 live finding).
                out["my_read"] = re.sub(
                    r"^\s*my read:\s*", "", my_read.strip(), flags=re.I
                ) or None
            else:
                out["my_read"] = None
        if len(_WORD_RE.findall(out["headline"])) > 14:
            warnings.append(f"story {n}: headline over the 12-word band")
        # Mandatory revival disclosure: date verbatim in the lede's opening.
        for rv in slot.get("revived_threads", []):
            date_needed = rv.get("last_covered")
            if date_needed:
                first_two = " ".join(re.split(r"(?<=[.!?])\s+", out["lede"])[:2])
                if date_needed not in first_two:
                    raise ValueError(
                        f"story {n}: revival date {date_needed!r} missing from "
                        "the lede's first two sentences (mandatory disclosure)"
                    )
        # Single-source: outlet named in lede prose (writer-owned warning).
        # Token-level match: display names like "BBC News — World" are
        # legitimately spoken as "the BBC" (M5 live finding).
        if slot.get("corroboration_count") == 1 and slot.get("outlets"):
            if _outlet_token(slot["outlets"][0]) not in out["lede"].lower():
                warnings.append(
                    f"story {n}: single-outlet story should name "
                    f"{slot['outlets'][0]!r} in the lede prose"
                )
        text_blob = " ".join(v for v in out.values() if isinstance(v, str))
        hits = _scan_banned(text_blob)
        if hits:
            warnings.append(f"story {n}: banned strings present: {hits}")
        low = text_blob.lower()
        truisms = [x for x in TRUISM_WARN_STRINGS if x in low]
        if truisms:
            warnings.append(f"story {n}: truism-class phrases (A3): {truisms}")
        moralize = [x for x in MORALIZE_WARN_STRINGS if x in low]
        if moralize:
            warnings.append(
                f"story {n}: moralization-class words in own voice? (A3, "
                f"quotes are fine): {moralize}"
            )
        clean.append(out)
    # A7 rhythm warn: five stories must never share one framing.
    why_labels = [c.get("why_label") for c in clean if c.get("why_label")]
    if len(why_labels) >= 3 and len(set(why_labels)) == 1:
        warnings.append(
            f"all {len(why_labels)} movement stories share one framing "
            f"({why_labels[0]!r}) — A7 wants varied rhythm [warn-only]"
        )
    # A8 lead-depth pressure: a lead near slot-2 length is a flag.
    if clean and clean[0].get("tier") == "full":
        lead_words = len(_WORD_RE.findall(
            " ".join(v for v in clean[0].values() if isinstance(v, str))))
        if lead_words <= 240:
            warnings.append(
                f"lead landed at {lead_words} words — near slot-2 length; A8 "
                "wants the lead's why-movement built from source specifics"
            )
    return clean, warnings


def assemble_narrative(
    date: str, variant: str, stories: List[Dict], inputs: Dict
) -> str:
    weekday, human = _spoken_date(date)
    slots = inputs["slots"]
    parts = [f"# NewsLens — {weekday}, {human}", "", "In today's briefing:"]
    parts += [f"- {st['headline']}" for st in stories]
    parts.append("")

    for st, slot in zip(stories, slots):
        parts.append("---")
        if slot.get("override"):
            parts.append(OVERRIDE_TEXT_LABEL.format(reason=_override_reason(slot)))
            parts.append("")
        parts.append(f"**{st['headline']}**")
        parts.append("")
        parts.append(st["lede"])
        parts.append("")
        if st.get("tier") in ("full", "medium"):
            why_label = st.get("why_label") or "Why it matters"
            watch_label = st.get("watch_label") or "Watch for"
            parts.append(f"**{why_label}:** {st['why_it_matters']}")
            if st.get("my_read"):
                parts.append("")
                parts.append(f"**My read:** {st['my_read']}")
            parts.append("")
            parts.append(f"**{watch_label}:** {st['watch_for']}")
            parts.append("")
        # quick hits (A2): headline + the 1-3 sentence hit + trust furniture
        # only — no movement structure.
        matches = ", ".join(
            [t["name"] for t in slot.get("matched_tags", [])]
            + slot.get("matched_memory", [])
        )
        # Latent bug found by the cold-start sample: no-match is not the same
        # as override — never point at a label that isn't there.
        if matches:
            here_for = matches
        elif slot.get("override"):
            here_for = "editor's override — see note above"
        else:
            here_for = "world-impact selection (no tag or thread match)"
        meta_line = slot.get("corroboration_label", "")
        outlets = slot.get("outlets") or []
        outlet_names = f" — {', '.join(outlets)}" if outlets else ""
        parts.append(f"*{meta_line}{outlet_names}. Here for: {here_for}.*")
        parts.append("")

    # Footer block — fixed order, deterministic (§5.7).
    parts.append("---")
    wm = inputs.get("window_meta") or {}
    window = (wm.get("window") or {}) if isinstance(wm, dict) else {}
    start = (window.get("start_iso") or "window-start unavailable")[:16]
    end = (wm.get("ran_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"))[:16]
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append("*" + WINDOW_LINE.format(timestamp=now_ts, start=start, end=end) + "*")
    parts.append("")
    parts.append("*" + ranking.CORROBORATION_CAVEAT + "*")
    # A1: the variant stamp retired with the alternation window (samples are
    # labeled by their file headers; no methodology self-reference in output).
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Script pass
# ---------------------------------------------------------------------------

def _script_budgets(n_slots: int, narrative_words: int) -> Tuple[int, str]:
    """Script target scales with the NARRATIVE, not slot count alone: the
    contract's bands (900-1300 written -> 1600-2000 spoken) imply ~1.5x, and
    its own rule — "fewer or thinner stories = shorter episode; never pad" —
    means a thin 3-story day must not demand a full-fat script (M5 live
    finding: a 667-word narrative can't honestly fill 1,440 spoken words).
    Segment guidance scales proportionally for the prompt."""
    slot_budget = SCRIPT_OPEN_WORDS + SCRIPT_OUTRO_WORDS + sum(
        SCRIPT_SEGMENTS[i] for i in range(1, n_slots + 1)
    )
    total = min(slot_budget, max(400, int(narrative_words * 1.5)))
    scale = total / slot_budget if slot_budget else 1.0
    desc = " · ".join(
        f"slot {i}: ~{int(SCRIPT_SEGMENTS[i] * scale)}"
        for i in range(1, n_slots + 1)
    )
    return total, desc


def build_labels_block(inputs: Dict) -> str:
    lines = []
    for s in inputs["slots"]:
        n = s["slot"]
        if s.get("override"):
            lines.append(f"story {n}: OVERRIDE — reason: {_override_reason(s)}")
        if s.get("corroboration_count") == 1 and s.get("outlets"):
            lines.append(f"story {n}: SINGLE-SOURCE — outlet: {s['outlets'][0]}")
        for rv in s.get("revived_threads", []):
            if rv.get("last_covered"):
                lines.append(
                    f"story {n}: REVIVAL — say the date: last covered {rv['last_covered']}"
                )
        lines.append(
            f"story {n}: corroboration for the ear: {s.get('corroboration_label', '')}"
        )
    lines.append("corrections flagged upstream: none this run")
    return "\n".join(lines)


def build_script_prompt(date: str, variant: str, narrative: str, inputs: Dict) -> str:
    template = (paths.PROMPTS_DIR / PROMPT_SCRIPT).read_text(encoding="utf-8")
    n_slots = len(inputs["slots"])
    total, per_desc = _script_budgets(n_slots, wc(narrative))
    weekday, human = _spoken_date(date)
    epistemic = (
        '; epistemic first person ("I think") is banned in this voice'
        if variant == "A"
        else '; epistemic first person is allowed only when voicing the '
        'briefing\'s labeled "My read" judgments'
    )
    return template.format(
        date_line=f"{weekday}, {human}",
        time_of_day=_time_of_day(),
        word_target=f"{int(total * 0.9)}-{int(total * 1.1)}",
        minutes_target=f"{max(4, round(total / 160))}",
        budget_open=SCRIPT_OPEN_WORDS,
        budget_stories=per_desc,
        budget_outro=SCRIPT_OUTRO_WORDS,
        weekday=weekday,
        spoken_date=human,
        spoken_caveat=SPOKEN_CAVEAT,
        epistemic_rule=epistemic,
        labels_block=build_labels_block(inputs),
        narrative_text=narrative,
    )


def _date_spoken_forms(iso_date: str) -> List[str]:
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return [iso_date]
    month = _MONTHS[d.month - 1]
    day = d.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return [iso_date, f"{month} {day}", f"{month} {day}{suffix}"]


def validate_script(
    text: str, narrative: str, inputs: Dict
) -> Tuple[str, List[str], List[str]]:
    """Returns (possibly-repaired text, hard_problems, warnings).
    Hard problems: missing mandatory spoken disclosures (override elements,
    revival dates) — retry material. Frozen furniture (spoken caveat,
    sign-off) is deterministically appended if absent (verbatim strings, not
    facts) with a disclosure warning. Fact-subset + hedge checks warn (§5.9
    #7-8: flag for review, never auto-fix)."""
    hard: List[str] = []
    warnings: List[str] = []
    body = text.strip()
    low = body.lower()

    for s in inputs["slots"]:
        n = s["slot"]
        if s.get("override"):
            reason = _override_reason(s)
            reason_head = " ".join(reason.split()[:4]).rstrip(".,").lower()
            if "outside your" not in low:
                hard.append(f"story {n}: spoken override missing the outside-your-tags acknowledgment")
            if reason_head and reason_head not in low:
                hard.append(f"story {n}: spoken override missing its reason")
        for rv in s.get("revived_threads", []):
            date_needed = rv.get("last_covered")
            if date_needed and not any(f.lower() in low for f in _date_spoken_forms(date_needed)):
                # A5: spoken presentation is licensed; the TEXT disclosure
                # stays hard (validate_narrative_payload). Warn-grade here.
                warnings.append(
                    f"story {n}: spoken revival date {date_needed!r} not voiced"
                )
        # A5: per-story spoken attribution (incl. single-source phrasing) is
        # editorial judgment now — no presence check. Accuracy checks stay.

    if SPOKEN_CAVEAT.lower() not in low:
        body = body.rstrip()
        if SIGNOFF.lower() in low:
            body = re.sub(re.escape(SIGNOFF), "", body, flags=re.I).rstrip()
        body += "\n\n" + SPOKEN_CAVEAT + "\n\n" + SIGNOFF
        warnings.append("spoken caveat was missing — appended verbatim (frozen furniture)")
    elif SIGNOFF.lower() not in low:
        body = body.rstrip() + "\n\n" + SIGNOFF
        warnings.append("sign-off was missing — appended verbatim")

    if "see you tomorrow" in low:
        hard.append("schedule promise ('see you tomorrow') — banned, v1 is on-demand")

    # Fact-subset proxy (§5.9 #7): script numerals must exist in the narrative
    # (comma-insensitive; sanctioned ear-rounding words exempt the check only
    # for the rounded phrase, not for new precise figures).
    narrative_nums = {x.replace(",", "").rstrip(".") for x in _NUM_RE.findall(narrative)}
    script_nums = {x.replace(",", "").rstrip(".") for x in _NUM_RE.findall(body)}
    loose = sorted(x for x in script_nums - narrative_nums if x not in {"2", "3"})
    if loose:
        warnings.append(f"script numerals absent from narrative (review): {loose[:8]}")
    # Hedge preservation (§5.9 #8, coarse): "will" in script needs "will" in narrative.
    if re.search(r"\bwill\b", body, re.I) and not re.search(r"\bwill\b", narrative, re.I):
        warnings.append("script uses 'will' where the narrative never does — hedge check")

    hits = _scan_banned(body)
    if hits:
        warnings.append(f"script banned strings: {hits}")
    mech = [x for x in MECHANICAL_TRANSITIONS if x in low]
    if mech:
        warnings.append(f"mechanical transition defaults (A4): {mech}")
    # A4 intro formula: the dateline should not be the opening breath.
    dateline_pos = low.find("it's ")
    if 0 <= dateline_pos < 60:
        warnings.append(
            "intro formula (A4): dateline arrives before any what/why/"
            "uncertainty framing"
        )
    return body, hard, warnings


# ---------------------------------------------------------------------------
# Persistence + instrumentation + artifact
# ---------------------------------------------------------------------------

def persist_generation(
    con: sqlite3.Connection, date: str, narrative: str, script: str,
    steps: List[Dict], audio_path: Optional[str] = None
) -> None:
    """Write narrative/script onto the briefing row. If a narrative already
    exists (re-generation), archive the row to briefings_history first —
    same rule persist() applies on re-rank."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with con:
        row = con.execute("SELECT * FROM briefings WHERE date = ?", (date,)).fetchone()
        if row is None:
            raise GenerateError(f"briefing row for {date} vanished mid-run")
        if row["narrative_text"]:
            con.execute(
                "INSERT INTO briefings_history (briefing_id, date, story_slots,"
                " corroboration_labels, narrative_text, script_text,"
                " audio_file_path, token_cost, generated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["id"], row["date"], row["story_slots"],
                 row["corroboration_labels"], row["narrative_text"],
                 row["script_text"], row["audio_file_path"],
                 row["token_cost"], row["generated_at"]),
            )
        try:
            token_cost = json.loads(row["token_cost"] or "{}")
        except ValueError:
            token_cost = {}
        existing_steps = token_cost.get("steps") or []
        all_steps = existing_steps + steps
        total = round(sum(s.get("usd") or 0 for s in all_steps), 6)
        con.execute(
            "UPDATE briefings SET narrative_text = ?, script_text = ?,"
            " audio_file_path = ?, token_cost = ?, generated_at = ?"
            " WHERE id = ?",
            (narrative, script, audio_path,
             json.dumps({"steps": all_steps, "total_usd": total}), now, row["id"]),
        )


def log_generation(entry: Dict) -> None:
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_path = paths.DATA_DIR / GENERATION_LOG_NAME
    entry = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **entry}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def write_artifact(date: str, variant: str, sample: bool, narrative: str,
                   script: str, no_threads: bool = False) -> Path:
    out_dir = paths.DATA_DIR / BRIEFINGS_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    if not sample:
        name = f"{date}.md"
    elif no_threads:
        name = f"{date}-no-threads-SAMPLE.md"
    else:
        name = f"{date}-variant-{variant}-SAMPLE.md"
    path = out_dir / name
    if no_threads:
        header = (
            "<!-- SAMPLE — no active threads (cold-start view); not the "
            "briefing of record -->\n\n"
        )
    elif sample:
        header = (
            f"<!-- SAMPLE — variant {variant} for comparison; NOT the briefing "
            "of record for this date -->\n\n"
        )
    else:
        header = ""
    path.write_text(
        header + narrative
        + "\n\n---\n\n## Podcast script (feeds M6 audio; not part of the read briefing)\n\n"
        + script + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_generate(
    date: Optional[str] = None,
    con: Optional[sqlite3.Connection] = None,
    env: Optional[dict] = None,
    variant_override: Optional[str] = None,
    refresh: bool = True,
    no_threads: bool = False,
) -> GenReport:
    import os

    src_env = env if env is not None else os.environ
    date = date or ranking.local_today()
    key = (src_env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise GenerateError(
            "OPENAI_API_KEY not set — get one at platform.openai.com/api-keys, "
            "then add to .env (generation is an LLM step; there is no keyless mode)"
        )

    scheduled = ACTIVE_VOICE  # A1: alternation ended; A is the voice of record
    variant = (variant_override or scheduled).upper()
    if variant not in ("A", "B"):
        raise GenerateError(f"variant must be A or B, got {variant!r}")
    sample = (variant != scheduled) or no_threads
    if sample and refresh:
        # M5 gate finding 1: a sample must NEVER mutate the briefing of
        # record — the refresh chain's rank persist archives and NULLs the
        # record narrative before the sample renders. Samples always consume
        # the existing row; a plain `generate` is how the record refreshes.
        refresh = False
    report = GenReport(date=date, variant=variant, sample=sample)
    if sample:
        report.warnings.append(
            "sample request: refresh chain skipped — the briefing of record "
            "is untouched (run a plain `generate` to refresh the record)"
        )
    report.no_threads = no_threads
    if no_threads:
        report.warnings.append(
            "no-threads SAMPLE (cold-start view): thread/memory context "
            "emptied, tags kept — rendered to a file, briefings row untouched"
        )
    if variant != scheduled:
        report.warnings.append(
            f"voice {variant} is retired (editorial review A1; {scheduled} is "
            "the voice of record) — SAMPLE mode: rendered to a file, the "
            "briefing of record untouched"
        )

    own_con = con is None
    if own_con:
        db.migrate()
        con = db.connect()
    try:
        try:
            return _run_generate_body(
                con, date, src_env, key, report, refresh, no_threads
            )
        except GenerateError as exc:
            log_generation({"date": date, "variant": variant, "sample": sample,
                            "status": "failed", "error": str(exc)[:500],
                            "warnings": report.warnings})
            raise
    finally:
        if own_con:
            con.close()


def _run_generate_body(
    con: sqlite3.Connection, date: str, src_env, key: str,
    report: GenReport, refresh: bool, no_threads: bool = False
) -> GenReport:
    from . import ingest

    if refresh:
        try:
            ing = ingest.run_ingest(con=con, env=src_env)
        except config.SourcesParseError as exc:
            raise GenerateError(str(exc)) from exc
        report.ingest_summary = (
            f"{len(ing.succeeded)}/{ing.attempted} sources; "
            f"{ing.items_new} new items; discovery: {ing.discovery_status}"
        )
        if ing.degradation_message:
            report.warnings.append(ing.degradation_message)
        try:
            rank_rep = ranking.run_rank(date=date, con=con, env=src_env)
        except ranking.RankingError as exc:
            raise GenerateError(f"rank stage failed: {exc}") from exc
        report.warnings.extend(rank_rep.warnings)

    inputs = load_briefing_inputs(con, date)
    if no_threads:
        # Cold-start view (ADR-0007 amendment): tags stay; every thread/memory
        # trace is stripped from a COPY of the inputs — thread list, per-story
        # matched_memory, and revival data — so prompt, validators, assembly
        # meta-lines, and script labels are all consistently thread-free. The
        # persisted slots are untouched (samples never persist).
        inputs["threads"] = []
        inputs["slots"] = [
            {**s, "matched_memory": [], "revived_threads": []}
            for s in inputs["slots"]
        ]
    report.continuity_status = inputs["continuity_status"]
    if inputs["continuity_status"] == "corrupt":
        report.warnings.append(
            "continuity SUSPENDED this run: a prior briefing exists but its "
            "story record is unreadable — the writer was told not to reference "
            "prior coverage (M4 gate must-address: this is distinguished from "
            "'first briefing', never silent)"
        )

    cap = config.budget_cap_usd_per_run(src_env)
    spent = 0.0

    # --- Narrative pass ---
    n_prompt = build_narrative_prompt(date, report.variant, inputs)
    est = _est_cost(n_prompt, NARRATIVE_MAX_TOKENS)
    if spent + est > cap:
        raise GenerateError(
            f"estimated narrative cost ${est:.4f} exceeds the remaining budget "
            f"cap (${cap:.2f}) — aborting before the call"
        )
    draft_holder: List[Dict] = []

    def _shape_check(content: str) -> None:
        payload = json.loads(content)
        if not isinstance(payload, dict) or not isinstance(payload.get("stories"), list):
            raise ValueError("draft must be a JSON object with a `stories` list")
        if len(payload["stories"]) != len(inputs["slots"]):
            raise ValueError(
                f"{len(payload['stories'])} draft stories for "
                f"{len(inputs['slots'])} slots — must match"
            )
        draft_holder[:] = [payload]

    _, usage_n = call_llm(
        key, n_prompt, "narrative", NARRATIVE_MAX_TOKENS,
        NARRATIVE_TEMPERATURE, True, validate=_shape_check,
    )
    draft_payload = draft_holder[0]
    step_n = {"step": f"narrative_{report.variant}", "model": WRITER_MODEL,
              "prompt_tokens": usage_n.get("prompt_tokens"),
              "completion_tokens": usage_n.get("completion_tokens"),
              "usd": round(_step_cost(usage_n), 6)}
    report.steps.append(step_n)
    spent += step_n["usd"] or 0

    # --- Editor pass (M6 mandate 2): cut/tighten/concretize ONLY — the
    # editor may never add facts; the edited payload is what gets fully
    # validated, persisted, and adapted. Editor failure degrades to the
    # unedited draft WITH disclosure — never a dead run.
    edited_payload = draft_payload
    editor_note = "editor: skipped"
    try:
        e_template = (paths.PROMPTS_DIR / PROMPT_EDITOR).read_text(encoding="utf-8")
        e_prompt = e_template.format(
            labels_block=build_labels_block(inputs),
            draft_json=json.dumps(draft_payload, ensure_ascii=False),
        )
        est_e = _est_cost(e_prompt, EDITOR_MAX_TOKENS)
        if spent + est_e > cap:
            raise GenerateError(
                f"editor pass estimate ${est_e:.4f} would exceed the run cap"
            )
        edited_holder: List[Dict] = []

        def _editor_shape(content: str) -> None:
            payload = json.loads(content)
            if not isinstance(payload, dict) or not isinstance(payload.get("stories"), list):
                raise ValueError("editor must return the same JSON shape")
            if len(payload["stories"]) != len(draft_payload["stories"]):
                raise ValueError("editor changed the story count")
            for de, dr in zip(payload["stories"], draft_payload["stories"]):
                if not (isinstance(de, dict) and isinstance(dr, dict)):
                    continue
                if de.get("tier") != dr.get("tier"):
                    raise ValueError("editor changed a tier")
                for lbl in ("why_label", "watch_label"):
                    if dr.get(lbl) is not None and de.get(lbl) != dr.get(lbl):
                        raise ValueError(f"editor changed {lbl} (A7 labels are the writer's)")
            edited_holder[:] = [payload]

        _, usage_e = call_llm(
            key, e_prompt, "editor", EDITOR_MAX_TOKENS,
            EDITOR_TEMPERATURE, True, validate=_editor_shape,
        )
        edited_payload = edited_holder[0]
        step_e = {"step": "editor_pass", "model": WRITER_MODEL,
                  "prompt_tokens": usage_e.get("prompt_tokens"),
                  "completion_tokens": usage_e.get("completion_tokens"),
                  "usd": round(_step_cost(usage_e), 6)}
        report.steps.append(step_e)
        spent += step_e["usd"] or 0
        before = sum(wc(" ".join(v for v in s.values() if isinstance(v, str)))
                     for s in draft_payload["stories"] if isinstance(s, dict))
        after = sum(wc(" ".join(v for v in s.values() if isinstance(v, str)))
                    for s in edited_payload["stories"] if isinstance(s, dict))
        pct = round((before - after) / before * 100) if before else 0
        editor_note = f"editor: {before} -> {after} words ({pct}% tighter)"
        report.warnings.append(editor_note)
        # Carryover 18a: mechanical tripwire for epistemic-qualifier deletion.
        hedge_re = re.compile(
            r"\b(could|may|might|likely|expect(?:s|ed)?|appears?|suggests?|"
            r"unclear|reportedly|unconfirmed)\b", re.I)
        draft_text = " ".join(
            v for s in draft_payload["stories"] if isinstance(s, dict)
            for v in s.values() if isinstance(v, str))
        edited_text = " ".join(
            v for s in edited_payload["stories"] if isinstance(s, dict)
            for v in s.values() if isinstance(v, str))
        h_before, h_after = len(hedge_re.findall(draft_text)), len(hedge_re.findall(edited_text))
        if h_before >= 3 and h_after < h_before * 0.5:
            report.warnings.append(
                f"editor hedge-ratio: {h_before} -> {h_after} hedge words — "
                "check that epistemic qualifiers weren't stripped from kept "
                "claims (carryover 18a tripwire)"
            )
    except (GenerateError, OSError) as exc:
        editor_note = f"editor: DEGRADED to unedited draft ({exc})"
        report.warnings.append(editor_note)

    # ALL narrative validators run on the EDITED text (mandate 2) — INSIDE
    # the degrade seam (BUG-8): a validator-violating edit (live repro: the
    # editor clipped a mandatory revival date) degrades to the re-validated
    # draft with disclosure; a draft that ALSO fails is a logged, visible
    # GenerateError — never a raw crash.
    try:
        stories, narrative_warnings = validate_narrative_payload(
            edited_payload, inputs["slots"], report.variant
        )
    except ValueError as exc:
        if edited_payload is not draft_payload:
            report.warnings.append(
                f"editor: output FAILED validation ({exc}) — degraded to the "
                "writer's draft (disclosed; the edit was discarded)"
            )
            editor_note += " [DISCARDED: failed validation]"
            try:
                stories, narrative_warnings = validate_narrative_payload(
                    draft_payload, inputs["slots"], report.variant
                )
            except ValueError as exc2:
                raise GenerateError(
                    f"narrative draft failed validation after editor degrade: {exc2}"
                ) from exc2
        else:
            raise GenerateError(f"narrative failed validation: {exc}") from exc
    report.warnings.extend(narrative_warnings)

    narrative = assemble_narrative(date, report.variant, stories, inputs)
    report.narrative_text = narrative
    report.narrative_words = wc(narrative)
    report.per_story_words = [
        wc(" ".join(v for v in st.values() if isinstance(v, str))) for st in stories
    ]
    TIER_BANDS = {"full": (250, 550), "medium": (100, 300), "quick": (15, 110)}
    for st, words in zip(stories, report.per_story_words):
        lo_t, hi_t = TIER_BANDS[st["tier"]]
        if not lo_t <= words <= hi_t:
            report.warnings.append(
                f"{st['tier']} story {words} words — outside the "
                f"{lo_t}-{hi_t} tier guidance (A2) [KNOB; warn-only]"
            )
    lo, hi = NARRATIVE_BAND
    scale = len(inputs["slots"]) / 5.0
    if not (lo * scale * 0.7 <= report.narrative_words <= hi * 1.15):
        report.warnings.append(
            f"narrative {report.narrative_words} words — outside the "
            f"~{int(lo * scale)}-{int(hi * max(scale, 0.4))} guidance band for "
            f"{len(inputs['slots'])} slot(s) [KNOB; warn-only]"
        )

    # --- Script pass ---
    s_prompt = build_script_prompt(date, report.variant, narrative, inputs)
    est_s = _est_cost(s_prompt, SCRIPT_MAX_TOKENS)
    if spent + est_s > cap:
        raise GenerateError(
            f"estimated script cost ${est_s:.4f} would exceed the run budget "
            f"cap (${cap:.2f}, ${spent:.4f} already spent) — narrative was NOT "
            "persisted; raise the cap or re-run"
        )
    script_holder: List[str] = []
    script_warnings: List[str] = []

    total_target, _ = _script_budgets(len(inputs["slots"]), report.narrative_words)

    def _validate_script(content: str) -> None:
        body, hard, warns = validate_script(content, narrative, inputs)
        if hard:
            raise ValueError("; ".join(hard))
        if wc(body) < total_target * 0.55:
            # Severe under-delivery is not a band nuance: a 3-minute "10-13
            # minute" episode under-serves the listener (implementer call,
            # ADR-0007; ordinary band misses still warn-only per §5.9 #9).
            raise ValueError(
                f"script severely short: {wc(body)} words vs ~{total_target} target"
            )
        script_holder[:] = [body]
        script_warnings[:] = warns

    _, usage_s = call_llm(
        key, s_prompt, "script", SCRIPT_MAX_TOKENS,
        SCRIPT_TEMPERATURE, False, validate=_validate_script,
    )
    script = script_holder[0]
    report.warnings.extend(script_warnings)
    step_s = {"step": "script_adapt", "model": WRITER_MODEL,
              "prompt_tokens": usage_s.get("prompt_tokens"),
              "completion_tokens": usage_s.get("completion_tokens"),
              "usd": round(_step_cost(usage_s), 6)}
    report.steps.append(step_s)
    report.script_text = script
    report.script_words = wc(script)
    if not (total_target * 0.7 <= report.script_words <= total_target * 1.25):
        report.warnings.append(
            f"script {report.script_words} words vs ~{total_target} target "
            f"for {len(inputs['slots'])} slot(s) [KNOB; warn-only]"
        )

    # --- Audio step (M6 mandate 1): the last stage; a synth failure
    # degrades to a no-audio run WITH disclosure, never a dead run.
    from . import audio as audio_mod

    cfg_full = config.load_sources()
    audio_path_str = None
    out_dir = paths.DATA_DIR / BRIEFINGS_DIR_NAME
    if report.sample:
        stem = (f"{date}-no-threads-SAMPLE" if report.no_threads
                else f"{date}-variant-{report.variant}-SAMPLE")
    else:
        stem = date
    try:
        result = audio_mod.generate_audio(
            script, out_dir / f"{stem}.wav",
            engine=cfg_full.tts_engine, openai_key=key,
            budget_cap=max(0.0, cap - spent),
        )
        audio_path_str = result.path
        report.steps.append({
            "step": f"tts_{result.engine}", "model": result.engine,
            "duration_s": result.duration_s, "gen_time_s": result.gen_time_s,
            "usd": result.est_cost_usd,
        })
        report.warnings.append(
            f"audio: {result.engine} — {result.duration_s / 60:.1f} min in "
            f"{result.gen_time_s:.0f}s"
            + (f" (${result.est_cost_usd:.4f})" if result.est_cost_usd else " ($0)")
        )
    except audio_mod.AudioError as exc:
        report.warnings.append(
            f"audio: SKIPPED — {exc} (the text briefing is unaffected)"
        )

    # --- Persist (never for samples), artifact, instrumentation ---
    if not report.sample:
        persist_generation(con, date, narrative, script, report.steps,
                           audio_path=audio_path_str)
    report.artifact_path = str(
        write_artifact(date, report.variant, report.sample, narrative, script,
                       no_threads=no_threads)
    )
    log_generation({
        "date": date, "variant": report.variant, "sample": report.sample,
        "no_threads": no_threads,
        "status": "ok",
        "tiers": [s.get("tier") for s in stories],
        "framings": [s.get("why_label") for s in stories],
        "editor": editor_note,
        "draft_stories": draft_payload.get("stories"),  # carryover 18b: forensics
        "stories": stories,  # M7: the UI's structured render source (ADR-0010)
        "audio": audio_path_str,
        "warnings": report.warnings,
        "narrative_words": report.narrative_words,
        "per_story_words": report.per_story_words,
        "script_words": report.script_words,
        "per_story_tiers": [st.get("tier") for st in stories],
        "override_rendered": any(s.get("override") for s in inputs["slots"]),
        "revival_rendered": any(s.get("revived_threads") for s in inputs["slots"]),
        "continuity": report.continuity_status,
        "steps": report.steps,
        "total_usd": round(sum(s.get("usd") or 0 for s in report.steps), 6),
    })
    return report
