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
FOLLOW_NARROW = "this story"                 # scope  seat — the row qualifier "— this story"
FOLLOW_DOT_ON = "●"
FOLLOW_DOT_OFF = "○"
# The acts line on a MANAGEMENT surface: "Instead: <alt> · this story · Unfollow".
# Never on a card (his 07-25 ruling ②: today cards are clean A3).
FOLLOW_INSTEAD_PREFIX = "Instead:"
# Lawful worded fallback when the settle named no alternative (alt_label ''):
# name the other coverage in words, never a bare symbol.
FOLLOW_ALT_FALLBACK_ENTITY = "the company"
FOLLOW_ALT_FALLBACK_STORYLINE = "the wider story"   # NL-103 row 14
FOLLOW_RUNG_THIS_STORY = "this story"        # NL-103 row 13 — bare rung, "just" dead
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
FOLLOW_DEGRADE_LEAD = "Following — this story."          # RETIRED-NOT-RENDERED (NL-103 row 3)
FOLLOW_DEGRADE_UPGRADE = "Couldn't fetch broader follow — choose it anytime."  # RETIRED-NOT-RENDERED (NL-103 row 3)
FOLLOW_DEGRADE_COMMITTED = FOLLOW_DEGRADE_LEAD + " " + FOLLOW_DEGRADE_UPGRADE  # RETIRED-NOT-RENDERED (NL-103 row 3)
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
