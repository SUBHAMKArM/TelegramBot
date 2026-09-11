import io
import os
import re
import asyncio
import av
import speech_recognition as sr
import edge_tts
import pyttsx3

# Optional faster-whisper for offline fallback
_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        except Exception as e:
            print(f"Whisper init error: {e}")
    return _whisper_model

# ---------- PyAV in-memory converter ----------
def convert_to_wav(audio_input) -> io.BytesIO:
    """Converts any audio file/bytes (ogg, opus, mp3, wav, etc.) to 16kHz mono WAV."""
    if isinstance(audio_input, (bytes, bytearray)):
        inp = av.open(io.BytesIO(audio_input))
    else:
        inp = av.open(audio_input)

    out_buf = io.BytesIO()
    out = av.open(out_buf, 'w', format='wav')
    out_stream = out.add_stream('pcm_s16le', rate=16000, layout='mono')

    resampler = av.audio.resampler.AudioResampler(format='s16', layout='mono', rate=16000)

    for frame in inp.decode(audio=0):
        for rf in resampler.resample(frame):
            for packet in out_stream.encode(rf):
                out.mux(packet)
    for rf in resampler.resample(None):
        for packet in out_stream.encode(rf):
            out.mux(packet)
    for packet in out_stream.encode(None):
        out.mux(packet)

    out.close()
    out_buf.seek(0)
    return out_buf

# ---------- Speech-to-Text (STT) ----------
def choose_best_transcript(res_bn: str, res_hi: str, res_en: str) -> tuple[str, str]:
    """Determines whether the audio was English, Bengali, or Hindi."""
    common_en = {
        'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'i', 'it', 'for', 'not', 'on', 'with', 
        'he', 'as', 'you', 'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her', 
        'she', 'or', 'an', 'will', 'my', 'one', 'all', 'would', 'there', 'their', 'what', 'so', 'up', 
        'out', 'if', 'about', 'who', 'get', 'which', 'go', 'me', 'when', 'make', 'can', 'like', 'time', 
        'no', 'just', 'him', 'know', 'take', 'people', 'into', 'year', 'your', 'good', 'some', 'could', 
        'them', 'see', 'other', 'than', 'then', 'now', 'look', 'only', 'come', 'its', 'over', 'think', 
        'also', 'back', 'after', 'use', 'two', 'how', 'our', 'work', 'first', 'well', 'way', 'even', 
        'new', 'want', 'because', 'any', 'these', 'give', 'day', 'most', 'us', 'weather', 'report', 'hello', 'hi'
    }
    
    en_tokens = set(re.findall(r'[a-zA-Z]+', res_en.lower()))
    en_matches = len(en_tokens & common_en)
    if len(en_tokens) > 0 and (en_matches / len(en_tokens) >= 0.5 or en_matches >= 2):
        return res_en, "English"

    bn_score = sum(1 for w in ['কেমন', 'কি', 'কী', 'আছে', 'করো', 'দাও', 'বলো', 'কটা', 'বাজে', 'সময়', 'আবহাওয়া', 'আজকের', 'নমস্কার', 'হ্যালো', 'ধন্যবাদ'] if w in res_bn)
    hi_score = sum(1 for w in ['कैसा', 'क्या', 'है', 'करो', 'दो', 'बोलो', 'कितना', 'बजा', 'समय', 'मौसम', 'आज', 'नमस्ते', 'हेलो', 'धन्यवाद'] if w in res_hi)

    if hi_score > bn_score:
        return res_hi, "Hindi"
    if bn_score > hi_score:
        return res_bn, "Bengali"

    if res_bn:
        return res_bn, "Bengali"
    if res_hi:
        return res_hi, "Hindi"
    return res_en, "English"

def transcribe_audio(audio_data) -> tuple[str, str]:
    """
    Transcribes audio to text in Bengali, Hindi, or English.
    Returns: (transcribed_text, language_detected)
    """
    wav_buf = convert_to_wav(audio_data)
    r = sr.Recognizer()

    res_bn, res_hi, res_en = "", "", ""

    # Try all three languages
    for lang_code in ["bn-IN", "hi-IN", "en-IN"]:
        try:
            wav_buf.seek(0)
            with sr.AudioFile(wav_buf) as source:
                audio = r.record(source)
            text = r.recognize_google(audio, language=lang_code).strip()
            if lang_code == "bn-IN": res_bn = text
            elif lang_code == "hi-IN": res_hi = text
            elif lang_code == "en-IN": res_en = text
        except Exception:
            pass

    if res_bn or res_hi or res_en:
        return choose_best_transcript(res_bn, res_hi, res_en)

    # Offline fallback: faster-whisper
    try:
        model = get_whisper_model()
        if model:
            wav_buf.seek(0)
            segments, info = model.transcribe(wav_buf, beam_size=3)
            w_text = " ".join([seg.text for seg in segments]).strip()
            lang_map = {"bn": "Bengali", "hi": "Hindi", "en": "English"}
            det_lang = lang_map.get(info.language, info.language)
            if w_text:
                return w_text, det_lang
    except Exception as e:
        print(f"Offline whisper error: {e}")

    return "", "unknown"

# ---------- Text-to-Speech (TTS) ----------
def detect_script_language(text: str) -> str:
    """Detects if text is predominantly Bengali, Hindi/Devanagari, or English."""
    if re.search(r'[\u0980-\u09FF]', text):
        return "bn"
    if re.search(r'[\u0900-\u097F]', text):
        return "hi"
    return "en"

async def text_to_speech(text: str, output_path: str) -> bool:
    """
    Generates natural voice for Bengali, Hindi, or English.
    Uses edge-tts (ultra-realistic neural voice) with local pyttsx3 fallback.
    """
    lang = detect_script_language(text)
    
    # Choose natural neural voice
    if lang == "bn":
        voice = "bn-IN-BashkarNeural"
    elif lang == "hi":
        voice = "hi-IN-MadhurNeural"
    else:
        voice = "en-IN-NeerjaNeural"

    # Clean markdown formatting so TTS reads fluently
    clean = re.sub(r'[*_`#~\[\]\(\)]', '', text)
    clean = re.sub(r'http\S+', '', clean)
    clean = re.sub(r'[^\w\s\d।,.\?\!\-\:—]', '', clean).strip()
    if not clean:
        clean = "Message received."

    # 1. Try edge-tts
    try:
        communicate = edge_tts.Communicate(clean[:1500], voice)
        await communicate.save(output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 500:
            return True
    except Exception as e:
        print(f"edge-tts failed: {e}")

    # 2. Offline pyttsx3 fallback
    try:
        engine = pyttsx3.init()
        engine.save_to_file(clean[:500], output_path)
        engine.runAndWait()
        return os.path.exists(output_path) and os.path.getsize(output_path) > 500
    except Exception as e:
        print(f"pyttsx3 failed: {e}")
        return False
