# 🤖 Hybrid Telegram AI Assistant & PC Controller
> **High-Performance Autonomous Hybrid (Cloud + Local) AI Agent with Multilingual Voice (STT/TTS), Automated Spreadsheet Generation, and Live System Control.**

---

## 📌 1. Project Overview

This project transforms a Windows PC/Laptop (HP Victus) into an always-on, autonomous, private personal assistant accessible 24/7 from anywhere via Telegram on smartphone or desktop. 

The bot intelligently balances **Cloud AI (Google Gemini)** and **Local Offline AI (Ollama with Qwen 2.5 & Gemma)**, features **low-latency Speech-to-Text (STT)** and **Neural Text-to-Speech (TTS)** in **Bengali, Hindi, and English**, connects to live weather APIs, generates styled Excel/Word documents via headless automation, and executes remote computer administration commands—all while protecting privacy, conserving API quotas, and persisting zero-effort auto-startup.

---

## 🏗️ 2. System Architecture

```
                                    ┌────────────────────────┐
                                    │    Telegram Client     │
                                    │ (Smartphone / Desktop) │
                                    └───────────┬────────────┘
                                                │ HTTPS (TLS 1.3)
                                                ▼
                                    ┌────────────────────────┐
                                    │  Telegram Bot Servers  │
                                    └───────────┬────────────┘
                                                │ Long-polling
                                                ▼
                            ┌────────────────────────────────────────┐
                            │      bot_server.py (Windows PC)        │
                            │   Authorization: ALLOWED_USER_ID       │
                            └───────────────────┬────────────────────┘
                                                │
                 ┌──────────────────────────────┴──────────────────────────────┐
                 ▼                                                             ▼
       [Voice Message (.ogg)]                                         [Text Message]
                 │                                                             │
                 ▼                                                             │
     ┌───────────────────────┐                                                 │
     │   audio_helper.py     │                                                 │
     │  PyAV in-memory WAV   │                                                 │
     │    16kHz Mono PCM     │                                                 │
     └───────────┬───────────┘                                                 │
                 ▼                                                             │
     ┌───────────────────────┐                                                 │
     │ Multi-Language STT    │                                                 │
     │ bn-IN / hi-IN / en-IN │                                                 │
     │  (Whisper Fallback)   │                                                 │
     └───────────┬───────────┘                                                 │
                 ▼                                                             │
         Transcribed Text ─────────────────────────────────────────────────────┤
                                                                               │
                                                                               ▼
                                                                ┌──────────────────────────────┐
                                                                │   Smart Persistent Cache     │
                                                                │  (gemini_cache.json, ≥70%)   │
                                                                └──────────────┬───────────────┘
                                                                               │ (Cache Miss)
                                                                               ▼
                                                                ┌──────────────────────────────┐
                                                                │      Intent Classifier       │
                                                                │  Rule-based + Fast Qwen LLM  │
                                                                └──────────────┬───────────────┘
                                                                               │
         ┌────────────────────────┬─────────────────────────┬──────────────────┴────────────────────┐
         ▼                        ▼                         ▼                                       ▼
  [TIME / DATE]              [WEATHER]                   [EXCEL]                             [CHAT / COMPLEX]
         │                        │                         │                                       │
         ▼                        ▼                         ▼                                       ▼
 ┌───────────────┐     ┌──────────────────────┐  ┌───────────────────────┐              ┌───────────────────────┐
 │ Real-Time PC  │     │   OpenWeatherMap     │  │ antigravity_main.py   │              │  Cloud Gemini Flash   │
 │ Local Clock   │     │  City Extractor +    │  │ Headless CLI Exec     │              │  (Daily Limit Track)  │
 │ (EN + Bengali)│     │  Natural Synthesis   │  │ SUCCESS_FILE Contract │              │          OR           │
 └───────┬───────┘     └──────────┬───────────┘  └──────────┬────────────┘              │ Local Ollama Models   │
         │                        │                         │                           │ (Qwen 1.5B / Gemma)   │
         │                        │                         │                           └───────────┬───────────┘
         └────────────────────────┴─────────────────────────┴───────────────────────────────────────┘
                                                │
                                                ▼
                                     ┌─────────────────────┐
                                     │  Response Formatter │
                                     └──────────┬──────────┘
                                                │
                        ┌───────────────────────┴───────────────────────┐
                        ▼                                               ▼
               Text / Document Reply                            Voice Note Reply
                                                              (if user spoke voice)
                                                                        │
                                                                        ▼
                                                             ┌─────────────────────┐
                                                             │   audio_helper.py   │
                                                             │   Edge-TTS Neural   │
                                                             │ bn-IN / hi-IN / en  │
                                                             │ (pyttsx3 Fallback)  │
                                                             └─────────────────────┘
```

