"""M6 audio seam (ADR-0008): the one vendor-agnostic boundary.

NO real synthesis anywhere: kokoro runs against a shim "venv python" written
into the sandboxed DATA_DIR (subprocess plumbing without the engine), openai
runs against the loopback fake server via audio.OPENAI_TTS_URL. The doctor's
skip-marker pattern (NEWSLENS_DOCTOR_TTS_SYNTH=0) is QA-ACCEPTED under two
pinned conditions: the skip always renders its INFO marker, and it never
masks engine absence.
"""

from __future__ import annotations

import io
import json
import os
import wave
from pathlib import Path

import pytest

from newslens import audio, config, paths


def make_wav_bytes(n_frames=800, framerate=8000, placeholder_header=False):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x01\x02" * n_frames)
    blob = buf.getvalue()
    if placeholder_header:
        # Mimic the API's streaming headers: nframes field lies (0xFFFFFFFF).
        # (data chunk size lives at offset 40 for a canonical 44-byte header)
        blob = blob[:4] + b"\xff\xff\xff\xff" + blob[8:40] + b"\xff\xff\xff\xff" + blob[44:]
    return blob


# --- chunker edges -------------------------------------------------------------------

def test_chunker_packs_paragraphs_under_the_cap():
    text = "Para one.\n\nPara two.\n\nPara three."
    chunks = audio._chunk_text(text, cap=100)
    assert chunks == ["Para one.\n\nPara two.\n\nPara three."]  # all fit in one


def test_chunker_splits_at_paragraph_boundaries():
    paras = [f"Paragraph {i} " + "word " * 20 for i in range(4)]
    text = "\n\n".join(paras)
    chunks = audio._chunk_text(text, cap=150)
    assert len(chunks) >= 2
    assert all(len(c) <= 150 for c in chunks)
    # Nothing lost: content survives modulo the chunk-boundary whitespace.
    assert "".join(c.replace("\n", " ").replace(" ", "") for c in chunks) == (
        text.replace("\n", " ").replace(" ", "")
    )


def test_chunker_oversized_paragraph_falls_back_to_sentences():
    para = " ".join(f"Sentence number {i} ends here." for i in range(30))
    chunks = audio._chunk_text(para, cap=120)
    assert all(len(c) <= 120 for c in chunks)
    assert len(chunks) > 1
    assert chunks[0].startswith("Sentence number 0")


def test_chunker_hard_slices_boundaryless_runs():
    blob = "x" * 950  # no spaces, no sentence boundaries
    chunks = audio._chunk_text(blob, cap=200)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks) == blob  # sliced, never dropped


def test_chunker_empty_input_is_empty():
    assert audio._chunk_text("", cap=100) == []
    assert audio._chunk_text("\n\n\n", cap=100) == []


# --- openai engine: chunked synthesis + lossless concat (offline) ----------------------

def test_openai_engine_concatenates_chunks_losslessly(
    tmp_path, fake_api, monkeypatch
):
    blob = make_wav_bytes(n_frames=800, placeholder_header=True)
    fake_api.add_route("/v1/audio/speech", status=200, body=blob,
                       content_type="audio/wav")
    monkeypatch.setattr(audio, "OPENAI_TTS_URL",
                        fake_api.base_url + "/v1/audio/speech")
    # NB: _chunk_text's cap default binds at def time — patching the module
    # constant is a no-op (adjudicated: normal Python, not a product bug).
    # Exercise the REAL 3800 cap with genuinely long paragraphs instead.
    para = "spoken word " * 300  # ~3,600 chars, just under the cap
    script = "\n\n".join(f"Paragraph {i}. {para}" for i in range(3))
    out = tmp_path / "episode.wav"

    result = audio.generate_audio(script, out, engine="openai", openai_key="sk-x")

    posts = [r for r in fake_api.recorded if r["method"] == "POST"]
    n_chunks = len(audio._chunk_text(script))
    assert len(posts) == n_chunks and n_chunks >= 2
    assert all(len(p["body"]["input"]) <= 4096 for p in posts)  # the API cap holds
    assert all(p["body"]["model"] == audio.OPENAI_TTS_MODEL for p in posts)
    # The output header carries REAL frame counts (the placeholder-nframes
    # live finding): stdlib wave can read the whole file back.
    with wave.open(str(out), "rb") as w:
        assert w.getnframes() == 800 * n_chunks
        assert w.getframerate() == 8000 and w.getnchannels() == 1
        assert len(w.readframes(w.getnframes())) == 800 * 2 * n_chunks
    expected_duration = 800 * n_chunks / 8000
    assert result.duration_s == pytest.approx(expected_duration, abs=0.01)
    assert result.est_cost_usd == pytest.approx(
        expected_duration / 60 * audio.OPENAI_TTS_USD_PER_MIN, abs=1e-4
    )
    assert result.detail["chunks"] == n_chunks


