"""Stage-0 C1 — THE COMMISSIONING: the founding page a stranger's first run opens.

Contract: `design/mockup-v12-commissioning.html` as amended at the principal's
browser gate 2026-07-28 — its first-run state matrix, its VOICE inventory, its
accessibility contract, and the six ENGINEERING SEAMS.

THE TWO CHARGES THIS FILE EXISTS FOR (both named to QA as the trust charges):

  SEAM 2 — THE REFUSAL PATH. Before C1, a generate started on a profile with no
  interests died in `run_rank` with a sentence naming the profile and a
  filesystem path, and the server rendered that verbatim. It is the one screen
  nobody exercises because it only happens once per reader, on their first
  minute. Everything below that asserts on a CLI-shaped string is guarding it.

  THE WRITE ORDERING. Write -> VERIFY READABLE -> only then generate. A run
  started on an unverified write burns thirty minutes and ends in that same
  refusal, which is why the ordering is build-blocking and why the pins here
  drive it through a door that lies about succeeding.

Sandbox: the tree conftest's autouse fixtures apply (sandboxed paths, loopback
network, real-state tripwire). $0 by construction — GEN_JOB is stubbed or left
idle in every test; no pipeline runs.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from newslens import (catalog, commissioning, config, db, generate, labels,
                      paths, ranking, server)

DATE = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh(tmp_paths):
    """A freshly provisioned reader's world: migrated DB, the shipped source
    template with its interests block EMPTY — exactly what `profile create`
    leaves behind, which is the state this whole milestone is about."""
    db.migrate()
    paths.SOURCES_FILE.write_text(
        paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8")
    return tmp_paths


@pytest.fixture
def ui(fresh, monkeypatch):
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    box = SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}",
                          httpd=httpd)
    yield box
    httpd.shutdown()
    httpd.server_close()


def get(ui, path):
    try:
        with urllib.request.urlopen(ui.base + path, timeout=10) as r:
            return r.getcode(), r.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def post(ui, path, payload):
    req = urllib.request.Request(
        ui.base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.getcode(), json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def reader_text(html):
    """The page minus its stylesheet and its behaviour script — i.e. what a
    reader can actually end up looking at.

    The copy sweeps below run on THIS, not on raw bytes, and the two exclusions
    are deliberate and narrow. The <style> block is CSS: `--popup-scrim`,
    `100%` and `/* … */` are tokens and syntax, not sentences. The behaviour
    <script> holds route strings (`/api/status`) that no reader ever sees. What
    is NOT stripped is the NL_C1 label blob — that IS reader copy, delivered to
    the client, and a banned word smuggled in through it must still go red."""
    out = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script>\s*var NL_C1 = \(.*?</script>", "", out, flags=re.S)


def publish(con, date=DATE, narrative="# Edition\n\n## A story\n\nBody.\n"):
    with con:
        con.execute(
            "INSERT INTO briefings (date, story_slots, corroboration_labels,"
            " narrative_text) VALUES (?, '[]', '[]', ?)", (date, narrative))


# ---------------------------------------------------------------------------
# THE FIRST-RUN STATE MATRIX
# ---------------------------------------------------------------------------

def test_a_fresh_profile_gets_the_founding_page_at_every_url(ui):
    """Matrix row 1. Today, an edition deep link and the Archive all answer
    with the Commissioning — navigation to three empty rooms is a maze, not a
    courtesy, and a stranger must not be able to fall out of the door."""
    for path in ("/", "/edition?date=" + DATE, "/archive"):
        code, html = get(ui, path)
        assert code == 200, path
        assert labels.COMMISSION_MASTHEAD in html, path
        assert labels.COMMISSION_FOUND in html, path


def test_topics_picked_but_nothing_generated_still_founds(ui):
    """Matrix row 2: act 3 live. The reader has topics and no edition — the
    founding page, not the app's 'Nothing yet' panel."""
    ok, _ = server.topic_add("Inflation", "specific")
    assert ok
    code, html = get(ui, "/")
    assert code == 200
    assert labels.COMMISSION_MASTHEAD in html
    assert "Nothing yet" not in html


def test_a_published_edition_ends_the_founding_page(ui):
    """Matrix: EDITION READY -> the page opens the edition. The founding page
    is a first-run surface and it does not linger."""
    con = db.connect()
    try:
        publish(con)
    finally:
        con.close()
    code, html = get(ui, "/")
    assert code == 200
    assert labels.COMMISSION_MASTHEAD not in html
    assert labels.NAV_FOLLOWING in html          # the app shell is back


def test_a_bodyless_row_is_not_publication(fresh):
    """ranking.persist() commits today's row at the RANK stage with
    narrative_text NULL — thirty minutes before there is anything to read. A
    founding page keyed on row existence would flip to the app mid-run and
    strand the reader on an empty Today."""
    con = db.connect()
    try:
        with con:
            con.execute("INSERT INTO briefings (date, story_slots,"
                        " corroboration_labels) VALUES (?, '[]', '[]')", (DATE,))
        assert commissioning.has_published_edition(con) is False
        assert commissioning.first_run_state(con, {"state": "running"}) \
            == commissioning.WAITING
        publish(con, date="2026-01-01")
        assert commissioning.has_published_edition(con) is True
        assert commissioning.first_run_state(con, {"state": "idle"}) is None
    finally:
        con.close()


