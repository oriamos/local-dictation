# Local Dictation — מחקר, ארכיטקטורה והחלטות

## 1. החומרה בפועל (נבדק)

| רכיב | ערך |
|---|---|
| מחשב | ASUS Zenbook Duo UX8406CA |
| מעבד | Intel Core Ultra 9 285H (Arrow Lake-H, 16 ליבות: 6P + 8E + 2LP-E) |
| GPU | Intel Arc 140T (משולב, Xe2, זיכרון משותף עד 16GB) — **אין GPU ייעודי** |
| NPU | Intel AI Boost (פעיל, ComputeAccelerator) |
| RAM | 32GB |
| מיקרופונים | Intel SST Digital Mic, Realtek, Bluetooth, USB |

## 2. מודלים שנבדקו

| מודל | רישיון | Streaming אמיתי | עברית (WER, טבלת ivrit.ai) | אנגלית בתוך עברית | הרצה מקומית ב-Windows |
|---|---|---|---|---|---|
| **ivrit-ai/whisper-large-v3-turbo** | Apache-2.0 | לא (מקטעים) | eval-d1 5.3 · whatsapp **7.1** | טובה (בסיס Whisper רב-לשוני) | CT2 (faster-whisper), OpenVINO (המרה), ONNX, GGML |
| **ivrit-ai/zipformer2-66m-streaming-he** (2026-10) | CC-BY-4.0 | **כן** (chunk 1.28s) | eval-d1 **5.1** · whatsapp 8.9 | **חלשה** (מאיית אות-אות) | sherpa-onnx (CPU int8) |
| ivrit-ai/whisper-large-v3 | Apache-2.0 | לא | דומה ל-turbo, איטי פי ~3–4 | טובה | CT2 / GGML |
| openai/whisper-large-v3-turbo | MIT | לא | 8.4 · 12.8 | טובה | כל המנועים |
| openai whisper small / medium | MIT | לא | גבוה משמעותית | בינונית | כל המנועים |
| parakeet-tdt-0.6b-v3-he | — | חלקי | 6.1 · 8.8 | לא נבדק | NeMo (כבד ב-Windows) |

מסקנה מהמחקר: אין כיום מודל אחד שמנצח בכל הצירים. Whisper-turbo של ivrit.ai הכי מדויק בדיבור חופשי (WhatsApp) ותומך במעבר שפות,
Zipformer הוא היחיד שמזרים באמת אבל חלש באנגלית.

## 3. החלטות

* **מודל ראשי:** `ivrit-ai/whisper-large-v3-turbo` (דיוק בעברית + code-switching = עדיפות 1).
* **מודל חלופי:** אותו מודל ב-CTranslate2 על CPU (int8) — נטען אוטומטית אם ה-GPU/דרייבר נכשל.
* **Streaming (Phase 4):** Zipformer לתצוגה חיה בזמן הדיבור ב-Overlay, ו-Whisper מפיק את הטקסט הסופי בסיום.
  כך מקבלים עדכונים כל ~300–700ms בלי לוותר על הדיוק ועל האנגלית.
* **האצה:** OpenVINO GenAI על Arc 140T — ראו מדידות בסעיף 5. ה-NPU נבדק כחלופה חסכונית בחשמל.

## 4. ארכיטקטורה

תהליך אחד, Python, ללא שרת וללא ענן. בחרתי ב-Qt (PySide6-Essentials) ולא ב-Tauri/Electron:
מנועי ה-ASR הם ספריות Python, כך שתהליך אחד חוסך IPC ו-sidecar; Qt נותן Tray, חלון שלא מקבל פוקוס והגדרות
ב-~60MB RAM. Tauri דורש Rust ו-sidecar Python ממילא, והיתרון היחיד שלו (גודל התקנה) לא רלוונטי כאן.

