# Carryover notes (living file — current target: milestone 3)

## Resolved in milestone 2 (2026-07-03)

- ~~Finding 4: `briefings.date` GLOB format check~~ — done as
  `migrations/0002_briefings_date_format.sql` (triggers, not a table-rebuild
  CHECK — rationale in ADR-0003 §1).
- ~~Finding 5: "fetch-day = UTC day" stated explicitly~~ — done: binding
  contract block atop `src/newslens/ingest.py`, README ingestion section,
  ADR-0003 §5.
- ~~Finding 6: unused `Optional` import in doctor.py~~ — removed with the M2
  doctor changes.

## Still open

1. **[QA-owned, from M1 review finding 3] Pin the remaining unreadable-file
   paths:** unreadable `.env` (`doctor.load_effective_env`, dotenv AND
   fallback branches) and unreadable `prompts/doctor_sonar_ping.txt`
   (`doctor.check_perplexity_key`). Implementer must not write these —
   `tests/` is QA's.

## New carryovers for milestone 3

2. **Wire-syndication flags on republishers are judgment calls, revisit with
   real data.** Yahoo Finance / Investing.com / Whatfinger are wire-flagged in
   `sources.yaml` on documented-republisher grounds; after a week of real
   ingested items, check whether the flag over- or under-excludes for
   corroboration counting (per-source notes mark this).
3. **Does ranking need the Sonar answer text persisted?** M2 stores only
   `search_results` rows (title/url/date) and surfaces the answer text in the
   run report; if M3 ranking wants it as context, decide where it lives
   (NOT as a source_items excerpt — ADR-0003 §6).
4. **Sonar reliability spike still pending** — gated on `PERPLEXITY_API_KEY`;
   one command when granted: `scripts/sonar_spike`. A failed spike is a
   principal checkpoint (GNews fallback, cost change), never absorbed.
5. **Cross-feed same-URL attribution is last-writer-wins** (QA-pinned:
   `test_ingest.py::test_cross_feed_same_url_same_day_is_last_writer_wins`).
   A wire-flagged republisher fetched later overwrites the original outlet's
   attribution + wire flag for that day's snapshot. M3 corroboration counting
   needs a deliberate ruling here — do not inherit by accident. (QA obs. 1,
   2026-07-04.)
6. **Well-formed HTML at an rss_url is a permanent silent 0-item success** —
   doctor catches it, the ingest report never will. M3 candidate: flag sources
   that parse but never yield entries. (QA obs. 2.)
7. **Feed body size is unbounded** — items are capped at 20/feed, bytes are
   not; one `read(cap)` away from bounded. Low risk with curated feeds. (QA
   obs. 3.)