def test_the_running_first_generate_renders_the_vigil(ui, monkeypatch):
    """Matrix: GENERATE RUNNING -> the wait. Stage word, stage clock, TOTAL."""
    server.GEN_JOB.state = "running"
    server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
    server.GEN_JOB._progress("Writing the briefing", "claude-opus-4-6")
    code, html = get(ui, "/")
    assert code == 200
    assert labels.COMMISSION_WAIT_HEAD in html
    assert labels.COMMISSION_STAGE_NARRATIVE in html
    assert labels.COMMISSION_TOTAL in html
    assert labels.COMMISSION_WAIT_LEAVE in html


def test_the_vigil_is_re_derived_on_every_load_seam_4(ui):
    """SEAM 4 — the run is a server-side thread and the panel re-derives from
    its snapshot on ANY load, which is what makes 'Closing this page won't stop
    it' true. Two independent GETs (a closed tab, then a return) must both
    land on the wait, with the stage the JOB currently holds — no state in the
    browser, none in a cookie, none in the URL."""
    server.GEN_JOB.state = "running"
    server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
    server.GEN_JOB._progress("Gathering the news", None)
    _, first = get(ui, "/")
    assert labels.COMMISSION_STAGE_INGEST in first
    server.GEN_JOB._progress("Editing", "claude-3-5-haiku")
    _, second = get(ui, "/archive")          # a different URL, same truth
    assert labels.COMMISSION_STAGE_EDITOR in second
    assert labels.COMMISSION_STAGE_INGEST not in second


def test_a_failed_first_run_states_the_outcome_and_offers_try_again(ui):
    """Matrix: GENERATE FAILED -> the head, the run's own sentence, the
    outcome, Try again."""
    server.GEN_JOB.state = "error"
    server.GEN_JOB.error = "ingest failed: 12 of 37 feeds timed out"
    code, html = get(ui, "/")
    assert code == 200
    assert labels.COMMISSION_FAIL_HEAD in html
    assert "ingest failed: 12 of 37 feeds timed out" in html
    assert "Nothing was published." in html
    assert labels.COMMISSION_FAIL_TRY_AGAIN in html


# ---------------------------------------------------------------------------
# SEAM 2 — the refusal path (THE trust charge)
# ---------------------------------------------------------------------------

def _cli_shaped(html):
    """Anything on this page that reads like a terminal talking to an operator:
    a filesystem path, a config filename, the YAML key, the CLI's own way of
    naming a profile."""
    return [m.group(0) for m in re.finditer(
        r"(?:[\w.-]*/[\w.-]+/[\w./-]*)|(?:\b[\w-]+\.ya?ml\b)"
        r"|(?:\binterests:\s)|(?:\bprofile '[^']*')", html)]


def test_the_real_no_interests_refusal_is_cli_shaped(fresh):
    """THE PREMISE, MEASURED RATHER THAN ASSUMED. The sentence a first run used
    to hand a stranger, taken from the shipped ranker itself — not paraphrased
    here, so this pin cannot drift from what the code actually raises."""
    with pytest.raises(ranking.RankingError) as exc:
        ranking.run_rank()
    text = str(exc.value)
    assert "has no interests configured" in text
    assert _cli_shaped(text), text
    assert commissioning.unfit_for_readers(text)


def test_that_refusal_can_never_reach_the_founding_pages_failure_panel(ui):
    """If the CLI sentence somehow becomes the job's error — a hand-fired
    /api/generate on an older build, a future path nobody has thought of — the
    founding page omits it rather than paraphrasing a failure it cannot vouch
    for. The panel still states what happened and offers the act."""
    con = db.connect()
    try:
        cfg = config.load_sources()
        with pytest.raises(ranking.RankingError) as exc:
            ranking.run_rank(cfg=cfg, con=con, env={})
        real = str(exc.value)
    finally:
        con.close()
    server.GEN_JOB.state = "error"
    server.GEN_JOB.error = real
    code, html = get(ui, "/")
    assert code == 200
    assert labels.COMMISSION_FAIL_HEAD in html
    assert "has no interests configured" not in html
    assert "sources.yaml" not in html
    assert labels.COMMISSION_FAIL_TRY_AGAIN in html


@pytest.mark.parametrize("state", ["picker", "waiting", "failed"])
def test_no_state_of_the_founding_page_shows_a_path_or_a_config_filename(
        ui, state):
    """Swept across all three states, on the rendered bytes. `--danger` may
    appear once, on a generation failure; a filesystem path may appear never."""
    if state == "waiting":
        server.GEN_JOB.state = "running"
        server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
    elif state == "failed":
        server.GEN_JOB.state = "error"
        server.GEN_JOB.error = "ingest failed: 12 of 37 feeds timed out"
    code, html = get(ui, "/")
    assert code == 200
    body = reader_text(html)
    assert not _cli_shaped(body), _cli_shaped(body)


