"""NL-142 gate pin — the corroboration trap, enforced rather than documented.

AUTHORED BY THE NL-142 SHIP GATE (FIX-B, research/2026-08-13--nl142-gate.md
§3), landed unedited by fix loop 2 and re-verified there under its own plant.

QA's F-1 fix contract and two shipped comment blocks warn: do NOT clamp
`s["outlet"]` INTO the map dict in `build_source_map` — `compute_provenance`
builds its corroboration set from the raw dict values (`sources[c]["outlet"]`,
S/C cites), so a dict-level clamp can merge two distinct outlets and DEFLATE a
reader-facing provenance tier. At the NL-142 ship gate this was proven REAL and
UNPINNED: planting exactly that wrong fix (S+C outlets clamped via
`clamp_map_labels` into the dict, `_material_header`'s clamp reverted) left the
full 49-test NL-142 + NL-133 + NL-139 batch GREEN while
`compute_provenance(["C1", "C2"])` deflated from
'cluster-corroborated (2 outlets)' to 'cluster-single' — every neighbouring
wrong door (title-at-the-wrong-door, identity truncation) reds 6-9 pins and
this one redded zero. This pin is the red that plant now flips.

FIX LOOP 2 WIDENS WHAT IT GUARDS. The F-G0 close puts R-key labels inside the
same budget, and an R key's dict outlet is its URL host — which
`compute_provenance` prints into the reader-facing "retrieved-single (%s)"
tier string. So the dict door is now forbidden for one more reason: clamping
there would shorten a host the READER sees, not only map furniture. Both
render doors clamp; the dict never.

MUTATION-PROVEN (ENGINEERING.md pin-route law): observed RED under the gate's
dict-clamp plant and again under fix loop 2's own re-take of that plant, GREEN
on the landed tree — receipts in research/2026-08-13--nl142-gate.md §3 and
research/2026-08-13--nl142-fixloop2.md.
"""
from newslens import analysis


def test_the_map_dict_outlet_stays_raw_so_corroboration_cannot_deflate():
    prefix = "The Consolidated Metropolitan Reporting Consortium of Greater "
    a = prefix + "Alpharetta Journal-Constitution Bureau"
    b = prefix + "Betaville Journal-Constitution Bureau"
    # SAME-length hosts on purpose: the water-fill then hands both outlets the
    # same share, so a dict-level clamp truncates both to the shared prefix
    # and they collide into one "outlet". This pin fails on exactly that.
    items = [
        {"url": "https://alpha.example/1", "outlet": a, "title": "t1",
         "raw_excerpt": "body one", "fetched_at": "2026-08-02T00:00Z",
         "published_at": "2026-08-02"},
        {"url": "https://betaz.example/2", "outlet": b, "title": "t2",
         "raw_excerpt": "body two", "fetched_at": "2026-08-02T00:00Z",
         "published_at": "2026-08-02"},
    ]
    sources = analysis.build_source_map([], items, [], [])
    # The dict keeps the RAW outlet — the render doors clamp, the dict never.
    assert sources["C1"]["outlet"] == a, (
        "the map dict no longer holds the raw outlet — a clamp moved to "
        "build_source_map, the door the F-1 fix contract forbids")
    assert sources["C2"]["outlet"] == b
    # ...and the tier the model modulates confidence against counts TWO.
    assert analysis.compute_provenance(["C1", "C2"], sources) == (
        "cluster-corroborated (2 outlets)"), (
        "two distinct outlets counted as one — the corroboration set read a "
        "clamped string; the label clamps belong at the render doors only")


def test_an_r_keys_dict_outlet_stays_whole_for_the_provenance_the_reader_sees():
    """Fix loop 2's half of the same line. `compute_provenance` prints an R
    key's dict outlet verbatim into 'retrieved-single (%s)' — a string the
    READER sees on the brief. The F-G0 close clamps R labels at the two render
    doors; if it ever moves into `build_source_map`, this is the red."""
    host = "an-extremely-long-syndicated-newsroom-subdomain.example.com"
    assert 2 * len(host) > analysis.MAP_LABEL_BUDGET_CHARS, (
        "this host no longer exceeds the label budget — the pin would pass "
        "without exercising the clamp")
    sources = analysis.build_source_map(
        [], [], [{"url": "https://%s/story" % host, "title": "t",
                  "snippet": "s"}], [])
    assert sources["R1"]["outlet"] == host, (
        "an R key's dict outlet was clamped — the reader-facing provenance "
        "string would quote a truncated host")
    assert analysis.compute_provenance(["R1"], sources) == (
        "retrieved-single (%s)" % host)
