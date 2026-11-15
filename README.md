<div dir="rtl">

# Local Dictation — הכתבה קולית בעברית, מקומית, מהירה על מחשבי Intel

מחזיקים מקש, מדברים בעברית (או עברית ואנגלית באותו משפט), משחררים — והטקסט נכתב במקום הסמן, בכל אפליקציה:
VS Code, Cursor, Claude Code, Chrome, WhatsApp, Word ועוד.
**הכול רץ על המחשב שלכם. בלי ענן, בלי מנוי, בלי שליחת הקלטות.**

### למה עוד כלי הכתבה בעברית?

רוב הכלים הקיימים מהירים רק עם כרטיס NVIDIA (CUDA), או שולחים את הדיבור לענן.
ברוב המחשבים הניידים החדשים יש **Intel Core Ultra עם GPU מובנה (Arc) ו-NPU** — ושם Whisper על המעבד בלבד לוקח ~5 שניות למשפט.
הפרויקט הזה מריץ את מודל העברית של [ivrit.ai](https://www.ivrit.ai) דרך **OpenVINO על ה-GPU של Intel**: **~0.5 שניות** מסוף הדיבור עד הטקסט.

| | זמן מסוף הדיבור (חציון) | WER עברית* |
|---|---|---|
| **ivrit turbo על Intel Arc 140T (OpenVINO)** | **0.55 שנ'** | **13.6%** |
| ivrit turbo על CPU (faster-whisper) | 5.2 שנ' | 13.7% |
| OpenAI whisper-large-v3-turbo על CPU | 5.7 שנ' | 29.5% |

\* FLEURS-he, נמדד על Core Ultra 9 285H. פירוט מלא, כולל NPU ועומס מקביל: [`bench/RESULTS.md`](bench/RESULTS.md).

### יכולות

- **Push-to-Talk** (החזקה) או **לחיצה קצרה** להקלטה ללא ידיים; מקש לבחירה — כולל מקש Copilot.
- **עברית + אנגלית באותו משפט**, ומילון אישי שמבין תחיליות: "בסופה בייס" ← "ב-Supabase", "לורסל" ← "ל-Vercel".
- **מצבי עיבוד** לפי האפליקציה הפעילה: Verbatim / Clean / Professional / Developer.
- **תצוגה חיה בזמן הדיבור** (אופציונלי, Zipformer streaming של ivrit.ai).
- **ליטוש עם מודל שפה מקומי** (אופציונלי, Gemma 4 E4B על ה-GPU) עם **שומר שמונע שינוי משמעות**:
  המודל לא עונה לשאלות שהוכתבו, לא מבצע הוראות שבטקסט, לא משנה מספרים, ולא מכניס מילה באנגלית שלא נאמרה
  (בדיקה פונטית: "גט יוזר" ← getUser ✓, "איי די" ← API ✗). כל דחייה ⇒ הטקסט המקורי נשמר.
- **פרטיות:** מיקרופון פתוח רק בזמן הקלטה, אין הכתבה לשדות סיסמה, הטקסט לא נכנס להיסטוריית Win+V,
  הלוגים מכילים זמנים בלבד — לעולם לא את התוכן, ואין שום קריאת רשת בזמן ריצה.
- **אמינות:** החלפת מיקרופון, ניתוק אוזניות, Sleep, החלפת חלון — הטקסט לעולם לא הולך לאיבוד (נשאר בלוח ובתפריט).

### דרישות

- Windows 11, Python 3.12
- מומלץ: Intel Core Ultra / Arc (GPU מובנה). בלי GPU של Intel — עובד על CPU (מדויק, ~5 שנ' להכתבה).
- ~6GB דיסק (‏+6GB למודל השפה האופציונלי), 16GB RAM ומעלה.

### התקנה

```
git clone <repo-url> local-dictation
cd local-dictation
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\setup_models.py          # הורדה והמרה של המודלים (פעם אחת, דורש אינטרנט)
.venv\Scripts\python scripts\setup_models.py --llm    # אופציונלי: מודל השפה לליטוש
```

אחר כך: לחיצה כפולה על **`Local Dictation.lnk`** בתיקיית הפרויקט. אייקון מיקרופון יופיע במגש המערכת.
אין במקלדת Right Ctrl? תפריט ← מקש קיצור ← "בחר מקש קיצור…" ולוחצים על המקש הרצוי.

### מגבלות ידועות

- Windows בלבד. אין עדיין מסלול CUDA ל-NVIDIA.
- המיקרופון הדיגיטלי של Intel צריך ~0.4 שנ' "להתעורר" בכל פתיחה (מוצג "מתחבר…"); אפשר להפעיל "מיקרופון חם" ל-30 שנ'.
- התצוגה החיה מתעדכנת כל ~1.3 שנ' (מגבלה של מודל ה-Zipformer).
- מודל השפה מוסיף 2–6 שנ', ולכן כבוי כברירת מחדל.

</div>

---

# Local Dictation (English)

Offline Hebrew/English voice typing for Windows, fast on Intel laptops without NVIDIA.
Hold a key, speak, release — the text is inserted at the cursor in any app. Nothing leaves your machine.

It runs ivrit.ai's Hebrew fine-tune of Whisper large-v3-turbo through **OpenVINO on the Intel Arc iGPU** (~0.5 s from end of
speech to text, vs ~5 s on CPU), with a CTranslate2 CPU fallback, an optional ivrit.ai Zipformer live preview, a Hebrew-aware
personal dictionary, per-app processing modes, and an optional local-LLM polish pass (Gemma 4 E4B) behind a guard that rejects
any output that answers, obeys, changes numbers, or introduces English words that were not spoken.

**Install:** see the commands above (`requirements.txt`, then `scripts/setup_models.py`). **Run:** `Local Dictation.lnk`
or `.venv\Scripts\pythonw.exe launch.pyw`. Settings/dictionary live in `%APPDATA%\LocalDictation\dictation.db`.

| Path | |
|---|---|
| `dictation/` | the app — one module per layer: `audio`, `asr`, `postprocess`, `llm`, `context`, `inject`, `hotkey`, `overlay`, `storage` |
| `scripts/setup_models.py` | downloads / converts models; creates the shortcut |
| `bench/` | ASR + LLM benchmarks, personal test-set recorder, [`RESULTS.md`](bench/RESULTS.md) |
| `tests/` | smoke tests: `python -m tests.smoke_hotkey`, `tests.smoke_inject`, `tests.smoke_pipeline` |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | research, design decisions, trade-offs (Hebrew) |

Contributions welcome — especially an NVIDIA/CUDA engine, macOS/Linux ports, and personal-voice benchmark data.

## Credits & licenses

Code: MIT (see [LICENSE](LICENSE)). Models are downloaded separately and keep their own licenses:

| Component | License |
|---|---|
| [ivrit-ai/whisper-large-v3-turbo](https://huggingface.co/ivrit-ai/whisper-large-v3-turbo) (+ `-ct2`) — ivrit.ai | Apache-2.0 |
| [ivrit-ai/zipformer2-66m-streaming-he](https://huggingface.co/ivrit-ai/zipformer2-66m-streaming-he) — developed by Yakov Kolani, mirrored by ivrit.ai | CC-BY-4.0 |
| [OpenVINO/gemma-4-E4B-it-int4-ov](https://huggingface.co/OpenVINO/gemma-4-E4B-it-int4-ov) — Google / Intel | Apache-2.0 |
| OpenVINO, OpenVINO GenAI (Intel) · faster-whisper / CTranslate2 · sherpa-onnx (k2-fsa) · Silero VAD | Apache-2.0 / MIT |
| PySide6 (Qt for Python) | LGPL-3.0 |
| Benchmark data: [google/fleurs](https://huggingface.co/datasets/google/fleurs) (not redistributed) | CC-BY-4.0 |

Thanks to [ivrit.ai](https://www.ivrit.ai) for making open Hebrew speech models possible.
