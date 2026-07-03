# ADR 0003 — Milestone-2 ingestion decisions

**Date:** 2026-07-03 · **Status:** accepted · **Milestone:** 2

## Context

M2 turns the principal's ~45-outlet list into working tier-1 ingestion plus a
cold tier-2 discovery seam (key not yet granted), under the standing
constraints: no scraping, zero network when keyless, graceful degradation as
a tested deliverable. Every feed URL was live-verified 2026-07-03 with the
pipeline's own User-Agent before being seeded.

## Decisions

1. **`briefings.date` format enforced by TRIGGERS, not a retrofitted CHECK**
   (migration 0002). SQLite cannot `ALTER TABLE ... ADD CHECK`; a rebuild
   (copy/drop/rename) of a table referenced by two FK'd tables is the risky
   path. BEFORE INSERT/UPDATE triggers enforce the identical GLOB rule with
   zero rebuild, matching 0001's append-only-trigger precedent. Format only;
   calendar validity stays pipeline-code responsibility.
2. **Source tier model in sources.yaml** (`full` / `headline_only` /
   `cautious` / `reference_only` + per-source `enabled`, `note`).
   `reference_only` is structural — `Source.fetchable` is false regardless of
   flags, so NYT/Wikipedia/AP/Reuters can never be fetched by a config slip.
   `cautious` is DEFAULT-DISABLED: omitting `enabled` on a cautious source
   means off, and enabling one is an explicit, warned act.
3. **`wire_syndication` extended beyond wires to documented wire-republishers**
   (Yahoo Finance, Investing.com, Whatfinger) so M3's corroboration counting
   doesn't double-count wire copy. Judgment-tagged now, revisit with real
   feed data at M3 (noted per-source in the YAML).
4. **feedparser (new dependency) for RSS/Atom/RDF parsing; urllib for HTTP.**
   Feed XML dialect handling is a mature solved problem — hand-rolling it is
   the wrong wheel to reinvent. HTTP stays urllib (no requests dep): we
   control timeout/UA, and doctor stays consistent. **A custom 308 redirect
   handler is required:** Python 3.9's urllib only follows 308 from 3.11+,
   and real outlets in the list 308 (found in the M2 sweep).
5. **Idempotency: SELECT-then-write upsert on (url, UTC fetch-day),** not
   `ON CONFLICT`: the dedupe key lives in an expression index and the app is
   single-writer, so the boring readable form wins. `fetched_at` is preserved
   on update so a row never migrates across its fetch-day. **Fetch-day = UTC
   day, stated in the ingestion contract** (reviewer finding 5): dedupe
   boundary is midnight UTC; `briefings.date` stays principal-local. One
   transaction per source: a mid-feed failure leaves no half-writes.
6. **Discovery (Sonar) stores `search_results` rows only** (title/url/date,
   `source_type='sonar'`, excerpt NULL) — the generated answer text is not a
   source and is never stored as one (faithfulness by construction). Whether
   ranking wants the answer text persisted is an open M3 question, carried in
   NOTES-M3. Keyless/interest-less runs build no request (zero-network rule
   applies to the pipeline, not just the doctor). One call per run, one retry
   only on timeout/5xx (never 4xx), budget-guarded against
   `config.budget_cap_usd_per_run` before the request is built.
7. **Volume guardrails:** `MAX_ITEMS_PER_FEED = 20` (code constant, not env
   var) bounds ~29 enabled feeds to ≲580 rows/run worst case. The ranking
   context budget implications belong to M3 and are flagged in the milestone
   report for the principal's checkpoint.
8. **On-demand-only scope change** (DECISIONS.md 2026-07-03):
   `GENERATE_HOUR_LOCAL` is dormant — unset/valid render informationally in
   the doctor; a set-but-garbage value still fails because a typo'd .env line
   is a config error regardless of dormancy. QA's M1 pins on the FAIL path
   remain intentionally green.

## Alternatives rejected

- Table rebuild for a real CHECK constraint (FK juggling risk >> benefit).
- `requests`/`httpx` for ingestion (second HTTP stack for no new capability).
- Trusting sources.yaml `enabled: true` on cautious aggregators by default
  (violates the default-disabled instruction).
- Storing the Sonar answer text as a source_items excerpt (fabricated
  "source" content — faithfulness violation).
- Refusing the ~45-outlet volume back to the spec's 8–12 (coordinator said
  implement with flags and surface concerns at checkpoint instead).
