"""Microphone capture.

Privacy: the stream is open only while recording (plus an optional, user-enabled "warm" window after a
dictation, during which samples are discarded, never stored). Windows' mic indicator reflects this exactly.

Latency: on this laptop the Intel SST digital mic needs ~330 ms to wake on every open (measured, both MME and
WASAPI). So start() never blocks: the stream opens on a background thread, capture is armed immediately,
and on_first_audio fires when samples actually flow — the overlay turns red only then.

The default input device is re-scanned on each open (~20 ms), so switching mics between dictations just works.
A device lost mid-recording ends the take gracefully: the audio captured so far is kept.
"""
from __future__ import annotations

import threading
import time

import numpy as np
import sounddevice as sd

SR = 16000


class Recorder:
    def __init__(self, on_level=None, on_device_lost=None, on_chunk=None, on_first_audio=None, on_open_error=None):
        self.on_level = on_level or (lambda lvl: None)
        self.on_device_lost = on_device_lost or (lambda: None)
        self.on_chunk = on_chunk                     # optional live consumer (streaming preview)
        self.on_first_audio = on_first_audio or (lambda ms: None)
        self.on_open_error = on_open_error or (lambda e: None)
        self.warm_s = 0.0                            # keep the stream open this long after a dictation (0 = off)
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.RLock()
        self._capturing = False
        self._closing_by_us = False
        self._opener: threading.Thread | None = None
        self._close_timer: threading.Timer | None = None
        self._t_arm = 0.0
        self._first_seen = False
        self.device_name = ""
        self.open_ms = 0.0

    @property
    def active(self):
        return self._capturing

    # ------------------------------------------------------------------ public
    def start(self):
        """Arm capture now; open the device in the background if it is not already open (warm)."""
        with self._lock:
            if self._capturing:
                return
            if self._close_timer:
                self._close_timer.cancel(); self._close_timer = None
            self._frames, self._first_seen, self._t_arm = [], False, time.perf_counter()
            self._capturing = True
            if self._stream is not None:      # warm: instant
                self.open_ms = 0.0
                return
            if self._opener is None or not self._opener.is_alive():
                self._opener = threading.Thread(target=self._open, daemon=True, name="mic-open")
                self._opener.start()

    def stop(self) -> np.ndarray:
        """Disarm and return the captured audio. Closes the device now, or after warm_s."""
        op = self._opener
        if op is not None and op.is_alive():
            op.join(2.0)                       # released before the mic finished opening
        with self._lock:
            self._capturing = False
            audio = np.concatenate(self._frames) if self._frames else np.zeros(0, np.float32)
            self._frames = []
            if self._stream is not None:
                if self.warm_s > 0:
                    self._close_timer = threading.Timer(self.warm_s, self._close)
                    self._close_timer.daemon = True
                    self._close_timer.start()
                else:
                    self._close()
            return audio

    def close(self):
        with self._lock:
            if self._close_timer:
                self._close_timer.cancel(); self._close_timer = None
            self._capturing = False
            self._close()

    # ------------------------------------------------------------------ internals
    def _open(self):
        t0 = time.perf_counter()
        try:
            sd._terminate(); sd._initialize()   # pick up newly plugged / changed default mic
            dev = sd.query_devices(kind="input")
            s = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=int(SR * 0.03),
                               callback=self._cb, finished_callback=self._finished)
            s.start()
        except Exception as e:
            with self._lock:
                self._capturing = False
            self.on_open_error(e)
            return
        with self._lock:
            self.device_name = dev["name"]
            self.open_ms = (time.perf_counter() - t0) * 1000
            self._stream = s
            if not self._capturing and self.warm_s <= 0:  # released before open finished
                self._close()

    def _close(self):
        s, self._stream = self._stream, None
        if s is not None:
            self._closing_by_us = True
            try:
                s.stop(); s.close()
            except Exception:
                pass
            finally:
                self._closing_by_us = False

    def _cb(self, indata, frames, t, status):
        if not self._capturing:
            return                              # warm window: samples are dropped, never stored
        chunk = indata[:, 0].copy()
        self._frames.append(chunk)
        if not self._first_seen:
            self._first_seen = True
            self.on_first_audio((time.perf_counter() - self._t_arm) * 1000)
        if self.on_chunk:
            self.on_chunk(chunk)
        rms = float(np.sqrt(np.mean(chunk ** 2)) + 1e-9)
        self.on_level(min(1.0, max(0.0, (20 * np.log10(rms) + 55) / 45)))  # ~-55dB..-10dB -> 0..1

    def _finished(self):
        # The stream stopped without us asking: the device went away.
        if not self._closing_by_us:
            self._stream = None
            if self._capturing:
                self.on_device_lost()
