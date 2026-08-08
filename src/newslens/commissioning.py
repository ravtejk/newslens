"""THE COMMISSIONING — Stage-0 C1, the founding page a stranger's first run opens.

Binding spec: `design/mockup-v12-commissioning.html` in the product-org tree, as
amended at the principal's browser gate 2026-07-28 (terse leave/sched copy;
"topics" ratified over "subjects" wholesale). Its state blocks, its VOICE
inventory, its KILLED-ON-SIGHT list, its first-run state matrix and its
accessibility contract are the contract; every string ships byte-for-byte from
`labels.py`.

WHAT THIS PAGE IS. A freshly provisioned profile cannot rank — `run_rank`
refuses a reader with no tags, and that refusal is a CLI sentence naming a
profile and a filesystem path. Before C1 that sentence WAS a stranger's first
screen. This module is the door that refusal always meant: one page, in the
paper's own type, two acts and a button — pick topics, found Edition No. 1,
watch the honest half hour, and the edition opens.

THE SEAMS, AS RATIFIED (the mockup's ENGINEERING SEAMS block):

  1. THE PICKER WRITES INTERESTS to the profile's own sources.yaml — domain
     picks to the domain level, topic picks to the topic level, through the
     SHIPPED line-surgery editor (server.topic_add, the door M1 fix loop 1 F3
     opened for empty templates). The reader never sees that split; the catalog
     carries the level.

  2. BUILD-BLOCKING ORDERING — `commission()` writes the topics, RE-READS the
     file and verifies every one of them is back, and only then does its caller
     start the generate. There is no path here that starts a run on an
     unverified write, because the function returns a refusal instead of a
     go-ahead. Belt, not just braces: `unfit_for_readers()` keeps any refusal
     that names a path or a profile out of the failure panel even if one
     somehow reaches it.

  3. THE STAGE MAP — `reader_stage()` speaks reader-world stage words and drops
     the model name. It maps by PHASE KEY, inverted from
     generate.PROGRESS_LABELS at call time rather than copied, so a re-pin
     there cannot silently orphan a word here (and the suite fails loudly if a
     new phase arrives without one).

  4. THE WAIT SURVIVES A CLOSED TAB — and this module must not make that false.
     Every state below is DERIVED SERVER-SIDE from GEN_JOB's snapshot on each
     load; the page's script only polls that snapshot and reloads. Nothing about
     the run lives in the browser, so "Closing this page won't stop it" stays
     true.

  5. NL-90's scheduler opt-in is NOT BUILT (held at the principal's arm — it
     would write a schedule nothing reads). There is no scheduler markup, no
     preference write, and no place either could hide.

  6. NOTHING HERE MINTS A FOLLOW. No thread row, no memory write, no baseline
     enqueue happens on this page; the first write of that class is the
     reader's first tap in Edition No. 1.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from html import escape
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import catalog, config, coverage, labels, webui

# ---------------------------------------------------------------------------
# The first-run state matrix
# ---------------------------------------------------------------------------

PICKER = "picker"
WAITING = "waiting"
FAILED = "failed"


def has_published_edition(con: sqlite3.Connection) -> bool:
    """Has this profile EVER had an edition with a body?

    Deliberately NOT server._stories_for. That predicate answers "can this
    edition render", per row, and it reads the generation log — the right
    question for one date and the wrong one to ask of every row on every page
    load. This asks the first-run question: has a run ever landed a body at
    all. The distinction matters because ranking.persist() commits today's row
    at the RANK stage with narrative_text NULL, so row existence is not
    publication and a founding page keyed on it would flip to the app mid-run
    — thirty minutes before there is anything to read."""
    try:
        row = con.execute(
            "SELECT 1 FROM briefings WHERE narrative_text IS NOT NULL"
            " AND TRIM(narrative_text) <> '' LIMIT 1").fetchone()
    except sqlite3.Error:
        # A database too young to have the table cannot have published one.
        return False
    return row is not None


def first_run_state(con: sqlite3.Connection,
                    gen_state: Dict) -> Optional[str]:
    """PICKER / WAITING / FAILED while this profile is in its first run; None
    once it is out of it (the app renders).

    THE PREDICATE, AND THE ONE PLACE IT READS NARROWER THAN THE MATRIX. The
    mockup's matrix opens with "PROFILE HAS NO TOPICS -> the founding page, at
    every URL" and follows with "TOPICS PICKED, NOTHING GENERATED -> the
    founding page, act 3 live". Both rows describe a profile that has never
    produced an edition, and their union is exactly the test below. The one
    state where the two readings diverge is a profile that HAS a published
    edition and then loses its topics — a founder deleting his own tags, not a
    stranger — and there the founding page would be a lie: "Founding an
    edition" printed over a full archive. So the app renders there, and rank's
    own refusal (the founder-grade surface it has always been) stands. Flagged
    in the C1 build report rather than decided quietly."""
    if has_published_edition(con):
        return None
    state = (gen_state or {}).get("state")
    if state == "running":
        return WAITING
    if state == "error":
        return FAILED
    return PICKER


# ---------------------------------------------------------------------------
# SEAM 3 — the reader-world stage map
# ---------------------------------------------------------------------------

# phase key (generate.PROGRESS_LABELS' own keys) -> the word a reader gets.
# script/audio collapse to one word and persist/state to another, per the
# mockup: a reader watching a wait does not need the pipeline's joints.
PHASE_TO_READER_STAGE = {
    "ingest": labels.COMMISSION_STAGE_INGEST,
    "rank": labels.COMMISSION_STAGE_RANK,
    "analysis": labels.COMMISSION_STAGE_ANALYSIS,
    "narrative": labels.COMMISSION_STAGE_NARRATIVE,
    "editor": labels.COMMISSION_STAGE_EDITOR,
    "script": labels.COMMISSION_STAGE_RECORDING,
    "audio": labels.COMMISSION_STAGE_RECORDING,
    "persist": labels.COMMISSION_STAGE_SAVING,
    "state": labels.COMMISSION_STAGE_SAVING,
}


def _label_to_phase() -> Dict[str, str]:
    """generate.PROGRESS_LABELS, inverted, built fresh on each call.

    GEN_JOB stores the label generate already mapped the phase to; the reader
    word is keyed on the PHASE. Inverting the shipped table rather than keeping
    a second copy of it means a re-pin over there lands here for free, and a
    NEW phase over there shows up as a missing key that
    tests/test_stage0_c1_commissioning.py turns red."""
    from . import generate
    return {v: k for k, v in generate.PROGRESS_LABELS.items()}


def reader_stage(stage_label: Optional[str]) -> str:
    """The stage word for the ceremony. Never a phase key, never a model name.

    On the founder's own screen the shipped panel's internal vocabulary is a
    feature; on a stranger's most-watched surface it is the product talking to
    itself. `None` (a job that has started but not yet crossed a boundary) is
    the shipped "Starting…", reused rather than re-drafted."""
    if not stage_label:
        return labels.COMMISSION_STAGE_STARTING
    phase = _label_to_phase().get(stage_label)
    if phase is None:
        return labels.COMMISSION_STAGE_FALLBACK
    return PHASE_TO_READER_STAGE.get(phase, labels.COMMISSION_STAGE_FALLBACK)


# ---------------------------------------------------------------------------
# SEAM 2 — write, verify, and only then hand back a go-ahead
# ---------------------------------------------------------------------------

# A refusal that carries a filesystem path, a config filename or a profile
# name is a CLI sentence. It is correct for the terminal and it is the exact
# thing a stranger must never meet — the whole reason SEAM 2 is build-blocking.
# KEPT AS THE BELT, not as the defence: see _READER_SAFE_FAILURE below.
#
# C1 fix loop 2 (QA re-verify correction 1): the two `profile`/`interests` arms
# are widened from the CLI's PUNCTUATION to the WORDS themselves. QA's own leak
# class #3 — `rank failed: profile default has no interests` — is plain words
# behind a real phase prefix, so the allowlist passes it and the old quoted-slug
# arm never fired. Neither word is reader vocabulary: this page says *topics*
# and never names a profile, so a run sentence carrying either is the operator's
# grade of the fact by construction, and there is nothing honest to lose.
_CLI_SHAPED = re.compile(
    r"(/[^\s/]+/)"                     # any absolute-ish path segment
    r"|(\.ya?ml\b)|(\.db\b)"           # config/database filenames
    r"|(\binterests\b)"                # the YAML key, with or without its colon
    r"|(\bprofile\b)",                 # the CLI's noun for the reader's file
    re.IGNORECASE)

# THE ALLOWLIST (QA-6, 2026-07-30). A denylist could not close this and the
# proof is the ten classes it missed: a bare relative path, an OSError with a
# filename, an env var name, a module path, a backticked YAML key, a profile
# named without the CLI's quotes — and the three that decide the shape of the
# fix, A MODEL ID, A CREDENTIAL ERROR AND A DOLLAR FIGURE, on the first screen
# of a product whose whole posture is that it spends nothing on a stranger.
# Every regex in a denylist is a guess about the sentences an operator can
# write; there is no finite set of them.
#
# So the predicate is INVERTED. The run's own sentence renders only when it
# matches the ONE form the mockup blessed — a named pipeline stage failing, in
# plain words and digits — and anything else is omitted. The panel already
# knows how to omit, and omitting costs the reader nothing they can act on:
# they still get "Edition No. 1 didn't finish.", the outcome sentence, and Try
# again. The operator still gets the sentence, in the serve terminal, where it
# was always the right grade of prose (server._GenJob._run logs it).
#
# THE TAIL IS A TOKEN GRAMMAR, NOT A CHARSET (C1 fix loop 2, QA re-verify
# correction 1). The loop-1 charset `[A-Za-z0-9 ,'’-]` carried a comment
# claiming a model id, a credential fragment and a spend figure could not ride;
# QA measured the claim false, because '-' and unbounded digit runs JOIN tokens
# that a space would otherwise have separated. Behind a valid phase prefix these
# all rendered: `claude-opus-4-6`, `invalid x-api-key`, `api-anthropic-com`,
# `190000 input tokens`. Latent, not live — 0 of generate.py's 18 GenerateError
# templates reach it — but the comment was the thing that was wrong, and a
# comment that overstates a guard is how the next sentence gets written.
#
# So the tail is now a list of TOKENS separated by single spaces, and the
# separator is what does the work:
#   word   — unicode letters, at most ONE internal apostrophe or hyphen, so
#            `Most-Viewed` and `reader's` ride and `x-api-key`, `api-anthropic-com`
#            and `claude-opus-4-6` cannot (two joins, or a digit across one);
#   number — at most FOUR digits, so `12 of 37 feeds` rides and a token count or
#            a spend figure (`190000`) cannot;
#   & — — – ( ) , ; .  — the connectives the reader's OWN vocabulary needs.
# Still absent, and now by construction rather than by charset luck: '/', ':',
# '$', '_', backtick, bracket, quote — so a path, a module path, a "key: value"
# fragment, an env var and a dollar figure have no token to be. A '.' rides only
# as a token's tail (a sentence's full stop), never mid-token, so `sources.yaml`
# and `newslens.ranking.RankingError` are two tokens with no space and match
# nothing. '%' stays excluded; it is on the mockup's KILLED-ON-SIGHT list.
#
# WHAT IT STILL ADMITS, stated rather than implied: an honest English sentence
# that happens to name a real thing in plain words (`ingest failed: could not
# read sources`). That is the fail-safe direction working — no path, no
# extension, no credential shape, and `sources` is this page's own word.
_TAIL_WORD = r"[^\W\d_]{1,40}(?:['’-][^\W\d_]{1,40})?"   # ONE join, letters only
_TAIL_NUM = r"\d{1,4}"                                   # never a spend figure
_TAIL_TOKEN = r"\(?(?:%s|%s|&|—|–)[,;.)]?" % (_TAIL_WORD, _TAIL_NUM)
_READER_SAFE_FAILURE = re.compile(
    r"^([a-z][a-z0-9]{0,19}) failed: (%s(?: %s){0,30})$" % (_TAIL_TOKEN, _TAIL_TOKEN))


def _is_reader_safe_failure(text: str) -> bool:
    """The allowlist arm: `<phase> failed: <plain words>`, where <phase> is a
    phase the shipped pipeline actually has.

    The phase set is READ FROM generate.PROGRESS_LABELS rather than copied, the
    same derivation reader_stage() uses — a new phase over there is covered
    here for free, and a made-up phase name is not a pipeline sentence."""
    m = _READER_SAFE_FAILURE.match(text)
    if not m:
        return False
    try:
        from . import generate
    except Exception:       # noqa: BLE001 — unimportable means unvouchable
        return False
    return m.group(1) in generate.PROGRESS_LABELS


def unfit_for_readers(error: str) -> bool:
    """True when a run's own sentence may not be shown on the founding page.

    The mockup DOES render the run's own sentence ("ingest failed: 12 of 37
    feeds timed out") — an honest fact about the reader's morning. What it must
    never render is the CLI grade of the same channel. SEAM 2's ordering makes
    the no-interests refusal unreachable from this page; this predicate is the
    belt for every other way an operator sentence could reach the panel, and
    when it fires the panel omits the sentence rather than paraphrasing a
    failure it cannot vouch for.

    ALLOWLIST FIRST, denylist as the belt behind it: a sentence that matches
    the safe form is still refused if it carries a CLI shape. The belt earns
    its keep on CONTENT the grammar cannot see — `rank failed: profile default
    has no interests` is six plain words in the blessed form, and it is still
    the operator's sentence."""
    text = (error or "").strip()
    if not text:
        return False                    # nothing to hide; the panel omits anyway
    if _CLI_SHAPED.search(text):
        return True
    return not _is_reader_safe_failure(text)


