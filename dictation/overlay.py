"""Small recording pill near the bottom of the active screen.

It can never take focus or intercept clicks: Qt.WindowDoesNotAcceptFocus + TransparentForInput, plus
WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW set natively, shown without activation.
"""
from __future__ import annotations

import ctypes
import sys
from collections import deque

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

MAC = sys.platform == "darwin"
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_LAYERED = 0x08000000, 0x20, 0x80, 0x8, 0x80000
if not MAC:
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]

COLORS = {"recording": QColor("#ff4d4f"), "processing": QColor("#f5a623"), "error": QColor("#ff4d4f"),
          "info": QColor("#52c41a"), "streaming": QColor("#ff4d4f"), "connecting": QColor("#8a8f98")}


class Overlay(QWidget):
    W, H = 240, 46

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        if MAC:
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)  # tool windows hide when the app is inactive
        self.resize(self.W, self.H)
        self.state, self.text = "idle", ""
        self.levels = deque([0.0] * 28, maxlen=28)
        self._level = 0.0
        self._tick = QTimer(self, interval=33, timeout=self._frame)
        self._hide = QTimer(self, singleShot=True, timeout=self._hide_now)
        self.enabled = True

    def _native(self):
        if MAC:
            return  # Qt flags + WA_MacAlwaysShowToolWindow are enough; app is menu-bar only (never active)
        hwnd = int(self.winId())
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED)

    W_STREAM = 600

    def _place(self):
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.bottom() - self.H - 24)

    def set_level(self, lvl: float):  # called from audio thread; just stores a float
        self._level = max(self._level * 0.6, lvl)

    def show_state(self, state: str, text: str = "", hide_after_ms: int = 0):
        self.state, self.text = state, text
        if state == "idle":
            self._hide_now(); return
        if not self.enabled and state not in ("error",):
            return
        w = self.W_STREAM if (state == "streaming" and text) else self.W
        if w != self.width():
            self.resize(w, self.H)
            if self.isVisible():
                self._place()
        if not self.isVisible():
            self._place()
            self.show(); self._native()
        self._tick.start()
        self._hide.stop()
        if hide_after_ms:
            self._hide.start(hide_after_ms)
        self.update()

    def _hide_now(self):
        self._tick.stop(); self.hide(); self.state = "idle"
        self.levels.extend([0.0] * self.levels.maxlen)

    def _frame(self):
        if self.state in ("recording", "streaming"):
            self.levels.append(self._level); self._level *= 0.5
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        r = QRectF(1, 1, W - 2, self.H - 2)
        path = QPainterPath(); path.addRoundedRect(r, self.H / 2, self.H / 2)
        p.fillPath(path, QColor(22, 22, 26, 235))
        c = COLORS.get(self.state, QColor("#888"))
        # status dot (pulses while processing)
        p.setPen(Qt.NoPen); p.setBrush(c)
        p.drawEllipse(QRectF(r.right() - 30, r.center().y() - 6, 12, 12))
        if self.state in ("recording", "streaming"):
            bw, gap, x0 = 4, 2.5, r.right() - 44
            p.setBrush(QColor(235, 235, 240))
            for i, lv in enumerate(reversed(self.levels)):
                h = 4 + lv * (self.H - 18)
                x = x0 - i * (bw + gap)
                if x < r.left() + 16:
                    break
                p.drawRoundedRect(QRectF(x, r.center().y() - h / 2, bw, h), 2, 2)
            if self.text:  # streaming preview: short level meter on the right, newest text visible (elided left)
                p.fillRect(QRectF(r.left() + 14, 4, W - 150, self.H - 8), QColor(22, 22, 26, 255))
                self._draw_text(p, QRectF(r.left() + 18, 0, W - 160, self.H), self.text, QColor(200, 200, 210))
        else:
            self._draw_text(p, r.adjusted(16, 0, -44, 0), self.text, QColor(235, 235, 240))

    def _draw_text(self, p, rect, text, color):
        f = QFont("Segoe UI", 10); p.setFont(f); p.setPen(color)
        fm = p.fontMetrics()
        t = fm.elidedText(text, Qt.ElideLeft, int(rect.width()))
        p.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, t)
