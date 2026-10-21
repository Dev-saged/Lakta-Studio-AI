"""مصمم الأغلفة: أفضل لقطة بوجه واضح، تحسين الصورة، وثلاثة تصاميم بهوية القناة"""
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .render import frame
from .templates import _rgb, tpl_conf
from .thumbs import _shape, part_word

SIZES = {"wide": (1280, 720), "short": (1080, 1920)}
LAYOUTS = ("hero", "split", "pop")


# ===== اختيار اللقطات =====
def _score(path, det):
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        return -1, None
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharp = min(1.0, cv2.Laplacian(gray, cv2.CV_64F).var() / 400)
    light = 1 - abs(gray.mean() - 125) / 125
    face, s_face = None, 0.0
    if det:
        small = cv2.resize(img, None, fx=640 / w, fy=640 / w) if w > 640 else img
        k = w / small.shape[1]
        fs = det.faces(small)
        if len(fs):
            cx, cy, area = max(fs, key=lambda f: f[2])
            face = (cx * k, cy * k, (area ** 0.5) * k)
            ratio = area * k * k / (w * h)
            s_face = min(1.0, ratio * 25) * (1 - abs(cx * k / w - 0.5))
    return s_face * 2 + sharp + light * 0.5, face


def pick_frames(src, s, dur_src, work, tag, models_dir, log, n=3):
    from . import reframe
    det = None
    try:
        if "d" not in reframe._det:
            reframe._det["d"] = reframe.Detector(models_dir, log)
        det = reframe._det["d"]
    except Exception:
        pass
    cands = []
    for i, f in enumerate((0.08, 0.18, 0.3, 0.42, 0.55, 0.68, 0.8, 0.9)):
        p = work / f"cov_{tag}_{i}.jpg"
        try:
            frame(src, s + dur_src * f, p)
            sc, face = _score(p, det)
            cands.append((sc, f, p, face))
        except Exception:
            pass
    cands.sort(key=lambda c: -c[0])
    out = []
    for c in cands:
        if all(abs(c[1] - o[1]) >= 0.15 for o in out):
            out.append(c)
        if len(out) == n:
            break
    return [(p, face) for _, _, p, face in out] or [(cands[0][2], cands[0][3])] if cands else []


# ===== الصورة =====
def _enhance(img):
    img = ImageOps.autocontrast(img.convert("RGB"), cutoff=1)
    img = ImageEnhance.Color(img).enhance(1.18)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    return ImageEnhance.Sharpness(img).enhance(1.35)


def _cover_crop(img, W, H, face=None, bias=0.5):
    """قص بنسبة الإطار مع إبقاء الوجه في المكان المطلوب أفقياً"""
    w, h = img.size
    scale = max(W / w, H / h)
    img = img.resize((max(W, int(w * scale + 0.5)), max(H, int(h * scale + 0.5))), Image.LANCZOS)
    cx = (face[0] * scale) if face else img.width / 2
    cy = (face[1] * scale) if face else img.height * 0.4
    x = int(min(max(0, cx - W * bias), img.width - W))
    y = int(min(max(0, cy - H * 0.38), img.height - H))
    return img.crop((x, y, x + W, y + H)), ((cx - x, cy - y, face[2] * scale) if face else None)


# ===== النص =====
def _font(path, size):
    return ImageFont.truetype(str(path), max(10, int(size)))


def _wrap(draw, words, font, maxw):
    lines, cur = [], []
    for w in words:
        test = cur + [w]
        if cur and _measure(draw, " ".join(test), font) > maxw:
            lines.append(cur)
            cur = [w]
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _measure(draw, text, font):
    t, kw = _shape(text)
    b = draw.textbbox((0, 0), t, font=font, **kw)
    return b[2] - b[0]