# --- the WIRE's guard, sibling to the panel's ------------------------------
# QA-9 (HIGH, C1 fix loop 2). unfit_for_readers() guards ONE surface — the
# failure panel (_failed_html). The found act's refusal line is a second
# surface and it was guarded by nothing: nl_c1_js's foundEdition writes the
# server's string into #c1-refusal VERBATIM, _api_commission caught only
# catalog.CatalogError, and do_POST's catch-all answers {"error": str(exc)}.
# Reachable with the shipped code and no monkeypatch — server._yaml_edit wraps
# only config.load_sources(), so a read-only profile directory walks a real
# OSError out of topic_add and `[Errno 13] Permission denied: '/Users/…/
# sources.yaml.tmp'` renders on a stranger's first screen, at the one act
# SEAM 2 is build-blocking to protect.
#
# THE CODOMAIN IS THE GUARD. reader_refusal() does not inspect the sentence it
# is handed for danger — inspection is what a denylist does, and QA-6 already
# proved that class of defence cannot be finished. It answers only from a set
# this module composes: the five blessed constants, plus the ONE variable
# sentence RE-DERIVED from the (name, level) pairs the same call reported as
# landed. Nothing else can come out, so nothing else can go in. There is no
# charset to get wrong here and no regex to outgrow.
_BLESSED_REFUSALS = frozenset({
    labels.COMMISSION_WRITE_REFUSAL,
    labels.COMMISSION_VERIFY_REFUSAL,
    labels.COMMISSION_UNKNOWN_TOPIC,
    labels.COMMISSION_FOUND_REFUSAL,
    labels.STALENESS_REFUSAL,
})


def reader_refusal(text: str, written: Sequence[Tuple[str, str]] = ()) -> str:
    """The one string /api/commission is allowed to put on the wire.

    `written` is commission()'s third return — what it read back off disk — and
    it is the ONLY reason the partial sentence can be vouched for: the names in
    it came out of catalog.resolve(), i.e. they are the reader's own picks in
    the catalog's spelling, the same bytes the picker rendered. Re-deriving the
    sentence from those pairs and comparing for equality means the funnel never
    has to trust the string.

    Anything unrecognised — a raw exception, an OSError with a path, a refusal
    a future caller invents and nobody blessed — becomes COMMISSION_WRITE_REFUSAL.
    The operator's grade of the same fact belongs in the serve terminal; the
    caller logs it there (server._commission_answer), exactly as
    server._GenJob._run already does for a failed generate."""
    candidate = str(text or "")
    if candidate in _BLESSED_REFUSALS:
        return candidate
    if written and candidate == _refusal_for("", written):
        return candidate                # the partial sentence, rebuilt and matched
    return labels.COMMISSION_WRITE_REFUSAL


