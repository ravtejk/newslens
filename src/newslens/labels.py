"""User-facing section/surface labels — ONE string table.

Every user-visible section name and surface label the v7 shell renders is read
from THIS module at render time (attribute access, not a captured constant), so
a naming re-pin lands in one place and every surface follows. The v7-M2 build
(NL-29 consolidation + the adjacent-copy slate) widened this table to cover the
Following spine, the archive calendar, the thread page, the deep-back labels,
and the follow-control copy the client JS renders (injected as NL_LABELS).

Wiring proof: newslens.server reads these as `labels.<NAME>` at call time; the
client reads the JS-facing subset from a server-rendered `window.NL_LABELS`
blob (server.NL_JS_LABELS). A monkeypatch of any constant appears in rendered
output (see test_v7_shell_m1's label-liveness tests + test_v7_m2's global-
absence asserts — the red tests only the wiring can flip).

Stdlib-only by design (see newslens/__init__.py). No f-strings, no logic — a
table, deliberately boring so the re-pin is a one-line diff per name.

RETIRED-NOT-RENDERED (the sweep marker, NL-103 row 21): a constant whose string
no surface renders any more is kept — so nothing imports a dangling name — and
carries that exact marker on its own line. A copy/vocabulary sweep greps the
marker and SKIPS those strings: they are record-keeping, not live copy, and
reading them as live is how a dead phrase gets "fixed" into a ruling it no
longer belongs to. Live constants never carry the marker.
"""

# --- Nav destinations (the section line: Today · Following · Archive) --------
NAV_TODAY = "Today"
NAV_FOLLOWING = "Following"
NAV_ARCHIVE = "Archive"

# --- Today front-page furniture ---------------------------------------------
# NL-68 item 6: the visible "The Lead" kicker DIED — the design carries the
# hierarchy (largest type, top-left). The constant is retired-but-kept so nothing
# imports a dangling name; no surface renders it (grep server.py for KICKER_LEAD).
KICKER_LEAD = "The Lead"          # RETIRED-NOT-RENDERED (NL-68 item 6)
IN_BRIEF = "In brief"             # the quick-tier cluster heading

# --- The edition bar (§6 — the podcast player is edition-level furniture) -----
LISTEN_TO_EDITION = "Listen to the edition"

# --- The deep-view entry affordances (NL-65 splits their PLACEMENT, not text) -
FULL_PICTURE = "The full picture"      # analyst-tier deep-view entry
SOURCES_CONTEXT = "Sources & context"  # In-Brief (quick-tier) $0 entry

# --- Deep-view section labels (NL-29 consolidation slate, DECISIONS 2026-07-14:
#     "NL-29 RULED: the consolidation slate" — deep view goes 7 sections -> 5) --
DEEP_FACTS = "The facts"                    # numeric specifics FOLD IN here (Merge 2)
DEEP_MECHANISM = "How this works"           # WAS "Mechanism" (principal pick; veto open)
DEEP_EFFECTS = "What could follow"
DEEP_OPEN = "What’s still open"             # discrepancies FOLD IN here (Merge 1)
DEEP_SOURCES = "Sources"
DEEP_WHY_SEEING = "Why you’re seeing this"  # sources-context view (gate FIX-2, v7-M1)
DEEP_EYEBROW = "The full picture"           # the deep-view eyebrow (same words as the entry)

# --- Deep-view jumplist short labels (where they differ from the section head) -
JUMP_FACTS = "Facts"
JUMP_OPEN = "Still open"
# NL-68 item 5: the collapsed discrepancy sub-group's summary (count-bearing).
DISCREPANCY_FOLD = "points where the sources disagree"
DISCREPANCY_FOLD_ONE = "point where the sources disagree"

# The Today card's continuity stamp — his 07-25 ruling ⑤: moved-since-your-last-
# read is the single word, in the prose register (the Following spine keeps its
# own mono all-caps UPDATED stamp; the word follows its class register, §3
# casing law, and the two are different classes on different surfaces).
MEMLINE_UPDATED = "Updated"

# --- Memory surfaces (thread page + deep-view memory sections) ---------------
WHERE_THIS_STANDS = "Where this stands"
THE_STORY_SO_FAR = "The story so far"
# NL-77 the cold-start backgrounder (entry-zero baseline) — a permanent section
# between "Where this stands" and "The story so far". Its content is external
# synthesis (background NewsLens never itself covered), always disclosed as such.
HOW_WE_GOT_HERE = "How we got here"
BASELINE_DISCLOSURE = ("Founding background — researched context NewsLens did "
                       "not itself cover, not part of our record.")
BASELINE_PENDING = ("Preparing the background for this new thread — check back "
                    "shortly.")

