"""Stage-0 C1 — THE VOCABULARY TOOTH: banned reader-facing words go red.

TERM LAW (ratified 2026-07-28, DECISIONS "[2026-07-28] COMMISSIONING v12
APPROVED AS AMENDED" §2): the reader-facing word is **topics**. "subject" /
"subjects" may not appear on any shipped reader surface — the data model
already speaks topic (sources.yaml's topic level; the rank prompt's tag rungs
print "(topic)"), and a UI saying "subjects" made the product bilingual.

Plus the v12 mockup's KILLED ON SIGHT list, which is binding vocabulary law and
not a style note: another reader's clock ("most readers pick three" and every
sibling), welcome copy, progress chrome, "while you wait", percentages and
ETAs, the interface talking about itself, and the DEAD broad/granular/specific
rung words as READER words.

TWO TIERS, AND THE SCOPE IS DELIBERATE:

  TIER 1 — the Commissioning's own surfaces carry the WHOLE list. This is the
  page the law was written for and it is built new, so it is held to all of it.

  TIER 2 — "subject"/"subjects" is swept across EVERY reader surface, because
  the term ruling was product-wide, not page-local.

WHAT THIS FILE DELIBERATELY DOES NOT ASSERT, and why it is in the build report
rather than silently absent.

THE POPUP HALF IS CLOSED (NL-150, 2026-08-25). This flag used to read "the
SHIPPED settings popup still says 'Add this as a broad topic or a specific
one?' with buttons 'Add as broad' / 'Add as specific'". The 2026-08-24 ruling
killed the ask and both rung buttons; `test_nl150_topics_surface.py` now pins
their absence from `webui.POPUPS` and sweeps the whole add-topic card for all
three dead words. That surface is no longer a live violation and no longer
needs a flag.

WHAT SURVIVES — the residue this flag now points at, six strings, all measured
live in the bytes named:

  1-2. the token GROUP HEADERS "Broad (N)" / "Specific (N)"  server.py:4111-4114
  3-4. the empty notes "No broad topics yet" / "No specific
       topics yet"                                           server.py:4124-4125
  5.   “Didn’t add it — your sources file has no section
       for {level} topics.”                                  server.py:1330-1331
  6.   “Didn’t add it — {name} is already in your
       {level} topics.”                                      server.py:1336-1337

They are now INCOHERENT, not merely off-register: with the ask dead the reader
is never shown a level, so copy naming one references a choice that no longer
exists. 5 and 6 are ruled copy in their own right (NL-103 FIX-2, gate
2026-07-26, which made topic-add refusals reader-facing and loud) — a reword
here would mint unruled reader copy, which is exactly the half-fix the C1 gate
refused on 2026-07-30 when it declined a button reword.

CHARTERED HOME: **NL-123**, the rung-vocabulary lane (C1 gate 2026-07-30, Row
B). It was filed as a three-member lane — the popup ask, the two buttons, and
`topic_add`'s level-bearing refusals; the first two members were discharged by
NL-150's kill, the refusals are members 5-6 above, and the NL-150 gate
(2026-08-25, R-2) added the four Following-page strings and the topic-removal
warn-arm copy to the same Content-Lead visit. Widening tier 1 to these lines
before that visit would redden the suite on copy no ruling has replaced yet.
Flagged for the principal as a finding, not hidden by a narrow grep.
"""

from __future__ import annotations

import re
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from newslens import catalog, db, labels, paths, server

# The term ruling, product-wide.
TERM_LAW = (r"\bsubjects?\b",)

# The mockup's KILLED ON SIGHT list, as patterns. Anchored on word boundaries so
# a legitimate substring ("processed") cannot trip the "progress" pattern.
KILLED_ON_SIGHT = (
    r"most readers",                    # another reader's clock — banned class
    r"\btrending\b", r"\bmost followed\b", r"\bpopular\b",
    r"welcome to newslens",
    r"\bstep \d+ of \d+\b",             # progress chrome
    r"\bprogress\b", r"\bstep counter\b", r"\bchecklist\b",
    r"while you wait",
    r"almost there",
    r"\bwe recommend\b",                # the interface talking about itself
    r"complete your profile",
    r"\bbroad\b", r"\bgranular\b", r"\bspecific\b",   # dead rung vocabulary
    r"\baltitude\b", r"\bresolver\b", r"\bconfidence\b", r"\bscope\b",
    r"\d+\s?%",                         # any percentage
    r"\bETA\b",
)


@pytest.fixture
def fresh(tmp_paths):
    db.migrate()
    paths.SOURCES_FILE.write_text(
        paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8")
    return tmp_paths


@pytest.fixture
def ui(fresh, monkeypatch):
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    box = SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}")
    yield box
    httpd.shutdown()
    httpd.server_close()


def fetch(ui, path="/"):
    with urllib.request.urlopen(ui.base + path, timeout=10) as r:
        return r.read().decode("utf-8")