---

## ⚙️ 3. Core Working Principles

### 3.1. Multilingual Voice Pipeline (STT & TTS)
* **Audio Transcoding:** Telegram voice messages arrive in `.ogg` (Opus-compressed) format. Instead of requiring external system dependencies like `ffmpeg.exe`, the bot uses **PyAV (`av`)** to decompress and resample the audio in-memory into 16,000 Hz single-channel mono PCM WAV format.
* **Speech-to-Text (STT):**
  1. The WAV stream is scored across **Bengali (`bn-IN`)**, **Hindi (`hi-IN`)**, and **English (`en-IN`)**.
  2. Language script analysis determines whether the speech is native Bengali, Devanagari Hindi, or Latin English.
  3. **Offline Fallback:** If internet is disconnected, local **`faster-whisper` (base model)** running with `int8` quantization on CPU automatically transcribes speech offline.
* **Text-to-Speech (TTS):**
  1. Detects unicode character scripts (`\u0980-\u09FF` for Bengali, `\u0900-\u097F` for Hindi, Latin for English).
  2. Synthesizes lifelike human neural voices using **Edge-TTS**:
     - 🇧🇩 **Bengali:** `bn-IN-BashkarNeural` / `bn-IN-TanishaaNeural`
     - 🇮🇳 **Hindi:** `hi-IN-MadhurNeural` / `hi-IN-SwaraNeural`
     - 🇬🇧 **English:** `en-IN-NeerjaNeural` / `en-US-JennyNeural`
  3. Clean text sanitization removes markdown symbols (`*`, `_`, `#`, URLs) so speech flows naturally without reading punctuation aloud.
  4. The audio is sent directly to Telegram as an interactive, playable voice bubble (`reply_voice`).
  5. **Offline Fallback:** Local Windows SAPI5 (`pyttsx3`) provides offline speech synthesis.

### 3.2. Hybrid AI Router & Zero-Waste Caching
1. **Persistent Cache (`gemini_cache.json`):**
   - Stores prompt, response, timestamp, and source.
   - Exact query matches return in `< 1ms` with 0 API tokens spent.
   - Employs tokenized **Jaccard Keyword Similarity (≥ 70%)** to match rephrased questions and save repetitive API calls.
2. **Deterministic Intent Classifier:**
   - Evaluates input instantly using compiled regex patterns for **WEATHER**, **TIME**, and **EXCEL** creation.
   - Bypasses unnecessary LLM inference for high-frequency commands.
   - Ambiguous queries fall back to a fast local classifier (`qwen2.5:1.5b`).
3. **Adaptive Cloud / Local AI Allocation:**
   - **Gemini 3.6 Flash:** Handles complex reasoning, coding, conversational Bengali/Hindi, and natural summaries within a daily tracked quota (`DAILY_API_LIMIT = 15`).
   - **Local Ollama Models:** When offline or daily API limit is reached, queries route seamlessly to **`qwen2.5:1.5b`** (fast responses) or **`gemma4:e4b`** (deep logic).

### 3.3. Autonomous Headless Document Architect
- The bot interfaces directly with `antigravity_main.py` via headless subprocess.
- **Contract:**
  ```powershell
  python antigravity_main.py "<user_prompt>"
  ```
- Generates professional spreadsheets (`.xlsx`) with formulas, dynamic cell styling, or documents (`.docx`).
- Outputs `SUCCESS_FILE: <path>` on stdout with return code `0`.
- The bot captures the path, generates an inline confirmation keyboard (`✅ হ্যাঁ, পাঠাও / ❌ না`), and uploads the file directly to the Telegram user.

