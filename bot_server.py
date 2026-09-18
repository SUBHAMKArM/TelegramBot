import os
import sys
import json
import datetime
import subprocess
import time
import requests
from google import genai
import audio_helper
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.request import HTTPXRequest

# ---------- Load Environment Variables (.env) ----------
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

# ---------- AI Models Settings ----------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
FAST_LOCAL_MODEL = os.environ.get("FAST_LOCAL_MODEL", "qwen2.5:1.5b")
HEAVY_LOCAL_MODEL = os.environ.get("HEAVY_LOCAL_MODEL", "gemma4:e4b")
DAILY_API_LIMIT = int(os.environ.get("DAILY_API_LIMIT", "15"))
TRACKER_FILE = "api_usage_tracker.json"
CACHE_FILE = "gemini_cache.json"

# ---------- Excel Settings (Optional) ----------
EXCEL_SCRIPT_PATH = os.environ.get("EXCEL_SCRIPT_PATH", "")
EXCEL_OUTPUT_DIR = os.environ.get("EXCEL_OUTPUT_DIR", "./dist")
GENERATED_EXTENSIONS = (".xlsx", ".docx", ".html")

# ---------- Gemini Setup ----------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
gemini_client = None
if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ---------- Gateway Setup (Localhost 127.0.0.1) ----------
from gateway_api import (
    is_destructive_command, COMMAND_SAFETY_REFUSAL,
    GATEWAY_HOST, GATEWAY_PORT
)
GATEWAY_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/vault/query"
GATEWAY_STATS_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/vault/stats"
GATEWAY_FEEDBACK_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/vault/feedback"
GATEWAY_MEMORY_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/vault/memory_stats"

# ---------- OpenWeather Setup ----------
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")

def is_authorized(update: Update) -> bool:
    user = update.effective_user
    if user is not None and user.id == ALLOWED_USER_ID:
        return True
    uid = user.id if user else "Unknown"
    print(f"⛔ [SECURITY ALERT] Unauthorized Telegram user rejected: ID={uid}")
    return False

