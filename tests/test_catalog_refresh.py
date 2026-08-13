"""The catalog-refresh verb — `newslens profile refresh-catalog`.

BORN RED: `newslens.catalog_refresh` does not exist at HEAD (0d90f9b), so every
pin here fails at HEAD. The import is done INSIDE each test rather than at
module scope for the reason test_nl135_slate_land.py gives: a module-level
import of a module that does not exist yet takes the whole file down with one
collection error, and a collection error names nothing. Each pin should red on
its own, naming its own missing thing.

WHAT IS PINNED, in the order the constraints were chartered:

  1. USER-INVOKED / dry-run-by-default — the CLI writes only when --apply is
     typed, and a dry run leaves the file byte-identical.
  2. PRESERVES THE READER'S EDITS — add-only, proven against a profile that has
     disabled a feed, added its own, and chosen its own interests. Including
     the case that motivated it: the org template DISAGREEING about a source
     the reader already has (the template disabled CNN on 2026-08-07; a profile
     born before that still has it enabled) is REPORTED, never applied.
  3. THE FOUNDER PIN — refused by name AND structurally, through a symlink
     named something else, which is the only version of that guard NL-132-B's
     QA loop left standing.
  4. MERGE-WITH-PROVENANCE — a feed adopted and later deleted is never offered
     back; a skipped feed is remembered as declined; the ledger is comments
     only and cannot change what the YAML means.

Plus the driving case end to end: the real shipped org template, refreshed onto
a pre-slate profile, lands the six health feeds that `fresh1` (created
2026-08-02, one day before the slate) has never had.

CARRIED INVARIANT (born GREEN, labelled as the born-red law requires):
`test_the_shipped_template_still_parses_clean` — it guards behaviour that
already worked and must keep working through this diff.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

import pytest

from newslens import config, paths, profiles

# A pre-slate profile catalog: two sources, the reader's own interests, and the
# comments that make the file theirs. Deliberately NOT a copy of the shipped
# template — the merge must work on any well-formed sources.yaml, and a test
# that mirrors the template would prove only that a file matches itself.
OLD_CATALOG = """\
# NewsLens sources & interests — this file is yours to edit.
# A comment the reader wrote and must still be here afterwards.

settings:
  threads_steer_selection: true
  tts_engine: openai

sources:
  # --- Broadcast ------------------------------------------------------------
  - name: CNN
    rss_url: http://rss.cnn.com/rss/cnn_topstories.rss
    note: "kept on: I still read it"
  - name: NPR
    rss_url: https://feeds.npr.org/1001/rss.xml
    enabled: false          # the reader turned this OFF by hand
  # --- My own additions ------------------------------------------------------
  - name: My Local Paper
    rss_url: https://example.invalid/local/feed
    note: "hand-added by the reader"

# --- Interests ---------------------------------------------------------------
interests:
  broad:
    - Public Health
  granular:
    - Vaccine Policy
"""

# The org catalog, one refresh later: CNN disabled (the NL-142b posture), NPR
# unchanged, and three new beat feeds. `My Local Paper` is deliberately absent —
# the org has never heard of it and must leave it alone.
NEW_TEMPLATE = """\
# NewsLens PROFILE TEMPLATE — the source catalog a new profile is born with.

settings:
  threads_steer_selection: false
  tts_engine: kokoro

sources:
  - name: CNN
    rss_url: http://rss.cnn.com/rss/cnn_topstories.rss
    enabled: false  # FROZEN FEED: newest item 2023-04-25
    note: "http-only: CNN's https feed endpoint has broken TLS"
  - name: NPR
    rss_url: https://feeds.npr.org/1001/rss.xml

  # --- Health & Science (NL-135; the proven gap) ------------------------------
  - name: STAT News
    rss_url: https://www.statnews.com/feed/
    note: "biotech/pharma/health-policy newsroom"
  - name: KFF Health News
    rss_url: https://kffhealthnews.org/feed/
    wire_syndication: true
    note: "nonprofit, CC-republished widely — wire-flagged"
  - name: Nature
    rss_url: https://www.nature.com/nature.rss
    tier: headline_only
    note: "science record-of-note; journal paywall"

# --- Interests ---------------------------------------------------------------
interests:
  broad:
  granular:
"""


# THE SHIPPED STATE OF EVERY NEW PROFILE: interests chosen but EMPTY. This is
# `templates/profile-sources.yaml`'s own shape, and it is the shape in which
# `_verify`'s interests guard is structurally blind — `before.interests_broad`
# and `after.interests_broad` are both `[]` whether the block is intact or gone
# (QA F-1). Every pin about losing a reader's file uses THIS catalog, not the
# one with interests already picked, because the blind case is the common case.
EMPTY_INTERESTS_CATALOG = """\
# NewsLens sources & interests — this file is yours to edit.

settings:
  threads_steer_selection: true
  tts_engine: openai

sources:
  - name: CNN
    rss_url: http://rss.cnn.com/rss/cnn_topstories.rss
  - name: NPR
    rss_url: https://feeds.npr.org/1001/rss.xml

# --- Interests ---------------------------------------------------------------
# THE READER'S OWN DOCUMENTATION. Pick this reader's tags here; `newslens
# rank` refuses by name until they are chosen. Broad tags are domains,
# granular tags are the specific beats inside them. Names must come from
# `newslens topics` — a tag nobody authored is a tag nothing can serve.
# Add one per line under the right heading; delete any you stop caring
# about. Nothing here is chosen for you.
interests:
  broad: []
  granular: []
