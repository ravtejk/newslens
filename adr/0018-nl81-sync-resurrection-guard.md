# ADR-0018 — NL-81: the sync resurrection guard

**Status:** accepted (gate-pending) · 2026-07-25
**Milestone:** NL-81, one build milestone ahead of Stage 0 (pre-registered HIGH
blocker). Design contract: `workspace/debates/2026-07-25--newslens--engineering-2.md` §5.
**Schema:** migration 0022 (additive) — principal checkpoint.

## Context

On 2026-07-17 a memory.md reconstruction was fed through `sync_memory()`. The
sync is file-wins with no memory of deletion and no recency signal of any kind,
so it propagated the poison faithfully: four threads the swept database didn't
have were re-INSERTed, the renamed thread the file couldn't see was dismissed as
"user-deleted", and that org-caused dismissal rendered as "(dismissed by you)".

Three distinct holes:

1. **No deletion memory.** `delete_thread` is a hard DELETE (ADR-0010); nothing
   survived to contradict a stale file. Rename (`move_follow_altitude`) changes
   the `lower(topic)` match key with no forwarding record, so the old name reads
   as an unknown topic and comes back as a brand-new thread.
2. **No recency signal.** Not even mtime was consulted — and mtime would not
   have helped: the poisoned reconstruction was *freshly written*. Newest mtime,
   stale content. Any wall-clock signal passes the exact file that caused the
   incident.
3. **Misattributed agency.** `render_file` printed one annotation for every
   `dismissed_user` row regardless of who caused it, so the org's own inference
   was rendered as the principal's decision — on principal-owned state.

This is the same future-state-poisoning family as NL-72, and it was the third
principal-state corruption event in two days. Twice was already a mechanism.

## Decision

### 1. Tombstones are DB-resident and append-only

`memory_tombstones` records every supported-lane `delete` / `rename`, plus an
explicit `lift`. Latest row wins per `topic_key`; the sync may never re-INSERT a
key whose latest row is `delete`/`rename`.

- **DB-only, never rendered into memory.md.** The file is the untrusted side: it
  is hand-editable and it is the artifact that goes stale. A deletion record
  that lives in the file is a deletion record a stale file can erase. (This also
  banks the design cut: no "## Deleted" section — more parser surface, more
  states to round-trip, for a record the file has no business holding.)
- **Append-only structurally**, via the 0004/0009/0020 `RAISE(ABORT)` trigger
  pair — not convention. A deletion record a later write can rewrite is the same
  hole one layer down.
- **`thread_id` is a plain integer, not a foreign key** (0020 precedent). The
  log must outlive the row; `PRAGMA foreign_keys` is ON per connection, so a
  real FK would either block the delete or cascade away the very record whose
  job is to survive it.
- **No expiry, ever.** Expiry reopens the hole precisely for the oldest, stalest
  files — the ones most likely to be wrong. Rows are tiny; N threads is tens.
- **Rename tombstones block re-INSERT of the old name only.** No note-mapping
  across renames in v1 (banked cut): once a recency gate exists, the "stale file
  dismisses the renamed row" case is unreachable, so carrying edits forward
  across a rename chain buys nothing. The successor key is stored so the
  disclosure can name the thread as it lives today; chains resolve iteratively
  with a visited-set cycle guard.

### 2. Recency is a generation stamp — never mtime

`sync_state.sync_generation` increments on every DB→file render and is echoed
into the file's header comment together with a pairing identity:

```
<!-- newslens-sync: gen=N identity=<hex> profile=<slug|-> rendered=<iso8601> -->
```

Import precondition: **file gen == DB gen AND identity matches.** Equal gen
means the file descends from the last render; hand-edits don't touch the stamp,
so the principal's normal edit loop passes untouched. A reconstruction, a
restored backup, or a copy from another profile carries an old gen, no stamp, or
a foreign identity.

- `write_memory_file` is the ONE place the counter moves (bump, then render), so
  "the counter advanced" and "the file was rewritten" are the same event. If the
  write then fails, the DB is one generation ahead — which reads as stale next
  time, i.e. the failure lands on the refuse side, never the silently-import
  side. `render_file` alone never bumps, so rendering for inspection cannot
  invalidate the file on disk.
- **The stamp is an HTML comment**, which `parse_file` already skips. A
  pre-NL-81 build reads a stamped file with no behavior change: rollback is
  "revert the code", with nothing to unwind.