# --- The thread page (the "Open thread" destination, v7-M2) -------------------
# The thread NAME is the single action on a Following row (Design's ruling,
# extends the §12.5 fold grammar); "→ The whole thread" is the fallback control
# label for that action (its accessible/link purpose + the row's control name).
THREAD_WHOLE = "→ The whole thread"
# NL-103 row 17: the VISIBLE back label is the bare destination (mockup form);
# the accessible name ("Back to Following") is derived at render time by
# server._back_link — §3 aria law: it names the destination and CONTAINS the
# visible label (WCAG 2.5.3).
THREAD_BACK = "← Following"                # the thread page's back affordance
# NOTE: DECISIONS 2026-07-14 lists an "open question / next fixed point" on the
# thread page, but no thread-persisted field carries it (it lives per-edition in
# a brief's watch/unknowns, not in thread_state/thread_deltas/memory). Per A8
# no-fabrication + the dispatch's "renders from thread_state/thread_deltas/memory
# ONLY / do NOT invent fields", it is rendered by HONEST ABSENCE — those labels
# arrive when a thread-level field does (flagged for NL-68).
THREAD_NO_ARC = "This thread is new — no earlier coverage yet."  # day-one empty state
THREAD_NO_STATE = "No standing summary yet — the thread hasn’t been rewritten."
THREAD_NO_LEDGER = "No dated entries yet."
THREAD_EDITIONS_LABEL = "In these editions"  # the edition back-links group label

# (Arc verdict labels ARC_ADVANCES/REVERSES/MATCHES removed with the arc-line
# batch, 2026-07-18: the deep-view arc no longer renders a derived verdict from
# brief['arc'] — it renders the memory pass's authored thread_state.arc_line
# verbatim. No other surface consumed these labels.)

# --- Still-tracking strip (Today surface; retro-mock idiom) -------------------
# Composed as: "Still tracking {thread} — {note}. {fixed_point}."
STILL_TRACKING_PREFIX = "Still tracking"
STILL_TRACKING_NO_DATE = "No next date is set."

# --- The why-chosen provenance line (NL-134 F3; folds NL-117) -----------------
# THE PRINCIPAL'S DISPLAY SPEC, 2026-08-02, verbatim: "The reason for the story
# should be just be displayed as 'Chosen because:' or 'Related to:' and then
# '{relevant topics the user follows} or Important World News.'" It rides every
# front-page story on every tier and REPLACES the old override note, which
# rendered the ranker's full prose reason (twice — the F1 bug).
#
# NL-138 (his ruling ④, later the same day) widened these constants' reach:
# with the ranker's prose reason dead pipeline-wide, WHY_CHOSEN_BECAUSE +
# WHY_WORLD_NEWS are now THE override vocabulary everywhere, not only on the
# front page — generate.OVERRIDE_TEXT_LABEL (the markdown edition's §5.7 line),
# the spoken-disclosure check in generate.validate_script, moat_battery's
# conformance render, and the rank CLI all compose from here. "just" is
# load-bearing in both directions now: one phrasing, one place, and no surface
# says the same thing a second way.
WHY_RELATED_TO = "Related to:"          # the reader follows the named things
WHY_CHOSEN_BECAUSE = "Chosen because:"  # nothing followed matched
WHY_WORLD_NEWS = "Important World News"
WHY_FOLLOWED_WRITER = "a writer you follow"   # composed as "<outlet> (…)" when
                                              # the outlet name resolves, and
                                              # rendered bare when it does not
# RETIRED-NOT-RENDERED (NL-138, ruling ④ 2026-08-02). This labeled the deep
# view's full-prose-reason paragraph for the few hours between the NL-134 land
# and the ruling that killed the sentence it labeled. No surface renders it;
# kept per this module's header rule so nothing imports a dangling name, and a
# copy sweep must SKIP it — record-keeping, not live copy.
WHY_FULL_REASON = "The full reason it was included:"