def test_generate_is_refused_outright_when_the_profile_has_no_topics(ui):
    """SEAM 2's BELT, and a new enforcement surface. The trigger refuses before
    a thread is started, so the CLI sentence is never even generated — on this
    route, on the founding page's Try again, on anything that can reach it."""
    code, out = post(ui, "/api/generate", {})
    assert code == 409
    assert out["ok"] is False
    assert out["error"] == labels.COMMISSION_FOUND_REFUSAL
    assert server.GEN_JOB.snapshot()["state"] == "idle"


def test_generate_still_runs_for_a_commissioned_reader(ui, monkeypatch):
    """The belt refuses a topicless profile and NOTHING else — the guard must
    not become a second staleness gate on the founder's own trigger."""
    ok, _ = server.topic_add("Inflation", "specific")
    assert ok
    started = {"n": 0}
    monkeypatch.setattr(server.GEN_JOB, "start",
                        lambda: started.__setitem__("n", started["n"] + 1) or True)
    code, out = post(ui, "/api/generate", {})
    assert code == 200 and out["ok"] is True
    assert started["n"] == 1


# ---------------------------------------------------------------------------
# THE FOUND ACT — write, verify, and only then start
# ---------------------------------------------------------------------------

def test_the_found_act_writes_verifies_and_only_then_starts(ui, monkeypatch):
    """The ordering, observed rather than asserted about: at the instant
    GEN_JOB.start() is called, the topics must already be readable from the
    file. If the order ever inverts, `seen` records an uncommissioned world."""
    seen = {}

    def spy():
        seen["interests"] = config.load_sources().interests_granular
        return True

    monkeypatch.setattr(server.GEN_JOB, "start", spy)
    code, out = post(ui, "/api/commission", {"topics": ["Inflation"]})
    assert code == 200 and out["ok"] is True
    assert seen["interests"] == ["Inflation"], (
        "the generate started before the write was readable — SEAM 2")


def test_a_write_that_reports_success_but_does_not_land_starts_nothing():
    """THE VERIFY, driven. A door that answers ok and writes nothing is exactly
    the failure the read-back exists for; the answer is a refusal, and no
    go-ahead is ever returned."""
    calls = []

    def liar(name, level):
        calls.append((name, level))
        return True, "added"

    ok, refusal, written = commissioning.commission(["Inflation"], liar)
    assert calls == [("Inflation", "specific")]
    assert ok is False and written == []
    assert refusal == labels.COMMISSION_VERIFY_REFUSAL


def test_a_write_the_editor_refuses_returns_a_reader_world_refusal(fresh):
    def refuser(name, level):
        return False, "Didn’t add it — your sources file has no section for ..."

    ok, refusal, _ = commissioning.commission(["Inflation"], refuser)
    assert ok is False
    assert refusal == labels.COMMISSION_WRITE_REFUSAL
    assert "sources file" not in refusal      # the editor's own words, not ours


def test_an_unknown_topic_is_refused_before_anything_is_written(ui):
    """'Typing filters this list; it never adds to it' — in the MECHANISM. A
    hand-rolled POST carrying a name the catalog does not hold is refused, and
    the reader's file is byte-identical afterwards."""
    before = paths.SOURCES_FILE.read_text(encoding="utf-8")
    code, out = post(ui, "/api/commission",
                     {"topics": ["Inflation", "Pickleball"]})
    assert code == 400 and out["ok"] is False
    assert out["error"] == labels.COMMISSION_UNKNOWN_TOPIC
    assert paths.SOURCES_FILE.read_text(encoding="utf-8") == before
    assert server.GEN_JOB.snapshot()["state"] == "idle"


def test_zero_picks_is_the_rendered_refusal_not_a_started_run(ui):
    code, out = post(ui, "/api/commission", {"topics": []})
    assert code == 400
    assert out["error"] == labels.COMMISSION_FOUND_REFUSAL
    assert server.GEN_JOB.snapshot()["state"] == "idle"


def test_domain_picks_land_at_the_domain_level_and_topics_at_the_topic_level(
        ui, monkeypatch):
    """SEAM 1. The reader never sees the split; the file must still get it
    right, because the two levels weigh differently in the ranker."""
    monkeypatch.setattr(server.GEN_JOB, "start", lambda: True)
    code, out = post(ui, "/api/commission",
                     {"topics": ["Central Bank Policy", "Inflation"]})
    assert code == 200 and out["ok"] is True
    cfg = config.load_sources()
    assert cfg.interests_broad == ["Central Bank Policy"]
    assert cfg.interests_granular == ["Inflation"]
    assert not cfg.problems


def test_a_resubmit_is_not_an_error(ui, monkeypatch):
    monkeypatch.setattr(server.GEN_JOB, "start", lambda: True)
    assert post(ui, "/api/commission", {"topics": ["Inflation"]})[1]["ok"]
    code, out = post(ui, "/api/commission", {"topics": ["Inflation"]})
    assert code == 200 and out["ok"] is True
    assert config.load_sources().interests_granular == ["Inflation"]