def _fold(name: str) -> str:
    """One folding rule for every name comparison in this module.

    Whitespace-collapsed and lowercased, exactly as catalog._key folds, so a
    name that resolves through the catalog and a name read back out of
    sources.yaml can never compare unequal over a double space."""
    return " ".join(str(name or "").split()).lower()


def _interest_keys(cfg) -> set:
    """Every interest in the file, BOTH levels, folded.

    Membership — not level — is what the reader asked for: they picked a name,
    and the catalog owns the level. This set is the one predicate both the skip
    step and the verify step read, which is the bug QA-4 found: the old code
    skipped on the union and then verified at the catalog level, so a name held
    at the OPPOSITE level was skipped forever and refused forever."""
    return ({_fold(n) for n in cfg.interests_broad}
            | {_fold(n) for n in cfg.interests_granular})


def _name_list(names: Sequence[str]) -> str:
    """'Inflation' · 'Inflation and ECB' · 'Inflation, ECB and Housing'."""
    items = list(names)
    if len(items) <= 1:
        return items[0] if items else ""
    return "%s%s%s" % (", ".join(items[:-1]), labels.COMMISSION_LIST_AND,
                       items[-1])


def _refusal_for(fallback: str, saved: Sequence[Tuple[str, str]]) -> str:
    """The refusal, chosen so it cannot be false.

    NEW READER-FACING COPY (flagged for the gate). Before this, a found act
    whose second write failed answered "your topics couldn't be saved, so
    nothing was started." while the first pick WAS on disk — and `written` came
    back empty, so no caller could have disclosed it either. The reader's next
    act was then taken against a file they believed was empty, and the orphaned
    topic silently personalised the edition that eventually generated. When
    something landed, the refusal NAMES IT."""
    if not saved:
        return fallback
    return labels.COMMISSION_PARTIAL_REFUSAL.format(
        topics=_name_list([n for n, _ in saved]))


def commission(
    names: Sequence[str],
    write_topic: Callable[[str, str], Tuple[bool, str]],
    load: Callable[[], "config.SourcesConfig"] = None,
    cat: Optional[catalog.Catalog] = None,
) -> Tuple[bool, str, List[Tuple[str, str]]]:
    """THE FOUND ACT. Returns (ok, refusal, written) — and `ok` is the ONLY
    licence to start a generate.

    `write_topic` is the shipped sources.yaml editor (server.topic_add), passed
    in rather than imported so this module never depends on the server module
    that renders it — and so a test can prove the ordering by handing in a door
    that fails.

    THE ORDER, WHICH IS THE POINT:
      1. resolve every pick against the catalog — an unknown name is refused
         before anything is written, which is how "typing filters this list; it
         never adds to it" is true in the MECHANISM and not only in the copy;
      2. write each new pick at its catalog level;
      3. RE-READ the file and verify every pick came back;
      4. only now answer ok=True.
    A failure at any step returns a reader-world refusal and NOTHING is
    started. No follow, no thread, no memory write happens anywhere in here
    (SEAM 6) — this function touches one YAML file and nothing else.

    `written` IS WHAT LANDED ON DISK, read back — not what we asked for. Three
    QA findings (2026-07-30) were one defect wearing three faces, and all three
    were the function believing its own intentions over the file:

      QA-3, the partial write. Second write fails, first is on disk, reader is
      told "your topics couldn't be saved" and `written` comes back []. Now the
      landed picks come back in `written` AND are named in the refusal.

      QA-4, the opposite-level deadlock. `already` was the union of both levels
      (so a name held at the other level was skipped) and the verify demanded
      the CATALOG level (where it was not) — an unrecoverable refusal, and
      'Systemic Risk' is exactly that shape in the shipped data. Membership is
      now the contract on BOTH sides: a name already in the reader's topics
      satisfies the pick at whichever level they already hold it, and no second
      copy is written at the other level (that would hand the ranker the same
      tag at two weights). The catalog level is still enforced for every name
      THIS call actually wrote — which is the half of the check that was
      protecting anything.

      QA-5, the second tab. Two loads read `before`, one writes first, and the
      editor tells the loser "already in your specific topics" — a SUCCESS
      condition (the name is present, which is what the caller wanted) that was
      being read as a write failure. A refused write is now re-checked against
      the file before it is believed."""
    load = load or config.load_sources
    cat = cat if cat is not None else catalog.load()

    picks: List[Tuple[str, str]] = []
    seen = set()
    for raw in names or []:
        hit = cat.resolve(str(raw or ""))
        if hit is None:
            return False, labels.COMMISSION_UNKNOWN_TOPIC, []
        if hit[0] in seen:
            continue
        seen.add(hit[0])
        picks.append(hit)                      # (canonical name, level)
    if not picks:
        return False, labels.COMMISSION_FOUND_REFUSAL, []

    before = load()
    already = _interest_keys(before)

    wrote: List[Tuple[str, str]] = []          # what THIS call put in the file
    write_failed = False
    for name, level in picks:
        if _fold(name) in already:
            continue                            # a re-submit is not an error
        ok, _msg = write_topic(name, catalog.LEVEL_TO_EDITOR_LEVEL[level])
        if ok:
            wrote.append((name, level))
            continue
        # THE DOOR SAID NO — which is not the same as "the write did not
        # happen". Between our read of `before` and this call, a second tab (or
        # a second thread; the page itself invites one, "Closing this page
        # won't stop it") may have written this exact name, and the editor
        # refuses a name that is already present. That refusal is a SUCCESS
        # condition. So re-read the file before believing the boolean.
        if _fold(name) in _interest_keys(load()):
            continue
        # A real failure. The editor already reverted its own edit (it
        # validates each one and restores the original), and its message names
        # the reader's LEVEL — a word this page has spent its whole existence
        # not saying. So: our own refusal, and we stop rather than keep
        # hammering a door that is answering no.
        write_failed = True
        break

    # 3. THE VERIFY. Reading the file back is the whole seam: a write that
    #    reported success and did not land would otherwise start a ~30-minute
    #    run that ends in the CLI refusal a stranger must never see.
    after = load()
    healthy = (not after.problems) and after.has_interests
    landed = _interest_keys(after) if healthy else set()
    # What the reader can be TOLD, because it is readable from the file right
    # now — the disclosure QA-3 found missing, and the value of `written`.
    saved = [(n, lv) for n, lv in picks if _fold(n) in landed]

    if write_failed:
        return False, _refusal_for(labels.COMMISSION_WRITE_REFUSAL, saved), saved
    if not healthy:
        return False, labels.COMMISSION_VERIFY_REFUSAL, []
    for name, level in picks:
        if _fold(name) not in landed:
            return (False,
                    _refusal_for(labels.COMMISSION_VERIFY_REFUSAL, saved),
                    saved)
    for name, level in wrote:
        stored = (after.interests_broad if level == catalog.DOMAIN
                  else after.interests_granular)
        if _fold(name) not in {_fold(n) for n in stored}:
            return (False,
                    _refusal_for(labels.COMMISSION_VERIFY_REFUSAL, saved),
                    saved)
    return True, "", picks


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _e(v) -> str:
    return escape(str(v if v is not None else ""), quote=False)


def _attr(v) -> str:
    return escape(str(v if v is not None else ""), quote=True)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "x"


def _long_date(d: datetime) -> str:
    """'Jul 27, 2026' — spelled without %-d, which is not portable."""
    return "%s %d, %d" % (d.strftime("%b"), d.day, d.year)


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


# --- act 2: the source pack, counted from the reader's OWN file --------------