# --- Following — the Spine (§7/§12.2/§12.5) ----------------------------------
FOLLOWING_TRIAD_THREADS = "Threads"    # WAS the switcher's "Ongoing stories"
FOLLOWING_TRIAD_TOPICS = "Topics"
FOLLOWING_TRIAD_WRITERS = "Writers"
# The ●UPDATED movement stamp (UPDATED — reaffirmed twice; ADVANCED is dead).
UPDATED_DOT = "●"
UPDATED_STAMP = "UPDATED"
UPDATED_THIS_EDITION = "THIS EDITION"
LAST_UPDATED = "LAST UPDATED"
# v8-M1 item 5 (2026-07-17): an empty thread (no state/deltas/baseline) has no
# content date — its only honest date is when the follow was created.
FOLLOWED = "FOLLOWED"
# The counted quiet-fold (§12.5): "{n} quiet threads · no movement this edition".
QUIET_FOLD_NOUN = "quiet threads"
QUIET_FOLD_NOUN_ONE = "quiet thread"
QUIET_FOLD_SUFFIX = "no movement this edition"
# Lifecycle sections below the active spine (status != active).
FOLLOWING_DORMANT_H = "Quiet for now"
FOLLOWING_DISMISSED_H = "You stopped following"
# NL-103 row 20: the Threads sub-view's empty state names its own class. The
# nearest heading is the page-title "Following" (h1), but the triad nav and the
# follow-a-story combobox sit between them and THREE sub-views share that h1 —
# no programmatic adjacency, so the class noun rides in-string (§3 empty-state
# rule).
FOLLOWING_EMPTY = "No threads yet"
# Row verbs (the thread editor / lifecycle controls).
VERB_STOP = "Stop"
VERB_RESUME = "Resume"
VERB_DELETE = "Delete"
VERB_EDIT_NOTE = "Edit note"

# --- Follow control — THE ONE FOLLOW-LINE COMPONENT (NL-17-M1c, mockup-v11 +
#     the v11 content finals 2026-07-27; server.py + client JS via NL_LABELS).
#     ONE grammar, ONE place, FOUR mounts (card · continuation card · deep view ·
#     Following row).
#
# THE THREAD MODEL (his 07-25 gate rulings; DECISIONS "MOCKUP-V11 GATE RULINGS"):
# one noun — THREAD. The tap commits a story-seeded thread INSTANTLY ($0, local,
# nothing waits on the settle). What settles in background is only what ELSE the
# thread covers, and the settle NEVER renders: named silently when it lands,
# otherwise the story-scoped follow simply stands. THE ASK IS DEAD. No standing
# Broaden/Widen exists anywhere; the surviving scope acts are NAMED swaps
# ("Instead: …") on management surfaces only. ---
# Resting verb (compact deck verb, not following): the ○/● pairing is the
# disclosure mark. NL-103 row 12 / v11 item 2: the object seat takes THREAD.
FOLLOW_THREAD_INACTIVE = "○ Follow this thread"
# The CTA's accessible-name STEM (content finals #49): the visible label without
# its mark glyph, composed as "<stem> — <story name>". A screen-reader button
# list on a today page of 8-12 cards otherwise reads "Follow this thread" a dozen
# times, indistinguishably. Held as its own constant, not sliced off the visible
# label at render time — a slice is a silent breakage the day the mark changes.
FOLLOW_THREAD_ARIA = "Follow this thread"
# Committed forms are COMPOSED around the settled compact qualifier name (the
# disclosure), never static — the disclosure rides every follow surface:
#   moment (follow-line):  "● " + FOLLOW_COMMITTED_VERB + " " + <disclosure>
#   steady (deck verb):    "● " + FOLLOW_STEADY_PREFIX  + " " + <disclosure>
#   story-seeded:          …the object seat's deictic, FOLLOW_THREAD_SELF
FOLLOW_COMMITTED_VERB = "Following"          # "● Following Volkswagen (company)"
FOLLOW_STEADY_PREFIX = "Following —"         # "● Following — Volkswagen (company)"
# TWO-REFERENT NOUN LAW (TAXONOMY §1.1, ratified 2026-07-26): thread takes the
# OBJECT seats, story takes the SCOPE seats. Same deictic shape, different seat —
# they are not interchangeable and neither may take the other's place.
FOLLOW_THREAD_SELF = "this thread"           # object seat  — "● Following this thread"
FOLLOW_DOT_ON = "●"
FOLLOW_DOT_OFF = "○"
# The acts line on a MANAGEMENT surface: "Instead: <alt> · Unfollow".
# Never on a card (his 07-25 ruling ②: today cards are clean A3).
FOLLOW_INSTEAD_PREFIX = "Instead:"
FOLLOW_UNFOLLOW = "Unfollow"                 # the symmetry-law verb (2026-07-18)
# Receipts (§2.1.4). The unfollow receipt reverts ~3s to the resting CTA — no
# undo affordance (his 07-25 verdict killed A11). A receipt never takes a pronoun
# object, so an unnamed story-seeded thread falls back to the class noun.
FOLLOW_UNFOLLOWED_RECEIPT = "Unfollowed —"           # + " <name>."
FOLLOW_UNFOLLOWED_SELF = "Unfollowed — this thread."
FOLLOW_REVERT_MS = 3000                              # the ~3s receipt window
# Follow-again resume clause — renders ONCE at re-follow (revive/merge lane).
FOLLOW_RESUMED_PREFIX = "Picked up where it left off —"   # + " <k> entries kept."
FOLLOW_RESUMED_ENTRIES = "entries kept."
FOLLOW_RESUMED_ENTRY = "entry kept."

