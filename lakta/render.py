from pathlib import Path

from config import CONFIG
from .env import encoder_args, enhance_filters, filter_path, run
from . import reframe
from .edit import ZOOM

SIZES = {("short", 1080): (1080, 1920), ("short", 720): (720, 1280), ("wide", 1080): (1920, 1080), ("wide", 720): (1280, 720),
         ("square", 1080): (1080, 1080), ("square", 720): (720, 720), ("portrait", 1080): (1080, 1350), ("portrait", 720): (720, 900)}
CROPPED = ("short", "square", "portrait")
WM_POS = {"tr": ("W-w-{p}", "{p}"), "tl": ("{p}", "{p}"), "br": ("W-w-{p}", "H-h-{p}"), "bl": ("{p}", "H-h-{p}")}


_smooth_ok = {}
_filters = {}


def has_filter(name):
    if "list" not in _filters:
        import subprocess
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
            _filters["list"] = {ln.split()[1] for ln in out.splitlines() if len(ln.split()) > 2 and ln.startswith(" ")}
        except Exception:
            _filters["list"] = set()
    return name in _filters["list"]


def stab_prep(src, s, dur_src, work, tag, cancel=None):
    """تثبيت الصورة: تحليل vidstab بمرور أول، أو deshake بمرور واحد إن لم يتوفر"""
    if has_filter("vidstabdetect") and has_filter("vidstabtransform"):
        trf = work / f"stab_{tag}.trf"
        try:
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{s:.3f}", "-t", f"{dur_src:.3f}", "-i", str(src),
                 "-vf", f"vidstabdetect=shakiness=5:accuracy=12:result='{filter_path(trf)}'", "-f", "null", "-"], cancel=cancel)
            return ("vidstab", trf)
        except RuntimeError:
            pass
    return ("deshake", None) if has_filter("deshake") else None


def smooth_zoom_ok():
    """زوم متدرج يحتاج متغير الزمن في scale (ffmpeg 5+)؛ وإلا يُستخدم الزوم الثابت"""
    if "v" not in _smooth_ok:
        import subprocess
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=64x64:d=0.3",
                            "-vf", "scale=w='trunc(64*(1+t)/2)*2':h='trunc(64*(1+t)/2)*2':eval=frame,crop=64:64",
                            "-f", "null", "-"], check=True, capture_output=True, timeout=30)
            _smooth_ok["v"] = True
        except Exception:
            _smooth_ok["v"] = False
    return _smooth_ok["v"]


def _zoom_filter(W, H, dur, zoomed, punches, smooth):
    if not smooth:
        return f",crop=iw/{ZOOM}:ih/{ZOOM},scale={W}:{H}:flags=lanczos,setsar=1" if zoomed else ""
    # دفع بطيء للأمام بمنحنى ناعم + نبضة زوم عند الكلمات المفتاحية
    z = f"1.04+0.08*(1-pow(1-min(t/{max(dur, 0.3):.3f},1),3))" if zoomed else "1"
    for tk in punches[:2]:
        z += f"+0.06*exp(-pow((t-{tk:.3f})/0.22,2))"
    if z == "1":
        return ""
    return (f",scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame:flags=bicubic,"
            f"crop={W}:{H},setsar=1")


def hex_color(c):
    c = (c or "#E8402A").lstrip("#")
    return "0x" + (c if len(c) == 6 and all(x in "0123456789abcdefABCDEF" for x in c) else "E8402A")


