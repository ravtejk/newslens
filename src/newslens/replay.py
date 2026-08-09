"""replay.py — NL-17 M2: DETERMINISTIC RANK REPLAY + THE DEFAULT-DENY AUDITS.

WHAT THIS IS FOR. NL-17's central claim is causal: "following this actor is why
this story was considered." A claim like that is worth exactly as much as the
instrument that could falsify it. This module is that instrument. It rebuilds a
past run's rank input from the persisted envelope, proves the rebuild by hash,
re-runs the deterministic selection with steering flipped, and attributes any
difference in what got selected to steering and to nothing else — n=1, but
n=1-CONCLUSIVE, because everything except the flipped bit is byte-identical.

IT SPENDS NOTHING, AND THAT IS STRUCTURAL. There is no model call anywhere in
this file, no import of `llm`, no network. A replay re-runs the DETERMINISTIC
half of ranking (`select_slots` and below) over clusters the model already
produced and we already persisted. So the "cap-gated model path" the charter
requires of any replay lane is satisfied by there being no such path to gate —
which is stated here rather than left to be inferred, because "$0 on live paths"
is a claim the gate checks.

READ-ONLY, AND NO CLI DOOR — deliberately. Every function takes an explicit
connection and issues SELECTs only. There is no `__main__`, no script, and no
subcommand, because a one-command replay is a one-command way to point this at
the founder database, and ENGINEERING.md's probe rules exist because that class
of accident has already happened twice. QA and the gate call these functions
with a sandbox connection.

-----------------------------------------------------------------------------
DEFAULT-DENY IS THE WHOLE POSTURE (data M3, "vacuous pass via missing receipts
-> missing = FAIL; the audit's own blindness is a violation"). Every check here
returns FAIL when the evidence it needs is absent, never PASS-by-silence. A run
with no replay envelope is a FAILED audit, not an un-audited one.

THE AUDIT RECOMPUTES INDEPENDENTLY. `_pool` below is a SECOND implementation of
the scoring pool, written from the law rather than imported from
`ranking.personal_score`. That duplication is intentional and is the only
duplication in this batch: an audit that calls the function under test proves
that the function agrees with itself. The pin that keeps the two honest is
`test_audit_catches_a_planted_stack` — mutate the scorer, the audit goes red.
(Named exactly, fix loop 1 / QA F-2: the earlier text cited a
`..._planted_stacking_bug` that does not exist. That pin's catch is C2, the
recompute check; C3's own first firings were produced by QA's constructions —
the summed-law shape and the suppressed-tag XOR half — not by any pin here.)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

from . import config, ranking, steering


class ReplayError(Exception):
    """A replay that cannot be proved faithful. Never a soft warning."""


# ---------------------------------------------------------------------------
# Reading a run back
# ---------------------------------------------------------------------------

def runs_for(con: sqlite3.Connection, date: str) -> List[sqlite3.Row]:
    """Every ranking_runs row for a date, oldest first. Read-only."""
    return con.execute(
        "SELECT id, date, ran_at, meta FROM ranking_runs WHERE date = ?"
        " ORDER BY ran_at, id", (date,)).fetchall()


def envelope(run_row) -> Dict:
    """The run's replay envelope, or raise.

    Raising rather than returning {} is the default-deny posture at its
    entrance: a caller that gets an empty dict tends to carry on and report a
    pass over nothing.
    """
    try:
        meta = json.loads(run_row["meta"])
    except (TypeError, ValueError) as exc:
        raise ReplayError(f"run meta is not valid JSON ({exc})") from exc
    env = meta.get("replay")
    if not isinstance(env, dict) or not env.get("prompt_sha256"):
        raise ReplayError(
            "run carries no replay envelope (pre-NL-17-M2 run, or a run that "
            "failed before the envelope was built) — NOT replayable, and that "
            "is a FAIL, not an absence")
    return env


class _CfgShim:
    """The two `SourcesConfig` fields `build_prompt` reads, restored from the
    envelope. A shim rather than a re-read of sources.yaml ON PURPOSE: the live
    file is the principal's and he edits it, so rebuilding from it would replay
    TODAY's interests into YESTERDAY's prompt and call the result faithful."""

    def __init__(self, env: Dict):
        self.interests_broad = list((env.get("tags") or {}).get("broad") or [])
        self.interests_granular = list((env.get("tags") or {}).get("granular") or [])


# ---------------------------------------------------------------------------
# The rebuild, and its proof
# ---------------------------------------------------------------------------

def rebuild_prompt(con: sqlite3.Connection, env: Dict) -> Tuple[str, Dict]:
    """Rebuild the exact prompt string this run sent. Returns (prompt, proof).

    `proof` carries the three hash comparisons, and they are separate on
    purpose: a bare "did not match" sends an investigator to read 40,000
    characters, while `items_sha256` mismatching alone says "a headline was
    revised under us" (see ranking._replay_envelope on ingest's in-place title
    UPDATE) and `template_sha256` alone says "prompts/rank.txt changed".

    The items are re-rendered through `ranking.render_items_block` — the SAME
    renderer the run used, not a copy — so a passing hash attests the run's
    bytes rather than this module's idea of them.
    """
    ids = list(env.get("item_ids") or [])
    rows_by_id = {}
    for chunk in _chunks(ids, 400):
        q = ",".join("?" * len(chunk))
        for r in con.execute(
                f"SELECT id, outlet, title FROM source_items WHERE id IN ({q})",
                chunk):
            rows_by_id[r["id"]] = r
    missing = [i for i in ids if i not in rows_by_id]
    rows = [rows_by_id[i] for i in ids if i in rows_by_id]
    threads = env.get("threads") or {}
    prompt = ranking.build_prompt(
        env.get("date_local", ""), rows, _CfgShim(env),
        list(threads.get("active") or []), env.get("window_desc", ""),
        list(threads.get("dormant") or []))
    items_sha = ranking._sha256(ranking.render_items_block(rows))
    template_sha = ""
    try:
        template_sha = ranking._sha256(
            (ranking.paths.PROMPTS_DIR / env.get("prompt_file",
                                                 ranking.PROMPT_FILE)
             ).read_text(encoding="utf-8"))
    except OSError:
        pass
    proof = {
        "prompt_sha256": ranking._sha256(prompt),
        "prompt_sha_match": ranking._sha256(prompt) == env["prompt_sha256"],
        "items_sha_match": items_sha == env.get("items_sha256"),
        "template_sha_match": template_sha == env.get("template_sha256"),
        "missing_item_ids": missing,
        "faithful": (ranking._sha256(prompt) == env["prompt_sha256"]
                     and not missing),
    }
    return prompt, proof


def _chunks(seq: Sequence, n: int):
    for i in range(0, len(seq), n):
        yield list(seq[i:i + n])


# ---------------------------------------------------------------------------
# The flip
# ---------------------------------------------------------------------------

def flip_replay(con: sqlite3.Connection, env: Dict,
                state_armed: Optional[steering.SteeringState] = None,
                *, memory_steers: bool = False) -> Dict:
    """Re-run the deterministic selection twice — steering dark, steering armed
    — over the SAME persisted clusters, and attribute the difference.

    Everything that is not the flipped bit is held identical BY CONSTRUCTION:
    the clusters are the ones the model returned, the item rows are the ones it
    saw, the followed-outlet set is the run's own. So a story that appears in one
    selection and not the other did so because of steering. That is the whole
    causal argument, and it is why the envelope had to exist first.

    `state_armed` lets a caller supply the armed state to test against (the
    sandbox path, and how a planted delta is proved detectable). Omitted, the
    run's own watched entities are re-derived with `armed=True`.

    THE REPLAY IS CONSTRAINED TO WHAT THE LIVE PIPELINE COULD REACH (fix loop 1,
    QA F-7). `select_slots` is called here without a `con`, so NL-57's
    quiet-thread classification cannot run — and a quiet-ZERO cluster leaves
    Today UNCONDITIONALLY and BEFORE selection (ranking.py:1777-1790). Degrading
    that on both slates is symmetric in MECHANISM but not VERDICT-PRESERVING:
    the cluster competes in both replay slates, so an entity weight lands on a
    story the run itself had already removed, and the difference is counted as a
    steering flip. Measured: live delta [], replay flips 1, attributed TRUE. Since
    metric-M1's T3 bound (10-run mean <= 1.0, any run <= 2) is fed by that count,
    the consequence is a false review trigger on any quiet-thread edition — in
    the real measurement era too. So the run's own recorded verdict
    (`quiet_zero`, persisted per cluster in the envelope) removes those clusters
    from BOTH slates here. An envelope written without that receipt cannot be
    corrected after the fact, and says so: `quiet_receipt: "absent"` plus a
    caveat naming the run NON-ATTRIBUTABLE, rather than a number that looks like
    every other number.
    """
    all_clusters = [dict(c) for c in (env.get("clusters") or [])]
    if not all_clusters:
        raise ReplayError("envelope carries no clusters — nothing to replay")
    receipted = any(c.get("quiet_zero") is not None for c in all_clusters)
    quiet_excluded = [c.get("story_title") for c in all_clusters
                      if c.get("quiet_zero")]
    clusters = [c for c in all_clusters if not c.get("quiet_zero")]
    ids = sorted({i for c in clusters for i in (c.get("item_ids") or [])})
    items_by_id = {}
    for chunk in _chunks(ids, 400):
        q = ",".join("?" * len(chunk))
        for r in con.execute(
                "SELECT id, source_type, outlet, url, title, published_at,"
                " fetched_at, wire_syndication_flag FROM source_items"
                f" WHERE id IN ({q})", chunk):
            items_by_id[r["id"]] = r
    followed = set(env.get("followed_outlets") or [])
    if state_armed is None:
        watched = steering.watched_entities(con)
        state_armed = steering.derive(steering.live_moves(con), watched,
                                      armed=True)
    dark_slots, _dm = ranking.select_slots(
        [dict(c) for c in clusters], items_by_id, followed,
        memory_steers=memory_steers, state=steering.INERT)
    armed_slots, _am = ranking.select_slots(
        [dict(c) for c in clusters], items_by_id, followed,
        memory_steers=memory_steers, state=state_armed)
    dark_titles = [s.story_title for s in dark_slots]
    armed_titles = [s.story_title for s in armed_slots]
    gained = [t for t in armed_titles if t not in dark_titles]
    lost = [t for t in dark_titles if t not in armed_titles]
    return {
        "dark": dark_titles,
        "armed": armed_titles,
        "gained": gained,
        "lost": lost,
        "flips": len(gained),
        "attributed_to_steering": bool(gained or lost),
        # The live pipeline's own pre-selection removals, held identical on both
        # slates so they can never become a flip (see the docstring).
        "quiet_zero_excluded": quiet_excluded,
        "quiet_receipt": "envelope" if receipted else "absent",
        "caveat": "" if receipted else (
            "envelope predates the per-cluster quiet_zero receipt, so NL-57's "
            "pre-selection removals cannot be reproduced: `flips` here is NOT "
            "ATTRIBUTABLE and this run must be excluded from the metric-M1 "
            "count rather than read as a measurement"),
        # data metric M1's bound is 10-run mean <= 1.0 flips and any run <= 2.
        # Reported, never enforced by a clamp here (charter item 5): a clamp
        # would hide the breach the trigger table exists to catch.
        "bound_note": "M1 bound: 10-run mean <= 1.0 flips/run; any run <= 2",
    }


# ---------------------------------------------------------------------------
# The audits — XOR, no-stacking, storyline-zero, receipt completeness
# ---------------------------------------------------------------------------

def _pool(cluster: Dict, followed: bool, memory_steers: bool,
          suppressed: frozenset, armed: bool,
          weight_bearing: Optional[set] = None) -> List[float]:
    """The scoring pool, recomputed FROM THE LAW rather than from the code under
    test (see the module header). Returns the individual contributions.

    THIS IS THE CAP-INDEPENDENT INSTRUMENT, and it is why it exists at all.
    `personal_score` returns `min(base, 1.0)`, and that cap can launder a sum:
    a domain tag (0.5) plus an entity (1.0) reads 1.0 whether the law is max()
    or sum(), so no assertion on the RETURNED SCORE can distinguish them. Only
    the pool can — count the contributions, never the total (Rook's tooth,
    adopted into product criterion (d))."""
    out: List[float] = []
    for t in cluster.get("matched_tags") or []:
        if (t.get("name") or "").casefold() in suppressed:
            continue
        out.append(ranking.TOPIC_WEIGHT if t.get("level") == "topic"
                   else ranking.DOMAIN_WEIGHT)
    if (cluster.get("matched_memory") or []) and memory_steers:
        out.append(ranking.MEMORY_WEIGHT)
    if armed and any(e in (weight_bearing or set())
                     for e in (cluster.get("matched_entities") or [])):
        out.append(steering.ENTITY_WEIGHT)
    return out


def audit(con: sqlite3.Connection, run_row) -> Dict:
    """The stacking / XOR / completeness audit for ONE run. DEFAULT-DENY.

    Returns {"status": "PASS"|"FAIL", "checks": {...}, "violations": [...]}.
    Every check names the run and the cluster it failed on, because an audit
    result that says only "FAIL" costs a full re-derivation to act on.

    THE FOUR CHECKS:
      C1 receipt completeness — envelope present, every cluster carrying the
         scores the run used. Missing evidence is a violation in itself.
      C2 recompute equality — independently recomputed personal/combined equal
         the recorded values exactly (combined at the 4-dp the code rounds to).
      C3 no-stacking / count-once — the pool contains AT MOST ONE entity
         contribution however many entities matched, and no concept contributes
         through both a tag and its entity. Contribution-level and therefore
         CAP-INDEPENDENT: `min(base, 1.0)` can launder a sum, so the check
         reads the pool, never the capped score (Rook's tooth, product
         criterion (d)).
      C4 storyline-zero / XOR of identity — every entity that contributed weight
         is weight-bearing (entity altitude). One identity per instant: an
         entity contributes, or a storyline thread does, never both for one
         concept.
    """
    violations: List[Dict] = []
    checks = {"receipts": False, "recompute": False,
              "no_stacking": False, "storyline_zero": False}
    try:
        env = envelope(run_row)
    except ReplayError as exc:
        return {"status": "FAIL", "checks": checks,
                "violations": [{"check": "C1", "reason": str(exc)}]}
    meta = json.loads(run_row["meta"])
    ents = meta.get("entities") or {}
    armed = bool(ents.get("armed"))
    memory_steers = bool(meta.get("threads_steer_selection"))
    suppressed = frozenset(ents.get("suppressed_tags") or [])
    weight_bearing = {r["entity_id"] for r in (ents.get("receipts") or [])
                      if r.get("steers")}

    clusters = env.get("clusters") or []
    if not clusters:
        violations.append({"check": "C1", "reason": "envelope carries no clusters"})
    for c in clusters:
        if c.get("personal_score") is None or c.get("combined_score") is None:
            violations.append({"check": "C1", "cluster": c.get("story_title"),
                               "reason": "cluster carries no recorded scores"})
    # RECEIPT COMPLETENESS INCLUDES THE ENTITY BLOCK (fix loop 1, QA F-5).
    # `select_slots` writes meta["entities"] on EVERY post-M2 run, dark included
    # — so on a run that carries an envelope, its absence is a receipt GAP, and
    # data-M3's words are "missing = FAIL, incl. any receipt gap". It used to
    # audit PASS: with the block gone, `armed` read False, `weight_bearing` read
    # empty, C3/C4 had nothing to disagree with, and a run whose receipts were
    # LOST was indistinguishable from a clean dark run. That is the exact
    # vacuous-pass-via-missing-receipts shape this module exists to refuse.
    if not isinstance(meta.get("entities"), dict):
        violations.append({
            "check": "C1",
            "reason": "run carries a replay envelope but no meta.entities "
                      "receipts block — every post-M2 run writes one, so this "
                      "is a receipt gap, not a dark run"})
    elif not isinstance(ents.get("receipts"), list):
        violations.append({
            "check": "C1",
            "reason": "meta.entities carries no `receipts` list — the per-entity "
                      "record the audit's weight-bearing set is derived from"})
    checks["receipts"] = not any(v["check"] == "C1" for v in violations)

    followed_outlets = set(env.get("followed_outlets") or [])
    ids = sorted({i for c in clusters for i in (c.get("item_ids") or [])})
    outlet_by_id: Dict[int, str] = {}
    for chunk in _chunks(ids, 400):
        q = ",".join("?" * len(chunk))
        for r in con.execute(
                f"SELECT id, outlet FROM source_items WHERE id IN ({q})", chunk):
            outlet_by_id[r["id"]] = r["outlet"]

    for c in clusters:
        title = c.get("story_title")
        followed = any(outlet_by_id.get(i) in followed_outlets
                       for i in (c.get("item_ids") or []))
        pool = _pool(c, followed, memory_steers, suppressed, armed,
                     weight_bearing)
        base = max(pool) if pool else 0.0
        expect_p = min(base + (ranking.FOLLOWED_BOOST if followed else 0.0), 1.0)
        expect_c = ranking.combined_score(expect_p, c.get("world_impact") or 0)
        if round(expect_p, 3) != round(c.get("personal_score") or 0.0, 3):
            violations.append({
                "check": "C2", "cluster": title,
                "reason": f"personal {c.get('personal_score')} != recomputed "
                          f"{round(expect_p, 3)}"})
        if expect_c != c.get("combined_score"):
            violations.append({
                "check": "C2", "cluster": title,
                "reason": f"combined {c.get('combined_score')} != recomputed "
                          f"{expect_c}"})
        matched = c.get("matched_entities") or []
        recorded = round(c.get("personal_score") or 0.0, 3)
        boost = ranking.FOLLOWED_BOOST if followed else 0.0
        if armed and matched:
            # C3 — COUNT-ONCE, checked against the RECORDED number rather than
            # against this module's own pool (which appends the entity weight at
            # most once by construction and would therefore be checking itself).
            # The discriminating question: does the recorded score match what a
            # SUMMING law would have produced, and not what the max law does?
            summed = min(sum(pool) + boost, 1.0)
            if recorded != round(expect_p, 3) and recorded == round(summed, 3):
                violations.append({
                    "check": "C3", "cluster": title,
                    "reason": f"personal {recorded} matches a SUMMED pool "
                              f"{pool} — max()/count-once violated"})
            # The XOR half, and this one reads pure recorded data: a concept
            # that a live move suppressed must not still be sitting in the
            # contributing tag list.
            live_tags = [(t.get("name") or "")
                         for t in c.get("matched_tags") or []
                         if (t.get("name") or "").casefold() in suppressed]
            if live_tags and steering.ENTITY_WEIGHT in pool:
                violations.append({
                    "check": "C3", "cluster": title,
                    "reason": f"suppressed tag(s) {live_tags} present alongside "
                              "an entity contribution — one concept, two "
                              "vocabularies"})
            # C4 — STORYLINE-ZERO. Every entity this cluster matched sits at
            # storyline altitude, so no entity weight may have been applied.
            # Detectable only when the two laws disagree numerically; when they
            # agree the distinction has no consequence and is not claimed.
            if not any(e in weight_bearing for e in matched):
                leaked = min(max(pool + [steering.ENTITY_WEIGHT]) + boost, 1.0)
                if recorded != round(expect_p, 3) and recorded == round(leaked, 3):
                    violations.append({
                        "check": "C4", "cluster": title,
                        "reason": f"entities {matched} carry storyline altitude "
                                  "only, yet the recorded score reflects entity "
                                  "weight — criterion (c) violated"})
    checks["recompute"] = not any(v["check"] == "C2" for v in violations)
    checks["no_stacking"] = not any(v["check"] == "C3" for v in violations)
    checks["storyline_zero"] = not any(v["check"] == "C4" for v in violations)
    return {
        "status": "PASS" if not violations else "FAIL",
        "run_id": run_row["id"], "date": run_row["date"], "armed": armed,
        "checks": checks, "violations": violations,
    }


def xor_scan(con: sqlite3.Connection,
             cfg: Optional[config.SourcesConfig]) -> Dict:
    """"No concept in both vocabularies" — the catalog-level XOR (data M3).

    Run at migration, on every catalog change, and weekly. Reads the LIVE moves
    and asks whether any moved concept still carries a tag line in the config.
    A hit is NOT automatically a violation during the sequenced activation
    window — pre-move coexistence is today's legal state of record — so the
    result names the state rather than judging it, and the caller (the gate)
    applies the window.

    THE CATALOG IS REQUIRED, and `cfg=None` is a REFUSAL (fix loop 1, QA F-4).
    It used to be an optional argument that silently produced an empty tag set,
    so a scan with no catalog returned `clean: true` with live moves on the
    books — a default-deny instrument passing by silence, which is the one thing
    this module's posture forbids. The refusal is unconditional rather than
    "refuse only when moves exist": a check whose strictness depends on how much
    there happened to be to find is the same failure wearing a different shape.
    """
    if cfg is None:
        raise ReplayError(
            "xor_scan needs the tag catalog to scan against — an absent catalog "
            "is UNDECIDABLE, not clean (default-deny: missing = FAIL)")
    moves = steering.live_moves(con)
    tags = {t.casefold() for t in
            list(cfg.interests_broad) + list(cfg.interests_granular)}
    both = sorted({m.concept for m in moves if m.concept.casefold() in tags})
    return {
        "live_moves": len(moves),
        "in_both_vocabularies": both,
        "clean": not both,
        "note": ("a concept in both vocabularies is lawful ONLY inside the "
                 "approved activation window, and only because rank suppresses "
                 "the tag from the ledger row — outside it, this is a breach"),
    }
