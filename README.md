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
BOT_TOKEN=your_telegram_bot_token_here
ALLOWED_USER_ID=8046833336

# Local AI Gateway & Obsidian Configuration
OBSIDIAN_VAULT_PATH=C:\Users\sarmi\Documents\Obsidian Vault
GATEWAY_HOST=127.0.0.1
GATEWAY_PORT=8765

# Cloud AI (Google Gemini - for general non-vault tasks only)
GEMINI_API_KEY=your_gemini_api_key_here

# Weather API (OpenWeatherMap)
OPENWEATHER_API_KEY=your_openweather_api_key_here

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

## 🔒 6. Obsidian Read-Only Knowledge Assistant

### 🎯 Architecture & Strict Security Boundary
```
Telegram Question (User: 8046833336)
       │
       ▼
Command Safety Filter ──► [Destructive/Write Intent] ──► Immediate Refusal
       │
       ▼ [Read Query]
Local AI Gateway (FastAPI http://127.0.0.1:8765)
       │
       ▼
Targeted RAG Engine (`vault_rag.py`)
       │
       ▼ (Path Boundary Validation & Mode: "r" only)
Obsidian Read-Only Adapter (`obsidian_vault_reader.py`)
       │
       ▼ (Smallest Sufficient Context < 2500 chars)
Local Ollama Engine (`qwen2.5:1.5b` on http://127.0.0.1:11434)
       │
       ▼ (Grounded Answer + Sources)
Telegram Response (Text + Optional Voice)
```

### 🛡️ Security Invariants
1. **Hard Read-Only Boundary:** The adapter `obsidian_vault_reader.py` exposes *only* safe inspection methods (`read_file`, `search_files`, `search_content`, `get_metadata`, `get_links`, `read_canvas_data`, `get_file_history`, `get_file_diff`). Zero write, create, delete, move, rename, or restore methods exist.
2. **Directory Traversal Protection:** Every path is validated with `os.path.commonpath` against the vault root and rejects any `..` patterns with `SecurityPathViolationError`.
3. **Command Safety Interception:** If a user sends commands like `"delete this note"`, `"edit note"`, `"create file"`, or Bengali equivalents, the system strictly returns:
   > *"I can read and search the vault, but this Telegram AI has no permission to edit or delete files."*
4. **Zero Cloud Leakage:** All Obsidian vault content and queries are processed 100% locally via the Local Gateway and Ollama. Vault data is **never** sent to Google Gemini, OpenAI, Claude, or any third-party service.
5. **Tool Isolation:** Ollama receives no filesystem tools or execution capabilities; it receives retrieved text exclusively as grounded context.
6. **Localhost Binding:** The FastAPI gateway binds exclusively to `127.0.0.1:8765`.

### 📂 Domain-Aware Knowledge Retrieval
The RAG engine analyzes queries and targets the user's specific Obsidian folder structure:
* **`01_College`**: College timetable, classes, teachers (SRB, RJR, ANS, SDM, etc.), subjects, rooms, and exams.
* **`02_Projects` & `99_System/AI_Memory/Projects`**: Technical and software projects (NIT Attendance, AeroIntel, Victus AI, etc.).
* **`03_Second_Brain/AI`**: Unified AI knowledge domain covering Machine Learning, Deep Learning, LLMs, Embeddings, RAG, Transformers, and Vector Databases.
* **`04_Resources` & `05_Archive`**: References, resources, and archived material.

---

## 💬 7. Telegram Usage & Voice Guide

