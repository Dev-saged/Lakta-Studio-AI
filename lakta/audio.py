import shutil
import subprocess
import sys
from pathlib import Path

from .env import run

FF = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]


def _atempo(speed):
    # atempo يقبل 0.5-2 في الفلتر الواحد
    chain, s = [], float(speed)
    while s > 2.0:
        chain.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        chain.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-3:
        chain.append(f"atempo={s:.4f}")
    return chain


def segment(src, s, dur_src, speed, out, has_audio, cancel, edl=None):
    """صوت الفيديو الأصلي للمقطع، بعد القص (إن وُجدت قائمة قطع) والسرعة"""
    segs = (edl or {}).get("audio") or (edl or {}).get("segs")
    out_dur = (sum(b - a for a, b in segs) if segs else dur_src) / speed
    if not has_audio:
        run(FF + ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{out_dur:.3f}", str(out)], cancel=cancel)
        return out
    tail = ",".join(_atempo(speed) + ["aresample=48000"])
    if segs:
        n = len(segs)
        fc = [f"[0:a]aformat=channel_layouts=stereo,asplit={n}" + "".join(f"[a{i}]" for i in range(n))]
        for i, (a, b) in enumerate(segs):
            fd = min(0.012, (b - a) / 4)
            fc.append(f"[a{i}]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,afade=t=in:d={fd:.3f},"
                      f"afade=t=out:st={max(0, b - a - fd):.3f}:d={fd:.3f}[b{i}]")
        fc.append("".join(f"[b{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1,{tail}[out]")
        run(FF + ["-ss", f"{s:.3f}", "-t", f"{dur_src:.3f}", "-i", str(src), "-filter_complex", ";".join(fc),
                  "-map", "[out]", "-ac", "2", str(out)], cancel=cancel)
        return out
    run(FF + ["-ss", f"{s:.3f}", "-t", f"{dur_src:.3f}", "-i", str(src), "-vn", "-ac", "2", "-af", tail, str(out)], cancel=cancel)
    return out


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def fit(voice_wav, words, target, work, tag, cancel, max_tempo=1.3):
    """يضبط مدة الصوت الجديد على مدة اللقطة: تسريع خفيف إن طال"""
    v = duration(voice_wav)
    tempo = 1.0
    if v > target * 1.06:
        tempo = min(v / target, max_tempo)
    if tempo > 1.0:
        out = work / f"voice_{tag}_fit.wav"
        run(FF + ["-i", str(voice_wav), "-af", ",".join(_atempo(tempo)), str(out)], cancel=cancel)
        voice_wav = out
        words = [{"w": w["w"], "s": round(w["s"] / tempo, 3), "e": round(w["e"] / tempo, 3)} for w in words]
    return voice_wav, words, duration(voice_wav)


def separate(wav, work, gpu, log, cancel):
    """فصل صوت المتكلم عن الخلفية (Demucs) — يرجع مسار الخلفية أو None"""
    try:
        try:
            import demucs  # noqa
        except ImportError:
            log("تثبيت Demucs لفصل الصوت (مرة واحدة)…")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "demucs"], check=True)
        outdir = work / "sep"
        cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(outdir), str(wav)]
        if gpu:
            cmd += ["-d", "cuda"]
        try:
            run(cmd, cancel=cancel)
        except RuntimeError as e:
            # torchaudio الحديث يحتاج torchcodec لحفظ الصوت
            if "torchcodec" not in str(e).lower():
                raise
            log("تثبيت torchcodec لـ Demucs…")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "torchcodec"], check=True)
            run(cmd, cancel=cancel)
        found = list(outdir.rglob("no_vocals.wav"))
        if found:
            dst = work / f"{Path(wav).stem}_bg.wav"
            shutil.move(str(found[0]), dst)
            shutil.rmtree(outdir, ignore_errors=True)
            return dst
    except Exception as e:
        if e.__class__.__name__ == "Cancelled":
            raise
        log(f"تعذّر فصل الخلفية ({str(e)[:140]}) · سيُخفض الصوت الأصلي بدلاً منه")
    return None