def source_pack_sentence(cfg) -> str:
    """The four true numbers, derived — never typed into copy.

    Each clause is dropped when its count is zero, so the sentence can never
    claim "0 aggregators are off". Against the shipped profile template this
    renders (measured NL-142b, 2026-08-07): 69 outlets. 64 are fetched each
    morning. 4 are attribution-only by design. 1 source is off.

    THE OFF CLAUSE CAME BACK WITH THE NEUTRAL NOUN, and that pairing is what
    the `agg` branch below exists for: the one off source is CNN — a full-tier
    outlet whose feed has been frozen since 2023-04-25, disabled at the
    principal's ruling (a) 2026-08-06 — so calling it an aggregator would be a
    small lie in the one sentence whose whole job is being countable. Between
    NL-136 ① (which dropped Whatfinger Business and left the template with zero
    disabled sources) and that ruling the clause was ABSENT, which is the same
    zero-count guard doing its work on a real caller instead of a unit test.
    (Before the NL-135 slate this same builder rendered the mockup's original
    line: 42 outlets. 37 are fetched each morning. 4 are cited but never
    fetched. 1 aggregator is off.)"""
    total = len(cfg.sources)
    fetched = len(cfg.fetchable_sources)
    cited = len(cfg.reference_only_sources)
    off = list(cfg.disabled_sources)
    parts = ["%d %s." % (total, _plural(total, labels.COMMISSION_PACK_OUTLET,
                                       labels.COMMISSION_PACK_OUTLETS))]
    if fetched:
        parts.append("%d %s." % (fetched, _plural(
            fetched, labels.COMMISSION_PACK_FETCHED_ONE,
            labels.COMMISSION_PACK_FETCHED_MANY)))
    if cited:
        parts.append("%d %s." % (cited, _plural(
            cited, labels.COMMISSION_PACK_CITED_ONE,
            labels.COMMISSION_PACK_CITED_MANY)))
    if off:
        # "aggregator" only when every off source IS one; otherwise the neutral
        # noun. Calling a disabled full-tier outlet an aggregator would be a
        # small lie in a sentence whose whole job is being countable.
        agg = all(s.tier == "cautious" for s in off)
        one, many = ((labels.COMMISSION_PACK_OFF_AGG_ONE,
                      labels.COMMISSION_PACK_OFF_AGG_MANY) if agg else
                     (labels.COMMISSION_PACK_OFF_SRC_ONE,
                      labels.COMMISSION_PACK_OFF_SRC_MANY))
        parts.append("%d %s." % (len(off), _plural(len(off), one, many)))
    return " ".join(parts)


def _analyst_note(cfg) -> str:
    """'3 analyst newsletters are in the list; none are followed.'

    Both halves are observed: the newsletters are the template's own
    `analyst newsletter` notes, and 'none are followed' is
    `followed_analyst` being unset on all of them. If any IS followed the
    second clause is dropped rather than negated — a fresh profile has none,
    and inventing a "2 are followed" string for a state this page cannot reach
    would be copy nobody ruled."""
    sheets = [s for s in cfg.sources
              if str(s.note or "").lower().startswith("analyst newsletter")]
    if not sheets:
        return ""
    followed = [s for s in sheets if s.followed_analyst]
    noun = _plural(len(sheets), labels.COMMISSION_PACK_ANALYST_ONE,
                   labels.COMMISSION_PACK_ANALYST_MANY)
    if followed:
        return "%d %s." % (len(sheets), noun)
    return "%d %s%s" % (len(sheets), noun, labels.COMMISSION_PACK_ANALYST_NONE)


def _source_list_html(cfg) -> str:
    def li(s):
        q = (' <span class="q">%s</span>' % _e(labels.COMMISSION_PACK_HEADLINES_ONLY)
             if s.tier == "headline_only" else "")
        return "<li>%s%s</li>" % (_e(s.name), q)

    out = []
    fetched = cfg.fetchable_sources
    cited = cfg.reference_only_sources
    off = list(cfg.disabled_sources)
    if fetched:
        out.append('<p class="pack-group">%s · %d</p>'
                   % (_e(labels.COMMISSION_PACK_FETCHED), len(fetched)))
        out.append('<ul class="pack-list">%s</ul>'
                   % "".join(li(s) for s in fetched))
    if cited:
        out.append('<p class="pack-group">%s · %d</p>'
                   % (_e(labels.COMMISSION_PACK_CITED), len(cited)))
        out.append('<ul class="pack-list">%s</ul>'
                   % "".join(li(s) for s in cited))
    if off:
        out.append('<p class="pack-group">%s · %d</p>'
                   % (_e(labels.COMMISSION_PACK_OFF), len(off)))
        out.append('<ul class="pack-list">%s</ul>' % "".join(li(s) for s in off))
    note = _analyst_note(cfg)
    if note:
        out.append('<p class="pack-note">%s</p>' % _e(note))
    out.append('<p class="pack-note">%s</p>'
               % _e(labels.COMMISSION_SOURCES_SETTINGS))
    return "".join(out)


# --- act 1: the picker ------------------------------------------------------

def _pick_row(name: str, level: str, idx: str, checked: bool = False,
              cov: "Optional[coverage.NameState]" = None) -> str:
    """One native checkbox wearing the product's mark.

    The input stays in the DOM with every native behaviour intact (space
    toggles, state announced, group semantics from the fieldset); the ○/● is
    drawn on an aria-hidden sibling — decoration ON a real control, never
    instead of one. The picked state carries three channels: native checked,
    the mark, and the weight of the name.

    EVERY ATTRIBUTE VALUE IS QUOTED (QA-1, BLOCKER, 2026-07-30). `_attr()`
    escapes but does not add the quotes, and an unquoted HTML attribute value
    terminates at the FIRST WHITESPACE — so `value=Central Bank Policy` parsed
    as value="Central" plus two invented boolean attributes, and 54 of the 66
    catalog entries were unpickable in a browser: the reader ticked a name the
    page had just rendered and the found act answered "one of those topics
    isn't in the list". `data-name` broke the filter the same way, which is how
    typing `bank` told a reader that *Central Bank Policy* did not exist while
    it was on screen. The quotes are the whole fix; the escaping was already
    right (`"` is escaped to `&quot;`).

    `checked` is QA-7: the founding page is reachable repeatedly before the
    first edition publishes, and a picker that renders 66 empty circles over a
    non-empty file hides state the reader already owns.

    `cov` is NL-135 Q1: the coverage state for THIS name, computed live from
    the shipped feed→topic map plus the reader's own enabled sources. It lands
    two ways — a `data-cov` attribute the client counts without re-deriving
    anything, and a badge the reader can read. The badge sits INSIDE the label,
    so it joins the checkbox's accessible name: a screen-reader user hears
    "Vaccine Policy, No source in your list covers this regularly" as one
    control, which is the point — the caveat belongs to the pick, not to the
    page. (Flagged for design/a11y review; the alternative, an
    aria-describedby sibling outside the label, buys quieter announcement at
    the cost of the caveat being skippable.)"""
    badge = ""
    if cov is not None:
        if cov.state == coverage.UNSERVED:
            badge = labels.COMMISSION_COV_NONE
        elif cov.state == coverage.HEADLINES:
            badge = labels.COMMISSION_COV_HEADLINES
        elif level == catalog.DOMAIN:
            # Positive claims are INCLUSION and stop at the domain grain — see
            # labels.COMMISSION_COV_ONE. A topic row says nothing when served.
            badge = "%d %s" % (cov.served_count,
                               labels.COMMISSION_COV_ONE if cov.served_count == 1
                               else labels.COMMISSION_COV_MANY)
    return (
        '<label class="pick">'
        '<input class="pk" type="checkbox" id="pk-%s" value="%s" '
        'data-level="%s" data-name="%s" data-cov="%s"%s onchange="pickChanged()">'
        '<span class="mark" aria-hidden="true"></span>'
        '<span class="pick-name">%s</span>%s</label>'
    ) % (idx, _attr(name), _e(level), _attr(name.lower()),
         _attr(cov.state if cov is not None else ""),
         " checked" if checked else "", _e(name),
         ('<span class="cov">%s</span>' % _e(badge)) if badge else "")


