"""Local Dictation — tray app and controller.

Flow: hotkey down → mic opens → hotkey up → VAD trims → Whisper (local) → cleanup + dictionary
      → password-field check → paste at caret (focus never changes, Enter never pressed).
Any failure after recording keeps the text (tray: "Copy last transcription") and the audio (tray: "Retry").
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QHeaderView, QMenu, QPushButton,
                               QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QLabel)

from . import context, inject, platform_util
from .platform_util import autostart_enabled, set_autostart
from .asr import StreamingPreview, make_engine
from .audio import Recorder
from .hotkey import DIALOG_HINT, KEY_LABELS, MODIFIERS, VK_NAMES, HotkeyHook, label as hotkey_label, spec_from_combo
from .overlay import Overlay
from .postprocess import Dictionary, process
from .storage import APP_DIR, Store

log = logging.getLogger("dictation")
MODES = {"verbatim": "Verbatim — נאמן לדיבור", "clean": "Clean — נקי וקריא", "professional": "Professional — מקצועי",
         "developer": "Developer — מונחים טכניים"}
HE = {"ready": "מוכן", "loading": "טוען מודל…", "rec": "מקליט", "proc": "מעבד…"}


def tray_icon(color: str) -> QIcon:
    pm = QPixmap(64, 64); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing); p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(22, 6, 20, 34, 10, 10)                   # mic capsule
    p.setBrush(Qt.NoBrush); pen = p.pen(); pen.setColor(QColor(color)); pen.setWidth(5); p.setPen(pen)
    p.drawArc(14, 14, 36, 36, 200 * 16, 140 * 16)              # holder
    p.drawLine(32, 50, 32, 58); p.drawLine(22, 58, 42, 58)
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    down = Signal(); up = Signal(); other = Signal(); escape = Signal()
    done = Signal(object); failed = Signal(str, object); device_lost = Signal(); loaded = Signal(str)
    inject_failed = Signal(str); partial = Signal(str); key = Signal(int, bool)
    first_audio = Signal(float); mic_error = Signal(str); lecture = Signal(str, str)


class HotkeyDialog(QDialog):
    """Press the key(s) you want; shows what Windows actually receives (special keys only, never letters)."""

    def __init__(self, app: "App"):
        super().__init__()
        self.app, self.held, self.peak, self.spec = app, set(), set(), None
        self.setWindowTitle("בחירת מקש קיצור"); self.setLayoutDirection(Qt.RightToLeft); self.resize(460, 220)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("לחץ על המקש (או צירוף המקשים) שתרצה להחזיק בזמן ההכתבה, ושחרר.\n" + DIALOG_HINT))
        self.show_lbl = QLabel("— ממתין ללחיצה —"); f = self.show_lbl.font(); f.setPointSize(16); f.setBold(True)
        self.show_lbl.setFont(f); self.show_lbl.setAlignment(Qt.AlignCenter); lay.addWidget(self.show_lbl)
        self.cur = QLabel(f"המקש הנוכחי: {hotkey_label(app.store.get('hotkey'))}"); lay.addWidget(self.cur)
        b = QHBoxLayout(); lay.addLayout(b)
        self.ok = QPushButton("השתמש במקש הזה"); self.ok.setEnabled(False); self.ok.clicked.connect(self._use); b.addWidget(self.ok)
        c = QPushButton("ביטול"); c.clicked.connect(self.reject); b.addWidget(c)
        app.b.key.connect(self._on_key)
        app.hook.capture = lambda vk, down: app.b.key.emit(vk, down)
        self.finished.connect(self._done)

    def _on_key(self, vk, down):
        if vk not in VK_NAMES:  # letters/digits/etc. are ignored (not a valid hotkey, and never shown)
            return
        if down:
            self.held.add(vk); self.peak |= self.held
        else:
            self.held.discard(vk)
            if not self.held and self.peak:
                self.spec = spec_from_combo(sorted(self.peak)); self.peak = set()
                self.show_lbl.setText(hotkey_label(self.spec)); self.ok.setEnabled(True)

    def _use(self):
        self.app.set_hotkey(self.spec); self.accept()

    def _done(self, _):
        self.app.hook.capture = None
        try:
            self.app.b.key.disconnect(self._on_key)
        except Exception:
            pass


class DictionaryDialog(QDialog):
    def __init__(self, store: Store, on_saved):
        super().__init__()
        self.store, self.on_saved = store, on_saved
        self.setWindowTitle("מילון אישי"); self.setLayoutDirection(Qt.RightToLeft); self.resize(640, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("מונח (כפי שייכתב) והגיות חלופיות, מופרדות בפסיק. התחיליות ב/ל/ה/ו/מ/ש/כ מזוהות אוטומטית."))
        self.t = QTableWidget(0, 2); self.t.setHorizontalHeaderLabels(["מונח", "הגיות / שגיאות נפוצות"])
        self.t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(self.t)
        for term, vs in store.dictionary().items():
            self._row(term, ", ".join(vs))
        b = QHBoxLayout(); lay.addLayout(b)
        for txt, fn in (("הוסף", lambda: self._row("", "")), ("מחק שורה", self._del), ("שמור", self._save)):
            btn = QPushButton(txt); btn.clicked.connect(fn); b.addWidget(btn)

    def _row(self, a, b):
        r = self.t.rowCount(); self.t.insertRow(r)
        self.t.setItem(r, 0, QTableWidgetItem(a)); self.t.setItem(r, 1, QTableWidgetItem(b))

    def _del(self):
        for r in sorted({i.row() for i in self.t.selectedIndexes()}, reverse=True):
            self.t.removeRow(r)

    def _save(self):
        keep = {}
        for r in range(self.t.rowCount()):
            term = (self.t.item(r, 0).text() if self.t.item(r, 0) else "").strip()
            if term:
                vs = [v.strip() for v in (self.t.item(r, 1).text() if self.t.item(r, 1) else "").split(",") if v.strip()]
                keep[term] = vs
        for old in self.store.dictionary():
            if old not in keep:
                self.store.delete_term(old)
        for t, vs in keep.items():
            self.store.set_term(t, vs)
        self.on_saved(); self.accept()


class App:
    def __init__(self):
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        platform_util.prepare_app()
        self.store = Store()
        self.dict = Dictionary(self.store.dictionary())
        self.b = Bridge()
        self.overlay = Overlay(); self.overlay.enabled = self.store.get("overlay")
        self.preview = StreamingPreview(on_partial=self.b.partial.emit)
        self.preview_active = False
        self.rec = Recorder(on_level=self.overlay.set_level, on_device_lost=self.b.device_lost.emit,
                            on_chunk=lambda c: self.preview_active and self.preview.feed(c),
                            on_first_audio=self.b.first_audio.emit, on_open_error=lambda e: self.b.mic_error.emit(str(e)))
        self.rec.warm_s = float(self.store.get("mic_warm_s") or 0)
        self.engine = None
        self.llm = None  # LLMPolisher, loaded only when enabled
        self.model_ready = threading.Event()
        self.asr_pool = ThreadPoolExecutor(1, thread_name_prefix="asr")
        self.inject_pool = ThreadPoolExecutor(1, thread_name_prefix="inject")
        self.recording = False
        self.latched = False  # hands-free recording started by a short tap
        self.rec_started = 0.0
        self.pending = 0
        self.last_text = ""
        self.last_audio: np.ndarray | None = None
        self._last_wall = time.time()

        for sig, fn in ((self.b.down, self.on_down), (self.b.up, self.on_up), (self.b.other, self.on_other),
                        (self.b.escape, self.on_escape), (self.b.done, self.on_done), (self.b.failed, self.on_failed),
                        (self.b.device_lost, self.on_device_lost), (self.b.loaded, self.on_loaded),
                        (self.b.inject_failed, self.on_inject_failed), (self.b.partial, self.on_partial),
                        (self.b.first_audio, self.on_first_audio), (self.b.mic_error, self.on_mic_error),
                        (self.b.lecture, self.on_lecture)):
            sig.connect(fn)
        self.hook = HotkeyHook(self.store.get("hotkey"), self.b.down.emit, self.b.up.emit, self.b.other.emit, self.b.escape.emit)

        self.tray = QSystemTrayIcon(tray_icon("#9aa0a6"))
        self.tray.setToolTip("Local Dictation — " + HE["loading"])
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.copy_last())
        self._build_menu()
        self.tray.show()

        self.watchdog = QTimer(interval=5000, timeout=self._watch); self.watchdog.start()
        self._init_lecture()
        threading.Thread(target=self._load_model, daemon=True).start()
        threading.Thread(target=context.warm_uia, daemon=True).start()
        log.info("hotkey hook installed: %s (%s)", self.store.get("hotkey"), self.store.get("activation"))
        missing = context.missing_permissions()
        if missing:  # macOS: Accessibility / Input Monitoring / Microphone
            log.warning("missing permissions: %s", ", ".join(missing))
            self.tray.showMessage("Local Dictation — נדרשות הרשאות",
                                  "אשר ב-System Settings → Privacy & Security: " + ", ".join(missing) +
                                  " — ואז הפעל מחדש את האפליקציה.", QSystemTrayIcon.Warning, 15000)
        if self.store.get("live_preview"):
            threading.Thread(target=self._load_preview, daemon=True).start()
        if self.store.get("llm")["enabled"]:
            threading.Thread(target=self._load_llm, daemon=True).start()
        self.hook.start()

    # ------------------------------------------------------------------ menu
    def _build_menu(self):
        m = QMenu(); m.setLayoutDirection(Qt.RightToLeft)
        self.status_action = m.addAction(HE["loading"]); self.status_action.setEnabled(False)
        m.addSeparator()
        m.addAction("העתק תמלול אחרון", self.copy_last)
        self.retry_action = m.addAction("נסה שוב לתמלל את ההקלטה האחרונה", self.retry); self.retry_action.setEnabled(False)
        m.addSeparator()

        act = m.addMenu("הפעלה")
        g = QActionGroup(act)
        for key, label in (("ptt", "לחיצה ממושכת (Push-to-Talk)"), ("toggle", "לחיצה להתחלה / לחיצה לסיום")):
            a = QAction(label, act, checkable=True, checked=self.store.get("activation") == key)
            a.triggered.connect(lambda _=False, k=key: self.store.set("activation", k)); g.addAction(a); act.addAction(a)
        act.addSeparator()
        a = QAction("תצוגה חיה בזמן הדיבור (Streaming)", act, checkable=True, checked=self.store.get("live_preview"))
        a.toggled.connect(self.set_live_preview); act.addAction(a)

        hk = m.addMenu("מקש קיצור")
        hk.addAction("בחר מקש קיצור… (לחיצה על המקש)", self.pick_hotkey)
        hk.addSeparator()
        g = QActionGroup(hk)
        presets = dict(KEY_LABELS, f23="Copilot (F23)")
        cur = self.store.get("hotkey")
        if cur not in presets:
            presets[cur] = hotkey_label(cur)
        for key, label in presets.items():
            a = QAction(label, hk, checkable=True, checked=cur == key)
            a.triggered.connect(lambda _=False, k=key: self.set_hotkey(k)); g.addAction(a); hk.addAction(a)
        self.hotkey_menu = hk

        md = m.addMenu("מצב עיבוד")
        a = QAction("אוטומטי לפי האפליקציה", md, checkable=True, checked=self.store.get("auto_profile"))
        a.triggered.connect(lambda c: self.store.set("auto_profile", c)); md.addAction(a); md.addSeparator()
        g = QActionGroup(md)
        for key, label in MODES.items():
            a = QAction(label, md, checkable=True, checked=self.store.get("mode") == key)
            a.triggered.connect(lambda _=False, k=key: self.store.set("mode", k)); g.addAction(a); md.addAction(a)

        a = md.addAction("שיפור טקסט עם AI מקומי (Professional + הכתבות ארוכות)")
        a.setCheckable(True); a.setChecked(self.store.get("llm")["enabled"]); a.toggled.connect(self.set_llm)
        m.addAction("מילון אישי…", self.open_dictionary)
        m.addAction("📝 עורך הרצאות (תמלול חי)…", self.open_editor)
        m.addAction("פתח את תיקיית ההרצאות", self.open_lectures_folder)
        m.addSeparator()
        a = m.addAction("הצג חיווי הקלטה (Overlay)"); a.setCheckable(True); a.setChecked(self.store.get("overlay"))
        a.toggled.connect(self.set_overlay)
        a = m.addAction("שמור היסטוריית תמלולים"); a.setCheckable(True); a.setChecked(self.store.get("history"))
        a.toggled.connect(lambda c: self.store.set("history", c))
        m.addAction("מחק את כל ההיסטוריה", self.store.clear_history)
        a = m.addAction("השאר מיקרופון פתוח 30 שניות אחרי הכתבה (התחלה מיידית)"); a.setCheckable(True)
        a.setChecked(bool(self.store.get("mic_warm_s"))); a.toggled.connect(self.set_mic_warm)
        a = m.addAction("הקלדה ישירה במקום הדבקה"); a.setCheckable(True); a.setChecked(self.store.get("inject_method") == "type")
        a.toggled.connect(lambda c: self.store.set("inject_method", "type" if c else "paste"))
        a = m.addAction("הפעל עם Windows"); a.setCheckable(True); a.setChecked(autostart_enabled())
        a.toggled.connect(set_autostart)
        m.addSeparator()
        m.addAction("יציאה", self.quit)
        self.menu = m
        self.tray.setContextMenu(m)

    def set_hotkey(self, k):
        self.store.set("hotkey", k); self.hook.set_spec(k); self.hook.restart()
        log.info("hotkey set to %s", k)
        status, retry = self.status_action.text(), self.retry_action.isEnabled()
        self._build_menu()  # refresh checked item / custom entry
        self.status_action.setText(status); self.retry_action.setEnabled(retry)
        self.tray.showMessage("Local Dictation", f"מקש ההכתבה: {hotkey_label(k)} — החזק, דבר ושחרר.",
                              QSystemTrayIcon.Information, 4000)

    def pick_hotkey(self):
        self._hk_dlg = HotkeyDialog(self)
        self._hk_dlg.show(); self._hk_dlg.raise_(); self._hk_dlg.activateWindow()

    def set_llm(self, on):
        cfg = self.store.get("llm"); cfg["enabled"] = on; self.store.set("llm", cfg)
        if on and self.llm is None:
            threading.Thread(target=self._load_llm, daemon=True).start()
        elif not on:
            self.llm = None  # frees ~3 GB of shared GPU memory

    def _load_llm(self):
        from .llm import LLMPolisher
        cfg = self.store.get("llm")
        try:
            p = LLMPolisher(cfg["model_dir"], cfg["device"], cfg["budget_s"], cfg.get("budget_per_word", 0.0))
            p.load()
            if self.store.get("llm")["enabled"]:
                self.llm = p
            log.info("llm loaded in %.1fs", p.load_s)
        except Exception:
            log.exception("llm failed to load (dictation continues without it)")

    def _should_polish(self, mode, text):
        cfg = self.store.get("llm")
        if not (cfg["enabled"] and self.llm):
            return False
        if mode == "professional":
            return cfg["professional"]
        return mode == "clean" and len(text.split()) >= cfg["clean_min_words"]

    def set_live_preview(self, on):
        self.store.set("live_preview", on)
        if on and not self.preview.ready:
            threading.Thread(target=self._load_preview, daemon=True).start()

    def _load_preview(self):
        try:
            t0 = time.perf_counter(); self.preview.load()
            log.info("streaming preview loaded in %.1fs", time.perf_counter() - t0)
        except Exception:
            log.exception("streaming preview failed to load")

    def on_partial(self, text):
        if self.recording and self.preview_active:
            self.overlay.show_state("streaming", process(text, "verbatim", self.dict))

    def set_overlay(self, on):
        self.store.set("overlay", on); self.overlay.enabled = on
        if not on:
            self.overlay.show_state("idle")

    def open_dictionary(self):
        self._dlg = DictionaryDialog(self.store, lambda: setattr(self, "dict", Dictionary(self.store.dictionary())))
        self._dlg.show(); self._dlg.raise_(); self._dlg.activateWindow()

    # ------------------------------------------------------------------ model
    def _load_model(self):
        err = None
        for key in ("asr_primary", "asr_fallback"):
            try:
                eng = make_engine(self.store.get(key))
                eng.load()
                self.engine = eng
                self.model_ready.set()
                log.info("engine %s loaded in %.1fs", eng.name, eng.load_s)
                self.b.loaded.emit(f"{HE['ready']} · {eng.name} · {eng.load_s:.1f}s")
                return
            except Exception as e:
                log.exception("engine %s failed", key)
                err = e
        self.b.loaded.emit(f"שגיאה בטעינת המודל: {err}")

    def on_loaded(self, msg):
        self.status_action.setText(msg); self.tray.setToolTip("Local Dictation — " + msg)
        self.tray.setIcon(tray_icon("#e8eaed" if self.model_ready.is_set() else "#ff4d4f"))
        if self.model_ready.is_set():
            self.tray.showMessage("Local Dictation מוכן",
                                  f"החזק {hotkey_label(self.store.get('hotkey'))}, דבר ושחרר. "
                                  "אין מקש כזה במקלדת? תפריט ← מקש קיצור ← בחר מקש קיצור…",
                                  QSystemTrayIcon.Information, 6000)
        else:
            self.tray.showMessage("Local Dictation", msg, QSystemTrayIcon.Warning, 8000)

    # ------------------------------------------------------------------ hotkey events
    # ------------------------------------------------------------------ lecture mode
    def _init_lecture(self):
        from PySide6.QtCore import QStandardPaths
        from pathlib import Path
        from .lecture import LectureController
        docs = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or Path.home())
        self.lectures_dir = docs / "Local Dictation - הרצאות"
        self.lecture = LectureController(self.store, lambda: self.engine if self.model_ready.is_set() else None,
                                         self.asr_pool.submit, self.lectures_dir,
                                         lambda kind, folder: self.b.lecture.emit(kind, str(folder)))
        self.editor = None

    def open_editor(self):
        if self.editor is None:
            from .editor import LectureEditor
            self.editor = LectureEditor(self.lecture, self.store, self.store.dictionary)
        self.editor.show(); self.editor.raise_(); self.editor.activateWindow()

    def lecture_mark(self):
        self.lecture.mark()
        self.overlay.show_state("info", "⭐ סומן כחשוב", 1000)

    def on_lecture(self, kind, folder):
        if kind == "start":
            if self.recording:
                self.cancel(silent=True)
            self.tray.setIcon(tray_icon("#ff4d4f"))
            self.tray.setToolTip("Local Dictation — מתמלל הרצאה")
        else:
            self._idle_icon()
            self.tray.setToolTip("Local Dictation — " + self.status_action.text())
            self.tray.showMessage("תמלול ההרצאה נעצר", f"השמע, התמלול והמסמך נשמרו ב:\n{folder}",
                                  QSystemTrayIcon.Information, 6000)

    def open_lectures_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        self.lectures_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.lectures_dir)))

    def on_down(self):
        if getattr(self, "lecture", None) is not None and self.lecture.active:
            self.lecture_mark(); return  # during a lecture the dictation key marks an important moment
        if self.recording and (self.store.get("activation") == "toggle" or self.latched):
            self.finish(); return
        if not self.recording:
            self.start()

    def on_up(self):
        if self.recording and self.store.get("activation") == "ptt" and not self.latched:
            if (time.time() - self.rec_started) * 1000 < self.store.get("min_hold_ms"):
                # Short tap = hands-free: keep recording until the next press (also makes keys that only
                # send a tap, like some Copilot keys, usable).
                self.latched = True
                self.overlay.show_state("recording", "")
                log.info("hands-free recording (tap); next press ends it")
            else:
                self.finish()

    def on_other(self):
        # Hotkey was part of a shortcut (e.g. Right Ctrl + C): drop the recording silently.
        if self.recording and self.store.get("activation") == "ptt" and self.hook.keys & MODIFIERS:
            self.cancel(silent=True)

    def on_escape(self):
        if self.recording:
            self.cancel()

    # ------------------------------------------------------------------ recording
    def _end_preview(self):
        self.preview_active = False
        self.preview.stop()

    def start(self):
        if self.store.get("live_preview") and self.preview.ready:
            self.preview.start(); self.preview_active = True
        self.rec.start()  # never blocks: the device opens in the background (~330 ms on this mic)
        self.recording, self.rec_started, self.latched = True, time.time(), False
        self.overlay.show_state("connecting", "מתחבר…")
        self.tray.setIcon(tray_icon("#ff4d4f"))

    def on_first_audio(self, ms):
        log.info("rec start: audio flowing after %.0f ms (device open %.0f ms, %s)", ms, self.rec.open_ms,
                 (self.rec.device_name or "")[:40])
        if self.recording:
            self.overlay.show_state("recording")  # red = speak now

    def on_mic_error(self, err):
        log.warning("mic open failed: %s", err)
        self.recording = False; self.latched = False; self._end_preview(); self._idle_icon()
        self.overlay.show_state("error", "אין גישה למיקרופון", 3000)

    def set_mic_warm(self, on):
        self.store.set("mic_warm_s", 30 if on else 0)
        self.rec.warm_s = 30.0 if on else 0.0
        if not on and not self.recording:
            self.rec.close()

    def cancel(self, silent=False):
        self.rec.stop(); self.recording = False; self.latched = False; self._end_preview()
        self.tray.setIcon(tray_icon("#e8eaed"))
        self.overlay.show_state("idle") if silent else self.overlay.show_state("info", "בוטל", 900)

    def finish(self):
        audio = self.rec.stop(); self.recording = False; self.latched = False; self._end_preview()
        t_stop = time.perf_counter()
        hwnd = context.foreground_hwnd()
        proc = context.process_name(hwnd)
        mode = context.mode_for(proc, self.store.get("profiles"), self.store.get("mode")) if self.store.get("auto_profile") \
            else self.store.get("mode")
        self.last_audio = audio
        self.pending += 1
        self.overlay.show_state("processing", HE["proc"] if self.model_ready.is_set() else HE["loading"])
        self.tray.setIcon(tray_icon("#f5a623"))
        self.asr_pool.submit(self._transcribe, audio, dict(hwnd=hwnd, proc=proc, mode=mode, t_stop=t_stop))

    def _transcribe(self, audio, meta):
        try:
            self.model_ready.wait()
            prompt = self.dict.prompt() if self.store.get("use_prompt") else None
            r = self.engine.transcribe(audio, prompt=prompt)
            text = process(r.text, meta["mode"], self.dict) if r.text else ""
            meta.update(raw=r.text, no_speech=r.no_speech, audio_s=r.audio_s, decode_s=r.decode_s, llm="")
            if text and self._should_polish(meta["mode"], text):
                try:
                    p = self.llm.polish(text, meta["mode"], list(self.dict._lookup.values()))
                    meta["llm"] = f"{'accepted' if p.accepted else 'rejected'} ({p.reason}) {p.gen_s:.2f}s"
                    text = p.text  # the input itself when rejected: the LLM can never make it worse
                except Exception as e:
                    log.warning("llm polish failed: %s", e)
            meta["text"] = text
            self.b.done.emit(meta)
        except Exception as e:
            log.exception("transcription failed")
            self.b.failed.emit(str(e), meta)

    def on_done(self, meta):
        self.pending -= 1
        latency = time.perf_counter() - meta["t_stop"]
        log.info("done app=%s mode=%s audio=%.1fs decode=%.2fs latency=%.2fs llm=%s", meta["proc"], meta["mode"],
                 meta["audio_s"], meta["decode_s"], latency, meta.get("llm") or "-")
        self._idle_icon()
        if meta["no_speech"] or not meta["text"]:
            self.overlay.show_state("info", "לא זוהה דיבור", 1200); return
        text = meta["text"]
        self.last_text = text; self.retry_action.setEnabled(False)
        self.store.add_history(app=meta["proc"], mode=meta["mode"], raw=meta["raw"], text=text,
                               audio_s=meta["audio_s"], latency_s=latency)
        now = context.foreground_hwnd()
        if not now or now != meta["hwnd"]:
            self._to_clipboard(text, "החלון השתנה — הטקסט הועתק ללוח"); return
        if context.focused_is_password():
            self.overlay.show_state("error", "שדה סיסמה — הטקסט לא הוכנס", 2500); return
        self.overlay.show_state("idle")
        # Space between consecutive dictations into the same field.
        self.inject_pool.submit(self._inject, text + " ")

    def _inject(self, text):
        try:
            inject.insert(text, self.store.get("inject_method"), self.store.get("restore_clipboard"))
        except Exception as e:
            log.warning("inject failed: %s", e)
            self.b.inject_failed.emit(text)

    def on_inject_failed(self, text):
        self._to_clipboard(text.strip(), "ההכנסה נכשלה — הטקסט הועתק ללוח")

    def _to_clipboard(self, text, msg):
        try:
            inject.set_clipboard_text(text)
            self.overlay.show_state("error", msg, 3000)
        except Exception:
            self.overlay.show_state("error", "הטקסט שמור — תפריט: העתק תמלול אחרון", 4000)

    def on_failed(self, err, meta):
        self.pending -= 1; self._idle_icon()
        self.retry_action.setEnabled(True)
        self.overlay.show_state("error", "התמלול נכשל — אפשר לנסות שוב מהתפריט", 4000)

    def on_device_lost(self):
        if self.recording:
            log.warning("input device lost during recording")
            self.finish()
            self.overlay.show_state("error", "המיקרופון נותק — מתמלל את מה שהוקלט", 2500)

    def retry(self):
        if self.last_audio is not None and len(self.last_audio):
            self.pending += 1
            hwnd = context.foreground_hwnd()
            self.overlay.show_state("processing", HE["proc"])
            self.asr_pool.submit(self._transcribe, self.last_audio, dict(hwnd=hwnd, proc=context.process_name(hwnd),
                                                                         mode=self.store.get("mode"), t_stop=time.perf_counter()))

    def copy_last(self):
        if self.last_text:
            inject.set_clipboard_text(self.last_text)
            self.overlay.show_state("info", "הועתק ללוח", 900)

    def _idle_icon(self):
        if getattr(self, "lecture", None) is not None and self.lecture.active:
            return  # stays red for the whole lecture
        if not self.recording and self.pending <= 0:
            self.tray.setIcon(tray_icon("#e8eaed"))

    # ------------------------------------------------------------------ robustness
    def _watch(self):
        now = time.time()
        if now - self._last_wall > 20:  # timer stalled → the machine slept; LL hooks may be gone
            log.info("resume detected; reinstalling hotkey hook")
            if self.recording:
                self.finish()
            self.hook.restart()
        self._last_wall = now
        if self.recording and now - self.rec_started > 600:  # 10 min safety cap (e.g. stuck key)
            self.finish()

    def quit(self):
        if getattr(self, "lecture", None) is not None and self.lecture.active:
            self.lecture.stop()
            time.sleep(1.0)  # let the audio file close cleanly
        self.hook.stop(); self.rec.close(); self.tray.hide(); self.qt.quit()

    def run(self):
        return self.qt.exec()


def main():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=APP_DIR / "app.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")  # never logs transcript text
    if not platform_util.single_instance():
        return 0
    platform_util.prepare_process()
    return App().run()
