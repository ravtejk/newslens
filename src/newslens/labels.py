"""User-facing section/surface labels — ONE string table (NL-29 re-pin lands here).

Every user-visible section name and surface label the v7 shell renders is read
from THIS module at render time (attribute access, not a captured constant), so
the NL-29 naming round can re-pin a name in one place and every surface follows.

Provisional pending NL-29 (a naming round runs in parallel — the values below
are the CURRENT names, verbatim, NOT new picks; the implementer does not rename):
the principal's NL-29 list is "The full picture", "Advances the thread",
"Where this stands", "The story so far", "The facts", "Mechanism",
"What's Still Open". Those seven live here alongside the shell's own surface
labels (nav destinations, kicker, In brief, still-tracking).

Wiring proof: newslens.server reads these as `labels.<NAME>` at call time; a
monkeypatch of any constant appears in rendered output (see test_v7_shell_m1's
label-liveness test — the red test only the wiring can flip).

Stdlib-only by design (see newslens/__init__.py). No f-strings, no logic — a
table, deliberately boring so the re-pin is a one-line diff per name.
"""

# --- Nav destinations (the section line: Today · Following · Archive) --------
NAV_TODAY = "Today"
NAV_FOLLOWING = "Following"
NAV_ARCHIVE = "Archive"

# --- Today front-page furniture ---------------------------------------------
KICKER_LEAD = "The Lead"          # the lead story's kicker
IN_BRIEF = "In brief"             # the quick-tier cluster heading

# --- The deep-view entry affordances (NL-65 splits their PLACEMENT, not text) -
FULL_PICTURE = "The full picture"      # analyst-tier deep-view entry
SOURCES_CONTEXT = "Sources & context"  # In-Brief (quick-tier) $0 entry

# --- Deep-view section labels (NL-29 list) -----------------------------------
DEEP_FACTS = "The facts"
DEEP_NUMBERS = "The numbers"
DEEP_UNRESOLVED = "Unresolved"
DEEP_MECHANISM = "Mechanism"
DEEP_EFFECTS = "What could follow"
DEEP_OPEN = "What’s still open"   # principal's list: "What's Still Open"
DEEP_SOURCES = "Sources"
DEEP_WHY_SEEING = "Why you’re seeing this"  # sources-context view (gate FIX-2, v7-M1)
DEEP_EYEBROW = "The full picture"      # the deep-view eyebrow (same words as the entry)

# --- Deep-view jumplist short labels (where they differ from the section head) -
JUMP_FACTS = "Facts"
JUMP_OPEN = "Still open"

# --- Memory surfaces (NL-29 list; render lands with the M2 memory build) -----
WHERE_THIS_STANDS = "Where this stands"
THE_STORY_SO_FAR = "The story so far"

# --- Arc verdicts (NL-29 list carries "Advances the thread") -----------------
ARC_ADVANCES = "Advances the thread"
ARC_REVERSES = "Reverses the thread"
ARC_MATCHES = "Merely matches the thread"

# --- Still-tracking strip (Today surface; retro-mock idiom) -------------------
# Composed as: "Still tracking {thread} — {note}. {fixed_point}."
STILL_TRACKING_PREFIX = "Still tracking"
STILL_TRACKING_NO_DATE = "No next date is set."
