"""Lecture editor: a small word processor (ONLYOFFICE-inspired look, right-to-left first) that receives the live
lecture transcription while you keep editing.

- New paragraphs are appended at the END through a separate cursor: your caret and selection never move.
  The view follows new text only while you are already at the bottom.
- Formatting: bold / italic / underline / highlight, headings, bullets, alignment, paragraph direction (RTL/LTR),
  font size, undo/redo.
- Autosave every 20 s (editor.html in the lecture folder), Word export (.docx) with proper RTL paragraphs.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QKeySequence, QTextBlockFormat, QTextCharFormat,
                           QTextCursor, QTextListFormat, QTextOption)
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

log = logging.getLogger("dictation.editor")

# ONLYOFFICE-like palette
HEADER = "#446995"
TOOLBAR = "#f7f7f7"
BORDER = "#cbcbcb"
CANVAS = "#e2e2e2"
GRAY_TS = QColor(150, 150, 150)
HIGHLIGHT = QColor("#fff176")

STYLE = f"""
QMainWindow {{ background: {CANVAS}; }}
#header {{ background: {HEADER}; }}
#header QLabel {{ color: white; font-size: 13px; }}
#recdot {{ color: #ff6b6b; font-size: 14px; }}
#toolbar {{ background: {TOOLBAR}; border-bottom: 1px solid {BORDER}; }}
#toolbar QToolButton {{ border: 1px solid transparent; border-radius: 2px; padding: 3px 6px; min-width: 22px;
                       font-size: 13px; color: #333; background: transparent; }}
