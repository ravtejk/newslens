# ADR 0006 — Memory lifecycle v2 (principal amendment, 2026-07-04)

**Date:** 2026-07-04 · **Status:** accepted · **Milestone:** 4 (pre-QA
amendment) · **Supersedes:** the v1 lifecycle portions of ADR-0005.

## The contract, as implemented

1. **Three states:** `active` | `dormant` | `dismissed_user`. Under
   auto-revival, "stale" and "auto-dismissed" behave identically, so they
   merged. Migration 0006 is a TABLE REBUILD (0001's CHECK physically rejects
   the new values; nothing FK-references memory, so rebuild is safe) with
   stale->dormant, dismissed->dismissed_user mapping, a widened CHECK, and a
   new `status_changed_at` column so file annotations date the actual
   transition (updated_at moves on note edits; transition dates must not).
   Re-apply-safe via pass-through CASE.
2. **active -> dormant:** unreferenced 14d (same clock as v1, same
   max(created_at, referenced-briefing-time) basis; note edits don't reset
   it). Dormant threads are OUT of the prompt's thread list and have zero
   ranking influence.
3. **dormant -> active (auto-revival) — the hard constraint.** Mechanism
   chosen: the contract's match-only, zero-scoring option. Dormant topics
   enter the prompt as a separate RECOGNITION-ONLY vocabulary
   (`matched_dormant`, validated against the provided list like every other
   vocabulary). Zero influence holds by construction at three layers:
   (a) `personal_score` never reads matched_dormant, so a dormant match can't
   create a personal signal, can't lift combined score, and can't remove a
   cluster from the override pool; (b) selection runs before revival —
   `persist()` is the only place revival happens, and only slots that already
   WON on merits reach it; (c) `memory.revive_matched` filters
   status='dormant', so nothing else can flip. The revived slot carries
   `revived_threads` [{topic, last_covered}] captured BEFORE the update, so
   M5's narrative can say "last covered <date>"; run output prints the dated
   revival line; memory.md is re-rendered immediately after the run so the
   transition is visible now, not next run.
4. **dismissed_user never auto-revives.** It is absent from the match
   vocabulary by construction (`dormant_topics` selects status='dormant'
   only) — not just filtered at apply time. Stays VISIBLE in memory.md
   ("dismissed by you <date>"); revival is explicit (`memory add` / move the
   line to Active).
5. **memory.md: Active / Inactive.** Inactive renders complete (no pruning at
   personal scale), sorted by transition recency, each line annotated —
   "(dormant since <date>, last covered <date>)" vs "(dismissed by you
   <date>)". The PARSER reads annotations back: a rendered dormant line
   round-trips as dormant; a BARE line under Inactive (or a deleted line) is
   an explicit principal demotion -> dismissed_user. File-wins semantics,
   loud parse errors, and canonical rewrite all unchanged from ADR-0005.
6. **v1->v2 file migration:** the DB rebuild happens in 0006; the FILE simply
   regenerates from the DB (delete or first resync) — a v1-format file fails
   the v2 parser loudly with the regenerate hint, never silently.

## Also recorded

Migration 0005 principal-approved; the invented-ids repair extension is
DEFERRED by the principal — hard-reject stays, the temp-0/ascending-ids
mitigation stays, recurrences land in ranking_runs as logged failures.

## Gate-fix amendment — 2026-07-04 (M4 BLOCK items 1-2 + adopted optionals)

1. **Dormancy basis now includes `status_changed_at`** — max(created_at,
   referenced-briefing-time, last status transition). Without it, explicit
   revival was structurally dead: a dormant thread is >14d unreferenced by
   definition, and dormancy runs after file-apply in every sync, so a
   file-move revival flipped back in the SAME sync and a `memory add`
   revival died at the next run's sync-first. An explicit dated transition
   now resets the 14d clock. Note edits still don't (updated_at stays out of
   the basis). Reviewer-validated safety holds: active-only scan; seeds have
   status_changed_at == created_at; auto-revived rows re-reference anyway.
2. **Active-section parsing recognizes kept annotations as revival intent.**
   The header instructs keeping annotations when rearranging; the parser now
   strips "(dormant since …)" / "(dismissed by you …)" / "(last referenced:
   …)" under Active instead of misreading the move as a junk new thread plus
   a dismissal of the real one (the inverted-audit demo). Inactive lines
   symmetrically strip a kept "(last referenced: …)" so it can't leak into
   topic/note.
3. **Adopted (gate optionals):** (a) post-run memory.md refresh is
   mtime-guarded — if the file changed during the LLM call, the refresh is
   skipped with a visible warning instead of clobbering the hand edit;
   (b) items_block sanitizes brackets out of titles, closing the
   id-in-headline class outright (a headline can no longer fabricate an
   "[id=N]" token).
4. **Migration 0006 header corrected:** replay is safe but NOT identical —
   status_changed_at re-seeds from updated_at on replay (accepted residual,
   record-loss crash gap only).