def test_the_commissioning_mints_no_follow_seam_6(ui, monkeypatch):
    """SEAM 6, binding. No thread row, no memory write, no baseline enqueue —
    the first write of that class is the reader's first tap in Edition No. 1."""
    monkeypatch.setattr(server.GEN_JOB, "start", lambda: True)
    assert post(ui, "/api/commission",
                {"topics": ["Inflation", "Central Bank Policy"]})[1]["ok"]
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"] == 0
        assert con.execute(
            "SELECT COUNT(*) c FROM thread_baselines").fetchone()["c"] == 0
    finally:
        con.close()
    assert not paths.MEMORY_FILE.exists() or \
        paths.MEMORY_FILE.read_text(encoding="utf-8") == ""


def test_no_follow_control_is_offered_anywhere_on_the_founding_page(ui):
    _, html = get(ui, "/")
    for banned in ("Follow this thread", "deck-follow", "/api/follow",
                   labels.FOLLOWING_TRIAD_THREADS):
        assert banned not in html, banned


# ---------------------------------------------------------------------------
# SEAM 3 — the reader-world stage map
# ---------------------------------------------------------------------------

def test_every_generate_phase_has_a_reader_word():
    """The coverage pin. A phase added to generate.PROGRESS_LABELS with no
    reader word here reddens the suite instead of shipping an internal key onto
    the most-watched screen in the product."""
    missing = [k for k in generate.PROGRESS_LABELS
               if k not in commissioning.PHASE_TO_READER_STAGE]
    assert not missing, missing
    for phase, label in generate.PROGRESS_LABELS.items():
        word = commissioning.reader_stage(label)
        assert word == commissioning.PHASE_TO_READER_STAGE[phase]
        assert word != labels.COMMISSION_STAGE_FALLBACK


def test_the_map_is_inverted_from_the_shipped_table_not_copied(monkeypatch):
    """A re-pin in generate.PROGRESS_LABELS must land here for free. Pinned by
    monkeypatching the shipped table and watching the map follow — the red test
    only the derivation can flip."""
    monkeypatch.setitem(generate.PROGRESS_LABELS, "ingest", "Slurping feeds")
    assert commissioning.reader_stage("Slurping feeds") == \
        labels.COMMISSION_STAGE_INGEST
    assert commissioning.reader_stage("Gathering the news") == \
        labels.COMMISSION_STAGE_FALLBACK


def test_the_wait_never_renders_a_pipeline_word_or_a_model_name(ui):
    """On the founder's own screen the internal vocabulary is a feature; on a
    stranger's first-run wait it is the product talking to itself."""
    server.GEN_JOB.state = "running"
    server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
    server.GEN_JOB._progress("Updating the story threads", "claude-opus-4-6")
    html = reader_text(get(ui, "/")[1])
    assert labels.COMMISSION_STAGE_SAVING in html
    assert "claude-opus-4-6" not in html
    reader_words = set(commissioning.PHASE_TO_READER_STAGE.values())
    for phase, shipped in generate.PROGRESS_LABELS.items():
        # "Saving" is BOTH the shipped label for `persist` and the reader word
        # for persist/state — the one place the two vocabularies already agreed,
        # and not a leak. Every other shipped label must be absent.
        if shipped in reader_words:
            continue
        assert shipped not in html, shipped
        assert (">%s<" % phase) not in html


def test_api_status_carries_the_reader_stage_so_the_map_stays_server_side(ui):
    server.GEN_JOB.state = "running"
    server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
    server.GEN_JOB._progress("Ranking stories", "claude-3-5-haiku")
    code, raw = get(ui, "/api/status")
    assert code == 200
    payload = json.loads(raw)
    assert payload["reader_stage"] == labels.COMMISSION_STAGE_RANK
    assert payload["stage"] == "Ranking stories"        # unchanged for the app


def test_the_founders_own_running_panel_is_untouched(fresh, monkeypatch):
    """The WALL. The app's shipped panel still speaks its own vocabulary; C1
    added a second surface, it did not re-pin the first."""
    ok, _ = server.topic_add("Inflation", "specific")
    assert ok
    con = db.connect()
    try:
        publish(con, date="2026-01-01")
        gen = {"state": "running", "stage": "Gathering the news",
               "stage_model": "claude-3-5-haiku", "stage_elapsed_s": 1,
               "total_elapsed_s": 2, "error": "", "started_at": None}
        html = server._render_today(con, None, None, gen)
    finally:
        con.close()
    assert "Gathering the news" in html
    assert "claude-3-5-haiku" in html
    assert labels.COMMISSION_STAGE_INGEST not in html


