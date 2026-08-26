# NewsLens setup — clone to first doctor pass

Goal: everything on this page takes minutes, and `scripts/doctor` tells you
your exact remaining steps at every point. When the doctor exits `0`, you're
done with setup. Exit `0` is reachable with just the OpenAI key: Perplexity
is deferred-by-choice (informational, not failing). One honest exception: if
an outlet's feed dies upstream (404/moved), the doctor fails it until you fix
the URL or set `enabled: false` on that source in `sources.yaml` — a feed you
believe you're reading but aren't is a real setup problem, not noise.

## 0. Prerequisites

- macOS with Python **3.9 or newer** — the system Python is fine
  (`python3 --version`; this machine's `/usr/bin/python3` is 3.9.6, which is
  exactly what the project targets). Nothing else to install.

## 1. Install

```bash
cd workspace/products/newslens/prototype
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip        # required once: stock 3.9 pip (21.2) can't do
                                 # editable pyproject installs (needs >= 21.3)
pip install -e ".[dev]"
```

Sanity check before any keys exist — this should already work:

```bash
newslens migrate     # creates data/newslens.db
scripts/doctor       # friendly report; exit 1 is EXPECTED until keys+sources are in
.venv/bin/newslens serve   # after your first generate: the UI at http://127.0.0.1:8484/
```

## 2. Create your .env

```bash
cp .env.example .env
```

Fill it in **yourself** — never paste keys into chat with the org's agents; if
a key ever ends up in a chat or a commit, rotate it at the provider first and
then fix the leak. `.env` is gitignored.

### 2a. OPENAI_API_KEY (required — text generation)

1. Go to <https://platform.openai.com/api-keys> → **Create new secret key**
   (a default project key is fine; no special permissions needed).
2. **Set a hard spend cap in the dashboard** — Settings → Organization →
   Limits → set a monthly budget (e.g. $10 — expected generation spend is well
   under $1/month for text; audio costs this key **nothing by default** —
   NL-96 (the $0-run law, 2026-07-25) put the default voice back on Kokoro-82M
   local. Your 2026-07-06 ear-test pick, gpt-4o-mini-tts, is one line away
   (`settings.tts_engine: openai`) and then costs ~$0.015/min, ~+$0.07/run,
   roughly ~$2/month at daily cadence).
3. Put the key in `.env` as `OPENAI_API_KEY=...`

### 2b. PERPLEXITY_API_KEY (not needed — tier-2 discovery is PAUSED)

**Ruling 2026-07-25: tier-2 Sonar discovery is paused.** Over three weeks it
contributed 0.41% of cited items, and three vendor configurations each
returned roughly one usable item per 180 results. `ingest` no longer makes a
discovery call at all — key present or absent — and the doctor reports the
pause rather than asking you for a key.

**One Sonar caller is still live and still metered when this key IS set:**
analysis *verification*, one call per depth-tier story on `analyze` /
`generate` (~$0.003 on the 2026-07-25 edition, logged as `analysis_usd`).
That caller was outside the pause ruling's scope. **If you want every Sonar
path in the product cold, comment `PERPLEXITY_API_KEY` out of `.env`** — key
gating stops all of it instantly, with no code change.

Discovery can be re-armed for testing (NL-102: the Claude-web-search
comparison) with `NEWSLENS_DISCOVERY_ENABLED=1`. It is deliberately not in
`.env.example`: it is a testing opt-in, not a setting to fill in.

If you ever do want a key:

1. Go to <https://www.perplexity.ai/settings/api> → generate an API key.
2. Sonar is pay-as-you-go against a **prepaid credit balance — that balance is
   your real spend cap**; load the minimum (e.g. $5 — expected spend is cents
   per month). `BUDGET_CAP_USD_PER_RUN` in `.env` is only a secondary,
   in-app guard.
3. Put the key in `.env` as `PERPLEXITY_API_KEY=...`

### 2c. The Claude CLI — the subscription lane for the content seats (B3 + item C)

The B3 depth-architecture flip (2026-07-16) plus item C (2026-07-17, field-proven
edition 7) run **all the anthropic content seats** on the **`claude -p`
subscription lane by default** — the **ranking** seat (Claude Sonnet 5) and the
**editorial-tighten**, **TTS-script**, **memory/state**, **writer** and
**analyst** seats (Claude Opus 4.8). They ride your Claude subscription (a flat-rate seat), not
the metered API. `usd_charged` for these seats is **$0.00**; the ledger still
records `usd_shadow` (what the API would have cost) so caps and dashboards stay
honest — a default edition charges ~$0 while its shadow compute is ~$0.90–1.30.