"""

# The same catalog with a HALF-DELETED ledger: the opening sentinel is there,
# the closing one is not — what an interrupted hand-edit of the block leaves
# behind. Everything below the survivor is the reader's `interests:` block.
HALF_DELETED_LEDGER = EMPTY_INTERESTS_CATALOG.replace(
    "\n# --- Interests",
    "\n  # ===== newslens catalog-refresh ledger =====\n"
    "  # The record of which org-catalog feeds this profile has been OFFERED.\n"
    "  #@adopted Something Adopted Earlier\n"
    "\n# --- Interests")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reindented(catalog: str, indent: int) -> str:
    """The same catalog with its `sources:` list items at a different column.
    All three shapes below are valid YAML; two of them are what a reader who
    hand-writes or reformats their catalog actually produces."""
    out = []
    for line in catalog.splitlines():
        if line.startswith("  - name:") or line.startswith("    rss_url:"):
            out.append(" " * indent + line[2:] if indent >= 0 else line)
        else:
            out.append(line)
    return "\n".join(out) + "\n"


@pytest.fixture
def reader(tmp_path):
    """A provisioned profile whose catalog is OLD_CATALOG — i.e. a reader whose
    copy froze before the org's latest slate. Returns (slug, sources_file)."""
    profiles.create("tester")
    sources = tmp_path / "profiles" / "tester" / "sources.yaml"
    sources.write_text(OLD_CATALOG, encoding="utf-8")
    return "tester", sources


@pytest.fixture
def org_template(tmp_path):
    p = tmp_path / "org-template.yaml"
    p.write_text(NEW_TEMPLATE, encoding="utf-8")
    return p


# ===========================================================================
# 1. Dry run by default — the org proposes, the reader disposes
# ===========================================================================

def test_a_plan_changes_nothing_at_all(reader, org_template):
    from newslens import catalog_refresh

    _slug, sources = reader
    before = sha(sources)
    plan = catalog_refresh.plan("tester", template=org_template)
    assert sha(sources) == before, "plan() wrote to the reader's file"
    assert [o.name for o in plan.offers] == ["STAT News", "KFF Health News",
                                             "Nature"]
    assert plan.applied is False


def test_the_cli_is_a_dry_run_unless_apply_is_typed(reader, capsys):
    """The CLI's default path must not write. Run against the SHIPPED org
    template, which is what a reader actually gets."""
    from newslens import cli

    _slug, sources = reader
    before = sha(sources)
    rc = cli.main(["profile", "refresh-catalog", "tester"])
    out = capsys.readouterr().out
    assert rc == 0
    assert sha(sources) == before, "a dry run wrote to the reader's file"
    assert "DRY RUN — nothing was changed" in out
    assert "WOULD ADD" in out
    assert "--apply" in out


# ===========================================================================
# 2. Add-only: the reader's own edits survive, and disagreements are reported
# ===========================================================================

def test_apply_adds_the_org_additions_and_touches_nothing_else(reader,
                                                               org_template):
    from newslens import catalog_refresh

    _slug, sources = reader
    before = config.load_sources(sources)
    plan = catalog_refresh.apply(catalog_refresh.plan("tester",
                                                     template=org_template))
    after = config.load_sources(sources)

    assert plan.adopted_now == ["STAT News", "KFF Health News", "Nature"]
    assert after.problems == []
    assert ([s.name for s in after.sources]
            == [s.name for s in before.sources] + plan.adopted_now)

    # Every source that was already there is IDENTICAL, field for field.
    for old in before.sources:
        new = next(s for s in after.sources if s.name == old.name)
        assert new == old, f"{old.name} was modified by a refresh"

    # The adopted entries carry the org's own posture, not a re-derived one.
    nature = next(s for s in after.sources if s.name == "Nature")
    assert nature.tier == "headline_only" and nature.enabled is True
    kff = next(s for s in after.sources if s.name == "KFF Health News")
    assert kff.wire_syndication is True


def test_the_readers_own_edits_and_choices_all_survive(reader, org_template):
    from newslens import catalog_refresh

    _slug, sources = reader
    catalog_refresh.apply(catalog_refresh.plan("tester", template=org_template))
    after = config.load_sources(sources)

    # the feed they turned off stays off
    npr = next(s for s in after.sources if s.name == "NPR")
    assert npr.enabled is False
    # the feed they added by hand is still there, unedited
    mine = next(s for s in after.sources if s.name == "My Local Paper")
    assert mine.rss_url == "https://example.invalid/local/feed"
    # their interests are untouched — the template's are EMPTY, and an
    # overwrite would silently wipe the strongest personalization signal
    assert after.interests_broad == ["Public Health"]
    assert after.interests_granular == ["Vaccine Policy"]
    # their settings too: the template says kokoro/false, they said openai/true
    assert after.tts_engine == "openai"
    assert after.threads_steer_selection is True
    # and their own comment is still in the file
    assert ("A comment the reader wrote and must still be here afterwards"
            in sources.read_text(encoding="utf-8"))


def test_an_org_disagreement_on_a_shared_source_is_reported_never_applied(
        reader, org_template):
    """The motivating case: the org disabled CNN (NL-142b, 2026-08-07); this
    profile's copy froze before that and still has it enabled. A refresh must
    SAY SO and change nothing — `enabled` is the reader's line."""
    from newslens import catalog_refresh

    _slug, sources = reader
    plan = catalog_refresh.plan("tester", template=org_template)
    cnn = [d for d in plan.divergences if d.name == "CNN"]
    assert cnn, "the CNN posture disagreement was not reported"
    assert cnn[0].field == "enabled"
    assert cnn[0].template is False and cnn[0].profile is True

    catalog_refresh.apply(plan)
    after = config.load_sources(sources)
    assert next(s for s in after.sources if s.name == "CNN").enabled is True, \
        "a refresh disabled a source the reader had enabled"


def test_the_readers_own_sources_are_never_offered_or_removed(reader,
                                                              org_template):
    from newslens import catalog_refresh

    plan = catalog_refresh.plan("tester", template=org_template)
    assert plan.reader_only == ["My Local Paper"]
    assert "My Local Paper" not in [o.name for o in plan.offers]


# ===========================================================================
# 3. The founder pin — nominal AND structural
# ===========================================================================

def test_the_founder_catalog_is_refused_by_name():
    from newslens import catalog_refresh

    with pytest.raises(catalog_refresh.RefreshRefused) as exc:
        catalog_refresh.plan(paths.DEFAULT_PROFILE)
    assert "founder" in str(exc.value).lower()