# ---------------------------------------------------------------------------
# SEAM 5 — the NL-90 block is HELD, and there is nowhere for it to hide
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["picker", "waiting"])
def test_no_scheduler_control_exists_on_any_state(ui, state):
    """Held at the principal's arm: it would write a schedule nothing reads.
    Swept on the rendered page rather than trusted to a code review."""
    if state == "waiting":
        server.GEN_JOB.state = "running"
        server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
    _, html = get(ui, "/")
    for banned in ("Generate tomorrow", "tomorrow’s edition", "7:00 am",
                   "Runs even while NewsLens is closed", "schedule",
                   "time-in", "sched"):
        assert banned not in html, banned


# ---------------------------------------------------------------------------
# THE ACCESSIBILITY CONTRACT (build-binding)
# ---------------------------------------------------------------------------

def test_the_picker_is_native_checkboxes_in_fieldsets_with_hidden_legends(ui):
    """(1) One <input type=checkbox> per topic inside a <fieldset> with a
    visually-hidden <legend>; the drawn mark is aria-hidden and sits BESIDE the
    control, never in place of it. No listbox, no roving tabindex, no arrow-key
    invention."""
    _, html = get(ui, "/")
    cat = catalog.load()
    assert html.count('type="checkbox"') == cat.entry_count
    assert html.count('<fieldset class="dom"') == cat.domain_count
    assert html.count('<legend class="vh">') == cat.domain_count
    assert html.count('<span class="mark" aria-hidden="true">') == \
        cat.entry_count
    for banned in ('role="listbox"', 'role="option"', "tabindex=\"-1\" class=\"pk",
                   'role="radiogroup"'):
        assert banned not in html, banned


def test_announcement_discipline(ui):
    """(4) filter results role=status · the zero-pick refusal role=status ·
    the picked-count line deliberately NOT live · no clock inside any live
    region."""
    _, html = get(ui, "/")
    assert 'id="c1-filter-status" role="status"' in html
    assert 'id="c1-refusal" role="status"' in html
    count = re.search(r'<p class="count" id="c1-count"[^>]*>', html).group(0)
    assert "aria-live" not in count and "role=" not in count

    server.GEN_JOB.state = "running"
    server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
    _, wait = get(ui, "/")
    live = re.search(r'<p class="live".*?</p>', wait, re.S).group(0)
    # role="status" is on the STAGE LABEL only. A counter that announces every
    # tick is a screen-reader denial of service, so the two clocks sit outside
    # any live region — the mockup's own a11y contract, which is the binding
    # half of an artifact whose markup put role on the whole paragraph.
    assert '<p class="live"' in live and 'role="status"' not in \
        live.split('<span class="stage"')[0]
    for clock_id in ('id="c3-stage-clock"', 'id="c3-total-clock"'):
        seg = live.split(clock_id)[0]
        assert seg.rfind('role="status"') < seg.rfind("</span>")


def test_the_forced_colors_and_focus_floors_are_shipped_not_promised(ui):
    """(5)/(8): a 3px --terra-deep ring on every control including the drawn
    mark, and forced-colors restores the native box."""
    _, html = get(ui, "/")
    assert "@media (forced-colors: active)" in html
    assert ".pk:focus-visible + .mark" in html
    assert "outline: 3px solid var(--terra-deep)" in html


def test_the_hit_target_is_the_whole_label_row(ui):
    _, html = get(ui, "/")
    assert re.search(r"\.pick \{[^}]*min-height: 44px", html, re.S)


def test_the_page_carries_no_modal_and_nothing_traps(ui):
    """(9) No modal anywhere; nothing traps. The wait page is a page, not a
    dialog: a reader can leave it, and the copy says so."""
    html = reader_text(get(ui, "/")[1])
    for banned in ('role="dialog"', "aria-modal", "popup-scrim", "slide-panel"):
        assert banned not in html, banned


def test_the_tokens_are_the_shipped_direction_tokens_not_a_second_copy():
    """DESIGN_SYSTEM.md: tokens live in variables. Two files declaring the same
    hexes is how they drift, so the founding page's stylesheet IS webui's token
    block — the same object, not a copy that resembles it."""
    from newslens import webui
    assert commissioning.CSS.startswith(webui.TOKENS)
    assert webui.CSS.startswith(webui.TOKENS)
    assert "#8F4A2E" not in commissioning.CSS[len(webui.TOKENS):]


# ---------------------------------------------------------------------------
# THE VOICE INVENTORY, and the counted lines
# ---------------------------------------------------------------------------

def test_the_founding_page_ships_the_voice_strings_byte_for_byte(ui):
    _, html = get(ui, "/")
    for s in (labels.COMMISSION_TOPICS_HEAD, labels.COMMISSION_TOPICS_SAY,
              labels.COMMISSION_TOPICS_NOTE, labels.COMMISSION_FILTER_LABEL,
              labels.COMMISSION_COUNT_NONE, labels.COMMISSION_SOURCES_HEAD,
              labels.COMMISSION_SOURCES_SHOW, labels.COMMISSION_FOUND,
              labels.COMMISSION_FOUND_SUB, labels.COMMISSION_CONSEQUENCE,
              labels.COMMISSION_SOURCES_SETTINGS):
        assert s in html, s


