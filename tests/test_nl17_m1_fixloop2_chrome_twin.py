"""NL-17 M1 FIX LOOP 2 / QA F-5 — the chrome twin renders the class ONCE.

THE DEFECT. `flRenderChrome` composed the thread's name from `disclosure` and
then appended a qualifier derived from that same disclosure, so an entity rename
rendered the class twice — measured live by QA as `OpenAI (company) (company)`
on a Following-row h2. The server twin composes the stored TOPIC plus one
qualifier, so a reload disagreed with the live render and silently repaired it.
A display lie that self-heals is the hardest kind to notice, and the F-6 pins
missed it because they check STRUCTURE (which renderer runs, which attributes
are re-stamped) and never the COMPOSED STRING.

So this file asserts the composition, and asserts it as a TWIN: the client's
output for a case must equal the server's for that same case. Structure pins
cannot catch a twin divergence; only comparing the two outputs can.

TWO TEETH, different blind spots — the same shape the parse gate uses, for the
same reason (this batch has now been bitten twice by a check that could not see
the channel the defect lived in):

  1. BEHAVIOURAL, node-gated: actually execute the shipped `webui.JS` against a
     minimal DOM stub and compare the produced markup to the server's. This is
     the real twin test. Skipped when node is absent.
  2. STRUCTURAL, always-on: the composition reads the stored topic and splits
     the disclosure when it must fall back — never the raw disclosure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from newslens import server, webui

from test_nl143_follow_surface_truth import _fn, _js_code


# The four shapes a Following-row h2 can be renamed into.
CASES = [
    # (topic, altitude, disclosure)
    ("OpenAI", "entity", "OpenAI (company)"),           # the defect's case
    ("Volkswagen", "entity", "Volkswagen (company)"),
    ("US-China AI competition", "storyline", "US-China AI competition"),
    ("Redemption Gates", "storyline", "Redemption Gates (fund-withdrawal story)"),
]

_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
// a DOM stub thin enough to load the script and run one renderer
const el = () => ({ _html: '', attrs: {},
  set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
  setAttribute(k, v) { this.attrs[k] = v; },
  getAttribute(k) { return this.attrs[k] || null; },
  querySelector() { return this._a; } });
global.document = {
  body: { getAttribute: () => '2026-08-08' },
  querySelectorAll: () => [], addEventListener: () => {},
  getElementById: () => null, createElement: () => el(),
};
global.window = global; global.NL_LABELS = {};
global.requestAnimationFrame = () => {};
eval(src);
const out = [];
for (const c of JSON.parse(process.argv[3])) {
  const node = el(); node._a = el();
  flRenderChrome(node, c[0], c[1], c[2]);
  out.push(node._a.innerHTML);
}
console.log(JSON.stringify(out));
"""


def test_the_chrome_twin_renders_the_same_markup_as_the_server():
    """BORN RED against the shipped state — THE REAL TWIN TEST.

    Executes the SHIPPED client script and compares its h2 content, case by
    case, with what `_thread_name_link` puts inside the same anchor. Any
    divergence — a doubled class, a missing one, a different split — fails here
    with both strings quoted."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available — the structural tooth below still binds")
    with tempfile.TemporaryDirectory() as d:
        js = Path(d) / "webui.js"
        js.write_text(webui.JS, encoding="utf-8")
        harness = Path(d) / "h.js"
        harness.write_text(_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [node, str(harness), str(js), json.dumps(CASES)],
            capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    client = json.loads(proc.stdout.strip().splitlines()[-1])

    for (topic, altitude, disclosure), got in zip(CASES, client):
        row = {"altitude": altitude, "disclosure": disclosure}
        link = server._thread_name_link(1, topic,
                                        qualifier=server._altitude_qualifier_html(row))
        want = link.split(">", 2)[2].rsplit("</a>", 1)[0]
        assert got == want, (
            f"twin divergence for {disclosure!r}:\n"
            f"  client: {got!r}\n  server: {want!r}")
        # …and the class never appears twice, which is the defect stated directly
        assert got.count("(company)") <= 1, got


def test_the_composition_uses_the_stored_topic_not_the_raw_disclosure():
    """BORN RED against the shipped state — THE ALWAYS-ON TOOTH.

    The stored topic is already the split head (`_settle_onto` stores
    `split_qualifier(disclosure)`), so it is the name; the qualifier is appended
    once. The fallback splits rather than using the disclosure whole, so even a
    caller with no topic cannot double the class."""
    body = _js_code(_fn("flRenderChrome"))
    assert "topic || flNameOnly(disclosure)" in body
    assert "disclosure || topic" not in body        # the defect, exactly
    assert body.count("flAltQualifier(") == 1
    split = _js_code(_fn("flNameOnly"))
    assert "match(" in split and "[1]" in split
