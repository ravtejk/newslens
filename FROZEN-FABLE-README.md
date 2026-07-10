# FROZEN — Fable line, as of 2026-07-07

This folder is a **complete, independent, frozen snapshot of the pre-Opus (Fable) line**
of NewsLens. It was created 2026-07-07 at the principal's instruction, so the Fable work
can be resumed exactly where it left off, fully isolated from the Opus experiment.

## What this is
- **Code:** Fable's exact interrupted state — `prototype-import @ 91403a8` **plus** the
  in-progress P3.1 editorial-enforcement work that Fable had floating (uncommitted) at the
  moment of the model switch: modified `src/newslens/generate.py`, `prompts/editor_pass.txt`,
  `sources.yaml`, and the untracked `tests/fixtures/script/2026-07-06-repetitive.txt`.
  (This is Fable's real interrupted state, warts and all — e.g. the in-progress
  `sources.yaml` edit — not the polished Opus version.)
- **Data:** a copy of the live database as of the freeze (25 `consumption_events`,
  17 memory rows, briefings 07-04/05/06). This is a **separate database file** from the
  Opus line — the two never share data again from here.
- **venv:** rebuilt in place, so it imports THIS folder's `src` (verified), never the Opus
  folder's.

## This is fully independent of the Opus line
- **Opus (live) line:** `~/Downloads/product-org/workspace/products/newslens/prototype`
  (branch `opus-work`). All ongoing Opus work happens there.
- Reading or building in either folder **cannot** affect the other — separate code,
  separate databases, separate venvs.

## To resume Fable here
```bash
cd ~/Downloads/newslens-fable-frozen
.venv/bin/newslens serve      # then open http://127.0.0.1:8484/
```
It stays "Fable" as long as you don't `git checkout` a different branch in this folder
(it's on `prototype-import`).

## One note
The ~546 MB local Kokoro TTS **model** was excluded from the copy (it's re-downloadable
via `scripts/setup_tts` if you want to generate audio). Text reading, generation, memory,
and everything else work as-is; only Kokoro audio synthesis needs that one-time re-fetch.