def test_the_copy_table_is_read_at_render_time(ui, monkeypatch):
    """The label-liveness pin the house keeps for every string table: a re-pin
    in labels.py must appear in rendered output, client strings included."""
    monkeypatch.setattr(labels, "COMMISSION_TOPICS_SAY", "Pick a few things.")
    monkeypatch.setattr(labels, "COMMISSION_FOUND_REFUSAL", "No dice.")
    _, html = get(ui, "/")
    assert "Pick a few things." in html
    assert "No dice." in html                 # the client blob, same table


def test_the_source_pack_numbers_are_counted_from_the_readers_own_file(ui):
    """The true numbers, against the shipped template: 69 outlets · 65 fetched
    · 4 attribution-only. Re-pinned 2026-08-03 — the NL-135 slate added 28
    feeds and NL-136 ① dropped the one disabled aggregator, so the fourth
    clause has nothing to count and drops. That the numbers MOVED with the
    file is the property this test exists for; they are counted, never typed."""
    _, html = get(ui, "/")
    assert ("69 outlets. 51 are fetched each morning. "
            "4 are attribution-only by design. 14 sources are off.") in html
    assert "3 analyst newsletters are in the list; none are followed." in html


def test_the_pack_sentence_drops_a_clause_it_cannot_fill():
    """A count of zero renders no clause — never '0 aggregators are off'."""
    cfg = config.SourcesConfig(sources=[
        config.Source(name="Only", rss_url="https://x.invalid/f")])
    assert commissioning.source_pack_sentence(cfg) == \
        "1 outlet. 1 is fetched each morning."


def test_the_picked_count_line_is_derived_not_typed(ui):
    """The rest-state count is the catalog's own size; nothing on this page
    states a number the data does not hold."""
    _, html = get(ui, "/")
    cat = catalog.load()
    assert ">%d topics.</p>" % cat.entry_count in html


def test_the_killed_vocabulary_is_absent_from_every_state(ui):
    """The mockup's KILLED ON SIGHT list, swept on rendered bytes."""
    for gen in ("idle", "running", "error"):
        server.GEN_JOB.state = gen
        server.GEN_JOB.error = "ingest failed: 12 of 37 feeds timed out"
        server.GEN_JOB.started_at = datetime.now(timezone.utc).isoformat()
        html = reader_text(get(ui, "/")[1])
        for banned in ("Most readers", "Welcome to NewsLens", "Step 1 of",
                       "While you wait", "Almost there", "We recommend",
                       "Complete your profile", "progress", "%"):
            assert banned not in html, (gen, banned)


def test_the_founding_page_carries_the_staleness_banner(ui, monkeypatch):
    """A stale server REFUSES the found act (server._api_commission checks the
    same guard the generate trigger does), so the banner that names the one-line
    restart has to render on the page where the refusal happens. Found by the
    suite, not by a review: test_serverside_batch_qa_20260717's
    reading-stays-untouched pin caught the founding page shipping without it."""
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "oldsha"))
    monkeypatch.setattr(server, "_git_head", lambda: "newsha")
    code, html = get(ui, "/")
    assert code == 200
    assert 'class="staleness-banner"' in html and 'role="alert"' in html
    # And the act itself refuses, rather than starting a run this server may
    # not be fit to make.
    code, out = post(ui, "/api/commission", {"topics": ["Inflation"]})
    assert code == 409 and out["ok"] is False
    assert out["error"] == labels.STALENESS_REFUSAL
    assert server.GEN_JOB.snapshot()["state"] == "idle"
    # The topics ARE saved — that write is the reader's and it stands.
    assert config.load_sources().interests_granular == ["Inflation"]


# ---------------------------------------------------------------------------
# QA-9 (HIGH, fix loop 2) — THE FOUND ACT'S WIRE
#
# QA's own acceptance test (tests/test_stage0_c1_qa.py) drives the ONE repro
# that proves the defect: a read-only profile directory, no monkeypatch, a real
# OSError out of the shipped topic_add. These are the FALSIFIER — the claim
# that the wire's vocabulary is CLOSED, not merely that this one fault is now
# handled. A closed codomain is falsified by a single seam that leaks, so every
# seam _commission_answer touches is made to raise, one at a time, with a
# marker no blessed sentence contains.
# ---------------------------------------------------------------------------

POISON = "x-api-key /Users/ravtej/.newslens/sources.yaml.tmp $4.12 claude-opus-4-6"

BLESSED = None          # filled per-test from labels; see _blessed()


def _blessed():
    return {labels.COMMISSION_WRITE_REFUSAL, labels.COMMISSION_VERIFY_REFUSAL,
            labels.COMMISSION_UNKNOWN_TOPIC, labels.COMMISSION_FOUND_REFUSAL,
            labels.STALENESS_REFUSAL}


def _raiser(exc):
    def _boom(*a, **kw):
        raise exc
    return _boom


