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
   under $1/month for text; audio's default is gpt-4o-mini-tts on this key —
   your ear-test pick, 2026-07-06 — at ~$0.015/min, ~+$0.07/run, roughly
   ~$2/month at daily cadence; Kokoro-82M local remains the $0 fallback).
3. Put the key in `.env` as `OPENAI_API_KEY=...`

### 2b. PERPLEXITY_API_KEY (optional — deferred by choice, 2026-07-05)

You deferred this key: ingest runs RSS-only and says so on every run, and
the doctor reports the absence as informational (○), not failing — the
product's actual running state. If you want the daily discovery query later:

1. Go to <https://www.perplexity.ai/settings/api> → generate an API key.
2. Sonar is pay-as-you-go against a **prepaid credit balance — that balance is
   your real spend cap**; load the minimum (e.g. $5 — expected spend is cents
   per month). `BUDGET_CAP_USD_PER_RUN` in `.env` is only a secondary,
   in-app guard.
3. Put the key in `.env` as `PERPLEXITY_API_KEY=...`

### 2c. Everything else in .env

- `BUDGET_CAP_USD_PER_RUN` — leave the 0.25 default unless you have a reason
  (recommended value cut from 0.50 with the M9 Analyst ruling, 2026-07-06 —
  if your .env still pins 0.50, lower it to match).
- `GENERATE_HOUR_LOCAL` — **dormant**: v1 generates on-demand only (your
  2026-07-03 call), nothing reads this. Leave it or delete it; the doctor
  treats it as informational either way.
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
⚠ sources.yaml validation skipped (PyYAML not installed — see the missing-deps line above)
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

## The voice — engine choice + optional local TTS setup

The default voice is **gpt-4o-mini-tts** on your OpenAI key (~$0.015/min,
~+$0.07/run) — your ear-test pick, ruling 2026-07-06. Nothing to install;
the audio lands next to each briefing: `data/briefings/<date>.wav`.

The $0 local fallback (Kokoro-82M) stays fully built. To use it, pin
`settings.tts_engine: kokoro` in sources.yaml (the doctor will note the pin
vs. the recommended default) and run the one-time setup:

```bash
scripts/setup_tts   # brew python@3.12 + isolated engine venv + ~340MB model
scripts/doctor      # its TTS section runs a REAL short synthesis (kokoro)
```

## Later milestones (placeholders, so this file has one home)

- **On-demand trigger + instrumentation (M7):** `generate` stays manual (v1 is
  on-demand only, your 2026-07-03 call — no cron/launchd), plus the
  `read`/`listen` commands whose usage log feeds the day-30 verdict.
- **Audio:** decided — the ear test ran 2026-07-06 and gpt-4o-mini-tts is the
  default (your ruling: "I prefer the voice of the openai wav"); Kokoro-82M
  local is the built $0 fallback. Both sit behind the same `generate_audio()`
  wrapper; see "The voice" section above for switching.