def _catalog_html(cat: catalog.Catalog, saved: Optional[set] = None,
                  cov: Optional[coverage.CoverageState] = None) -> str:
    saved = saved or set()
    out = ['<div class="cat" id="cat">']
    for d in cat.domains:
        sid = "subs-%s" % _slug(d.name)
        out.append('<fieldset class="dom" data-domain="%s">'
                   % _attr(d.name.lower()))
        out.append('<legend class="vh">%s</legend>' % _e(d.name))
        out.append(_pick_row(d.name, catalog.DOMAIN, _slug(d.name),
                             _fold(d.name) in saved,
                             cov.of(d.name) if cov else None))
        if d.topics:
            out.append(
                '<button class="dom-more" type="button" aria-expanded="false" '
                'aria-controls="%s" onclick="toggleDomain(this)">%s %d %s</button>'
                % (sid, _e(labels.COMMISSION_SHOW_NARROWER), len(d.topics),
                   _e(labels.COMMISSION_NARROWER_SUFFIX)))
            out.append('<ul class="subs" id="%s" hidden>' % sid)
            for t in d.topics:
                out.append('<li>%s</li>'
                           % _pick_row(t, catalog.TOPIC, _slug(t),
                                       _fold(t) in saved,
                                       cov.of(t) if cov else None))
            out.append('</ul>')
        out.append('</fieldset>')
    out.append('</div>')
    return "".join(out)


def saved_picks(cfg, cat: catalog.Catalog) -> List[str]:
    """The catalog entries this profile's sources.yaml already holds.

    Read at BOTH levels, because the reader picked a NAME — the level is the
    catalog's business and never theirs. Anything in the file that is not a
    catalog entry (the founder's four local tags, say) renders no row here, so
    it is not counted: the number this returns is exactly the number of ticked
    boxes on the page, which is what keeps the server-rendered count line and
    the client's own recount from ever disagreeing."""
    saved = _interest_keys(cfg)
    return [n for n in cat.names() if _fold(n) in saved]


def _count_line(n_picked: int) -> str:
    """"Nothing picked yet." / "1 topic picked." / "3 topics picked."

    The SAME derivation the client's pickChanged() runs, from the same three
    label constants — so a page rendered over a file that already holds topics
    states the truth before a single byte of script executes. Server-rendering
    it is the other half of QA-7: `checked` boxes under a line reading "Nothing
    picked yet." would just move the lie one element down the page."""
    if n_picked <= 0:
        return labels.COMMISSION_COUNT_NONE
    return "%d %s" % (n_picked, labels.COMMISSION_COUNT_ONE if n_picked == 1
                      else labels.COMMISSION_COUNT_MANY)


def _coverage_line(n_unserved: int) -> str:
    """"You picked 3 topics your sources don't cover regularly. Briefings will
    lean on general news there until sources are added."

    The SAME derivation the client's pickChanged() runs, from the same label
    constants — the count-line precedent, for the same reason: the founding
    page is reachable over a file that already holds picks, and a server-
    rendered page that stays silent about them until script runs would tell a
    reader with JavaScript off nothing at all.

    Empty string at zero. There is no cheerful inverse ("all your topics are
    covered!") and there must not be: that is a sufficiency claim, and a static
    map cannot earn it."""
    if n_unserved <= 0:
        return ""
    return "%s %d %s %s" % (
        labels.COMMISSION_COV_UNSERVED_HEAD, n_unserved,
        labels.COMMISSION_COV_UNSERVED_ONE if n_unserved == 1
        else labels.COMMISSION_COV_UNSERVED_MANY,
        labels.COMMISSION_COV_UNSERVED_TAIL)


def _unclassified_line(cov: "Optional[coverage.CoverageState]") -> str:
    """"2 sources in your list aren't classified yet, so they aren't counted
    above." — the honesty valve, and the only line here the client never
    recomputes (it is a property of the FILE, not of the ticks).

    A fresh profile renders nothing: its list is exactly the mapped catalog.
    A reader who hand-adds an outlet gets this, because every absence badge on
    the page is then a claim made over sources the map has not read."""
    if not cov or not cov.unclassified:
        return ""
    n = len(cov.unclassified)
    return "%d %s" % (n, labels.COMMISSION_COV_UNKNOWN_ONE if n == 1
                      else labels.COMMISSION_COV_UNKNOWN_MANY)


def _coverage_state(cfg, cat: catalog.Catalog):
    """Coverage state, or None if the map cannot be read.

    DEGRADES SILENT ON PURPOSE, and this is the one place in the module that
    does: every badge this powers is a CLAIM, and a page that cannot read the
    map has no basis for any of them. Rendering "No source covers this" from a
    failed load would be the fabrication the mechanism exists to prevent, so a
    broken map costs the reader the badges and costs nobody the truth. The
    suite is where a malformed map is supposed to be caught (coverage.load
    raises CoverageError loudly, and tests/test_nl135_slate_land.py runs it
    against the shipped files)."""
    try:
        return coverage.state(cfg, cat=cat)
    except (coverage.CoverageError, catalog.CatalogError, OSError):
        return None


def _picker_html(cat: catalog.Catalog, cfg) -> str:
    n = cat.entry_count
    already = saved_picks(cfg, cat)
    n_picked = len(already)
    cov = _coverage_state(cfg, cat)
    n_unserved = len(cov.unserved_among(already)) if cov else 0
    return """
<section class="act" aria-labelledby="c1-topics">
  <h2 class="act-h" id="c1-topics">{head}</h2>
  <p class="act-say">{say}</p>
  <p class="note">{note}</p>
  <label class="filter-lab" for="c1-filter">{filter_label}</label>
  <input class="filter-in" id="c1-filter" type="search" autocomplete="off"
         aria-describedby="c1-filter-status" oninput="filterCatalog()">
  <p class="filter-status" id="c1-filter-status" role="status"
     data-total="{n}">{n} {topics_word}.</p>
  {catalog_html}
  <p class="count" id="c1-count">{count}</p>
  <p class="consequence" id="c1-consequence"{consequence_hidden}>{consequence}</p>
  <p class="coverage" id="c1-coverage"{coverage_hidden}>{coverage}</p>
  <p class="cov-note" id="c1-cov-note"{unclassified_hidden}>{unclassified}</p>
</section>
<section class="act" aria-labelledby="c1-sources">
  <h2 class="act-h" id="c1-sources">{sources_head}</h2>
  <p class="pack-fact">{pack}</p>
  <details class="pack"><summary>{show_list}</summary>{pack_list}</details>
</section>
<section class="act" aria-label="{found}">
  <button class="cta" type="button" id="c1-found" onclick="foundEdition()">{found}</button>
  <p class="cta-sub">{found_sub}</p>
  <p class="act-refusal" id="c1-refusal" role="status"></p>
</section>""".format(
        head=_e(labels.COMMISSION_TOPICS_HEAD),
        say=_e(labels.COMMISSION_TOPICS_SAY),
        note=_e(labels.COMMISSION_TOPICS_NOTE),
        filter_label=_e(labels.COMMISSION_FILTER_LABEL),
        n=n,
        topics_word=_e(_plural(n, labels.COMMISSION_TOPIC_WORD,
                               labels.COMMISSION_TOPICS_WORD)),
        catalog_html=_catalog_html(cat, {_fold(x) for x in already}, cov),
        count=_e(_count_line(n_picked)),
        # FLAG ⑤ as ruled and as the client re-computes it: the consequence
        # renders at 1–2 picks only.
        consequence_hidden="" if n_picked in (1, 2) else " hidden",
        consequence=_e(labels.COMMISSION_CONSEQUENCE),
        # NL-135 Q1: hidden at zero unserved picks, and it stays an element in
        # the DOM either way so the client can fill it without building markup.
        coverage_hidden="" if n_unserved else " hidden",
        coverage=_e(_coverage_line(n_unserved)),
        unclassified_hidden="" if (cov and cov.unclassified) else " hidden",
        unclassified=_e(_unclassified_line(cov)),
        sources_head=_e(labels.COMMISSION_SOURCES_HEAD),
        pack=_e(source_pack_sentence(cfg)),
        show_list=_e(labels.COMMISSION_SOURCES_SHOW),
        pack_list=_source_list_html(cfg),
        found=_e(labels.COMMISSION_FOUND),
        found_sub=_e(labels.COMMISSION_FOUND_SUB),
    )