# --- REFUSALS — the protected class (TAXONOMY §3; v11 content finals §2.1) ----
# THE LAW THIS TABLE EXISTS FOR: a tap must never vanish silently. Every refusal
# payload renders its reason, on all four buckets, announced via aria-live.
#
# TWO refusal CLASSES, opposite marks — the client routes on the payload's
# CLASS, never on the HTTP status (write refusals are 200/ok:false; the coverage
# refusal is 409; a status-shaped discriminator misses half of them):
#   R-WRITE    nothing was followed  -> ○, loud, aria-live      (this section)
#   R-COVERAGE the follow STANDS; only the broadening was refused -> renders
#              NOTHING (the "— this story" qualifier is the whole disclosure)
#
# FRAME (fixed):  "Didn’t <verb> — <reason>."  +  "<remedy>"
REFUSAL_DIDNT_FOLLOW = "Didn’t follow —"
REFUSAL_DIDNT_SWITCH = "Didn’t switch —"
REFUSAL_DIDNT_UNFOLLOW = "Didn’t unfollow —"
# Reason clauses: reader-world, lowercase, no terminal period, UI-lane — NEVER
# str(exc). One per MemorySyncError arm; the arm is named at the RAISE site
# (memory.MemorySyncError.kind), so the branch that renders the reason is the
# branch that produced it.
REFUSAL_MEM_UNREADABLE = "your memory file can’t be read"
REFUSAL_MEM_UNREADABLE_FIX = "Fix the permissions on memory.md, then try again."
REFUSAL_MEM_UNPARSEABLE = "your memory file has lines NewsLens can’t read"
REFUSAL_MEM_UNPARSEABLE_FIX = ("Fix memory.md, or delete it and let NewsLens "
                               "rebuild it, then try again.")