You grant this by **installing the CLI and logging in** — no key needed for the
default path:

1. **Install the Claude CLI.** On this machine it's already at
   `~/.local/bin/claude` (the doctor confirms). If you ever need to reinstall,
   follow the official install for your platform.
2. **Log in once, interactively:** run `claude` (no flags) and complete the
   subscription login. The subscription lane reuses that stored login; NewsLens
   never handles your credentials.
3. **PATH note:** the CLI is not on the non-login-shell PATH here. NewsLens
   resolves it as `NEWSLENS_CLAUDE_BIN` → `PATH` → `~/.local/bin/claude`, so the
   default just works. Set `NEWSLENS_CLAUDE_BIN=/full/path/to/claude` in `.env`
   only if you install it elsewhere.
4. **Verify:** `scripts/doctor` prints a "Subscription lane (claude -p)" section
   — which binary resolved, the CLI version, and an "auth NOT probed" note. By
   default the doctor does **not** spend your quota, and it is worth knowing
   exactly what that costs you: the binary and version checks both pass on a
   machine whose CLI is *logged out*, so a green section is not proof of login.

   To actually prove login, fire the live probe:

   ```
   NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE=1 scripts/doctor
   ```

   It sends one 1-token prompt (`ok`) through the same flags and the same
   stripped child env the real lane uses, and reports one of three things:
   **logged in** (costs a single token of quota), **NOT authenticated** (costs
   nothing — there is no session to bill — and prints the fix), or
   **inconclusive** (the call failed for something that is not an auth
   rejection, so login state is still unproven). `ANTHROPIC_API_KEY` is stripped
   from the probe's child process, so this check can never quietly become a
   metered API call.

   Worth running whenever generates start failing: an expired login is what took
   the pipeline down on 2026-08-24, and it now says so by name.

### 2c-alt. ANTHROPIC_API_KEY (the API fall-over credential)

`ANTHROPIC_API_KEY` is now needed only when an anthropic content seat (Sonnet 5
rank, or Opus 4.8 editor/script/state/writer/analyst) runs the **API lane**
instead of the subscription default — i.e. you pin `NEWSLENS_LANE_<SEAT>=api`, or
you arm `NEWSLENS_LANE_FALLBACK=api` (the principal-armed opt-in; the ship
checkpoint asks whether to arm it). The API lane spends real money the
subscription lane would not, so it stays opt-in.

1. Go to <https://console.anthropic.com/settings/keys> → **Create Key**.
2. **Set a hard monthly spend cap** — Settings → Billing.
3. Put it in `.env` as `ANTHROPIC_API_KEY=...`. Leave blank if you only ever run
   the subscription lane.

### 2d. Everything else in .env

- `BUDGET_CAP_USD_PER_RUN` — leave the 0.25 default unless you have a reason
  (recommended value cut from 0.50 with the M9 Analyst ruling, 2026-07-06 —
  if your .env still pins 0.50, lower it to match).
- `GENERATE_HOUR_LOCAL` — the local hour (0–23) a **scheduled** run fires at;
  default `6`. Dormant from your 2026-07-03 on-demand-only call until NL-146
  brought scheduling back. It only matters once you install the launchd agent
  (§5); on-demand `generate` ignores it. **Since NL-152 this is the fallback,
  not the last word:** setting the hour in Settings › Generation time writes
  `settings.generate_hour` in `sources.yaml` and that value wins. The doctor
  says which layer decided whenever the two disagree.
- `GNEWS_API_KEY` — **leave blank.** Deliberately ungranted fallback; only
  becomes relevant if the Sonar reliability spike fails, and that would come
  back to you as a checkpoint first.

## 3. Review your outlets, add your interests

`sources.yaml` is already seeded with your outlet list (2026-07-03), every
feed URL live-verified, tiered (`full` / `headline_only` / `cautious` /
`reference_only`) and flagged for wire syndication. Things worth a look:

- **Enable/disable** any source by flipping `enabled:` — cautious aggregators
  (Whatfinger) ship disabled and stay off until you explicitly opt in.
