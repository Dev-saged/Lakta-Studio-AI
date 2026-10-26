"""تجميع فيديو من سكربت: صوت لكل مشهد + لقطة مخزون (فيديو ← صورة بحركة ← خلفية مولَّدة) → ملف مصدر واحد"""
import random

from . import stock, voice
from .env import ffprobe, run

FPS = 30
XFADE = 0.35
TRANSITIONS = ("fade", "smoothleft", "smoothright", "fadegrays", "circleopen")
SIZES = {"short": (1080, 1920), "wide": (1920, 1080)}


def canvas(opts):
    return SIZES["short"] if opts["formats"].get("short") else SIZES["wide"]


def _hex(c, default):
    c = str(c or default).lstrip("#")
    return "0x" + (c if len(c) == 6 else default.lstrip("#"))


def _enc(out):
    return ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)]


def _video_seg(src, out, W, H, dur, cancel):
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
          f"eq=brightness=-0.03:saturation=1.06,format=yuv420p")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src), "-t", f"{dur:.3f}", "-vf", vf] + _enc(out), cancel=cancel)


def _image_seg(src, out, W, H, dur, idx, cancel):
    frames = max(2, int(dur * FPS))
    # حركة بطيئة: تقريب أو ابتعاد بالتناوب مع انزياح خفيف
    z = "min(zoom+0.0007,1.22)" if idx % 2 == 0 else "if(eq(on,0),1.22,max(zoom-0.0007,1.0))"
    x = "iw/2-(iw/zoom/2)+on*0.15" if idx % 3 == 0 else "iw/2-(iw/zoom/2)"
    vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
          f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(src), "-t", f"{dur:.3f}", "-vf", vf] + _enc(out), cancel=cancel)


def _generated_seg(out, W, H, dur, idx, brand, cancel):
    """خلفية متحركة بألوان الهوية — تضمن أن الفيديو لا يتعطل بلا مفتاح أو بلا نتائج"""
    t = brand.get("template") or {}
    c0, c1 = _hex(t.get("base"), "#0E1B3D"), _hex(t.get("accent"), "#C9A24A")
    if idx % 2:
        c0, c1 = c1, c0
    vf = f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=0:y0=0:x1={W}:y1={H}:speed=0.015:d={dur:.3f}:r={FPS},eq=brightness=-0.05:saturation=1.15,format=yuv420p"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", vf] + _enc(out), cancel=cancel)


def _pick(settings, scene, orient, used, log):
    """أفضل لقطة غير مستخدمة: فيديو بالكلمة الأولى والبديلة ثم صورة"""
    for kind in ("video", "image"):
        for q in (scene["query"], scene.get("alt_query")):
            if not q:
                continue
            for c in stock.search(settings, q, orient, kind, 8, log):
                if (c["provider"], c["id"]) not in used and (kind == "image" or c["dur"] == 0 or c["dur"] >= 2):
                    return c
    return None


def build(env, settings, script, opts, work, log, cancel, progress):
    """يرجع (src, words, used_clips). src بنفس شكل ingest.* ليدخل خط الإنتاج كمصدر عادي"""
    W, H = canvas(opts)
    orient = "portrait" if H > W else "landscape"
    a = opts["audio"]
    brand = settings.data["brand"]
    vopts = {"engine": a["engine"], "voice": a["voice"], "dialect": a["dialect"]}
    scenes = script["scenes"]
    n = len(scenes)
    used, used_clips, segs, wavs, words, durs = set(), [], [], [], [], []
    t0 = 0.0
    for i, sc in enumerate(scenes):
        cancel and cancel()
        log(f"المشهد {i + 1} من {n}: صوت ولقطة")
        wav, w = voice.synth(settings, sc["narration"], vopts, work, f"scene{i}", bool(env.gpu), log, cancel)
        d = ffprobe(wav)["duration"]
        words += [{"w": x["w"], "s": round(x["s"] + t0, 3), "e": round(x["e"] + t0, 3)} for x in w]
        wavs.append(wav)
        durs.append(d)
        t0 += d
        seg = work / f"seg{i}.mp4"
        length = d + XFADE
        clip = None
        try:
            clip = _pick(settings, sc, orient, used, log)
            if clip:
                path = stock.fetch(env, clip, log)
                (_video_seg(path, seg, W, H, length, cancel) if clip["kind"] == "video" else _image_seg(path, seg, W, H, length, i, cancel))
                used.add((clip["provider"], clip["id"]))
                used_clips.append(clip)
        except stock.ProviderError as e:
            log(f"تعذّرت لقطة المشهد {i + 1}: {e}")
            clip = None
        if not clip:
            _generated_seg(seg, W, H, length, i, brand, cancel)
        segs.append(seg)
        progress((i + 1) / (n + 1))

    # دمج المشاهد بانتقالات ناعمة؛ كل مقطع ممدود بمقدار الانتقال فيبقى طول الفيديو = طول الصوت
    inputs = []
    for s in segs:
        inputs += ["-i", str(s)]
    chain, cur, off = [], "[0:v]", 0.0
    rnd = random.Random(sum(map(ord, script["title"])))
    for i in range(1, n):
        off += durs[i - 1]
        out = f"[x{i}]"
        chain.append(f"{cur}[{i}:v]xfade=transition={rnd.choice(TRANSITIONS)}:duration={XFADE}:offset={off:.3f}{out}")
        cur = out
    narr = work / "narration.wav"
    alist = work / "narr.txt"
    alist.write_text("".join(f"file '{w}'\n" for w in wavs), "utf-8")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(alist), "-c", "copy", str(narr)], cancel=cancel)
    total = sum(durs)
    final = work / "topic_source.mp4"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs + ["-i", str(narr)]
    if chain:
        fc = work / "topic_fc.txt"
        fc.write_text(";".join(chain), "utf-8")
        cmd += ["-filter_complex_script", str(fc), "-map", cur]
    else:
        cmd += ["-map", "0:v"]
    cmd += ["-map", f"{n}:a", "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(final)]
    run(cmd, cancel=cancel)
    progress(1.0)
    for s in segs:
        s.unlink(missing_ok=True)
    src = {"path": str(final), "title": script["title"], "description": script.get("description", ""), "url": "", "vtt": None}
    return src, words, used_clips, narr