def test_openai_engine_http_failure_names_the_chunk(tmp_path, fake_api, monkeypatch):
    fake_api.add_route("/v1/audio/speech", status=500, body=b'{"error": "down"}',
                       content_type="application/json")
    monkeypatch.setattr(audio, "OPENAI_TTS_URL",
                        fake_api.base_url + "/v1/audio/speech")
    with pytest.raises(audio.AudioError) as excinfo:
        audio.generate_audio("Some text.", tmp_path / "x.wav",
                             engine="openai", openai_key="sk-x")
    assert "chunk 1/1 failed (HTTP 500)" in str(excinfo.value)


def test_openai_engine_keyless_refuses_with_the_setting_hint(tmp_path):
    with pytest.raises(audio.AudioError) as excinfo:
        audio.generate_audio("Text.", tmp_path / "x.wav", engine="openai",
                             openai_key="")
    assert "OPENAI_API_KEY not set" in str(excinfo.value)
    assert "settings.tts_engine" in str(excinfo.value)


def test_unknown_engine_is_a_named_refusal(tmp_path):
    with pytest.raises(audio.AudioError) as excinfo:
        audio.generate_audio("Text.", tmp_path / "x.wav", engine="espeak")
    assert "unknown tts engine 'espeak'" in str(excinfo.value)


# --- kokoro: readiness + subprocess plumbing (shim engine, no real synthesis) -----------

def _install_shim(behavior="ok"):
    """A fake data/tts engine under the SANDBOXED DATA_DIR: proves paths
    resolve at call time (the import-order fix) and exercises the subprocess
    seam without kokoro."""
    venv_py = audio.tts_venv_py()
    venv_py.parent.mkdir(parents=True, exist_ok=True)
    if behavior == "ok":
        body = (
            "#!/bin/sh\n"
            'printf "RIFFfake" > "$3"\n'
            'echo \'{"duration_s": 12.3, "rate_x_realtime": 4.4}\'\n'
        )
    elif behavior == "fail":
        body = "#!/bin/sh\necho 'model exploded spectacularly' >&2\nexit 3\n"
    else:  # garbage stats
        body = "#!/bin/sh\necho 'this is not json'\n"
    venv_py.write_text(body, encoding="utf-8")
    venv_py.chmod(0o755)
    audio.tts_model().write_bytes(b"fake-onnx")
    audio.tts_voices().write_bytes(b"fake-voices")
    return venv_py


def test_kokoro_ready_names_each_missing_piece_as_its_fix():
    # Sandbox DATA_DIR (autouse) is empty: venv missing first.
    assert "run: scripts/setup_tts" in audio.kokoro_ready()
    venv_py = audio.tts_venv_py()
    venv_py.parent.mkdir(parents=True, exist_ok=True)
    venv_py.write_text("#!/bin/sh\n", encoding="utf-8")
    assert "Kokoro model files missing" in audio.kokoro_ready()
    audio.tts_model().write_bytes(b"x")
    audio.tts_voices().write_bytes(b"x")
    assert audio.kokoro_ready() is None  # call-time resolution under the sandbox


def test_kokoro_missing_engine_is_an_audio_error_with_the_fix(tmp_path):
    with pytest.raises(audio.AudioError) as excinfo:
        audio.generate_audio("Text.", tmp_path / "x.wav", engine="kokoro")
    assert "run: scripts/setup_tts" in str(excinfo.value)


def test_kokoro_shim_success_parses_stats_and_cleans_tmp(tmp_path):
    _install_shim("ok")
    out = tmp_path / "ep.wav"
    result = audio.generate_audio("Hello there.", out, engine="kokoro")
    assert result.engine == "kokoro"
    assert result.duration_s == 12.3
    assert result.est_cost_usd == 0.0
    assert out.exists()
    assert not out.with_suffix(".txt.tmp").exists()  # tmp input cleaned