def reader_text(html):
    """Rendered bytes minus the stylesheet and the behaviour script — CSS
    tokens and fetch routes are syntax, not sentences. The NL_C1 label blob
    stays in: it IS reader copy, and a banned word smuggled through it must
    still go red."""
    out = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script>\s*var NL_C1 = \(.*?</script>", "", out, flags=re.S)


def hits(text, patterns):
    return sorted({m.group(0) for p in patterns
                   for m in re.finditer(p, text, re.IGNORECASE)})


def live_label_strings():
    """Every constant in the ONE string table that a surface still renders.

    RETIRED-NOT-RENDERED entries are skipped by the module's own sweep marker
    (labels.py's docstring): they are record-keeping, and reading them as live
    copy is how a dead phrase gets "fixed" into a ruling it no longer belongs
    to."""
    src = paths.PROJECT_ROOT / "src" / "newslens" / "labels.py"
    out = {}
    for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.+?)$(?:\n(?:\s+.+)$)*",
                         src.read_text(encoding="utf-8"), re.M):
        name = m.group(1)
        if "RETIRED-NOT-RENDERED" in m.group(0):
            continue
        value = getattr(labels, name, None)
        if isinstance(value, str):
            out[name] = value
    return out


# ---------------------------------------------------------------------------
# TIER 1 — the Commissioning's own surfaces carry the whole list
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("job", ["idle", "running", "error"])
def test_the_founding_page_carries_no_banned_reader_word(ui, job):
    server.GEN_JOB.state = job
    server.GEN_JOB.error = "ingest failed: 12 of 37 feeds timed out"
    server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
    text = reader_text(fetch(ui))
    assert not hits(text, TERM_LAW), hits(text, TERM_LAW)
    assert not hits(text, KILLED_ON_SIGHT), hits(text, KILLED_ON_SIGHT)


def test_the_commissioning_copy_block_carries_no_banned_word():
    """The table itself, so a re-pin that never reaches a rendered state still
    goes red at the source."""
    strings = {k: v for k, v in live_label_strings().items()
               if k.startswith("COMMISSION_")}
    assert strings, "the Commissioning copy block vanished from labels.py"
    joined = " ".join(strings.values())
    assert not hits(joined, TERM_LAW), hits(joined, TERM_LAW)
    assert not hits(joined, KILLED_ON_SIGHT), hits(joined, KILLED_ON_SIGHT)


def test_the_catalog_offers_no_banned_reader_word():
    """Catalog names ARE reader copy — they are the biggest block of words on
    the page. A future data edit that adds "Broad Markets" trips here."""
    names = " ".join(catalog.load().names())
    assert not hits(names, TERM_LAW), hits(names, TERM_LAW)


def test_the_tooth_bites(ui, monkeypatch):
    """The tooth proves it can detect the thing it guards — a banned word
    planted in the copy table reaches the rendered page and this sweep sees it.
    Without this, a grep that silently matched nothing would look identical to
    a clean page."""
    monkeypatch.setattr(labels, "COMMISSION_TOPICS_HEAD", "Subjects")
    text = reader_text(fetch(ui))
    assert hits(text, TERM_LAW) == ["Subjects"]

    monkeypatch.setattr(labels, "COMMISSION_TOPICS_HEAD", "Topics")
    monkeypatch.setattr(labels, "COMMISSION_TOPICS_SAY",
                        "Most readers pick three.")
    text = reader_text(fetch(ui))
    assert "Most readers" in hits(text, KILLED_ON_SIGHT)


# ---------------------------------------------------------------------------
# TIER 2 — the term ruling was product-wide
# ---------------------------------------------------------------------------

def test_no_reader_surface_in_the_string_table_says_subject():
    strings = live_label_strings()
    offenders = {k: v for k, v in strings.items() if hits(v, TERM_LAW)}
    assert not offenders, offenders


def test_no_shipped_template_says_subject():
    """The app shell, its popups and its script — the places copy can arrive
    from without passing through the string table."""
    from newslens import webui
    for name in ("PAGE", "POPUPS", "JS"):
        blob = getattr(webui, name)
        assert not hits(blob, TERM_LAW), (name, hits(blob, TERM_LAW))


def test_the_apps_own_rendered_page_says_subject_nowhere(ui):
    """The whole running product, not only the new page: a commissioned reader
    with an edition, swept end to end."""
    ok, _ = server.topic_add("Inflation", "specific")
    assert ok
    con = db.connect()
    try:
        with con:
            con.execute(
                "INSERT INTO briefings (date, story_slots,"
                " corroboration_labels, narrative_text)"
                " VALUES (?, '[]', '[]', ?)",
                (datetime.now().strftime("%Y-%m-%d"),
                 "# Edition\n\n## A story\n\nBody.\n"))
    finally:
        con.close()
    text = reader_text(fetch(ui))
    assert not hits(text, TERM_LAW), hits(text, TERM_LAW)