def test_the_founder_catalog_is_refused_through_a_symlink_named_otherwise(
        tmp_path, org_template):
    """Structural, not nominal. A name check is defeated by a link called
    anything else — the NL-132-B finding, re-proven on a verb that WRITES."""
    from newslens import catalog_refresh

    founder = tmp_path / "sources.yaml"
    founder.write_text(OLD_CATALOG, encoding="utf-8")
    before = sha(founder)

    profiles.create("decoy")
    theirs = tmp_path / "profiles" / "decoy" / "sources.yaml"
    theirs.unlink()
    theirs.symlink_to(founder)

    with pytest.raises(catalog_refresh.RefreshRefused) as exc:
        catalog_refresh.plan("decoy", template=org_template)
    assert "founder" in str(exc.value).lower()
    assert sha(founder) == before, "the founder's catalog was written"


def test_a_catalog_that_resolves_outside_the_profile_root_is_refused(
        tmp_path, org_template):
    from newslens import catalog_refresh

    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(OLD_CATALOG, encoding="utf-8")
    before = sha(elsewhere)

    profiles.create("escapee")
    theirs = tmp_path / "profiles" / "escapee" / "sources.yaml"
    theirs.unlink()
    theirs.symlink_to(elsewhere)

    with pytest.raises(catalog_refresh.RefreshRefused) as exc:
        catalog_refresh.plan("escapee", template=org_template)
    assert "outside" in str(exc.value).lower()
    assert sha(elsewhere) == before


def test_an_unknown_profile_is_refused_not_created(tmp_path):
    from newslens import catalog_refresh

    with pytest.raises(profiles.ProfileMissingError):
        catalog_refresh.plan("ghost")
    assert not (tmp_path / "profiles" / "ghost").exists()


# ===========================================================================
# 4. Merge with provenance — the ledger
# ===========================================================================

def test_a_feed_the_reader_adopted_and_then_deleted_is_never_offered_back(
        reader, org_template):
    """Without this, every refresh argues with the reader's editing: 'you
    deleted STAT News' and 'STAT News is new to you' are the same observation
    unless something remembers."""
    from newslens import catalog_refresh

    _slug, sources = reader
    catalog_refresh.apply(catalog_refresh.plan("tester", template=org_template))

    # The reader deletes the ENTRY by hand, the way the file invites — and
    # leaves the ledger alone, which is the case this pin is about. (Deleting
    # the ledger line too is the documented un-remember, tested separately.)
    text = sources.read_text(encoding="utf-8")
    lines = text.splitlines()
    keep = [ln for ln in lines
            if ln.strip().startswith("#@")
            or ("STAT News" not in ln
                and "statnews.com" not in ln
                and "biotech/pharma" not in ln)]
    sources.write_text("\n".join(keep) + "\n", encoding="utf-8")
    assert "STAT News" not in [s.name for s in
                               config.load_sources(sources).sources]

    again = catalog_refresh.plan("tester", template=org_template)
    assert "STAT News" not in [o.name for o in again.offers], \
        "a refresh offered back a feed the reader deleted"
    assert "STAT News" in again.reader_removed
    assert again.is_noop


def test_a_skipped_feed_is_remembered_as_declined(reader, org_template):
    from newslens import catalog_refresh

    _slug, sources = reader
    plan = catalog_refresh.plan("tester", skip=["Nature"],
                                template=org_template)
    assert plan.skipped == ["Nature"]
    assert "Nature" not in [o.name for o in plan.offers]

    catalog_refresh.apply(plan)
    assert "Nature" not in [s.name for s in config.load_sources(sources).sources]

    again = catalog_refresh.plan("tester", template=org_template)
    assert "Nature" not in [o.name for o in again.offers], \
        "a declined feed came back"
    assert "Nature" in again.previously_declined


def test_the_ledger_is_comments_only_and_cannot_change_the_yaml(reader,
                                                                org_template):
    from newslens import catalog_refresh

    _slug, sources = reader
    catalog_refresh.apply(catalog_refresh.plan("tester", skip=["Nature"],
                                               template=org_template))
    text = sources.read_text(encoding="utf-8")
    ledger = [ln for ln in text.splitlines()
              if ln.strip().startswith(("#@", catalog_refresh.LEDGER_BEGIN,
                                        catalog_refresh.LEDGER_END))]
    assert ledger, "no ledger was written"
    for line in ledger:
        assert line.lstrip().startswith("#"), f"ledger line is not a comment: {line}"
    assert config.load_sources(sources).problems == []


def test_a_second_refresh_with_nothing_new_is_a_noop(reader, org_template):
    from newslens import catalog_refresh

    _slug, sources = reader
    catalog_refresh.apply(catalog_refresh.plan("tester", template=org_template))
    settled = sha(sources)

    again = catalog_refresh.plan("tester", template=org_template)
    assert again.offers == [] and again.is_noop
    catalog_refresh.apply(again)
    assert sha(sources) == settled, "an empty refresh rewrote the file"


def test_adopted_entries_arrive_with_the_orgs_own_comments(reader,
                                                           org_template):
    """Provenance: the notes explaining what a feed IS are the catalog's
    documentation. A merge that dropped them would hand the reader a list of
    URLs and call it a catalog."""
    from newslens import catalog_refresh

    _slug, sources = reader
    catalog_refresh.apply(catalog_refresh.plan("tester", template=org_template))
    text = sources.read_text(encoding="utf-8")
    assert "biotech/pharma/health-policy newsroom" in text
    assert "wire-flagged" in text
    assert "ADOPTED FROM THE ORG CATALOG" in text


# ===========================================================================
# 5. The write is checked before it lands
# ===========================================================================

