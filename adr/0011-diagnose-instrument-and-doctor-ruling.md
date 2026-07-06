# ADR-0011: the diagnose instrument, the construction cutover, and the doctor's Perplexity ruling

**Status:** accepted (milestone 8 — final construction milestone)
**Date:** 2026-07-05

## Context

Construction ends here; the day-14/day-30 verdicts happen against real
usage. Three decisions shaped the instruments those verdicts will use.

## Decision 1 — `newslens diagnose`: caveats travel with the number

The readout command (read-only, offline, $0) prints the day-30 falsifier
and the generation record. The three M7-gate caveats (NOTES-M2 21a-c)
print **inline with the falsifier count on every run** — not in docs, not
behind a flag. Rationale: the number's consumers (the org at day 14, the
principal at day 30) will meet it detached from this repo's lore; a count
of "open days" that silently includes org traffic, or silently excludes
terminal reads, would be a lie of presentation. An instrument that must
be remembered-to-be-caveated will eventually be read uncaveated.

## Decision 2 — the construction cutover generalizes caveat 21c

NOTES 21c recorded a specific instance ("the 2 synthetic reads on
2026-07-05"), but by the time M8 landed, construction traffic on that
date had grown (implementer demo, CoS gate verification, QA probes) and
would grow again with every pre-handoff check. Hard-coding "2" would rot
immediately. `diagnose` instead carries `CONSTRUCTION_END_UTC =
2026-07-06`: every consumption event on or before that day is flagged
construction-period and excluded from the usage-window readout (shown,
labeled, never deleted — the rows stay raw per ADR-0010 §3). The recorded
2026-07-05 instance is still named in the caveat text.

Rejected: deleting the synthetic rows (data is raw; interpretation
belongs to the reader); a `synthetic` column (a schema change to encode
one boundary date that never moves again).

## Decision 3 — doctor: a deferral is a decision, not a failure

`PERPLEXITY_API_KEY` absent was a required-✗, making doctor exit 1 the
permanent state of the *correctly configured* install — the principal
deferred that key by explicit choice (DECISIONS.md), and ingest degrades
loudly to RSS-only. A health check that fails the product's intended
state trains its user to ignore exit codes. Ruling: absence is now
informational (○) with the deferred-by-choice note and the how-to-add
path; a **set-but-invalid** key still fails, because a typo is an error
while a deferral is a decision. Exit 0 is now reachable on the real
install (remaining honest exception: a dead upstream feed fails until
fixed or disabled — believing you read a source you don't is a real
problem).

QA note: two hint pins updated to the new wording
(`tests/test_doctor_offline.py`, `tests/test_preinstall_doctor.py`).

## Consequences

- The day-14 diagnostic is one command, and its numbers cannot appear
  without their interpretation limits.
- If construction-style verification ever happens again (post-usage-window
  probes), the cutover constant does NOT cover it — such probes would
  need their own disclosure. Accepted: the org doesn't drive the UI
  after handoff.
- Doctor exit 0 regains meaning: "the product as the principal configured
  it is fully runnable."