- **No-feed outlets** (FPRI, Times of Israel, WEF, CFR, Carnegie, BNN
  Bloomberg, Man Group, FinancialContent, VisaHQ, wn.com) are documented as
  comments in the file with the verified reason each has no usable feed.
- **CoS-suggested additions** (Guardian, FT, Axios, Politico, Economist,
  Chartbook, Noahpinion, Slow Boring) are **enabled** — you approved them
  2026-07-03; each carries an "approved" note in the file. Disable any by
  adding `enabled: false` to its entry.
- **Interests are still empty and yours to write** — broad tags steer
  ranking, granular tags sharpen it and shape the one capped discovery query
  per run. Discovery skips itself (and says so) until tags exist.

Then: `newslens ingest` pulls everything enabled into the local DB. Re-running
it the same UTC day updates in place — never duplicates.

## 3b. Profiles — a second reader on the same machine (Stage-0 M1)

Everything above describes **your** world, which NewsLens calls the `default`
profile. Adding profiles moves none of your files: `data/`, `memory.md` and
`sources.yaml` stay exactly where they are.

```bash
newslens profile create tester1     # fresh DB, empty memory.md, own catalog
newslens profile list               # honest status per profile; * = active
newslens profile refresh-catalog tester1          # DRY RUN: what the org
                                    # catalog has that this reader's copy does
                                    # not (a profile's catalog freezes at
                                    # create; new feed slates do not reach it)
newslens profile refresh-catalog tester1 --apply  # adopt them. Adds only —
                                    # your edits, interests and settings are
                                    # re-checked field by field before it writes
newslens --profile tester1 doctor   # health check for THAT reader's world
newslens --profile tester1 generate
newslens migrate --all-profiles     # upgrade every profile's database at once
```

What a new profile gets, and what it deliberately does not:

