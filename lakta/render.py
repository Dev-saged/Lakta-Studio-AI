from .env import encoder_args, filter_path, run
from . import reframe

SIZES = {("short", 1080): (1080, 1920), ("short", 720): (720, 1280), ("wide", 1080): (1920, 1080), ("wide", 720): (1280, 720)}
WM_POS = {"tr": ("W-w-{p}", "{p}"), "tl": ("{p}", "{p}"), "br": ("W-w-{p}", "H-h-{p}"), "bl": ("{p}", "H-h-{p}")}


def hex_color(c):
    c = (c or "#E8402A").lstrip("#")
    return "0x" + (c if len(c) == 6 and all(x in "0123456789abcdefABCDEF" for x in c) else "E8402A")


def clip(src, out, fmt, res, s, dur_src, out_dur, speed, audio_wav, ass_path, fonts_dir, brand, watermark,
         crop=None, work=None, cancel=None, progress=None, template=None):
    W, H = SIZES[(fmt, 720 if int(res) <= 720 else 1080)]
    v = []
    if fmt == "short" and crop:
        keys, cw = crop
        v.append(f"crop=w={cw}:h=ih:x='{reframe.expr(keys)}':y=0")
        v.append(f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}")
    else:
        v.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black")
    v += ["setsar=1", f"setpts=(PTS-STARTPTS)/{speed:.4f}", "fps=30"]
    if brand.get("frame_on") and not template:
        t = max(8, W // 50)
        col = hex_color(brand.get("frame_color"))
        v.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={col}:t={t}")
        v.append(f"drawbox=x={t}:y={t}:w=iw-{2 * t}:h=ih-{2 * t}:color=white:t={max(2, t // 4)}")
    ass = f"ass='{filter_path(ass_path)}':fontsdir='{filter_path(fonts_dir)}':shaping=complex"
    inputs = ["-ss", f"{s:.3f}", "-t", f"{dur_src:.3f}", "-i", str(src), "-i", str(audio_wav)]
    idx = 2
    if template:
        inputs += ["-loop", "1", "-i", str(template)]
        graph = (f"[0:v]{','.join(v)}[v0];[{idx}:v]scale={W}:{H},format=rgba[tp];"
                 f"[v0][tp]overlay=0:0:format=auto:shortest=1,{ass}[base]")
        idx += 1
    else:
        graph = f"[0:v]{','.join(v + [ass])}[base]"
    last = "[base]"
    if watermark:
        p = int(W * 0.03)
        x, y = (e.format(p=p) for e in WM_POS.get(brand.get("watermark_pos"), WM_POS["tr"]))
        inputs += ["-i", str(watermark)]
        graph += f";[{idx}:v]scale={int(W * 0.15)}:-1,format=rgba,colorchannelmixer=aa=0.85[wm];[base][wm]overlay=x={x}:y={y}:format=auto[vout]"
        last = "[vout]"
    script = work / f"fc_{out.stem}.txt"
    script.write_text(graph, "utf-8")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex_script", str(script),
           "-map", last, "-map", "1:a", "-t", f"{out_dur:.3f}", *encoder_args(),
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    run(cmd, cancel=cancel, progress=progress, total=out_dur)
    return out


def frame(src, t, out, crop=None):
    vf = f"crop={crop[1]}:ih:{crop[0]}:0" if crop else "null"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", vf, "-q:v", "2", str(out)])
    return out
