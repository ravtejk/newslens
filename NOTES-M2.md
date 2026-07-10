# Carryover notes (living file — current target: milestone 5)

## Resolved in milestone 2 (2026-07-03)

- ~~Finding 4: `briefings.date` GLOB format check~~ — done as
  `migrations/0002_briefings_date_format.sql` (triggers, not a table-rebuild
  CHECK — rationale in ADR-0003 §1).
- ~~Finding 5: "fetch-day = UTC day" stated explicitly~~ — done: binding
  contract block atop `src/newslens/ingest.py`, README ingestion section,
  ADR-0003 §5.
- ~~Finding 6: unused `Optional` import in doctor.py~~ — removed with the M2
  doctor changes.

## Still open

1. **[QA-owned, from M1 review finding 3] Pin the remaining unreadable-file
   paths:** unreadable `.env` (`doctor.load_effective_env`, dotenv AND
   fallback branches) and unreadable `prompts/doctor_sonar_ping.txt`
   (`doctor.check_perplexity_key`). Implementer must not write these —
   `tests/` is QA's.

## New carryovers for milestone 3

2. **[Still open — needs a week of real data] Wire-syndication flags on
   republishers are judgment calls.** Yahoo Finance / Investing.com / Whatfinger are wire-flagged in
   `sources.yaml` on documented-republisher grounds; after a week of real
   ingested items, check whether the flag over- or under-excludes for
   corroboration counting (per-source notes mark this).
3. ~~Does ranking need the Sonar answer text persisted?~~ Resolved in M3:
   **no** — ranking consumes sonar rows via title/url like any item (they can
   cluster but never count as "named outlets", ADR-0004 §5); the answer text
   stays report-only. Reopen only if M5 narrative quality shows a gap.
   Original question: M2 stores only
   `search_results` rows (title/url/date) and surfaces the answer text in the
   run report; if M3 ranking wants it as context, decide where it lives
   (NOT as a source_items excerpt — ADR-0003 §6).
4. **Sonar reliability spike still pending** — gated on `PERPLEXITY_API_KEY`;
   one command when granted: `scripts/sonar_spike`. A failed spike is a
   principal checkpoint (GNews fallback, cost change), never absorbed.
5. **Cross-feed same-URL attribution is last-writer-wins** (QA-pinned:
   `test_ingest.py::test_cross_feed_same_url_same_day_is_last_writer_wins`).
   A wire-flagged republisher fetched later overwrites the original outlet's
   attribution + wire flag for that day's snapshot. M3 corroboration counting
   needs a deliberate ruling here — do not inherit by accident. (QA obs. 1,
   2026-07-04.)
6. **Well-formed HTML at an rss_url is a permanent silent 0-item success** —
   doctor catches it, the ingest report never will. M3 candidate: flag sources
   that parse but never yield entries. (QA obs. 2.)
7. **Feed body size is unbounded** — items are capped at 20/feed, bytes are
   not; one `read(cap)` away from bounded. Low risk with curated feeds. (QA
   obs. 3.)

## Resolved in milestone 3 (2026-07-04)

- ~~Item 5: LWW same-URL attribution ruling~~ — ruled: keep LWW; corroboration
  counts stored outlets; both failure directions undercount (conservative for
  a trust label). ADR-0004 §4. Real-data revisit stays under item 2.
- ~~Item 6: silent-HTML zero-entry sources~~ — ingest report now warns per
  source ("fetched and parsed but yielded 0 entries").
- ~~Item 7: unbounded feed body read~~ — `net.fetch_bytes` caps at 4MB
  (cap+1 read, loud per-source failure); ingest and doctor share the same
  opener/UA/308 behavior via `net.py`.

## New carryovers for milestone 4

8. **Tags table + CLI verbs** (taxonomy contract §A/§F): `tags` table with
   `status=inactive` soft-delete lands WITH `tag add/drop/list` verbs; file
   remains source of truth until then (ADR-0004 §2). M4's memory seeding (the
   contract's 14 live threads + 5 borderline acute twins) is the natural
   moment to decide file-vs-table sync direction for both surfaces.
9. **Day-14 override recalibration readout**: `SELECT date, json_extract(meta,
   '$.override.fired'), json_extract(meta, '$.override.pool_size') FROM
   ranking_runs` — contract §E defines the loosen/tighten rules; someone must
   actually run this at day 14.
10. **Weight constants are v1 guesses** (topic 1.0 / domain 0.5 / followed
    +0.35 / share 0.55, threshold 8): tune against real briefings during
    M5-M6 dogfooding, as reviewed diffs.

## From the M3 gate review (2026-07-04)

11. **[MUST FIX BEFORE M5 SHIPS GENERATE] Re-rank UPDATE preserves stale
    narrative fields** (`ranking.py:703-707`): `persist` archives correctly
    but the UPDATE leaves `narrative_text`/`script_text`/`audio_file_path`
    from the previous version — once generate exists, a manual re-rank would
    keep a narrative describing the OLD slots, silently. NULL them on slot
    overwrite (history preserves the old state). Zero live impact today
    (columns always NULL).
