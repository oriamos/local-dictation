"""End-to-end pipeline without a mic: default engine (from settings) → VAD → ASR → post-processing.
Also: silence / noise must yield no text (no Whisper hallucinations), and the dictionary prompt must not break output.
Run:  python -m tests.smoke_pipeline
"""
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.stdout.reconfigure(encoding="utf-8")
from dictation.asr import make_engine  # noqa: E402
from dictation.postprocess import Dictionary, process  # noqa: E402
from dictation.storage import DEFAULT_DICTIONARY, DEFAULT_SETTINGS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
clips = sorted((ROOT / "bench/data/fleurs_he/dev").glob("*.wav"))[:3]

eng = make_engine(DEFAULT_SETTINGS["asr_primary"])
t0 = time.perf_counter(); eng.load(); print(f"engine {eng.name} load+warmup {time.perf_counter() - t0:.1f}s")
d = Dictionary(DEFAULT_DICTIONARY)
ok = True

rng = np.random.default_rng(1)
for name, audio in [("silence 3s", np.zeros(48000, np.float32)),
                    ("room noise 3s", rng.normal(0, 0.003, 48000).astype(np.float32))]:
    r = eng.transcribe(audio, prompt=d.prompt())
    print(f"{name:14s} no_speech={r.no_speech} text={r.text!r} ({r.decode_s * 1000:.0f} ms)")
    ok &= r.no_speech and not r.text

for c in clips:
    a, _ = sf.read(c, dtype="float32")
    a = np.concatenate([np.zeros(16000, np.float32), a, np.zeros(24000, np.float32)])  # silence around speech
    for prompt in (None, d.prompt()):
        r = eng.transcribe(a, prompt=prompt)
        print(f"{c.name[:12]} {r.audio_s:4.1f}s speech={r.speech_s:4.1f}s {r.decode_s:.2f}s prompt={'Y' if prompt else 'N'} | "
              f"{process(r.text, 'clean', d)}")
        ok &= bool(r.text)
print("RESULT:", "PASS" if ok else "FAIL")