# --- C3: the wait, and the wait that failed ---------------------------------

def _record_line(gen_state: Dict) -> str:
    started = (gen_state or {}).get("started_at")
    when = None
    if started:
        try:
            when = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            when = when.astimezone()
        except (TypeError, ValueError):
            when = None
    return "%s %s" % (labels.COMMISSION_RECORD_BEGAN,
                      _long_date(when or datetime.now()))


def _waiting_html(gen_state: Dict) -> str:
    """The vigil. The stage word sits in role="status"; THE CLOCKS DO NOT SIT
    IN ANY LIVE REGION — a counter that announces every tick is a screen-reader
    denial of service, and the shipped panel already ticks between polls. (The
    mockup's own markup puts role="status" on the paragraph that contains the
    clocks; its accessibility contract, which is the build-binding half, says
    the label only. The contract wins; the divergence is in the build report.)"""
    stage = reader_stage((gen_state or {}).get("stage"))
    total = (gen_state or {}).get("total_elapsed_s") or 0
    stage_el = (gen_state or {}).get("stage_elapsed_s") or 0
    return """
<div class="panel" id="c3-waiting">
  <h2>{head}</h2>
  <p>{sub}</p>
  <p class="live" id="c3-live" data-total="{total}" data-stage-el="{stage_el}">
    <span class="stage" id="c3-stage" role="status">{stage}</span><span
      class="sep">·</span><span id="c3-stage-clock"></span><span
      class="sep">·</span>{total_word} <span id="c3-total-clock"></span>
  </p>
  <p class="leave">{leave}</p>
  <p class="record-line">{record}</p>
</div>""".format(
        head=_e(labels.COMMISSION_WAIT_HEAD),
        sub=_e(labels.COMMISSION_FOUND_SUB),
        total=total, stage_el=stage_el,
        stage=_e(stage),
        total_word=_e(labels.COMMISSION_TOTAL),
        leave=_e(labels.COMMISSION_WAIT_LEAVE),
        record=_e(_record_line(gen_state)),
    )


def _failed_html(gen_state: Dict, outcome: str) -> str:
    error = str((gen_state or {}).get("error") or "")
    err_html = ("" if (not error or unfit_for_readers(error))
                else '<p class="err">%s</p>' % _e(error))
    return """
<div class="panel" id="c3-failed">
  <h2>{head}</h2>
  {err}
  <p>{outcome}</p>
  <button class="cta-quiet" type="button" onclick="foundAgain()">{again}</button>
  <p class="record-line">{record}</p>
</div>""".format(
        head=_e(labels.COMMISSION_FAIL_HEAD),
        err=err_html,
        outcome=_e(outcome),
        again=_e(labels.COMMISSION_FAIL_TRY_AGAIN),
        record=_e(_record_line(gen_state)),
    )


# --- the page ---------------------------------------------------------------

def nl_c1_js() -> str:
    """The client's copy blob, read from labels.py at RENDER time.

    Same contract webui/server already keep for NL_LABELS: the script holds no
    string of its own, so a copy re-pin in the table lands on the client too
    and a monkeypatch of any constant shows up in rendered output — the red
    test only the wiring can flip."""
    return "window.NL_C1 = %s;" % json.dumps({
        "countNone": labels.COMMISSION_COUNT_NONE,
        "countOne": labels.COMMISSION_COUNT_ONE,
        "countMany": labels.COMMISSION_COUNT_MANY,
        "refusal": labels.COMMISSION_FOUND_REFUSAL,
        "show": labels.COMMISSION_SHOW_NARROWER,
        "hide": labels.COMMISSION_HIDE_NARROWER,
        "narrower": labels.COMMISSION_NARROWER_SUFFIX,
        "topicWord": labels.COMMISSION_TOPIC_WORD,
        "topicsWord": labels.COMMISSION_TOPICS_WORD,
        "matchOne": labels.COMMISSION_FILTER_MATCH_ONE,
        "matchMany": labels.COMMISSION_FILTER_MATCH_MANY,
        "noMatch": labels.COMMISSION_FILTER_NO_MATCH,
        # NL-135 Q1 — the client re-derives the coverage summary from the same
        # words the server rendered it with. No coverage RULE crosses the wire:
        # the client counts `data-cov="unserved"` on the boxes the server
        # already stamped, so the map, the fetchability rule and the topic
        # inheritance all stay server-side and there is exactly one of each.
        "covHead": labels.COMMISSION_COV_UNSERVED_HEAD,
        "covOne": labels.COMMISSION_COV_UNSERVED_ONE,
        "covMany": labels.COMMISSION_COV_UNSERVED_MANY,
        "covTail": labels.COMMISSION_COV_UNSERVED_TAIL,
    })


def render(state: str, cfg, gen_state: Dict,
           outcome: str = "", cat: Optional[catalog.Catalog] = None,
           staleness_banner: str = "") -> str:
    """The founding page in one of its three states. Server-rendered whole: the
    script polls and reloads, it never builds a state.

    `staleness_banner` is the app's own banner, passed through unchanged. It
    belongs here for the same reason it belongs on Today: on a stale server the
    found act REFUSES (see server._api_commission), and a refusal whose remedy
    renders on a different page than the refusal is not a remedy. Surfaced by
    the suite, not by a review — tests/test_serverside_batch_qa_20260717.py's
    reading-stays-untouched pin found the founding page missing it."""
    cat = cat if cat is not None else catalog.load()
    if state == WAITING:
        body = _waiting_html(gen_state)
    elif state == FAILED:
        body = _failed_html(gen_state, outcome)
    else:
        body = _picker_html(cat, cfg)
    return PAGE.format(
        css=CSS,
        staleness_banner=staleness_banner,
        skip=_e(SKIP_LINK),
        wordmark="NewsLens",
        mast=_e("%s · %s" % (labels.COMMISSION_MASTHEAD,
                             _long_date(datetime.now()))),
        body=body,
        c1_labels_js=nl_c1_js(),
        js=JS,
    )


# ---------------------------------------------------------------------------
# The page's own shell, styles and script
# ---------------------------------------------------------------------------

# Implementer-drafted (the mockup specifies the CONTROL — "skip link" heads its
# tab-order walk — but not its words). Flagged in the build report.
SKIP_LINK = "Skip to topics"