- **Its own** database, corpus, generated artifacts, spend log
  (`generation_log.jsonl`), `memory.md` and `sources.yaml`, all under
  `profiles/<name>/` (gitignored — a tester's reading is private state).
- **Nothing inherited.** No threads (there is no first-run seeding any more),
  no notes, no interest tags. Its `sources.yaml` is a copy of the committed
  catalog `templates/profile-sources.yaml` with the interests block empty — so
  `newslens --profile <name> rank` **refuses by name** until that reader
  chooses their own tags. That refusal is intentional: choosing is the
  reader's first act, never something inherited from you.
- **The same `.env`.** Keys are machine credentials, not reader state, and one
  machine has one set. Per-reader *spend* still separates, because each
  profile logs to its own `generation_log.jsonl`.
- **The same safety guard.** A profile's state is real state: refused to an
  unsanctioned process exactly like yours, never a sandbox redirection.

### The generation log rotates (NL-152 batch)

`data/generation_log.jsonl` is the append-only record of every run, every
analysis stage and every scheduled fire. It grows by roughly **26 KB per
generate** (measured on your own file: 700 KB across 51 lines, run entries
averaging 24 KB), and it is read on every page build — so it rotates.

- When the live file passes **4 MB**, the oldest lines move into
  `generation_log.archive-0001.jsonl` (then `-0002`, and so on) beside it, and
  the newest **60 runs** stay live. The reports screen shows 30, so it is always
  whole.
- **Rotation moves bytes; it never deletes them.** Archive segments accumulate
  and are never rewritten. Deleting old segments is your call, not the app's —
  nothing here will ever do it for you.
- Nothing you read gets shorter. `newslens diagnose` totals every segment, the
  per-profile spend ledger spans them, the settings row still counts every run
  ever recorded, and opening an archived edition still finds its entry.

`NEWSLENS_PROFILE=tester1` does the same thing as `--profile` for a shell or a
launchd job; the flag wins when both are set. An unknown or malformed name is
**refused, never created** — so a typo cannot quietly mint an empty world and
bury a reader's writes in it.

## 4. Verify

```bash
newslens migrate   # no-op if already run — safe to repeat
scripts/doctor
```

Expected end state: exit `0`, every required line `✓` — Python/deps, both
keys validated by harmless read-only calls (the Sonar ping costs a fraction of
a cent), schema applied, every feed URL resolving. Any `✗` line tells you the
fix inline; `⚠` lines are advisory.

## What the doctor looks like before you've done any of this

Fresh clone, no venv, no `.env`, template `sources.yaml` — `scripts/doctor`
still runs (stdlib-only) and exits `1` with, in short:

```
✗ missing Python deps: PyYAML, python-dotenv — fix: python3 -m venv .venv && ...
○ .env not found — run: cp .env.example .env  (then fill keys in; ...)
✗ OPENAI_API_KEY not set — get one at platform.openai.com/api-keys, then add to .env
○ tier-2 Sonar discovery is PAUSED by ruling (2026-07-25) ... No probe was fired and nothing was charged.
✓ migrations apply cleanly to a scratch DB — tables: briefings, briefings_history, memory, source_items
⚠ sources.yaml validation skipped (PyYAML not installed — see the missing-deps line above)
```

That's the designed experience: nothing crashes, every gap names its fix.

## 5. Scheduled generation — the edition is ready before you open it (NL-146)

Optional, and off until you install it. Generating an edition takes about half
an hour; scheduling it means you open, read, close.

**NewsLens never installs the launchd agent.** It renders the file and prints
the commands; you run them. Three steps:

```bash
mkdir -p ~/Library/LaunchAgents
newslens schedule plist > ~/Library/LaunchAgents/com.newslens.generate.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.newslens.generate.plist
newslens schedule status
```

`newslens schedule install-instructions` prints the same steps with your own
paths filled in. `newslens schedule plist` prints the agent and nothing else, so
the redirect above is safe.

**The hour** is set in **Settings › Generation time** (NL-152), which writes
`settings.generate_hour` into `sources.yaml`. If you have not set it there, it
falls back to `GENERATE_HOUR_LOCAL` in your `.env`, then to `6` (06:00 local).
Whichever layer wins is the hour baked into the agent.

Changing the hour — in Settings or in `.env` — does **not** change an
already-installed agent. The plist bakes the hour, and the fired command never
re-reads it, so the agent keeps firing at its old time until you re-render and
re-bootstrap. Settings tells you this at the moment you save, and both
`newslens schedule status` and the doctor keep saying it until you do:

```bash
launchctl bootout gui/$UID/com.newslens.generate
newslens schedule plist > ~/Library/LaunchAgents/com.newslens.generate.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.newslens.generate.plist
```

`scripts/doctor` warns if the installed agent's hour and the resolved hour
disagree, and names which layer set the one it is comparing against.

### Pause without uninstalling — the kill switch

**Settings › Scheduled generation** is the switch for this — the toggle flips
exactly the file below, so the screen, the doctor and a 6am fire are always
reading one state. The shell form is unchanged and still works:

```bash
touch data/SCHEDULE_PAUSED    # scheduled runs decline before spending anything
rm data/SCHEDULE_PAUSED       # resume
```

The agent stays loaded; the run declines at its first gate, records the decision,
and charges nothing. This is the switch for travelling, a metered connection, or
a few quiet days — uninstalling is for stopping altogether.

### What a scheduled run will and won't do

- **It won't generate twice.** A fire on a day that already has a published
  edition does nothing, and says so in the record.
- **It won't publish half an edition.** The body is written last, in one
  transaction. A run killed at 06:20 leaves yesterday's edition exactly as it was
  and today showing an honest empty state, not a masthead over a void.
- **It retries only a network outage.** If nothing could be fetched anywhere it
  backs off 15 minutes, then 45, then stops with a quiet note on the Today
  screen. Any other failure is **not** retried — it may already have spent money,
  and spending it twice unattended is the thing that guard exists to prevent.
- **It cannot outspend a run you started yourself.** The retries share ONE
  `BUDGET_CAP_USD_PER_RUN` between them, and a retry only ever follows a failure
  that cost nothing — once a session has charged real money the ladder stops
  there. A whole unattended morning therefore costs at most one attended run.
- **It won't run beside a generation you started.** A scheduled fire and the
  Generate button are two different processes, and two pipelines on one day
  would spend twice and race each other over the same database. Whichever starts
  first holds the machine's generation slot (`data/RUN_IN_FLIGHT` — ours to
  write and remove, never yours to touch) and the other declines with a reason.
  While a scheduled run is going, Today says so instead of showing an empty
  screen and a button that would be refused.
- **Every run is on the record.** Settings → Generation reports marks each entry
  `Scheduled` or `You ran it`.

### If your Mac is asleep at the scheduled hour

