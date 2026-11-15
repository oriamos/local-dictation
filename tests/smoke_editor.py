"""Lecture editor without a mic: opens the real editor window (offscreen), feeds live events while the user's
cursor sits in the middle of the document, then exports to Word and reads the .docx back.
Checks: paragraphs appended at the end in order, the user's cursor/selection untouched, mark highlighted,
docx paragraphs are RTL (w:bidi) with Hebrew text intact, and a manual LTR paragraph stays LTR.
Run:  python -m tests.smoke_editor
"""
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv[:1] + ["-platform", "offscreen"])
from dictation.editor import LectureEditor, to_docx  # noqa: E402


class Store(dict):
    def get(self, k, d=None): return super().get(k, d)
    def set(self, k, v): self[k] = v


class Ctl:  # minimal controller stand-in
    listeners, active, base_dir = [], False, Path(tempfile.gettempdir())
    def mark(self): pass


ed = LectureEditor(Ctl(), Store(), dict)
ed.show()
e = ed.edit
e.setPlainText("הערה שלי בתחילת המסמך.\nעוד שורה שכתבתי.")
# user puts the caret inside the first line and selects a word
c = e.textCursor(); c.setPosition(5); c.setPosition(9, QTextCursor.KeepAnchor); e.setTextCursor(c)
before = (e.textCursor().anchor(), e.textCursor().position())

events = [{"type": "segment", "ts": "00:00", "text": "היום נדבר על גרפים מכוונים ועל BFS."},
          {"type": "mark", "ts": "00:07", "text": "חשוב!"},
          {"type": "segment", "ts": "00:09", "text": "האלגוריתם של דייקסטרה עובד רק עם משקלים חיוביים."}]
for ev in events:
    ed._on_event(ev)
app.processEvents()
after = (e.textCursor().anchor(), e.textCursor().position())
text = e.toPlainText()
blocks = text.split(" ") if " " in text else text.split("\n")
print("document:\n  " + "\n  ".join(blocks))
ok_order = blocks[-3].endswith("BFS.") and "⭐" in blocks[-2] and "דייקסטרה" in blocks[-1]
ok_cursor = before == after
# mark is highlighted
last = e.document().lastBlock().previous()
it = last.begin(); hl = it.fragment().charFormat().background().style() != Qt.NoBrush

# a manual English paragraph set to LTR by the user
c = QTextCursor(e.document()); c.movePosition(QTextCursor.End); c.insertBlock(); c.insertText("Manual English note")
e.setTextCursor(c); ed._direction(Qt.LeftToRight)

out = Path(tempfile.mkdtemp()) / "lecture.docx"
to_docx(e.document(), out)
from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
paras = [p for p in Document(str(out)).paragraphs if p.text.strip()]
bidi = [p._p.pPr is not None and p._p.pPr.find(qn("w:bidi")) is not None for p in paras]
print("docx:", [(p.text[:28], "RTL" if b else "LTR") for p, b in zip(paras, bidi)])
ok_docx = all(bidi[:-1]) and not bidi[-1] and any("דייקסטרה" in p.text for p in paras)
print(f"order {ok_order} | cursor untouched {ok_cursor} {before}->{after} | mark highlighted {hl} | docx {ok_docx}")
print("RESULT:", "PASS" if all([ok_order, ok_cursor, hl, ok_docx]) else "FAIL")