#toolbar QToolButton:hover {{ background: #e0e0e0; border-color: #d0d0d0; }}
#toolbar QToolButton:checked {{ background: #cbcbcb; border-color: #bdbdbd; }}
#toolbar QComboBox {{ min-width: 52px; padding: 1px 4px; }}
#sep {{ background: {BORDER}; }}
#side {{ background: {TOOLBAR}; border-left: 1px solid {BORDER}; }}
#side QLabel {{ color: #555; }}
#page {{ background: white; border: 1px solid #bebebe; }}
QPushButton#rec {{ background: #d9534f; color: white; border: 1px solid #c9302c; border-radius: 3px; padding: 6px; }}
QPushButton#rec:disabled {{ background: #e9a5a3; border-color: #e9a5a3; }}
QPushButton#mark {{ background: #fff3a0; border: 1px solid #e0c800; border-radius: 3px; padding: 6px; }}
QPushButton {{ padding: 6px; }}
"""


class _Bridge(QObject):
    event = Signal(dict)


class LectureEditor(QMainWindow):
    def __init__(self, controller, store, get_dictionary):
        super().__init__()
        self.ctl, self.store, self.get_dictionary = controller, store, get_dictionary
        self.setWindowTitle("תמלול הרצאה — Local Dictation")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1180, 820)
        self.setStyleSheet(STYLE)
        self.folder: Path | None = None
        self._dirty = False
        self._started = 0.0

        self.bridge = _Bridge()
        self.bridge.event.connect(self._on_event)
        controller.listeners.append(self.bridge.event.emit)  # worker threads → Qt thread

        root = QWidget(); self.setCentralWidget(root)
        v = QVBoxLayout(root); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(self._header())
        v.addWidget(self._toolbar())
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        v.addLayout(body, 1)
        body.addWidget(self._side())
        body.addWidget(self._canvas(), 1)

        self.autosave = QTimer(self, interval=20000, timeout=self.save_draft); self.autosave.start()
        self.clock = QTimer(self, interval=1000, timeout=self._tick)
        self._sync_state()

    # ------------------------------------------------------------------ layout
    def _header(self):
        w = QWidget(objectName="header"); w.setFixedHeight(34)
        h = QHBoxLayout(w); h.setContentsMargins(12, 0, 12, 0)
        self.title_lbl = QLabel("מסמך חדש")
        f = self.title_lbl.font(); f.setBold(True); self.title_lbl.setFont(f)
        h.addWidget(self.title_lbl); h.addStretch(1)
        self.rec_dot = QLabel("●", objectName="recdot"); self.rec_dot.hide()
        self.clock_lbl = QLabel("")
        h.addWidget(self.rec_dot); h.addWidget(self.clock_lbl)
        return w

    def _btn(self, bar, text, tip, slot, checkable=False, shortcut=None, font=None):
        b = QToolButton(); b.setText(text); b.setToolTip(tip + (f"  ({shortcut})" if shortcut else ""))
        b.setCheckable(checkable)
        if font:
            b.setFont(font)
        b.clicked.connect(slot)
        if shortcut:
            a = QAction(self); a.setShortcut(QKeySequence(shortcut)); a.triggered.connect(slot); self.addAction(a)
        bar.addWidget(b)
        return b

    def _sep(self, bar):
        s = QFrame(objectName="sep"); s.setFixedSize(1, 20); bar.addSpacing(4); bar.addWidget(s); bar.addSpacing(4)

    def _toolbar(self):
        w = QWidget(objectName="toolbar"); w.setFixedHeight(40)
        bar = QHBoxLayout(w); bar.setContentsMargins(8, 4, 8, 4); bar.setSpacing(2)
        self._btn(bar, "💾", "שמור כקובץ Word", self.export_docx, shortcut="Ctrl+S")
        self._btn(bar, "↶", "בטל", lambda: self.edit.undo(), shortcut=None)
        self._btn(bar, "↷", "בצע שוב", lambda: self.edit.redo(), shortcut=None)
        self._sep(bar)
        self.size_box = QComboBox(); self.size_box.addItems([str(s) for s in (9, 10, 11, 12, 14, 16, 18, 24, 32)])
        self.size_box.setCurrentText("12"); self.size_box.setToolTip("גודל גופן")
        self.size_box.textActivated.connect(lambda t: self._merge(size=float(t))); bar.addWidget(self.size_box)
        bf = QFont(); bf.setBold(True)
        itf = QFont(); itf.setItalic(True)
        uf = QFont(); uf.setUnderline(True)
        self.b_bold = self._btn(bar, "B", "מודגש", lambda: self._merge(bold=self.b_bold.isChecked()), True, "Ctrl+B", bf)
        self.b_ital = self._btn(bar, "I", "נטוי", lambda: self._merge(italic=self.b_ital.isChecked()), True, "Ctrl+I", itf)
        self.b_under = self._btn(bar, "U", "קו תחתון", lambda: self._merge(underline=self.b_under.isChecked()), True, "Ctrl+U", uf)
        self._btn(bar, "🖍", "הדגשה בצהוב", self._toggle_highlight)
        self._sep(bar)
        self._btn(bar, "H1", "כותרת ראשית", lambda: self._heading(1))
        self._btn(bar, "H2", "כותרת משנה", lambda: self._heading(2))
        self._btn(bar, "¶", "טקסט רגיל", lambda: self._heading(0))
        self._btn(bar, "•", "רשימת תבליטים", self._bullets)
        self._sep(bar)
        self._btn(bar, "⇥", "יישור לימין", lambda: self._align(Qt.AlignRight | Qt.AlignAbsolute))
        self._btn(bar, "↔", "מרכוז", lambda: self._align(Qt.AlignHCenter))
        self._btn(bar, "⇤", "יישור לשמאל", lambda: self._align(Qt.AlignLeft | Qt.AlignAbsolute))
        self._btn(bar, "¶◂", "פסקה מימין לשמאל (עברית)", lambda: self._direction(Qt.RightToLeft))
        self._btn(bar, "▸¶", "פסקה משמאל לימין (אנגלית)", lambda: self._direction(Qt.LeftToRight))
        bar.addStretch(1)
        return w

    def _side(self):
        w = QWidget(objectName="side"); w.setFixedWidth(250)
        v = QVBoxLayout(w); v.setContentsMargins(12, 12, 12, 12); v.setSpacing(6)
        v.addWidget(QLabel("שם ההרצאה"))
        self.title_in = QLineEdit(); self.title_in.setPlaceholderText("למשל: אלגוריתמים – שיעור 5")
        v.addWidget(self.title_in)
        v.addWidget(QLabel("מונחים בהרצאה (מופרדים בפסיק)"))
        self.terms_in = QPlainTextEdit(); self.terms_in.setFixedHeight(90)
        self.terms_in.setPlaceholderText("דייקסטרה, BFS, גרף מכוון, פרופ' כהן")
        self.terms_in.setPlainText(", ".join(self.store.get("lecture_terms") or []))
        v.addWidget(self.terms_in)
        self.start_btn = QPushButton("●  התחל הקלטה", objectName="rec"); self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("■  עצור"); self.stop_btn.clicked.connect(self.stop)
        self.mark_btn = QPushButton("⭐  רגע חשוב", objectName="mark"); self.mark_btn.clicked.connect(self.ctl.mark)
        for b in (self.start_btn, self.stop_btn, self.mark_btn):
            v.addWidget(b)
        self.status = QLabel(""); self.status.setWordWrap(True); v.addWidget(self.status)
        v.addStretch(1)
        hint = QLabel("הטקסט נוסף בסוף המסמך — אפשר לערוך ולהוסיף הערות בכל מקום בזמן ההקלטה.\n"
                      "מקש ההכתבה מסמן רגע חשוב.\nהשמע, התמלול והמסמך נשמרים אוטומטית.")
        hint.setWordWrap(True); hint.setStyleSheet("color:#888; font-size:11px;"); v.addWidget(hint)
        folder_btn = QPushButton("פתח תיקיית הרצאה"); folder_btn.clicked.connect(self._open_folder); v.addWidget(folder_btn)
        return w

    def _canvas(self):
        outer = QWidget(objectName="canvas")
        outer.setStyleSheet(f"QWidget#canvas {{ background:{CANVAS}; }}")  # scoped: must not leak into the page
        h = QHBoxLayout(outer); h.setContentsMargins(24, 18, 24, 0)
        h.addStretch(1)
        self.edit = QTextEdit(objectName="page")
        self.edit.setMaximumWidth(860); self.edit.setMinimumWidth(480)
        self.edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.edit.setFrameShape(QFrame.NoFrame)
        doc = self.edit.document()
        doc.setDocumentMargin(56)
        opt = QTextOption(doc.defaultTextOption()); opt.setTextDirection(Qt.RightToLeft); doc.setDefaultTextOption(opt)
        f = QFont("David" if self._has_font("David") else "Arial", 12); doc.setDefaultFont(f)
        self.edit.setLayoutDirection(Qt.RightToLeft)
        self.edit.textChanged.connect(lambda: setattr(self, "_dirty", True))
        self.edit.cursorPositionChanged.connect(self._sync_format_buttons)
        h.addWidget(self.edit, 6)
        h.addStretch(1)
        return outer

    @staticmethod
    def _has_font(name):
        from PySide6.QtGui import QFontDatabase
        return name in QFontDatabase.families()

    # ------------------------------------------------------------------ formatting
    def _merge(self, bold=None, italic=None, underline=None, size=None, background=None):
        fmt = QTextCharFormat()
        if bold is not None:
            fmt.setFontWeight(QFont.Bold if bold else QFont.Normal)
        if italic is not None:
            fmt.setFontItalic(italic)
        if underline is not None:
            fmt.setFontUnderline(underline)
        if size is not None:
            fmt.setFontPointSize(size)
        if background is not None:
            fmt.setBackground(background)
        c = self.edit.textCursor()
        if not c.hasSelection():
            c.select(QTextCursor.WordUnderCursor)
        c.mergeCharFormat(fmt); self.edit.mergeCurrentCharFormat(fmt)
        self.edit.setFocus()

    def _toggle_highlight(self):
        cur = self.edit.textCursor().charFormat().background()
        on = cur.style() == Qt.NoBrush or cur.color() != HIGHLIGHT
        self._merge(background=QBrush(HIGHLIGHT) if on else QBrush(Qt.NoBrush))

    def _heading(self, level):
        c = self.edit.textCursor()
        bf = c.blockFormat(); bf.setHeadingLevel(level); c.setBlockFormat(bf)
        cf = QTextCharFormat()
        cf.setFontPointSize({1: 20, 2: 15}.get(level, 12)); cf.setFontWeight(QFont.Bold if level else QFont.Normal)
        c.select(QTextCursor.BlockUnderCursor); c.mergeCharFormat(cf)
        self.edit.setFocus()

    def _bullets(self):
        c = self.edit.textCursor()
        if c.currentList():
            lst = c.currentList(); bf = c.blockFormat()
            lst.remove(c.block()); bf.setIndent(0); c.setBlockFormat(bf)
        else:
            c.createList(QTextListFormat.ListDisc)
        self.edit.setFocus()

    def _align(self, a):
        self.edit.setAlignment(a); self.edit.setFocus()

    def _direction(self, d):
        c = self.edit.textCursor(); bf = c.blockFormat(); bf.setLayoutDirection(d)
        bf.setAlignment((Qt.AlignRight if d == Qt.RightToLeft else Qt.AlignLeft) | Qt.AlignAbsolute)
        c.setBlockFormat(bf); self.edit.setFocus()

    def _sync_format_buttons(self):
        f = self.edit.currentCharFormat()
        self.b_bold.setChecked(f.fontWeight() >= QFont.Bold)
        self.b_ital.setChecked(f.fontItalic()); self.b_under.setChecked(f.fontUnderline())
        if f.fontPointSize() > 0:
            self.size_box.setCurrentText(str(int(f.fontPointSize())))

    # ------------------------------------------------------------------ live insertion
    def _append(self, parts: list[tuple[str, QTextCharFormat]], heading=0):
        """Append one paragraph at the end without touching the user's cursor/selection."""
        sb = self.edit.verticalScrollBar()
        follow = sb.value() >= sb.maximum() - 30          # only auto-scroll if already at the bottom
        c = QTextCursor(self.edit.document())
        c.movePosition(QTextCursor.End)
        c.beginEditBlock()
        if c.block().text().strip() or c.block().previous().isValid():
            c.insertBlock()
        bf = QTextBlockFormat(); bf.setLayoutDirection(Qt.RightToLeft); bf.setHeadingLevel(heading)
        bf.setAlignment(Qt.AlignRight | Qt.AlignAbsolute); bf.setBottomMargin(6)
        c.setBlockFormat(bf)
        for text, fmt in parts:
            c.insertText(text, fmt)
        c.endEditBlock()
        if follow:
            sb.setValue(sb.maximum())

    def _fmt(self, color=None, size=None, bold=False, bg=None):
        f = QTextCharFormat()
        f.setFontPointSize(size or 12)
        f.setFontWeight(QFont.Bold if bold else QFont.Normal)
        f.setForeground(QBrush(color) if color else QBrush(QColor("#222")))
        f.setBackground(QBrush(bg) if bg else QBrush(Qt.NoBrush))
        return f

    def _on_event(self, ev):
        t = ev.get("type")
        if t == "segment":
            self._append([(f"[{ev['ts']}] ", self._fmt(GRAY_TS, 9)), (ev["text"], self._fmt())])
        elif t == "mark":
            self._append([(f"⭐ {ev['text']} [{ev['ts']}]", self._fmt(bold=True, bg=HIGHLIGHT))])
        elif t == "info":
            self._append([(f"[{ev['ts']}] {ev['text']}", self._fmt(GRAY_TS, 9))])
            if ev["text"] == "סוף ההקלטה":
                self.save_draft(force=True)
        self._sync_state()

    # ------------------------------------------------------------------ session control
    def start(self):
        terms = [t.strip() for t in self.terms_in.toPlainText().replace("\n", ",").split(",") if t.strip()]
        try:
            s = self.ctl.start(self.title_in.text(), terms, self.get_dictionary())
        except Exception as e:
            QMessageBox.warning(self, "לא ניתן להתחיל", str(e)); return
        self.folder = s.folder
        self.title_lbl.setText(s.title)
        if not self.edit.toPlainText().strip():
            self._append([(s.title, self._fmt(size=20, bold=True))], heading=1)
        self._started = time.time()
        self.clock.start()
        self._sync_state()

    def stop(self):
        self.ctl.stop()
        self.clock.stop()
        self._sync_state()

    def _tick(self):
        if self.ctl.active:
            from .lecture import fmt_ts
            self.clock_lbl.setText(fmt_ts(self.ctl.session.elapsed))
            self.rec_dot.setVisible(not self.rec_dot.isVisible())

    def _sync_state(self):
        rec = self.ctl.active
        self.start_btn.setEnabled(not rec); self.stop_btn.setEnabled(rec); self.mark_btn.setEnabled(rec)
        self.title_in.setEnabled(not rec)
        self.rec_dot.setVisible(rec)
        if not rec:
            self.clock_lbl.setText("")
        self.status.setText("מקליט ומתמלל…" if rec else ("ההקלטה נשמרה" if self.folder else "מוכן"))

    # ------------------------------------------------------------------ saving
    def save_draft(self, force=False):
        if not self.folder or not (self._dirty or force):
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / "editor.html").write_text(self.edit.toHtml(), encoding="utf-8")
            self._dirty = False
        except Exception:
            log.exception("autosave failed")

    def export_docx(self):
        start = str((self.folder or Path.home()) / f"{self.title_lbl.text() or 'הרצאה'}.docx")
        path, _ = QFileDialog.getSaveFileName(self, "שמור כקובץ Word", start, "Word (*.docx)")
        if not path:
            return
        try:
            to_docx(self.edit.document(), Path(path))
            self.status.setText(f"נשמר: {Path(path).name}")
        except Exception as e:
            log.exception("docx export failed")
            QMessageBox.warning(self, "השמירה נכשלה", str(e))

    def _open_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        target = self.folder or self.ctl.base_dir
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def closeEvent(self, e):
        if self.ctl.active:
            r = QMessageBox.question(self, "ההקלטה פעילה", "לעצור את ההקלטה ולסגור את העורך?")
            if r != QMessageBox.Yes:
                e.ignore(); return
            self.stop()
        self.save_draft(force=True)
        e.accept()


