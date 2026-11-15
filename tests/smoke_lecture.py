"""Lecture pipeline without a mic or ONLYOFFICE: three FLEURS clips with pauses are fed as a 'lecture'.
Checks segmentation at pauses, timestamps, transcript/audio files on disk, the mark event, and the local API
(token required). Run:  python -m tests.smoke_lecture
"""
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

sys.stdout.reconfigure(encoding="utf-8")
from dictation.asr import make_engine  # noqa: E402
from dictation.lecture import LectureController, LectureSession  # noqa: E402
from dictation.storage import DEFAULT_DICTIONARY, DEFAULT_SETTINGS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
clips = sorted((ROOT / "bench/data/fleurs_he/dev").glob("*.wav"))[10:13]
SR = 16000

eng = make_engine(DEFAULT_SETTINGS["asr_primary"]); eng.load()
pool = ThreadPoolExecutor(1)
events = []

tmp = Path(tempfile.mkdtemp())
s = LectureSession(tmp / "lec", "בדיקה", ["פלאפל"], pool.submit, DEFAULT_DICTIONARY, events.append, print)
s.engine = eng
# start without a mic: open the files and run the worker, then push audio ourselves
s.folder.mkdir(parents=True)
s._audio_file = sf.SoundFile(s.folder / "audio.flac", "w", samplerate=SR, channels=1, format="FLAC")
s._md = open(s.folder / "transcript.md", "a", encoding="utf-8")
s.running = True
import threading  # noqa: E402
w = threading.Thread(target=s._worker, daemon=True); w.start()

lecture = []
for c in clips:
    a, _ = sf.read(c, dtype="float32")
    lecture += [a, np.zeros(int(1.2 * SR), np.float32)]  # 1.2 s pause between "sentences"
audio = np.concatenate(lecture)
t0 = time.perf_counter()
for i in range(0, len(audio), int(0.1 * SR)):  # real time, like the mic callback (100 ms blocks)
    s._q.put(audio[i:i + int(0.1 * SR)])
    time.sleep(0.1)
s.mark()
s.stop(); w.join(60)
segs = [e for e in events if e["type"] == "segment"]
print(f"lecture {len(audio) / SR:.1f}s → {len(segs)} segments in {time.perf_counter() - t0:.1f}s")
for e in segs:
    print(f"  [{e['ts']}] {e['text']}")
md = (s.folder / "transcript.md").read_text(encoding="utf-8")
flac_s = sf.info(s.folder / "audio.flac").duration
ok = len(segs) >= 3 and all(e["text"] for e in segs) and md.count("[") >= len(segs) and abs(flac_s - len(audio) / SR) < 0.5
print(f"transcript.md lines: {md.count(chr(10))}, audio.flac {flac_s:.1f}s, mark event: {any(e['type'] == 'mark' for e in events)}")

# ---- local API: auth + status/events
class S(dict):
    def get(self, k, d=None): return super().get(k, d)
    def set(self, k, v): self[k] = v
ctl = LectureController(S(), lambda: eng, pool.submit, tmp, lambda *a: None)
port = ctl.serve()
def get(path):
    try:
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3).read())
    except urllib.error.HTTPError as e:
        return {"http": e.code}
bad = get("/status?t=wrong"); good = get(f"/status?t={ctl.token}"); evs = get(f"/events?since=0&t={ctl.token}")
print("api: no token →", bad, "| status →", good.get("state"), "| events →", len(evs.get("events", [])))
ok &= bad.get("http") == 403 and good.get("state") == "idle"
print("RESULT:", "PASS" if ok else "FAIL")