REFUSAL_MEM_UNWRITABLE = "your memory file couldn’t be saved"
REFUSAL_MEM_UNWRITABLE_FIX = "Make sure memory.md is writable, then try again."
# The required fallback: an unmapped arm (a transport failure, an unclassified
# raise) must never render raw CLI prose and must never silently revert.
REFUSAL_MEM_FALLBACK = "NewsLens couldn’t save to your memory file"
REFUSAL_MEM_FALLBACK_FIX = "Run newslens memory sync to see what’s wrong."
# RETIRED with the thread model (2026-07-25/27 rulings), kept so nothing imports
# a dangling name and so the ruled strings stay on record:
#   * the settle is INVISIBLE (ruling ①) — no status copy exists any more
#   * THE ASK IS DEAD (ruling ④) — the lead and its option row have no state
#   * the degrade pair dies with it (NL-103 row 3): a failed settle is an
#     ordinary story-scoped follow, disclosed by the "— this story" qualifier
FOLLOW_RESOLVING = "Deciding what this follow covers…"   # RETIRED-NOT-RENDERED (v11 ruling ①)
FOLLOW_LOW_LEAD = "What would you like to follow?"       # RETIRED-NOT-RENDERED (v11 ruling ④)
FOLLOW_JUST_THIS_STORY_OPTION = "Just this story"        # RETIRED-NOT-RENDERED (v11 ruling ④)
# --- NL-17 M1: BURIED BY AMENDMENT (i) ---------------------------------------
# The principal's 2026-08-07 amendment (i) kills "this story" as an offered
# choice, and the product council's case (a) extends that to every seat it held:
# "'This story' appears nowhere — not as label, not as qualifier, not as rung."
# These two were the QUALIFIER (a Following row's "— this story") and the RUNG
# (the acts line's narrow switch). Both emit sites are deleted; kept here under
# the retired-but-named convention so nothing imports a dangling constant and so
# the ruled strings stay on the record. NOTHING REPLACES THEM — the council ruled
# the rung out with no substitute and the row now renders its name bare.
FOLLOW_NARROW = "this story"             # RETIRED-NOT-RENDERED (NL-17 M1, amendment (i))
FOLLOW_RUNG_THIS_STORY = "this story"    # RETIRED-NOT-RENDERED (NL-17 M1, amendment (i))
# --- THE WORDED-FALLBACK ARM, BURIED (NL-17 M1 fix loop 2b) ------------------
# Product RECONVENE-2, unanimous, ruling (b), on QA's F-4. These named the OTHER
# RUNG IN WORDS when a settle never named it — "Instead: the wider story" — an
# anchor with an aria promise and no target identity behind it, which fix loop 1
# turned into a silent no-op (bucket 1051). The council killed the arm rather
# than repair it: a restore is definitionally empty (the state exists only when
# nothing resolved, and the one nameable target left is the seed storyline that
# amendment (i) bans), inert prose states nothing and fails the register, and a
# bare "Instead:" asserts alternatives that do not exist.
#
# THE LAST GENERIC-WIDEN GHOST: v10 killed the language, v11 the posture, this
# makes the burial deliberate. Unnamed scope switches are no longer offered
# anywhere; Unfollow and the next story's follow line remain. Kept named under
# the retired-but-named convention so nothing imports a dangling constant and
# the ruled strings stay on the record — rendered NOWHERE, and absent from the
# client label table so no branch can reach them.
FOLLOW_ALT_FALLBACK_ENTITY = "the company"          # RETIRED-NOT-RENDERED (fix loop 2b)
FOLLOW_ALT_FALLBACK_STORYLINE = "the wider story"   # RETIRED-NOT-RENDERED (fix loop 2b; was NL-103 row 14)
# THE 07-18 FAILURE COPY, FORMALLY BURIED (product council 2026-08-08 §1 + case
# (b)). It was already dead twice — v11 ruling ① (the settle is invisible) and
# ruling ④ (an unsettled follow simply stands) — and case (b) makes the burial a
# ruling of record: a failed or timed-out settle renders NO STRING AT ALL.
# Silence is the ruling. The failure now exists in exactly one place, the 0025
# `settle_failed` event, which is a record and not a surface.
FOLLOW_DEGRADE_LEAD = "Following — this story."          # RETIRED-NOT-RENDERED (NL-103 row 3; BURIED NL-17 M1)
FOLLOW_DEGRADE_UPGRADE = "Couldn't fetch broader follow — choose it anytime."  # RETIRED-NOT-RENDERED (NL-103 row 3; BURIED NL-17 M1)
FOLLOW_DEGRADE_COMMITTED = FOLLOW_DEGRADE_LEAD + " " + FOLLOW_DEGRADE_UPGRADE  # RETIRED-NOT-RENDERED (NL-103 row 3; BURIED NL-17 M1)
# Switch refused (FIX LOOP 2 R2). SUPERSEDED by the R-WRITE frame above: an act
# refusal now states its REASON in reader-world terms and names no transience
# the client cannot know ("just now"). Kept as the record of the string it
# replaced; the live path is REFUSAL_DIDNT_SWITCH + a reason clause.
FOLLOW_SWITCH_FAILED = "Couldn't switch just now — try again."   # RETIRED-NOT-RENDERED (M1c §4.1)
# Cap refusal (R1, 2026-07-25) — RETIRED FROM READER COPY by M1c, ARM A of the
# content pass's §5.1 fork. Its precondition (flagged in-code with the R1 batch,
# and deliberately left to M1c) is now SATISFIED IN THE OTHER DIRECTION: the tap
# commits the story-seeded thread BEFORE anything can be refused, so a cap
# refusal can only refuse the BROADENING. That is the R-COVERAGE class, and the
# R-COVERAGE class renders nothing — the "— this story" qualifier is the whole
# disclosure. The wire payload keeps a machine-parseable `detail` for diagnostics
# (never reader copy); the meter that would have paired with this sentence stays
# dormant, because the surface it needed no longer exists.
FOLLOW_CAP_REFUSAL = (   # RETIRED-NOT-RENDERED (M1c Arm A)
    "Couldn’t choose a broader follow — it costs more than this run’s budget "
    "allows. The follow stands — this story.")
# RETIRED 2026-07-18 (M1b), retired-but-kept so nothing imports a dangling name
# (KICKER_LEAD precedent). The instant-flip toast and static active label are
# replaced by the inline resolving→committed disclosure; no surface renders them.
FOLLOW_STORY_ACTIVE = "Following this story"   # RETIRED-NOT-RENDERED (M1b)
FOLLOW_STORY_CONFIRM = "✓ Following — see it under Following → Threads"  # RETIRED-NOT-RENDERED (M1b)
TRACKED_ONGOING_PREFIX = "Tracked ongoing story —"

# --- Staleness guard (2026-07-16 stale-server incident -> a mechanism) --------
# The server was running in-memory modules that predated two committed
# milestones and generated a defective edition with zero disclosure. Reading a
# stale-rendered page is tolerable; WRITING an edition with stale code is the
# incident — so the banner warns and the generate trigger refuses.
STALENESS_BANNER_TITLE = "This server’s running code no longer matches what’s on disk."
STALENESS_BANNER_BODY = (
    "Reading is fine, but new editions are paused until you restart it:")
STALENESS_REFUSAL = (
    "This server’s running code no longer matches what’s on disk — restart it "
    "to generate a new edition: newslens serve")

