"""`newslens profile refresh-catalog` — how an EXISTING profile adopts org
source-catalog updates.

THE GAP THIS CLOSES (DECISIONS 2026-08-06 ④, chartered in the 2026-08-13
amendment). A profile's sources.yaml is a COPY of the org template, taken once
at `profile create` and frozen there (profiles.create:400, `shutil.copyfile`).
So the NL-135 slate that landed in the template on 2026-08-03 reached every
profile created after it and NO profile created before it. `fresh1` was created
2026-08-02: it ranked 385 general-news items against health interests and
surfaced 2 stories, because its own catalog has none of the six health feeds
that exist in the org template one directory away.

THE FOUR CONSTRAINTS THIS VERB IS BUILT AROUND, and how each becomes a
mechanism rather than a promise:

  1. USER-INVOKED, NEVER A SILENT ORG EDIT. There is no code path anywhere that
     calls `apply()` on its own — no generate hook, no doctor autofix, no
     migration. A reader's sources.yaml is theirs ("this file is yours to
     edit", the header of every copy), so the org proposes and the reader
     disposes, by typing the verb.

  2. PRESERVES THE READER'S OWN EDITS — enforced as ADD-ONLY. This verb appends
     source entries whose NAME the profile does not have. It never rewrites,
     re-enables, disables, re-URLs, re-tiers or removes an entry that is
     already there, and it never touches `interests:` or `settings:`. When the
     org template and the reader's copy disagree about a shared name (the
     template disabled CNN on 2026-08-07; a profile born before that still has
     it enabled) the disagreement is REPORTED and left alone. Verified after
     the merge, before the write lands, by a PAIR of gates: `_verify` re-parses
     the merged file and compares FIELDS (every pre-existing Source identical,
     the source set exactly `old | adopted`, interests and settings untouched),
     and `_verify_text` compares the TEXT — top-level blocks, and every line
     the ledger strip kept. The pair exists because either one alone has a
     blind spot the other covers: `_verify` cannot see a comment, and an
     `interests:` block that vanished is indistinguishable from one that was
     always empty to anything working from the parsed config — which is the
     state every new profile ships in.

  3. COMPOSES WITH THE ORG-UNTOUCHABLE FOUNDER PIN. The founder's own
     sources.yaml is not a refresh target from the org side, and this verb IS
     the org side. It refuses `default` by name AND structurally: the target's
     RESOLVED path is compared against the founder's resolved sources.yaml, and
     must additionally resolve inside the named profile's own root. Name
     comparisons alone are defeated by a symlink called anything else — that is
     the NL-132-B lesson (`profiles._at_or_within`), and it is reused here
     rather than re-learned.

  4. DRY RUN BY DEFAULT. `plan()` reads; `apply()` writes; the CLI calls the
     first unless `--apply` is typed. Same shape as `profile delete` and
     `discovery-clean`.

MERGE SEMANTICS, stated once so they can be argued with:

  Identity is the source NAME (config.Source.name), normalized on whitespace
  and case — the same `_key` rule catalog.py and coverage.py already use, and
  the field that attribution and corroboration already key on.

  * name in template, not in profile, never offered before  -> OFFERED
  * name in both                                            -> UNTOUCHED
                                                               (divergences reported)
  * name in profile, not in template                        -> UNTOUCHED
                                                               (reader's own add, or
                                                               an entry the org dropped
                                                               — either way, theirs)
  * name adopted by an earlier refresh, absent now          -> NEVER RE-OFFERED
                                                               (the reader deleted it)
  * name declined by an earlier refresh (--skip)            -> NEVER RE-OFFERED

  The last two are why the ledger exists. Without it, "the reader deleted STAT
  News" and "STAT News is new to this reader" are the same observation, and the
  verb would argue with the reader's editing every time it ran. The ledger is a
  comment block written into the reader's own file: visible, hand-editable
  (deleting a line un-remembers it), and it travels with the file it describes.
  It is comments only, so it can never change what the YAML means.

  FIRST-RUN HONESTY, since a merge design should say where it is weak: a
  profile created before this verb existed has no ledger, so its first refresh
  cannot distinguish "deleted on purpose" from "never seen". That is exactly
  why the default is a dry run that prints every name it would add — the reader
  reads the list and either applies it or skips what they meant to be rid of.
  From the first apply onward the distinction is recorded and the question does
  not come back.

WHY TEXT, NOT A YAML ROUND-TRIP. PyYAML is the only YAML dependency and it does
not preserve comments; a load/dump cycle would silently delete every comment in
the reader's file — the header that says the file is theirs, the per-entry notes
explaining what a feed is, the reasons beside each disabled line. Those comments
ARE the catalog's documentation. So the merge is textual: entries are copied out
of the template verbatim, their own comments included, and appended at the end
of the `sources:` block. The result is then parsed and checked before it lands.

Two consequences of that choice, each of which cost a bug (QA 2026-08-13):

  * A TEXTUAL COPY CARRIES THE TEMPLATE'S COLUMN. The template writes its list
    at two spaces; a reader who writes theirs at four, or at zero, is not wrong
    — both are valid YAML — but a sequence with two indentations is not. So the
    copy is moved to the reader's own column (`_list_indent` / `_reindent`),
    and the shift is computed in `plan()` so the dry run cannot promise
    something `apply` refuses.
  * WHAT THE PARSER CANNOT SEE, THE TEXT GATE MUST. See constraint 2 above.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import config, paths, profiles


class RefreshRefused(Exception):
    """This refresh will not run against this target. Nothing was changed."""


class RefreshMalformed(Exception):
    """A file this verb must edit is not shaped like a sources.yaml, or the
    merge it produced does not parse. Nothing was changed."""


# --- the ledger's on-disk vocabulary ---------------------------------------
# Comment lines, so they are invisible to YAML and visible to the reader.
LEDGER_BEGIN = "# ===== newslens catalog-refresh ledger ====="
LEDGER_END = "# ===== end catalog-refresh ledger ====="
_TAG_REFRESH = "#@refresh "
_TAG_ADOPTED = "#@adopted "
_TAG_DECLINED = "#@declined "

_ITEM_RE = re.compile(r"^(\s*)-\s+name:\s*(.+?)\s*$")
_TOPLEVEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:")
_TOPLEVEL_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:")


def _key(name: str) -> str:
    """Source-name identity. Same rule as catalog._key / coverage._key."""
    return " ".join(str(name or "").split()).lower()


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


# ---------------------------------------------------------------------------
# What a plan says
# ---------------------------------------------------------------------------

@dataclass
class Offer:
    """One org-catalog entry this profile does not have, with the template's
    own text for it — comments included, copied never re-generated."""
    name: str
    lines: List[str]
    tier: str
    enabled: bool
    rss_url: Optional[str]

    @property
    def summary(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"{self.name} ({self.tier}, {state})"


@dataclass
class Divergence:
    """A name both files carry, where the org template now says something
    different. REPORTED ONLY — the reader's line is the reader's."""
    name: str
    field: str
    template: object
    profile: object


@dataclass
class Ledger:
    """What this profile has already been offered."""
    history: List[str] = field(default_factory=list)   # verbatim #@refresh lines
    adopted: List[str] = field(default_factory=list)
    declined: List[str] = field(default_factory=list)

    @property
    def keys(self) -> set:
        return {_key(n) for n in self.adopted} | {_key(n) for n in self.declined}


@dataclass
class RefreshPlan:
    slug: str
    sources_file: Path
    template_file: Path
    template_digest: str
    offers: List[Offer] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    reader_removed: List[str] = field(default_factory=list)
    previously_declined: List[str] = field(default_factory=list)
    reader_only: List[str] = field(default_factory=list)
    divergences: List[Divergence] = field(default_factory=list)
    ledger: Ledger = field(default_factory=Ledger)
    applied: bool = False
    adopted_now: List[str] = field(default_factory=list)
    # --skip names that matched nothing this refresh would have offered (QA
    # F-10). Accepted, reported, never an error: a reader who skips a feed they
    # already have has said something true.
    skip_unmatched: List[str] = field(default_factory=list)
    # How far the copied blocks had to move to match THIS reader's list
    # indentation (QA F-3). Computed here so `apply` writes exactly what the
    # dry run costed — one number, one place, no second implementation to
    # disagree with the first.
    indent_shift: int = 0
    # Set by apply(): the line ending the merged file was written with, and
    # whether the reader's file used more than one (QA F-7).
    line_ending: str = "\n"
    mixed_line_endings: bool = False

    @property
    def is_noop(self) -> bool:
        """Nothing to add AND nothing new to remember."""
        return not self.offers and not self.skipped


# ---------------------------------------------------------------------------
# Target resolution — constraint 3 lives here
# ---------------------------------------------------------------------------

def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:                       # broken symlink, unreadable parent
        return path.absolute()


def target_sources_file(profile: str, anchor: Optional[Path] = None) -> Tuple[str, Path]:
    """The one file this verb may write, or a refusal.

    Structural, not nominal. `profile delete` learned (NL-132-B, QA F-1) that a
    table of protected names protects only what somebody remembered to name and
    is defeated by one symlink; the same reasoning applies to a verb that
    rewrites a catalog. So: refuse the founder by name, refuse anything that
    RESOLVES to the founder's sources.yaml whatever it is called, and refuse
    anything that resolves outside the named profile's own root."""
    slug = paths.normalize_profile(profile)

    if slug == paths.DEFAULT_PROFILE:
        raise RefreshRefused(
            f"{paths.DEFAULT_PROFILE!r} is the founder's own catalog — this "
            "verb never targets it. His sources.yaml is his own file, edited "
            "by hand; the org's copy is templates/profile-sources.yaml and "
            "the two are deliberately not the same file. Name a reader "
            "profile instead (`newslens profile list`).")

    slug = profiles.require_exists(slug, anchor)
    target = paths.profile_layout(slug, anchor)["SOURCES_FILE"]
    if not target.exists():
        raise RefreshMalformed(
            f"profile {slug!r} has no sources.yaml at {target} — nothing to "
            "refresh. Re-provision it, or restore the file from "
            "templates/profile-sources.yaml.")

    real_target = _resolve(target)
    founder = _resolve(paths.profile_layout(paths.DEFAULT_PROFILE, anchor)["SOURCES_FILE"])
    if real_target == founder:
        raise RefreshRefused(
            f"refusing: profile {slug!r}'s sources.yaml resolves to the "
            f"FOUNDER's own catalog ({founder}). Whatever the link is called, "
            "the file behind it is his, and this verb does not write it. "
            "Nothing was changed.")

    real_root = _resolve(paths.profile_root(slug, anchor))
    if not profiles._at_or_within(real_target, real_root):
        raise RefreshRefused(
            f"refusing: profile {slug!r}'s sources.yaml resolves OUTSIDE its "
            f"own profile root — {real_target} is not inside {real_root}. A "
            "refresh writes a catalog; it does not follow a link out of the "
            "world it was pointed at. Nothing was changed.")

    # HARDLINK — the flank the two guards above structurally cannot cover, and
    # the reason QA F-6 called the founder's safety here undefended. A hardlink
    # is not a link the resolver can follow: `Path.resolve()` does not traverse
    # one, so a profile catalog hardlinked to the founder's file is a DIFFERENT
    # path to the SAME inode and both guards read it as innocent. The founder
    # survives today only because `os.replace` swaps a directory entry instead
    # of writing through — a true property of the writer, but a side effect
    # rather than a decision, and the pin for it is
    # `test_the_write_unlinks_rather_than_writing_through_a_hardlink`.
    # This is the decision: a catalog that is also known by another name is not
    # a file this verb rewrites without being told twice.
    try:
        links = target.stat().st_nlink
    except OSError:
        links = 1
    if links > 1:
        raise RefreshRefused(
            f"refusing: profile {slug!r}'s sources.yaml is a HARD LINK — the "
            f"same file is reachable under {links} names ({target}). A refresh "
            "rewrites a catalog, and this one may be somebody else's catalog "
            "too. Break the link first (copy the file, delete the original, "
            "rename the copy back) and re-run. Nothing was changed.")
    return slug, target


