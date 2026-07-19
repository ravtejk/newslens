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
"""

# --- Nav destinations (the section line: Today · Following · Archive) --------
NAV_TODAY = "Today"
NAV_FOLLOWING = "Following"
NAV_ARCHIVE = "Archive"

# --- Today front-page furniture ---------------------------------------------
# NL-68 item 6: the visible "The Lead" kicker DIED — the design carries the
# hierarchy (largest type, top-left). The constant is retired-but-kept so nothing
# imports a dangling name; no surface renders it (grep server.py for KICKER_LEAD).
KICKER_LEAD = "The Lead"          # RETIRED (NL-68 item 6) — not rendered anywhere
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
THREAD_BACK = "← Back to Following"        # the thread page's back affordance
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
FOLLOWING_EMPTY = "Nothing yet"
# Row verbs (the thread editor / lifecycle controls).
VERB_STOP = "Stop"
VERB_RESUME = "Resume"
VERB_DELETE = "Delete"
VERB_EDIT_NOTE = "Edit note"

# --- Follow control — the follow-altitude picker (NL-17-M1b, mockup-v9 copy
#     register; server.py + client JS via NL_LABELS). One grammar, one place. ---
# Resting verb (compact deck verb, not following): the ○/● pairing is the
# disclosure mark (mockup COPY REGISTER). NB re-pin 2026-07-18: v8 shipped "＋
# Follow this story"; mockup-v9's register names "○" (see implementer report —
# flagged for the principal's glance against the STATE-0 "unchanged" caption).
FOLLOW_STORY_INACTIVE = "○ Follow this story"
# Committed forms are COMPOSED around the resolver's compact qualifier name (the
# disclosure), never static — Kass's disclosure on every follow surface:
#   moment (follow-line):  "● " + FOLLOW_COMMITTED_VERB + " " + <disclosure>
#   steady (deck verb):    "● " + FOLLOW_STEADY_PREFIX  + " " + <disclosure>
#   narrow (this-story):   …STEADY_PREFIX + " " + FOLLOW_NARROW_QUALIFIER-less
FOLLOW_COMMITTED_VERB = "Following"          # "● Following Volkswagen (company)"
FOLLOW_STEADY_PREFIX = "Following —"         # "● Following — Volkswagen (company)"
FOLLOW_NARROW = "this story"                 # narrow follow's name ("— this story")
FOLLOW_DOT_ON = "●"
FOLLOW_DOT_OFF = "○"
# The resolving interval (the control expands from the verb into the line).
FOLLOW_RESOLVING = "Deciding what this follow covers…"
# The acts line under a committed follow: "Instead: <alt> · just this story · Unfollow".
FOLLOW_INSTEAD_PREFIX = "Instead:"
# Lawful worded fallback when the resolver named no alternative (alt_label ''):
# name the OTHER altitude in words, never a bare symbol (mockup seam note).
FOLLOW_ALT_FALLBACK_ENTITY = "the company"
FOLLOW_ALT_FALLBACK_STORYLINE = "the ongoing story"
FOLLOW_JUST_THIS_STORY = "just this story"   # inline, acts line
FOLLOW_UNFOLLOW = "Unfollow"                 # the symmetry-law verb (2026-07-18)
# Low confidence: the line ASKS; nothing is followed until the reader picks.
FOLLOW_LOW_LEAD = "What would you like to follow?"
FOLLOW_JUST_THIS_STORY_OPTION = "Just this story"   # option-row form
# Resolver failure / timeout: the this-story follow commits IMMEDIATELY with
# EXACTLY this copy (principal ruling 2026-07-18, verbatim; string-equality
# pinned in test_nl17_m1b_wiring). The two halves are the moment line's two
# lines AND the Following-row upgrade line; they compose the exact whole.
FOLLOW_DEGRADE_LEAD = "Following — this story."
FOLLOW_DEGRADE_UPGRADE = "Couldn't fetch broader follow — choose it anytime."
FOLLOW_DEGRADE_COMMITTED = FOLLOW_DEGRADE_LEAD + " " + FOLLOW_DEGRADE_UPGRADE
# Switch refused: the server declined the "Instead" move (e.g. a transient); the
# current follow is UNCHANGED. A plain register line so the reader's tap is never
# a silent no-op (FIX LOOP 2 R2). FLAGGED FOR THE GATE — this exact string is the
# implementer's plainest register candidate, not a settled copy call; the gate /
# content own the final wording.
FOLLOW_SWITCH_FAILED = "Couldn't switch just now — try again."
# RETIRED 2026-07-18 (M1b), retired-but-kept so nothing imports a dangling name
# (KICKER_LEAD precedent). The instant-flip toast and static active label are
# replaced by the inline resolving→committed disclosure; no surface renders them.
FOLLOW_STORY_ACTIVE = "Following this story"   # RETIRED (M1b) — composed now
FOLLOW_STORY_CONFIRM = "✓ Following — see it under Following → Threads"  # RETIRED (M1b)
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
BACK_TO_TODAY = "← Back to today’s edition"
BACK_TO_EDITION = "← Back to this edition"
BACK_TO_ARCHIVE = "← Back to Archive"

# --- Archive (§14 step-back redesign; supersedes the §8 list-primary law) -----
ARCHIVE_EMPTY = "Nothing yet"
# NL-68 item 14: ARCHIVE_CAL_INDEX_NOTE ("The grid is an index of the list below
# it.") REMOVED — interface-explaining copy the principal named as condescension.
ARCHIVE_TODAY_TAG = "TODAY"
# §14: the day panel's jump — sits ABOVE the headlines (his approved spec).
ARCHIVE_VIEW_BRIEFING = "View briefing"
# §14 gate FIX-2: the sr-only qualifier on a no-edition today — terra alone is
# color-alone non-visually; the a11y tree must not carry a bare numeral.
ARCHIVE_TODAY_NO_EDITION = "today — no edition yet"
