"""Kokoro engine runner — executes INSIDE the isolated TTS venv
(data/tts/venv, Python 3.12; see scripts/setup_tts and ADR-0008).

Invoked by src/newslens/audio.py via subprocess: reads script text from
argv[1] (a file path), writes a 24kHz mono WAV to argv[2], prints one JSON
line of stats to stdout. Kept dependency-minimal and stateless: text in,
audio + stats out. Voice fixed to af_heart (Kokoro's flagship US-English
voice) until the principal asks for a voice knob.
"""
import json
import sys
import time
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

BASE = Path(__file__).resolve().parent.parent / "data" / "tts"
VOICE = "af_heart"
SPEED = 1.0


def main() -> int:
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    out_path = Path(sys.argv[2])
    t0 = time.monotonic()
    kokoro = Kokoro(str(BASE / "kokoro-v1.0.onnx"), str(BASE / "voices-v1.0.bin"))
    load_s = time.monotonic() - t0
    t1 = time.monotonic()
    samples, sample_rate = kokoro.create(text, voice=VOICE, speed=SPEED, lang="en-us")
    synth_s = time.monotonic() - t1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), samples, sample_rate)
    duration = len(samples) / sample_rate
    print(json.dumps({
        "engine": "kokoro-onnx-0.5.0", "voice": VOICE,
        "duration_s": round(duration, 2),
        "model_load_s": round(load_s, 2),
        "synth_s": round(synth_s, 2),
        "realtime_x": round(duration / synth_s, 1) if synth_s > 0 else None,
        "sample_rate": sample_rate,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
