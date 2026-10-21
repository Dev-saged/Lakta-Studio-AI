from config import CONFIG
from .env import encoder_args, enhance_filters, filter_path, run
from . import reframe
from .edit import ZOOM

SIZES = {("short", 1080): (1080, 1920), ("short", 720): (720, 1280), ("wide", 1080): (1920, 1080), ("wide", 720): (1280, 720)}
WM_POS = {"tr": ("W-w-{p}", "{p}"), "tl": ("{p}", "{p}"), "br": ("W-w-{p}", "H-h-{p}"), "bl": ("{p}", "H-h-{p}")}


def hex_color(c):
    c = (c or "#E8402A").lstrip("#")
    return "0x" + (c if len(c) == 6 and all(x in "0123456789abcdefABCDEF" for x in c) else "E8402A")


def clip(src, out, fmt, res, s, dur_src, out_dur, speed, audio_wav, ass_path, fonts_dir, brand, watermark,
         crop=None, work=None, cancel=None, progress=None, template=None, tpl=None, quality=None, edl=None, codec="h264"):
    """template: قالب PNG مرفوع · tpl: قالب مولَّد {static, prog, cta}"""
    W, H = SIZES[(fmt, 720 if int(res) <= 720 else 1080)]
    D = f"{out_dur:.3f}"
    v = []
    if fmt == "short" and crop:
        keys, cw = crop
        v.append(f"crop=w={cw}:h=ih:x='{reframe.expr(keys)}':y=0")
        v.append(f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}")
    else:
        v.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black")
    v += enhance_filters(quality) + ["setsar=1"]
    post = [f"setpts=(PTS-STARTPTS)/{speed:.4f}", "fps=30"]
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
        parts = [f"[0:v]{','.join(v)},split={n}" + "".join(f"[e{i}]" for i in range(n))]
        for i, ((a, b), z) in enumerate(zip(edl["segs"], edl["zoom"])):
            zf = f",crop=iw/{ZOOM}:ih/{ZOOM},scale={W}:{H}:flags=lanczos,setsar=1" if z else ""
            parts.append(f"[e{i}]trim=start={a}:end={b},setpts=PTS-STARTPTS{zf}[c{i}]")
        parts.append("".join(f"[c{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,{','.join(post)}[v0]")
    else:
        parts = [f"[0:v]{','.join(v + post)}[v0]"]

    def step(chain, label):
        nonlocal cur
        parts.append(chain.format(cur=cur, out=label))
        cur = label

    if overlay_png:
        inputs += ["-loop", "1", "-t", D, "-i", str(overlay_png)]
        parts.append(f"[{idx}:v]scale={W}:{H},format=rgba[tp]")
        step("{cur}[tp]overlay=0:0:format=auto{out}", "[v1]")
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
