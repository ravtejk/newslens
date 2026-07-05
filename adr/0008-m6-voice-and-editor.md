# ADR 0008 — Milestone-6: the voice (TTS) + the editor pass

**Date:** 2026-07-05 · **Status:** accepted · **Milestone:** 6
**Contract:** spec §E-M6; TTS reconvene (engineering-2); editor decision
(DECISIONS.md 2026-07-05).

## TTS decisions

1. **Packaging finding (new evidence):** current Kokoro packaging requires
   Python >=3.10 on both routes — kokoro-onnx is ResolutionImpossible on
   3.9, and the kokoro/torch route dies building spacy from source. This
   machine has only system Python 3.9.6 + Homebrew. The reconvene didn't
   have this datum.
2. **Resolution: isolated engine venv, not a floor bump.** The app keeps its
   3.9 floor (the pre-install doctor contract depends on system Python);
   the engine lives in data/tts/venv on brew python@3.12 — ONE brew
   dependency, exactly the budget the reconvene priced (it was espeak-ng
   then; espeak now arrives as a pip wheel). kokoro-onnx chosen over
   kokoro/torch: no 2GB torch, onnxruntime arm64 wheels, same model.
   audio.py invokes tools/tts_runner.py by subprocess (text in, WAV +
   JSON stats out) — the wrapper boundary makes the isolation invisible.
3. **THE MEASUREMENT (Rook's dissent vindicated, said loudly):** this
   machine synthesizes at **~4.4x realtime** (749-word script -> 316s audio
   in 71s) — far below the reconvene's 14x M-series floor. The operational
   bar ("minutes not an hour, nobody waiting") still clears: ~3 min for a
   full-length script. Whether 4.4x re-opens the vendor choice is the
   PRINCIPAL'S call at the M6 ear test — flagged, not absorbed.
4. **gpt-4o-mini-tts fallback fully built** behind the same wrapper:
   paragraph-boundary chunking under the 4,096-char API cap, lossless WAV
   concatenation via stdlib wave, ~$0.015/min. Engine choice =
   settings.tts_engine (kokoro|openai, default kokoro) — a config flip.
5. **Doctor runs a REAL short synthesis** (engineering-2: not a liveness
   ping), reporting duration/time/rate with the below-floor note; QA seam:
   NEWSLENS_DOCTOR_TTS_SYNTH=0 skips with an INFO marker (QA rules on the
   pattern); engine-missing renders the scripts/setup_tts fix.
6. Audio is generate's LAST step and degrades to a no-audio run WITH
   disclosure — the text briefing is never hostage to synthesis.

## Editor-pass decisions

7. **Position:** between the writer's draft (shape-checked only) and FULL
   validation — the edited payload is what gets validated, persisted, and
   script-adapted. The editor prompt (prompts/editor_pass.txt) is built
   from A1-A6: cut/tighten/concretize-from-present, strip moralizing,
   attribute-or-cut predictions, hedges immutable in both directions.
   **The editor never adds facts** — its concretizations must already
   appear in the draft or the label data it sees.
8. **Structural guards, code-side:** same story count, same order, same
   tier per story (a changed tier is a validation error); then every
   narrative validator re-runs on the edited payload.
9. **Degrade, never die:** editor failure (call/shape/cap) falls back to
   the unedited draft with an explicit "editor: DEGRADED" disclosure; the
   before/after word counts + one-line summary go to warnings and the
   generation log; per-step cost logged (measured in the M6 report).

## Dependency enumeration (checkpoint)

- Homebrew: python@3.12 (new).
- pip, ISOLATED engine venv only (data/tts/venv — nothing added to the app
  venv): kokoro-onnx 0.5.0, soundfile, and transitives (onnxruntime, numpy,
  espeakng-loader, phonemizer-fork, et al. — pip freeze in the report).
- Model artifacts (data/tts/, gitignored): kokoro-v1.0.onnx (310MB),
  voices-v1.0.bin (27MB).
- App venv: NO new dependencies.
