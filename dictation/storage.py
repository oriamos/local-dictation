"""Settings, personal dictionary and optional history in one local SQLite file.

Location: %APPDATA%/LocalDictation (Windows), ~/Library/Application Support/LocalDictation (macOS).
"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
import threading
import time
from pathlib import Path

MAC = sys.platform == "darwin"
APPLE_SILICON = MAC and platform.machine() == "arm64"
APP_DIR = (Path.home() / "Library" / "Application Support" / "LocalDictation") if MAC else \
    Path(os.environ.get("APPDATA", Path.home())) / "LocalDictation"
PROJECT_DIR = Path(__file__).resolve().parent.parent
# Converted OpenVINO models live next to the code (override with LOCALDICTATION_MODELS).
MODELS_DIR = Path(os.environ.get("LOCALDICTATION_MODELS", PROJECT_DIR / "models"))

DEFAULT_SETTINGS = {
    "hotkey": "right_option" if MAC else "right_ctrl",  # see hotkey_*.GROUPS; combos like "ctrl+win" allowed
    "activation": "ptt",             # ptt | toggle
    "mic_warm_s": 0,                 # keep the mic open N s after a dictation for instant restart (0 = off)
    "live_preview": False,           # streaming: Zipformer partial text in the overlay while speaking
    "mode": "clean",                 # verbatim | clean | professional | developer
    "auto_profile": True,            # pick mode from the foreground app
    "overlay": True,
    # primary engine; if it fails to load (driver, missing files) the fallback is used automatically
    # Windows: OpenVINO on the Intel GPU. Apple Silicon: MLX on the Metal GPU. Intel Mac: CPU (CTranslate2).
    "asr_primary": ({"backend": "mlx", "model": "mlx-community/ivrit-ai-whisper-large-v3-turbo-mlx"} if APPLE_SILICON else
                    {"backend": "ct2", "model": "ivrit-ai/whisper-large-v3-turbo-ct2", "threads": 8, "beam": 1} if MAC else
                    {"backend": "openvino", "device": "GPU", "model_dir": str(MODELS_DIR / "ivrit-turbo-ov-fp16")}),
    "asr_fallback": {"backend": "ct2", "model": "ivrit-ai/whisper-large-v3-turbo-ct2", "threads": 8, "beam": 1},
    "use_prompt": True,              # bias Whisper toward dictionary terms
    # optional local-LLM polish (Gemma 4 E4B int4 on the GPU). Off by default: adds ~1.5–6 s.
    # budget: max(budget_s, budget_per_word × words) — past it, generation is cancelled and the text is kept as is.
    "llm": {"enabled": False, "device": "MLX" if APPLE_SILICON else "GPU", "budget_s": 2.5, "budget_per_word": 0.16,
            "model_dir": "mlx-community/gemma-4-e4b-it-4bit" if APPLE_SILICON else str(MODELS_DIR / "gemma-4-E4B-it-int4-ov"),
            "professional": True,          # always polish in Professional mode
            "clean_min_words": 25},        # in Clean mode only for long dictations; Verbatim/Developer never
    "lecture_chunk_s": 30,           # lecture editor: a chunk every ~30 s regardless of pauses (max 29.5 s audio)
    "lecture_polish": False,         # lecture editor: normalize each chunk with the local LLM (guarded)
    "history": False,                # opt-in
    "inject_method": "paste",        # paste | type
    "restore_clipboard": True,
    "min_hold_ms": 250,              # shorter PTT presses are ignored (accidental taps)
    "profiles": {                    # process name (lowercase, no .exe) -> mode
        "code": "developer", "cursor": "developer", "windsurf": "developer",
        "windowsterminal": "developer", "cmd": "verbatim", "powershell": "developer", "pwsh": "developer",
        "winword": "professional", "outlook": "professional",
        "whatsapp": "clean", "whatsapp.root": "clean", "claude": "clean", "chatgpt": "clean",
        "chrome": "clean", "msedge": "clean", "firefox": "clean", "notepad": "clean",
        # macOS (localized app names, lowercase)
        "visual studio code": "developer", "terminal": "developer", "iterm2": "developer", "warp": "developer",
        "xcode": "developer", "ghostty": "developer", "microsoft word": "professional", "pages": "professional",
        "mail": "professional", "microsoft outlook": "professional", "google chrome": "clean", "safari": "clean",
        "arc": "clean", "notes": "clean", "slack": "clean", "messages": "clean",
    },
}

# term -> spoken/misheard variants. Variants match with optional Hebrew prefixes (ב, ל, ה, ו, מ, ש, כ).
DEFAULT_DICTIONARY = {
    "Supabase": ["סופה בייס", "סופהבייס", "סופבייס", "סופא בייס", "סופר בייס", "סופרבייס", "סופה בס", "supa base"],
    "Next.js": ["נקסט ג'יי אס", "נקסט ג׳יי אס", "נקסטג'ס", "נקסט JS", "next js", "nextjs", "נקסט ג'ס"],
    "TypeScript": ["טייפסקריפט", "טייפ סקריפט", "טיפסקריפט", "type script", "typescript"],
    "Vercel": ["ורסל", "ורצל", "ורסאל", "וורסל", "versel", "vercel"],
    "PostHog": ["פוסטהוג", "פוסט הוג", "פוסטהאג", "post hog", "posthog"],
    "Claude Code": ["קלוד קוד", "קלאוד קוד", "cloud code", "claude code"],
    "Claude": ["קלוד", "קלאוד"],
    "MCP": ["אם סי פי", "אמ סי פי", "אם.סי.פי", "mcp"],
    "GitHub": ["גיטהאב", "גיט האב", "גיטהב", "גיט-האב", "github", "git hub"],
    "React": ["ריאקט", "רי-אקט", "react"],
    "Docker": ["דוקר", "docker"],
    "Python": ["פייתון", "פיתון", "python"],
    "API": ["איי פי איי", "אייפיאיי", "אי פי איי", "api"],
    "SQL": ["אס קיו אל", "אסקיואל", "סיקוול", "sql"],
    # common dev vocabulary spoken in Hebrew (editable/removable in the dictionary window)
    "deploy": ["דפלוי", "דיפלוי"],
    "build": ["בילד"],
    "commit": ["קומיט"],
    "push": ["פוש"],
    "merge": ["מרג'", "מרג׳", "מרג"],
    "branch": ["בראנץ'", "בראנץ׳", "בראנץ", "ברנץ'"],
    "repo": ["ריפו"],
    "repository": ["ריפוזיטורי", "רפוזיטורי"],
    "pull request": ["פול ריקווסט", "פול רקווסט"],
    "staging": ["סטייג'ינג", "סטייג׳ינג", "סטיג'ינג"],
    "production": ["פרודקשן"],
    "frontend": ["פרונטאנד", "פרונט אנד", "פרונטנד"],
    "backend": ["באקאנד", "בק אנד", "בקאנד", "באק אנד"],
    "endpoint": ["אנדפוינט", "אנד פוינט"],
    "webhook": ["ווב הוק", "וובהוק"],
    "prompt": ["פרומפט"],
}


class Store:
    def __init__(self, path: Path | None = None):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or APP_DIR / "dictation.db"
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dictionary (term TEXT PRIMARY KEY, variants TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, ts REAL, app TEXT, mode TEXT,
                                                raw TEXT, text TEXT, audio_s REAL, latency_s REAL);
        """)
        if not self.db.execute("SELECT 1 FROM dictionary LIMIT 1").fetchone() and \
                not self.db.execute("SELECT 1 FROM settings WHERE key='_dict_seeded'").fetchone():
            for t, v in DEFAULT_DICTIONARY.items():
                self.db.execute("INSERT OR IGNORE INTO dictionary VALUES (?,?)", (t, json.dumps(v, ensure_ascii=False)))
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('_dict_seeded','true')")
        self.db.commit()

    # -------- settings
    def get(self, key):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return DEFAULT_SETTINGS.get(key)
        v = json.loads(row[0])
        if isinstance(v, dict) and isinstance(DEFAULT_SETTINGS.get(key), dict):
            return {**DEFAULT_SETTINGS[key], **v}
        return v

    def set(self, key, value):
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
            self.db.commit()

    # -------- dictionary
    def dictionary(self) -> dict[str, list[str]]:
        return {t: json.loads(v) for t, v in self.db.execute("SELECT term, variants FROM dictionary ORDER BY term")}

    def set_term(self, term: str, variants: list[str]):
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO dictionary VALUES (?,?)", (term, json.dumps(variants, ensure_ascii=False)))
            self.db.commit()

    def delete_term(self, term: str):
        with self._lock:
            self.db.execute("DELETE FROM dictionary WHERE term=?", (term,))
            self.db.commit()

    # -------- history (only written when the user enabled it)
    def add_history(self, **row):
        if not self.get("history"):
            return
        with self._lock:
            self.db.execute("INSERT INTO history (ts, app, mode, raw, text, audio_s, latency_s) VALUES (?,?,?,?,?,?,?)",
                            (time.time(), row.get("app"), row.get("mode"), row.get("raw"), row.get("text"),
                             row.get("audio_s"), row.get("latency_s")))
            self.db.commit()

    def recent_history(self, n=20):
        return self.db.execute("SELECT ts, app, text FROM history ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def clear_history(self):
        with self._lock:
            self.db.execute("DELETE FROM history")
            self.db.commit()
            self.db.execute("VACUUM")