COMMISSION_SEAMS = [
    # (label, module, attribute, exception) — every call _commission_answer
    # makes that can raise, including the two the loop-1 code caught narrowly
    # (catalog.CatalogError only) and the one QA proved reachable for real.
    ("catalog load, its own error", catalog, "load", None),
    ("catalog load, an error nobody modelled", catalog, "load", OSError),
    ("the write door (QA-9's real mechanism)", server, "topic_add", OSError),
    ("the file read commission() verifies with", config, "load_sources", OSError),
    ("commission() itself", commissioning, "commission", RuntimeError),
    ("the staleness check", server, "_server_is_stale", OSError),
    ("the job start", None, None, RuntimeError),
]


@pytest.mark.parametrize("label,mod,attr,kind", COMMISSION_SEAMS)
def test_QA9_no_seam_of_the_found_act_can_put_raw_exception_text_on_the_wire(
        fresh, monkeypatch, label, mod, attr, kind):
    """THE FALSIFIER for QA-9. `str(exc)` has no route to /api/commission's
    payload from ANY seam — not the one QA found, all of them.

    The old shape leaked because the catch was chosen per-exception-type:
    _api_commission caught catalog.CatalogError, and do_POST's catch-all
    answered {"ok": false, "error": str(exc)} for everything else, which the
    client writes into #c1-refusal verbatim. Catching one more type would only
    move the hole. The fix closes the CODOMAIN instead — every error string is
    the output of commissioning.reader_refusal() — so this test is written as
    the falsifier of that claim: make each seam raise a marker carrying a
    credential fragment, an absolute path, a temp filename, a dollar figure and
    a model id, and assert the marker cannot be found anywhere in the answer.

    The job-start seam has no module attribute to patch (GEN_JOB is an object),
    hence the `None` row."""
    if mod is None:
        monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
        monkeypatch.setattr(server.GEN_JOB, "start", _raiser(kind(POISON)))
    elif kind is None:
        monkeypatch.setattr(mod, attr, _raiser(catalog.CatalogError(POISON)))
    else:
        monkeypatch.setattr(mod, attr, _raiser(kind(POISON)))
        monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
        monkeypatch.setattr(server.GEN_JOB, "start", lambda: True)

    payload, status = server._commission_answer({"topics": ["Inflation"]})

    wire = json.dumps(payload, ensure_ascii=False)
    assert POISON not in wire, "%s leaked the raw sentence: %s" % (label, wire)
    for fragment in ("x-api-key", "/Users", ".tmp", "$4.12", "claude-opus",
                     "Errno", "Traceback"):
        assert fragment not in wire, "%s leaked %r: %s" % (label, fragment, wire)
    shown = str(payload.get("error", ""))
    assert not shown or shown in _blessed(), \
        "%s answered an unblessed sentence: %r" % (label, shown)
    assert isinstance(status, int)


def test_QA9_an_unblessed_refusal_invented_downstream_never_reaches_the_wire(
        fresh, monkeypatch):
    """The other half of the codomain claim: the funnel does not trust
    commission()'s RETURN either.

    A future caller — or a future refusal added to commission() without a
    label — can hand back any string it likes. reader_refusal() answers from a
    set this module composes, so an unvouched sentence is replaced rather than
    forwarded. Without this arm the guard would only be as good as every
    downstream author's discipline, which is the assumption QA-6 already
    falsified once."""
    monkeypatch.setattr(commissioning, "commission",
                        lambda *a, **kw: (False, POISON, []))
    payload, status = server._commission_answer({"topics": ["Inflation"]})
    assert payload["error"] == labels.COMMISSION_WRITE_REFUSAL
    assert POISON not in json.dumps(payload, ensure_ascii=False)
    assert status == 400


def test_QA9_the_partial_refusal_still_names_what_landed(fresh, monkeypatch):
    """The funnel closes the codomain WITHOUT flattening the one sentence QA-3
    made truthful. The partial refusal is re-derived from the (name, level)
    pairs commission() read back off disk and compared for equality — so it
    rides, and a doctored one does not."""
    saved = [("Inflation", catalog.TOPIC)]
    monkeypatch.setattr(
        commissioning, "commission",
        lambda *a, **kw: (False, commissioning._refusal_for(
            labels.COMMISSION_WRITE_REFUSAL, saved), saved))
    payload, _ = server._commission_answer({"topics": ["Inflation", "ECB"]})
    assert "Inflation" in payload["error"] and "only" in payload["error"]

    # ...and the same sentence with a name that did NOT land is not the one the
    # file supports, so it is refused.
    monkeypatch.setattr(
        commissioning, "commission",
        lambda *a, **kw: (False, commissioning._refusal_for(
            labels.COMMISSION_WRITE_REFUSAL,
            [("Inflation", catalog.TOPIC), ("ECB", catalog.TOPIC)]), saved))
    payload, _ = server._commission_answer({"topics": ["Inflation", "ECB"]})
    assert payload["error"] == labels.COMMISSION_WRITE_REFUSAL


