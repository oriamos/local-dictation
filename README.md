# Local Dictation

Offline Hebrew + English voice typing for Windows. Hold a key, speak, release: the text appears at your cursor in any app.
Nothing leaves your machine.

Built for Intel laptops without NVIDIA: it runs [ivrit.ai](https://www.ivrit.ai)'s Hebrew Whisper on the Intel Arc iGPU via OpenVINO,
about **10× faster than CPU**.

## Performance

Core Ultra 9 285H, FLEURS-he (details in [`bench/RESULTS.md`](bench/RESULTS.md)):

| Engine | Latency after speech | Hebrew WER |
|---|---|---|
| **ivrit turbo, Intel Arc GPU (OpenVINO)** | **0.55 s** | **13.6%** |
| ivrit turbo, CPU | 5.2 s | 13.7% |
| OpenAI whisper-large-v3-turbo, CPU | 5.7 s | 29.5% |

## Features

- **Works in any app**: VS Code, Cursor, browsers, WhatsApp, Word, terminals. Never presses Enter.
- **Hebrew/English in one sentence**, with a personal dictionary that handles Hebrew prefixes (`בסופה בייס` → `ב-Supabase`).
- **Hold to talk, or tap for hands-free.** Any key, including the Copilot key.
- **Per-app modes**: Verbatim, Clean, Professional, Developer.
- **Optional live preview** while speaking (ivrit.ai Zipformer streaming).
- **Optional local LLM polish** (Gemma 4 E4B). A guard rejects any output that answers, follows dictated instructions,
  changes numbers, or adds English words you didn't say.
- **Private**: mic opens only while recording, no dictation into password fields, no network at runtime, logs contain timings only.

## Requirements

- Windows 11, Python 3.12
- Intel Core Ultra / Arc recommended. Without an Intel GPU it falls back to CPU (accurate, ~5 s).
- ~6 GB disk (+6 GB for the optional LLM)

## Install

```
git clone https://github.com/oriamos/local-dictation
cd local-dictation
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\setup_models.py        # one-time download + conversion
```

Add `--llm` to also download the optional polish model.

## Usage

Double-click **`Local Dictation.lnk`**. A mic icon appears in the system tray (the menu is in Hebrew).

| Action | |
|---|---|
| Hold **Right Ctrl**, speak, release | dictate |
| Tap once, tap again | hands-free dictation |
| **Esc** | cancel |
| Tray → Hotkey → Pick a hotkey… | choose another key |
| Tray → Personal dictionary | add terms and how they're misheard |

## Known limitations

- Windows only. No NVIDIA/CUDA path yet.
- Intel's digital mic takes ~0.4 s to wake (shown as "connecting"). Enable the warm-mic option for instant restarts.
- Live preview updates every ~1.3 s (model limit).
- The LLM polish adds 2–6 s, so it's off by default.

## Project layout

| Path | |
|---|---|
| `dictation/` | the app: audio, asr, postprocess, llm, context, inject, hotkey, overlay, storage |
| `scripts/setup_models.py` | model download/conversion, shortcut |
| `bench/` | ASR and LLM benchmarks |
| `tests/` | smoke tests (`python -m tests.smoke_pipeline`, etc.) |
| `docs/ARCHITECTURE.md` | design decisions (Hebrew) |

Contributions welcome, especially an NVIDIA engine, macOS/Linux ports, and real-voice test data.

## License

Code: MIT. Models are downloaded separately under their own licenses:
[ivrit-ai Whisper](https://huggingface.co/ivrit-ai/whisper-large-v3-turbo) (Apache-2.0),
[ivrit-ai Zipformer](https://huggingface.co/ivrit-ai/zipformer2-66m-streaming-he) by Yakov Kolani (CC-BY-4.0),
[Gemma 4 E4B OpenVINO](https://huggingface.co/OpenVINO/gemma-4-E4B-it-int4-ov) (Apache-2.0).
Built on OpenVINO, faster-whisper, sherpa-onnx, Silero VAD and PySide6 (LGPL-3.0).
