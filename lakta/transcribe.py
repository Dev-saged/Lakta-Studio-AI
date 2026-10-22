import array
import re
import threading
import time
import wave

from config import CONFIG
from .env import run

_model = {"key": None, "obj": None}
_lock = threading.Lock()


def extract_audio(src, out, cancel=None):
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out)], cancel=cancel)
    return out


def peaks(wav_path, n=300):
    # قمم الموجة الصوتية للخط الزمني في الواجهة
    with wave.open(str(wav_path), "rb") as w:
        frames = w.getnframes()
        step = max(1, frames // n)
        out = []
        for _ in range(n):
            buf = w.readframes(step)
            if not buf:
                break
            a = array.array("h", buf)
            out.append(max(abs(min(a)), abs(max(a))) if a else 0)
    top = max(out) or 1
    return [round(v / top, 3) for v in out]


def _pcm(wav_path):
    # تمرير الصوت كمصفوفة يتجاوز PyAV (إصداراته الحديثة لا تقبل metadata_errors)
    import numpy as np
    with wave.open(str(wav_path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise RuntimeError("صيغة الصوت غير متوقعة")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _load(quality, gpu, log):
    from faster_whisper import WhisperModel
    name = CONFIG["whisper"].get(quality, CONFIG["whisper"]["balanced"])["gpu" if gpu else "cpu"]
    key = (name, bool(gpu))
    with _lock:
        if _model["key"] != key:
            log(f"تحميل نموذج التفريغ {name}…")
            _model["obj"] = None
            dev, ct = ("cuda", "float16") if gpu else ("cpu", "int8")
            _model["obj"] = WhisperModel(name, device=dev, compute_type=ct)
            _model["key"] = key
        return _model["obj"]


def preload(quality, gpu):
    """تحميل النموذج بالتوازي مع تنزيل الفيديو"""
    def go():
        try:
            _load(quality, gpu, lambda *_: None)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def whisper(wav, quality, gpu, log, cancel, progress, language="ar"):
    audio = _pcm(wav)
    try:
        return _run(audio, quality, gpu, log, cancel, progress, language)
    except Exception as e:
        if not gpu or not any(k in str(e).lower() for k in ("cuda", "cudnn", "cublas", "gpu")):
            raise
        log(f"تعذّر التفريغ على GPU ({str(e)[:100]}) · إكمال على المعالج")
        return _run(audio, quality, False, log, cancel, progress, language)


def _collect(segs, info, cancel, progress):
    words, segments = [], []
    for seg in segs:
        if cancel():
            break
        segments.append({"s": round(seg.start, 2), "e": round(seg.end, 2), "t": seg.text.strip()})
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append({"w": t, "s": round(w.start, 2), "e": round(w.end, 2)})
        if info.duration:
            progress(min(0.99, seg.end / info.duration))
    return words, segments


def _run(audio, quality, gpu, log, cancel, progress, language):
    model = _load(quality, gpu, log)
    t0 = time.time()
    opts = dict(language=language, word_timestamps=True, vad_filter=True, beam_size=5)
    res = None
    # دفعات متوازية: نفس النموذج ونفس التوقيت كلمة بكلمة، أسرع ×2 على المعالج وأكثر على كرت الشاشة
    try:
        from faster_whisper import BatchedInferencePipeline
        segs, info = BatchedInferencePipeline(model=model).transcribe(
            audio, batch_size=CONFIG.get("whisper_batch", 16) if gpu else 8, **opts)
        res = _collect(segs, info, cancel, progress)
    except Exception as e:
        if e.__class__.__name__ == "Cancelled":
            raise
        log(f"التفريغ الدفعي غير متاح ({str(e)[:80]}) · التفريغ المتسلسل")
    if res is None:
        segs, info = model.transcribe(audio, **opts)
        res = _collect(segs, info, cancel, progress)
    took = max(0.1, time.time() - t0)
    if info.duration:
        log(f"التفريغ: {took:.0f}ث لـ {info.duration / 60:.1f} دقيقة · أسرع من الزمن الحقيقي {info.duration / took:.0f}×")
    return {"words": res[0], "segments": res[1], "source": "whisper"}


def from_vtt(vtt_path):
    import webvtt
    words, segments, last = [], [], []
    for cap in webvtt.read(vtt_path):
        text = re.sub(r"<[^>]+>", "", cap.text).replace("\n", " ").strip()
        cur = text.split()
        if not cur:
            continue
        ov = 0
        for i in range(min(len(last), len(cur)), 0, -1):
            if last[-i:] == cur[:i]:
                ov = i
                break
        last = cur
        new = cur[ov:]
        if not new:
            continue
        s, e = cap.start_in_seconds, cap.end_in_seconds
        total = sum(len(w) for w in new) or 1
        t = s
        for w in new:
            d = (e - s) * len(w) / total
            words.append({"w": w, "s": round(t, 2), "e": round(t + d, 2)})
            t += d
        segments.append({"s": round(s, 2), "e": round(e, 2), "t": " ".join(new)})
    return {"words": words, "segments": segments, "source": "youtube"}


def transcribe(wav, vtt, quality, gpu, log, cancel, progress):
    try:
        return whisper(wav, quality, gpu, log, cancel, progress)
    except Exception as e:
        if vtt:
            log(f"تعذّر Whisper ({str(e)[:120]}) · استخدام ترجمة يوتيوب")
            return from_vtt(vtt)
        raise


def regroup(words, max_gap=0.8, max_len=14.0):
    # جُمل زمنية من الكلمات (لتوجيه المونتير)
    segs, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > max_gap or w["e"] - cur[0]["s"] > max_len or cur[-1]["w"][-1:] in ".؟?!"):
            segs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return [{"s": c[0]["s"], "e": c[-1]["e"], "t": " ".join(x["w"] for x in c)} for c in segs]
