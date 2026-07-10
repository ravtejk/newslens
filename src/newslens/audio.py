"""generate_audio — the vendor-agnostic TTS wrapper (milestone 6, spec §E-M6
+ the TTS reconvene, workspace/debates/2026-07-02--newslens--engineering-2.md).

ONE function boundary, no provider registry (Remy's seam ruling). Engines:

  * openai (DEFAULT since the 2026-07-06 ear test — principal ruling, P3.1
    item 4: "I prefer the voice of the openai wav"): gpt-4o-mini-tts on the
    existing key (~$0.015/min, ~+$0.07/run at current script lengths).
    Scripts exceed the API's 4,096-char input cap, so the text is chunked on
    paragraph boundaries and the WAV segments are concatenated losslessly
    (stdlib wave).
  * kokoro (the fully built $0 FALLBACK; was the v1 default 2026-07-02 →
    2026-07-06): Kokoro-82M via kokoro-onnx in an ISOLATED Python 3.12 venv
    (data/tts/venv), invoked by subprocess through tools/tts_runner.py — the
    app itself stays on the 3.9 floor (ADR-0008: current Kokoro packaging
    requires >=3.10; the engine venv is the boring resolution, one brew
    dependency, no torch). Local, free, no key. MEASURED on this machine:
    ~4.4x realtime — LOUDLY below the reconvene's 14x M-series floor (Rook's
    dissent vindicated), while still clearing the operational bar (~71s for
    a 5-minute episode). The 4.4x re-open is moot while Kokoro isn't the
    default; the engine question re-opens if voice quality changes
    (principal: "maybe this can change in the future").

Engine choice: settings.tts_engine in sources.yaml (kokoro|openai, default
openai) — a config flip, not a code fork. Every path has a timeout and a
visible failure; generate degrades to a no-audio run WITH disclosure rather
than dying (audio is the last step; the text briefing must never be hostage
to a synth failure).
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import paths

VALID_TTS_ENGINES = ("kokoro", "openai")
# P3.1 item 4 (principal ear-test ruling 2026-07-06): gpt-4o-mini-tts is the
# default voice; kokoro stays fully built as the $0 local fallback.
DEFAULT_TTS_ENGINE = "openai"

# Engine paths resolve DYNAMICALLY from paths.DATA_DIR (not import-time
# constants): sandboxed suites patch paths.DATA_DIR, and binding at import
# would make engine presence depend on import order (M6 QA-seam finding).
def tts_dir() -> Path:
    return paths.DATA_DIR / "tts"


def tts_venv_py() -> Path:
    return tts_dir() / "venv" / "bin" / "python"


def tts_model() -> Path:
    return tts_dir() / "kokoro-v1.0.onnx"


def tts_voices() -> Path:
    return tts_dir() / "voices-v1.0.bin"


TTS_RUNNER = paths.PROJECT_ROOT / "tools" / "tts_runner.py"
KOKORO_TIMEOUT_S = 900          # 4.4x realtime measured => full script ~3min; 6x headroom

OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_VOICE = "alloy"
OPENAI_TTS_CHUNK_CHARS = 3800   # API cap 4096; headroom for safety
OPENAI_TTS_TIMEOUT_S = 300
OPENAI_TTS_USD_PER_MIN = 0.015  # engineering-2 pricing basis


class AudioError(RuntimeError):
    """Visible, handled synthesis failure — callers degrade with disclosure."""


@dataclass
class AudioResult:
    path: str
    engine: str
    duration_s: float
    gen_time_s: float
    est_cost_usd: float
    detail: Dict


def kokoro_ready() -> Optional[str]:
    """None when the local engine is fully present; otherwise the missing
    piece, phrased as the fix (the doctor renders this)."""
    if not tts_venv_py().exists():
        return "TTS engine venv missing — run: scripts/setup_tts"
    if not tts_model().exists() or not tts_voices().exists():
        return "Kokoro model files missing — run: scripts/setup_tts"
    if not TTS_RUNNER.exists():
        return "tools/tts_runner.py missing — restore it from the repo"
    return None


def _synthesize_kokoro(script_text: str, out_path: Path) -> AudioResult:
    problem = kokoro_ready()
    if problem:
        raise AudioError(problem)
    tmp_txt = out_path.with_suffix(".txt.tmp")
    tmp_txt.write_text(script_text, encoding="utf-8")
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(tts_venv_py()), str(TTS_RUNNER), str(tmp_txt), str(out_path),
             str(tts_model()), str(tts_voices())],
            capture_output=True, text=True, timeout=KOKORO_TIMEOUT_S,
            env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},  # carryover 19: scrub
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioError(
            f"kokoro synthesis exceeded {KOKORO_TIMEOUT_S}s — engine wedged? "
            "(scripts/doctor runs a short real-synthesis check)"
        ) from exc
    finally:
        tmp_txt.unlink(missing_ok=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise AudioError(f"kokoro runner failed (exit {proc.returncode}): {tail}")
    try:
        stats = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise AudioError(f"kokoro runner returned unreadable stats: {exc}") from exc
    return AudioResult(
        path=str(out_path), engine="kokoro",
        duration_s=stats.get("duration_s") or 0.0,
        gen_time_s=round(time.monotonic() - t0, 2),
        est_cost_usd=0.0, detail=stats,
    )


def _chunk_text(text: str, cap: int = OPENAI_TTS_CHUNK_CHARS) -> List[str]:
    """Paragraph-boundary chunking under the API input cap; a single
    oversized paragraph falls back to sentence splits."""
    chunks: List[str] = []
    current = ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        pieces = [para]
        if len(para) > cap:
            import re
            pieces = re.split(r"(?<=[.!?])\s+", para)
        for piece in pieces:
            while len(piece) > cap:  # pathological boundary-less run: hard-slice
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.append(piece[:cap])
                piece = piece[cap:]
            if len(current) + len(piece) + 2 > cap and current:
                chunks.append(current.strip())
                current = ""
            current += piece + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _synthesize_openai(script_text: str, out_path: Path, key: str,
                       budget_cap: Optional[float] = None) -> AudioResult:
    if not key:
        raise AudioError(
            "OPENAI_API_KEY not set — the openai TTS engine needs it "
            "(or switch settings.tts_engine to kokoro, the $0 local fallback)"
        )
    # Carryover 16: the one spending path without a cap pre-check.
    est_minutes = len(script_text.split()) / 160.0
    est_usd = est_minutes * OPENAI_TTS_USD_PER_MIN
    if budget_cap is not None and est_usd > budget_cap:
        raise AudioError(
            f"estimated openai-tts cost ${est_usd:.3f} exceeds the remaining "
            f"run budget (${budget_cap:.2f}) — aborting before any call"
        )
    chunks = _chunk_text(script_text)
    t0 = time.monotonic()
    wav_params = None
    frames: List[bytes] = []
    for i, chunk in enumerate(chunks, start=1):
        body = json.dumps({
            "model": OPENAI_TTS_MODEL, "voice": OPENAI_TTS_VOICE,
            "input": chunk, "response_format": "wav",
        }).encode("utf-8")
        req = urllib.request.Request(
            OPENAI_TTS_URL, data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": "NewsLens/0.1 (personal news briefing; tts)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=OPENAI_TTS_TIMEOUT_S) as resp:
                blob = resp.read()
        except urllib.error.HTTPError as exc:
            from . import ranking  # carryover 19: one error-detail parser, not two
            detail = ranking._http_error_detail(exc)
            raise AudioError(
                f"openai tts chunk {i}/{len(chunks)} failed (HTTP {exc.code}"
                + (f"; {detail}" if detail else "") + ")"
            ) from exc
        except Exception as exc:
            raise AudioError(
                f"openai tts chunk {i}/{len(chunks)}: {type(exc).__name__}: "
                f"{getattr(exc, 'reason', exc)}"
            ) from exc
        # Concatenate WAV payloads losslessly via frames (stdlib only).
        import io
        with wave.open(io.BytesIO(blob), "rb") as w:
            params = w.getparams()
            if wav_params is None:
                wav_params = params
            elif (params.nchannels, params.sampwidth, params.framerate) != (
                wav_params.nchannels, wav_params.sampwidth, wav_params.framerate
            ):
                # Carryover 19: refuse to concatenate mismatched formats —
                # a silent mix produces chipmunk/garbled audio, not an error.
                raise AudioError(
                    f"openai tts chunk {i} format differs "
                    f"({params.framerate}Hz/{params.nchannels}ch vs "
                    f"{wav_params.framerate}Hz/{wav_params.nchannels}ch)"
                )
            frames.append(w.readframes(w.getnframes()))
    if wav_params is None:
        raise AudioError("openai tts produced no audio (empty script?)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        # Never copy params wholesale: the API streams its WAV, so chunk
        # headers carry a placeholder nframes (~0xFFFFFFFF) that poisons the
        # output header (live M6 finding: struct.error on write). Set the
        # format fields; wave computes real frame counts from written data.
        w.setnchannels(wav_params.nchannels)
        w.setsampwidth(wav_params.sampwidth)
        w.setframerate(wav_params.framerate)
        for fr in frames:
            w.writeframes(fr)
    total_frames = sum(len(f) for f in frames) // (wav_params.sampwidth * wav_params.nchannels)
    duration = total_frames / wav_params.framerate
    return AudioResult(
        path=str(out_path), engine="openai",
        duration_s=round(duration, 2),
        gen_time_s=round(time.monotonic() - t0, 2),
        est_cost_usd=round(duration / 60.0 * OPENAI_TTS_USD_PER_MIN, 4),
        detail={"model": OPENAI_TTS_MODEL, "voice": OPENAI_TTS_VOICE,
                "chunks": len(chunks)},
    )


def generate_audio(
    script_text: str,
    out_path: Path,
    engine: str = DEFAULT_TTS_ENGINE,
    openai_key: str = "",
    budget_cap: Optional[float] = None,
) -> AudioResult:
    """THE wrapper (engineering-2's one-function-boundary ruling)."""
    if engine == "kokoro":
        return _synthesize_kokoro(script_text, out_path)
    if engine == "openai":
        return _synthesize_openai(script_text, out_path, openai_key, budget_cap)
    raise AudioError(
        f"unknown tts engine {engine!r} — settings.tts_engine must be one of "
        f"{VALID_TTS_ENGINES}"
    )
