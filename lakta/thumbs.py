import textwrap

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, features

from .render import frame

PART_WORDS = ["الأول", "الثاني", "الثالث", "الرابع", "الخامس"]


def part_word(n):
    return PART_WORDS[n - 1] if 0 < n <= len(PART_WORDS) else str(n)


def _shape(text):
    if features.check_feature("raqm"):
        return text, {"direction": "rtl", "language": "ar"}
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.ArabicReshaper(configuration={"delete_harakat": True}).reshape(text)), {}


def _text(draw, pos, text, font, fill, depth, stroke):
    t, kw = _shape(text)
    for i in range(depth, 0, -1):
        draw.multiline_text((pos[0] - i, pos[1] + i), t, font=font, fill="#1A0000", stroke_width=stroke + 2,
                            stroke_fill="black", anchor="mm", align="center", **kw)
    draw.multiline_text(pos, t, font=font, fill=fill, stroke_width=stroke, stroke_fill="black", anchor="mm", align="center", **kw)


def _sharp(path):
    try:
        import cv2
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        return cv2.Laplacian(img, cv2.CV_64F).var()
    except Exception:
        return Image.open(path).convert("L").filter(ImageFilter.FIND_EDGES).getextrema()[1]


def best_frame(src, s, dur_src, work, tag):
    cands = []
    for i, f in enumerate((0.12, 0.3, 0.5)):
        p = work / f"cand_{tag}_{i}.jpg"
        try:
            frame(src, s + dur_src * f, p)
            cands.append((_sharp(p), p))
        except Exception:
            pass
    return max(cands)[1] if cands else None


def _frame_border(draw, w, h, color):
    t = int(w * 0.02)
    draw.rectangle([0, 0, w - 1, h - 1], outline=color, width=t)
    draw.rectangle([t, t, w - t - 1, h - t - 1], outline="white", width=max(2, t // 4))


def _decorate(img, template, brand, color):
    """قالب PNG جاهز إن وُجد، وإلا الإطار المرسوم"""
    if template:
        tp = Image.open(template).convert("RGBA").resize(img.size, Image.LANCZOS)
        base = img.convert("RGBA")
        base.alpha_composite(tp)
        return base.convert("RGB")
    if brand.get("frame_on"):
        _frame_border(ImageDraw.Draw(img), *img.size, color)
    return img


def make(still, out_dir, text, part, font_path, brand, crop_x=None, templates=None):
    """غلاف عريض 1280×720 + غلاف شورتس 1080×1920 + لقطتا معاينة بلا نص"""
    base = Image.open(still).convert("RGB")
    w, h = base.size
    label = f"الجزء {part_word(part)}"
    color = brand.get("frame_color") or "#E8402A"
    res = {}

    if w / h >= 16 / 9 - 0.01:
        wide = ImageOps.fit(base, (1280, 720), Image.LANCZOS)
    else:
        fg = base.resize((int(720 * w / h), 720), Image.LANCZOS)
        wide = ImageOps.fit(base, (1280, 720)).filter(ImageFilter.GaussianBlur(30))
        wide.paste(fg, ((1280 - fg.width) // 2, 0))
    wide.save(out_dir / f"poster_wide_{part}.jpg", quality=88)
    wide = _decorate(wide, (templates or {}).get("wide"), brand, color)
    d = ImageDraw.Draw(wide)
    fs = int(1280 * 0.10)
    f1, f2 = ImageFont.truetype(str(font_path), fs), ImageFont.truetype(str(font_path), int(fs * 0.7))
    _text(d, (640, 720 * 0.66), text, f1, "#FFEA00", 8, 4)
    _text(d, (640, 720 * 0.66 + fs * 1.25), label, f2, "#00FF00", 5, 3)
    res["thumb_wide"] = out_dir / f"Thumbnail_Wide_Part{part}.jpg"
    wide.save(res["thumb_wide"], quality=93)

    cw = int(h * 9 / 16)
    if cw >= w:
        short = ImageOps.fit(base, (1080, 1920), Image.LANCZOS)
    else:
        x = max(0, min(w - cw, (w - cw) // 2 if crop_x is None else crop_x))
        short = base.crop((x, 0, x + cw, h)).resize((1080, 1920), Image.LANCZOS)
    short.save(out_dir / f"poster_short_{part}.jpg", quality=88)
    short = _decorate(short, (templates or {}).get("short"), brand, color)
    d = ImageDraw.Draw(short)
    fs = int(1080 * 0.15)
    f1, f2 = ImageFont.truetype(str(font_path), fs), ImageFont.truetype(str(font_path), int(fs * 0.8))
    wrapped = "\n".join(textwrap.wrap(text, width=10)) or text
    n = wrapped.count("\n") + 1
    y = 1920 * 0.55
    _text(d, (540, y), wrapped, f1, "#FFEA00", 6, 3)
    _text(d, (540, y + fs * (n * 0.55 + 0.75)), label, f2, "#00FFFF", 5, 2)
    res["thumb_short"] = out_dir / f"Thumbnail_Shorts_Part{part}.jpg"
    short.save(res["thumb_short"], quality=93)
    res["poster_short"] = out_dir / f"poster_short_{part}.jpg"
    res["poster_wide"] = out_dir / f"poster_wide_{part}.jpg"
    return res