# --- Deep-back labels (the one-line back affordances) ------------------------
# NL-103 row 17 (B8): visible label = the bare destination, matching the mockup;
# the accessible name ("Back to <destination>") is derived at render time by
# server._back_link so a re-pin here re-pins the aria too (§3 aria law).
BACK_TO_TODAY = "← Today"
BACK_TO_EDITION = "← This edition"
BACK_TO_ARCHIVE = "← Archive"

# --- Archive (§14 step-back redesign; supersedes the §8 list-primary law) -----
# NL-103 row 20: KEPT bare. This one renders as the immediate next sibling of
# the page's own <h1 class="page-title">Archive</h1> inside the same .page
# container, nothing between them (server._render_archive) — the section head is
# programmatically adjacent, which is exactly the case §3 licenses.
ARCHIVE_EMPTY = "Nothing yet"
# NL-68 item 14: ARCHIVE_CAL_INDEX_NOTE ("The grid is an index of the list below
# it.") REMOVED — interface-explaining copy the principal named as condescension.
ARCHIVE_TODAY_TAG = "TODAY"
# §14: the day panel's jump — sits ABOVE the headlines (his approved spec).
ARCHIVE_VIEW_BRIEFING = "View briefing"
# §14 gate FIX-2: the sr-only qualifier on a no-edition today — terra alone is
# color-alone non-visually; the a11y tree must not carry a bare numeral.
ARCHIVE_TODAY_NO_EDITION = "today — no edition yet"

# =============================================================================
# THE COMMISSIONING — Stage-0 C1, the founding page a stranger's first run opens
# =============================================================================
# Every string below is the v12 mockup's VOICE inventory, as amended at the
# principal's browser gate 2026-07-28 (the leave pair compressed to one line,
# the sched line compressed; "topics" ratified over "subjects" wholesale).
# Ship them BYTE-FOR-BYTE: this table is where a copy re-pin lands, and
# tests/test_stage0_c1_commissioning.py pins each one to the surface that
# renders it.
#
# THE VOCABULARY LAW (ratified 2026-07-28): the reader-facing word is TOPICS.
# "subject"/"subjects" may not appear on any reader surface, and neither may
# the mockup's KILLED-ON-SIGHT list. tests/test_stage0_c1_vocabulary.py greps
# the RENDERED page, not this table, so a banned word cannot arrive through a
# template either.

# --- The masthead ------------------------------------------------------------
COMMISSION_MASTHEAD = "Founding an edition"

# --- Act 1: topics -----------------------------------------------------------
COMMISSION_TOPICS_HEAD = "Topics"
COMMISSION_TOPICS_SAY = "Pick at least one."
COMMISSION_TOPICS_NOTE = (
    "These are the topics NewsLens can rank. Typing filters this list; it "
    "never adds to it.")
COMMISSION_FILTER_LABEL = "Find a topic"
# The filter status renders "<n> topics." at rest and "<n> topics match “q”." /
# "No topic matches “q”." while filtering — assembled in commissioning.py so the
# count is derived from the catalog, never typed into copy.
COMMISSION_COUNT_NONE = "Nothing picked yet."
# "1 topic picked." / "3 topics picked." — the numeral and plural are computed.
COMMISSION_COUNT_ONE = "topic picked."
COMMISSION_COUNT_MANY = "topics picked."
# FLAG ⑤ as ruled: floor of 1, and this line renders at counts 1–2 only.
COMMISSION_CONSEQUENCE = (
    "With fewer than three topics, more of the first edition is general news.")

# --- Coverage honesty at pick time (NL-135 Q1, ruled 2026-08-02) -------------
# THE CLAIM ASYMMETRY IS IN THE WORDS, not only in the code. Absence is
# provable from the shipped feed→topic map, so it is stated flatly. Presence is
# INCLUSION — "4 sources cover this area" is a fact about the list and must
# never be written as a promise of stories ("you're covered", "well covered",
# "we've got this" are all out of bounds, and none of them can be earned from
# a static map). Every numeral here is computed by commissioning.py.
#
# "regularly" is doing real work and is not padding: the map records a feed's
# BEAT, so a general front page that mentions a subject during a big week is
# not counted. Without that word the absence line would be a false claim over
# a list that holds four world desks.
COMMISSION_COV_NONE = "No source in your list covers this regularly."
# Rendered at BOTH grains, because it weakens rather than asserts. It is the
# one honest thing a static map can say about the Systemic Risk credit topics,
# where every covering source (Bloomberg, FT, WSJ) is titles-and-summaries.
COMMISSION_COV_HEADLINES = "Your sources here carry headlines only."
# "4 sources cover this area." — DOMAIN rows only. A per-topic positive claim
# is unearnable from a per-feed map (a technology feed proves nothing about
# one technology topic), so topic rows stay silent unless they carry a warning.
COMMISSION_COV_ONE = "source covers this area."
COMMISSION_COV_MANY = "sources cover this area."
# The consequence summary, computed from the reader's own ticks and recomputed
# client-side on every toggle: "You picked 3 topics your sources don't cover
# regularly. Briefings will lean on general news there until sources are
# added." Unserved names stay PICKABLE — picking one is standing intent and
# the org's demand signal for the next slate; hiding them would lie by omission.
COMMISSION_COV_UNSERVED_HEAD = "You picked"
COMMISSION_COV_UNSERVED_ONE = "topic your sources don’t cover regularly."
COMMISSION_COV_UNSERVED_MANY = "topics your sources don’t cover regularly."
COMMISSION_COV_UNSERVED_TAIL = (
    "Briefings will lean on general news there until sources are added.")
