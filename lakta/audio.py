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


def segment(src, s, dur_src, speed, out, has_audio, cancel):
    """صوت الفيديو الأصلي للمقطع، بعد تطبيق السرعة"""
    out_dur = dur_src / speed
    if not has_audio:
        run(FF + ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{out_dur:.3f}", str(out)], cancel=cancel)
        return out
    af = ",".join(_atempo(speed) + ["aresample=48000"])
    run(FF + ["-ss", f"{s:.3f}", "-t", f"{dur_src:.3f}", "-i", str(src), "-vn", "-ac", "2", "-af", af, str(out)], cancel=cancel)
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


def mix(mode, out, dur, orig=None, voice=None, bg=None, music=None, cancel=None):
    final = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if mode == "original":
        run(FF + ["-i", str(orig), "-af", final, "-t", f"{dur:.3f}", "-ac", "2", str(out)], cancel=cancel)
        return out
    inputs, fc = ["-i", str(voice)], ["[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=1.0,asplit=2[v][key]"]
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
