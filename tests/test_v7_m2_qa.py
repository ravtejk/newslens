"""v7-M2 QA extensions (QA-owned, declared at the M2 gate pass 2026-07-14).

Adversarial pins for contracts the M2 batch ships but test_v7_m2.py leaves
un-pinned — each was first proven by probe against the landed tree, then
frozen here so the property survives NL-68's stop:

  * thread page: the never-a-dead-link law under PURGED and CORRUPT ledger
    dates (the shipped fixtures only cover dates that exist as briefings);
  * thread page: lifecycle verbs scoped per status for dormant/dismissed
    (the shipped test covers active only);
  * Following spine: a HOSTILE generation log can neither mint nor suppress
    the ●UPDATED stamp (the stamp is thread_deltas-vs-MAX(briefings.date),
    dispatch item 2 — proven here mechanically, not by reading the code);
  * quiet fold: the singular noun at n=1;
  * deep view: the jumplist/section pairing across degradation states
    (k=5/4/3, discrepancies-only 'still open', slate-only entries);
  * the whole-document heading law (one h1 per view, no skipped levels,
    every section-label class an h2 — the M2 tests pin single surfaces);
  * paths guard: redirection outranks an ACTIVE sanction (the property the
    doctor child depends on; unpinned, a future 'sanction first' reorder
    would silently re-open the v7-M1 pinhole);
  * the real-state tripwire sees IN-PLACE db/log rewrites (born red against
    the pre-widening conftest: dir mtime/listing miss append/clobber — the
    2026-07-14 incident shape; flipped only by the _REAL_STATE_FILES
    widening that landed with this file).

Fully offline; autouse sandbox; fixtures only, never the live DB.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from newslens import db, labels, paths, server

DATE = "2026-07-10"


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _con():
    db.migrate()
    return db.connect()


def _mem(con, topic, status="active", note=None):
    return con.execute(
        "INSERT INTO memory (topic, status, principal_note, status_changed_at,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (topic, status, note, iso_now(), iso_now(), iso_now())).lastrowid


def _briefing(con, date):
    con.execute("INSERT INTO briefings (date, story_slots, generated_at)"
                " VALUES (?, ?, ?)", (date, "[]", f"{date}T04:44:00.000Z"))


def _delta(con, tid, date, what="Something moved.", signif="It matters."):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json) VALUES (?, ?, 1, 'advances',"
        " ?, ?, ?)", (tid, date, what, signif, json.dumps(["S1"])))


# ===========================================================================
# 1. Thread page — the never-a-dead-link law + verb scoping per status
# ===========================================================================

def test_thread_editions_purged_or_corrupt_dates_never_link():
    """A ledger date with NO briefing row (edition purged/never existed) and a
    calendar-impossible date must render as plain dates — never openEdition
    links (NL-60 dead-link law; the shipped fixture never exercises this)."""
    con = _con()
    _briefing(con, "2026-07-05")
    tid = _mem(con, "Purge Case")
    _delta(con, tid, "2026-07-05")
    _delta(con, tid, "2026-07-08", "Move in a purged edition.")
    _delta(con, tid, "2026-13-45", "Corrupt-dated move.")
    mrow = con.execute("SELECT * FROM memory WHERE id = ?", (tid,)).fetchone()
    html = server._render_thread_page(con, mrow)
    con.close()
    ed_sec = html.split("-editions\">", 1)[1].split("</div>")[0]
    assert "openEdition('2026-07-05', event)" in ed_sec       # real edition links
    assert "openEdition('2026-07-08'" not in ed_sec           # purged: no link
    assert "<span>Jul 8</span>" in ed_sec                     # ...but still named
    assert "openEdition('2026-13-45'" not in ed_sec           # corrupt: no link
    assert "<span>2026-13-45</span>" in ed_sec


def test_thread_verbs_scoped_for_dormant_and_dismissed():
    """§10 verb scoping beyond the shipped active-only pin: dormant → Edit
    note + Resume (no Stop/Delete); dismissed → Resume + Delete (no Edit
    note/Stop)."""
    con = _con()
    d1 = _mem(con, "Dormant One", status="dormant")
    d2 = _mem(con, "Dismissed One", status="dismissed_user")
    rows = {r["id"]: r for r in con.execute("SELECT * FROM memory")}

    def verbs(html):
        sec = html.split('class="thread-verbs"')[1].split("</div>")[0]
        return {v for v in (labels.VERB_EDIT_NOTE, labels.VERB_STOP,
                            labels.VERB_RESUME, labels.VERB_DELETE)
                if f">{v}</button>" in sec}

    dorm = server._render_thread_page(con, rows[d1])
    dism = server._render_thread_page(con, rows[d2])
    con.close()
    assert verbs(dorm) == {labels.VERB_EDIT_NOTE, labels.VERB_RESUME}
    assert verbs(dism) == {labels.VERB_RESUME, labels.VERB_DELETE}


# ===========================================================================
# 2. Following spine — the stamp is DB truth; a hostile log changes nothing
# ===========================================================================

def test_updated_stamp_immune_to_hostile_generation_log():
    """Dispatch item 2, proven mechanically: 'updated this edition' is a
    thread_deltas row dated MAX(briefings.date). A generation log that CLAIMS
    the quiet thread updated (and the updated one didn't) must change not one
    byte of the Following render."""
    con = _con()
    _briefing(con, DATE)
    loud = _mem(con, "Loud Thread")
    _delta(con, loud, DATE, "Moved today.")
    quiet = _mem(con, "Quiet Thread")
    _delta(con, quiet, "2026-07-05", "Old move.")   # not this edition

    baseline = server._render_following(con)        # log absent
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    hostile = [{"date": DATE, "threads_updated": ["Quiet Thread"],
                "note": "hostile: claims the quiet thread moved"},
               {"date": DATE, "threads_updated": [],
                "note": "hostile: claims nothing moved"}]
    log.write_text("\n".join(json.dumps(e) for e in hostile) + "\n",
                   encoding="utf-8")
    with_log = server._render_following(con)
    con.close()
    assert with_log == baseline                      # not one byte
    arts = re.findall(r'<article class="thread">.*?</article>', baseline, re.S)
    stamped = [a for a in arts if labels.UPDATED_STAMP in a]
    assert len(stamped) == 1 and ">Loud Thread</a>" in stamped[0]
    fold = baseline.split('class="quiet-fold"')[1]
    assert ">Quiet Thread</a>" in fold.split("</details>")[0]


def test_quiet_fold_singular_noun_at_one():
    con = _con()
    _briefing(con, DATE)
    loud = _mem(con, "Loud Thread")
    _delta(con, loud, DATE)
    _mem(con, "Only Quiet One")
    html = server._render_following(con)
    con.close()
    summary = html.split('class="quiet-fold"')[1].split("</summary>")[0]
    assert f"1 {labels.QUIET_FOLD_NOUN_ONE}" in summary
    assert labels.QUIET_FOLD_NOUN not in summary     # never "1 quiet threads"


# ===========================================================================
# 3. Deep view — jumplist/section pairing across degradation states
# ===========================================================================

_SOURCES = [{"key": "S1", "outlet": "The Hill", "title": "T",
             "url": "http://x.invalid", "kind": "cluster-full-text",
             "retrieved_at": "2026-07-10T00:00:00Z"}]
_SLATE = {"facts", "mechanism", "effects", "open", "sources"}


def _brief_variant(effects=False, unknowns=False, watch=False, disc=False,
                   numbers=False):
    b = {"pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}],
         "mechanism": "M [S1].", "effects": [], "unknowns": [], "watch": [],
         "ledger": [], "sources": list(_SOURCES)}
    if effects:
        b["effects"] = [{"holder": "H", "effect": "E.", "cites": ["S1"]}]
    if unknowns:
        b["unknowns"] = [{"question": "q?", "why_material": "w",
                          "would_resolve": "r"}]
    if watch:
        b["watch"] = [{"observable": "o", "when": "soon", "signals": "s"}]
    if disc:
        b["ledger"].append({"discrepancy": True,
                            "a": {"value": "9 dead", "cites": ["S1"]},
                            "b": {"value": "12 dead", "cites": ["S1"]},
                            "note": "n"})
    if numbers:
        b["ledger"].append({"claim": "At least 46 hurt.", "cites": ["S1"]})
    return b


def test_jumplist_and_sections_pair_at_every_degradation_state():
    """Zero dead anchors and zero unlisted sections at every k: the jumplist
    entry set must EQUAL the rendered slate-section set in every content
    state, and never exceed the five-section slate (retired numbers/
    unresolved entries can never resurface)."""
    scenarios = {
        "k5-full": _brief_variant(effects=True, unknowns=True, watch=True,
                                  disc=True, numbers=True),
        "k4-no-effects": _brief_variant(unknowns=True),
        "k4-disc-only-open": _brief_variant(effects=True, disc=True),
        "k4-watch-only-open": _brief_variant(effects=True, watch=True),
        "k3-minimal": _brief_variant(),
    }
    for name, brief in scenarios.items():
        html = server._render_deep_view("story-0", "H",
                                        {"header": {}, "brief": brief}, DATE)
        jump = html.split('class="deep-jumplist"')[1].split("</p>")[0]
        hrefs = re.findall(r'href="#story-0-([a-z]+)"', jump)
        sections = set(re.findall(r'id="story-0-([a-z]+)"', html))
        assert set(hrefs) == sections & _SLATE, (name, hrefs, sections)
        assert set(hrefs) <= _SLATE and len(hrefs) == len(set(hrefs)), name
        for a in hrefs:
            assert f'id="story-0-{a}"' in html, (name, a)   # every anchor live
    # the boundary cases by name:
    k5 = server._render_deep_view("story-0", "H",
                                  {"header": {}, "brief": scenarios["k5-full"]},
                                  DATE)
    k5_jump = k5.split('class="deep-jumplist"')[1].split("</p>")[0]
    assert len(re.findall(r'href="#story-0-', k5_jump)) == 5
    disc_only = server._render_deep_view(
        "story-0", "H", {"header": {}, "brief": scenarios["k4-disc-only-open"]},
        DATE)
    assert 'id="story-0-open"' in disc_only          # discrepancies EARN the section
    assert 'class="deep-open-discrepancies"' in disc_only
    minimal = server._render_deep_view(
        "story-0", "H", {"header": {}, "brief": scenarios["k3-minimal"]}, DATE)
    assert 'id="story-0-open"' not in minimal        # and absence leaves no residue
    assert 'id="story-0-effects"' not in minimal


# ===========================================================================
# 4. The whole-document heading law
# ===========================================================================

def test_document_heading_law_across_all_views():
    """One h1 per document view; no skipped heading levels inside a view;
    every section-label class is an h2 — across Today, Following, Archive AND
    every generated thread page in one built document (the shipped pins cover
    single surfaces; this is the document-wide law the gate names)."""
    con = _con()
    _briefing(con, datetime.now().strftime("%Y-%m-%d"))
    loud = _mem(con, "Loud Thread", note="a note")
    _delta(con, loud, datetime.now().strftime("%Y-%m-%d"))
    _mem(con, "Quiet Thread")
    _mem(con, "Dormant Thread", status="dormant")
    _mem(con, "Dismissed Thread", status="dismissed_user")
    page, _ = server.build_page(con)
    con.close()
    views = re.findall(r'<section[^>]*class="view[^"]*"[^>]*>.*?</section>',
                       page, re.S)
    assert len(views) >= 7                            # 3 shell views + 4 thread pages
    for v in views:
        assert v.count("<h1") == 1, v[:80]
        seq = [int(t) for t in re.findall(r"<h([1-6])", v)]
        assert seq[0] == 1 and all(b - a <= 1 for a, b in zip(seq, seq[1:])), \
            (v[:80], seq)
    for cls in ("deep-section-label", "section-h", "brief-label"):
        tags = set(re.findall(rf'<(\w+)[^>]*class="{cls}"', page))
        assert tags <= {"h2"}, (cls, tags)


# ===========================================================================
# 5. Paths guard — redirection outranks an ACTIVE sanction
# ===========================================================================

def _run_paths_child(code, extra_env):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTEST_CURRENT_TEST", "NEWSLENS_REAL_DATA",
                        "NEWSLENS_DATA_DIR", "NEWSLENS_DB_PATH")}
    env.update(extra_env)
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=str(paths.PROJECT_ROOT), env=env,
                          timeout=60)


def test_data_dir_override_outranks_active_sanction(tmp_path):
    """The property the QA suite's doctor child stands on: a process that IS
    sanctioned (allow_real_paths(), as doctor.main/cli.main call, or
    NEWSLENS_REAL_DATA=1) still resolves the NEWSLENS_DATA_DIR override, never
    the real location. A 'sanction wins' reorder in paths.__getattr__ would
    re-open the v7-M1 pinhole with every guard test still green — this is the
    red test that catches it."""
    sb = tmp_path / "sb"
    r1 = _run_paths_child(
        "from newslens import paths; paths.allow_real_paths(); "
        "print(paths.DATA_DIR); print(paths.DB_PATH)",
        {"NEWSLENS_DATA_DIR": str(sb)})
    assert r1.returncode == 0, r1.stderr
    assert r1.stdout.splitlines() == [str(sb), str(sb / "newslens.db")]
    r2 = _run_paths_child(
        "from newslens import paths; print(paths.DATA_DIR)",
        {"NEWSLENS_DATA_DIR": str(sb), "NEWSLENS_REAL_DATA": "1"})
    assert r2.returncode == 0, r2.stderr
    assert r2.stdout.strip() == str(sb)


# ===========================================================================
# 6. The real-state tripwire sees in-place rewrites (born red pre-widening)
# ===========================================================================

def _conftest_module():
    for m in list(sys.modules.values()):
        f = (getattr(m, "__file__", "") or "").replace("\\", "/")
        if f.endswith("tests/conftest.py"):
            return m
    raise AssertionError("tests/conftest.py module not found in sys.modules")


def test_tripwire_snapshot_sees_inplace_db_and_log_rewrites(tmp_path):
    """Structural half: the landed watchlist stats the db and the log
    individually. Mechanical half: with the watch pointed at a tmp mirror
    built by the same rule, _real_state_snapshot distinguishes (a) a file
    create, (b) a log APPEND and (c) an in-place db rewrite — (b)/(c) are the
    2026-07-14 incident shape that dir mtime+listing alone provably miss
    (probe-verified: they passed the pre-widening tripwire).

    The globals are swapped/restored MANUALLY inside the test body (not via
    the monkeypatch fixture): monkeypatch is instantiated before the autouse
    tripwire (scrub_env depends on it), so its undo would land AFTER the
    tripwire's own after-snapshot and the mirror paths would leak into it."""
    cf = _conftest_module()
    # (structural) the widening is landed:
    watched = {str(p) for p in cf._REAL_STATE_FILES}
    assert str(paths._GUARDED["DB_PATH"]) in watched
    assert str(cf._REAL_DATA_DIR / "generation_log.jsonl") in watched

    # (mechanical) same rule, tmp mirror:
    fake = tmp_path / "fakereal"
    (fake / "briefings").mkdir(parents=True)
    dbf = fake / "newslens.db"
    logf = fake / "generation_log.jsonl"
    dbf.write_bytes(b"SQLite format 3\x00 original")
    logf.write_text('{"real": "line"}\n', encoding="utf-8")
    saved = (cf._REAL_DATA_DIR, cf._REAL_STATE_FILES)
    try:
        cf._REAL_DATA_DIR = fake
        cf._REAL_STATE_FILES = (dbf, logf)
        base = cf._real_state_snapshot()
        assert cf._real_state_snapshot() == base      # stable at rest
        with open(logf, "a", encoding="utf-8") as fh:  # (b) the log-append shape
            fh.write('{"fake": "clobber"}\n')
        after_append = cf._real_state_snapshot()
        assert after_append != base
        dbf.write_bytes(b"SQLite format 3\x00 rewritten in place, longer")  # (c)
        after_rewrite = cf._real_state_snapshot()
        assert after_rewrite != after_append
        (fake / "created.txt").write_text("x", encoding="utf-8")            # (a)
        assert cf._real_state_snapshot() != after_rewrite
    finally:
        cf._REAL_DATA_DIR, cf._REAL_STATE_FILES = saved