def _rich(img, words, highlight, font, box, colors, stroke, align="center", shadow=None, spacing=1.12):
    """نص عربي متعدد الأسطر بكلمة مميزة اللون — الكلمات تُرصّ يدوياً من اليمين"""
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    lines = _wrap(d, words, font, x1 - x0)
    asc, desc = font.getmetrics()
    lh = (asc + desc) * spacing
    total = lh * len(lines)
    y = y0 + max(0, (y1 - y0 - total) / 2)
    space = _measure(d, "ب ب", font) - 2 * _measure(d, "ب", font)
    for line in lines:
        widths = [_measure(d, w, font) for w in line]
        lw = sum(widths) + space * (len(line) - 1)
        x = {"center": (x0 + x1 + lw) / 2, "right": x1, "left": x0 + lw}[align]
        for w, ww in zip(line, widths):
            t, kw = _shape(w)
            fill = colors[1] if w.strip("،.!؟?") == highlight else colors[0]
            if shadow:
                d.text((x - ww + shadow[0], y + shadow[1]), t, font=font, fill=shadow[2], stroke_width=stroke,
                       stroke_fill=shadow[2], **kw)
            d.text((x - ww, y), t, font=font, fill=fill, stroke_width=stroke, stroke_fill=colors[2], **kw)
            x -= ww + space
        y += lh
    return y


def _pill(img, xy, text, font, bg, fg, anchor="ra"):
    d = ImageDraw.Draw(img)
    t, kw = _shape(text)
    b = d.textbbox((0, 0), t, font=font, **kw)
    tw, th = b[2] - b[0], b[3] - b[1]
    px, py = th * 0.7, th * 0.45
    w, h = tw + px * 2, th + py * 2
    x = xy[0] - w if anchor[0] == "r" else xy[0]
    d.rounded_rectangle([x, xy[1], x + w, xy[1] + h], h / 2, fill=bg)
    d.text((x + px - b[0], xy[1] + py - b[1]), t, font=font, fill=fg, **kw)


# ===== التصاميم =====
def _hero(img, face, W, H, words, hl, font_path, c, label, handle):
    base, _ = _cover_crop(img, W, H, face)
    base = base.convert("RGBA")
    g = Image.new("L", (1, 256))
    for y in range(256):
        g.putpixel((0, y), int(max(0, (y - 100) / 156) ** 1.3 * 235))
    shade = Image.new("RGBA", (W, H), c["base"][:3] + (255,))
    shade.putalpha(g.resize((W, H)))
    base = Image.alpha_composite(base, shade)
    u = min(W, H) / 100
    font = _font(font_path, (W if H > W else H) * (0.14 if H > W else 0.115))
    top = H * (0.58 if H > W else 0.6)
    _rich(base, words, hl, font, (W * 0.06, top, W * 0.94, H * 0.9), (c["ink"], c["accent"], (0, 0, 0, 255)),
          int(u * 0.9), shadow=(int(u * 0.8), int(u * 0.9), (0, 0, 0, 160)))
    if label:
        _pill(base, (W - 4 * u, 4 * u), label, _font(font_path, u * 4.6), c["accent"], c["base"])
    if handle:
        d = ImageDraw.Draw(base)
        d.text((W / 2, H - 3.5 * u), handle, font=_font(font_path, u * 3), fill=c["ink"][:3] + (220,), anchor="ms")
    return base


def _split(img, face, W, H, words, hl, font_path, c, label, handle):
    u = min(W, H) / 100
    canvas = Image.new("RGBA", (W, H), c["base"])
    d = ImageDraw.Draw(canvas)
    if H > W:
        ph = int(H * 0.6)
        pic, _ = _cover_crop(img, W, ph, face)
        mask = Image.new("L", (W, ph), 255)
        ImageDraw.Draw(mask).polygon([(0, ph), (W, ph - 7 * u), (W, ph), (0, ph)], fill=0)
        canvas.paste(pic, (0, 0), mask)
        d.polygon([(0, ph - 0.2 * u), (W, ph - 7.2 * u), (W, ph - 5.6 * u), (0, ph + 1.4 * u)], fill=c["accent"])
        box = (W * 0.07, ph + 4 * u, W * 0.93, H - 9 * u)
        align = "center"
    else:
        pw = int(W * 0.6)
        pic, _ = _cover_crop(img, pw, H, face, bias=0.45)
        mask = Image.new("L", (pw, H), 255)
        ImageDraw.Draw(mask).polygon([(0, 0), (9 * u, 0), (0, H)], fill=0)
        canvas.paste(pic, (W - pw, 0), mask)
        d.polygon([(W - pw, 0), (W - pw + 9 * u, 0), (W - pw + 1.6 * u, H), (W - pw - 1.2 * u, H)], fill=c["accent"])
        box = (5 * u, 10 * u, W - pw - 2 * u, H - 12 * u)
        align = "right"
    font = _font(font_path, (W if H > W else H) * (0.13 if H > W else 0.115))
    _rich(canvas, words, hl, font, box, (c["ink"], c["accent"], c["base"]), 0, align=align,
          shadow=(int(u * 0.7), int(u * 0.7), (0, 0, 0, 120)))
    if label:
        lx = W - 4 * u if H > W else box[2]
        _pill(canvas, (lx, H - 8.5 * u if H > W else 4 * u), label, _font(font_path, u * 4.2), c["accent"], c["base"])
    if handle:
        d.text((4 * u if H > W else box[0], H - 3.5 * u), handle, font=_font(font_path, u * 3), fill=c["ink"][:3] + (200,), anchor="ls")
    return canvas


