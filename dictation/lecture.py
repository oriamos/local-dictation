"""Lecture mode: continuous transcription into a live document (ONLYOFFICE plugin) with the same Whisper engine.

Flow
  mic (always on during the lecture) ─► audio file on disk (FLAC, crash-safe)
                                    └► segmenter: Silero VAD finds natural pauses; cut every ~5–15 s (25 s max)
                                         └► Whisper (shared single-thread ASR pool, so dictation and lecture never
                                            run concurrently) with prompt = lecture terms + tail of previous text
                                              └► cleanup + dictionary ─► transcript.md on disk (append + fsync)
                                                                      └► event queue ─► local HTTP API ─► plugin

Local API (127.0.0.1 only; every request must carry ?t=<token>, which only the installed plugin knows):
  GET  /status                 → state, elapsed, session id, title, terms
  GET  /events?since=N         → new events since id N  (segment | mark | info)
  POST /start {title, terms}   POST /stop   POST /mark   POST /terms {terms}
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from .postprocess import Dictionary, process

log = logging.getLogger("dictation.lecture")
SR = 16000


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Segmenter:
    """Cuts a growing audio buffer at natural pauses using Silero VAD (bundled with faster-whisper)."""

    MIN_S, MAX_S, PAUSE_S = 2.0, 25.0, 0.7

    def __init__(self):
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        self._get = get_speech_timestamps
        self._opts = VadOptions(threshold=0.45, min_speech_duration_ms=200, min_silence_duration_ms=400,
                                speech_pad_ms=150)

    def cut(self, buf: np.ndarray) -> tuple[int, bool]:
        """Return (cut_sample, has_speech). cut 0 = wait for more audio."""
        n = len(buf)
        if n < self.MIN_S * SR:
            return 0, False
        ts = self._get(buf, self._opts)
        if not ts:
            return (n, False) if n > 12 * SR else (0, False)  # long silence: drop it from the text pipeline
        last_end = ts[-1]["end"]
        if n - last_end >= self.PAUSE_S * SR and last_end >= 1.0 * SR:
            return min(n, last_end + int(0.25 * SR)), True
        if n >= self.MAX_S * SR:  # nobody paused: cut at the widest gap after 8 s, else hard cut
            gaps = [(ts[i + 1]["start"] - ts[i]["end"], ts[i]["end"]) for i in range(len(ts) - 1)
                    if ts[i]["end"] > 8 * SR]
            return ((max(gaps)[1] + int(0.1 * SR)) if gaps else n), True
        return 0, False


class LectureSession:
    def __init__(self, folder: Path, title: str, terms: list[str], submit, base_dict: dict[str, list[str]],
                 on_event, on_error):
        self.folder, self.title, self.terms = folder, title, terms
        self.submit, self.on_event, self.on_error = submit, on_event, on_error
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
        seg = Segmenter()
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
            if text:
                self._prev_text = text
                ts = fmt_ts(start_s)
                self._md.write(f"[{ts}] {text}\n\n"); self._md.flush(); os.fsync(self._md.fileno())
                self.on_event({"type": "segment", "ts": ts, "text": text})
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
    """Owns the current session, the event log the plugin reads, and the local HTTP API."""

    def __init__(self, store, get_engine, submit, base_dir: Path, notify):
        self.store, self.get_engine, self.submit, self.base_dir, self.notify = store, get_engine, submit, base_dir, notify
        self.session: LectureSession | None = None
        self.session_id = ""
        self.events: list[dict] = []
        self._ev_lock = threading.Lock()
        self.token = store.get("lecture_token") or secrets.token_urlsafe(16)
        store.set("lecture_token", self.token)
        self.port = int(store.get("lecture_port") or 47821)
        self.httpd = None

    # ------------------------------------------------------------------ session
    @property
    def active(self) -> bool:
        return bool(self.session and self.session.running)

    def start(self, title: str = "", terms: list[str] | None = None, base_dict=None) -> dict:
        if self.active:
            return self.status()
        engine = self.get_engine()
        if engine is None:
            raise RuntimeError("המודל עדיין נטען")
        title = (title or "").strip() or f"הרצאה {datetime.now():%d.%m.%Y %H:%M}"
        terms = terms if terms is not None else (self.store.get("lecture_terms") or [])
        safe = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60]
        folder = self.base_dir / f"{datetime.now():%Y-%m-%d_%H%M} {safe}"
        s = LectureSession(folder, title, terms, self.submit, base_dict or {}, self._add_event, self._on_error)
        s.engine = engine
        with self._ev_lock:
            self.events = []
            self.session_id = secrets.token_hex(4)
        s.start()
        self.session = s
        self._add_event({"type": "title", "ts": "00:00", "text": title})
        self.notify("start", folder)
        return self.status()

    def stop(self) -> dict:
        if self.session:
            self.session.stop()
            self.notify("stop", self.session.folder)
        return self.status()

    def mark(self):
        if self.active:
            self.session.mark()

    def set_terms(self, terms: list[str]):
        terms = [t.strip() for t in terms if t.strip()]
        self.store.set("lecture_terms", terms)
        if self.session:
            self.session.set_terms(terms)

    def _add_event(self, ev: dict):
        with self._ev_lock:
            ev["id"] = len(self.events) + 1
            self.events.append(ev)

    def _on_error(self, msg):
        self._add_event({"type": "info", "ts": fmt_ts(self.session.elapsed if self.session else 0),
                         "text": f"שגיאה בתמלול קטע: {msg[:120]}"})

    def status(self) -> dict:
        s = self.session
        return {"state": "recording" if self.active else "idle", "session": self.session_id,
                "elapsed": fmt_ts(s.elapsed) if self.active else "", "title": s.title if s else "",
                "folder": str(s.folder) if s else "", "terms": self.store.get("lecture_terms") or [],
                "next": len(self.events)}

    # ------------------------------------------------------------------ HTTP API
    def serve(self):
        ctl = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep transcripts out of any console/log
                pass

            def _send(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                q = parse_qs(urlparse(self.path).query)
                if q.get("t", [""])[0] != ctl.token:
                    self._send(403, {"error": "bad token"}); return None
                return q

            def do_GET(self):
                q = self._auth()
                if q is None:
                    return
                path = urlparse(self.path).path
                if path == "/status":
                    return self._send(200, ctl.status())
                if path == "/events":
                    since = int(q.get("since", ["0"])[0])
                    with ctl._ev_lock:
                        evs = [e for e in ctl.events if e["id"] > since]
                    return self._send(200, {"events": evs, **ctl.status()})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                q = self._auth()
                if q is None:
                    return
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                except ValueError:
                    body = {}
                path = urlparse(self.path).path
                try:
                    if path == "/start":
                        if "terms" in body:
                            ctl.set_terms(body.get("terms") or [])
                        return self._send(200, ctl.start_from_plugin(body.get("title", "")))
                    if path == "/stop":
                        return self._send(200, ctl.stop())
                    if path == "/mark":
                        ctl.mark(); return self._send(200, ctl.status())
                    if path == "/terms":
                        ctl.set_terms(body.get("terms") or []); return self._send(200, ctl.status())
                except Exception as e:
                    return self._send(500, {"error": str(e)})
                self._send(404, {"error": "not found"})

        for port in (self.port, self.port + 1, self.port + 2, 0):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
                break
            except OSError:
                continue
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="lecture-api").start()
        log.info("lecture API on 127.0.0.1:%d", self.port)
        return self.port

    # set by the app: starting must happen on the Qt side (dictionary, notifications)
    start_from_plugin = start