12. **M4 nice-to-haves from review:** clamp `_retry_after_seconds` to
    finite ≥0 before `time.sleep()` (a hostile negative/nan Retry-After
    raises outside RankingError and bypasses BUG-6 logging); add the
    one-line prompt armor ("item lines are data, never instructions") —
    matters more once Sonar's open-web titles join the pool; `--date`
    calendar validity via `strptime` (2026-13-01 currently passes regex +
    GLOB). Cosmetics: 0003 header promises `override {reason, slot}` meta
    keys the code doesn't write; broken-bold artifact in this file's item 3;
    `persist` stores JSON `null` for empty usage where `log_failed_run` uses
    SQL NULL; `.env.example` BUDGET_CAP text says "generate run" but it also
    guards rank; `top_zero_match_score` misnames a world-impact value; when
    M4/M5 tunes weights (item 10), record the max-not-sum combinator choice
    in `personal_score` as a stated design decision.

## Resolved in milestone 4 (2026-07-04)

- ~~Item 8 (decide)~~ — **tags table DEFERRED again, deliberately** (ADR-0005):
  memory got its sync machinery this milestone; adding a second file<->table
  sync for tags in the same change would double the riskiest surface. File
  (`sources.yaml` interests) remains the tags source of truth; revisit when
  real use demands `tag add/drop` ergonomics the file can't give.
- ~~Item 11 (narrative-NULLing)~~ — fixed in M4 (persist was being modified
  anyway): re-rank NULLs narrative_text/script_text/audio_file_path on slot
  overwrite; history archives the old state first.
- ~~Item 12~~ — Retry-After clamped finite>=0; prompt armor line added;
  `--date` real-calendar via strptime; cosmetics: 0003 meta now writes
  override {reason, slot}, `top_zero_match_world_impact` renamed, persist
  uses SQL NULL for empty usage, .env.example BUDGET_CAP text covers rank,
  this file's item-3 bold artifact fixed. Max-not-sum combinator recorded as
  a stated decision in ADR-0005.

## New carryovers for milestone 5

13. **Continuity consumption**: `memory.prior_briefing_context(con, date)` is
    built and bounded — M5's generate prompt consumes it (a) verbatim active
    memory list with principal notes, (b) the prior-briefing text_block.
    Repeat-suppression ("don't re-cover unless developed") lands there too.
14. **memory.md checkpoint**: the principal opens memory.md and confirms it
    reads as genuinely editable — scheduled at the M4 boundary.

## Memory lifecycle v2 amendment (2026-07-04, pre-QA)

- Lifecycle replaced per principal contract: see ADR-0006 (three states,
  earned-slot auto-revival, Active/Inactive file, migration 0006 rebuild).
- On record from the same dispatch: migration 0005 principal-APPROVED;
  invented-ids repair extension DEFERRED (hard-reject + mitigation stand;
  recurrences are logged failures in ranking_runs).
- Fixed during amendment: memory verbs' trailing full-sync clobbered the
  verb's own change (fresh `add` was dismissed-by-deletion instantly; fresh
  note reverted) — trailing step is now render-only. QA: pin it.

## Must-address at M5 (from the M4 gate, 2026-07-04)

15. **`prior_briefing_context` returns None for corrupt story_slots JSON,
    indistinguishable from "no prior briefing"** — M5's generate must
    distinguish (warn on corrupt vs proceed on genuinely-first) rather than
    silently writing a continuity-free narrative.
16. Closed at the gate, on record: id-in-headline spoofing (brackets now
    sanitized out of titles in items_block); the ~90s memory.md clobber
    window (mtime-guarded refresh). Residual accepted: dismissed_user
    tombstones render in memory.md forever at personal scale — revisit only
    if the Inactive section becomes noise.

## Binding process change (from the M4 gate)

README currency is part of the implementer's definition of done: no
milestone report ships until README status/commands/data-model/module-list
match the tree (stale three gates running: M2, M3, M4).

## Resolved in milestone 5 (2026-07-05)

- ~~Item 13 (continuity consumption)~~ — generate consumes
  prior_briefing_context (delta-only callbacks, cap 2, mandatory
  revival/correction disclosures per content contract §5.3); repeat handling
  is delta-treatment by the writer.
- ~~Item 15 (corrupt-slots None ambiguity)~~ — generate distinguishes
  row-exists+unreadable (continuity SUSPENDED, loud warning, logged) from
  no-prior-row (first briefing). ADR-0007 §7.

## Milestone 6 record (2026-07-05) — the editor + parked audio infrastructure

