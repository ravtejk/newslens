"""NL-17 M3 — the twin-move PROPOSAL instrument and the YAML-side XOR door.

WHAT HAS TO BE TRUE, in order of how much damage getting it wrong would do:

  1. IT NEVER TOUCHES sources.yaml. That file is the principal's standing local
     edit. An agent rewriting it is the one move nobody sanctioned, and "we
     remembered not to" is not a property — so it is pinned structurally AND
     behaviourally, the same two-guarantee posture the M1 backfill carries.
  2. IT NEVER WRITES A LEDGER ROW. Remy's law: the vocabulary_moves row IS the
     atomic switch, so a proposal that ledgered itself would BE the move.
  3. IT NEVER FORCES A KIND. `China-Taiwan` has no honest seat in the closed
     org/place/person vocabulary; naming that is the whole reason the kind
     vocabulary is closed.
  4. THE YAML DOOR DEGRADES LOUD, NEVER DEAD. A collision is reported, never
     raised — his file legitimately carries a tag whose concept is mid-move,
     and the ledger decides whether it scores.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
import sqlite3

import pytest

from newslens import config, db, entities, memory, paths, steering, vocab_move


def _py_code(src: str) -> str:
    from test_nl17_m1_fixloop1_instead import _py_code as strip
    import textwrap
    return "".join(strip(textwrap.dedent(src)).split())


@pytest.fixture()
def con():
    db.migrate(db_path=paths.DB_PATH)
    c = db.connect(paths.DB_PATH)
    yield c
    c.close()


class _Cfg:
    def __init__(self, granular=(), broad=()):
        self.interests_granular = list(granular)
        self.interests_broad = list(broad)


YAML = """interests:
  broad:
    - Central Bank Policy
  granular:
    - Oil Markets
    - OPEC+
    - Strait of Hormuz        # standing-interest twin
    - Federal Reserve
    - ECB
    - Recession Risk
