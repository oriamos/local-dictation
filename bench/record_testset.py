"""Record a personal test set for the benchmark (code-switching, tech terms, numbers).

Run:  .venv\\Scripts\\python bench\\record_testset.py
For each prompt: press Enter to start recording, read it naturally (fillers are fine),
press Enter to stop. Files go to bench/data/mine/NN.wav + NN.txt (the reference).
Recordings stay on this machine. Delete the folder whenever you like.
"""
import sys
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

OUT = Path(__file__).resolve().parent / "data" / "mine"

PROMPTS = [
    "פתח את ה-repository ב-GitHub ותבדוק את ה-migrations האחרונות ב-Supabase.",
    "אני רוצה שתבדוק את Supabase ותראה אם יש בעיה בטבלת users, ואז תתקן אותה.",
    "תעשה deploy ל-Vercel אחרי שה-build עובר בלי שגיאות TypeScript.",
    "תוסיף event חדש ב-PostHog שנקרא signup completed.",
    "ב-Next.js תעביר את ה-API route לתיקייה app/api/webhooks.",
    "תריץ את השאילתה ב-SQL ותגיד לי כמה רשומות חזרו.",
    "תפתח את Claude Code ותבקש ממנו לכתוב בדיקות ל-API.",
    "תגדיר שרת MCP חדש שמתחבר ל-GitHub.",
    "הפגישה נקבעה ליום שלישי, ה-12 בדצמבר, בשעה 14:30.",
    "המחיר הוא 1,250 שקלים לחודש, כולל מע\"מ.",
    "יש לנו 3 באגים פתוחים ו-17 משימות שנשארו לספרינט.",
    "שלום, רציתי לשאול אם אתה פנוי מחר בבוקר לשיחה קצרה?",
    "תודה רבה על העזרה, זה ממש עזר לי להבין את הבעיה.",
    "הצוות עובד על הגרסה החדשה ב-React ואמור לסיים עד סוף השבוע.",
    "לגבי ה-Docker, צריך לבדוק את זה שוב מול הצוות.",
    "Please review the pull request and let me know if the tests pass.",
    "תכתוב פונקציה בשם getUserById שמקבלת id ומחזירה אובייקט user.",
    "הקובץ נמצא ב-src/components/Header.tsx, תשנה שם את הצבע של הכפתור.",
    # long, natural (read it, then keep talking freely if you want; the reference is just this text)
    "אוקיי, אז ככה. אני רוצה שנבנה מסך הגדרות חדש. בצד ימין יהיה תפריט עם קטגוריות, "
    "ובצד שמאל התוכן של כל קטגוריה. כל שינוי צריך להישמר אוטומטית ב-Supabase, "
    "ואם יש שגיאה צריך להציג הודעה קטנה למטה. בנוסף, תוודא שזה עובד גם במובייל.",
    "בוא נעבור על התוכנית להיום. קודם כול נסגור את הבאג בטופס ההרשמה, אחרי זה נעבור "
    "על ה-pull requests הפתוחים, ובסוף היום נעשה deploy לסביבת staging ונבדוק שהכול תקין.",
]


def record() -> np.ndarray:
    frames, stop = [], threading.Event()

    def cb(indata, n, t, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=16000, channels=1, dtype="float32", callback=cb):
        input()
    return np.concatenate(frames)[:, 0] if frames else np.zeros(0, np.float32)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Mic: {sd.query_devices(kind='input')['name']}\nOutput: {OUT}\n")
    for i, p in enumerate(PROMPTS, 1):
        wav = OUT / f"{i:02d}.wav"
        if wav.exists():
            continue
        print(f"[{i}/{len(PROMPTS)}] {p}")
        input("  Enter = start ")
        print("  ● recording… Enter = stop")
        a = record()
        sf.write(wav, a, 16000)
        (OUT / f"{i:02d}.txt").write_text(p, encoding="utf-8")
        print(f"  saved {len(a) / 16000:.1f}s\n")
    print("Done. Run: .venv\\Scripts\\python bench\\run_bench.py --dataset bench\\data\\mine --limit 0 --engines ivrit-turbo,openai-turbo,zipformer")


if __name__ == "__main__":
    main()
