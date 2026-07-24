"""NL-70 Option B — the rank-id short key (Crockford base32 render alias +
check symbol).

Contract (workspace/briefs/2026-07-21--newslens--rank-shortkey.md, ruled
2026-07-24): the ranking pass renders each candidate's `[id=KEY]` as the
canonical DB id encoded in Crockford base32 with a trailing mod-37 check symbol;
decode_keys() turns the model's string keys back into ints BEFORE
validate_payload, whose int contract and closed-vocab (invented-id) guard stay
untouched. A decode/check failure is a LOUD reject that rides the existing
corrected-retry path.

BORN-RED METHOD (ENGINEERING.md): the behavioural/wiring tests below fail at the
unpatched parent — the wiring tests by BEHAVIOUR (validate_payload rejects string
item_ids, so the call raises) and are collectable at HEAD; the codec/twin tests
by ABSENCE of the new codec. The report states which is which with a HEAD-run.
"""

import json

import pytest

from newslens import ranking


# --- local helpers (self-contained; mirror the ranking test fixtures) ----------

TAGS = {"AI regulation": "topic", "economy": "domain"}
MEMORY = ["chip export controls"]


def cluster(ids, title="A story", summary="What happened.", impact=5):
    return {
        "story_title": title,
        "summary": summary,
        "item_ids": ids,
        "matched_tags": [],
        "matched_memory": [],
        "world_impact": impact,
        "world_impact_reason": "Because it matters.",
    }


