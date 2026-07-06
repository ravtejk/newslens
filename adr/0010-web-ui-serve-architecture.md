# ADR-0010: `newslens serve` — web UI architecture, consumption semantics, soft delete

**Status:** accepted (milestone 7)
**Date:** 2026-07-05

## Context

M7 puts a face on the pipeline: a local web UI rendering Today / Following /
Archive per the approved `design/mockup-v5.html` (+ DIRECTION-v3 + addendum +
four final principal tweaks), instrumenting real consumption for the day-30
falsifier, and adding the follow/unfollow/delete surface for memory threads.
Constraints: stdlib only, localhost only, $0 marginal cost, and the M3+
discipline that trust furniture is code-owned, never model prose.

## Decisions

### 1. Server shape: one rendered page, stdlib `ThreadingHTTPServer`, 127.0.0.1

`server.py` renders ONE page carrying all three views (client-side view
switching — exactly the mockup's structure) fresh from SQLite on every GET.
No cache, no session, no framework, no client build step; templates are
Python strings in `webui.py` (CSS/JS ported from the mockup). JSON POST
endpoints under `/api/*` mutate state. The server binds `127.0.0.1`
explicitly — this is a single-person surface; exposing it would put
generation spend and thread editing on the network.

Rejected: separate routes per view (breaks the mockup's instant-switch
feel for zero benefit at this data size); any dependency (violates the
milestone's no-new-deps expectation).

### 2. Structured stories ride the generation log

The UI needs per-story structure (headline / lede / labeled movements), but
briefings persist assembled markdown. From M7 the generation-log entry also
carries `stories` (the final validated list — alongside M6's `draft_stories`
forensics). Pre-M7 briefings fall back to parsing the narrative markdown,
which is safe because `assemble_narrative()` is code-owned and deterministic:
the parser mirrors a format we control, not model output. Furniture
(corroboration line, "Here for", tracked marker, override note) always
re-renders from slots, never from prose — on both paths.

Rejected: a schema migration copying structure into `briefings` (a second
source of truth for the same text); parsing as the primary path (fragile to
future assembler edits).

### 3. Consumption events: reads raw, listens deduped, metric dedups anyway

Migration `0007_consumption_events.sql` (escalation-flagged and approved in
the M7 dispatch): `(id, date, kind CHECK read|listen, occurred_at)`.

- `read`: logged server-side on every rendered-briefing page view. Raw
  truth; no insert-time dedup — dedup at *write* time would bake today's
  metric definition into the data and make tomorrow's questions
  unanswerable ("how many times did he come back?").
- `listen`: logged when the episode WAV is served from byte 0, at most once
  per (briefing-date, calendar-day) — an `<audio>` element issues bursts of
  Range requests per play; logging each would record the player's buffering
  strategy, not listening.
- The day-30 metric (`events.trailing_open_days`) counts DISTINCT calendar
  days with any event in the trailing 14 — flood-immune by construction, so
  the asymmetry above cannot skew it.

Generation stays in `generation_log.jsonl`; the join key is `date`. The
whole point of the falsifier is that producing an edition and coming to it
are different facts.

### 4. Soft delete = remove the tracking row, never the record

"You stopped following → Delete" removes the memory row (thread disappears
from memory.md, the UI, and all future steering). Past briefings are
immutable — their references to the story are baked narrative text, and no
briefing row points into `memory`, so nothing dangles. **No migration
needed**: this is a DELETE, not a state. `dismissed_user` remains the
never-auto-revives parking state; Delete is the stronger verb offered only
from that state, matching the mockup's copy ("Past editions that mentioned
it are unaffected").

Rejected: a fourth `deleted` status (keeps the row the principal asked to
be rid of, and every query forever filters it); cascading redaction of past
narratives (rewrites history — against the org's record discipline).

### 5. Thread verbs: one code path for CLI and UI

The verb bodies moved from `cli.py` into `memory.py` (`add_thread`,
`dismiss_thread`, `set_note`, `delete_thread`, `write_memory_file`); both
surfaces now run sync-file-first → verb → render-only file write. The web
follow seam additionally stamps `last_referenced_briefing_id` with the
edition the follow came from.

### 6. sources.yaml edits are line surgery, validated, reverting

Topics (broad/granular) and writers edit `sources.yaml` by inserting/
removing/flipping single lines — never parse-and-rewrite, because the file
carries principal comments a regeneration would destroy. Every edit
re-loads the file through `config.load_sources()` and restores the original
text if validation fails. Writer add is the paste-a-link path (entry with
`followed_analyst: true`); name-only resolution is rendered but marked
coming. **Unfollow a writer disables the whole entry** (`enabled: false` +
flag flipped) — the feed exists because it was followed; leaving it
fetchable after "unfollow" would be silent surveillance of a source the
principal said goodbye to.

## Consequences

- The UI is greppable, dependency-free, and dies with the terminal — the
  right weight for a personal tool.
- Every regeneration enriches the log; old briefings render slightly
  plainer (parsed) than new ones (structured). Acceptable and self-healing.
- `consumption_events` grows unbounded in principle; at one reader's scale
  this is decades of rows before it matters.
- In-server generation runs on a background thread with coarse status
  (running/done/error) — the loading panel's step list is illustrative, not
  live per-step progress. Honest gap, recorded in the M7 report.