This is the common case, not the edge, and launchd — not NewsLens — decides what
happens: it starts the job **the next time the Mac wakes**, and several missed
days coalesce into **one** fire (`man launchd.plist`). A lid opened at 09:12
starts the run then, and the edition lands around 09:50 rather than having
waited for you since 06:00.

NewsLens adds no lateness rule on top of that, deliberately — how late is too
late is your call, not ours. If you want 06:00 to be real, the fix is at the
system level and is **your** command to run, not ours:

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 05:55   # wakes the Mac five minutes early
```

### Removing the schedule

```bash
launchctl bootout gui/$UID/com.newslens.generate
rm ~/Library/LaunchAgents/com.newslens.generate.plist
```

## Troubleshooting

- **`newslens schedule status` says the agent file is present but nothing ever
  runs** — a file on disk is not a loaded job. NewsLens can see the file; only
  launchd knows whether it is loaded. Check with
  `launchctl print gui/$UID/com.newslens.generate` — if that errors, you skipped
  (or lost) the `launchctl bootstrap` step.
- **Generate says "a generation is already running" and you don't think one
  is** — something holds the machine's generation slot, `data/RUN_IN_FLIGHT`.
  `scripts/doctor` and `newslens schedule status` print which process and since
  when. A slot whose process is gone is released automatically the next time
  anything looks (that is what the recorded pid is for), so a crashed 6am run
  never wedges the button. If the file is there and the pid in it really is a
  live NewsLens run, the honest answer is that a run *is* going — it takes about
  half an hour. `rm data/RUN_IN_FLIGHT` is the manual override and it is safe
  only when you are sure nothing is generating; removing it while a run works is
  how you get the two-pipelines-one-day case the file exists to prevent.
- **`pip install -e ".[dev]"` fails with a "editable mode" / PEP 660 error** —
  you skipped `pip install --upgrade pip`. Run it inside the venv, retry.
- **`newslens: command not found`** — the venv isn't activated
  (`source .venv/bin/activate`), or install failed. `scripts/doctor` works
  regardless and will say what's wrong.
- **A feed URL fails to resolve** — open the `rss_url` in a browser; outlets
  occasionally move feeds. The doctor treats each feed independently, so one
  bad URL never blocks the rest.
- **Start the database over** — `rm data/newslens.db && newslens migrate`.
  (Once real briefings exist, milestones 5+ preserve history on re-runs —
  deleting the DB is only ever a pre-data, milestone-1-era reset.)
- **Corporate VPN/proxy** — the doctor's API checks need outbound HTTPS to
  `api.openai.com` and `api.perplexity.ai`; failures say "network-shaped" when
  that's the likely cause.

## The voice — engine choice + optional local TTS setup

The default voice is **Kokoro-82M, local and $0** — NL-96 (2026-07-25) put it
back per the $0-run law: a run that was never told which engine to use must
never silently pick a metered one. It needs a one-time setup:

```bash
scripts/setup_tts   # brew python@3.12 + isolated engine venv + ~340MB model
scripts/doctor      # its TTS section runs a REAL short synthesis (kokoro)
```

**gpt-4o-mini-tts on your OpenAI key stays fully built and is still your
ear-test pick** (ruling 2026-07-06 — that ruling was about VOICE and it
stands; only the unstated-default case moved, on spend). Nothing to install
for it; pin `settings.tts_engine: openai` in sources.yaml and it costs
~$0.015/min, ~+$0.07/run. Either way the audio lands next to each briefing:
`data/briefings/<date>.wav`.

## Later milestones (placeholders, so this file has one home)

- **On-demand trigger + instrumentation (M7):** the `read`/`listen` commands
  whose usage log feeds the day-30 verdict. (The "no cron/launchd" half of this
  placeholder is SUPERSEDED — your 2026-07-03 on-demand-only call was revisited
  by your 2026-08-09 flow word and scheduling shipped as NL-146; see §5.
  On-demand `generate` is unchanged and still the manual path.)
- **Audio:** decided twice, both rulings live. The ear test ran 2026-07-06 and
  gpt-4o-mini-tts is your preferred VOICE ("I prefer the voice of the openai
  wav"); the $0-run law (2026-07-25) made Kokoro-82M local the code DEFAULT,
  because an unstated engine must fail cheap. Both sit behind the same
  `generate_audio()` wrapper; see "The voice" section above for switching.