### 3.4. Background Persistence & Auto-Boot
- Controlled via `C:\TelegramBot\RunBot.vbs` located in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`.
- Runs completely hidden without console windows (`WshShell.Run ..., 0, False`).
- `ensure_ollama_running()` pings port `11434` at startup and silently boots `ollama serve` if inactive.

---

## 📁 4. Project File Structure

```
C:\TelegramBot\
│
├── .env                     # Secret API tokens, keys, and paths (gitignored)
├── .env.example             # Template environment configuration
├── .gitignore               # Excludes secrets, caches, audio files, and logs
├── README.md                # System documentation & operating manual (this file)
│
├── bot_server.py            # Main Telegram application, router & event loop
├── audio_helper.py          # Pure in-memory PyAV audio transcoder, STT & TTS engine
├── RunBot.vbs               # Silent background Windows launcher
│
├── api_usage_tracker.json   # Daily Gemini quota counter
├── gemini_cache.json        # Persistent response cache with metadata
│
└── ...
```

---

## 🔐 5. Configuration & Environment Variables (`.env`)

Secrets are isolated inside `.env` to prevent accidental exposure:

```env
# Telegram Bot Configuration
BOT_TOKEN=8566709518:AAEKMcyuSkzG1pA3u8N5uaKoVQi-LK0GfBk
ALLOWED_USER_ID=8046833336

# Cloud AI (Google Gemini)
GEMINI_API_KEY=AIzaSyCVCYJ9Ab14-km9-n-XCJ7Pdw_dLmqNVIg

# Weather API (OpenWeatherMap)
OPENWEATHER_API_KEY=27056fc00603e987d24c8122e04c8b04

# Local AI (Ollama)
OLLAMA_URL=http://127.0.0.1:11434/api/generate
FAST_LOCAL_MODEL=qwen2.5:1.5b
HEAVY_LOCAL_MODEL=gemma4:e4b
DAILY_API_LIMIT=15

# Excel Architect Paths
EXCEL_SCRIPT_PATH=C:\Users\sarmi\.gemini\antigravity\scratch\ai_excel_architect\antigravity_main.py
EXCEL_OUTPUT_DIR=C:\Users\sarmi\.gemini\antigravity\scratch\ai_excel_architect\dist
```

---

## 💬 6. Telegram Usage & Voice Guide

| Action / Query | Example (Voice or Text) | Result |
|---|---|---|
| **Voice Conversation** | Send voice note: *"আজকের আবহাওয়া কেমন?"* | Bot transcribes speech, responds with text & **Voice Note back** in natural Bengali. |
| **Hindi Voice** | Send voice note: *"आज का मौसम कैसा है?"* | Bot transcribes Hindi, replies in text & natural Hindi voice note. |
| **English Voice** | Send voice note: *"What time is it?"* | Bot transcribes English, replies with clock and English voice note. |
| **Live Weather** | `"Weather in Delhi"`, `"কলকাতা র আবহাওয়া"` | Queries OpenWeatherMap, summarizes temperature, humidity, wind & forecast. |
| **Real-time Clock** | `"কটা বাজে"`, `"সময় কত"`, `"time"` | Real-time PC local clock and calendar date. |
| **Excel Generation** | `"Make a loan calculator excel sheet"` | Generates `.xlsx` headlessly and sends file with interactive buttons. |
| **Remote Lock** | `/lock` | Instantly locks Windows workstation (`user32.dll,LockWorkStation`). |
| **Remote Terminal** | `/cmd dir`, `/cmd ipconfig` | Executes Windows terminal command and returns stdout. |
| **Inspect Cache** | `/cache`, `/cache stats`, `/cache clear` | View and manage cached API responses. |

---

## 🛠️ 7. Maintenance & Manual Controls

* **Start bot in background:**
  ```powershell
  wscript.exe "C:\TelegramBot\RunBot.vbs"
  ```
* **Run in foreground (for live debug logs):**
  ```powershell
  cd C:\TelegramBot
  python bot_server.py
  ```
* **Verify active bot process:**
  ```powershell
  Get-WmiObject Win32_Process -Filter "name='python.exe'" | Select-Object ProcessId, CommandLine
  ```
