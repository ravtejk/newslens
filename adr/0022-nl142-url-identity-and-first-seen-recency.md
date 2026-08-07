# ADR-0022 — URL identity, first-seen recency, and why no migration runs

**Status:** accepted (NL-142, 2026-08-06) · supersedes the `(url, UTC fetch-day)`
half of ADR-0003 · charter: slate-land gate `research/2026-08-03--slateland-gate.md`
§R-C option (c), ruled by the principal 2026-08-06.

## What

`source_items` is keyed on **URL alone, across all days**. One URL is one row,
forever. `fetched_at` is that row's **first sighting** and never moves again.

## Why

Ingest used to dedupe on `(url, date(fetched_at))`. Consequence: every item still
sitting in a feed **re-inserted every day** with that day's stamp. A feed's daily
contribution to the candidate pool was therefore its **depth** (≤
`MAX_ITEMS_PER_FEED` = 20), not its publishing cadence. Measured on the real DB:
249 of 563 rows on 2026-08-02 were same-URL re-inserts of 2026-08-01; 3,592 of
12,918 rows in the whole table are duplicate copies of a URL already present.

Two defects followed, and they compounded:

1. **The recency law was silently false.** `ranking.py`'s comment said
   "developed = fetch time, first-seen". Under daily re-insertion, "fetched
   inside the window" actually meant **"still sitting in the feed"**. Nothing
   ever aged out while its feed kept carrying it.
2. **The pool cap bound permanently and evicted by file position.** One ingest
   run stamps every row with a single shared `fetched_at`, so
   `ORDER BY fetched_at DESC, id DESC LIMIT 550` degenerates to reverse
   *insertion* order — which is `sources.yaml` order. The top of the file died
   first, every day. Measured: 27 of 48 ranking runs at `item_count=550`; the
   2026-08-06 run evicted 24 items — **all 20 Bloomberg Markets plus 4
   Bloomberg Politics**, the first two outlets in the founder's file.

The two together produced the worst case on record: **`CNN`
(`cnn_topstories.rss`) has been frozen since 2023-04-25** and still fed 20
three-year-old stories into the pool every single day — 380 rows across 20
distinct URLs, zero genuinely-new items on every measured ingest day. Dead
content was evicting live content, positionally and invisibly.

## The load-bearing detail: recency must NOT refresh

A re-fetch updates mutable fields (title, excerpt, `published_at`, outlet, wire
flag — all of which upstream edits) and **never** `fetched_at`. Refreshing the
anchor on re-sighting would make every lingering item eternally fresh and
reproduce the exact defect being removed, with a URL key instead of a day key.
The `UPDATE` statement in `ingest.upsert_item` therefore does not name
`fetched_at`, and where pre-NL-142 duplicate rows exist the upsert binds to the
**earliest** (`ORDER BY id LIMIT 1`) — the true first sighting.

## What this changes in behavior

A slow-developing story that lingers in a feed under one unchanged URL for more
than `RECENCY_CAP_DAYS` (14) now **leaves** the candidate pool, where before it
stayed indefinitely. This is the recency rule as written, finally enforced. A
genuinely fresh development arrives as its own URL and re-enters normally.

The idempotency law (`ENGINEERING.md`: "scheduled jobs are idempotent and
resumable — safe to re-run for the same day without duplicates") is **preserved
and strengthened**: the guarantee widens from "safe within one UTC day" to "safe
on any day". The old promise is a strict subset of the new one.

## Blast radius, enumerated

| Surface | Effect |
|---|---|
| `ranking.py` recency window | Means what it always claimed; documented in-line |
| `ranking.py` `item_count` telemetry | Stops pinning at 550; becomes a real signal |
| `discovery.py` `_store_results` | Twin upsert, moved in the same diff — half a dedupe is no dedupe |
| `discovery.py` drop label | `already-known-today` → `already-known`; `_dropped_phrase`'s filter moved with it (it would otherwise have reported known rows as dropped) |
| `MAX_ITEMS_PER_FEED` | Now bounds *first sightings* per feed per run, not re-snapshots |
| `data_span_days` (`MIN(fetched_at)`) | Unaffected |
| `analysis` corpus `retrieved_at` | Unaffected |
| NL-107 edition-coherence guards | Unaffected (edition stamps, not item stamps) |
| `IngestReport` new/updated ratio | Inverts — cosmetic; report strings unchanged in shape |
| Briefing citations (`story_slots` → item ids) | Untouched: nothing is deleted or renumbered |

## Why NO schema migration ships with this

The obvious hardening is `UNIQUE(url)` on `source_items`. It is **deliberately
not built**, and the fix does not need it:

- **The code fix requires no schema change.** The surviving
  `UNIQUE(url, date(fetched_at))` index is a strictly *weaker* constraint than
  the URL key. A URL-keyed upsert can never violate it, because it never
  inserts a second row for a known URL at all. The invariant is enforced one
  layer up, where it is readable.
- **`UNIQUE(url)` cannot be added without destroying data.** 3,592 historical
  rows are duplicate copies; a unique index cannot be created over them. The
  migration would have to delete a quarter of the reader's item history.
- **Deletion is against the house pattern.** `discovery.py`'s own retro-clean
  comment states it: the 0018 pattern is *additive-only*, "rollback = stop
  reading the column; nothing to undo", and "deletion needs eyes on it first".
  Rows cited by shipped briefings must keep resolving.
- **The leftovers are inert.** They carry old `fetched_at` stamps, so the
  candidate window (which starts at the last briefing for a daily reader)
  excludes them. They age out on their own; nothing needs to sweep them.

If the DB-enforced invariant is later wanted, the shape is: insurance-backup
table → collapse duplicates to `MIN(id)` per URL, protecting
`discovery.referenced_source_item_ids()` → `UNIQUE(url)`. Priced, reversible,
and **not** taken on the org's own initiative, because it trades a real risk to
a reader's history for a guarantee the code path already provides.

## Alternatives rejected

- **Raise `MAX_INPUT_ITEMS`.** Treats the symptom. At ~1,053/day steady state a
  550→1,060 raise merely stops the cap binding at *today's* feed depths, and
  eviction stays position-dependent whenever it binds again.
- **Add a `first_seen_at` column.** A new column for a fact `fetched_at`
  already holds. The existing column's *documented* meaning was always
  first-seen; the honest fix is to make it true, not to add a second date and
  leave a misleading one in place.
- **Per-day key with a longer retention sweep.** Keeps the re-insert machine and
  adds a second machine to clean up after it.
