# ADR-0019 — Stage-0 M1: the guarded profile dimension, and the death of first-run seeding

**Date:** 2026-07-25 · **Status:** accepted (build-crew loop; QA + gate follow)
**Context:** multi-user Executive Brief 2026-07-16 §1 "The correction (Rook)" and
§2 (reconciled Stage-0 scope) · TRACKER NL-78 · M0 cold-start QA report
`research/2026-07-25--m0-coldstart-qa.md` findings F1/F4 · ADR-0018 (NL-81).

---

## 1. The decision

NewsLens becomes multi-reader by adding a **profile** dimension **inside** the
existing real-paths guard, and by **deleting** the first-run seeding path.

## 2. What / why / what else was considered

### 2.1 Profiles are resolved on the sanctioned arm of `paths.__getattr__`

**What:** `--profile <name>` / `NEWSLENS_PROFILE` selects a profile;
`paths.__getattr__` resolves the five guarded names (`DATA_DIR`, `DB_PATH`,
`SOURCES_FILE`, `ENV_FILE`, `MEMORY_FILE`) for that profile *after* the
sanction check, never before it.

**Why:** the side-chat sketch was "profiles = point `NEWSLENS_DATA_DIR` at a
different directory". Rook's kill stands: that seam's law is **redirection =
not real state** — it exists so a sandbox can write freely without touching the
principal's world. Implementing profiles on it would have put every tester's
memory behind the door labelled *write-freely*, inverting the incident guard
(2026-07-14, 2026-07-16) for exactly the users whose data we have the least
right to lose. A profile's DB, corpus, spend log and memory.md are **real
state** and are refused to an unsanctioned process identically to the
founder's.

**Precedence, unchanged:** redirection still outranks sanction, which now
outranks the profile. A sandbox that redirects gets the sandbox, whatever
profile is active.

**Else considered:** a sixth guarded name `PROFILES_DIR` with its own env
override — rejected, it is a new env var (an escalation item) for something
the existing `NEWSLENS_DATA_DIR` anchor already gives (`anchor_dir()` =
`NEWSLENS_DATA_DIR`'s parent, or `PROJECT_ROOT`).

### 2.2 Zero-move adoption: the founder IS the `default` profile

**What:** `profiles/` holds every profile *except* the default. The default
profile's five locations are `paths._GUARDED` — literally the same dict the
pre-M1 code returned, returned by the same line of code. No file moves, no
renames, no data migration, no relocation of `data/`, `memory.md` or
`sources.yaml`.

**Why:** the principal's live world is the one irreplaceable thing in this
repo, and "we moved your data, it should be fine" is not a sentence the org
gets to say. `test_stage0_m1_profiles.py::test_default_profile_layout_is_
literally_the_pre_m1_guarded_table` pins the identity so a future tidy-up
cannot silently relocate him.

**Else considered:** `profiles/default/` symlinked to the checkout — rejected;
a symlink is a move with extra steps, and every backup, editor and `stat` in
the chain gets a chance to disagree.

### 2.3 `.env` is NOT profile-scoped

Keys are machine credentials, not reader state; one machine has one set. Every
profile resolves the same `.env`. Per-profile **spend** is separated by the
per-profile `generation_log.jsonl`, which hangs off `DATA_DIR` and therefore
splits for free.

### 2.4 Provisioning inherits nothing

`newslens profile create <name>` makes: a fresh database with **every**
migration applied, a **0-byte `memory.md`**, its own `data/`, and its own
`sources.yaml` copied from `templates/profile-sources.yaml`.

- **0-byte memory.md:** M0 proved it is a lawful true-zero start
  (`parse_file("") == []`, gen-0 bootstrap adopts it, stamp gen 1). Kept even
  after the seeding kill as defence in depth — a file that EXISTS forecloses
  any future empty-file bootstrap arm.
- **`templates/profile-sources.yaml`:** generated at M1 from the **committed**
  `sources.yaml` at fa26e45, never from the principal's working copy (which
  carries his own uncommitted tuning — his local edit stays his). No runtime
  `git` dependency: the template is a committed artifact, in the same class as
  `migrations/` and `prompts/`.
- **Interests deliberately EMPTY** — see §3, the one scope call in this ADR.
- **Refuses over an existing profile**, and refuses `default` outright.

### 2.5 First-run seeding is dead (M0 F1 / RED-1)

`memory.seed_if_first_run` and `memory.SEED_THREADS` are **deleted**.
`SyncResult.seeded` remains, permanently 0, so every caller and disclosure
that reads it keeps reading a truthful number.

**Kill, not founder-gate.** The gate was the obvious alternative and it was
rejected on three grounds: (a) the founder cannot reach the function anyway —
his memory table has rows and his `memory.md` exists, so it has returned 0 for
him since M4; (b) the only surviving caller shape is his own from-zero
reinstall, where replanting a 2026-07-04 taxonomy over what he actually
follows today would be wrong, not helpful; (c) **a gate is a thing that can be
tripped** — RED-1's contract is that a non-founder profile be *structurally*
unable to seed, and structure beats a conditional. The 14-thread list survives
in git history (last at fa26e45) and in ADR-0005 §2.