```
 hotkey.py ──(down/up/esc)──► app.py (state machine, Qt main thread)
 WH_KEYBOARD_LL                 │
                                ├─► audio.py   Recorder: WASAPI/PortAudio, נפתח רק בזמן הקלטה
                                ├─► asr.py     VAD (Silero) → Engine: OpenVINO-GPU | CT2-CPU   [thread ASR]
                                ├─► postprocess.py  מילון + ניקוי לפי מצב (Verbatim/Clean/Professional/Developer)
                                ├─► context.py  שם תהליך חזיתי → פרופיל;  UIA IsPassword
                                ├─► inject.py   Clipboard+Ctrl+V (עם שחזור) | SendInput Unicode   [thread inject]
                                ├─► overlay.py  Pill ללא פוקוס (WS_EX_NOACTIVATE | TRANSPARENT)
                                └─► storage.py  SQLite: הגדרות, מילון, היסטוריה (opt-in)
```

החלפת מודל = רשומת הגדרות (`asr_primary` / `asr_fallback`), בלי לגעת בשאר הקוד.

### החלטות עיצוב חשובות
* **מיקרופון פתוח רק בזמן הקלטה** — פרטיות + CPU אפסי במנוחה. נפתח מחדש בכל הקלטה על ההתקן הנוכחי ⇒ החלפת מיקרופון עובדת.
* **Hotkey ב-Low-Level hook** — נדרש ל-Push-to-Talk (Key-Up) ולמקשי Modifier בלבד. ה-callback רק מעביר אירוע (Windows מנתק hooks איטיים).
  זיהוי Sleep/Resume לפי קפיצת שעון ⇒ התקנה מחדש של ה-hook.
* **מקש שהוא חלק מקיצור** (Right Ctrl + C) ⇒ ההקלטה מבוטלת בשקט. לחיצה קצרה מ-250ms מתעלמת.
* **הזרקה:** הדבקה דרך הלוח (הכי אמין לעברית ב-Chrome/Electron/WhatsApp/VS Code), מסומנת כלא-נכנסת להיסטוריית Win+V
  ולסנכרון ענן; הלוח הקודם משוחזר אם אף אחד לא שינה אותו בינתיים. Enter לעולם לא נשלח.
* **לא מאבדים טקסט:** חלון היעד השתנה/נסגר ⇒ הטקסט בלוח + הודעה. כשל הזרקה ⇒ בלוח. כשל תמלול ⇒ האודיו נשמר בזיכרון ל"נסה שוב".
* **שדה סיסמה** (UIA IsPassword) ⇒ לא מוזרק.
* **הזיות Whisper בשקט** ("תודה רבה") ⇒ VAD לפני המודל + סינון ביטויים ידועים בהקלטות קצרות.
* **לוגים** — זמנים ומצבים בלבד, לעולם לא תוכן מוכתב.

## 5. Benchmark

סקריפט: `bench/run_bench.py`. ערכות: FLEURS-he (דיבור מוקרא, 60 קטעים) + `bench/record_testset.py` (הקלטות אישיות:
מונחים טכניים, עברית+אנגלית, מספרים, הקלטות ארוכות). מדדים: WER/CER, latency מסוף הדיבור, RTF, זמן טעינה, RAM,
CPU-seconds לשניית אודיו (תחליף לצריכת חשמל — ל-Windows אין API צריכת חשמל לתהליך), ו-`--stress N` לעומס מקביל.

תוצאות: ראו `bench/RESULTS.md`.

## 6. פשרות

| בחירה | רווח | מחיר |
|---|---|---|
| turbo במקום large-v3 | פי ~3–4 מהיר | הבדל דיוק זניח לפי טבלת ivrit.ai |
| GPU (OpenVINO) במקום CPU | latency נמוך בהרבה, CPU פנוי ל-IDE | זמן קומפילציה בהפעלה ראשונה (נשמר ב-cache), ~1.5–2GB זיכרון משותף |
| עיבוד דטרמיניסטי במקום LLM | 0ms, לא ממציא תוכן | לא משכתב ("טבלה של היוזרס" ⇒ נשאר) — זה תפקיד ה-LLM ב-Phase 3 |
| הדבקה דרך הלוח | עובדת בכל אפליקציה | נוגעת בלוח (משוחזר), ~350ms המתנה לשחזור ברקע |
