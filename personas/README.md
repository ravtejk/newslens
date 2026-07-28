# Synthetic personas — quality-audit fixtures

Stage-0 M3 (2026-07-27). Three org-authored reader worlds the org can generate
briefings for and read critically, plus the one-command affordances that serve
them.

---

## THE WALL

**These are quality-auditable fixtures. They are never engagement evidence.**

The multi-user round (`workspace/briefs/2026-07-16--newslens--multi-user.md` §2)
put this clause in the scope in these words: *3 synthetic personas (one
deliberately off-distribution; near-neighbor probes = the real
shallow-personalization kill-test; the WALL: quality-auditable, never
engagement evidence)*.

What that means operationally, and what every reader of a persona edition owes:

- A persona is **invented by the org**. Nobody chose these tags. Nobody read
  these editions and came back. There is no preference here to discover.
- Persona output answers **"is this any good, and is it honest?"** — coverage,
  faithfulness to source, whether two different readers actually get two
  different papers, whether a reader outside the source pack's world is told
  the truth about that.
- Persona output can never answer **"do people want this?"**, "does it retain",
  "which framing wins". Counting persona reads, opens, follows or sessions and
  reporting the number as signal is the failure this wall exists to prevent.
  There is no n here. There is one org, talking to itself, three times.
- Nothing in a persona world is a user. Nothing generated from one goes into a
  metric, a deck, or a claim about demand.

The falsification cohort in the multi-user brief (4+1 real testers) is the
instrument that answers demand questions. These fixtures are not a cheaper
version of it and must never be described as one.

---

## The three personas

| Slug | Role | Reader | Port |
|---|---|---|---|
| `rates-desk` | near-neighbour A | The rates-and-credit reader | 8485 |
| `energy-desk` | near-neighbour B | The energy-and-commodities reader | 8486 |
| `public-health` | off-distribution | The health-systems reader | 8487 |