def test_a_merge_that_would_not_parse_is_refused_and_nothing_is_written(
        reader, org_template, monkeypatch):
    """The verify gate, proven by breaking the thing it guards: with the
    renderer emitting garbage the file must come through untouched, not
    half-written. Without _verify this test writes broken YAML into a
    reader's catalog."""
    from newslens import catalog_refresh

    _slug, sources = reader
    before = sha(sources)
    monkeypatch.setattr(catalog_refresh, "_render_adoption",
                        lambda p, when: ["  - name: [unclosed", "    rss: ("])
    with pytest.raises(catalog_refresh.RefreshMalformed):
        catalog_refresh.apply(catalog_refresh.plan("tester",
                                                   template=org_template))
    assert sha(sources) == before, "a refused merge still wrote to the file"
    assert not (sources.parent / (sources.name + ".refresh-tmp")).exists()


def test_the_write_gate_refuses_a_merge_that_would_touch_interests(reader,
                                                                   tmp_path):
    """_verify's interests clause, pinned at its own seam. Interests are the
    strongest personalization signal in the system and the template's are EMPTY
    by design, so a merge that ever carried them would silently wipe a reader's
    picks. Route: `apply` calls `_verify` on the temp file before `os.replace`
    (catalog_refresh.apply), so this gate is the last thing between a bad merge
    and the reader's catalog."""
    from newslens import catalog_refresh

    _slug, sources = reader
    before = config.load_sources(sources)
    tampered = tmp_path / "tampered.yaml"
    tampered.write_text(
        sources.read_text(encoding="utf-8")
        .replace("    - Vaccine Policy", "    - Federal Reserve"),
        encoding="utf-8")
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify(before, tampered, adopted=[])
    assert "interest" in str(exc.value).lower()


def test_the_write_gate_refuses_a_merge_that_would_edit_a_source(reader,
                                                                 tmp_path):
    """The add-only promise as a mechanism rather than a property of the
    renderer: even if the merge text were wrong, a changed pre-existing source
    stops the write."""
    from newslens import catalog_refresh

    _slug, sources = reader
    before = config.load_sources(sources)
    tampered = tmp_path / "tampered.yaml"
    tampered.write_text(
        sources.read_text(encoding="utf-8")
        .replace("    enabled: false          # the reader turned this OFF by hand",
                 "    enabled: true"),
        encoding="utf-8")
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify(before, tampered, adopted=[])
    assert "NPR" in str(exc.value)


def test_a_template_entry_the_text_reader_cannot_see_is_refused_not_skipped(
        reader, tmp_path):
    """Two readers parse the org catalog: PyYAML (which entries exist) and the
    text scanner (which lines to copy, comments included). The text scanner is
    what actually writes, so an entry it cannot locate is a feed silently never
    offered — a failure with no symptom whatsoever. Mutation check: delete the
    `unseen` guard in plan() and this goes green with STAT News quietly
    missing from the offers."""
    from newslens import catalog_refresh

    odd = tmp_path / "odd-template.yaml"
    odd.write_text(NEW_TEMPLATE.replace(
        "  - name: STAT News\n    rss_url: https://www.statnews.com/feed/",
        "  - rss_url: https://www.statnews.com/feed/\n    name: STAT News"),
        encoding="utf-8")
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh.plan("tester", template=odd)
    assert "STAT News" in str(exc.value)


def test_a_profile_whose_catalog_does_not_parse_is_refused_untouched(
        reader, org_template):
    from newslens import catalog_refresh

    _slug, sources = reader
    sources.write_text("sources:\n  - name: X\n    tier: nonsense-tier\n",
                       encoding="utf-8")
    before = sha(sources)
    with pytest.raises(catalog_refresh.RefreshMalformed):
        catalog_refresh.plan("tester", template=org_template)
    assert sha(sources) == before


# ===========================================================================
# 6. The driving case, against the REAL shipped org catalog
# ===========================================================================

def test_the_shipped_catalog_lands_the_health_slate_on_a_pre_slate_profile(
        reader):
    """`fresh1` was created 2026-08-02; the NL-135 slate landed in the template
    on 2026-08-03. That profile ranked 385 general-news items against health
    interests and surfaced 2 stories because its own copy has no health beat.
    This is the verb that fixes that, run against the real template."""
    from newslens import catalog_refresh

    _slug, sources = reader
    plan = catalog_refresh.plan("tester")
    offered = [o.name for o in plan.offers]
    for feed in ("STAT News", "KFF Health News", "NPR Health",
                 "CIDRAP — Antimicrobial Stewardship", "Nature",
                 "Fierce Healthcare"):
        assert feed in offered, f"{feed} was not offered to a pre-slate profile"

    catalog_refresh.apply(plan)
    after = config.load_sources(sources)
    assert after.problems == []
    names = {s.name for s in after.sources}
    assert {"STAT News", "KFF Health News", "NPR Health", "Nature",
            "Fierce Healthcare"} <= names
    # and the reader is still themselves
    assert after.interests_granular == ["Vaccine Policy"]
    assert next(s for s in after.sources if s.name == "NPR").enabled is False


def test_the_refreshed_catalog_is_a_coverage_upgrade_the_reader_can_see(reader):
    """Not just bytes: the health topic this reader picked goes from unserved
    to served, which is the whole point of the verb (coverage is computed live
    from the reader's own enabled sources — templates/feed-coverage.yaml)."""
    from newslens import catalog_refresh, coverage

    _slug, sources = reader
    before = coverage.state(config.load_sources(sources))
    assert before.of("Vaccine Policy").unserved, \
        "the fixture reader is supposed to start with an unserved pick"

    catalog_refresh.apply(catalog_refresh.plan("tester"))
    after = coverage.state(config.load_sources(sources))
    assert not after.of("Vaccine Policy").unserved, \
        "the picked topic is still unserved after adopting the health slate"
    assert "STAT News" in after.of("Vaccine Policy").sources


# ===========================================================================
# CLI surface
# ===========================================================================