- **Bootstrap is one-shot**, keyed on generation 0 — the live file meets this
  path exactly once. Zero pending edits → adopt and stamp. Any pending edit →
  stale. No silent adoption of an unstamped file that disagrees. After the first
  render, unstamped is stale on its face, so the path cannot be re-entered by
  deleting the stamp out of a file you want imported.
- **Pairing identity is profile-aware from day one.** Stage 0 gives every
  profile its own DB and its own memory.md, so the new failure mode profiles
  introduce is cross-pairing (an env-var slip through the
  `NEWSLENS_MEMORY_FILE` / `NEWSLENS_DATA_DIR` seams, or a copied file). The
  field costs one line now; retrofitting it later recreates the
  unstamped-legacy ambiguity once per profile, in the wild.

The stamp defends against stale files, not adversarial ones — gen and identity
are printed in the header, so a hand-forged stamp passes the recency gate by
construction. That is inside NL-81's threat model, and under forgery the deeper
layers (tombstone blocking, mechanism-only attribution) still hold (QA defeat
probes, 2026-07-25).

### 3. The refusal surface splits by caller

Rejected: refuse everywhere. Throwing inside `ranking.py`'s opening sync kills
the morning edition at 3am over a stale *file* — strictly worse than the
disease.

- **Interactive (`newslens memory …`):** a writing verb refuses outright, exit
  1, disclosing what the file *would* have done and the two exits
  (`memory sync --accept-file`, or delete the file and let it regenerate).
  `memory list` is the single exception: it writes nothing at all, so it
  degrades to database state rather than denying the principal the very
  inspection that diagnoses the refusal.
- **Embedded (`ranking`, `server._with_memory`):** degrade. Skip the import, run
  on database state, skip the post-run file refresh too, and put the refusal
  somewhere unmissable (`report.warnings`, the JSON `warnings` key).
- **On refusal, neither side is mutated** — no import, no dormancy pass, no file
  rewrite. Skipping dormancy is deliberate: it is a database write, and "the
  refusal changed nothing" has to be literally true to be checkable.

`--accept-file` overrides **staleness only**. Tombstone blocking and the
attribution rule hold underneath it. (Preserved dissent: `--accept-file` is
still a one-command mass-edit door; per-line confirmation was cut for v1 and is
the pre-agreed answer if that lane ever burns us.)

### 4. "By you" only when it was you

`memory.dismissed_via` records which surface caused a dismissal:

| value | source | rendered |
|---|---|---|
| `principal` | a CLI/UI verb | `(dismissed by you <date>)` |
| `file_sync` | the sync inferred it from the file | `(removed from your memory.md <date>)` |
| NULL | pre-0022; provenance unrecoverable | `(dismissed <date>)` |

`parse_file` reads all three back to `dismissed_user`, so an unchanged Inactive
line produces zero writes and a round-trip cannot launder `file_sync` into
`principal`. **A bare line moved under Inactive gets `file_sync`**, by ruling:
it is explicit intent, but it arrives via the file, and the file can be written
by anyone — including the org. That is the incident. We never backfill agency we
cannot prove.

## Alternatives rejected

- **mtime + a deleted-topics table** (the cheap version). Disqualified by the
  incident itself: the poisoned file had the newest mtime on the machine.
- **Tombstones rendered into memory.md.** More parser surface and more
  round-trip states, to put the deletion record on the untrusted side.
- **Refuse at every call site.** Kills the 3am edition over a file.
- **Tombstone expiry.** Reopens the hole for exactly the oldest files.
- **Note-mapping across rename chains.** Unreachable once the recency gate
  exists; pure v1 cost.

## Consequences

- Migration 0022 is additive and applies on the principal's next restart (0018
  pattern). It is nonetheless a **checkpoint**: a schema change *and* a visible
  copy change — existing dismissed lines with no recorded provenance move from
  "(dismissed by you …)" to the neutral "(dismissed …)".
- Every profile created at Stage 0 runs 0022 and gets clean namespaces; the
  retrofit cost for tombstones, provenance, counters and the lift lane is zero.
  **Revisit-if:** Stage 0 abandons per-profile DBs for a shared one — then
  `memory_tombstones` and `sync_state` each need a profile column.
- Dormancy does not run while a file is stale. Disclosed, and it resumes on the
  next lawful sync; the alternative was writing to the database inside a call
  that promises it changed nothing.
- Hand-authored test fixtures must now carry a lawful stamp to be imported.
- The UI's "you deleted this on <date> — bring it back?" confirm prompt is NOT
  in this milestone. The CLI lift lane and the refusal copy are the v1 contract;
  the UI prompt rides the create door's existing revive-merge seam. Named
  residual, not a silent gap.