### 2.6 Ops

- `newslens migrate --all-profiles` — every profile's DB, default first.
- The doctor prints the profile it checked and takes `--profile` (so
  `scripts/doctor`, the pre-install entrypoint, stays usable per profile).
  **NL-98's future line** (memory.md stamp vs `sync_state`) slots into
  `check_database()`, next to this — marked in the code, deliberately not
  built here.
- NL-81's pairing identity is now genuinely per-profile: provisioning stamps
  `sync_state.profile_slug`, so a cross-profile `memory.md` is refused *by
  name* ("file … profile alice … this database … profile bob").
- An **unknown** profile name is refused, never created: `db.connect()` makes
  parent directories, so `--profile alcie` would otherwise mint a silent empty
  world and bury a reader's writes in it.

### 2.7 QA fix loop 1 (2026-07-25) — what the first pass got wrong

**F1 (MAJOR): the doctor validated only the `--profile` flag, not the
environment.** With `NEWSLENS_PROFILE=ghost` — the exact shell/launchd shape
this ADR and SETUP.md document — a typo'd name walked past every check and the
doctor's writability probe (`check_database` step 2,
`DATA_DIR.mkdir(parents=True)`) **created** `profiles/ghost/data/`. After that
one run `require_exists` finds a directory, so every other verb accepts the
typo too: `migrate` gives it a database, `memory add` buries a reader's writes
in it. The refusal was real in `cli.main` and absent in `doctor.main`; the
asymmetry was the bug.

Fixed on both sides, deliberately:
1. **At the boundary** — `doctor.main` now resolves the profile exactly the
   way `cli.main` does: always, however it was selected, under one `try` that
   catches malformed names too (those previously escaped as an uncaught
   `ProfileError` traceback out of a health check — safe direction, wrong
   contract). This also erases the doctor's half of the in-process pin leak,
   since `set_profile(None)` clears.
2. **At the minting site** — the writability probe now refuses to `mkdir` a
   profile root that does not exist, so a future entrypoint that forgets
   `require_exists` still cannot bring a reader's world into being. Scoped
   precisely: it fires only when the probe target really lives inside the
   missing root, so a path redirection (not real state, by law) is unaffected
   and the probe keeps doing its actual job for profiles that exist.
   Residual, accepted (QA charge-2, gate-confirmed): a redirection aimed INTO
   the real `profiles/<missing>` namespace mints at the operator's own hand
   and `require_exists` then accepts it — `mkdir -p` equivalent; guarding it
   would treat redirected targets as real state, inverting the seam.

**F3 (MINOR, but it shut the Commissioning's own door): the template's
`interests: broad: []` broke the UI's add-an-interest editor.** The editor
appends a block-sequence item (`    - X`) under the key; beneath an empty FLOW
list that is invalid YAML, so the editor's validator reverted the write and
told the reader the edit failed. A new reader could not choose their first
interests through the served UI — the one act §3 routes them toward — while
the hand-edit path worked.

Fixed on both sides too. The template now uses **bare keys** (`broad:` with no
value), which is not a new convention: it is exactly what `topic_remove`
leaves behind when you delete your last interest, so the editor round-trips
through its own output. And `topic_add` gained a narrow tolerance
(`_open_empty_flow_list`) that rewrites an *empty* flow list to the bare-key
form before inserting — which defends hand-written `broad: []` as well, not
just our template. A **populated** flow list is deliberately left alone:
converting it means moving items, a different and riskier edit, and its
current behaviour (honest revert, file restored intact) is already safe. Both
halves are pinned, including a scope fence on the populated case.

## 3. The one scope call, stated plainly

The dispatch said new profiles get "their own copy of the committed default".
The committed default's `interests:` block is the principal's 40-tag list.
**Interest tags are the strongest personalization signal in the system** — the
same class of inheritance as the memory threads RED-1 exists to kill — so the
template ships the org source catalog with **interests empty**.

The consequence is deliberate and loud: `ranking` already refuses, by name,
when a profile has no interests ("no interests configured in sources.yaml").
A provisioned profile therefore *cannot rank* until its reader has chosen
their own tags — which is precisely the Stage-0 Commissioning's job. Per-profile
interests move out of `sources.yaml` entirely in **M2**.

Two residuals ride this, both named for M2 rather than papered over:
1. `templates/profile-sources.yaml` will drift if the org edits the shared
   catalog. A test pins that it parses clean with active sources and zero
   interests; nothing pins it against `sources.yaml`, because `sources.yaml`
   is the principal's working file and no test may read it as a reference.
2. The catalog carries three `followed_analyst: true` outlets described as
   "principal-followed". That is a mild steering signal in a shared catalog;
   it is an editorial call for M2, not a plumbing call for M1.

## 4. What this does NOT do

No schema migration (profiles are filesystem/config-level namespacing; 0022
already carried `sync_state.profile_slug`). No per-profile ingest/rank/caps
(M2). No Commissioning UI (design track, behind the mockup gate). No script
continuity net (M2 — the M0 RED-2 pin is parked `xfail(strict=True)`, tagged
STAGE0-M2, so the day the net lands the suite goes red until the pin is
re-adopted).