def test_the_cli_applies_only_what_it_printed(reader, capsys):
    from newslens import cli

    _slug, sources = reader
    before = {s.name for s in config.load_sources(sources).sources}
    assert cli.main(["profile", "refresh-catalog", "tester"]) == 0
    dry = capsys.readouterr().out
    # rsplit, not split: outlet names contain parens ("Chartbook (Adam Tooze)")
    offered = {ln.strip()[2:].rsplit(" (", 1)[0]
               for ln in dry.splitlines() if ln.strip().startswith("+ ")}
    assert offered

    assert cli.main(["profile", "refresh-catalog", "tester", "--apply"]) == 0
    assert "APPLIED" in capsys.readouterr().out
    after = {s.name for s in config.load_sources(sources).sources}
    assert after - before == offered, \
        "the apply added something the dry run never printed"


def test_the_cli_refuses_the_founder_with_a_nonzero_exit(capsys):
    from newslens import cli

    rc = cli.main(["profile", "refresh-catalog", paths.DEFAULT_PROFILE])
    assert rc == 2
    assert "founder" in capsys.readouterr().err.lower()


def test_the_verb_is_reachable_from_the_installed_console_script(reader):
    """Wiring proof: the subcommand exists on the real parser, not only as a
    module function. `--help` is enough — it fails if the parser lacks it."""
    proc = subprocess.run(
        [sys.executable, "-m", "newslens.cli", "profile", "refresh-catalog",
         "--help"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "--apply" in proc.stdout and "--skip" in proc.stdout


# ===========================================================================
# CARRIED INVARIANT (born GREEN — labelled per the born-red law)
# ===========================================================================

def test_the_shipped_template_still_parses_clean():
    cfg = config.load_sources(paths.PROFILE_SOURCES_TEMPLATE)
    assert cfg.problems == []
    assert cfg.has_interests is False


# ===========================================================================
# FIX LOOP 1 (QA 2026-08-13, findings F-1..F-10)
#
# Everything below was born red against the bytes QA tested. The two that
# matter most: a half-deleted ledger used to delete the reader's `interests:`
# block with no error and no symptom (F-1), and `_verify` gated parsed FIELDS
# while never once looking at COMMENTS — the only thing the textual merge
# exists to protect (F-2).
# ===========================================================================

@pytest.fixture
def blank_reader(tmp_path):
    """A profile in the state every new profile ships in: interests chosen but
    EMPTY. Returns (slug, sources_file)."""
    profiles.create("blank")
    sources = tmp_path / "profiles" / "blank" / "sources.yaml"
    sources.write_text(EMPTY_INTERESTS_CATALOG, encoding="utf-8")
    return "blank", sources


# --- F-1: the ledger strip is bounded --------------------------------------

def test_an_unterminated_ledger_is_refused_instead_of_stripped_to_eof():
    """The bug at its own seam. `_strip_ledger` set `inside = True` on BEGIN
    and cleared it only on END, so a ledger missing its closing sentinel ate
    every line to the end of the file."""
    from newslens import catalog_refresh

    lines = HALF_DELETED_LEDGER.splitlines()
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._strip_ledger(lines)
    assert "never closed" in str(exc.value)
    # and a WELL-FORMED ledger still strips, or the fix has broken the feature
    ok = catalog_refresh._strip_ledger(
        HALF_DELETED_LEDGER.replace(
            "  #@adopted Something Adopted Earlier",
            "  #@adopted Something Adopted Earlier\n"
            "  # ===== end catalog-refresh ledger =====").splitlines())
    assert "#@adopted" not in "\n".join(ok)
    assert "interests:" in "\n".join(ok), "the strip ate the interests block"


def test_a_half_deleted_ledger_is_refused_by_the_dry_run_too(blank_reader,
                                                             org_template):
    """A refusal the dry run does not predict is not a dry run. `plan()` calls
    the strip for its raise, exactly as it calls `_sources_span`."""
    from newslens import catalog_refresh

    _slug, sources = blank_reader
    sources.write_text(HALF_DELETED_LEDGER, encoding="utf-8")
    before = sha(sources)
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh.plan("blank", template=org_template)
    assert "ledger" in str(exc.value)
    assert sha(sources) == before


def test_a_half_deleted_ledger_cannot_silently_delete_empty_interests(
        blank_reader, org_template):
    """THE F-1 CASE, end to end, in the arm where `_verify` is blind.

    The reader dry-runs, then hand-edits the ledger (deleting a line is what
    the block itself invites), then types --apply. Pre-fix this deleted nine
    lines — the whole `interests:` block including the six that explain how to
    choose interests — applied clean and reported success, because with EMPTY
    interests `before` and `after` are both `[]` and the guard cannot fire."""
    from newslens import catalog_refresh

    _slug, sources = blank_reader
    plan = catalog_refresh.plan("blank", template=org_template)
    assert plan.offers, "the fixture is supposed to have something to adopt"

    sources.write_text(HALF_DELETED_LEDGER, encoding="utf-8")
    before = sha(sources)
    with pytest.raises(catalog_refresh.RefreshMalformed):
        catalog_refresh.apply(plan)

    assert sha(sources) == before, "a refused apply still wrote to the file"
    text = sources.read_text(encoding="utf-8")
    assert "interests:" in text
    assert "THE READER'S OWN DOCUMENTATION" in text, \
        "the how-to-choose-interests comments were deleted"


# --- F-2: the write gate can see comments ----------------------------------

def test_the_write_gate_catches_a_lost_interests_block_even_with_the_strip_bug(
        blank_reader, org_template, monkeypatch):
    """The other half of the pair, and the reason it is a pair: with the
    bounded strip REPLACED by the original bug, the write gate must still
    refuse. Route: apply() calls `_verify_text` on the bytes it is about to
    land (catalog_refresh.apply), so this is the last thing between a merge
    that eats a reader's file and the reader's file."""
    from newslens import catalog_refresh

    _slug, sources = blank_reader
    plan = catalog_refresh.plan("blank", template=org_template)
    before = sha(sources)

    def strip_to_eof(lines):
        out, inside = [], False
        for line in lines:
            s = line.strip()
            if s == catalog_refresh.LEDGER_BEGIN:
                inside = True
                continue
            if inside or s.startswith("#@"):
                continue
            out.append(line)
        return out

    monkeypatch.setattr(catalog_refresh, "_strip_ledger",
                        lambda lines: strip_to_eof(
                            HALF_DELETED_LEDGER.splitlines()))
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh.apply(plan)
    assert "interests" in str(exc.value)
    assert sha(sources) == before


def test_the_write_gate_refuses_a_merge_that_lost_a_readers_comment():
    """F-2 proper: the module's whole rationale is that comments ARE the
    catalog's documentation, and `_verify` never looked at one."""
    from newslens import catalog_refresh

    lines = EMPTY_INTERESTS_CATALOG.splitlines()
    after = [ln for ln in lines if "this file is yours to edit" not in ln]
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify_text(lines, lines, after)
    assert "lost" in str(exc.value)
    assert "yours to edit" in str(exc.value)
    catalog_refresh._verify_text(lines, lines, lines)   # the clean case passes


def test_the_write_gate_refuses_a_merge_that_grew_a_new_top_level_block():
    """The top-level-key comparison in its own direction: a merge adds source
    entries inside `sources:`. It never adds a block to a reader's file."""
    from newslens import catalog_refresh

    lines = EMPTY_INTERESTS_CATALOG.splitlines()
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify_text(lines, lines, lines + ["discovery:"])
    assert "top-level" in str(exc.value)
    assert "discovery" in str(exc.value)


def test_the_write_gate_refuses_a_ledger_strip_that_removed_real_content():
    """Clearing the ledger can only ever remove COMMENTS — that is what the
    ledger IS. The moment the strip removes something else it has eaten
    content that belongs to the reader, whatever the reason."""
    from newslens import catalog_refresh

    lines = EMPTY_INTERESTS_CATALOG.splitlines()
    retained = [ln for ln in lines if "- name: NPR" not in ln]
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify_text(lines, retained, retained)
    assert "not comments" in str(exc.value)
    assert "NPR" in str(exc.value)


# --- F-3: the dry run predicts the apply -----------------------------------

@pytest.mark.parametrize("indent", [0, 2, 4])
def test_the_dry_run_predicts_the_apply_at_any_list_indentation(
        blank_reader, org_template, indent):
    """Pre-fix, a reader whose catalog writes its list at four columns (or at
    zero, the commonest hand-written style — both valid YAML) was told
    `WOULD ADD 68` and then met a raw PyYAML error reading as *your file is
    broken*, when the file was fine and the MERGE was what broke.

    The `indent=2` case is BORN GREEN and labelled: two columns is what every
    real catalog on this machine uses, which is exactly why the bug survived —
    it is the control, and it is here so a fix that only works at other
    indentations cannot pass."""
    from newslens import catalog_refresh

    _slug, sources = blank_reader
    sources.write_text(reindented(EMPTY_INTERESTS_CATALOG, indent),
                       encoding="utf-8")
    before_names = {s.name for s in config.load_sources(sources).sources}

    plan = catalog_refresh.plan("blank", template=org_template)
    promised = [o.name for o in plan.offers]
    assert promised, "nothing offered — the fixture stopped being a fixture"

    catalog_refresh.apply(catalog_refresh.plan("blank", template=org_template))
    after = config.load_sources(sources)
    assert after.problems == []
    assert {s.name for s in after.sources} - before_names == set(promised), \
        "the apply did not add what the dry run promised"


def test_adopted_entries_land_at_the_readers_own_indentation(blank_reader,
                                                             org_template):
    from newslens import catalog_refresh

    _slug, sources = blank_reader
    sources.write_text(reindented(EMPTY_INTERESTS_CATALOG, 0), encoding="utf-8")
    catalog_refresh.apply(catalog_refresh.plan("blank", template=org_template))
    item_lines = [ln for ln in sources.read_text(encoding="utf-8").splitlines()
                  if ln.lstrip().startswith("- name:")]
    assert item_lines
    assert all(not ln.startswith(" ") for ln in item_lines), \
        f"adopted entries kept the org's column: {item_lines}"


# --- F-4: one refusal register, not two ------------------------------------

def test_an_unparseable_catalog_is_a_refusal_not_a_traceback(reader, capsys):
    """Pre-fix these were two conditions to the machine and one to the reader:
    a TAB-indented catalog exited 1 with `SourcesParseError: ...` and dropped
    the "Nothing was changed" promise, while an unknown key exited 2 with a
    sentence that kept it."""
    from newslens import cli

    _slug, sources = reader
    sources.write_text("sources:\n\t- name: X\n\t  rss_url: http://x.invalid/f\n",
                       encoding="utf-8")
    before = sha(sources)
    rc = cli.main(["profile", "refresh-catalog", "tester"])
    err = capsys.readouterr().err
    assert rc == 2, f"tab-indented catalog exited {rc}: {err}"
    assert "Nothing was changed" in err
    assert "Traceback" not in err
    assert sha(sources) == before


# --- F-5: the individual guard arms, one pin each --------------------------
# The composed guards were sound empirically (23 founder attacks, 0 breaches;
# 22 hostile catalog shapes, 0 corrupt writes). What QA's mutation campaign
# found was that seven arms could be deleted one at a time with every pin
# still green. These are those seven arms.
#
# BORN GREEN, LABELLED — and the label matters here. Every pin from here to the
# end of the F-5 block, plus `test_the_write_unlinks_rather_than_writing_
# through_a_hardlink`, guards behaviour that ALREADY WORKS in the bytes QA
# tested. Nothing in this loop changed those arms; what changed is that
# deleting one now costs a red test. Their evidence is therefore the MUTATION
# table in the fix-loop report — each of these goes red under exactly the
# mutant named in its docstring — and not born-red-by-absence, which would be
# a false claim for code that was already there.
# (The hardlink GUARD pin, `test_a_hardlinked_catalog_is_refused_before_the_
# write`, is the exception in this neighbourhood: that guard is new in this
# loop and the pin is born red.)

def _tamper(reader_sources, tmp_path, old, new, name="tampered.yaml"):
    out = tmp_path / name
    out.write_text(reader_sources.read_text(encoding="utf-8").replace(old, new),
                   encoding="utf-8")
    return out


def test_the_write_gate_names_the_source_a_merge_would_have_dropped(reader,
                                                                    tmp_path):
    """Mutant V2. The set-equality clause below also notices a missing source,
    but it can only say `missing: ['my local paper']` — a normalized key in a
    diff of two sets. Which of the reader's feeds a refused merge would have
    deleted is the sentence the reader needs, so the sentence is the pin."""
    from newslens import catalog_refresh

    _slug, sources = reader
    before = config.load_sources(sources)
    tampered = _tamper(sources, tmp_path,
                       "  - name: My Local Paper\n"
                       "    rss_url: https://example.invalid/local/feed\n"
                       "    note: \"hand-added by the reader\"\n", "")
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify(before, tampered, adopted=[])
    assert "dropped" in str(exc.value)
    assert "My Local Paper" in str(exc.value)


def test_the_write_gate_refuses_a_source_nobody_adopted(reader, tmp_path):
    """Mutant V3. `_verify`'s per-source loop walks the sources that were
    ALREADY there, so it is structurally blind to an entry the merge invented.
    Only `set(after) == old | adopted` sees an arrival."""
    from newslens import catalog_refresh

    _slug, sources = reader
    before = config.load_sources(sources)
    tampered = _tamper(sources, tmp_path, "\n# --- Interests",
                       "\n  - name: Sneaky Feed"
                       "\n    rss_url: https://sneaky.invalid/rss"
                       "\n\n# --- Interests")
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify(before, tampered, adopted=[])
    assert "wrong source set" in str(exc.value)
    assert "sneaky feed" in str(exc.value).lower()


def test_the_write_gate_refuses_a_merge_that_would_change_settings(reader,
                                                                   tmp_path):
    """Mutant V5. Interests were pinned; settings were not. The template's
    settings differ from a reader's by design (`tts_engine: kokoro` vs their
    `openai`), so a merge that ever carried the template's settings across
    would move a reader onto a DIFFERENT, metered TTS engine silently."""
    from newslens import catalog_refresh

    _slug, sources = reader
    before = config.load_sources(sources)
    tampered = _tamper(sources, tmp_path, "tts_engine: openai",
                       "tts_engine: kokoro")
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify(before, tampered, adopted=[])
    assert "settings" in str(exc.value)


def test_the_write_gate_refuses_a_merge_that_parses_but_has_problems(reader,
                                                                     tmp_path):
    """Mutant V6. Not every bad catalog is bad YAML: an unknown key parses
    perfectly and lands in `problems`, where the pipeline refuses on it later.
    A refresh must not be the thing that put it there."""
    from newslens import catalog_refresh

    _slug, sources = reader
    before = config.load_sources(sources)
    tampered = _tamper(sources, tmp_path,
                       "    note: \"hand-added by the reader\"",
                       "    note: \"hand-added by the reader\"\n"
                       "    refresh_only_nonsense: 1")
    assert config.load_sources(tampered).problems, "the tamper did not tamper"
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh._verify(before, tampered, adopted=[])
    assert "does not parse cleanly" in str(exc.value)


def test_the_founder_is_refused_by_name_before_the_disk_is_consulted():
    """Mutant F1. The name check is backstopped by the resolves-to-founder
    check for every shape in which the founder's file EXISTS — so the arm only
    shows itself when it does not. Delete his sources.yaml and the name check
    is the only guard left standing: without it this is a RefreshMalformed
    about a missing file, which invites the reader to restore one."""
    from newslens import catalog_refresh

    founder = paths.profile_layout(paths.DEFAULT_PROFILE)["SOURCES_FILE"]
    if founder.exists():
        founder.unlink()
    with pytest.raises(catalog_refresh.RefreshRefused) as exc:
        catalog_refresh.plan(paths.DEFAULT_PROFILE)
    assert "founder" in str(exc.value).lower()


def test_a_profile_root_symlinked_to_the_founders_root_is_refused(tmp_path):
    """Mutant F2 — the one shape only the resolves-to-founder guard catches.

    The name is a reader's, so guard 1 passes. The catalog genuinely IS inside
    the profile root it was pointed at (the root is the link), so guard 3
    passes: `_at_or_within` compares resolved paths, and both sides resolve
    into the founder's world together. Only comparing the resolved TARGET with
    the founder's own file sees it."""
    from newslens import catalog_refresh

    founder = paths.profile_layout(paths.DEFAULT_PROFILE)["SOURCES_FILE"]
    founder.write_text(OLD_CATALOG, encoding="utf-8")
    before = sha(founder)

    root = paths.profiles_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "mirror").symlink_to(paths.profile_root(paths.DEFAULT_PROFILE),
                                 target_is_directory=True)

    with pytest.raises(catalog_refresh.RefreshRefused) as exc:
        catalog_refresh.plan("mirror")
    assert "founder" in str(exc.value).lower()
    assert sha(founder) == before, "the founder's catalog was written"


# --- F-6: the hardlink flank, pinned at both layers ------------------------

def test_a_hardlinked_catalog_is_refused_before_the_write(reader, org_template):
    """The guard added in this loop. `Path.resolve()` does not traverse a hard
    link, so a catalog hardlinked to somebody else's file reads as innocent to
    both path guards — it is a different NAME for the same inode, not a link
    to follow."""
    from newslens import catalog_refresh

    _slug, sources = reader
    other = sources.parent / "another-name.yaml"
    os.link(str(sources), str(other))
    assert sources.stat().st_nlink == 2

    with pytest.raises(catalog_refresh.RefreshRefused) as exc:
        catalog_refresh.plan("tester", template=org_template)
    assert "hard link" in str(exc.value).lower()


def test_the_write_unlinks_rather_than_writing_through_a_hardlink(
        reader, org_template):
    """Mutant T1, and QA F-6's undefended flank.

    The guard above cannot be the whole answer: a link made AFTER the check is
    a link the check never saw. What actually protects the other name is that
    `os.replace` swaps a directory entry — it un-links rather than writing
    through the old inode. That was true and completely unpinned: rewriting
    `apply` to write in place left all 26 pins green while opening a
    write-through vector onto whatever else shared the inode, the founder's
    own catalog included.

    So the link is made between `plan` and `apply` on purpose. This asserts the
    WRITER's contribution, not the guard's."""
    from newslens import catalog_refresh

    _slug, sources = reader
    plan = catalog_refresh.plan("tester", template=org_template)

    witness = sources.parent.parent / "witness.yaml"
    os.link(str(sources), str(witness))
    witness_bytes = witness.read_bytes()
    shared_inode = sources.stat().st_ino
    assert witness.stat().st_ino == shared_inode

    catalog_refresh.apply(plan)

    assert witness.read_bytes() == witness_bytes, \
        "the refresh wrote THROUGH the hard link into the other file"
    assert witness.stat().st_ino == shared_inode, "the witness moved inode"
    assert sources.stat().st_ino != shared_inode, \
        "the catalog kept its inode — the write was in place, not a replace"
    assert "STAT News" in [s.name for s in config.load_sources(sources).sources]


# --- F-7 / F-8 / F-10: the cheap ones --------------------------------------

def test_a_crlf_catalog_keeps_its_line_endings(blank_reader, org_template):
    """`read_text().splitlines()` + `"\\n".join(...)` rewrote every line ending
    in the file, not just the added ones, and `_verify` could not see it
    because the YAML parses identically either way."""
    from newslens import catalog_refresh

    _slug, sources = blank_reader
    sources.write_bytes(EMPTY_INTERESTS_CATALOG.replace("\n", "\r\n")
                        .encode("utf-8"))
    catalog_refresh.apply(catalog_refresh.plan("blank", template=org_template))

    raw = sources.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n"), \
        "a CRLF catalog came back with LF lines in it"
    after = config.load_sources(sources)
    assert after.problems == []
    assert "STAT News" in [s.name for s in after.sources]


def test_a_mixed_line_ending_catalog_is_normalised_out_loud(blank_reader,
                                                             capsys):
    """The half of F-7 that preservation cannot cover. A file that mixes CRLF
    and LF has no line ending to preserve, so the merge picks the dominant one
    — and then it has to SAY so, because the fix contract was "preserve or
    refuse, stated", and a normalisation nobody mentions is the silent rewrite
    with extra steps."""
    from newslens import catalog_refresh, cli

    assert catalog_refresh._detect_eol(b"a\nb\n") == ("\n", False)
    assert catalog_refresh._detect_eol(b"a\r\nb\r\n") == ("\r\n", False)
    assert catalog_refresh._detect_eol(b"a\rb\r") == ("\r", False)
    assert catalog_refresh._detect_eol(b"a\r\nb\n") == ("\r\n", True)

    _slug, sources = blank_reader
    mixed = EMPTY_INTERESTS_CATALOG.replace("\n", "\r\n").replace(
        "sources:\r\n", "sources:\n")            # one LF among the CRLFs
    sources.write_bytes(mixed.encode("utf-8"))

    assert cli.main(["profile", "refresh-catalog", "blank", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "mixed line endings" in out
    assert "CRLF throughout" in out
    assert config.load_sources(sources).problems == []


def test_a_refresh_overtaken_on_disk_refuses_instead_of_reporting_success(
        reader, org_template, monkeypatch):
    """QA F-8: two racing refreshes produced `rc=[0,1]`, a file that parsed,
    and a ledger recording only one process's declines — so the process that
    exited 0 may have had its merge discarded. No corruption, but a success
    line for work that did not land. This is that window, forced open."""
    from newslens import catalog_refresh

    _slug, sources = reader
    plan = catalog_refresh.plan("tester", template=org_template)
    interloper_text = OLD_CATALOG + "\n# the other process got here first\n"
    real_verify_text = catalog_refresh._verify_text

    def overtaken(*a, **kw):
        real_verify_text(*a, **kw)
        sources.write_text(interloper_text, encoding="utf-8")

    monkeypatch.setattr(catalog_refresh, "_verify_text", overtaken)
    with pytest.raises(catalog_refresh.RefreshMalformed) as exc:
        catalog_refresh.apply(plan)
    assert "changed on disk" in str(exc.value)
    assert sources.read_text(encoding="utf-8") == interloper_text, \
        "the loser of the race clobbered the winner's file anyway"
    assert list(sources.parent.glob("*.refresh-tmp*")) == []


def test_the_temp_file_is_private_to_this_process(reader, org_template,
                                                  monkeypatch):
    """The other half of F-8. One shared temp name is how a process came to
    verify, and then land, somebody else's merge — a gate that checks bytes
    you did not write is not a gate."""
    from newslens import catalog_refresh

    seen = []
    real_verify = catalog_refresh._verify

    def watch(before, path, adopted):
        seen.append(path)
        return real_verify(before, path, adopted)

    monkeypatch.setattr(catalog_refresh, "_verify", watch)
    _slug, sources = reader
    catalog_refresh.apply(catalog_refresh.plan("tester", template=org_template))
    assert seen, "_verify was never called"
    assert str(os.getpid()) in seen[0].name, \
        f"the temp file is not private to this process: {seen[0].name}"
    assert list(sources.parent.glob("*.refresh-tmp*")) == []


def test_a_skip_that_matched_nothing_is_reported_not_silent(reader, capsys):
    """QA F-10 / the build's own disclosure: a `--skip` naming something never
    offered landed in neither `skipped` nor an error. Most of the time that is
    a typo in the name the reader meant to decline, and silence reads as
    'skipped, remembered' — which it is not."""
    from newslens import catalog_refresh, cli

    _slug, _sources = reader
    plan = catalog_refresh.plan("tester", skip=["STAT News", "Stat Newz"])
    assert plan.skipped == ["STAT News"]
    assert plan.skip_unmatched == ["Stat Newz"]

    assert cli.main(["profile", "refresh-catalog", "tester",
                     "--skip", "Stat Newz"]) == 0
    out = capsys.readouterr().out
    assert "matched nothing" in out
    assert "? Stat Newz" in out