# ---------------------------------------------------------------------------
# Reading the two files as TEXT (comments are content here)
# ---------------------------------------------------------------------------

def _sources_span(lines: Sequence[str], where: str) -> Tuple[int, int]:
    """(first line after `sources:`, first line of whatever follows the block).

    The end bound is the next TOP-LEVEL key, backed up over the run of blank
    and comment lines immediately before it — those belong to the key they
    introduce (`# --- Interests ---` heads `interests:`), not to the last
    source entry."""
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^sources\s*:", line):
            start = i + 1
            break
    if start is None:
        raise RefreshMalformed(
            f"{where}: no top-level `sources:` key — this does not look like a "
            "sources.yaml. Nothing was changed.")

    end = len(lines)
    for j in range(start, len(lines)):
        if _TOPLEVEL_RE.match(lines[j]):
            end = j
            break
    while end > start and (not lines[end - 1].strip()
                           or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    return start, end


def _blocks(lines: Sequence[str], lo: int, hi: int) -> List[Tuple[str, int, int]]:
    """Per-entry text spans inside the sources block: (name, start, end).

    A block owns its own lines only. Trailing blank/comment lines are trimmed
    off its end — a section banner sitting above the next entry introduces THAT
    entry, and carrying it along would import the org's section furniture into
    a reader's file."""
    starts: List[Tuple[int, str]] = []
    for i in range(lo, hi):
        m = _ITEM_RE.match(lines[i])
        if m:
            starts.append((i, _unquote(m.group(2))))
    out: List[Tuple[str, int, int]] = []
    for n, (i, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else hi
        while end > i + 1 and (not lines[end - 1].strip()
                               or lines[end - 1].lstrip().startswith("#")):
            end -= 1
        out.append((name, i, end))
    return out


def _read_ledger(lines: Sequence[str]) -> Ledger:
    """Scan for the ledger's tag lines anywhere in the file. Placement-
    independent on purpose: the reader may move the block, and a ledger that
    only works in one position is a ledger that silently forgets."""
    led = Ledger()
    for line in lines:
        s = line.strip()
        if s.startswith(_TAG_REFRESH):
            led.history.append(s)
        elif s.startswith(_TAG_ADOPTED):
            name = s[len(_TAG_ADOPTED):].strip()
            if name:
                led.adopted.append(name)
        elif s.startswith(_TAG_DECLINED):
            name = s[len(_TAG_DECLINED):].strip()
            if name:
                led.declined.append(name)
    return led


def _strip_ledger(lines: Sequence[str]) -> List[str]:
    """Drop the ledger block so it can be re-rendered. Removes the sentinel
    span when both sentinels are present, and any stray tag lines otherwise —
    a half-deleted ledger must not be able to duplicate itself.

    BOUNDED, and the bound is the whole point (QA F-1). The first draft set
    `inside = True` on BEGIN and cleared it only on END, so a ledger whose
    closing sentinel had been deleted ran to EOF and swallowed everything below
    it. Because the ledger lands at the END of the `sources:` block, everything
    below it is the reader's `interests:` block — and on a profile whose
    interests are still empty (`templates/profile-sources.yaml`, the shipped
    state of every new profile) `_verify` structurally could not see the loss:
    `before.interests_broad` and `after.interests_broad` were both `[]`. Nine
    lines deleted with no error and no symptom, six of them the instructions
    for choosing the interests that `newslens rank` refuses by name without.

    Deleting a ledger line is not an exotic act — the block itself invites it
    ("deleting a line simply un-remembers it"), and half-deleting one is what
    an interrupted edit looks like. So an opened ledger that never closes is a
    REFUSAL: everything under a broken sentinel is the reader's content until
    proven otherwise, and this verb does not get to guess."""
    out: List[str] = []
    inside = False
    opened_at = 0
    for n, line in enumerate(lines, start=1):
        s = line.strip()
        if s == LEDGER_BEGIN:
            if not inside:
                opened_at = n
            inside = True
            continue
        if s == LEDGER_END:
            inside = False
            continue
        if inside:
            continue
        if s.startswith((_TAG_REFRESH, _TAG_ADOPTED, _TAG_DECLINED)):
            continue
        out.append(line)
    if inside:
        raise RefreshMalformed(
            f"the catalog-refresh ledger opened at line {opened_at} "
            f"({LEDGER_BEGIN!r}) is never closed by {LEDGER_END!r}. Refusing "
            "rather than treating the whole rest of the file as ledger — "
            "everything below that line is YOURS, including your `interests:` "
            "block. Fix it either way: put the closing line back at the end of "
            "the ledger, or delete the opening line and the `#@` lines under "
            "it (which simply un-remembers what they record). Nothing was "
            "changed.")
    return out


def _toplevel_keys(lines: Sequence[str]) -> List[str]:
    """The top-level keys a reader would SEE in this text, in file order.

    Deliberately textual rather than parsed: `SourcesConfig` flattens the file
    into fields, so an `interests:` block that vanished entirely and an
    `interests:` block that was always empty are the same object to it. The
    text knows the difference (QA F-1)."""
    out: List[str] = []
    for line in lines:
        m = _TOPLEVEL_KEY_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def _lines_missing(expected: Sequence[str], present: Sequence[str]) -> List[str]:
    """Which of `expected` do not appear in `present`, counting duplicates.
    Order-insensitive on purpose — a merge may move a line; it may not lose
    one."""
    pool: Dict[str, int] = {}
    for line in present:
        pool[line] = pool.get(line, 0) + 1
    missing: List[str] = []
    for line in expected:
        if pool.get(line, 0) > 0:
            pool[line] -= 1
        else:
            missing.append(line)
    return missing


def _list_indent(lines: Sequence[str], lo: int, hi: int) -> Optional[int]:
    """The column this file writes its `sources:` list items at, or None if it
    has no items yet.

    The MINIMUM over the entries the text scanner can see, not their common
    value. A `- name:` line living inside a block scalar (a note quoting a
    catalog snippet — one of QA's hostile shapes, which merges correctly today)
    is necessarily indented DEEPER than the real items that contain it, so the
    minimum is the sequence's own column and the impostor cannot move it."""
    indents = [len(m.group(1)) for m in
               (_ITEM_RE.match(lines[i]) for i in range(lo, hi)) if m]
    return min(indents) if indents else None


def _reindent(block: Sequence[str], shift: int, where: str) -> List[str]:
    """Move a copied entry sideways by `shift` columns, relative structure
    intact. Blank lines stay blank (trailing whitespace is not content)."""
    if shift == 0:
        return list(block)
    if shift > 0:
        pad = " " * shift
        return [pad + ln if ln.strip() else ln for ln in block]
    drop = -shift
    out: List[str] = []
    for ln in block:
        if not ln.strip():
            out.append(ln)
            continue
        lead = len(ln) - len(ln.lstrip(" "))
        if lead < drop:
            raise RefreshMalformed(
                f"cannot copy {where} into a catalog indented {drop} column(s) "
                f"further left: the line {ln.strip()[:40]!r} would have to move "
                "past column zero, which would change what it belongs to. "
                "Nothing was changed.")
        out.append(ln[drop:])
    return out


def _detect_eol(raw: bytes) -> Tuple[str, bool]:
    """(the line ending this file uses, whether it uses more than one).

    QA F-7: `read_text().splitlines()` + `"\\n".join(...)` rewrote every line
    ending in the file, not just the merged-in ones, and `_verify` could not
    see it because the YAML parses identically either way. A refresh that
    silently converts a reader's CRLF catalog to LF is a whole-file rewrite
    wearing an add-only costume."""
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    cr = raw.count(b"\r") - crlf
    kinds = sum(1 for c in (crlf, lf, cr) if c)
    if crlf and crlf >= lf and crlf >= cr:
        return "\r\n", kinds > 1
    if cr and cr > lf:
        return "\r", kinds > 1
    return "\n", kinds > 1


def template_digest(path: Optional[Path] = None) -> str:
    p = path or paths.PROFILE_SOURCES_TEMPLATE
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------

def plan(profile: str, skip: Sequence[str] = (), anchor: Optional[Path] = None,
         template: Optional[Path] = None) -> RefreshPlan:
    """What a refresh WOULD do. Reads two files, writes nothing, ever."""
    slug, target = target_sources_file(profile, anchor)
    tpl = template or paths.PROFILE_SOURCES_TEMPLATE
    if not tpl.exists():
        raise RefreshMalformed(
            f"org catalog template missing: {tpl} — run from the prototype "
            "checkout.")

    tpl_lines = tpl.read_text(encoding="utf-8").splitlines()
    own_lines = target.read_text(encoding="utf-8").splitlines()

    tlo, thi = _sources_span(tpl_lines, str(tpl))
    # A reader's file with no top-level `sources:` key has no insertion point,
    # and finding that out here means the dry run says so instead of `apply`
    # discovering it mid-merge.
    olo, ohi = _sources_span(own_lines, str(target))
    # Same reason, for the same failure one layer down: a half-deleted ledger
    # is a refusal, and a refusal the dry run does not predict is not a dry
    # run. Called for its raise, not its value.
    _strip_ledger(own_lines)
    tpl_blocks = _blocks(tpl_lines, tlo, thi)

    # THE DRY RUN MUST PREDICT THE APPLY (QA F-3). Entries are copied out of
    # the template verbatim, and the template writes its list at two columns.
    # Copied into a catalog that writes its list at four (or at zero — the
    # commonest hand-written style, and valid YAML), the result is a sequence
    # with two different indentations, which is not valid YAML at all: the dry
    # run promised "WOULD ADD 68" and the apply hard-refused with a raw PyYAML
    # error that read as *your file is broken* when the reader's file was fine
    # and the MERGE was what broke. So the copy moves to the reader's column,
    # and the shift is computed HERE, once, on the object `apply` is handed —
    # there is no second implementation that could disagree with this one.
    tpl_indent = _list_indent(tpl_lines, tlo, thi)
    own_indent = _list_indent(own_lines, olo, ohi)
    shift = 0
    if own_indent is not None and tpl_indent is not None:
        shift = own_indent - tpl_indent

    try:
        tpl_cfg = config.load_sources(tpl)
    except config.SourcesParseError as exc:
        # The template is the ORG's file, so this is an org-side fault; say so
        # rather than handing the reader a traceback about a file they did not
        # write. (Same reasoning as `_verify`'s wrap: invalid YAML RAISES out
        # of load_sources, config.py:299, it does not land in `.problems`.)
        raise RefreshMalformed(
            f"the org catalog template is not readable as a sources.yaml "
            f"({tpl}): {exc}. This is an org-side fault, not a fault in your "
            "profile. Nothing was changed.") from exc
    try:
        own_cfg = config.load_sources(target)
    except config.SourcesParseError as exc:
        # QA F-4: the sibling of the bug the build caught in `_verify`. An
        # unparseable reader file escaped this call as a raw SourcesParseError,
        # so a catalog with a TAB in it exited 1 with a traceback while a
        # catalog with an unknown key exited 2 with a sentence — two registers
        # and two exit codes for one condition ("my catalog is broken"), and
        # the traceback arm dropped the "Nothing was changed" promise both
        # refusal classes are documented to carry.
        raise RefreshMalformed(
            f"{target} is not readable as a sources.yaml and will not be "
            f"edited until that is fixed: {exc}. Nothing was changed.") from exc
    if own_cfg.problems:
        raise RefreshMalformed(
            f"{target} has parse problems and will not be edited until they "
            f"are fixed: {'; '.join(own_cfg.problems)}. Nothing was changed.")

    tpl_src = {_key(s.name): s for s in tpl_cfg.sources}
    own_src = {_key(s.name): s for s in own_cfg.sources}
    own_names = set(own_src)

    # THE TWO READERS OF THE TEMPLATE MUST AGREE. PyYAML says which entries
    # exist; `_blocks` says which LINES to copy — and the text reader is the
    # one that decides what actually gets written, because it is the only one
    # that can carry comments through a merge. So an entry the text reader
    # cannot locate is a feed that would be silently never offered: no error,
    # no missing file, nothing on screen, just a reader who never sees a beat
    # the org thinks they were given. Refuse instead of skipping.
    in_text = {_key(n) for n, _s, _e in tpl_blocks}
    unseen = sorted(s.name for s in tpl_cfg.sources if _key(s.name) not in in_text)
    if unseen:
        raise RefreshMalformed(
            f"{tpl}: {len(unseen)} catalog entry/entries parse as YAML but "
            f"cannot be located as text ({', '.join(unseen)}) — every entry "
            "must lead with `- name:`. Refusing rather than silently declining "
            "to offer them. Nothing was changed.")

    led = _read_ledger(own_lines)
    remembered = led.keys
    skip_keys = {_key(s) for s in skip}

    p = RefreshPlan(slug=slug, sources_file=target, template_file=tpl,
                    template_digest=template_digest(tpl), ledger=led,
                    indent_shift=shift)

    for name, start, end in tpl_blocks:
        k = _key(name)
        if k in own_names:
            continue
        if k in remembered:
            # Offered before and not here now: the reader removed it, or
            # declined it. Either way it is answered, and the answer holds.
            if k in {_key(n) for n in led.declined}:
                p.previously_declined.append(name)
            else:
                p.reader_removed.append(name)
            continue
        if k in skip_keys:
            p.skipped.append(name)
            continue
        src = tpl_src.get(k)
        p.offers.append(Offer(
            name=name,
            lines=_reindent(tpl_lines[start:end], shift, repr(name)),
            tier=src.tier if src else "full",
            enabled=bool(src.enabled) if src else True,
            rss_url=src.rss_url if src else None,
        ))

    # Reader's own entries (adds, or entries the org has since dropped). Taken
    # from the PARSED file, not the text scan: this list is only reported, and
    # reporting should describe the sources that actually load.
    for src in own_cfg.sources:
        if _key(src.name) not in tpl_src:
            p.reader_only.append(src.name)

    # Shared names where the org now says something else. Reported, never applied.
    for k, tsrc in tpl_src.items():
        osrc = own_src.get(k)
        if osrc is None:
            continue
        for fname in ("rss_url", "tier", "enabled", "wire_syndication"):
            tv, ov = getattr(tsrc, fname), getattr(osrc, fname)
            if tv != ov:
                p.divergences.append(Divergence(osrc.name, fname, tv, ov))

    # A `--skip` naming something this refresh was never going to offer is
    # accepted — the reader has said something true about a feed they already
    # have, or already answered, or that the org catalog does not carry. But
    # silence there reads as "skipped, remembered", and it is neither (QA
    # F-10). One line, so a typo shows up as a typo.
    answered = {_key(n) for n in p.skipped}
    p.skip_unmatched = [s for s in skip if _key(s) not in answered]
    return p


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------

def _own_indent(p: RefreshPlan) -> str:
    """The column this reader's catalog writes its list at, as a prefix. The
    org's furniture is written at the reader's column, not the org's, so a
    zero-indent or four-space catalog reads as one file afterwards rather than
    two glued together (QA F-3)."""
    return " " * max(0, 2 + p.indent_shift)


def _render_adoption(p: RefreshPlan, when: str) -> List[str]:
    i = _own_indent(p)
    out = ["",
           f"{i}# =========================================================================",
           f"{i}# ADOPTED FROM THE ORG CATALOG {when} by `newslens profile refresh-catalog`.",
           f"{i}# Source: {p.template_file.name} @ sha256:{p.template_digest[:12]}",
           f"{i}#",
           f"{i}# Copied verbatim from the org catalog, each entry's own comments included.",
           f"{i}# This file is still YOURS: edit, disable or delete any of these. A feed you",
           f"{i}# delete is not offered back — the ledger at the end of this block remembers",
           f"{i}# what you have already been shown.",
           f"{i}# ========================================================================="]
    for offer in p.offers:
        out.extend(offer.lines)
    return out


def _render_ledger(led: Ledger, indent: str = "  ") -> List[str]:
    i = indent
    out = ["",
           f"{i}{LEDGER_BEGIN}",
           f"{i}# The record of which org-catalog feeds this profile has been OFFERED, so a",
           f"{i}# refresh never argues with your editing. A name listed here is never offered",
           f"{i}# again: one you adopted and later deleted stays deleted, one you skipped",
           f"{i}# stays skipped. These are comments — deleting a line simply un-remembers it,",
           f"{i}# and the next refresh will offer that feed again."]
    out.extend(f"{i}{h}" for h in led.history)
    for n in sorted(set(led.adopted), key=_key):
        out.append(f"{i}{_TAG_ADOPTED}{n}")
    for n in sorted(set(led.declined), key=_key):
        out.append(f"{i}{_TAG_DECLINED}{n}")
    out.append(f"{i}{LEDGER_END}")
    return out


def _verify_text(before_lines: Sequence[str], retained_lines: Sequence[str],
                 after_lines: Sequence[str]) -> None:
    """The half of constraint 2 that `_verify` structurally cannot see.

    `_verify` compares PARSED FIELDS — sources, the source-name set, interests,
    settings. It never compares COMMENTS, which is the one thing this whole
    module exists to protect: the reason the merge is textual at all is that a
    YAML round-trip "would silently delete every comment in the reader's file…
    Those comments ARE the catalog's documentation" (module docstring). A merge
    bug that ate them shipped green through every field check (QA F-2), and
    F-1 was the live instance of exactly that.

    Three questions, each answering a different way to lose a reader's file:

      (a) TOP-LEVEL KEYS. Did a whole block appear or disappear? This is F-1's
          class regardless of values — an `interests:` block that vanished and
          an `interests:` block that was always empty are indistinguishable to
          the parsed config, and identical for every new profile, whose
          interests ship empty.
      (b) LINES KEPT. Did anything the ledger strip legitimately kept survive
          the merge? Checked against the bytes actually on disk, not against
          the list we built, so a temp file some other process overwrote fails
          here rather than landing.
      (c) LINES REMOVED. The strip may only ever remove COMMENTS — its own
          sentinels, its own `#@` tags, and the prose between them. The moment
          it removes something that is not a comment it has eaten content that
          belongs to the reader.
    """
    before_keys = _toplevel_keys(before_lines)
    after_keys = _toplevel_keys(after_lines)
    if sorted(before_keys) != sorted(after_keys):
        lost = sorted(set(before_keys) - set(after_keys))
        gained = sorted(set(after_keys) - set(before_keys))
        raise RefreshMalformed(
            "the merged catalog does not have the same top-level blocks as "
            f"yours (removed: {lost or 'none'}; added: {gained or 'none'}) — "
            "refused, nothing was written. A refresh adds source entries "
            "inside `sources:`; it never adds or removes a block of your file.")

    missing = _lines_missing(retained_lines, after_lines)
    if missing:
        raise RefreshMalformed(
            f"the merge lost {len(missing)} line(s) of your file — refused, "
            f"nothing was written. First: {missing[0].strip()[:70]!r}. This "
            "verb only ever ADDS lines; comments are the catalog's "
            "documentation and are content here, not decoration.")

    removed = _lines_missing(before_lines, retained_lines)
    not_comments = [ln for ln in removed if ln.strip()
                    and not ln.strip().startswith("#")]
    if not_comments:
        raise RefreshMalformed(
            f"clearing the catalog-refresh ledger would have removed "
            f"{len(not_comments)} line(s) that are not comments — refused, "
            f"nothing was written. First: {not_comments[0].strip()[:70]!r}. "
            "The ledger is comments only, so removing it can only ever remove "
            "comments; anything else is your file.")


def _verify(before: config.SourcesConfig, after_path: Path,
            adopted: Sequence[str]) -> None:
    """Constraint 2, as a mechanism: the merged file must parse clean, must
    contain every pre-existing source UNCHANGED in every field, must add
    exactly the adopted names and nothing else, and must leave interests and
    settings alone. Called BEFORE the write lands; a failure means the
    temporary file is deleted and the reader's file was never touched."""
    try:
        after = config.load_sources(after_path)
    except config.SourcesParseError as exc:
        # NOT a `problems` entry: invalid YAML raises out of load_sources
        # (config.py:298) rather than being collected, so a merge that produced
        # unparseable text would otherwise escape this gate as a raw
        # SourcesParseError and reach the reader as a traceback. Caught here so
        # every failure of the merge has the same shape and the same promise:
        # the temp file goes, the reader's file was never touched.
        raise RefreshMalformed(
            f"the merged catalog is not valid YAML, so it was NOT written: "
            f"{exc}") from exc
    if after.problems:
        raise RefreshMalformed(
            "the merged catalog does not parse cleanly, so it was NOT written: "
            + "; ".join(after.problems))

    after_by = {_key(s.name): s for s in after.sources}
    for src in before.sources:
        got = after_by.get(_key(src.name))
        if got is None:
            raise RefreshMalformed(
                f"merge would have dropped your source {src.name!r} — refused, "
                "nothing was written.")
        if got != src:
            raise RefreshMalformed(
                f"merge would have changed your source {src.name!r} "
                f"({src} -> {got}) — this verb only adds. Refused, nothing was "
                "written.")

    expected = {_key(s.name) for s in before.sources} | {_key(n) for n in adopted}
    if set(after_by) != expected:
        unexpected = sorted(set(after_by) - expected)
        missing = sorted(expected - set(after_by))
        raise RefreshMalformed(
            f"merge produced the wrong source set (unexpected: {unexpected}, "
            f"missing: {missing}) — refused, nothing was written.")

    if (after.interests_broad != before.interests_broad
            or after.interests_granular != before.interests_granular):
        raise RefreshMalformed(
            "merge would have altered your interests — refused, nothing was "
            "written. Interests are never a refresh target.")
    if (after.threads_steer_selection != before.threads_steer_selection
            or after.tts_engine != before.tts_engine):
        raise RefreshMalformed(
            "merge would have altered your settings — refused, nothing was "
            "written. Settings are never a refresh target.")


def apply(p: RefreshPlan, today: Optional[str] = None) -> RefreshPlan:
    """Land the plan. Atomic: the merged text is written to a sibling temp
    file that is private to this process, checked there by BOTH gates
    (`_verify` on the parsed fields, `_verify_text` on the text and its
    comments), confirmed to still be a merge of what is on disk, and only then
    `os.replace`d over the reader's file — so a refusal at any point leaves the
    original byte-identical and no temp file behind."""
    if p.is_noop:
        p.applied = True
        p.adopted_now = []
        return p

    when = today or date.today().isoformat()
    try:
        before = config.load_sources(p.sources_file)
    except config.SourcesParseError as exc:
        raise RefreshMalformed(
            f"{p.sources_file} is not readable as a sources.yaml: {exc}. "
            "Nothing was changed.") from exc

    # ONE read of the bytes, and they are kept: they are the line-ending
    # evidence (F-7), the before-picture the text gate compares against (F-2),
    # and the thing the pre-replace check re-reads to notice it has been
    # overtaken (F-8). `raw.decode().splitlines()` is exactly what
    # `read_text().splitlines()` produced before — splitlines already treats
    # CRLF as one break — so the merge sees the same lines it always did.
    raw = p.sources_file.read_bytes()
    eol, mixed = _detect_eol(raw)
    p.line_ending, p.mixed_line_endings = eol, mixed
    orig_lines = raw.decode("utf-8").splitlines()

    led = _read_ledger(orig_lines)
    lines = _strip_ledger(orig_lines)
    _lo, hi = _sources_span(lines, str(p.sources_file))

    adopted = [o.name for o in p.offers]
    led.adopted.extend(adopted)
    led.declined.extend(p.skipped)
    led.history.append(
        f"{_TAG_REFRESH}{when} template=sha256:{p.template_digest[:12]} "
        f"adopted={len(adopted)} declined={len(p.skipped)}")

    insert: List[str] = []
    if p.offers:
        insert.extend(_render_adoption(p, when))
    insert.extend(_render_ledger(led, _own_indent(p)))
    merged = lines[:hi] + insert + lines[hi:]
    text = eol.join(merged) + eol

    # The temp file is PRIVATE to this process. It used to be one shared name,
    # which is how two racing refreshes produced QA's F-8: both wrote the same
    # `sources.yaml.refresh-tmp`, so one process could verify and land the
    # OTHER's merge while reporting its own adopted list — a success line for
    # work that was discarded. A gate that checks somebody else's bytes is not
    # a gate. (Still no lock, deliberately: this is a single-user app and a
    # lock is a redesign. What changed is that a lost race is now detected and
    # said out loud instead of reported as success.)
    tmp = p.sources_file.with_name(
        "{}.refresh-tmp.{}".format(p.sources_file.name, os.getpid()))
    try:
        tmp.write_bytes(text.encode("utf-8"))
        _verify(before, tmp, adopted)
        _verify_text(orig_lines, lines,
                     tmp.read_text(encoding="utf-8").splitlines())
        if p.sources_file.read_bytes() != raw:
            raise RefreshMalformed(
                "this catalog changed on disk while the refresh was running "
                "(another `refresh-catalog`, or a hand edit) — so the merge "
                "that was just checked is no longer a merge of what is there "
                "now. Nothing was written; re-run the refresh to see the "
                "current delta.")
        # `os.replace` is the write. It swaps a directory entry rather than
        # writing through the old inode, which is why a catalog that is also
        # known by another name (a hardlink) does not get written through —
        # see the hardlink guard in `target_sources_file` and the pin
        # `test_the_write_unlinks_rather_than_writing_through_a_hardlink`.
        # DISCLOSED, accepted (QA F-9): there is no fsync on the temp file or
        # on the parent directory. The rename is atomic because the temp is a
        # sibling, so a crash can never leave a half-merged catalog under the
        # real name — but a power loss inside the window can, on some
        # filesystems, leave a zero-length or truncated file there. The cost of
        # that is re-copying templates/profile-sources.yaml; the cost of fsync
        # is a durability contract this app does not otherwise make anywhere.
        os.replace(tmp, p.sources_file)
    finally:
        if tmp.exists():
            tmp.unlink()

    p.applied = True
    p.adopted_now = adopted
    p.ledger = led
    return p