The founder is the `default` profile and keeps port **8484**. He does not move
and is not one of these. Switching personas means visiting a different port —
there is no identity in the shell and no profile switcher in the UI (ruled
architecture, multi-user brief §2: *"No in-shell identity — switching profiles
is visiting a different instance"*).

Each persona's full design intent lives in its own fixture file
(`personas/<slug>.yaml`), next to the tags it explains.

---

## Probe design

### Probe 1 — the shallow-personalization kill-test (`rates-desk` vs `energy-desk`)

The pair is built to a pre-registered geometry: **high overlap at the domain
rung, near-zero overlap at the topic rung.**

| | shared | union | Jaccard |
|---|---|---|---|
| domain rung | 2 | 4 | **0.50** |
| topic rung | 1 | 15 | **0.067** |

Both readers draw from the same 42-outlet source catalog and therefore from
near-identical corpora. The only thing that can separate their editions is the
interest signal. So:

- **Editions materially differ** → the topic rung does real work. The finding
  is *how much*, and whether the difference is the right difference.
- **Editions are near-identical** → personalization is shallow: either it lives
  only at the domain rung (where these two readers are 50% the same) or it is
  not biting at all. That is the kill.

`Inflation` is a deliberate single shared topic — the control. A pair with zero
topic overlap would be two strangers; the shared topic is what proves the two
worlds are reading the same corpus on the same day.

Read the two editions side by side. The question is not "are they different
strings" — a stochastic writer will always produce different strings. The
question is **"are they different papers"**: different stories selected,
different emphasis, a rates reader's morning versus a commodities reader's.

### Probe 2 — the off-distribution honesty test (`public-health`)

Zero tag overlap with the founder and with both near neighbours, at both rungs.
The source pack was assembled for a macro/geopolitics/credit reader.

**The pass condition is honesty, not richness.** A thin edition, a plainly
disclosed shortage, or a refusal is a PASS. A confident full edition assembled
out of material that does not really cover this reader's world is the defect.

### Probe 3 — cold-start continuity (all three, day one)

Every persona is born with a 0-byte `memory.md`, no threads, no prior edition
and no ledger. A day-one edition that claims continuity — *"as we've been
tracking"*, *"regular listeners will remember"* — is fabricating a relationship
with a reader it has never met. The Stage-0 M2 script-continuity net
(`generate.py:_SCRIPT_CONTINUITY_RE`) and the M0 narrative nets exist to catch
exactly that, and the persona worlds are their first real cold-start subjects.

---

## Vocabulary — how the NL-17 altitudes map onto the shipped file

Fixtures are authored in the **NL-17 catalog vocabulary**: `domain:` and
`topic:` (DECISIONS 2026-07-25, *"the Commissioning's picker builds on the
NL-17 catalog (Domain→Topic→Entity), never on the dying broad/granular
vocabulary"*).

The shipped storage keys are still `broad:` / `granular:` — that is what
`config.py` accepts (`_VALID_INTEREST_KEYS = {"broad", "granular"}`) and what
the topic editor writes. The shipped ranker already renames them to the catalog
altitudes on the way to the model (`ranking.py:466-467` renders
`interests_broad` as `(domain)` and `interests_granular` as `(topic)`), so the
ladder is live in the prompt layer and only the file keys are behind.

So the provisioner maps, once, in one place:

    fixture `domain:`  →  sources.yaml `broad:`     (rendered "(domain)")
    fixture `topic:`   →  sources.yaml `granular:`  (rendered "(topic)")

The fixtures never say `broad` or `granular`. When the interests file catches
up to the catalog, the mapping in `newslens/personas.py` is the only thing that
changes and no fixture is re-authored.

**The `entity` rung is absent, not faked.** The interests file has exactly two
rungs; there is no entity-level interest to write. Entity-grade following lives
on the thread machinery (NL-17-M1b/M1c), not in `sources.yaml`, and a fresh
persona follows nobody and nothing by law. Writing entity strings into
`granular:` would put a third rung's concepts under the second rung's label and
weight — the "one concept = one vocabulary, tag XOR entity" acceptance criterion
(TRACKER NL-17) forbids exactly that.

---

## Running them

    scripts/persona-provision --all      # mint the three worlds (idempotent-refusing)
    scripts/persona-provision --list     # what exists, honestly
    scripts/persona-ready --all          # $0 first-briefing readiness, no spend
    scripts/persona-serve rates-desk     # http://127.0.0.1:8485/
    scripts/persona-generate rates-desk  # the first briefing (SPENDS TIME, $0 charged)

`persona-serve` and `persona-generate` refuse the founder's `default` profile
by name — these tools exist for the fixtures, and his world is reached the way
it always was (`newslens serve`, `newslens generate`).

### Why `persona-generate` exists instead of a documented flag string

`newslens --profile <p> generate` is *not* sufficient to run a persona at $0.
The one metered residual left after the 2026-07-25 discovery pause is
`analysis._sonar_verify`, gated only on `PERPLEXITY_API_KEY` being empty — and
**deleting the variable from the environment does not make it empty.**
`config.load_env()` calls `load_dotenv(override=False)`, and python-dotenv's
override check is `if k in os.environ` — so a *deleted* variable is re-injected
from the principal's `.env` on the next line, live and chargeable. A variable
set to the empty string is present, is not overridden, and reads as absent
everywhere the key is consumed.

`persona-generate` sets it to empty rather than deleting it, and verifies the
scrub survived `load_env()` in the child before the run starts. That single
detail is why this is a script and not a README sentence someone retypes.

Both run doors (`persona-serve`, `persona-generate`) also refuse to start if
tier-2 discovery resolves UNPAUSED after `load_env` (the
`NEWSLENS_DISCOVERY_ENABLED` opt-in, NL-102's testing knob) — discovery is a
second metered Sonar path, paused by the 2026-07-25 ruling, and a persona run
never rides an exception the org has not ruled.

---

## What a persona world contains, and what it never touches

Created by `newslens profile create <slug>` (the shipped lane), under
`profiles/<slug>/` — **gitignored**, because a reader's reading is the most
private state this repo holds:

    profiles/<slug>/data/newslens.db          fully migrated, zero rows of content
    profiles/<slug>/data/generation_log.jsonl its own spend ledger (created on first run)
    profiles/<slug>/memory.md                 0 bytes — the lawful unseeded start
    profiles/<slug>/sources.yaml              the committed catalog + this persona's tags

Never touched by any of this: the founder's `data/`, `data/newslens.db`,
`memory.md`, and root `sources.yaml`. `.env` is deliberately shared — keys are
machine credentials, not reader state — and is never edited by these tools.

The committed, reviewable half of a persona is the fixture in this directory.
The world under `profiles/` is local state that can be deleted and re-minted
from the fixture at any time.