def _resp(payload):
    return {
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


# ===========================================================================
# A. Codec unit tests  (red at HEAD by ABSENCE of the new codec)
# ===========================================================================

def test_encode_parity_matches_build_contract():
    """The three reference values pinned in the build contract §1."""
    assert ranking.encode_rank_key(7714) == "7H2J"
    assert ranking.encode_rank_key(99999) == "31MZS"
    assert ranking.encode_rank_key(1000000) == "YGJ01"


@pytest.mark.parametrize("n", [1, 12, 31, 32, 546, 3679, 4228, 7713, 7714, 7715,
                               8355, 8356, 8904, 99999, 1000000])
def test_encode_decode_roundtrip(n):
    assert ranking.decode_rank_key(ranking.encode_rank_key(n)) == n


def test_neighbour_slip_is_a_loud_reject():
    """The run-30 class the check symbol EXISTS for: a single-symbol slip that
    lands on a REAL neighbouring id. In decimal (8355 -> 8356) both are in-vocab
    and the guard is blind; encoded, the copied check symbol no longer matches
    the slipped body, so decode_rank_key rejects LOUDLY before the vocab lookup.
    Adjacency is real (build contract §1): the two keys differ only in the last
    body symbol AND the check symbol."""
    assert ranking.encode_rank_key(8355) == "853Y"
    assert ranking.encode_rank_key(8356) == "854Z"
    # Slip 853Y -> 854Y: body now decodes to 8356 but the check still says 8355.
    with pytest.raises(ValueError) as exc:
        ranking.decode_rank_key("854Y")
    assert "check symbol" in str(exc.value)
    # The correctly-copied neighbour key decodes cleanly to the OTHER int — the
    # check symbol produces no false-loud on a legitimately different key.
    assert ranking.decode_rank_key("854Z") == 8356


def test_bad_check_symbol_rejected():
    # "7H2J" is the valid key for 7714; "7H2K" carries the WRONG check symbol.
    with pytest.raises(ValueError) as exc:
        ranking.decode_rank_key("7H2K")
    assert "check symbol" in str(exc.value)


def test_bad_body_symbol_rejected():
    # 'U' is a check-only symbol, never valid in the body.
    with pytest.raises(ValueError) as exc:
        ranking.decode_rank_key("7U2J")
    assert "invalid symbol" in str(exc.value)


def test_too_short_key_rejected():
    with pytest.raises(ValueError) as exc:
        ranking.decode_rank_key("7")
    assert "too short" in str(exc.value)


def test_case_insensitive_and_confusable_fold():
    # Lowercase decodes identically...
    assert ranking.decode_rank_key("7h2j") == 7714
    # ...and the Crockford confusables fold on decode (O->0, I/L->1). id 1024
    # encodes to "100S" (body carries both a '0' and a '1'); feed the confusable
    # glyphs and the fold must recover the same int.
    assert ranking.encode_rank_key(1024) == "100S"
    assert ranking.decode_rank_key("IOOS") == 1024      # I->1, O->0, O->0


# ===========================================================================
# B. Seam + wiring tests
# ===========================================================================

def test_string_keys_are_decoded_before_validate(monkeypatch):
    """WIRING PROOF (born-red by BEHAVIOUR, collectable at HEAD): the model now
    emits [id=KEY] codes as JSON strings; decode_keys must turn them into
    canonical ints BEFORE validate_payload's int contract runs. LITERAL keys are
    used (no reference to the new codec) so this fails at the unpatched parent
    because validate_payload rejects the strings and the call raises."""
    known = {7714, 7715}
    resp = _resp({"clusters": [cluster(["7H2J", "7H3K"])]})   # == ids 7714, 7715
    monkeypatch.setattr(ranking, "_post_chat", lambda key, prompt: resp)
    clusters, _usage = ranking.call_llm_validated("sk-x", "BASE", known, TAGS, MEMORY)
    assert [c["item_ids"] for c in clusters] == [[7714, 7715]]


def test_bad_key_rides_the_corrected_retry(monkeypatch):
    """A decode/check failure is the malformed-output class: attempt 1 (a
    bad-checksum key) is rejected and rides the EXISTING corrected retry, which
    recovers on attempt 2. Born-red by behaviour at HEAD (both string attempts
    are rejected there -> RankingError)."""
    known = {7714}
    sent = []
    responses = [
        _resp({"clusters": [cluster(["7H2K"])]}),   # wrong check symbol -> decode-fail
        _resp({"clusters": [cluster(["7H2J"])]}),   # valid -> recovers (id 7714)
    ]
    monkeypatch.setattr(ranking, "_post_chat",
                        lambda key, prompt: (sent.append(prompt), responses.pop(0))[1])
    clusters, _usage = ranking.call_llm_validated("sk-x", "BASE", known, TAGS, MEMORY)
    assert [c["item_ids"] for c in clusters] == [[7714]]      # recovered on retry
    assert len(sent) == 2
    assert sent[1] != sent[0] and "CORRECTION" in sent[1]     # corrected, not identical


def test_decode_keys_rejects_bare_int_ids():
    """QA F1 (fix loop 1): a bare JSON-number int in item_ids is a decode FAILURE,
    NOT a pass-through. A JSON-number int never reaches the check symbol, so an
    in-window fabrication like 8500 would validate SILENTLY — the run-30 in-vocab
    class the check symbol exists to catch. decode_keys rejects it loudly instead.
    Rejecting bare ints also closes the JSON-boolean hole (bool is an int subclass
    validate_payload's isinstance-int check would otherwise admit)."""
    # in-window bare int -> loud reject (this WAS the silent channel, now closed)
    with pytest.raises(ValueError) as exc:
        ranking.decode_keys({"clusters": [cluster([8500])]})
    assert "unresolvable item_id key" in str(exc.value)
    # a JSON boolean (int subclass) is rejected too
    with pytest.raises(ValueError):
        ranking.decode_keys({"clusters": [cluster([True])]})
    # positive control: the well-formed KEY for 8500 decodes cleanly to the int
    ok = ranking.decode_keys({"clusters": [cluster([ranking.encode_rank_key(8500)])]})
    assert ok["clusters"][0]["item_ids"] == [8500]


def test_f1_bare_int_through_full_path_is_not_silently_cited(monkeypatch):
    """QA F1 repro, closed end-to-end: a bare JSON-number int 8500 against the
    window {8355..8904}, emitted through call_llm_validated (transport stubbed),
    must NOT be silently cited [[8500]]. It rides the corrected retry; a second
    bare-int draw exhausts the retry and raises RankingError — loud, never silent.
    (Red against the idempotent-on-int decode_keys that F1 flagged.)"""
    known = set(range(8355, 8905))
    resp = _resp({"clusters": [cluster([8500])]})        # bare int, NOT a key
    monkeypatch.setattr(ranking, "_post_chat", lambda key, prompt: resp)
    with pytest.raises(ranking.RankingError) as exc:
        ranking.call_llm_validated("sk-x", "BASE", known, TAGS, MEMORY)
    assert "malformed LLM output" in str(exc.value)


# ===========================================================================
# C. The run-28 detection property — encoded-space TWIN
# ===========================================================================
# Mandate (build contract §5 / eng round Q-run28): keep the int-level fabricated
# lattice test (test_ranking_validation.py) AND add the encoded twin proving a
# base32'd fabricated lattice is rejected via decode-fail-or-invented-id. The
# sparse out-of-vocab window MUST keep its detection power under the render alias.

_RUN28_WINDOW_IDS = set(range(3679, 4229))          # the real 550-id window (ints)
_RUN28_LATTICE = [                                  # 12 clusters x 12 ids, step ~20
    list(range(base, base + 12 * 20, 20)) for base in (395, 386, 387, 383, 388, 389,
                                                       390, 391, 392, 393, 394, 396)
]


def test_run28_lattice_is_disjoint_from_window():
    """Precondition the twin rides on: the fabricated lattice ids are OUTSIDE the
    real window (same property the int-level test pins), so they must reject."""
    fabricated = {i for ids in _RUN28_LATTICE for i in ids}
    assert fabricated.isdisjoint(_RUN28_WINDOW_IDS)


def test_run28_encoded_fabrication_is_caught_by_decode_or_vocab():
    """The encoded twin: the model is shown base32 keys and emits a fabricated
    lattice as WELL-FORMED encoded strings (valid checksums, but for ids the real
    window never contained). decode_keys succeeds, then validate_payload rejects
    every cluster as invented (the invented-id OOV reject in validate_payload).
    Detection power preserved end to end."""
    payload = {"clusters": [
        cluster([ranking.encode_rank_key(i) for i in ids], title=f"c{n}")
        for n, ids in enumerate(_RUN28_LATTICE)
    ]}
    decoded = ranking.decode_keys(payload)           # valid checksums -> decodes clean
    with pytest.raises(ValueError) as exc:
        ranking.validate_payload(decoded, _RUN28_WINDOW_IDS, TAGS, MEMORY)
    assert str(exc.value).count("invented item_ids") == 12


def test_run28_corrupted_encoded_fabrication_is_caught_at_decode():
    """The other flavour: a fabrication emitted as MALFORMED encoded keys (a
    mis-copied symbol) fails decode_keys outright -> ValueError -> corrected
    retry. Corrupt each key's check symbol so none survives to the vocab lookup."""
    def _corrupt(k):                                 # flip the trailing check symbol
        last = k[-1]
        return k[:-1] + ("K" if last != "K" else "M")
    payload = {"clusters": [
        cluster([_corrupt(ranking.encode_rank_key(i)) for i in ids], title=f"c{n}")
        for n, ids in enumerate(_RUN28_LATTICE)
    ]}
    with pytest.raises(ValueError) as exc:
        ranking.decode_keys(payload)
    assert "unresolvable item_id key" in str(exc.value)
