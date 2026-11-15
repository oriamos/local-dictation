"""Lecture mode: continuous transcription into the built-in lecture editor, with the same Whisper engine.

Flow
  mic (always on during the lecture) ─► audio file on disk (FLAC, crash-safe)
                                    └► segmenter: a chunk every ~30 s regardless of pauses (cut on a breath if any)
                                         └► Whisper (shared single-thread ASR pool, so dictation and lecture never
                                            run concurrently) with prompt = lecture terms + tail of previous text
                                              └► cleanup + dictionary ─► optional local-LLM normalization (guarded)
                                                   └► transcript.md on disk (append + fsync) ─► listeners (editor)
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .postprocess import Dictionary, process

log = logging.getLogger("dictation.lecture")
SR = 16000


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Segmenter:
    """Fixed ~30 s chunks, whether or not the speaker pauses (Silero VAD only picks a clean cut point).

    Whisper reads exactly 30 s at a time, so chunks of up to 29.5 s give it the most context. Inside the last few
    seconds of each chunk the cut lands on the latest breath / short silence, so words are not split; if there is
    none, it is a hard cut at 29.5 s. Silence-only chunks are dropped from the text (the audio file keeps them).
    """

    WINDOW = 8.0   # look for a cut point within the last 8 s before the target

    def __init__(self, chunk_s: float = 30.0):
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        self.target = min(float(chunk_s), 29.5)
        self._get = get_speech_timestamps
        self._opts = VadOptions(threshold=0.45, min_speech_duration_ms=150, min_silence_duration_ms=120,
                                speech_pad_ms=60)

    def cut(self, buf: np.ndarray) -> tuple[int, bool]:
        """Return (cut_sample, has_speech). cut 0 = wait for more audio."""
        n = len(buf)
        lo = int((self.target - self.WINDOW) * SR)
        if n < lo + 2 * SR:
            return 0, False
        ts = self._get(buf, self._opts)
        if not ts:
            return (n, False) if n >= self.target * SR else (0, False)
        # cut points: every silence between speech regions (and the trailing one) as (length, middle)
        gaps = [(ts[i + 1]["start"] - ts[i]["end"], (ts[i]["end"] + ts[i + 1]["start"]) // 2)
                for i in range(len(ts) - 1)]
        if n - ts[-1]["end"] >= int(0.15 * SR):
            gaps.append((n - ts[-1]["end"], (ts[-1]["end"] + n) // 2))
        cand = [g for g in gaps if g[1] >= lo]
        # prefer the LONGEST pause in the window (usually a sentence end), later one on ties
        best = max(cand, key=lambda g: (g[0] // int(0.1 * SR), g[1]))[1] if cand else 0
        if n >= self.target * SR:                       # time's up: best clean point, else hard cut
            return (best or n), True
        if cand and n - ts[-1]["end"] >= int(0.6 * SR) and n >= (self.target - 3) * SR:
            return (ts[-1]["end"] + n) // 2, True       # close to the target and the speaker just paused
        return 0, False


class LectureSession:
    def __init__(self, folder: Path, title: str, terms: list[str], submit, base_dict: dict[str, list[str]],
                 on_event, on_error, chunk_s: float = 30.0, get_llm=None, polish_on=None):
        self.folder, self.title, self.terms = folder, title, terms
        self.submit, self.on_event, self.on_error = submit, on_event, on_error
        self.chunk_s = chunk_s
        self.get_llm = get_llm or (lambda: None)        # → LLMPolisher or None
        self.polish_on = polish_on or (lambda: False)   # read per chunk: the switch can change mid-lecture
        self.base_dict = base_dict
        self.dictionary = self._make_dict()
        self.started = time.time()
        self.running = False
        self._q: queue.Queue = queue.Queue()
        self._stream = None
        self._audio_file = None
        self._md = None
        self._prev_text = ""
        self._pending = 0  # segments submitted but not yet transcribed
        self._lock = threading.Lock()

    def _make_dict(self) -> Dictionary:
        d = dict(self.base_dict)
        for t in self.terms:
            d.setdefault(t, [])
        return Dictionary(d)

    def set_terms(self, terms: list[str]):
        self.terms = terms
        self.dictionary = self._make_dict()

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        import sounddevice as sd
        import soundfile as sf
        self.folder.mkdir(parents=True, exist_ok=True)
        self._audio_file = sf.SoundFile(self.folder / "audio.flac", "w", samplerate=SR, channels=1, format="FLAC")
        self._md = open(self.folder / "transcript.md", "a", encoding="utf-8")
        self._md.write(f"# {self.title}\n\n{datetime.now():%Y-%m-%d %H:%M}\n\n")
        self._md.flush()
        sd._terminate(); sd._initialize()
        self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=int(SR * 0.1),
                                      callback=lambda d, n, t, s: self._q.put(d[:, 0].copy()))
        self._stream.start()
        self.started = time.time()
        self.running = True
        threading.Thread(target=self._worker, daemon=True, name="lecture").start()
        log.info("lecture started: %s", self.folder.name)

    def stop(self):
        self.running = False
        if self._stream is not None:
            try:
                self._stream.stop(); self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._q.put(None)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    # ------------------------------------------------------------------ audio → segments
    def _worker(self):
        seg = Segmenter(self.chunk_s)
        buf = np.zeros(0, np.float32)
        offset = 0  # sample index (since start) of buf[0]
        last_check = 0.0
        while True:
            chunk = self._q.get()
            final = chunk is None
            if not final:
                self._audio_file.write(chunk)
                buf = np.concatenate([buf, chunk])
            now = time.perf_counter()
            if not final and now - last_check < 0.5:
                continue
            last_check = now
            try:
                cut, speech = (len(buf), True) if final else seg.cut(buf)
            except Exception as e:
                log.exception("segmenter failed"); cut, speech = 0, False
                self.on_error(str(e))
            if cut:
                piece, start_s = buf[:cut], offset / SR
                buf, offset = buf[cut:], offset + cut
                if speech and len(piece) > 0.5 * SR:
                    self._transcribe_later(piece, start_s)
            if final:
                break
        self._audio_file.close()
        self._wait_and_close()

    def _transcribe_later(self, audio: np.ndarray, start_s: float):
        with self._lock:
            self._pending += 1
        self.submit(lambda: self._transcribe(audio, start_s))

    def _transcribe(self, audio, start_s):
        try:
            prompt = ", ".join(self.terms[:40])
            if self._prev_text:
                prompt = (prompt + ". " if prompt else "") + self._prev_text[-200:]
            r = self.engine.transcribe(audio, prompt=prompt or None)
            text = process(r.text, "clean", self.dictionary) if r.text else ""
            polished = None
            if text and self.polish_on():
                llm = self.get_llm()
                if llm is not None:
                    # same guard as dictation: rejected / over budget → the Whisper text is kept unchanged
                    # a chunk arrives every ~30 s, so the model can take far longer than in dictation
                    budget = min(22.0, max(8.0, 0.35 * len(text.split())))
                    # only this lecture's terms: the personal dictionary was already applied deterministically,
                    # and dozens of unrelated terms in the prompt make the model drop real tokens (measured)
                    p = llm.polish(text, "lecture", list(self.terms), budget=budget)
                    log.info("lecture polish %s (%s) %.1fs", "accepted" if p.accepted else "rejected", p.reason, p.gen_s)
                    polished = p.accepted
                    text = p.text
            if text:
                self._prev_text = text
                ts = fmt_ts(start_s)
                self._md.write(f"[{ts}] {text}\n\n"); self._md.flush(); os.fsync(self._md.fileno())
                self.on_event({"type": "segment", "ts": ts, "text": text, "polished": polished})
        except Exception as e:
            log.exception("lecture segment failed")
            self.on_error(str(e))
        finally:
            with self._lock:
                self._pending -= 1

    def _wait_and_close(self):
        for _ in range(600):  # let queued segments finish (max ~60 s)
            with self._lock:
                if self._pending <= 0:
                    break
            time.sleep(0.1)
        if self._md:
            self._md.close(); self._md = None
        self.on_event({"type": "info", "ts": fmt_ts(self.elapsed), "text": "סוף ההקלטה"})
        log.info("lecture stopped after %s", fmt_ts(self.elapsed))

    def mark(self):
        ts = fmt_ts(self.elapsed)
        if self._md:
            self._md.write(f"**⭐ חשוב [{ts}]**\n\n"); self._md.flush()
        self.on_event({"type": "mark", "ts": ts, "text": "חשוב!"})


class LectureController:
    """Owns the current session and fans its events out to listeners (the editor window)."""

    def __init__(self, store, get_engine, submit, base_dir: Path, notify, get_llm=None):
        self.store, self.get_engine, self.submit, self.base_dir, self.notify = store, get_engine, submit, base_dir, notify
        self.get_llm = get_llm or (lambda: None)
        self.session: LectureSession | None = None
        self.listeners: list = []          # callables(event) — called from worker threads
        self._seq = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return bool(self.session and self.session.running)

    def start(self, title: str = "", terms: list[str] | None = None, base_dict=None) -> LectureSession:
        if self.active:
            return self.session
        engine = self.get_engine()
        if engine is None:
            raise RuntimeError("המודל עדיין נטען — נסה שוב בעוד כמה שניות")
        title = (title or "").strip() or f"הרצאה {datetime.now():%d.%m.%Y %H:%M}"
        if terms is not None:
            self.set_terms(terms)
        terms = self.store.get("lecture_terms") or []
        safe = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60]
        folder = self.base_dir / f"{datetime.now():%Y-%m-%d_%H%M} {safe}"
        s = LectureSession(folder, title, terms, self.submit, base_dict or {}, self._emit, self._on_error,
                           chunk_s=float(self.store.get("lecture_chunk_s") or 30),
                           get_llm=self.get_llm, polish_on=lambda: bool(self.store.get("lecture_polish")))
        s.engine = engine
        s.start()
        self.session = s
        self.notify("start", folder)
        return s

    def stop(self):
        if self.session and self.session.running:
            self.session.stop()
            self.notify("stop", self.session.folder)

    def mark(self):
        if self.active:
            self.session.mark()

    def set_terms(self, terms: list[str]):
        terms = [t.strip() for t in terms if t and t.strip()]
        self.store.set("lecture_terms", terms)
        if self.session:
            self.session.set_terms(terms)

    def _emit(self, ev: dict):
        with self._lock:
            self._seq += 1
            ev["id"] = self._seq
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:
                log.exception("lecture listener failed")

    def _on_error(self, msg):
        self._emit({"type": "info", "ts": fmt_ts(self.session.elapsed if self.session else 0),
                    "text": f"שגיאה בתמלול קטע: {msg[:120]}"})