**Parked by principal priority ruling (DECISIONS.md 2026-07-05): the audio/
podcast quality backlog** — 9 items recorded in the content contract's
"PRINCIPAL REVIEW ROUND 2" section (cold-open orientation, never-repeat
across open/headlines/story, sentence-rhythm variation, speech-not-prose,
contemporary transitions, editorial-judgment personality, one-idea density,
TTS-safe writing for the tics class, delivery pacing/emphasis) — plus the
ear-test re-run against a current script, the engine pick, and the
**4.4x-realtime-vs-14x-floor vendor question (principal's call at the ear
test)**. Infrastructure rides complete: both engines behind one seam, doctor
real-synthesis check with QA's two pinned skip-marker conditions,
`scripts/setup_tts` idempotent.

**M5 rides disposition:** LANDED this tree: `_outlet_token` The-prefix fix;
the sample no-row error hint ("generate the record first"). CARRIED:
corrections spoken-presence check (until a corrections pipeline exists —
`build_labels_block` still hardcodes "none this run"); hedge-coarseness
quality read (day-14); mechanism-depth quality read (day-14); tier-frequency
+ tolerance-frequency readouts (day-14, instrumentation live); keyless-refusal
log asymmetry; numeral {2,3} exemption; caveat-paraphrase double-render edge.

## New carryovers for milestone 7 (from the M6 gate review)

16. ~~DONE (M7)~~ **Budget-cap pre-check on the openai TTS call** (`audio.py`,
    `_synthesize_openai`) — the only spending path without one (~script_words
    /160 × $0.015 estimate vs remaining cap). Bounded today (~$0.08, no retry
    loop, non-default engine) but breaks the repo's cap discipline.
17. ~~DONE (M7)~~ **Pin `kokoro-onnx==0.5.0` in `scripts/setup_tts`** (ADR records 0.5.0;
    the runner's stats string hardcodes it; an unpinned future release breaks
    the isolated venv silently).
18. ~~DONE (M7)~~ **Editor forensics (gate nice-to-haves, cheap):** (a) draft-vs-edited
    hedge-word-ratio warn (mechanical tripwire for epistemic-qualifier
    deletion inside kept sentences); (b) persist the pre-edit draft JSON in
    the generation log entry so day-14 can attribute quality regressions to
    writer vs editor.
19. ~~DONE (M7)~~ **Audio hardening (gate nice-to-haves):** scrub `env=` on the kokoro
    subprocess (defense-in-depth — runner reads only argv); reuse
    `ranking._http_error_detail` for openai TTS errors; WAV params-consistency
    check across chunks; pass model paths to `tts_runner` as argv; model
    checksums in setup_tts.
20. **Framing-distribution read at day-14** (gate ruling: warn +
    instrumentation sufficient; the model could alternate two framings
    without tripping the >=3-same warn — the per-run `framings` log field is
    the readout).

20a. **[QA-owned, M7] Pin the calibration figure in-suite:** one assertion
     computing J of the chip-export fixture pair via `_sig_tokens` and
     asserting the exact value (0.667), so ADR-0009's cited figure is
     ENFORCED by the suite and can never drift from code again. Meta-lesson
     (gate, after the record accumulated four figures — 0.46, 0.583, 0.833,
     0.667 — three of them wrong): figures in decision records come from
     executable one-liners, not recollection or reconstruction.

## M7 backlog — recorded per dispatch, explicitly NOT built

21. **Date-treatment redesign** — v5's serif/small-caps edition-date block
    reverted to basic text by principal tweak; revisit as a design pass.
22. **Real NewsLens logo** — top bar carries a centered dashed PLACEHOLDER
    wordmark; principal designs the real mark.
23. **Masthead / splash entry moment** — design-addendum backlog item;
    design against habit-usage evidence, not before.
24. **Deeper-analysis story view** — headlines become tappable into a fuller
    surface; tap target marked in mockup comments only.
25. **Writer name→feed resolution** — the UI's type-a-name path is rendered
    but marked coming; paste-a-link is the functional path today.
26. **One-time pulse on a dot's first appearance** — motion considered and
    deferred; static dot shipped.

## Milestone 7 record (2026-07-06) — the web UI

**Landed this milestone:** `newslens serve` (stdlib, 127.0.0.1-only, one
server-rendered page); consumption_events via migration 0007 (reads raw,
listens deduped per-date-per-day; day-30 = trailing distinct open days);
sources.yaml line-surgery editors; shared thread verbs (CLI == UI);
follow-a-story seam; SOFT delete (dismissed-only, enforced in the shared
verb per the M7 gate ruling); single-flight generation job.
**Carryovers 16–19: LANDED AND GATE-VERIFIED** (openai-TTS cap pre-check
aborts before any call; kokoro-onnx==0.5.0 pinned + sha256 checksums,
bash-3.2-safe; hedge-ratio tripwire + draft_stories forensics; env-scrubbed
subprocess, shared error parser, WAV params check, argv model paths).
**Item 20a: LANDED (QA)** — calibration figure J=0.667 suite-enforced.
**M7 gate fixes (nine, CoS-applied per the enumerated-surface condition):**
problems-state validation + atomic replace in _yaml_edit; structural-char
name rejection; JSON-content-type CSRF guard on POSTs; read events only for
actually-rendered briefings; delete guarded dismissed-only in the verb;
settings shows the configured engine; revive-branch follow stamp;
preview_runtime/ gitignored; this record.
**Definition-of-done amendment (gate):** docs currency explicitly includes
NOTES-M2.md milestone records, not just README.