def clip(src, out, fmt, res, s, dur_src, out_dur, speed, audio_wav, ass_path, fonts_dir, brand, watermark,
         crop=None, work=None, cancel=None, progress=None, template=None, tpl=None, quality=None, edl=None, codec="h264",
         look=None, fill=False, fx=None, motion=None, motion_assets=None):
    """template: قالب PNG مرفوع · tpl: قالب مولَّد {static, prog, cta} · motion: تعبير geq اختياري لوميض فخم"""
    W, H = SIZES[(fmt, 720 if int(res) <= 720 else 1080)]
    D = f"{out_dur:.3f}"
    v = []
    look = look or {}
    pre_parts, pre = [], []
    stab = look.get("stab")
    if stab:
        pre.append(f"vidstabtransform=input='{filter_path(stab[1])}':smoothing=18:optzoom=1:interpol=bicubic"
                   if stab[0] == "vidstab" else "deshake=rx=32:ry=32:edge=mirror")
    if look.get("src_h") and H / look["src_h"] > 1.25:
        # مصدر أقل دقة من المخرج: إزالة تشويش الضغط قبل التكبير
        pre.append("hqdn3d=1.2:1.2:4:4")
    base = "[0:v]"
    if pre:
        pre_parts.append(f"[0:v]{','.join(pre)}[base]")
        base = "[base]"
    src_label = base
    if fmt in CROPPED and fill:
        # خلفية ضبابية: الإطار العريض كاملاً فوق نسخة مكبّرة ومموّهة منه
        pre_parts.append(f"{base}split[fa][fb];[fa]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                         f"gblur=sigma=12,eq=brightness=-0.07:saturation=1.1,scale={W}:{H}[fbg];[fb]scale={W}:-2:flags=lanczos[ffg];"
                         f"[fbg][ffg]overlay=(W-w)/2:(H-h)/2-H*0.04[fill]")
        src_label = "[fill]"
    elif fmt == "short" and isinstance(crop, dict):
        # شاشة مقسومة: المتكلم الأول أعلى والثاني أسفل، بينهما خط رفيع
        (x1, y1), (x2, y2) = crop["split"]
        cw, ch, hh = crop["w"], crop["h"], H // 2
        pre_parts.append(f"{base}split[sa][sb];[sa]crop={cw}:{ch}:{x1}:{y1},scale={W}:{hh}:force_original_aspect_ratio=increase:flags=lanczos,"
                         f"crop={W}:{hh},setsar=1[st];[sb]crop={cw}:{ch}:{x2}:{y2},scale={W}:{hh}:force_original_aspect_ratio=increase:"
                         f"flags=lanczos,crop={W}:{hh},setsar=1[sbt];[st][sbt]vstack,drawbox=x=0:y={hh - 3}:w=iw:h=6:color=black@0.85:t=fill[split]")
        src_label = "[split]"
    elif fmt in CROPPED and crop and not isinstance(crop, dict):
        keys, cw = crop
        v.append(f"crop=w={cw}:h=ih:x='{reframe.expr(keys)}':y=0")
        v.append(f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}")
    else:
        v.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black")
    v += enhance_filters(quality, look.get("grade"), look.get("grain"), look.get("auto")) + ["setsar=1"]
    post = [f"setpts=(PTS-STARTPTS)/{speed:.4f}", "fps=30"]
    if (fx or {}).get("flash") is not None:
        # وميض انتقال عند نهاية الافتتاحية
        t = float(fx["flash"])
        post.append(f"eq=brightness=0.32:contrast=1.1:enable='between(t,{max(0.0, t - 0.05):.3f},{t + 0.09:.3f})'")
    overlay_png = template or (tpl or {}).get("static")
    if brand.get("frame_on") and not overlay_png:
        t = max(8, W // 50)
        col = hex_color(brand.get("frame_color"))
        post.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={col}:t={t}")
        post.append(f"drawbox=x={t}:y={t}:w=iw-{2 * t}:h=ih-{2 * t}:color=white:t={max(2, t // 4)}")
    inputs = ["-ss", f"{s:.3f}", "-t", f"{dur_src:.3f}", "-i", str(src), "-i", str(audio_wav)]
    idx, cur = 2, "[v0]"
    if edl and edl.get("segs"):
        # قص وإعادة ترتيب بالمقاطع: صمت محذوف، افتتاحية مكررة، وزوم متناوب
        n = len(edl["segs"])
        parts = pre_parts + [f"{src_label}{','.join(v)},split={n}" + "".join(f"[e{i}]" for i in range(n))]
        smooth = bool(edl.get("smooth")) and smooth_zoom_ok()
        kw = edl.get("kw") or []
        for i, ((a, b), z) in enumerate(zip(edl["segs"], edl["zoom"])):
            zf = _zoom_filter(W, H, b - a, z, [k - a for k in kw if a <= k < b], smooth)
            parts.append(f"[e{i}]trim=start={a}:end={b},setpts=PTS-STARTPTS{zf}[c{i}]")
        parts.append("".join(f"[c{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,{','.join(post)}[v0]")
    else:
        parts = pre_parts + [f"{src_label}{','.join(v + post)}[v0]"]

    def step(chain, label):
        nonlocal cur
        parts.append(chain.format(cur=cur, out=label))
        cur = label

    if overlay_png:
        inputs += ["-loop", "1", "-t", D, "-i", str(overlay_png)]
        parts.append(f"[{idx}:v]scale={W}:{H},format=rgba[tp]")
        step("{cur}[tp]overlay=0:0:format=auto{out}", "[v1]")
        idx += 1
    if motion:
        ring_png, glow_png = motion_assets
        hexcol = hex_color((brand.get("template") or {}).get("accent") or brand.get("frame_color")).replace("0x", "")
        tr, tg, tb = (int(hexcol[i:i + 2], 16) / 255 for i in (0, 2, 4))
        for k, cq in enumerate(motion):
            asset = ring_png if cq["asset"] == "ring" else glow_png
            inputs += ["-loop", "1", "-t", D, "-i", str(asset)]
            w0, w1, t0, dur = cq["w0"] * W, cq["w1"] * W, cq["t0"], cq["dur"]
            sw = f"trunc(({w0:.1f}+{w1 - w0:.1f}*min(max((t-{t0:.3f})/{dur:.3f},0),1))/2)*2"
            parts.append(f"[{idx}:v]scale=w='{sw}':h=-1:eval=frame,format=rgba,"
                         f"colorchannelmixer=rr={tr:.3f}:rg=0:rb=0:gr=0:gg={tg:.3f}:gb=0:br=0:bg=0:bb={tb:.3f}:aa=1,"
                         f"fade=t=in:st={t0:.3f}:d={cq['fin']:.3f}:alpha=1,"
                         f"fade=t=out:st={t0 + dur - cq['fout']:.3f}:d={cq['fout']:.3f}:alpha=1[mo{k}]")
            step(f"{{cur}}[mo{k}]overlay=(W-w)/2:{cq['cy']:.3f}*H-h/2:format=auto:enable='between(t,{t0:.3f},{t0 + dur:.3f})'{{out}}", f"[vm{k}]")
            idx += 1
    step(f"{{cur}}ass='{filter_path(ass_path)}':fontsdir='{filter_path(fonts_dir)}':shaping=complex{{out}}", "[v2]")
    pg = (tpl or {}).get("prog")
    if pg:
        col = hex_color(pg["color"])
        mix = f",colorchannelmixer=aa={pg['alpha']}" if pg["alpha"] < 1 else ""
        pos = f"x=0:y='main_h-overlay_h*t/{D}'" if pg["vertical"] else f"x='main_w-overlay_w*t/{D}':y=0"
        parts.append(f"color=c={col}:s={pg['w']}x{pg['h']}:d={D}:r=30,format=rgba{mix}[pf]")
        parts.append(f"color=c=black@0.0:s={pg['w']}x{pg['h']}:d={D}:r=30,format=rgba[pt]")
        parts.append(f"[pt][pf]overlay={pos}:format=auto[pb]")
        step(f"{{cur}}[pb]overlay={pg['x']}:{pg['y']}:format=auto{{out}}", "[v3]")
    cta = (tpl or {}).get("cta")
    if cta:
        st = max(0.0, out_dur - CONFIG["template"]["cta_seconds"])
        inputs += ["-loop", "1", "-t", D, "-i", str(cta)]
        parts.append(f"[{idx}:v]format=rgba,fade=t=in:st={st:.2f}:d=0.45:alpha=1[ct]")
        step(f"{{cur}}[ct]overlay=0:0:format=auto:enable='gte(t,{st:.2f})'{{out}}", "[v4]")
        idx += 1
    if watermark:
        p = int(W * 0.03)
        x, y = (e.format(p=p) for e in WM_POS.get(brand.get("watermark_pos"), WM_POS["tr"]))
        inputs += ["-i", str(watermark)]
        parts.append(f"[{idx}:v]scale={int(W * 0.15)}:-1,format=rgba,colorchannelmixer=aa=0.85[wm]")
        step(f"{{cur}}[wm]overlay=x={x}:y={y}:format=auto{{out}}", "[v5]")
        idx += 1
    script = work / f"fc_{out.stem}.txt"
    script.write_text(";".join(parts), "utf-8")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex_script", str(script),
           "-map", cur, "-map", "1:a", "-t", D, *encoder_args(quality, codec=codec),
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    # محاولات احتياطية: نفس الترميز على المعالج ثم H.264 (قد تنقص ffmpeg خيارات NVENC أو libx265)
    attempts = []
    for a in (encoder_args(quality, codec=codec), encoder_args(quality, True, codec), encoder_args(quality, True, "h264")):
        if a not in attempts:
            attempts.append(a)
    for k, args in enumerate(attempts):
        i, j = cmd.index("-c:v"), cmd.index("-pix_fmt")
        cmd[i:j] = args
        try:
            run(cmd, cancel=cancel, progress=progress, total=out_dur)
            return out
        except RuntimeError:
            if k == len(attempts) - 1:
                raise
    return out


def bumper(clip_path, W, H, intro=None, outro=None, quality=None, codec="h264", cancel=None):
    """لصق مقدمة وختام القناة حول اللقطة بنفس المقاس والصوت"""
    from .env import ffprobe
    parts = [p for p in (intro, clip_path, outro) if p]
    if len(parts) == 1:
        return clip_path
    inputs, fc = [], []
    for i, p in enumerate(parts):
        m = ffprobe(p)
        inputs += ["-i", str(p)]
        fc.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                  f"setsar=1,fps=30,format=yuv420p[v{i}]")
        if m["has_audio"]:
            fc.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{m['duration']:.3f}[a{i}]")
    fc.append("".join(f"[v{i}][a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[v][a]")
    tmp = Path(clip_path).with_name(Path(clip_path).stem + "_b.mp4")
    for args in (encoder_args(quality, codec=codec), encoder_args(quality, True, "h264")):
        try:
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
                 *args, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)], cancel=cancel)
            tmp.replace(clip_path)
            return clip_path
        except RuntimeError:
            continue
    tmp.unlink(missing_ok=True)
    return clip_path


def light_copy(src, dst, target_mb=44):
    """نسخة خفيفة بحجم مضمون تحت حد تيليجرام (HEVC بمعدل محسوب من المدة)"""
    import subprocess
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                               capture_output=True, text=True).stdout.strip() or 0)
    if dur <= 0:
        return None
    kbps = max(300, int(target_mb * 8 * 1024 / dur) - 128)
    for codec in (["-c:v", "libx265", "-preset", "fast", "-tag:v", "hvc1", "-x265-params", "log-level=error"],
                  ["-c:v", "libx264", "-preset", "medium"]):
        try:
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), *codec, "-b:v", f"{kbps}k",
                 "-maxrate", f"{int(kbps * 1.15)}k", "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst)])
            return dst
        except RuntimeError:
            continue
    return None


def frame(src, t, out, crop=None):
    vf = f"crop={crop[1]}:ih:{crop[0]}:0" if crop else "null"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", vf, "-q:v", "2", str(out)])
    return out