# DIRECTION-v5 §1 tokens come from webui.TOKENS — ONE declaration for both
# pages (DESIGN_SYSTEM.md: tokens live in variables; two files declaring the
# same hexes is how they drift). Everything below is the v12 mockup's own
# stylesheet, product-frame rules only — none of its annotation layer.
CSS = webui.TOKENS + """
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--paper); color: var(--ink);
  font-family: var(--font-sans); font-size: 1rem; line-height: 1.62;
  -webkit-font-smoothing: antialiased; }
a { color: var(--terra); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { color: var(--terra-deep); }
a:focus-visible, button:focus-visible, summary:focus-visible,
input:focus-visible, .pk:focus-visible + .mark {
  outline: 3px solid var(--terra-deep); outline-offset: 2px; }
.skip-link { position: absolute; left: -9999px; top: 0; background: var(--ink);
  color: var(--paper); padding: 0.5rem 1rem; z-index: 50; }
.skip-link:focus { left: 0.5rem; top: 0.5rem; }
/* The staleness banner, styled exactly as the app styles it — same markup from
   the same builder, so the one surface a stale server must not hide from is
   not a second design. --danger's ONLY other lawful use on this page is a
   generation failure. */
.staleness-banner { position: sticky; top: 0; z-index: 60; background: var(--danger);
  color: #FFF; padding: 0.7rem 1.1rem; font-size: 0.9rem; line-height: 1.45; text-align: center; }
.staleness-banner strong { font-weight: 600; }
.staleness-banner code { font-family: var(--font-mono); font-size: 0.85em;
  background: rgba(255,255,255,0.2); padding: 0.05rem 0.4rem; border-radius: 4px; }
.sheet { max-width: 46rem; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
.vh { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }

/* ---- masthead ---- */
.wordmark { font-family: var(--font-display); font-weight: 700; font-size: 1.5rem;
  letter-spacing: 0.02em; margin: 0; line-height: 1.1; }
.mast-line { font-family: var(--font-mono); font-size: 0.7rem; letter-spacing: 0.12em;
  color: var(--ink-faint); margin: 0.35rem 0 0; text-transform: uppercase; }
.mast-rule { border: none; border-top: 1px solid var(--rule); margin: 0.9rem 0 1.2rem; }

/* ---- the acts ---- */
.act { margin: 0 0 1.9rem; }
.act-h { font-family: var(--font-display); font-weight: 700; font-size: 1.35rem;
  line-height: 1.2; margin: 0 0 0.25rem; }
.act-say { font-size: 0.95rem; margin: 0 0 0.45rem; }
.note { font-size: 0.78rem; font-style: italic; color: var(--ink-faint);
  margin: 0 0 0.9rem; max-width: 34rem; }
.count { font-family: var(--font-mono); font-size: 0.72rem; letter-spacing: 0.06em;
  color: var(--ink-soft); margin: 0.7rem 0 0; }
.consequence { font-size: 0.78rem; font-style: italic; color: var(--ink-faint);
  margin: 0.25rem 0 0; max-width: 34rem; }
.consequence[hidden] { display: none; }
/* NL-135 Q1 — the coverage summary sits with the consequence line and reads
   the same: a quiet statement of fact, not an alert. Nothing here is red,
   because an unserved pick is not the reader's mistake — it is ours. */
.coverage { font-size: 0.78rem; font-style: italic; color: var(--ink-faint);
  margin: 0.25rem 0 0; max-width: 34rem; }
.coverage[hidden] { display: none; }
.cov-note { font-size: 0.72rem; font-style: italic; color: var(--ink-faint);
  margin: 0.2rem 0 0; max-width: 34rem; }
.cov-note[hidden] { display: none; }

/* ---- the filter: suggestions-only made mechanical ---- */
.filter-lab { display: block; font-size: 0.8rem; color: var(--ink-soft); margin: 0 0 0.25rem; }
.filter-in { font-family: var(--font-sans); font-size: 0.95rem; color: var(--ink);
  background: var(--paper); border: 1px solid var(--ink-faint); border-radius: 0;
  padding: 0.45rem 0.55rem; width: 100%; max-width: 22rem; }
.filter-status { font-family: var(--font-mono); font-size: 0.7rem; letter-spacing: 0.06em;
  color: var(--ink-faint); margin: 0.35rem 0 0.9rem; }

/* ---- the picker ---- */
.cat { border-top: 1px solid var(--rule); }
fieldset.dom { border: none; margin: 0; padding: 0; border-bottom: 1px solid var(--rule); }
fieldset.dom[hidden] { display: none; }
.pick { display: flex; align-items: baseline; gap: 0.5rem; padding: 0.62rem 0;
  cursor: pointer; min-height: 44px; }
.pick[hidden] { display: none; }
.pk { position: absolute; opacity: 0; width: 1px; height: 1px; margin: 0; }
.mark { flex: 0 0 auto; line-height: 1.3; }
.mark::before { content: "\\25CB"; color: var(--ink-faint); font-size: 0.95rem; }
.pk:checked + .mark::before { content: "\\25CF"; color: var(--terra); }
.pick-name { font-size: 0.98rem; color: var(--ink-soft); min-width: 0; }
.pk:checked ~ .pick-name { color: var(--ink); font-weight: 700; }
/* The per-name coverage badge. Deliberately the quietest thing on the row:
   it qualifies the name, it does not shout at it. */
.cov { font-size: 0.72rem; font-style: italic; color: var(--ink-faint);
  margin-left: auto; padding-left: 0.6rem; text-align: right; }
.dom > .pick > .pick-name { font-family: var(--font-display); font-size: 1.05rem; }
.dom-more { background: none; border: none; padding: 0.3rem 0 0.55rem 1.45rem;
  cursor: pointer; font-family: var(--font-sans); font-size: 0.82rem;
  color: var(--terra); text-align: left; }
.dom-more:hover { color: var(--terra-deep); text-decoration: underline; }
.dom-more[hidden] { display: none; }
.subs { list-style: none; margin: 0 0 0.5rem; padding: 0 0 0 1.45rem; }
.subs[hidden] { display: none; }
.subs .pick { padding: 0.5rem 0; min-height: 40px; }
.subs .pick-name { font-size: 0.92rem; }
li[hidden] { display: none; }

/* ---- the source pack ---- */
.pack-fact { font-size: 0.9rem; margin: 0 0 0.7rem; max-width: 34rem; }
details.pack { border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
  padding: 0.6rem 0; }
details.pack > summary { font-size: 0.85rem; color: var(--terra); cursor: pointer; }
.pack-group { font-family: var(--font-mono); font-size: 0.68rem; letter-spacing: 0.08em;
  color: var(--ink-faint); margin: 0.9rem 0 0.25rem; }
.pack-list { list-style: none; margin: 0; padding: 0; font-size: 0.88rem; color: var(--ink-soft); }
.pack-list li { padding: 0.12rem 0; }
.pack-list .q { color: var(--ink-faint); font-size: 0.8rem; }
.pack-note { font-size: 0.78rem; font-style: italic; color: var(--ink-faint); margin: 0.9rem 0 0.2rem; }

/* ---- the found act ---- */
.cta { font-family: var(--font-sans); font-size: 1rem; font-weight: 700;
  color: var(--paper); background: var(--terra); border: none; border-radius: 0;
  padding: 0.75rem 1.15rem; cursor: pointer; }
.cta:hover { background: var(--terra-deep); }
.cta-sub { font-size: 0.8rem; color: var(--ink-faint); margin: 0.5rem 0 0; }
.cta-quiet { font-family: var(--font-sans); font-size: 0.92rem; font-weight: 700;
  color: var(--terra); background: none; border: 1px solid var(--terra);
  border-radius: 0; padding: 0.5rem 0.85rem; cursor: pointer; }
.cta-quiet:hover { color: var(--terra-deep); border-color: var(--terra-deep); }
/* the refusal carries NO mark: ○ already means "not picked" on this page, and
   one glyph may not carry two meanings on one screen. */
.act-refusal { font-size: 0.95rem; color: var(--ink); margin: 0.7rem 0 0; max-width: 34rem; }
.act-refusal:empty { margin: 0; }

/* ---- the wait ---- */
.panel { padding: 0.4rem 0 0; }
.panel h2 { font-family: var(--font-display); font-weight: 700; font-size: 1.6rem;
  line-height: 1.15; margin: 0 0 0.4rem; }
.panel p { font-size: 0.95rem; margin: 0 0 0.7rem; max-width: 34rem; }
.live { font-family: var(--font-mono); font-size: 0.76rem; letter-spacing: 0.06em;
  color: var(--ink-faint); border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule); padding: 0.55rem 0; margin: 0.2rem 0 1rem; }
.live .stage { color: var(--ink); font-weight: 700; }
.live .sep { margin: 0 0.45rem; color: var(--rule); }
.leave { font-size: 0.88rem; color: var(--ink-soft); margin: 0 0 0.4rem; max-width: 34rem; }
.err { font-size: 0.92rem; color: var(--danger); margin: 0 0 0.6rem; max-width: 34rem; }
.record-line { font-family: var(--font-mono); font-size: 0.68rem; letter-spacing: 0.1em;
  color: var(--ink-faint); margin: 1.6rem 0 0; text-transform: uppercase; }

/* ---- forced colors: give the native control back, drop the drawn mark ---- */
@media (forced-colors: active) {
  .pk { position: static; opacity: 1; width: auto; height: auto; margin: 0 0.4rem 0 0; }
  .mark { display: none; }
  .cta { border: 1px solid; }
}
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NewsLens</title>
<style>{css}</style>
</head>
<body>
<a class="skip-link" href="#main">{skip}</a>
{staleness_banner}
<main id="main" tabindex="-1" class="sheet">
<p class="wordmark">{wordmark}</p>
<p class="mast-line">{mast}</p>
<hr class="mast-rule">
{body}
</main>
<script>{c1_labels_js}</script>
<script>{js}</script>
</body>
</html>"""

