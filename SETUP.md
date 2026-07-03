# NewsLens setup — clone to first doctor pass

Goal: everything on this page takes minutes, and `scripts/doctor` tells you
your exact remaining steps at every point. When the doctor exits `0`, you're
done with setup.

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
   under $1/month; audio's v1 default is Kokoro-82M running locally at no API
   cost, with gpt-4o-mini-tts on this key as the built fallback — ~$5.40/month
   at daily cadence only if the fallback wins the milestone-6 listening test).
3. Put the key in `.env` as `OPENAI_API_KEY=...`

### 2b. PERPLEXITY_API_KEY (required — daily discovery query)

1. Go to <https://www.perplexity.ai/settings/api> → generate an API key.
2. Sonar is pay-as-you-go against a **prepaid credit balance — that balance is
   your real spend cap**; load the minimum (e.g. $5 — expected spend is cents
   per month). `BUDGET_CAP_USD_PER_RUN` in `.env` is only a secondary,
   in-app guard.
3. Put the key in `.env` as `PERPLEXITY_API_KEY=...`

### 2c. Everything else in .env

- `BUDGET_CAP_USD_PER_RUN` — leave the 0.50 default unless you have a reason.
- `GENERATE_HOUR_LOCAL` — set to the hour you actually want the briefing
  (used at milestone 7 when scheduling lands).
- `GNEWS_API_KEY` — **leave blank.** Deliberately ungranted fallback; only
  becomes relevant if the Sonar reliability spike fails, and that would come
  back to you as a checkpoint first.

## 3. Add your outlets and interests

Open `sources.yaml`. It ships with zero active sources on purpose — the
template comments show the exact format, with three real example feeds you can
uncomment to try. Add roughly 8–12 outlets you actually read, and set
`wire_syndication: true` on any wire service (AP/Reuters/AFP-style) so
corroboration counts stay honest. Then add a few `interests` tags — broad ones
steer ranking, granular ones sharpen it and shape the daily discovery query.

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
✗ PERPLEXITY_API_KEY not set — get one at perplexity.ai/settings/api, then add to .env
✓ migrations apply cleanly to a scratch DB — tables: briefings, briefings_history, memory, source_items
⚠ sources.yaml has no active sources — uncomment or add your outlets (...)
```

That's the designed experience: nothing crashes, every gap names its fix.

## Troubleshooting

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

## Later milestones (placeholders, so this file has one home)

- **Scheduling (M7):** launchd plist wiring for `GENERATE_HOUR_LOCAL`, plus
  `read`/`listen` commands whose usage log feeds the day-30 verdict.
- **Audio (M6):** v1 default is Kokoro-82M local TTS (no key; ~$0.10/month
  total run cost), with gpt-4o-mini-tts built in as the hosted fallback
  (~$5.50/month total). Both sit behind the same `generate_audio()` wrapper;
  you pick by ear at the milestone-6 listening test — setup steps land here
  then (per `workspace/debates/2026-07-02--newslens--engineering-2.md`).