# The honesty valve for a hand-edited list: the shipped map cannot classify an
# outlet nobody mapped, and an absence claim made over sources we did not read
# is exactly the fabrication this mechanism exists to prevent. A fresh profile
# never sees this line — its list IS the mapped catalog.
COMMISSION_COV_UNKNOWN_ONE = (
    "source in your list isn’t classified yet, so it isn’t counted above.")
COMMISSION_COV_UNKNOWN_MANY = (
    "sources in your list aren’t classified yet, so they aren’t counted above.")

# "Show 8 narrower topics" / "Hide 8 narrower topics" — count computed.
COMMISSION_SHOW_NARROWER = "Show"
COMMISSION_HIDE_NARROWER = "Hide"
COMMISSION_NARROWER_SUFFIX = "narrower topics"

# --- Act 2: sources ----------------------------------------------------------
COMMISSION_SOURCES_HEAD = "Sources"
COMMISSION_SOURCES_SHOW = "Show the list"
COMMISSION_PACK_FETCHED = "FETCHED EACH MORNING"
# NL-136 ①, same ruling as the pack sentence's third clause: the group the
# reader actually opens and reads the four names in should frame them the same
# way the sentence above it does. "CITED, NOT FETCHED" is mechanically true and
# reads as a shortfall; the four are a decision.
COMMISSION_PACK_CITED = "ATTRIBUTION ONLY, BY DESIGN"
COMMISSION_PACK_OFF = "OFF"
COMMISSION_PACK_HEADLINES_ONLY = "(headlines only)"
COMMISSION_SOURCES_SETTINGS = "Sources can be turned off in Settings."

# --- Act 3: the found act ----------------------------------------------------
COMMISSION_FOUND = "Generate Edition No. 1"
COMMISSION_FOUND_SUB = "This takes about half an hour."
COMMISSION_FOUND_REFUSAL = "Didn’t generate — no topics are picked."

# --- The wait (C3) -----------------------------------------------------------
COMMISSION_WAIT_HEAD = "Generating Edition No. 1…"
COMMISSION_WAIT_LEAVE = (
    "Closing this page won’t stop it — the edition will be here when it’s "
    "ready.")
# The record line: "This record began <Mon D, YYYY>" — the date is the profile's
# own first-run stamp, computed at render.
COMMISSION_RECORD_BEGAN = "This record began"

# --- The wait's stage words (SEAM-3) -----------------------------------------
# The reader-world stage map. The shipped panel renders generate.PROGRESS_LABELS
# plus the model name — right on the founder's own screen, internal vocabulary
# on a stranger's. commissioning.reader_stage() maps by PHASE KEY (derived from
# generate.PROGRESS_LABELS, never a second copy of it) so a re-pin there cannot
# silently orphan a word here.
COMMISSION_STAGE_INGEST = "Fetching sources"
COMMISSION_STAGE_RANK = "Picking the stories"
COMMISSION_STAGE_ANALYSIS = "Reading around them"
COMMISSION_STAGE_NARRATIVE = "Writing"
COMMISSION_STAGE_EDITOR = "Editing"
COMMISSION_STAGE_RECORDING = "Recording the episode"   # script AND audio
COMMISSION_STAGE_SAVING = "Saving"                     # persist AND state
COMMISSION_STAGE_STARTING = "Starting…"                # before the first phase
# Defence in depth ONLY: a phase added to generate.PROGRESS_LABELS with no
# reader word here fails tests/test_stage0_c1_commissioning.py before it can
# ship, so nothing in production renders this. It exists so that if one ever
# did, a stranger reads a true plain word instead of an internal key.
COMMISSION_STAGE_FALLBACK = "Working"
COMMISSION_TOTAL = "TOTAL"