def test_kokoro_runner_failure_surfaces_the_stderr_tail(tmp_path):
    _install_shim("fail")
    with pytest.raises(audio.AudioError) as excinfo:
        audio.generate_audio("Hello.", tmp_path / "ep.wav", engine="kokoro")
    msg = str(excinfo.value)
    assert "exit 3" in msg and "model exploded spectacularly" in msg


def test_kokoro_unreadable_stats_is_a_named_failure(tmp_path):
    _install_shim("garbage")
    with pytest.raises(audio.AudioError) as excinfo:
        audio.generate_audio("Hello.", tmp_path / "ep.wav", engine="kokoro")
    assert "unreadable stats" in str(excinfo.value)


# --- config + doctor -----------------------------------------------------------------

def test_settings_tts_engine_validation(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text(
        "sources:\n  - name: A\n    rss_url: https://a.example/f\n"
        "settings:\n  tts_engine: openai\n",
        encoding="utf-8",
    )
    cfg = config.load_sources(p)
    assert cfg.problems == [] and cfg.tts_engine == "openai"

    p.write_text(
        "sources:\n  - name: A\n    rss_url: https://a.example/f\n"
        "settings:\n  tts_engine: espeak\n",
        encoding="utf-8",
    )
    assert any("must be kokoro or openai" in pr for pr in config.load_sources(p).problems)

    p.write_text("sources:\n  - name: A\n    rss_url: https://a.example/f\n",
                 encoding="utf-8")
    assert config.load_sources(p).tts_engine == "kokoro"  # default


def test_doctor_missing_engine_is_hard_fail_even_with_synth_skip(monkeypatch):
    """The QA-ruling condition: the skip marker must NEVER mask engine
    absence — a listening-primary product with no engine is ✗."""
    from newslens import doctor

    monkeypatch.setenv("NEWSLENS_DOCTOR_TTS_SYNTH", "0")
    results = doctor.check_tts()
    fails = [r for r in results if r.status == doctor.FAIL]
    assert fails and "run: scripts/setup_tts" in fails[0].text


def test_doctor_synth_skip_always_renders_its_marker(monkeypatch):
    """The other ruling condition: skipping is DISCLOSED, never silent."""
    from newslens import doctor

    _install_shim("ok")
    monkeypatch.setenv("NEWSLENS_DOCTOR_TTS_SYNTH", "0")
    results = doctor.check_tts()
    assert not any(r.status == doctor.FAIL for r in results)
    infos = [r.text for r in results if r.status == doctor.INFO]
    assert any("tts real-synthesis check skipped" in t and "QA/offline mode" in t
               for t in infos)


def test_doctor_openai_engine_mode_missing_local_engine_is_info(tmp_paths):
    from newslens import doctor

    paths.SOURCES_FILE.write_text(
        "sources:\n  - name: A\n    rss_url: https://a.invalid/f\n"
        "settings:\n  tts_engine: openai\n",
        encoding="utf-8",
    )
    results = doctor.check_tts()
    assert not any(r.status == doctor.FAIL for r in results)
    assert any("settings.tts_engine = openai" in r.text for r in results)


# --- M6 ride: _outlet_token skips leading articles --------------------------------------

def test_outlet_token_skips_leading_articles():
    from newslens import generate

    assert generate._outlet_token("The Hill") == "hill"
    assert generate._outlet_token("BBC News — World") == "bbc"
    # And the single-source lede check is no longer vacuous for The-prefixed
    # outlets: naming "the Hill" in prose satisfies it.
    slots = [dict(
        slot=1, story_title="T", summary="S", item_ids=[1],
        outlets=["The Hill"], matched_tags=[], matched_memory=[],
        matched_dormant=[], followed_analyst=False, personal_score=0.0,
        world_impact=5, world_impact_reason="R", combined_score=0.3,
        override=False, override_label=None, corroboration_count=1,
        corroboration_label="Reported by 1 named outlet",
        wire_items_excluded=0, revived_threads=[],
    )]
    payload = {"stories": [{
        "tier": "full",
        "headline": "A headline here",
        "lede": "Only the Hill is carrying this development so far. Details are thin.",
        "why_it_matters": "Concrete effects on the reader.",
        "watch_for": "The next filing.",
        "why_label": "Why it matters",   # A7 (M6 fix loop): sanctioned menu
        "watch_label": "Watch for",
    }]}
    _, warns = generate.validate_narrative_payload(payload, slots, "A")
    assert not any("single-outlet" in w for w in warns)