## New carryovers for milestone 8 (from the M7 gate)

21. **Day-30 readout caveats (must appear IN the readout):** (a) deflation —
    reading the emitted markdown artifact directly bypasses UI capture
    (UI-only capture was the design ruling; say so); (b) interpretation —
    single-page architecture makes "opened the app" == "briefing rendered",
    so open-days measures app-opens per ADR-0010 §3's own definition; (c) the
    2 disclosed synthetic reads on 2026-07-05 (implementer demo + CoS
    verification) are not principal reads.
22. ~~DONE (M8)~~ **Host-header allowlist** (belt-and-braces on the CSRF fix) at M8/preflight.
23. ~~DONE (M8)~~ **Error-panel claim edge:** "Nothing was published" is false in one case —
    artifact-write failure AFTER persist (the row IS published). One wording fix.
24. ~~DONE (M8)~~ **GEN_JOB BaseException stranding:** a BaseException in the job thread
    strands state at "running" until restart. Cheap guard at M8.
25. ~~DONE (M8)~~ **Unfollow eats an inline comment on the `enabled:` line** (write-side
    sibling of BUG-9's read-side fix — same tolerance needed when rewriting).
26. ~~DONE (M8)~~ **`_parse_narrative` dead branch** (server.py:145-148) — remove or exercise.
27. **Drift-guard suggestion (QA, optional):** a "furniture contract" test
    rendering a synthetic briefing through build_page asserting the code-owned
    furniture set (tracked marker, override note, meta-footnote, disclosure
    trigger, follow button with aria-pressed) — pins the trust surface against
    webui edits without pinning pixels. Plus dated-delta notes in webui.py's
    header pointing at DIRECTION-v3.
28. Carried M6 minors: corrections presence check (when the pipeline exists)
    — STILL CARRIED; ~~keyless-refusal log asymmetry; numeral {2,3}
    exemption; caveat-paraphrase double-render edge~~ DONE (backlog-minors
    batch 2026-07-06).

## Milestone 8 record (2026-07-05) — hardening, the readout, the human handoff

Construction's last implementer pass. **Rides 22-26 landed and live-verified:**
Host-header allowlist (DNS-rebinding belt; hostile Host -> 403 on GET+POST,
localhost names + absent-Host allowed); error-panel claim now true in both
failure positions ("No half-written edition ever goes out..."); GEN_JOB
finally-guard (synthetic KeyboardInterrupt in the job thread -> state lands
'error', never stranded 'running'); unfollow's enabled:-line rewrite is
key-anchored and preserves inline comments (BUG-9 write-side sibling,
verified with a commented canary entry); _parse_narrative dead branch removed
with the ordering it gestured at documented in place.

**`newslens diagnose` shipped** (src/newslens/diagnose.py): read-only/$0
readout — falsifier open-days with caveats 21a-c printed inline (21c
generalized to a construction cutover, ADR-0011 D2), generation record
(tiers/framings/override/editor stats/hedge warns/disclosure buckets/costs),
usage-window vs construction split. First real run against live data is in
the M8 report.

**Doctor ruling (ADR-0011 D3):** Perplexity absence = ○ informational
(deferred by choice); set-but-garbage still ✗. Exit 0 now reachable on the
real install. QA pins updated: PERPLEXITY_HINT in test_doctor_offline.py +
test_preinstall_doctor.py.

**PREFLIGHT.md shipped** (prototype root): human-engineer review guide —
spend paths table (4 call sites + cap checks), secrets flow, server surface
(binding/CSRF/host/XSS/audio-range/yaml-surgery), trust-machinery contracts
(corroboration + LWW undercount direction, tolerance disclosures, editor
two-lane, code-owned furniture), residual-risk table with ADR pointers,
verification commands. Every probe in it was executed before it was written
down.

**Item 27 (furniture-contract drift guard) remains QA's, optional. Item 28
minors carried, still open — recorded, not silently dropped.**

## Post-construction polish — P1 batch (2026-07-06; not a milestone)

**1. Glance restyle (server.py `_render_today`, webui.py):** "In today's
briefing" now renders in the ARCHIVE's visual grammar — `.archive-row` cards
(shared classes, no new vocabulary), serif headline line + soft topic-keyword
line derived from the SLOT (matched tags + memory, code-owned; fallback
"world-impact pick" mirrors the meta-footnote's language). Each row anchors
in-page to its story (`#story-i`; `scroll-margin-top` + smooth scroll with a
reduced-motion opt-out).
**2. Ongoing recency order (server.py `_following_rows`):** active threads
sort by last-picked-up date desc; never-picked-up sink to the end; stable
within ties. Display-order only — lifecycle untouched.
**3. Splash logo (webui.py):** logo placeholder opens large
(`body.splash`, 2.1rem) and shrinks to the masthead size at >24px scroll;
idempotent passive scroll listener, both directions; transitions honor
`prefers-reduced-motion`; NO JS -> class never applied -> static masthead
size (degradation by construction). Dashed border kept in both states (the
placeholder marker leaves only with the real logo, P4). Interpretation note:
the top bar is NOT sticky — that's the P4 masthead decision; the shrink
plays while the bar is still in view at small scroll offsets.
**4. Politico feed:** investigated the 404 — upstream-transient, now healed.
The recorded URL (rss.politico.com/politics-news.xml) answers 200 with valid
RSS ("Politics" channel) through curl, plain urllib with the doctor's UA, and
net.OPENER. No sources.yaml change made (swapping a correct URL against a
transient would be churn). Side effect: **doctor exit 0 — first in project
history** (0 required failing · 1 warning · 49 passing).
**Verification discipline:** items 1-3 verified by in-process `build_page`
rendering + suite — zero consumption events generated (the day-30 window
opens 2026-07-07; the events table stayed at 11 construction rows throughout).

## M9 "the Analyst" — milestone 1 record (2026-07-06): the fetcher

**Landed:** `src/newslens/analysis.py` — the retrieval leg under the
principal's four binding boundaries (tier-scoped {full,cautious} with
socketless tier-excluded records; robots.txt honored per-host-per-run-cached,
404=allow / unreachable-or-4xx-5xx=conservative-deny per RFC 9309;
attributed FetchRecords for every URL; sequential polite-delay pacing, one
attempt per URL, byte-capped through net.fetch_bytes). Stdlib extraction
(Pax's ruling): first-title capture, <article>-scope-then-paragraphs chooser,
script/style/svg dropped WITH content, chrome (nav/header/footer/aside/form)
excluded from prose, paywall-marker + length-floor + link-density
classification into the closed outcome vocabulary (ok / robots-denied /
paywall-suspected / empty / error / tier-excluded). `fetch_stats()` is the
week-1 readout seed (success rate over ATTEMPTED only — policy exclusions
never dilute the <30% dep trigger).
**Cap change landed:** BUDGET_CAP_USD_PER_RUN default 0.50 -> 0.25
(config.py with the degradation-ladder ordering + escalation condition in
the docstring; .env.example; README; SETUP; doctor now WARNs on a cap pinned
above the recommended default). **The principal's own .env still pins 0.50 —
flagged to the CoS; agents never edit .env.**
**Fixtures:** tests/fixtures/analysis/ — clean_article.html is a REAL saved
page (The Hill NATO-summit story, fetched by the disclosed live probe);
paywall/js-shell/hostile are synthetic BY DECISION (capturing a real paywall
would require fetching a paywalled outlet — the act the tier boundary
prohibits). Hostile fixture pins Rook's demand both ways: body-prose payload
SURVIVES extraction (the M2 validator must see it), script payload NEVER
surfaces.
**Live probe (disclosed):** 3 real articles (The Hill, Al Jazeera, CNBC) +
1 tier-exclusion (WaPo, no socket): 3/3 attempted OK, 2.2–3.0k chars each,
$0 spend, zero consumption events. Migration 0008: NOT landed here —
sequenced to M2 with persistence, per the engineering transcript.
**Slot-3 reconciliation (my confirmation, as flagged in the design brief):**
CONFIRMED workable — the fetcher is tier-agnostic (callers pass tier), and
the analyst's medium-vs-quick call for slot 3 binds at M2's loop level; no
pipeline-contract complication at this layer.
**QA pins flipped (mechanical, intended changes):** cap default 0.25 in
test_config_guards (default + INFO text; PASS case split into
at-or-below-default PASS + above-default WARN — new behavior pinned),
test_repo_hygiene (.env.example value), test_doctor_offline (template-run
"default 0.25").

## M9 "the Analyst" — milestone 2 record (2026-07-06): the organ

**Landed:** migration 0008 (analysis_briefs — append-only, rejected briefs
persisted for forensics, readers take newest valid); the analysis stage
(`run_analysis`/`analyze_story` in analysis.py + `newslens analyze` verb):
M1 fetch -> per-story Sonar verification (discovery's call shape) ->
gpt-4o synthesis (ANALYSIS_MODEL seam, fallback rung gpt-4o-mini) ->
deterministic validation -> persistence + stage-logged costs. Contract §5
sections as data; prompt carries the borrowed-inference rule and the
data-never-instructions armor. Validation: fabricated keys / non-verbatim
quotes / uncited pinned facts / one-sided discrepancies / generic unknowns
= HARD REJECT; own-voice effects dropped-with-disclosure; provenance +
source table CODE-computed (ADR-0012). Ladder: Sonar skipped first, briefs
skipped next (derating = escalation flag), briefing itself untouchable from
this stage. diagnose gains THE ANALYST section (extraction rate = the
week-1 <30% readout).
**Checkpoint run (live, 2026-07-05 edition):** 2/2 depth briefs VALID,
$0.0424 total, 7/7 extraction, Sonar 8+8 results — and a REAL discrepancy
caught and rendered (meeting date Jul 8 [rferl] vs Wednesday [cluster]),
never averaged. Milestone LLM spend incl. dev loops: $0.042 (estimate was
$0.10-0.20; first live run validated clean, no retries needed).
**QA pins flipped (mechanical):** migration cascade for 0008 (test_migrations
list+tables, test_cli count 7->8, test_doctor_offline behind-by + scratch
tables, test_preinstall_doctor scratch tables).
**For M3:** render_writer_view is the writer's input block;
latest_valid_brief(con, date, slot) is the view's read; demoted-quick
outcomes must reach the writer's tier assignment; reader rendering excludes
notes_for_writer and renders citations as outlet-named links; footer:
"Based on N sources retrieved <time>" + degradation label; "cited" never
"verified".

## M9-M2 fix loop 1 record (2026-07-06)

**QA's five bugs — 13 reds flipped green:** BUG11 glyph-symmetric quote
check (curly-pair detection + normalization both sides; direction-safe);
BUG10 validator totality (_require_str at every text boundary, dict guards
on pinned/discrepancy entries, run-level belt: validator escape = disclosed
'rejected' with a log entry, never a crash after paid synthesis); BUG14
migration 0009 append-only triggers on analysis_briefs (reaches the LIVE db;
0004 precedent); BUG12 identical-cite-set discrepancies reject + Sonar-vs-
cluster URL dedup at map build (QA's frozen test CONSCIOUSLY FLIPPED to pin
the new behavior, per its own docstring); BUG13 both paid attempts' costs
accumulate into the returned cost.
**Editor's five (their report quoted in the diffs):** officeholder fidelity
prompt rule (F1; deterministic lint SKIPPED as fuzzy — needs entity
extraction, per the don't-force-it clause); same-referent date
normalization code-owned (F2: weekday==calendar-date within ±10d of the
edition drops the entry, disclosed); attributed-take recency (G3:
take_date field + validator re-basis to historical-pattern-with-date when
>7d older than the edition); basis lint (F4: modal text under mechanical →
borrowed-inference drop path); arc integrity (item 10: arc citing no P-key
while one exists is dropped, disclosed — the delta feeds the writer
mechanically).
**Receipts persist (item 11):** analysis_retrieval table (0009), rows
written with every brief (valid AND rejected); live: 23 rows / 40KB for
2 briefs — ~1-3MB/month. Hand-traces re-check stored text, never re-fetch.
**Passing fix (disclosed):** report status now ok/partial/failed
(demoted-quick counts as a decision); was 'ok' even when all stories failed.
**Live re-run ($0.0435):** F1 fixed in the wild ("President Donald Trump",
source wording); arc cites P1 with a named delta; take_dates present.
HONEST RESIDUE: slot-2's 188k-vs-206k discrepancy (G2 tranche class)
persists — the prompt rule alone didn't stop it and the deterministic
same-referent rule covers dates, not period-figures; Editor's hand-trace
lane catches it meanwhile; if the week shows recurrence, ledger sides may
need their own dated-referent field (day-14 item, on record).

## M9 "the Analyst" — milestone 3 record (2026-07-06): writer integration + the deep view

**Writer-from-brief (generate.py):** analysis stage runs inside generate on
record-refreshing runs (one cap governs: analysis spends first, cheapest-
degrades-first ladder intact; stage-wide failure = disclosed excerpt
degrade). Briefed depth stories' material = render_writer_view (trace-
don't-generate directive + notes_for_writer; excerpts dropped, titles kept
as context); unbriefed depth stories keep excerpts + a disclosure line +
the artifact meta-line ladder label ("Analysis: unavailable — built from
feed excerpts"). THE SLOT-3 RECONCILIATION NOW BINDS: the analyst's verdict
(valid brief = medium, demoted-quick = quick) pins the writer's A2 tier
validation; the model chooses only when no verdict exists (fallback rung).
**The deep view (server.py/webui.py):** v6-as-edited rendered faithfully —
entry affordance "→ The full picture" in the trailing stack with the three
binding states (degraded-hidden == absent, Axel's ruling); 8 sections;
trailing-qualifier provenance grammar; discrepancies as two paired lines
ending "unresolved"; three-beat unknowns; source rows with real accessible
names; deterministic footer with the cited-not-verified paragraph;
openDeepView/closeDeepView with lastStoryAnchor scroll restore (binding).
Views render FROM persisted rows (never regenerate), newest-valid-wins,
date-addressed (archive editions carry their files on their own page).
**Axel instrumentation:** generate log gains deep_views per depth slot +
analysis_usd; diagnose prints per-day availability. Deep-view opens are
client-side view switches — NO new consumption event kind (concluded, not
built: day-30 semantics ruled on app-opens; an in-page switch is within an
already-counted open).
**v6 deviation (disclosed):** content §5.3's "opens with one line stating
what the view adds" — v6-as-approved opens with eyebrow+title+jumplist and
no adds-line; v6 wins as the later approval.
**Live proof (2026-07-06 edition):** 3/3 valid briefs ($0.0665), writer
stage $0.0548 — run total $0.121 under the $0.25 cap; tiers [full, medium,
medium] with slot-3 ruled BY THE ANALYST (first live exercise of the
reconciliation); page renders 3 entry affordances + 3 files; consumption
events untouched at 11 (all verification in-process).

## M9-M3 gate batch record (2026-07-06)

**Gate 1a — numeral trace-check (warn-grade), PRE-REGISTERED ESCALATION:**
trace_check_numerals warns on story numerals outside the briefed slot's
universe (writer view + cluster titles + title/summary). WARN not reject —
derived numerals ("doubled", "up 4%") legitimately compute from brief
figures (§5.9 #7 logic). ESCALATION, pre-registered: if a week of warns
shows genuine invented specifics rather than arithmetic noise, reject-grade
returns as a PRINCIPAL CHECKPOINT — never silently tightened.
**Gate 1b:** editor receives the briefed slots' fact universe
(build_analysis_facts_block) + the one authorized constraint line in
editor_pass.txt: a specific not present is a fabrication to CUT.
**Gate 2:** slot-3 verdicts persist (demotions as rejected verdict rows,
reject_reason 'demoted-quick: ...') and analyst_slot3_tier() is the ONE
derivation path both refresh modes use.
**Gate 3 render batch:** BUG16 (one provenance path — _cite_qualifier
derives via compute_prov_display when uncalled-with-provenance); v6 bare
"(via Outlet)" effects grammar; no dead Arc anchor on arc-less briefs.
**Consciously flipped pins (each per the gate's own order):** double-via
freeze, arc-anchor-gap freeze, demotion no-row clause (verdict row now
required), and the generate-wiring pin's verdict expectation (fake
per_story without persistence = no verdict under the one-path ruling).

## P3 record (2026-07-06) — podcast quality: the ROUND 2 nine

**Prompt (script_adapt.txt):** items 1 (cold-open orient, 2-3 sentences,
never pre-play), 2 (never-repeat across open/menu/body), 3 (rhythm), 4
(speech-not-prose: contractions, natural possessives, written-register ban),
6 (editorial-judgment license within A1), 7 (one-idea-per-ear), 9 (breath
points, structural emphasis). Items 5 (transitions) already present (A4).
**Code (generate.py):** tts_safe_pass (item 8) — enumerated deterministic
transforms (X+ -> "X plus", $NT -> "N trillion dollars", bare $ -> dollars,
even thousands/millions spoken, year ranges, percent), idempotent, applied
AFTER validation (form never facts), disclosed per class; wired at the
script acceptance site (grep: generate.py:1498 region). validate_script
gains warn-grade P3 detectors: never-repeat (6-gram cold-open overlap),
rhythm (3 consecutive 25+-word sentences), written register (latter/former/
aforementioned/respectively/semicolons). Liveness red proves the wiring
(test_LIVENESS_tts_safe_pass_reaches_the_persisted_script).
**Live run:** all machinery fired on today's edition — transforms landed in
the persisted script, never-repeat detector caught the model's remaining
cold-open repetition, banned-strings caught "remains to be seen". Honest
state: model compliance with the new prompt rules is PARTIAL on n=1 (cold
open still heavy) — warn-documented, ear-test decides significance.
**Ear test staged:** 2026-07-06-eartest-{kokoro,openai}.wav + NOTES (costs:
kokoro $0/4.8min/65s, openai $0.067/4.4min/50s; 4.4x-vs-14x note included —
principal's ruling). Milestone spend: $0.061 (regen) + $0.067 (openai TTS)
= $0.128.

## Backlog-minors batch record (2026-07-06)

**1. Tap-outside-popup dismisses** (webui.py, shared component, single-
pattern rule): scrim tap routes through ONE dismissPopup(); the binding
nuance implemented as DIRTY = NO-OP (judgment, disclosed: mis-taps are
common on mobile, Cancel stays one tap away, and no-op is the least
destructive reading of "never eat unsaved text silently"). Dirty = any
field differing from its OPENING snapshot (dataset.initialValue set in
openPopup — prefilled edit-note text is clean until touched). Escape
parity: Escape now uses the same guarded path (it previously ate typed
text unconditionally — the exact bug class).
**2. Topic-add autofill** (server.py): native datalist (no custom widget;
no-JS/no-support degrades to the plain input, Enter flow unchanged);
vocabulary = current interests + every coverage-matched tag name from
persisted slots (_topic_vocabulary).
**3. Writer-add autofill, v1-scoped** (server.py): datalist over RECALL
only — followed analysts + writer-shaped sources, both display forms
offered; resolution stays P4; paste-a-link untouched.
**4. NOTES 28 minors:** 28a keyless refusal logs symmetrically (check moved
inside the logged region; liveness pin); 28b the {2,3} exemption became
principled (enumeration numerals up to the story count; old pin flipped +
slot-bounded pin added); 28c caveat paraphrases in the outro are REMOVED
before the frozen append (stem-match >=3 of outlet/wire/source/count/truth;
disclosed; never two caveats).
**5. Sweep:** Topics/Writers hint copy updated to describe the suggestions
honestly.

## P3.1 record (2026-07-07/09) — editorial enforcement (principal rulings 2026-07-06)

The principal's first full real-use review (morning read + ear test,
DECISIONS.md "principal rulings batch" 2026-07-06) as hard gates. Build
interrupted mid-run 2026-07-07 (credits outage); resumed and finished
2026-07-09 on the frozen Fable line (this tree — DECISIONS.md 2026-07-09).

**Items 1+2 — the spoken editorial bar, hard-with-retry (generate.py):**
`script_structural_check` — cold-open cap (<=3 sentences / ~50 words +60
slack before the dateline anchor, no story pre-play) + cross-section
repetition (>=3 distinct shared 6-grams between any two 15+-word
paragraphs; reports capped at 3). Violations trigger ONE script retry with
the violations injected verbatim; a still-violating retry ships the better
attempt WITH disclosure — never silent, never a dead run, never a second
retry. Retry cost pre-checked against the remaining cap (skip = disclosed).
The old warn-grade never-repeat detector REMOVED from validate_script
(promoted, not duplicated). CALIBRATION pinned as fixtures: the verbatim
2026-07-06 script that shipped to the principal's ears is CAUGHT (7-sentence
/97-word cold open + 3 retell pairs — the exact violations he named); a
legitimately edited script of the same edition passes CLEAN
(tests/fixtures/script/2026-07-06-{repetitive,legitimate}.txt).

**Item 3 — lead tier floor (generate.py + prompts/editor_pass.txt):**
LEAD_FLOOR_WORDS=300 (derivation at the constant: 900-1300 lead-weighted
total with A2's 250-550 lead band; a valid <=700-word cited lead brief
removes the thin-material excuse). Binds ONLY when a valid slot-1 analysis
brief exists; severity = warn-shaped hard-with-retry (one narrative retry
with the deficiency injected; improvement adopted; a second miss ships
with disclosure — the briefing always ships). The M6 editor's cut power
gains a floor: an edit cutting a floor-meeting briefed lead below 300 is
DISCARDED through the existing degrade path, disclosed. editor_pass.txt
carries the one authorized tier-floor constraint line.

**Item 4 — TTS default flip (config.py / audio.py / doctor.py / docs):**
DEFAULT_TTS_ENGINE + config default + settings fallback kokoro -> openai
(gpt-4o-mini-tts; ear-test ruling: "I prefer the voice of the openai wav";
~$0.015/min, ~+$0.07/run). Kokoro stays fully built as the $0 fallback;
4.4x re-open moot while not default. Doctor: pinned-kokoro draws the
cap-change-pattern WARN nudge (recommended default + ruling cite + "your
pin wins" — the doctor NEVER edits sources.yaml; the principal's own file
pins kokoro and stays untouched). README/SETUP/.env.example currency done.

**Suite: 877 -> 893 passing.** New: tests/test_p31_enforcement.py
(implementer-written; calibration, boundary units, LIVENESS reds proven to
fail with the wiring stubbed, offline spend-proofs: each retry path fires
at most once and a cap-breaching retry estimate is skipped with
disclosure, $0). Pin flips, all mechanical cascades of the rulings,
each commented at the site: test_p3_script.py never-repeat warn ->
promotion pin (implementer-authored, flipped by its author);
tts-default cascades in test_audio.py (x3), test_doctor_offline.py
(exit-0 fixture pins kokoro to keep the synth-skip machinery exercised),
test_server.py (engine display poles swapped), test_generate.py
(tts_openai step/warning). **4 stale-by-design QA-owned pins left RED for
QA's ordered pass** (never weakened by the implementer): test_p3_qa.py x3
(pin the retired warn-grade never-repeat channel: proper-noun FP,
dateline-anchor limitation, all-three-warn-grade — each concern transfers
to script_structural_check and needs QA's re-characterization, note the
new detector's paragraph-pair shape narrows the proper-noun FP class) +
test_m3_qa.py::test_editor_receives_the_analysis_fact_universe_block
(indexes the editor prompt as json_calls[1]; a tier-floor narrative retry
is json-mode and shifts the editor to [2] — the fact-universe block itself
is intact; re-index by prompt marker, not position).