# ---------------------------------------------------------------------- Word export
def _word_jc(al, rtl: bool) -> str | None:
    """Qt alignment → OOXML w:jc using logical start/end (what Word expects in bidi paragraphs).
    Qt: AlignLeft/Right without AlignAbsolute are leading/trailing; with AlignAbsolute they are visual."""
    if al & Qt.AlignHCenter:
        return "center"
    if al & Qt.AlignJustify:
        return "both"
    absolute = bool(al & Qt.AlignAbsolute)
    if al & Qt.AlignRight:
        visual_right = True if absolute else not rtl
    elif al & Qt.AlignLeft:
        visual_right = False if absolute else rtl
    else:
        return None  # default: start of the paragraph's direction
    start_is_right = rtl
    return None if visual_right == start_is_right else "end"


def to_docx(qdoc, path: Path):
    """QTextDocument → .docx: paragraphs (RTL/LTR, alignment, headings, bullets) and runs (bold, italic,
    underline, size, color, highlight)."""
    from docx import Document
    from docx.enum.text import WD_COLOR_INDEX
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]; st.font.name = "Arial"; st.font.size = Pt(12)
    rpr = st.element.get_or_add_rPr(); fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts"); rpr.append(fonts)
    fonts.set(qn("w:cs"), "Arial")  # complex-script (Hebrew) font

    block = qdoc.begin()
    while block.isValid():
        bf = block.blockFormat()
        level = bf.headingLevel()
        if level:
            p = d.add_heading(level=min(level, 3))
        elif block.textList():
            p = d.add_paragraph(style="List Bullet")
        else:
            p = d.add_paragraph()
        rtl = bf.layoutDirection() != Qt.LeftToRight
        if rtl:
            ppr = p._p.get_or_add_pPr(); bidi = OxmlElement("w:bidi"); ppr.insert(0, bidi)
        jc = _word_jc(bf.alignment(), rtl)
        if jc:
            ppr = p._p.get_or_add_pPr()
            el = OxmlElement("w:jc"); el.set(qn("w:val"), jc); ppr.append(el)
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.text():
                f = frag.charFormat()
                r = p.add_run(frag.text().replace(" ", "\n").replace(" ", "\n"))
                r.bold = f.fontWeight() >= QFont.Bold or None
                r.italic = f.fontItalic() or None
                r.underline = f.fontUnderline() or None
                if f.fontPointSize() > 0:
                    r.font.size = Pt(f.fontPointSize())
                    szcs = OxmlElement("w:szCs"); szcs.set(qn("w:val"), str(int(f.fontPointSize() * 2)))
                    r._r.get_or_add_rPr().append(szcs)
                fg = f.foreground()
                if fg.style() != Qt.NoBrush and fg.color().name() not in ("#000000", "#222222"):
                    c = fg.color(); r.font.color.rgb = RGBColor(c.red(), c.green(), c.blue())
                bg = f.background()
                if bg.style() != Qt.NoBrush:
                    r.font.highlight_color = WD_COLOR_INDEX.YELLOW
                if rtl:
                    rr = r._r.get_or_add_rPr(); rtl_el = OxmlElement("w:rtl"); rr.append(rtl_el)
                if r.bold:
                    rr = r._r.get_or_add_rPr(); rr.append(OxmlElement("w:bCs"))
            it += 1
        block = block.next()
    d.save(str(path))