def ensure_gateway_running():
    """Ensures the local FastAPI gateway is running on 127.0.0.1:8765."""
    try:
        r = requests.get(f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/health", timeout=1)
        if r.status_code == 200:
            return
    except Exception:
        pass
    gateway_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway_api.py")
    subprocess.Popen(
        [sys.executable, gateway_script],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    )
    time.sleep(2)
    print("✅ Local Obsidian AI Gateway চালু করা হয়েছে।")

# ======================================================================
# QUOTA TRACKER (daily API limit)
# ======================================================================
def get_api_quota_status() -> bool:
    today = str(datetime.date.today())
    data = {"date": today, "count": 0}
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data["count"] < DAILY_API_LIMIT

def increment_api_quota():
    today = str(datetime.date.today())
    data = {"date": today, "count": 0}
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["count"] = data.get("count", 0) + 1
    data["date"] = today
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ======================================================================
# SMART PERSISTENT CACHE (API রেসপন্স স্টোর + keyword match)
# ======================================================================
def _load_cache() -> list:
    """ক্যাশ ফাইল লোড করে। ফরম্যাট: list of {prompt, response, source, timestamp}"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _save_cache(entries: list):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

def _normalize(text: str) -> str:
    """ক্যাশ ম্যাচিং-এর জন্য টেক্সট নর্মালাইজ করে।"""
    return " ".join(text.strip().lower().split())

def _keyword_set(text: str) -> set:
    """স্টপওয়ার্ড বাদে গুরুত্বপূর্ণ keyword বের করে।"""
    stop = {'a','an','the','is','are','was','were','do','does','did','will','can','could',
            'should','would','what','how','why','when','where','who','which','me','my',
            'i','you','your','we','our','it','its','to','of','in','for','on','with','at',
            'by','and','or','but','not','this','that','these','those','be','been','being',
            'have','has','had','am','about','from','up','out','so','if','then','than','very',
            'just','also','more','some','any','no','all','each','every','into','over','ki',
            'kore','koro','dao','bolo','ta','er','ke','te','ar','ekta','ek','na','hobe',
            'ache','keno','ki','bolte','paro','amake','amar','tumi','tomar','kothay','kon'}
    words = set(_normalize(text).split())
    return words - stop

def save_to_cache(prompt: str, response: str, source: str = "gemini"):
    """নতুন API রেসপন্স ক্যাশে সেভ করে।"""
    entries = _load_cache()
    entries.append({
        "prompt": prompt.strip(),
        "response": response,
        "source": source,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    _save_cache(entries)

def get_cached_response(prompt: str):
    """
    ক্যাশ থেকে রেসপন্স খোঁজে — ২ স্তরে:
    1. Exact match (normalized)
    2. Keyword similarity ≥ 70% হলে best match দেয়
    """
    entries = _load_cache()
    if not entries:
        return None

    norm_prompt = _normalize(prompt)

    # Pass 1: Exact match
    for e in entries:
        if _normalize(e["prompt"]) == norm_prompt:
            return e["response"]

    # Pass 2: Keyword similarity match
    prompt_kw = _keyword_set(prompt)
    if len(prompt_kw) < 2:
        return None  # খুব ছোট প্রম্পটে fuzzy match বিভ্রান্তি করবে

    best_match = None
    best_score = 0.0

    for e in entries:
        cached_kw = _keyword_set(e["prompt"])
        if not cached_kw:
            continue
        common = prompt_kw & cached_kw
        # উভয় দিকের similarity check (Jaccard-like)
        score = len(common) / max(len(prompt_kw), len(cached_kw))
        if score > best_score:
            best_score = score
            best_match = e

    if best_score >= 0.70 and best_match:
        return best_match["response"]

    return None

def get_cache_stats() -> str:
    """ক্যাশের সামারি — /cache কমান্ডের জন্য।"""
    entries = _load_cache()
    if not entries:
        return "📭 ক্যাশ খালি — কোনো API রেসপন্স স্টোর হয়নি।"

    lines = [f"📦 মোট ক্যাশ: {len(entries)} টি এন্ট্রি\n"]
    for i, e in enumerate(entries[-15:], 1):  # শেষ ১৫টা দেখায়
        src = e.get("source", "?").upper()
        ts = e.get("timestamp", "?")
        q = e["prompt"][:60] + ("..." if len(e["prompt"]) > 60 else "")
        lines.append(f"{i}. [{src}] {ts}\n   ❓ {q}")
    
    if len(entries) > 15:
        lines.append(f"\n... আরো {len(entries) - 15} টি আছে।")

    api_saved = len(entries)
    lines.append(f"\n💰 আনুমানিক API কল বাঁচানো: {api_saved} টি")
    return "\n".join(lines)

def get_cache_entry_detail(index: int) -> str:
    """নির্দিষ্ট ক্যাশ এন্ট্রির পুরো ডিটেইল দেখায়।"""
    entries = _load_cache()
    if not entries:
        return "ক্যাশ খালি।"
    if index < 1 or index > len(entries):
        return f"ভুল নম্বর। ১ থেকে {len(entries)} এর মধ্যে দিন।"
    e = entries[index - 1]
    return (
        f"📋 ক্যাশ এন্ট্রি #{index}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🕐 সময়: {e.get('timestamp', '?')}\n"
        f"🔧 সোর্স: {e.get('source', '?').upper()}\n"
        f"❓ প্রশ্ন:\n{e['prompt']}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💬 উত্তর:\n{e['response'][:3500]}"
    )

def clear_cache():
    """পুরো ক্যাশ ক্লিয়ার করে।"""
    _save_cache([])
    return "🗑️ ক্যাশ সম্পূর্ণ ক্লিয়ার করা হয়েছে!"

# ======================================================================
# LOCAL AI HELPERS
# ======================================================================
def query_ollama(prompt: str, model: str = FAST_LOCAL_MODEL, timeout: int = 60):
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False, "keep_alive": "10m"}, # keep_alive ফিক্স
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip() or None
    except Exception as e:
        print(f"❌ Ollama Error ({model}): {e}") 
        return None

def classify_intent(text: str) -> str:
    lower = text.lower().strip()

    # 0. COMMAND SAFETY CHECK (Strict Read-Only Refusal)
    if is_destructive_command(text):
        return "COMMAND_SAFETY_REFUSAL"

    # 1. TIME / DATE Keywords (instant check)
    time_keywords = [
        "কটা বাজে", "কয়টা বাজে", "কয়টা বাজে", "সময় কত", "সময় কত", "time", 
        "clock", "date", "tarikh", "তারিখ", "আজ কি বার", "আজকে কি বার", "what time",
        "কয়টা বাজল", "কটা বাজল"
    ]
    if any(k in lower for k in time_keywords):
        return "TIME"

    # 2. WEATHER Keywords (instant check)
    weather_keywords = [
        "weather", "temperature", "temp", "forecast", "climate", "rain", "raining", 
        "বৃষ্টি", "আবহাওয়া", "আবহাওয়া", "তাপমাত্রা", "ঝড়", "ঝড়বৃষ্টি", "গরম", "ঠান্ডা", 
        "মেঘ", "রোদ"
    ]
    if any(k in lower for k in weather_keywords):
        return "WEATHER"

    # 3. EXCEL / Document Creation (instant check)
    excel_keywords = [
        "excel", "spreadsheet", "xlsx", "docx", "এক্সেল", "বানাও", "তৈরি করো", 
        "বানিয়ে দাও", "make excel", "create excel", "excel sheet"
    ]
    if any(k in lower for k in excel_keywords):
        return "EXCEL"

    # 4. OBSIDIAN VAULT / COLLEGE / SECOND BRAIN / AI KNOWLEDGE (instant check)
    vault_keywords = [
        "vault", "obsidian", "ভল্ট", "নোট", "নোটস", "notes", "note", "second brain",
        "timetable", "routine", "ক্লাস", "রুটিন", "টিচার", "মাস্টার", "কলেজ", "পরীক্ষা", "exam",
        "syllabus", "room 401", "316p", "316q", "srb", "rjr", "ans", "sdm", "nrp", "spm",
        "mrb", "dms", "brd", "bca", "narula", "nit bca", "attendance project",
        "rag", "retrieval", "vector database", "embeddings", "neural network",
        "transformers", "deep learning", "machine learning", "artificial intelligence"
    ]
    if any(k in lower for k in vault_keywords):
        return "VAULT"

    # 5. Fallback: LLM Classification for complex queries
    classifier_prompt = (
        "Classify the following user message into exactly ONE category: VAULT, EXCEL, WEATHER, TIME, COMPLEX, or CHAT.\n"
        "- VAULT: user asks about personal notes, Obsidian vault, college timetable, college classes, teachers, syllabus, or AI concepts.\n"
        "- EXCEL: user asks to make spreadsheet, excel, doc, table, or tracker.\n"
        "- WEATHER: user asks about weather, rain, temperature, climate.\n"
        "- TIME: user asks what time it is or what date it is.\n"
        "- COMPLEX: advanced general coding, logic, architecture.\n"
        "- CHAT: greetings, casual talk, simple short questions.\n"
        f"Message: \"{text}\"\nOutput category ONLY:"
    )
    res = query_ollama(classifier_prompt, model=FAST_LOCAL_MODEL, timeout=10)
    if res:
        res_upper = res.upper()
        if "VAULT" in res_upper: return "VAULT"
        if "WEATHER" in res_upper: return "WEATHER"
        if "EXCEL" in res_upper: return "EXCEL"
        if "TIME" in res_upper: return "TIME"
        if "COMPLEX" in res_upper: return "COMPLEX"
    return "CHAT"

def run_excel_exe(prompt: str):
    """Call AI_Excel_Architect.py in headless mode and parse SUCCESS_FILE: contract."""
    if not os.path.isfile(EXCEL_SCRIPT_PATH):
        return False, None, f"স্ক্রিপ্ট পাওয়া যায়নি: {EXCEL_SCRIPT_PATH}"
    try:
        result = subprocess.run(
            [sys.executable, EXCEL_SCRIPT_PATH, prompt],
            capture_output=True, text=True, timeout=300,
            cwd=EXCEL_OUTPUT_DIR
        )
        if result.returncode != 0:
            return False, None, (result.stderr or "Unknown error").strip()[:1000]
        # Parse SUCCESS_FILE: from stdout
        for line in result.stdout.splitlines():
            if line.startswith("SUCCESS_FILE:"):
                filepath = line.split("SUCCESS_FILE:", 1)[1].strip()
                if os.path.isfile(filepath):
                    return True, filepath, None
        return False, None, "SUCCESS_FILE not found in output"
    except subprocess.TimeoutExpired:
        return False, None, "টাইমআউট: ৫ মিনিটের মধ্যে ফাইল তৈরি হয়নি"
    except Exception as e:
        return False, None, str(e)

def fetch_weather_for_prompt(prompt: str) -> dict:
    """Extract location from prompt and fetch live weather data dictionary."""
    prompt_lower = prompt.lower()
    
    # Common quick matches
    common_cities = {
        "kolkata": "Kolkata", "কলিকাতা": "Kolkata", "কলকাতা": "Kolkata", "calcutta": "Kolkata",
        "delhi": "Delhi", "দিল্লি": "Delhi", "new delhi": "Delhi",
        "mumbai": "Mumbai", "মুম্বাই": "Mumbai", "bombay": "Mumbai",
        "dhaka": "Dhaka", "ঢাকা": "Dhaka",
        "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
        "london": "London", "লন্ডন": "London",
        "new york": "New York", "tokyo": "Tokyo", "paris": "Paris",
        "chennai": "Chennai", "hyderabad": "Hyderabad", "pune": "Pune",
        "siliguri": "Siliguri", "শিলিগুড়ি": "Siliguri", "শিলিগুড়ি": "Siliguri",
        "howrah": "Howrah", "হাওড়া": "Howrah", "হাওড়া": "Howrah"
    }
    
    location = None
    for k, v in common_cities.items():
        if k in prompt_lower:
            location = v
            break
            
    if not location:
        extract_prompt = f"Extract the city or town name in English from this text. If no specific location is mentioned, reply 'Kolkata'. ONLY output the English city name. Text: '{prompt}'"
        extracted = query_ollama(extract_prompt, model=FAST_LOCAL_MODEL, timeout=10)
        if extracted and len(extracted) < 30:
            location = extracted.strip(" '\".").split('\n')[0].strip()
        else:
            location = "Kolkata"
            
    url = f"http://api.openweathermap.org/data/2.5/weather?q={location}&appid={OPENWEATHER_API_KEY}&units=metric"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            temp = data['main']['temp']
            feels_like = data['main']['feels_like']
            desc = data['weather'][0]['description']
            humidity = data['main']['humidity']
            wind = data.get('wind', {}).get('speed', 0)
            city = data['name']
            country = data['sys'].get('country', '')
            return {
                "success": True,
                "city": city,
                "country": country,
                "temp": temp,
                "feels_like": feels_like,
                "desc": desc,
                "humidity": humidity,
                "wind": wind,
                "raw_text": f"Weather in {city}, {country}: {temp}°C (feels like {feels_like}°C), Condition: {desc}, Humidity: {humidity}%, Wind: {wind} m/s"
            }
        else:
            return {"success": False, "error": f"শহর '{location}' খুঁজে পাওয়া যায়নি (HTTP {resp.status_code})"}
    except Exception as e:
        return {"success": False, "error": f"ওয়েদার API এরর: {e}"}

# ======================================================================
# HANDLERS
# ======================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("⛔ Unauthorized access.")
        return
    welcome_text = (
        "👋 নমস্কার! হাইব্রিড এআই সহকারী অনলাইন। 🚀\n\n"
        "🔒 **Obsidian Vault Knowledge Assistant (Strict Read-Only):**\n"
        "• ভল্টের যেকোনো বিষয়ে প্রশ্ন করুন (রুটিন, ক্লাস, AI নোট, ইত্যাদি)।\n"
        "• কমান্ড: `/vault <প্রশ্ন>` বা সরাসরি টেক্সট/ভয়েস পাঠান।\n"
        "• পরিসংখ্যান: `/vault_stats`\n\n"
        "🌤️ **ওয়েদার:** 'কলকাতা weather' বা 'বৃষ্টি হবে কি?'\n"
        "📊 **এক্সেল:** 'excel বানাও ...'\n"
        "🎙️ **ভয়েস সাপোর্ট:** বাংলা, হিন্দি ও ইংরেজিতে ভয়েস মেসেজ পাঠাতে পারেন।"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct Obsidian Vault Query Command."""
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("⛔ Unauthorized access.")
        return
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("📖 ব্যবহার: `/vault <আপনার প্রশ্ন>`\nউদাহরণ: `/vault সোমবার কি কি ক্লাস আছে?`", parse_mode="Markdown")
        return
    await process_user_query(update, context, query, reply_as_voice=False)

async def vault_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches read-only summary statistics and retrieval memory metrics from the local gateway."""
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("⛔ Unauthorized access.")
        return
    ensure_gateway_running()
    try:
        res = requests.get(GATEWAY_STATS_URL, timeout=5)
        if res.status_code == 200:
            data = res.json()
            domain_lines = "\n".join(f"  • {k}: {v} notes" for k, v in data.get("domains", {}).items())
            
            # Fetch retrieval memory stats
            mem_info = ""
            try:
                mres = requests.get(GATEWAY_MEMORY_URL, timeout=3)
                if mres.status_code == 200:
                    mdata = mres.json()
                    mem_info = (
                        f"\n\n🧠 **Retrieval Memory:**\n"
                        f"  • Tracked Queries: {mdata.get('total_queries', 0)}\n"
                        f"  • Boosted Sources: {mdata.get('boosted_sources_count', 0)}\n"
                        f"  • Demoted Sources: {mdata.get('demoted_sources_count', 0)}\n"
                        f"  • Feedback: 👍 {mdata.get('positive_feedback', 0)} | 👎 {mdata.get('negative_feedback', 0)}"
                    )
            except Exception:
                pass

            msg = (
                f"📚 **Obsidian Vault Statistics (Strict Read-Only)**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📁 মোট নোটস: {data.get('total_notes', 0)} টি\n"
                f"📂 ডোমেইন ব্রেকডাউন:\n{domain_lines}"
                f"{mem_info}\n\n"
                f"🔒 **নিরাপত্তা:** STRICT READ-ONLY\n"
                f"🚫 ফাইল তৈরি, পরিবর্তন বা মুছে ফেলা সম্পূর্ণ নিষ্ক্রিয়।"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(f"⚠️ ভল্ট স্ট্যাটাস আনতে সমস্যা: HTTP {res.status_code}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ গেটওয়ে এরর: {e}")

async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("⛔ Unauthorized access.")
        return
    command = " ".join(context.args)
    if not command:
        await update.message.reply_text("কমান্ড লেখো, যেমন: /cmd dir")
        return
    # Hard security invariant: Prohibit any shell command from touching the Obsidian vault
    cmd_lower = command.lower()
    if any(k in cmd_lower for k in ["obsidian", "vault"]):
        if any(w in cmd_lower for w in ["del", "rm", "remove", "erase", "format", "ren", "move", "copy", "echo", ">", ">>"]):
            await update.message.reply_text("⛔ Security Violation: Direct filesystem modification of the Obsidian Vault is prohibited.")
            return
    try:
        output = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, text=True)
        await update.message.reply_text(f"```\n{output[:4000] or 'Success'}\n```", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"ত্রুটি:\n{str(e)}")

async def lock_pc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True)
    await update.message.reply_text("ল্যাপটপ লক করা হয়েছে 🔒")

async def reply_text_and_voice(update: Update, text: str, reply_as_voice: bool = False):
    """Sends text reply and optionally converts it to voice and sends back."""
    await update.message.reply_text(text[:4000])
    
    if reply_as_voice:
        voice_path = f"voice_{update.message.message_id}_{int(time.time())}.mp3"
        try:
            ok = await audio_helper.text_to_speech(text, voice_path)
            if ok and os.path.exists(voice_path):
                with open(voice_path, "rb") as vf:
                    await update.message.reply_voice(voice=vf)
        except Exception as e:
            print(f"Error sending voice reply: {e}")
        finally:
            if os.path.exists(voice_path):
                try: os.remove(voice_path)
                except Exception: pass

async def process_user_query(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_as_voice: bool = False):
    """Processes any user request (from text or transcribed voice) and responds."""
    cached = get_cached_response(text)
    if cached:
        await reply_text_and_voice(update, f"💾 [Cached Result]\n{cached}", reply_as_voice=reply_as_voice)
        return

    intent = classify_intent(text)

    if intent == "COMMAND_SAFETY_REFUSAL":
        await reply_text_and_voice(update, COMMAND_SAFETY_REFUSAL, reply_as_voice=reply_as_voice)
        return

    elif intent == "VAULT":
        ensure_gateway_running()
        status_msg = await update.message.reply_text("🔍 Obsidian ভল্টে খোঁজা হচ্ছে ও লোকাল AI যাচাই করছে... 🧠")
        try:
            user_id = update.effective_user.id if update.effective_user else ALLOWED_USER_ID
            res = requests.post(
                GATEWAY_URL,
                json={"query": text, "telegram_user_id": user_id},
                timeout=120
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

            if res.status_code == 200:
                data = res.json()
                answer = data.get("answer", "I couldn't find enough information in the Obsidian vault.")
                sources = data.get("sources", [])

                # Feedback keyboard if answer came from vault & not a safety refusal
                keyboard = None
                if not data.get("refused") and data.get("has_context"):
                    context.user_data["last_vault_query"] = {
                        "query": text,
                        "sources": sources
                    }
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("👍 সঠিক", callback_data="fb_pos"),
                        InlineKeyboardButton("👎 ভুল/অসম্পূর্ণ", callback_data="fb_neg")
                    ]])

                if reply_as_voice:
                    await reply_text_and_voice(update, answer, reply_as_voice=True)
                    if keyboard:
                        await update.message.reply_text("রেসপন্সটি কি সঠিক ও সহায়ক ছিল?", reply_markup=keyboard)
                else:
                    await update.message.reply_text(answer[:4000], reply_markup=keyboard)
            else:
                await reply_text_and_voice(update, f"⚠️ ভল্ট গেটওয়ে ত্রুটি (HTTP {res.status_code})", reply_as_voice=reply_as_voice)
        except Exception as e:
            try:
                await status_msg.delete()
            except Exception:
                pass
            await reply_text_and_voice(update, f"⚠️ ভল্ট গেটওয়ে কানেকশন ত্রুটি: {e}", reply_as_voice=reply_as_voice)
        return

    elif intent == "EXCEL":
        await update.message.reply_text("📊 Antigravity এক্সেল বানাচ্ছে...")
        success, filepath, error = run_excel_exe(text)
        if success:
            context.user_data["pending_file"] = filepath
            filename = os.path.basename(filepath)
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ হ্যাঁ, পাঠাও", callback_data="send_file"),
                InlineKeyboardButton("❌ না", callback_data="cancel_file"),
            ]])
            await update.message.reply_text(f"✅ ফাইল রেডি: {filename}\nপাঠাবো?", reply_markup=keyboard)
            if reply_as_voice:
                await reply_text_and_voice(update, f"আপনার এক্সেল ফাইল রেডি হয়েছে। নাম {filename}। পাঠাব কি?", reply_as_voice=True)
        else:
            await reply_text_and_voice(update, f"❌ এক্সেল বানাতে সমস্যা: {error}", reply_as_voice=reply_as_voice)
        return

    elif intent == "TIME":
        now = datetime.datetime.now()
        time_str = now.strftime("%I:%M %p")
        date_str = now.strftime("%d %B, %Y (%A)")
        b_digits = {"0": "০", "1": "১", "2": "২", "3": "৩", "4": "৪", "5": "৫", "6": "৬", "7": "৭", "8": "৮", "9": "৯"}
        b_time = "".join(b_digits.get(c, c) for c in time_str)
        reply = f"⏰ এখন সময়: {time_str} ({b_time})\n📅 তারিখ: {date_str}"
        await reply_text_and_voice(update, reply, reply_as_voice=reply_as_voice)
        return

    elif intent == "WEATHER":
        await update.message.reply_text("🌤️ আবহাওয়ার খবর দেখা হচ্ছে...")
        weather_res = fetch_weather_for_prompt(text)
        
        if not weather_res.get("success"):
            await reply_text_and_voice(update, f"⚠️ {weather_res.get('error', 'আবহাওয়ার তথ্য পাওয়া যায়নি।')}", reply_as_voice=reply_as_voice)
            return

        card = (
            f"🌤️ আবহাওয়া রিপোর্ট: {weather_res['city']}, {weather_res['country']}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🌡️ তাপমাত্রা: {weather_res['temp']}°C (অনুভূত হচ্ছে {weather_res['feels_like']}°C)\n"
            f"☁️ অবস্থা: {weather_res['desc'].capitalize()}\n"
            f"💧 আর্দ্রতা: {weather_res['humidity']}%\n"
            f"🌬️ বাতাস: {weather_res['wind']} m/s"
        )

        if get_api_quota_status() and gemini_client:
            ai_prompt = (
                f"The user asked: \"{text}\".\n"
                f"Real-time weather data: {weather_res['raw_text']}.\n"
                "Formulate a warm, natural summary in Bengali (or English/Hindi if user asked in that language). "
                "Mention temperature, feels like, weather condition, and humidity. Keep it within 3-4 sentences."
            )
            try:
                gemini_res = gemini_client.models.generate_content(
                    model='gemini-3.6-flash', contents=ai_prompt
                )
                reply_text = gemini_res.text.strip()
                increment_api_quota()
                save_to_cache(text, reply_text, "gemini-weather")
                await reply_text_and_voice(update, f"🌤️ {reply_text}", reply_as_voice=reply_as_voice)
                return
            except Exception:
                pass

        await reply_text_and_voice(update, card, reply_as_voice=reply_as_voice)
        return

    elif intent == "COMPLEX":
        if get_api_quota_status() and gemini_client:
            await update.message.reply_text("🌐 Gemini ভাবছে...")
            try:
                gemini_res = gemini_client.models.generate_content(
                    model='gemini-3.6-flash', contents=text
                )
                reply_text = gemini_res.text
                increment_api_quota()
                save_to_cache(text, reply_text, "gemini")
                await reply_text_and_voice(update, f"🌐 [Gemini AI]\n{reply_text}", reply_as_voice=reply_as_voice)
                return
            except Exception as e:
                await update.message.reply_text(f"⚠️ API Error: {e}। লোকাল হেভি মডেলে যাচ্ছে...")
        else:
            await update.message.reply_text("⚠️ API কোটা শেষ বা কী নেই! লোকাল হেভি এআই-এ (gemma) যাচ্ছে... ⏳")
        
        heavy_res = query_ollama(text, model=HEAVY_LOCAL_MODEL, timeout=300)
        await reply_text_and_voice(update, heavy_res if heavy_res else "লোকাল হেভি এআই রেসপন্স দেয়নি।", reply_as_voice=reply_as_voice)
        return

    else:
        # General chat
        if get_api_quota_status() and gemini_client:
            try:
                gemini_res = gemini_client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=f"You are a helpful personal assistant bot on Telegram. Answer politely and concisely in the same language as user.\nUser: {text}"
                )
                reply_text = gemini_res.text.strip()
                increment_api_quota()
                save_to_cache(text, reply_text, "gemini-chat")
                await reply_text_and_voice(update, reply_text, reply_as_voice=reply_as_voice)
                return
            except Exception:
                pass

        reply = query_ollama(f"You are a helpful assistant. Reply concisely in user's language.\nUser: {text}\nAssistant:", model=FAST_LOCAL_MODEL, timeout=30)
        await reply_text_and_voice(update, reply if reply else "লোকাল এআই কানেক্ট করা যায়নি।", reply_as_voice=reply_as_voice)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles standard text messages."""
    if not is_authorized(update): return
    text = update.message.text
    if not text: return
    await process_user_query(update, context, text, reply_as_voice=False)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming voice messages and audio files."""
    if not is_authorized(update): return
    voice = update.message.voice or update.message.audio
    if not voice: return

    status_msg = await update.message.reply_text("🎙️ ভয়েস শুনছি ও রূপান্তর করছি...")
    try:
        voice_file = await context.bot.get_file(voice.file_id)
        audio_bytes = await voice_file.download_as_bytearray()
        
        transcribed_text, detected_lang = audio_helper.transcribe_audio(bytes(audio_bytes))
        if not transcribed_text:
            await status_msg.edit_text("❌ ভয়েস বুঝতে পারিনি। দয়া করে একটু স্পষ্ট করে বলুন।")
            return
            
        await status_msg.edit_text(f"🎤 [{detected_lang}]: \"{transcribed_text}\"")
        
        # Process request and respond in BOTH text and voice!
        await process_user_query(update, context, transcribed_text, reply_as_voice=True)
    except Exception as e:
        await status_msg.edit_text(f"⚠️ ভয়েস প্রসেসিং ত্রুটি: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ALLOWED_USER_ID: return
    await query.answer()
    if query.data == "send_file":
        filepath = context.user_data.get("pending_file")
        if filepath and os.path.exists(filepath):
            with open(filepath, "rb") as f:
                await query.message.reply_document(document=f)
        context.user_data.pop("pending_file", None)
    elif query.data == "cancel_file":
        await query.message.reply_text("বাতিল করা হলো।")
        context.user_data.pop("pending_file", None)
    elif query.data == "fb_pos":
        last_q = context.user_data.get("last_vault_query")
        if last_q:
            try:
                requests.post(GATEWAY_FEEDBACK_URL, json={
                    "query": last_q["query"],
                    "sources": last_q["sources"],
                    "is_positive": True
                }, timeout=5)
            except Exception as e:
                print(f"Feedback error: {e}")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("🙏 ধন্যবাদ! ফিডব্যাক লোকাল মেমরিতে রেকর্ড করা হয়েছে (এই নোটগুলো অগ্রাধিকার পাবে)।")
            context.user_data.pop("last_vault_query", None)
        else:
            await query.edit_message_reply_markup(reply_markup=None)
    elif query.data == "fb_neg":
        last_q = context.user_data.get("last_vault_query")
        if last_q:
            try:
                requests.post(GATEWAY_FEEDBACK_URL, json={
                    "query": last_q["query"],
                    "sources": last_q["sources"],
                    "is_positive": False
                }, timeout=5)
            except Exception as e:
                print(f"Feedback error: {e}")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("⚠️ ফিডব্যাক রেকর্ড করা হয়েছে (ভুল/অসম্পূর্ণ)। পরবর্তী অনুসন্ধানে বিকল্প ও সম্পর্কিত নোট বিবেচনা করা হবে।")
            context.user_data.pop("last_vault_query", None)
        else:
            await query.edit_message_reply_markup(reply_markup=None)

async def cache_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cache         → সব ক্যাশ দেখায়
    /cache 3       → ৩ নম্বর এন্ট্রির ডিটেইল
    /cache clear   → পুরো ক্যাশ ক্লিয়ার
    /cache stats   → কতটা API বাঁচানো হয়েছে
    """
    if not is_authorized(update): return
    args = context.args

    if not args:
        await update.message.reply_text(get_cache_stats())
    elif args[0].lower() == "clear":
        await update.message.reply_text(clear_cache())
    elif args[0].lower() == "stats":
        entries = _load_cache()
        today_count = sum(1 for e in entries if e.get("timestamp", "").startswith(str(datetime.date.today())))
        msg = (
            f"📊 ক্যাশ পরিসংখ্যান:\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📦 মোট এন্ট্রি: {len(entries)}\n"
            f"📅 আজকের: {today_count}\n"
            f"💰 API কল বাঁচানো: ~{len(entries)} টি\n"
            f"📁 ফাইল: {os.path.abspath(CACHE_FILE)}"
        )
        await update.message.reply_text(msg)
    elif args[0].isdigit():
        detail = get_cache_entry_detail(int(args[0]))
        await update.message.reply_text(detail[:4096])
    else:
        await update.message.reply_text(
            "📖 ব্যবহার:\n"
            "/cache — সব ক্যাশ দেখো\n"
            "/cache 3 — ৩নং এন্ট্রি বিস্তারিত\n"
            "/cache stats — পরিসংখ্যান\n"
            "/cache clear — সব মুছে ফেলো"
        )

def ensure_ollama_running():
    try:
        requests.get("http://127.0.0.1:11434", timeout=2)
    except Exception:
        # Ollama বন্ধ থাকলে উইন্ডোজের ব্যাকগ্রাউন্ডে সাইলেন্টলি অন করবে
        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        time.sleep(3)  # ইঞ্জিন বুট হতে ৩ সেকেন্ড সময়
        print("✅ Ollama সার্ভার ব্যাকগ্রাউন্ডে চালু করা হয়েছে।")

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN is missing! Please configure BOT_TOKEN in your .env file.")
        exit(1)
    ensure_ollama_running()
    ensure_gateway_running()
    custom_request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
    app = ApplicationBuilder().token(BOT_TOKEN).request(custom_request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vault", vault_cmd))
    app.add_handler(CommandHandler("vault_stats", vault_stats_cmd))
    app.add_handler(CommandHandler("cmd", run_cmd))
    app.add_handler(CommandHandler("lock", lock_pc))
    app.add_handler(CommandHandler("cache", cache_cmd))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("হাইব্রিড রাউটার ও ভল্ট বট সফলভাবে চালু হয়েছে! 💻⚡")
    app.run_polling(bootstrap_retries=5)