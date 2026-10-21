import array
import re
import wave

from config import CONFIG
from .env import run

_model = {"key": None, "obj": None}


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


def _load(quality, gpu, log):
    from faster_whisper import WhisperModel
    name = CONFIG["whisper"].get(quality, CONFIG["whisper"]["balanced"])["gpu" if gpu else "cpu"]
    key = (name, bool(gpu))
    if _model["key"] != key:
        log(f"تحميل نموذج التفريغ {name}…")
        _model["obj"] = None
        dev, ct = ("cuda", "float16") if gpu else ("cpu", "int8")
        _model["obj"] = WhisperModel(name, device=dev, compute_type=ct)
        _model["key"] = key
    return _model["obj"]


def whisper(wav, quality, gpu, log, cancel, progress, language="ar"):
    model = _load(quality, gpu, log)
    segs, info = model.transcribe(str(wav), language=language, word_timestamps=True, vad_filter=True, beam_size=5)
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
    return {"words": words, "segments": segments, "source": "whisper"}


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