"""


# =========================================================================
# 1 — IT NEVER TOUCHES HIS FILE, AND NEVER WRITES THE SWITCH
# =========================================================================

def test_the_module_has_no_write_path_to_sources_yaml_or_the_ledger():
    """BORN RED (the module does not exist pre-diff). Structural: no write verb
    against his file, and no INSERT into the ledger whose row IS the move.

    `shutil.copy2` is present and allowed — it writes the BACKUP, a new file at
    a new path; what must never appear is a write whose TARGET is his file.

    THE DISTINCTION THIS PIN HAD TO LEARN (it failed first, correctly): the
    ledger INSERT *does* appear in the module — inside `reverse_script`, as text
    the module EMITS for him to run later. Emitting a script that says INSERT is
    not performing one. So the scan excludes that function's body, and the
    companion below proves the verb lives nowhere else."""
    whole = _py_code(inspect.getsource(vocab_move))
    emitted = _py_code(inspect.getsource(vocab_move.reverse_script))
    performed = whole.replace(emitted, "")
    for verb in ("sources_file.write_text", "sources_file.write_bytes",
                 "sources_file.open('w')", 'sources_file.open("w")',
                 "INSERTINTOvocabulary_moves", "con.execute", "con.commit"):
        assert verb not in performed, verb


def test_the_only_ledger_insert_is_the_one_the_reverse_script_emits():
    """The companion: the INSERT exists exactly once, inside the emitted script,
    and the module never executes it. A future diff that moves the verb into
    live code fails here."""
    whole = _py_code(inspect.getsource(vocab_move))
    emitted = _py_code(inspect.getsource(vocab_move.reverse_script))
    assert whole.count("INSERTINTOvocabulary_moves") == 1
    assert emitted.count("INSERTINTOvocabulary_moves") == 1
    assert "cursor()" not in whole and "executescript" not in whole


def test_building_a_proposal_leaves_the_file_byte_identical(tmp_path):
    """BORN RED. The behavioural half: run the whole proposal over a real file
    and prove every byte is where it was."""
    f = tmp_path / "sources.yaml"
    f.write_text(YAML, encoding="utf-8")
    before = f.read_bytes()
    prop = vocab_move.build(_Cfg(granular=["OPEC+", "Federal Reserve", "ECB"]),
                            YAML, "sources.yaml", tier="all")
    assert prop["mover_names"]
    b = vocab_move.backup(f, tmp_path / "out")
    vocab_move.reverse_script(b, f, prop["mover_names"])
    assert f.read_bytes() == before
    # and the backup is a COPY, byte-identical, at a different path
    assert b.read_bytes() == before and b != f


def test_the_proposal_writes_no_vocabulary_moves_row(con, tmp_path):
    """BORN RED. The ledger row is the switch (ENG R2 :141) — proposing must
    leave the switch exactly where it was."""
    before = con.execute("SELECT COUNT(*) FROM vocabulary_moves").fetchone()[0]
    vocab_move.build(_Cfg(granular=["OPEC+"]), YAML, "sources.yaml", tier="all")
    after = con.execute("SELECT COUNT(*) FROM vocabulary_moves").fetchone()[0]
    assert after == before == 0


# =========================================================================
# 2 — THE CLASSIFICATION IS THE COUNCIL'S, AND IT NEVER FORCES A FIT
# =========================================================================

def test_the_enumeration_is_the_councils_five_move_three_stay():
    """ANTI-DRIFT: widening this list is a council act, so a silent edit fails
    here (the same posture as the backfill's blessed-list pin)."""
    movers = [c for c, d, _k, _t in vocab_move.TWINS if d == vocab_move.MOVE]
    stays = [c for c, d, _k, _t in vocab_move.TWINS if d == vocab_move.STAY]
    assert movers == ["Federal Reserve", "ECB", "OPEC+", "Strait of Hormuz",
                      "China-Taiwan"]
    assert stays == ["Credit Default Risk", "Recession Risk", "Stagflation"]
    pilot = [c for c, _d, _k, t in vocab_move.TWINS if t == vocab_move.TIER_PILOT]
    assert pilot == ["Federal Reserve", "ECB", "OPEC+"]


def test_china_taiwan_is_reported_kindless_never_forced(con):
    """A relation between two actors is not an actor. The closed kind vocabulary
    exists to make that refusable, and the same refusal the settle door makes
    must show up here rather than being smoothed into 'org'."""
    rows = {r["concept"]: r for r in
            vocab_move.classify(_Cfg(granular=["China-Taiwan"]))}
    ct = rows["China-Taiwan"]
    assert ct["kind"] == ""
    assert "no honest kind" in ct["note"]
    prop = vocab_move.build(_Cfg(granular=["China-Taiwan"]), YAML,
                            "sources.yaml", tier="all")
    assert [r["concept"] for r in prop["blocked"]] == ["China-Taiwan"]


def test_a_concept_already_gone_from_his_file_is_not_proposed_again(con):
    """He edits sources.yaml. A tag he already removed must not show up as a
    removal — the proposal reads his file as it IS."""
    rows = {r["concept"]: r for r in vocab_move.classify(_Cfg(granular=[]))}
    assert rows["Federal Reserve"]["present"] is False
    assert "nothing to remove" in rows["Federal Reserve"]["note"]
    prop = vocab_move.build(_Cfg(granular=[]), YAML, "sources.yaml", tier="all")
    assert prop["mover_names"] == []
    assert prop["diff"] == ""


# =========================================================================
# 3 — THE PROPOSED FILE IS SURGICAL
# =========================================================================

def test_the_proposed_yaml_removes_the_movers_and_nothing_else():
    """Only an interests item whose VALUE is a moved concept goes — trailing
    comments and every unrelated line survive."""
    out = vocab_move.proposed_yaml(YAML, ["OPEC+", "Federal Reserve", "ECB",
                                          "Strait of Hormuz"])
    for gone in ("- OPEC+", "- Federal Reserve", "- ECB", "- Strait of Hormuz"):
        assert gone not in out
    for kept in ("- Oil Markets", "- Recession Risk", "- Central Bank Policy",
                 "interests:", "  broad:", "  granular:"):
        assert kept in out
    assert len(out.splitlines()) == len(YAML.splitlines()) - 4


def test_a_comment_bearing_line_is_matched_on_its_value(con):
    """`- Strait of Hormuz        # standing-interest twin` is the real shape in
    his file; matching the raw line would have missed it and produced a
    half-applied proposal."""
    out = vocab_move.proposed_yaml(YAML, ["Strait of Hormuz"])
    assert "Strait of Hormuz" not in out
    assert "- Oil Markets" in out


def test_the_reverse_script_reverses_by_appending_never_by_mutating():
    """0026 is append-only and `steering.live_moves` DERIVES liveness from
    `reversal_of`. A reverse script that UPDATEd a column would be undoable
    only by breaking the ledger's own trigger."""
    script = vocab_move.reverse_script(
        paths.DATA_DIR / "sources.yaml.pre-move-X",
        paths.DATA_DIR / "sources.yaml", ["OPEC+"])
    assert "INSERT INTO vocabulary_moves" in script
    assert "reversal_of" in script
    assert "UPDATE vocabulary_moves" not in script
    assert "DELETE FROM vocabulary_moves" not in script


# =========================================================================
# 4 — THE YAML-SIDE XOR DOOR: LOUD, NEVER DEAD
# =========================================================================

def test_the_door_is_silent_when_no_entity_is_watched(con):
    """Zero live entities = zero collisions. An entities row nothing follows is
    not a vocabulary member, so it must not manufacture a collision."""
    con.execute("INSERT INTO entities (canonical_name, kind, aliases)"
                " VALUES ('Federal Reserve', 'org', 'Fed')")
    con.commit()
    assert vocab_move.yaml_door_collisions(
        con, _Cfg(granular=["Federal Reserve"])) == []


def test_the_door_reports_a_live_collision_by_tag_and_alias(con):
    """The XOR the criterion is about: a followed actor and a tag naming the
    same concept. Reported on the canonical name AND on any alias."""
    _o, tid = memory.add_thread_at_altitude(
        con, "Federal Reserve", altitude="entity",
        disclosure="Federal Reserve (agency)", primary_entity="Fed",
        source="auto")
    eid, _d = entities.mint_or_match(con, disclosure="Federal Reserve (agency)",
                                     primary_entity="Fed")
    con.execute("UPDATE memory SET entity_id = ? WHERE id = ?", (eid, tid))
    con.commit()
    hits = vocab_move.yaml_door_collisions(
        con, _Cfg(granular=["Federal Reserve", "Oil Markets"], broad=["Fed"]))
    by_tag = {h["tag"]: h for h in hits}
    assert set(by_tag) == {"Federal Reserve", "Fed"}
    assert by_tag["Federal Reserve"]["level"] == "granular"
    assert by_tag["Fed"]["level"] == "broad"
    assert all(h["entity"] == "Federal Reserve" for h in hits)


def test_the_door_never_raises_even_on_a_pre_0024_record(tmp_path):
    """DEGRADE-LOUD, NEVER A DEAD RUN (Rook's dissent, ENG :119). A door that
    can kill a generate is not a door, it is a brick."""
    raw = sqlite3.connect(str(tmp_path / "bare.db"))
    try:
        assert vocab_move.yaml_door_collisions(
            raw, _Cfg(granular=["Federal Reserve"])) == []
    finally:
        raw.close()


# =========================================================================
# 5 — FIX LOOP 1 / F-1: THE REVERSE SCRIPT IS EXECUTED, NOT READ
# =========================================================================
# QA ran the emitted script for the first time and it died on its first sqlite3
# call — Python-`repr` commas surviving sh word-splitting, plus a shell variable
# interpolated unquoted into SQL. `set -eu` then aborted BEFORE the YAML
# restore, so the insurance artifact had a 0% success rate. The old pin could
# not see any of it: it asserted emitted TEXT. These pins EXECUTE.

def _ledger_fixture(db_path):
    """A record with one live tag->entity move, as the blessed apply would leave
    it (0026 CHECK requires entity_id whenever to_vocab='entity')."""
    db.migrate(db_path=db_path)
    con = db.connect(db_path)
    con.execute("INSERT INTO entities (canonical_name, kind, aliases)"
                " VALUES ('Federal Reserve', 'org', 'Fed')")
    eid = con.execute("SELECT id FROM entities").fetchone()[0]
    for concept in ("Federal Reserve", "ECB"):
        con.execute(
            "INSERT INTO vocabulary_moves (concept, from_vocab, to_vocab,"
            " entity_id, blessed_by, note) VALUES (?, 'tag', 'entity', ?, ?, '')",
            (concept, eid, "test"))
    con.commit()
    return con


@pytest.mark.skipif(shutil.which("sqlite3") is None,
                    reason="sqlite3 CLI absent — the emitted script needs it")
def test_the_emitted_reverse_script_actually_runs_and_unwinds(tmp_path):
    """BORN RED (F-1). Exit 0, reversal rows appended, YAML restored byte-
    identical. The whole point of an insurance artifact is that it runs."""
    db = tmp_path / "newslens.db"
    con = _ledger_fixture(db)

    sources = tmp_path / "sources.yaml"
    sources.write_text(YAML, encoding="utf-8")
    original = sources.read_bytes()
    b = vocab_move.backup(sources, tmp_path / "out")
    # the "applied" world: his file post-move, ledger rows live
    sources.write_text(vocab_move.proposed_yaml(YAML, ["Federal Reserve", "ECB"]),
                       encoding="utf-8")
    assert sources.read_bytes() != original

    script = tmp_path / "reverse-move.sh"
    script.write_text(vocab_move.reverse_script(b, sources,
                                                ["Federal Reserve", "ECB"]),
                      encoding="utf-8")
    script.chmod(0o755)
    proc = subprocess.run(["/bin/sh", str(script)], capture_output=True,
                          text=True, env={**os.environ,
                                          "NEWSLENS_DB_PATH": str(db)})
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    # 1. the ledger reversed, by APPEND
    rows = con.execute("SELECT concept, from_vocab, to_vocab, reversal_of,"
                       " blessed_by FROM vocabulary_moves ORDER BY id").fetchall()
    assert len(rows) == 4, rows
    reversals = [r for r in rows if r[3] is not None]
    assert len(reversals) == 2
    assert all(r[1] == "entity" and r[2] == "tag" for r in reversals)
    assert all(r[4] == "reverse-script" for r in reversals)
    assert steering.live_moves(con) == []          # nothing live any more

    # 2. the YAML restored byte-identical to the pre-move original
    assert sources.read_bytes() == original
    con.close()


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI absent")
def test_running_the_reverse_script_twice_appends_nothing(tmp_path):
    """BORN RED (F-1). The `NOT IN` guard is the idempotency, and it only counts
    if the script reaches it — which the pre-fix emission never did."""
    db = tmp_path / "newslens.db"
    con = _ledger_fixture(db)
    sources = tmp_path / "sources.yaml"
    sources.write_text(YAML, encoding="utf-8")
    b = vocab_move.backup(sources, tmp_path / "out")
    script = tmp_path / "reverse-move.sh"
    script.write_text(vocab_move.reverse_script(b, sources, ["Federal Reserve"]),
                      encoding="utf-8")
    env = {**os.environ, "NEWSLENS_DB_PATH": str(db)}
    subprocess.run(["/bin/sh", str(script)], capture_output=True, env=env,
                   check=True)
    after_one = con.execute("SELECT COUNT(*) FROM vocabulary_moves").fetchone()[0]
    subprocess.run(["/bin/sh", str(script)], capture_output=True, env=env,
                   check=True)
    after_two = con.execute("SELECT COUNT(*) FROM vocabulary_moves").fetchone()[0]
    assert after_one == 3 and after_two == 3
    con.close()


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI absent")
def test_a_concept_carrying_an_apostrophe_does_not_break_the_script(tmp_path):
    """BORN RED. None of the eight twins contains `'`, but the escaping rule has
    to exist BEFORE one does — an unescaped apostrophe here is a syntax error at
    best and an injection seam at worst."""
    db = tmp_path / "newslens.db"
    con = _ledger_fixture(db)
    eid = con.execute("SELECT id FROM entities").fetchone()[0]
    con.execute("INSERT INTO vocabulary_moves (concept, from_vocab, to_vocab,"
                " entity_id, blessed_by, note)"
                " VALUES (?, 'tag', 'entity', ?, 'test', '')",
                ("Moody's", eid))
    con.commit()
    sources = tmp_path / "sources.yaml"
    sources.write_text(YAML, encoding="utf-8")
    b = vocab_move.backup(sources, tmp_path / "out")
    script = tmp_path / "reverse-move.sh"
    script.write_text(vocab_move.reverse_script(b, sources, ["Moody's"]),
                      encoding="utf-8")
    proc = subprocess.run(["/bin/sh", str(script)], capture_output=True,
                          text=True,
                          env={**os.environ, "NEWSLENS_DB_PATH": str(db)})
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    rev = con.execute("SELECT concept FROM vocabulary_moves"
                      " WHERE reversal_of IS NOT NULL").fetchall()
    assert [r[0] for r in rev] == ["Moody's"]
    con.close()


# =========================================================================
# 6 — GATE G-1: A HOSTILE CONCEPT NAME CANNOT REACH A SHELL
# =========================================================================
# The gate attacked the F-1 fix instead of reading it, and found that closing
# the SQL seam had left the SHELL seam wide open: the SQL-escaped literal was
# landing inside a double-quoted `sqlite3 "$DB" "…"` argument, where command
# substitution is live. `T`touch PWNED`X` and `A$(touch PWNED)B` both executed.
# These pins run the gate's own vectors and prove no shell fires.

HOSTILE = [
    ("backtick",     "T`touch PWNED`X"),
    ("dollar-paren", "A$(touch PWNED)B"),
    ("double-quote", 'Fed QE Program"x'),
    ("semicolon",    "X; touch PWNED; echo "),
    ("sql-inject",   "x'); DROP TABLE vocabulary_moves;--"),
    ("apostrophe",   "Moody's"),
    ("unicode",      "Société Générale"),
    ("cjk",          "中國台灣"),
]


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI absent")
@pytest.mark.parametrize("label,concept", HOSTILE, ids=[h[0] for h in HOSTILE])
def test_a_hostile_concept_name_never_reaches_a_shell(label, concept, tmp_path):
    """BORN RED on backtick and dollar-paren (gate G-1). Each concept runs in
    its OWN cwd so a `touch PWNED` is unambiguous — the gate's recipe.

    The bar is deliberately higher than "no PWNED file": the script must still
    EXIT 0 and still REVERSE the row. A fix that merely broke on hostile input
    would pass an injection check while destroying the insurance artifact for
    every legitimate name that happens to contain a quote."""
    workdir = tmp_path / f"cwd-{label}"
    workdir.mkdir()
    db = tmp_path / "newslens.db"
    con = _ledger_fixture(db)
    eid = con.execute("SELECT id FROM entities").fetchone()[0]
    con.execute("INSERT INTO vocabulary_moves (concept, from_vocab, to_vocab,"
                " entity_id, blessed_by, note)"
                " VALUES (?, 'tag', 'entity', ?, 'test', '')", (concept, eid))
    con.commit()

    sources = tmp_path / "sources.yaml"
    sources.write_text(YAML, encoding="utf-8")
    original = sources.read_bytes()
    b = vocab_move.backup(sources, tmp_path / "out")
    script = workdir / "reverse-move.sh"
    script.write_text(vocab_move.reverse_script(b, sources, [concept]),
                      encoding="utf-8")

    proc = subprocess.run(["/bin/sh", str(script)], capture_output=True,
                          text=True, cwd=str(workdir),
                          env={**os.environ, "NEWSLENS_DB_PATH": str(db)})

    # 1. NO SHELL FIRED — anywhere.
    pwned = list(workdir.glob("PWNED")) + list(tmp_path.glob("PWNED"))
    assert pwned == [], f"SHELL INJECTION via {label}: {pwned}"
    # 2. The script still worked: exit 0, the row reversed, the YAML restored.
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    rev = con.execute("SELECT concept FROM vocabulary_moves"
                      " WHERE reversal_of IS NOT NULL").fetchall()
    assert [r[0] for r in rev] == [concept]
    assert sources.read_bytes() == original
    # 3. The table survived the SQL payloads.
    assert con.execute("SELECT COUNT(*) FROM vocabulary_moves").fetchone()[0] == 4
    con.close()


def test_a_control_character_in_a_concept_is_refused_not_escaped():
    """BORN RED. A newline could emit a line that closes the quoted heredoc
    early and turn the remaining SQL back into shell — the one way the G-1 fix
    could be walked around. Refused loudly at generation time, because a
    concept is a name and a name has no newline in it."""
    for hostile in ("Fed\nNEWSLENS_REVERSE_SQL\ntouch PWNED", "a\rb", "a\x00b"):
        with pytest.raises(ValueError, match="control character"):
            vocab_move.reverse_script(paths.DATA_DIR / "b.yaml",
                                      paths.DATA_DIR / "sources.yaml", [hostile])


def test_the_sql_rides_a_quoted_heredoc_so_the_shell_expands_nothing():
    """The structural companion: an unquoted heredoc (<<DELIM) or a return to a
    double-quoted argument would re-open G-1 while every execution pin above
    still passed on today's shell-safe TWINS."""
    script = vocab_move.reverse_script(paths.DATA_DIR / "b.yaml",
                                       paths.DATA_DIR / "sources.yaml",
                                       ["Federal Reserve"])
    assert f"<<'{vocab_move.HEREDOC_DELIMITER}'" in script
    assert 'sqlite3 "$DB" "INSERT' not in script     # the pre-G-1 shape


def test_a_concept_appears_only_inside_the_quoted_heredoc(tmp_path):
    """THE GENERALISED PIN, and it exists because reasoning failed twice.

    G-1's fix closed the `sqlite3 "$DB" "…"` argument by moving the SQL into a
    quoted heredoc — and the very next execution run still created PWNED, from
    an `echo "reversed: {concept}"` line nobody had thought about. Both misses
    share a shape: the author asked "is THE seam closed?" when the question is
    "how many shell contexts does this script have?".

    So this asserts the structural property instead of enumerating contexts:
    outside the heredoc body a concept may appear ONLY inside a single-quoted
    shell word, where sh expands nothing. Strip the single-quoted spans from a
    line and the concept must be gone. Any future line that interpolates it into
    a bare or double-quoted context fails here without needing a new vector."""
    # Fixed concept BY NECESSITY (gate micro-confirm): the strip-single-quoted
    # heuristic and the verbatim non-vacuity guard below both assume a concept
    # with no apostrophe — do not parametrize this over HOSTILE.
    concept = "T`touch PWNED`X"
    script = vocab_move.reverse_script(tmp_path / "b.yaml",
                                       tmp_path / "sources.yaml", [concept])
    lines = script.splitlines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith("sqlite3 ") and vocab_move.HEREDOC_DELIMITER in l)
    end = next(i for i, l in enumerate(lines)
               if i > start and l == vocab_move.HEREDOC_DELIMITER)

    def _shell_live_part(line):
        """The line with every single-quoted span removed — what sh would still
        interpret."""
        return re.sub(r"'[^']*'", "", line)

    exposed = [(i + 1, l) for i, l in enumerate(lines)
               if not (start < i < end) and concept in _shell_live_part(l)]
    assert exposed == [], f"concept reaches a live shell context: {exposed}"
    # ...and it really does appear outside the heredoc, so the check above is
    # measuring something rather than passing over an empty set.
    assert any(concept in l for i, l in enumerate(lines)
               if not (start < i < end))
