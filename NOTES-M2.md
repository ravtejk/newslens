# Carryover for milestone 2 (from the milestone-1 code review, 2026-07-02)

Milestone 1 was approved with notes. Findings 1–2 were fixed in the follow-up
commit; the four below ride to milestone 2. Whoever picks up M2 (implementer,
QA, reviewer) reads this first, then deletes each item as it lands.

1. **[QA-owned] Pin the remaining unreadable-file paths.** The fix-loop-1
   change guarded three unguarded reads, but only unreadable `sources.yaml`
   has a pinned test (`tests/test_doctor_offline.py::test_BUG2_*`). Add pins
   for the other two friendly-degrade paths: unreadable `.env`
   (`doctor.load_effective_env`, dotenv AND fallback branches) and unreadable
   `prompts/doctor_sonar_ping.txt` (`doctor.check_perplexity_key`).
   Implementer must not write these — `tests/` is QA's.

2. **[Schema — needs the full QA loop] Consider a format CHECK on
   `briefings.date`.** The column is documented as `YYYY-MM-DD` but nothing
   structural rejects e.g. `2026-7-2` or a full timestamp. A
   `CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')` in a new
   migration would make the format constraint real. Schema change ⇒ migration
   ⇒ escalation trigger ⇒ QA loop — do it as part of M2's schema work, not as
   a drive-by.

3. **[M2 ingestion contract] State "fetch-day = UTC day" explicitly.** The
   `source_items` dedupe key is `UNIQUE (url, date(fetched_at))` and
   `fetched_at` is UTC ISO-8601, so the dedupe day boundary is midnight UTC —
   not the principal's local midnight (`briefings.date`, by contrast, is
   principal-local). M2's ingestion code and its docs/tests must say this out
   loud so a late-evening local run double-fetching across the UTC boundary is
   understood behavior, not a surprise.

4. **[Cleanup, fold into any M2 touch of doctor.py] Unused `Optional` import**
   in `src/newslens/doctor.py`'s `typing` import line. Not worth its own
   commit/loop; remove the next time that file is edited for real work.