JS = r"""
var NL_C1 = (window.NL_C1 || {});
function picked() {
  var out = [];
  document.querySelectorAll('#cat .pk').forEach(function (b) {
    if (b.checked) { out.push(b.value); }
  });
  return out;
}
/* The count line is deliberately NOT a live region: the checkbox announces its
   own state change natively, and re-announcing a running total on every toggle
   is double-talk (a11y contract item 4). */
function pickChanged() {
  var n = picked().length;
  var el = document.getElementById('c1-count');
  if (el) {
    el.textContent = n === 0 ? NL_C1.countNone
      : (n + ' ' + (n === 1 ? NL_C1.countOne : NL_C1.countMany));
  }
  var c = document.getElementById('c1-consequence');
  /* FLAG 5 as ruled: floor of 1, and the consequence renders at 1-2 only. */
  if (c) { c.hidden = !(n === 1 || n === 2); }
  coverageChanged();
  var r = document.getElementById('c1-refusal');
  if (r && n > 0) { r.textContent = ''; }
}
/* NL-135 Q1. Counts the boxes the SERVER stamped data-cov="unserved" — the
   client owns no coverage rule of its own, so there is one map, one
   fetchability rule and one inheritance rule, all of them server-side.
   Absence is the only thing this line ever says; there is deliberately no
   cheerful inverse, because "all covered" is a sufficiency claim a static map
   cannot earn. */
function coverageChanged() {
  var el = document.getElementById('c1-coverage');
  if (!el) { return; }
  var n = 0;
  document.querySelectorAll('#cat .pk').forEach(function (b) {
    if (b.checked && b.getAttribute('data-cov') === 'unserved') { n += 1; }
  });
  el.hidden = n === 0;
  if (n > 0) {
    el.textContent = NL_C1.covHead + ' ' + n + ' '
      + (n === 1 ? NL_C1.covOne : NL_C1.covMany) + ' ' + NL_C1.covTail;
  }
}
function toggleDomain(btn) {
  var ul = document.getElementById(btn.getAttribute('aria-controls'));
  if (!ul) { return; }
  var open = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', open ? 'false' : 'true');
  ul.hidden = open;
  btn.textContent = (open ? NL_C1.show : NL_C1.hide) + ' '
    + ul.querySelectorAll('.pick').length + ' ' + NL_C1.narrower;
}
/* TYPE -> the catalog filters and the count announces once, politely. Focus
   stays in the field; nothing is stolen, nothing scrolls under the thumb. The
   field cannot create a topic — that is the suggestions-only law satisfied by
   the mechanism rather than by a disclaimer. */
function filterCatalog() {
  var raw = (document.getElementById('c1-filter').value || '').trim();
  var q = raw.toLowerCase();
  var status = document.getElementById('c1-filter-status');
  var total = parseInt(status.getAttribute('data-total'), 10) || 0;
  var shown = 0;
  document.querySelectorAll('#cat fieldset.dom').forEach(function (fs) {
    var any = false;
    fs.querySelectorAll('.pick').forEach(function (row) {
      var input = row.querySelector('.pk');
      var name = input ? (input.getAttribute('data-name') || '') : '';
      var hit = !q || name.indexOf(q) !== -1;
      var li = row.parentElement && row.parentElement.tagName === 'LI'
        ? row.parentElement : null;
      (li || row).hidden = !hit;
      if (hit) { any = true; shown += 1; }
    });
    var more = fs.querySelector('.dom-more');
    var subs = fs.querySelector('.subs');
    if (q) {
      if (more) { more.hidden = true; }
      if (subs) { subs.hidden = false; }
    } else if (more) {
      more.hidden = false;
      if (subs) { subs.hidden = more.getAttribute('aria-expanded') !== 'true'; }
    }
    fs.hidden = !any;
  });
  var quoted = ' “' + raw + '”.';
  if (!q) {
    status.textContent = total + ' '
      + (total === 1 ? NL_C1.topicWord : NL_C1.topicsWord) + '.';
  } else if (shown === 0) {
    /* The no-match line says nothing more. The suggestions-only law is already
       satisfied by the mechanism (this field cannot create a topic), and a
       second sentence apologising for a missing topic invites the reader to
       argue with a catalog they cannot edit. */
    status.textContent = NL_C1.noMatch + quoted;
  } else {
    status.textContent = shown + ' '
      + (shown === 1 ? NL_C1.matchOne : NL_C1.matchMany) + quoted;
  }
}
function foundEdition() {
  var names = picked();
  var refusal = document.getElementById('c1-refusal');
  if (names.length === 0) {
    /* The remedy is the focus move plus the "Pick at least one." line already
       on screen. The refusal is announced once (role=status) and focus goes to
       the first topic row — the only programmatic focus move on this page. */
    if (refusal) { refusal.textContent = NL_C1.refusal; }
    var first = document.querySelector('#cat .pk');
    if (first) { first.focus(); }
    return;
  }
  var btn = document.getElementById('c1-found');
  if (btn) { btn.disabled = true; }
  fetch('/api/commission', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topics: names })
  }).then(function (r) { return r.json(); }).then(function (d) {
    if (d && d.ok) { location.reload(); return; }
    if (btn) { btn.disabled = false; }
    if (refusal) { refusal.textContent = (d && d.error) || NL_C1.refusal; }
  }).catch(function () {
    if (btn) { btn.disabled = false; }
    if (refusal) { refusal.textContent = NL_C1.refusal; }
  });
}
function foundAgain() {
  fetch('/api/generate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: '{}'
  }).then(function () { location.reload(); })
    .catch(function () { location.reload(); });
}
/* THE VIGIL. Two clocks, not one: a reader looking at a single number next to
   a long stage computes "stuck" from the only evidence on screen. Neither
   clock sits in a live region. Nothing about the RUN lives here — the run is a
   server-side thread and this only reads its snapshot, which is what keeps
   "Closing this page won't stop it" true. */
var c1Clock = { total: 0, stage: 0, sync: 0, timer: null };
function c1Fmt(s) {
  s = Math.max(0, Math.floor(s));
  var m = Math.floor(s / 60), r = s % 60;
  return m + 'm ' + (r < 10 ? '0' : '') + r + 's';
}
function c1Tick() {
  var d = (Date.now() - c1Clock.sync) / 1000;
  var st = document.getElementById('c3-stage-clock');
  var tt = document.getElementById('c3-total-clock');
  if (st) { st.textContent = c1Fmt(c1Clock.stage + d); }
  if (tt) { tt.textContent = c1Fmt(c1Clock.total + d); }
}
function c1Sync(totalS, stageS) {
  if (typeof totalS === 'number' && totalS >= 0) { c1Clock.total = totalS; }
  c1Clock.stage = (typeof stageS === 'number' && stageS >= 0) ? stageS : 0;
  c1Clock.sync = Date.now();
  if (!c1Clock.timer) { c1Clock.timer = setInterval(c1Tick, 1000); }
  c1Tick();
}
function c1Poll() {
  fetch('/api/status').then(function (r) { return r.json(); }).then(function (d) {
    if (d.state === 'running') {
      var st = document.getElementById('c3-stage');
      if (st && d.reader_stage) { st.textContent = d.reader_stage; }
      c1Sync(d.total_elapsed_s, d.stage_elapsed_s);
      setTimeout(c1Poll, 2500);
    } else { location.reload(); }
  }).catch(function () { setTimeout(c1Poll, 4000); });
}
if (document.getElementById('c3-waiting')) {
  var live = document.getElementById('c3-live');
  if (live) {
    c1Sync(parseFloat(live.getAttribute('data-total')) || 0,
           parseFloat(live.getAttribute('data-stage-el')) || 0);
  }
  c1Poll();
}
if (document.getElementById('cat')) { pickChanged(); }
"""