# تنقية صوت المتكلم: قص الهمهمة، إزالة ضوضاء خفيفة، وضوح الحروف، تخفيف السين، وضغط ناعم
CLEAN_ORIG = "highpass=f=75,afftdn=nf=-28:tn=1,equalizer=f=180:t=q:w=1:g=-1.5,equalizer=f=3200:t=q:w=1.2:g=2,deesser=i=0.35,acompressor=threshold=-20dB:ratio=2.5:attack=8:release=120:makeup=2"
CLEAN_TTS = "highpass=f=70,equalizer=f=3200:t=q:w=1.2:g=1.5,acompressor=threshold=-18dB:ratio=2:attack=10:release=150"


def mix(mode, out, dur, orig=None, voice=None, bg=None, music=None, cancel=None, clean=True):
    final = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if mode == "original" and music:
        # موسيقى خفيفة تنخفض تلقائياً تحت كلام المتكلم
        cl = f"{CLEAN_ORIG}," if clean else ""
        fc = [f"[0:a]aresample=48000,aformat=channel_layouts=stereo,{cl}asplit=2[v][key]",
              "[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.16[u]",
              "[u][key]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[d]",
              f"[v][d]amix=inputs=2:duration=first:normalize=0,{final}[a]"]
        run(FF + ["-i", str(orig), "-stream_loop", "-1", "-i", str(music), "-filter_complex", ";".join(fc), "-map", "[a]",
                  "-t", f"{dur:.3f}", "-ac", "2", str(out)], cancel=cancel)
        return out
    if mode == "original":
        af = f"{CLEAN_ORIG},{final}" if clean else final
        run(FF + ["-i", str(orig), "-af", af, "-t", f"{dur:.3f}", "-ac", "2", str(out)], cancel=cancel)
        return out
    vc = f",{CLEAN_TTS}" if clean else ""
    inputs, fc = ["-i", str(voice)], [f"[0:a]aresample=48000,aformat=channel_layouts=stereo{vc},volume=1.0,asplit=2[v][key]"]
    under = None
    if mode == "bg":
        if bg:
            inputs += ["-i", str(bg)]
            fc.append("[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.9[u]")
        else:
            inputs += ["-i", str(orig)]
            fc.append("[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.10[u]")
        under = "[u]"
    elif music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        fc.append("[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.22[u]")
        under = "[u]"
    if under:
        fc.append(f"{under}[key]sidechaincompress=threshold=0.04:ratio=6:attack=20:release=300[d]")
        fc.append(f"[v][d]amix=inputs=2:duration=first:normalize=0,{final}[a]")
    else:
        fc.append(f"[v]{final}[a]")
        fc[0] = fc[0].replace(",asplit=2[v][key]", "[v]")
    run(FF + inputs + ["-filter_complex", ";".join(fc), "-map", "[a]", "-t", f"{dur:.3f}", "-ac", "2", str(out)], cancel=cancel)
    return out


# مؤثرات صوتية مولّدة محلياً (بدون حقوق): نفخة انتقال ونقرة للكلمات المفتاحية
SFX = {"whoosh": "anoisesrc=color=pink:d=0.5:a=0.6,highpass=f=300,lowpass=f=6000,afade=t=in:d=0.32:curve=exp,afade=t=out:st=0.32:d=0.18,volume=0.55",
       "pop": "sine=f=1100:d=0.09,afade=t=in:d=0.004,afade=t=out:st=0.012:d=0.078,volume=0.32"}


def _sfx_file(kind, assets):
    p = Path(assets) / f"sfx_{kind}.wav"
    if not p.exists():
        run(FF + ["-f", "lavfi", "-i", SFX[kind], "-ar", "48000", "-ac", "2", str(p)])
    return p