def _pop(img, face, W, H, words, hl, font_path, c, label, handle):
    base, fpos = _cover_crop(img, W, H, face, bias=0.5 if H > W else 0.62)
    base = base.convert("RGBA")
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-W * 0.25, -H * 0.2, W * 1.25, H * 1.2], fill=255)
    vig = ImageOps.invert(vig.filter(ImageFilter.GaussianBlur(min(W, H) * 0.12)))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    dark.putalpha(vig.point(lambda v: int(v * 0.75)))
    base = Image.alpha_composite(base, dark)
    u = min(W, H) / 100
    d = ImageDraw.Draw(base)
    if fpos:
        fx, fy, fs = fpos
        r = fs * 0.95
        for k, a in ((1.6, 70), (1.0, 255)):
            d.ellipse([fx - r - k * u, fy - r * 1.15 - k * u, fx + r + k * u, fy + r * 1.15 + k * u],
                      outline=c["accent"][:3] + (a,), width=int(u * (1.4 if k == 1.0 else 3)))
    font = _font(font_path, (W if H > W else H) * (0.15 if H > W else 0.14))
    box = (W * 0.05, H * 0.62, W * 0.95, H * 0.94) if H > W else (W * 0.04, H * 0.08, W * 0.52, H * 0.92)
    _rich(base, words, hl, font, box, ((255, 255, 255, 255), c["accent"], (0, 0, 0, 255)), int(u * 1.3),
          align="center" if H > W else "right", shadow=(int(u * 1.1), int(u * 1.2), c["accent"][:3] + (200,)))
    if label:
        _pill(base, (W - 4 * u, 4 * u), label, _font(font_path, u * 4.4), (0, 0, 0, 200), c["ink"])
    if handle:
        d.text((W / 2, H - 3 * u), handle, font=_font(font_path, u * 2.8), fill=(255, 255, 255, 210), anchor="ms")
    return base


DRAW = {"hero": _hero, "split": _split, "pop": _pop}


def make(src, s, dur_src, out_dir, work, text, highlight, part, font_path, brand, models_dir, log, crop=None):
    """ثلاثة أغلفة لكل مقاس — الأول يحل محل Thumbnail_* القديم"""
    t = tpl_conf(brand)
    c = {"accent": _rgb(t["accent"]), "base": _rgb(t["base"]), "ink": _rgb(t["ink"])}
    if t["style"] == "none":
        c = {"accent": (255, 214, 10, 255), "base": (14, 20, 38, 255), "ink": (255, 255, 255, 255)}
    words = (text or "").split() or ["شاهد"]
    hl = highlight if highlight in words else max(words, key=len)
    label = f"الجزء {part_word(part)}" if part else ""
    handle = t["handle"].strip() if t["show_handle"] else ""
    frames = pick_frames(src, s, dur_src, work, part, models_dir, log)
    if not frames:
        return {}
    res = {"covers": {"short": [], "wide": []}}
    for fmt, (W, H) in SIZES.items():
        for i, layout in enumerate(LAYOUTS):
            path, face = frames[i % len(frames)]
            img = _enhance(Image.open(path))
            out = DRAW[layout](img, face, W, H, words, hl, font_path, c, label, handle).convert("RGB")
            name = f"Cover_{'Shorts' if fmt == 'short' else 'Wide'}_Part{part}_{i + 1}.jpg"
            out.save(out_dir / name, quality=92, optimize=True, progressive=True)
            res["covers"][fmt].append(name)
            if i == 0:
                main = f"Thumbnail_{'Shorts' if fmt == 'short' else 'Wide'}_Part{part}.jpg"
                out.save(out_dir / main, quality=93, optimize=True)
                res[f"thumb_{fmt}"] = main
    return res