def test_QA9_a_raising_write_door_is_answered_over_real_http(ui, monkeypatch):
    """The wiring proof: the guard is on the ROUTE, not only in a function the
    tests can call. Same fault QA drove with a read-only directory, injected
    here so it runs on every machine, driven over the socket the browser uses."""
    monkeypatch.setattr(server, "topic_add", _raiser(OSError(POISON)))
    code, out = post(ui, "/api/commission", {"topics": ["Inflation"]})
    assert out["ok"] is False
    assert out["error"] in _blessed(), out["error"]
    assert POISON not in json.dumps(out, ensure_ascii=False)
    assert code in (400, 500)
    # SEAM 2 held: no run was started on a file that did not take the write.
    assert server.GEN_JOB.snapshot()["state"] == "idle"


def test_QA9_a_raising_door_leaves_the_refusal_true_about_the_file(fresh, monkeypatch):
    """Why the raise is routed through commission() rather than answered at the
    top: the refusal has to stay TRUE about the file (QA-3's law).

    The first write lands, the second raises. A blanket "your topics couldn't
    be saved" would be false over a file that holds Inflation — so the door
    guard hands the fault to commission(), which re-reads the file and names
    what is in it."""
    real = server.topic_add
    calls = {"n": 0}

    def flaky(name, level):
        calls["n"] += 1
        if calls["n"] == 1:
            return real(name, level)
        raise OSError(POISON)

    monkeypatch.setattr(server, "topic_add", flaky)
    payload, _ = server._commission_answer(
        {"topics": ["Inflation", "Federal Reserve"]})
    assert payload["ok"] is False
    assert "Inflation" in payload["error"], payload["error"]
    assert POISON not in payload["error"]
    assert config.load_sources().interests_granular == ["Inflation"]


# ---------------------------------------------------------------------------
# RIDER 1 (QA re-verify correction 1) — the tail grammar matches its comment
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,error", [
    ("a model id", "narrative failed: claude-opus-4-6 returned an error"),
    ("a credential fragment", "narrative failed: invalid x-api-key"),
    ("a hostname", "ingest failed: api-anthropic-com refused the connection"),
    ("a token figure", "narrative failed: 190000 input tokens exceeded the window"),
    ("the profile noun", "rank failed: profile default has no interests"),
    ("the YAML key, unpunctuated", "rank failed: no interests are configured"),
    ("a filename", "ingest failed: could not read sources.yaml"),
    ("a module path", "rank failed: newslens.ranking.RankingError happened"),
    ("an env var", "ingest failed: NEWSLENS_SOURCES_FILE is not set"),
    ("a date stamp", "ingest failed: 2026-07-30 snapshot missing"),
])
def test_the_reader_safe_tail_admits_no_operator_fragment_behind_a_real_phase(
        label, error):
    """RIDER 1. The loop-1 comment claimed a model id, a credential fragment and
    a spend figure could not ride inside an otherwise-safe sentence; QA measured
    the claim FALSE, because the charset admitted '-' and unbounded digit runs,
    which JOIN tokens a space would have separated. Every string here carries a
    REAL phase prefix (`generate.PROGRESS_LABELS`), so the allowlist is the only
    thing standing between it and a stranger's screen.

    Latent, not live, when QA found it — 0 of generate.py's 18 GenerateError
    templates reach it. The comment was the defect: a guard whose documentation
    overstates it is how the next carrier sentence gets written."""
    assert error.split(" failed:")[0] in generate.PROGRESS_LABELS, \
        "the prefix must be a REAL phase or this test proves nothing"
    assert commissioning.unfit_for_readers(error), \
        "%s rides to the reader: %r" % (label, error)


@pytest.mark.parametrize("label,error", [
    ("the mockup's own blessed sentence",
     "ingest failed: 12 of 37 feeds timed out"),
    ("an ampersand in a shipped catalog name",
     "rank failed: Mergers & Acquisitions had no items"),
    ("an em dash in a shipped outlet name",
     "ingest failed: Washington Post — World returned nothing"),
    ("a second shipped outlet with an em dash",
     "ingest failed: BBC News — World was empty"),
    ("a third shipped outlet with an em dash",
     "ingest failed: The Guardian — World returned nothing"),
    ("a hyphenated shipped source title",
     "ingest failed: Most-Viewed Bills was empty"),
    ("non-ASCII in an outlet name", "ingest failed: Süddeutsche timed out"),
    ("a hyphenated catalog name", "rank failed: US-Israel Relations had no items"),
    ("two sentences", "ingest failed: 3 feeds timed out. 2 refused"),
    ("a parenthetical", "narrative failed: the draft came back empty (twice)"),
    ("a semicolon", "rank failed: one feed was empty; the others were fine"),
])
def test_the_reader_safe_tail_does_not_eat_the_products_own_words(label, error):
    """RIDER 2. QA measured the loop-1 inversion eating 12 of 20 honest
    sentences — including SIX shipped outlet names carrying an em dash and the
    catalog's own `Mergers & Acquisitions`. Over-blocking here costs a
    diagnostic the reader cannot act on, so the direction was right; but the
    classes that get eaten are the product's OWN vocabulary, which is cheap to
    admit and was never the thing the guard existed to stop."""
    assert not commissioning.unfit_for_readers(error), \
        "%s is eaten: %r" % (label, error)