def add_sfx(mix_wav, out, events, assets, cancel=None):
    """events: [(kind, ثانية)] تُدمج فوق المزيج النهائي مع محدِّد ذروة"""
    if not events:
        return mix_wav
    inputs, fc = ["-i", str(mix_wav)], []
    for k, (kind, t) in enumerate(events, 1):
        ms = max(0, int(t * 1000))
        inputs += ["-i", str(_sfx_file(kind, assets))]
        fc.append(f"[{k}:a]adelay={ms}|{ms}[s{k}]")
    fc.append("[0:a]" + "".join(f"[s{k}]" for k in range(1, len(events) + 1)) +
              f"amix=inputs={len(events) + 1}:duration=first:normalize=0,alimiter=limit=0.95[a]")
    try:
        run(FF + inputs + ["-filter_complex", ";".join(fc), "-map", "[a]", "-ar", "48000", "-ac", "2", str(out)], cancel=cancel)
        return out
    except RuntimeError:
        return mix_wav


# ===== تحليل الموسيقى: الإيقاع والطاقة والمزاج =====
MOODS = {"calm": "هادئ", "neutral": "متوسط", "energetic": "حماسي"}


def analyze_music(path, cache):
    """BPM ومواضع النبضات وطاقة المقطع (يُحفظ في ذاكرة مؤقتة بحسب الاسم والحجم)"""
    import json
    import numpy as np
    key = f"{Path(path).name}:{Path(path).stat().st_size}"
    try:
        meta = json.loads(cache.read_text("utf-8"))
    except Exception:
        meta = {}
    if key in meta:
        return meta[key]
    sr, hop = 11025, 128
    raw = subprocess.run(FF[:1] + ["-v", "error", "-i", str(path), "-t", "240", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, timeout=120).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size < sr * 5:
        return {"bpm": 0, "mood": "neutral", "beats": [], "energy": 0}
    n = x.size // hop
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-9)
    onset = np.maximum(0, np.diff(rms, prepend=rms[0]))
    onset = (onset - onset.mean()) / (onset.std() + 1e-9)
    fps = sr / hop
    lags = np.arange(int(fps * 60 / 180), int(fps * 60 / 60) + 1)
    ac = np.array([np.dot(onset[:-l], onset[l:]) / (len(onset) - l) for l in lags])
    # تفضيل المدى الشائع حول 120 BPM لتجنّب اختيار نصف الإيقاع
    prior = np.exp(-0.5 * (np.log2((60 * fps / lags) / 120) / 0.9) ** 2)
    score = np.maximum(ac, 0) * prior
    k = int(np.argmax(score))
    lag = float(lags[k])
    if 0 < k < len(score) - 1:
        # استيفاء قطعي لدقة أعلى من حجم الإطار حتى لا تنزاح النبضات
        a, b, c = score[k - 1], score[k], score[k + 1]
        den = a - 2 * b + c
        lag += 0.5 * (a - c) / den if den else 0
    bpm = 60 * fps / lag
    il = int(round(lag))
    phase = int(np.argmax([sum(onset[min(n - 1, int(round(o + j * lag)))] for j in range(int((n - o) / lag))) for o in range(il)]))
    beats = [round(float((phase + j * lag) / fps), 3) for j in range(int((n - phase) / lag))]
    energy = float(20 * np.log10(rms.mean() + 1e-9))
    mood = "energetic" if bpm >= 118 and energy > -24 else "calm" if bpm < 92 or energy < -30 else "neutral"
    res = {"bpm": int(round(bpm)), "mood": mood, "beats": beats[:600], "energy": round(energy, 1)}
    meta[key] = res
    try:
        cache.write_text(json.dumps(meta), "utf-8")
    except Exception:
        pass
    return res


def pick_music(music_dir, mood, n):
    """أنسب مقطع من المكتبة لمزاج اللقطة"""
    files = sorted(p for p in Path(music_dir).glob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg"))
    if not files:
        return None, None
    info = {p: analyze_music(p, Path(music_dir) / ".meta.json") for p in files}
    match = [p for p in files if info[p]["mood"] == mood] or files
    p = match[(n - 1) % len(match)]
    return p, info[p]
