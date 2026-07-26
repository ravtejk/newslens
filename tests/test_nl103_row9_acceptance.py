"""NL-103 row 9 — ACCEPTANCE REDS (QA-owned, born failing on purpose).

Destination when the gate accepts the finding:
    tests/test_nl103_row9_acceptance.py
(or fold into tests/test_nl103_register_conformance.py alongside the row-9 pins)

THE FINDING these encode
------------------------
Row 9 replaced an invariant narration with a state-dependent FACT:
    no row for today  -> "Nothing was published."
    a row for today   -> "The saved edition is intact."
The branch's only evidence is `_briefing_row(con, today) is not None`. But a
briefings row for today exists for the ENTIRE post-rank window of a run:

  ranking.persist()            (stage "rank", early)  INSERT/UPDATE story_slots
                                                      and on RE-rank explicitly
                                                      NULLs narrative_text,
                                                      script_text,
                                                      audio_file_path
  ... analysis -> narrative -> editor -> script -> audio ...   (the long tail)
  generate.persist_generation() (stage "persist", LAST) writes the body

The reader's edition body renders from `narrative_text` (server._stories_for),
or from the LAST generation_log entry's structured `stories`. A GenerateError
in the tail appends a `status: "failed"` log entry for the same date with no
`stories` key, and `_log_entry_for` takes the last entry for a date. So after a
failed regenerate the row exists, the panel says the saved edition is intact,
and the edition the reader can open is EMPTY.

FIX CONTRACT (any one of these makes both tests green)
------------------------------------------------------
1. Gate the "intact" sentence on the edition being READABLE, not on row
   existence: e.g. `_saved is not None and (_saved["narrative_text"] or
   (_log_entry_for(_today) or {}).get("stories"))`. The else-branch then needs
   a third honest sentence for "a row exists but no body does" — the panel must
   not fall back to "Nothing was published." if the row is on the record.
   -- OR --
2. Cut the second sentence and state only what the panel can actually know.
   -- OR --
3. Make the pipeline's row write atomic with the body write, so a row for today
   really does imply a readable edition (a build-side fix; then these tests pass
   unchanged).

Whichever lands, the invariant these tests hold is: THE PANEL NEVER CLAIMS AN
INTACT SAVED EDITION WHEN THE READER'S EDITION FOR TODAY IS EMPTY.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from newslens import db, paths, server

from test_server import ui, get, seed_briefing            # noqa: F401


INTACT = "The saved edition is intact."
NOTHING = "Nothing was published."


@pytest.fixture
def errjob(monkeypatch):
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    server.GEN_JOB.state = "error"
    server.GEN_JOB.error = "script stage failed"
    yield


def _panel(page: str) -> str:
    today = page.split('id="view-today"')[1].split('id="view-following"')[0]
    return re.search(r'<div class="state-panel">.*?</div>', today, re.S).group(0)


def test_row9_intact_is_never_claimed_for_a_body_less_row(ui, errjob):
    """RED. Fresh run: the rank stage committed today's row, a later stage
    failed. Nothing readable was ever published — the panel must not say the
    saved edition is intact."""
    today = datetime.now().strftime("%Y-%m-%d")
    con = db.connect()
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " token_cost, generated_at) VALUES (?, ?, ?, ?, ?)",
        (today, json.dumps([]), json.dumps([]), json.dumps({}),
         "2026-07-26T04:00:00.000Z"))
    con.commit()
    con.close()

    _, _, body = get(ui, "/")
    panel = _panel(body.decode("utf-8"))
    assert INTACT not in panel, (
        "the panel claims an intact saved edition for a row with no body:\n"
        + panel)


def test_row9_intact_matches_what_the_reader_can_actually_open(ui, errjob):
    """RED. The realistic failed-regenerate position, end to end: today's
    edition was published and readable; a regenerate re-ranked (archiving the
    body to briefings_history and NULLing the live row) and then failed. The
    panel's claim must agree with the edition the reader can open."""
    today = datetime.now().strftime("%Y-%m-%d")
    con = db.connect()
    slots = seed_briefing(con)                       # published + readable
    log = Path(paths.DATA_DIR) / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({
        "date": today, "status": "ok", "sample": False,
        "stories": [{"tier": "full", "headline": "Chip export controls pass",
                     "lede": "The lede sentence.",
                     "why_it_matters": "Concrete effects.",
                     "watch_for": "The vote."}]}) + "\n", encoding="utf-8")

    code, _, body = get(ui, f"/edition?date={today}")
    assert "Chip export controls pass" in body.decode("utf-8"), \
        "fixture broken: the edition was not readable to begin with"

    # ranking.persist()'s re-rank branch, verbatim in shape
    #
    # NL-106 NOTE (2026-07-26): the pipeline can no longer PRODUCE this state on
    # the regenerate path — rank now stages its new selection in
    # briefings_pending and leaves the readable live row alone. This hand-built
    # construction is kept DELIBERATELY, unchanged: it is a renderer-level pin
    # (the panel must never claim intact for an unreadable edition, whatever put
    # the DB in that state), and changing it would destroy the regression-red
    # signature captured in the NL-103 QA report §7. The pipeline-level version
    # of this scenario, with its outcome flipped to old-edition-intact, lives in
    # tests/test_nl106_stage_and_promote.py::
    #   test_g6_a_failed_regenerate_leaves_the_old_edition_intact_end_to_end
    row = con.execute("SELECT * FROM briefings WHERE date=?", (today,)).fetchone()
    con.execute(
        "INSERT INTO briefings_history (briefing_id, date, story_slots,"
        " corroboration_labels, narrative_text, script_text, audio_file_path,"
        " token_cost, generated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (row["id"], row["date"], row["story_slots"], row["corroboration_labels"],
         row["narrative_text"], row["script_text"], row["audio_file_path"],
         row["token_cost"], row["generated_at"]))
    con.execute(
        "UPDATE briefings SET story_slots = ?, corroboration_labels = ?,"
        " token_cost = ?, generated_at = ?, narrative_text = NULL,"
        " script_text = NULL, audio_file_path = NULL WHERE id = ?",
        (json.dumps(slots), row["corroboration_labels"], json.dumps({}),
         "2026-07-26T05:00:00.000Z", row["id"]))
    con.commit()
    con.close()

    # generate.run_generate's failed-run log entry (generate.py:3100)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": today, "status": "failed",
                             "error": "script stage failed", "steps": [],
                             "total_usd": 0.0, "warnings": []}) + "\n")

    code, _, body = get(ui, f"/edition?date={today}")
    readable = "Chip export controls pass" in body.decode("utf-8")

    _, _, body = get(ui, "/")
    panel = _panel(body.decode("utf-8"))
    claims_intact = INTACT in panel

    assert claims_intact == readable, (
        f"panel claims intact={claims_intact} but the reader's edition for "
        f"{today} is readable={readable}:\n{panel}")