# --- The wait, failed (C3-FAIL) ----------------------------------------------
COMMISSION_FAIL_HEAD = "Edition No. 1 didn’t finish."
COMMISSION_FAIL_TRY_AGAIN = "Try again"
# The three outcome sentences are INHERITED VERBATIM from the shipped panel —
# server._render_today owns them and the Commissioning reuses the same strings
# rather than re-drafting: "Nothing was published." / "The saved edition is
# intact." / "The saved edition is empty."

# --- The found act's own refusals (SEAM-2) -----------------------------------
# A stranger must never meet the CLI refusal (which names a profile and a
# filesystem path) or a raw generation exception. These are the reader-world
# forms of the two ways the found act can fail before a run starts.
COMMISSION_WRITE_REFUSAL = (
    "Didn’t generate — your topics couldn’t be saved, so nothing was started.")
COMMISSION_VERIFY_REFUSAL = (
    "Didn’t generate — your topics didn’t save, so nothing was started.")
COMMISSION_UNKNOWN_TOPIC = (
    "Didn’t generate — one of those topics isn’t in the list.")
# NEW COPY 2026-07-30, C1 fix loop 1 (QA-3) — FLAGGED FOR THE GATE, not ruled.
# The third way the found act can fail: some picks landed and a later one did
# not. The two refusals above are both FALSE in that state ("your topics
# couldn't be saved" over a file that holds one of them), so the reader is told
# which ones are in. `{topics}` is filled by commissioning._name_list, so the
# sentence carries the reader's own words back at them and never a count.
# Voice: the same "Didn't generate — <fact>, so nothing was started." frame the
# two siblings use, and the same intransitive "saved" as COMMISSION_VERIFY_REFUSAL.
COMMISSION_PARTIAL_REFUSAL = (
    "Didn’t generate — only {topics} saved, so nothing was started.")
COMMISSION_LIST_AND = " and "

# --- The source pack's counted clauses (Commissioning act 2) -----------------
# Assembled by commissioning.source_pack_sentence from the reader's OWN file —
# every number is counted, never typed, and a clause whose count is zero is
# dropped rather than rendered as "0". Against the shipped profile template
# they render (measured NL-142b, 2026-08-07): "69 outlets. 64 are fetched each
# morning. 4 are attribution-only by design. 1 source is off." (Before the
# NL-135 slate the same builder rendered the mockup's original line, "42
# outlets. 37 are fetched each morning. 4 are cited but never fetched. 1
# aggregator is off." — the numbers moved because the file did, which is the
# whole point of counting them.) The off clause has now been all three of its
# states on the shipped file: an aggregator (pre-NL-136), absent (NL-136 ①
# through ENG-M0), and a full-tier outlet under the NEUTRAL noun — the frozen
# CNN front page, disabled at the principal's ruling (a) 2026-08-06.
COMMISSION_PACK_OUTLET = "outlet"
COMMISSION_PACK_OUTLETS = "outlets"
COMMISSION_PACK_FETCHED_ONE = "is fetched each morning"
COMMISSION_PACK_FETCHED_MANY = "are fetched each morning"
# NL-136 ① (ruled 2026-08-02): the reference_only outlets are a DESIGN, and the
# copy now says so. "4 are cited but never fetched" read as a shortfall — a
# reader counts 42 outlets, learns four of them aren't fetched, and reasonably
# concludes something is broken or withheld. The four (AP, Reuters, NYT,
# Wikipedia) are wire services and records-of-note that the principal ruled
# citable-but-never-fetched on purpose: their copy reaches us through other
# feeds, or the outlet's terms say linkout. The structure is unchanged; only
# the sentence stops apologising for it.
COMMISSION_PACK_CITED_ONE = "is attribution-only by design"
COMMISSION_PACK_CITED_MANY = "are attribution-only by design"
COMMISSION_PACK_OFF_AGG_ONE = "aggregator is off"
COMMISSION_PACK_OFF_AGG_MANY = "aggregators are off"
COMMISSION_PACK_OFF_SRC_ONE = "source is off"
COMMISSION_PACK_OFF_SRC_MANY = "sources are off"
COMMISSION_PACK_ANALYST_ONE = "analyst newsletter is in the list"
COMMISSION_PACK_ANALYST_MANY = "analyst newsletters are in the list"
COMMISSION_PACK_ANALYST_NONE = "; none are followed."

# --- The picker's counted lines ----------------------------------------------
COMMISSION_TOPIC_WORD = "topic"
COMMISSION_TOPICS_WORD = "topics"
# "2 topics match “grid”." / "1 topic matches “grid”." / "No topic matches “x”."
COMMISSION_FILTER_MATCH_ONE = "topic matches"
COMMISSION_FILTER_MATCH_MANY = "topics match"
COMMISSION_FILTER_NO_MATCH = "No topic matches"
