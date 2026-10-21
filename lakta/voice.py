import asyncio
import base64
import hashlib
import threading
import wave

from config import CONFIG
from .env import run
from . import director

PREVIEW_TEXT = {
    "original": "أهلاً بك في استوديو لقطة، هذا صوتي في لقطاتك القادمة.",
    "msa": "أهلاً بك في استوديو لقطة، هذا صوتي في لقطاتك القادمة.",
    "gulf": "هلا والله، هذا صوتي في مقاطعك الجاية من استوديو لقطة.",
    "egy": "أهلاً بيك، ده صوتي في المقاطع الجاية من استوديو لقطة.",
    "levant": "أهلين فيك، هاد صوتي بالمقاطع الجاية من استوديو لقطة.",
    "maghreb": "مرحبا بيك، هادا صوتي فالمقاطع الجاية ديال استوديو لقطة.",
}
STYLE = {"original": "", "msa": "بالعربية الفصحى", "gulf": "باللهجة الخليجية", "egy": "باللهجة المصرية",
         "levant": "باللهجة الشامية", "maghreb": "بالدارجة المغربية"}


def _run_async(coro):
    # تشغيل آمن حتى داخل دفتر Jupyter الذي يملك حلقة أحداث
    box = {}

    def target():
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as e:  # noqa
            box["e"] = e
    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


def edge(text, voice, out_mp3):
    import edge_tts

    async def go():
        words = []
        try:
            com = edge_tts.Communicate(text, voice, boundary="WordBoundary")
        except TypeError:
            com = edge_tts.Communicate(text, voice)
        with open(out_mp3, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    s = ch["offset"] / 1e7
                    words.append({"w": ch["text"], "s": round(s, 3), "e": round(s + ch["duration"] / 1e7, 3)})
        return words
    return _run_async(go())


def _pcm_to_wav(data, path, rate=24000):
    if data[:4] == b"RIFF":
        path.write_bytes(data)
        return
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)


def gemini(settings, text, voice, dialect, out_wav):
    from google.genai import types
    key = settings.get("gemini_key")
    model = director.resolve(settings, "tts")
    c = director.client(key)
    style = f"اقرأ {STYLE.get(dialect, '')} بصوت معلّق محتوى حماسي وواضح وبإيقاع طبيعي:".replace("  ", " ")
    try:
        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
        r = c.models.generate_content(model=model, contents=f"{style}\n{text}", config=cfg)
        data = r.candidates[0].content.parts[0].inline_data.data
        if isinstance(data, str):
            data = base64.b64decode(data)
    except Exception as first:
        if not hasattr(c, "interactions"):
            raise
        try:
            it = c.interactions.create(model=model, input=[{"type": "text", "text": text, "speech_metadata": {"style": style}}],
                                       response_format={"type": "audio"}, voice=voice)
            data = base64.b64decode(it.output_audio.data)
        except Exception:
            raise first
    _pcm_to_wav(data, out_wav)


def synth(settings, text, opts, work, tag, gpu, log, cancel):
    """يولّد الصوت ويرجع (مسار wav بتردد 48k، توقيت الكلمات)"""
    out = work / f"voice_{tag}.wav"
    if opts["engine"] == "gemini":
        raw = work / f"voice_{tag}_raw.wav"
        gemini(settings, text, opts["voice"], opts["dialect"], raw)
        words = None
    else:
        raw = work / f"voice_{tag}.mp3"
        words = edge(text, opts["voice"], raw)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-af",
         "areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse,apad=pad_dur=0.25",
         "-ar", "48000", "-ac", "2", str(out)], cancel=cancel)
    if not words:
        words = align(out, work, tag, gpu, log, cancel)
    return out, words


def align(wav, work, tag, gpu, log, cancel):
    from . import transcribe
    mono = transcribe.extract_audio(wav, work / f"align_{tag}.wav", cancel)
    res = transcribe.whisper(mono, "fast", gpu, log, cancel, lambda p: None)
    return res["words"]


def preview(settings, engine, voice, dialect, cache_dir):
    text = PREVIEW_TEXT.get(dialect, PREVIEW_TEXT["msa"])
    h = hashlib.sha1(f"{engine}|{voice}|{dialect}".encode()).hexdigest()[:12]
    out = cache_dir / f"preview_{h}.mp3"
    if out.exists():
        return out
    if engine == "gemini":
        tmp = cache_dir / f"preview_{h}.wav"
        gemini(settings, text, voice, dialect, tmp)
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(tmp), "-b:a", "96k", str(out)])
        tmp.unlink(missing_ok=True)
    else:
        edge(text, voice, out)
    return out


def voices():
    return {"edge": CONFIG["edge_voices"], "gemini": CONFIG["gemini_voices"],
            "dialects": {k: v["label"] for k, v in CONFIG["dialects"].items()}}