| Action / Query | Example (Voice or Text) | Result |
|---|---|---|
| **Timetable / Classes** | *"সোমবার কি কি ক্লাস আছে?"*, *"What classes do I have on Monday?"* | Retrieves timetable from `01_College/Timetable`, lists periods, teachers, and rooms with source notes. |
| **AI Knowledge / Second Brain** | *"RAG কী এবং এটি কীভাবে কাজ করে?"*, *"What is an Embedding?"* | Retrieves definitions from `03_Second_Brain/AI/`, explains concepts, cites source notes. |
| **Vault Statistics** | `/vault_stats` | Displays total note counts and domain breakdown in strict read-only mode. |
| **Explicit Vault Query** | `/vault <your question>` | Direct query to the local Obsidian AI gateway. |
| **Destructive Command Refusal** | *"Delete Deep Learning.md"*, *"মুছে ফেলো এই ফাইলটা"* | Refuses immediately: *"I can read and search the vault, but this Telegram AI has no permission to edit or delete files."* |
| **Voice Conversation** | Send voice note in Bengali, Hindi, or English | Transcribes speech, queries local assistant/Ollama, replies in text & natural voice. |
| **Live Weather** | `"Weather in Kolkata"`, `"আজ বৃষ্টি হবে কি?"` | Queries OpenWeatherMap, summarizes temperature, humidity & wind. |
| **Excel Generation** | `"Make a monthly budget excel sheet"` | Generates `.xlsx` headlessly and sends file with download buttons. |
| **Remote PC Lock** | `/lock` | Instantly locks Windows workstation. |
| **Inspect Cache** | `/cache`, `/cache stats`, `/cache clear` | View and manage cached API responses. |

---

---

## 🔁 9. Iterative Answer Verification & Self-Improvement System

Instead of relying on a single raw search pass, queries to the Obsidian Vault undergo an iterative verification pipeline:

```
Telegram Question
       ↓
Temporal & Intent Preprocessing (e.g. 'kal' → 'Monday 2026-09-21')
       ↓
Retrieval Memory Check (Prioritize previously successful notes)
       ↓
┌────────────────────────────────────────────────────────┐
│ Iterative Verification Loop (1 to 5 cycles)            │
│                                                        │
│ 1. Search & Context Assembly                           │
│      ↓                                                 │
│ 2. Draft Answer Generation via Local Ollama            │
│      ↓                                                 │
│ 3. Answer Verification & Factual Grounding Check       │
│      ↓                                                 │
│ 4. Contradiction Detection across notes                │
│      ↓                                                 │
│ 5. Sufficient? (Confidence ≥ 0.80 & No Contradictions) │
│      ├─ YES ──→ Early Stop! Return Answer              │
│      └─ NO  ──→ Expand Query & Traverse Wikilinks      │
│                 Repeat up to 5 iterations              │
└────────────────────────────────────────────────────────┘
       ↓
Final Verified Answer with Verification Badge & Sources
       ↓
User Feedback Buttons: [ 👍 সঠিক ]  [ 👎 ভুল/অসম্পূর্ণ ]
       ↓
Local Retrieval Learning Memory (`retrieval_memory.json`)
```

### Self-Improvement Features:
1. **Adaptive Early Stopping:** Direct, unambiguous queries complete in 1–2 iterations with confidence $\ge 0.80$, saving local compute.
2. **Temporal Resolution (`verifier.py`):** Automatically maps relative date phrases like *"kal"*, *"kalke"*, *"কাল"*, *"আজকে"* to specific calendar dates and weekdays before searching timetables.
3. **Retrieval Memory (`retrieval_memory.py`):** Learns from query outcomes and user ratings without modifying model weights or vault files.
4. **Contradiction Detection:** Alerts when multiple notes report conflicting classroom numbers, superseded versions, or contradictory schedules.

---

## 🛠️ 10. Maintenance, Testing & Controls

* **Run Iterative Verification Test Suite (17 Tests):**
  ```powershell
  cd C:\TelegramBot
  python test_iterative_verification.py
  ```
* **Run Regression & Security Test Suite (15 Tests):**
  ```powershell
  cd C:\TelegramBot
  python test_vault_assistant.py
  ```
* **Inspect Local Retrieval Memory:**
  ```powershell
  curl http://127.0.0.1:8765/api/vault/memory_stats
  ```
* **Start Bot in Background (Silent Auto-Start):**
  ```powershell
  wscript.exe "C:\TelegramBot\RunBot.vbs"
  ```
* **Verify Health via Gateway API:**
  ```powershell
  curl http://127.0.0.1:8765/health
  ```

