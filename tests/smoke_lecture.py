"""Lecture pipeline without a mic: ~70 s of FLEURS clips with only short gaps (continuous speech) are fed as a
lecture. Checks ~30 s chunking regardless of pauses (22–29.5 s per chunk except the last), timestamps,
transcript/audio files, the mark event, and — with --polish — the local-LLM normalization (guarded).
Run:  python -m tests.smoke_lecture [--polish]
"""
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

sys.stdout.reconfigure(encoding="utf-8")
from dictation.asr import make_engine  # noqa: E402
from dictation.lecture import LectureSession  # noqa: E402
from dictation.storage import DEFAULT_DICTIONARY, DEFAULT_SETTINGS  # noqa: E402

POLISH = "--polish" in sys.argv
ROOT = Path(__file__).resolve().parent.parent
clips = sorted((ROOT / "bench/data/fleurs_he/dev").glob("*.wav"))[20:28]
SR = 16000

eng = make_engine(DEFAULT_SETTINGS["asr_primary"]); eng.load()
llm = None
if POLISH:
    from dictation.llm import LLMPolisher
    c = DEFAULT_SETTINGS["llm"]
    llm = LLMPolisher(c["model_dir"], c["device"], c["budget_s"], c["budget_per_word"])
    t = time.perf_counter(); llm.load(); print(f"LLM loaded in {time.perf_counter() - t:.1f}s")
pool = ThreadPoolExecutor(1)
events, done_at = [], {}

def on_event(ev):
    events.append(ev); done_at[ev.get("ts")] = time.perf_counter()

tmp = Path(tempfile.mkdtemp())
s = LectureSession(tmp / "lec", "בדיקה", [], pool.submit, DEFAULT_DICTIONARY, on_event, print,
                   chunk_s=30, get_llm=lambda: llm, polish_on=lambda: POLISH)
s.engine = eng
s.folder.mkdir(parents=True)
s._audio_file = sf.SoundFile(s.folder / "audio.flac", "w", samplerate=SR, channels=1, format="FLAC")
s._md = open(s.folder / "transcript.md", "a", encoding="utf-8")
s.running = True
w = threading.Thread(target=s._worker, daemon=True); w.start()

parts = []
for c in clips:
    a, _ = sf.read(c, dtype="float32")
    parts += [a, np.zeros(int(0.3 * SR), np.float32)]  # continuous lecture: only 0.3 s between sentences
audio = np.concatenate(parts)
SPEED = 4  # 4× real time; the segmenter decides on sample counts, not wall clock
t0 = time.perf_counter()
for i in range(0, len(audio), int(0.1 * SR)):
    s._q.put(audio[i:i + int(0.1 * SR)])
    time.sleep(0.1 / SPEED)
s.mark()
s.stop(); w.join(180)
segs = [e for e in events if e["type"] == "segment"]
starts = [int(e["ts"][:2]) * 60 + int(e["ts"][3:]) for e in segs] + [len(audio) / SR]
lengths = [starts[i + 1] - starts[i] for i in range(len(segs))]
print(f"lecture {len(audio) / SR:.1f}s → {len(segs)} chunks, lengths {[round(x, 1) for x in lengths]}"
      f"{' | polish: ' + str([e.get('polished') for e in segs]) if POLISH else ''}")
for e in segs:
    print(f"  [{e['ts']}] {e['text']}")
md = (s.folder / "transcript.md").read_text(encoding="utf-8")
flac_s = sf.info(s.folder / "audio.flac").duration
ok_chunks = all(21.5 <= x <= 30.0 for x in lengths[:-1]) and len(segs) >= 2
ok = ok_chunks and all(e["text"] for e in segs) and abs(flac_s - len(audio) / SR) < 0.5 and any(e["type"] == "mark" for e in events)
print(f"chunks ~30 s: {ok_chunks} | audio.flac {flac_s:.1f}s | transcript lines {md.count(chr(10))}")
print("RESULT:", "PASS" if ok else "FAIL")
