"""مولّد القوالب: Gemini يصمم المواصفات، والرسم برمجي بشفافية كاملة"""
import re

from PIL import Image, ImageDraw, ImageFont

from config import CONFIG
from . import director
from .thumbs import _shape, part_word

STYLES = ("cinema", "bar", "corners", "poster")
SS = 2  # رسم بضعف الدقة ثم تصغير لحواف ناعمة
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def _shape_any(s):
    # النص اللاتيني (مثل @handle) يُرسم من اليسار لليمين
    return _shape(s) if re.search(r"[\u0600-\u06FF]", s) else (s, {})


def _rgb(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def tpl_conf(brand):
    d = CONFIG["defaults"]["brand"]["template"]
    t = {**d, **(brand.get("template") or {})}
    for k in ("accent", "base", "ink"):
        if not HEX.match(str(t[k])):
            t[k] = d[k]
    return t


def cta_text(t):
    if t["cta_mode"] == "custom" and t["cta_custom"].strip():
        return t["cta_custom"].strip()[:28]
    if t["cta_mode"] == "ai" and t["cta_ai"].strip():
        return t["cta_ai"].strip()[:28]
    return CONFIG["template"]["cta_default"]


# ===== Gemini =====
SPEC_SCHEMA = {"type": "OBJECT", "properties": {
    "accent": {"type": "STRING"}, "base": {"type": "STRING"}, "ink": {"type": "STRING"},
    "mood": {"type": "STRING"}, "cta": {"type": "STRING"}, "style": {"type": "STRING"}},
    "required": ["accent", "base", "ink", "mood", "cta", "style"]}


def design(settings, idea):
    prompt = f"""أنت مصمم هويات بصرية لقنوات يوتيوب وريلز عربية.
فكرة القناة: {idea}
صمّم هوية قالب فيديو قصير:
- accent: لون الهوية الأساسي (hex) للعناصر البارزة.
- base: لون داكن أو عميق (hex) للخلفيات والنص فوق accent، بتباين عالٍ معه.
- ink: لون فاتح (hex) للنص فوق الفيديو.
- mood: كلمة عربية واحدة تصف الطابع (مثل: فخم، جريء، هادئ، حماسي).
- cta: عبارة دعوة للمتابعة من 2-4 كلمات عربية تناسب جمهور القناة، بدون إيموجي.
- style: الأنسب من: cinema, bar, corners, poster."""
    d = director.ask_json(settings, prompt, SPEC_SCHEMA, 0.8)
    t = CONFIG["defaults"]["brand"]["template"]
    out = {k: (d.get(k) if HEX.match(str(d.get(k, ""))) else t[k]).upper() for k in ("accent", "base", "ink")}
    out["mood"] = director._clean(d.get("mood", ""))[:12]
    out["cta"] = director._clean(d.get("cta", ""))[:28]
    out["style"] = d.get("style") if d.get("style") in STYLES else "cinema"
    return out


MOTIFS = ("star", "deco", "lattice")
ORN_SCHEMA = {"type": "OBJECT", "properties": {
    "motif": {"type": "STRING", "enum": list(MOTIFS)}, "density": {"type": "INTEGER"}, "weight": {"type": "NUMBER"}},
    "required": ["motif", "density", "weight"]}
_CENTERS = lambda n: ((0, 0), (n, 0), (0, n), (n, n), (n // 2, n // 2))


def geometric(t, out_path, size=480, motif="star", density=1, weight=1.0):
    """زخرفة متجهة قابلة للتكرار بألوان الهوية — بلا أي API"""
    import math
    density = max(1, min(3, int(density or 1)))
    weight = max(0.6, min(2.0, float(weight or 1.0)))
    im = Image.new("RGB", (size, size), _rgb(t["base"])[:3])
    d = ImageDraw.Draw(im)
    A = _rgb(t["accent"])[:3]
    soft = tuple(int(a * 0.55 + b * 0.45) for a, b in zip(A, _rgb(t["base"])[:3]))
    w1, w2, w3 = (max(1, round(v * weight)) for v in (4, 3, 2))
    if motif == "deco":
        for cx, cy in _CENTERS(size):
            for k in range(1, 4 + density):
                r = size * 0.07 * k
                d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=A if k % 2 else soft, width=w1 if k % 2 else w2)
        d.line([(0, 0), (size, size)], fill=soft, width=w3)
        d.line([(size, 0), (0, size)], fill=soft, width=w3)
    elif motif == "lattice":
        n = 2 * density
        step = size / n
        for i in range(-n, 2 * n + 1):
            x = i * step
            d.line([(x, 0), (x + size, size)], fill=soft, width=w3)
            d.line([(x, size), (x + size, 0)], fill=soft, width=w3)
        for i in range(n + 1):
            for j in range(n + 1):
                x, y = i * step, j * step
                d.ellipse([x - 5 * weight, y - 5 * weight, x + 5 * weight, y + 5 * weight], fill=A)
    else:
        for cx, cy in _CENTERS(size):
            for r, col, w in ((size * 0.22, A, w1), (size * 0.13, soft, w2)):
                pts = []
                for k in range(16):
                    rr = r if k % 2 == 0 else r * 0.62
                    a = math.pi / 8 * k
                    pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
                d.polygon(pts, outline=col, width=w)
        for x in (size // 2, 0, size):
            d.line([(x, 0), (x, size)], fill=soft, width=2)
            d.line([(0, x), (size, x)], fill=soft, width=2)
    im.save(out_path)
    return out_path


def _retry(fn, tries=3):
    import time
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            m = str(e).lower()
            if i + 1 < tries and ("503" in m or "unavailable" in m or "overloaded" in m):
                time.sleep(2 * (i + 1))
                continue
            raise


def ornament_spec(settings, idea, mood=""):
    """نموذج النص (مجاني) يختار نمط الزخرفة وكثافتها، والرسم محلي"""
    prompt = (f"اختر زخرفة متجهة لإطار فيديو. فكرة القناة: {idea}. الطابع: {mood or 'فخم'}. "
              "motif: star نجوم إسلامية، deco آرت ديكو بدوائر متحدة المركز، lattice شبكة معينات. "
              "density من 1 إلى 3، weight سماكة الخط من 0.6 إلى 2.")
    d = _retry(lambda: director.ask_json(settings, prompt, ORN_SCHEMA, 0.8))
    return {"motif": d.get("motif") if d.get("motif") in MOTIFS else "star", "density": d.get("density") or 1, "weight": d.get("weight") or 1.0}


def make_ornament(settings, t, out_path):
    """صورة بالذكاء الاصطناعي ← زخرفة بتصميم الذكاء الاصطناعي ← زخرفة جاهزة. يرجع (المصدر، تنبيه)"""
    idea = t.get("idea") or t.get("mood") or "elegant"
    if not settings.get("gemini_key"):
        geometric(t, out_path, motif=MOTIFS[sum(map(ord, idea)) % len(MOTIFS)])
        return "geometric", "لا يوجد مفتاح Gemini · استخدمتُ زخرفة جاهزة بألوان هويتك."
    notice = None
    try:
        _retry(lambda: ornament(settings, idea, t, out_path))
        return "ai", None
    except Exception as e:
        notice = director.friendly(e, "image")
    try:
        geometric(t, out_path, **ornament_spec(settings, idea, t.get("mood", "")))
        return "ai-vector", notice.rstrip(". ") + " · صمّم الذكاء الاصطناعي زخرفة متجهة بألوان هويتك بدلاً منها."
    except Exception:
        geometric(t, out_path, motif=MOTIFS[sum(map(ord, idea)) % len(MOTIFS)])
        return "geometric", notice.rstrip(". ") + " · استخدمتُ زخرفة جاهزة بألوان هويتك."


def ornament(settings, idea, t, out_path):
    """نقشة خفيفة يولّدها Gemini تُستخدم في الحواف فقط"""
    from google.genai import types
    key = settings.get("gemini_key")
    model = director.resolve(settings, "image")
    prompt = (f"A seamless, subtle ornamental pattern tile for a video frame border. Theme: {idea}. "
              f"Palette: {t['accent']} and {t['base']}. Flat, elegant, low contrast, no text, no letters, no faces, square.")
    r = director.client(key).models.generate_content(
        model=model, contents=prompt, config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
    for part in r.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            import io
            Image.open(io.BytesIO(part.inline_data.data)).convert("RGB").save(out_path)
            return out_path
    raise RuntimeError("لم يُرجع النموذج صورة")


def export_frames(env, brand, font_path):
    """يحفظ القالب الحالي كـPNG شفاف لكل مقاس ليُنزَّل ويُستخدم بدل المولَّد"""
    t = tpl_conf(brand)
    if t["style"] not in STYLES:
        raise ValueError("اختر نمطاً للقالب أولاً")
    logo = env.assets / brand["watermark"] if brand.get("watermark") and brand.get("watermark_on", True) else None
    orn = env.assets / "ornament.png"
    out = {}
    for key, (W, H) in (("frame_short", (1080, 1920)), ("frame_wide", (1920, 1080))):
        img, _ = static(t["style"], W, H, t, font_path, None, logo if logo and logo.exists() else None, orn if orn.exists() else None)
        img.save(env.assets / f"{key}.png")
        out[key] = f"{key}.png"
    return out


# ===== الرسم =====
class Canvas:
    def __init__(self, W, H, font_path):
        self.W, self.H = W, H
        self.im = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im)
        self.u = min(W, H) / 100 * SS
        self.font_path = str(font_path)

    def font(self, size):
        return ImageFont.truetype(self.font_path, max(8, int(size)))

    def layer(self):
        return Image.new("RGBA", self.im.size, (0, 0, 0, 0))

    def merge(self, layer):
        self.im = Image.alpha_composite(self.im, layer)
        self.d = ImageDraw.Draw(self.im)

    def vgrad(self, y0, y1, col, a0, a1):
        g = self.layer()
        gd = ImageDraw.Draw(g)
        y0, y1 = int(y0), int(y1)
        for y in range(y0, y1):
            k = (y - y0) / max(1, y1 - y0 - 1)
            gd.line([(0, y), (self.im.width, y)], fill=col[:3] + (int(a0 + (a1 - a0) * k),))
        self.merge(g)

    def text(self, xy, s, size, fill, anchor="mm", stroke=0, stroke_fill=None):
        t, kw = _shape_any(s)
        self.d.text(xy, t, font=self.font(size), fill=fill, anchor=anchor, stroke_width=int(stroke),
                    stroke_fill=stroke_fill, **kw)

    def text_box(self, s, size):
        t, kw = _shape_any(s)
        b = self.d.textbbox((0, 0), t, font=self.font(size), anchor="lt", **kw)
        return b[2] - b[0], b[3] - b[1]

    def vtext(self, center, s, size, fill):
        """نص عمودي (يُقرأ من الأسفل للأعلى)"""
        f = self.font(size)
        t, kw = _shape_any(s)
        b = ImageDraw.Draw(self.im).textbbox((0, 0), t, font=f, anchor="lt", **kw)
        tmp = Image.new("RGBA", (b[2] - b[0] + 8, b[3] - b[1] + 8), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).text((4 - b[0], 4 - b[1]), t, font=f, fill=fill, **kw)
        tmp = tmp.rotate(90, expand=True)
        lay = self.layer()
        lay.paste(tmp, (int(center[0] - tmp.width / 2), int(center[1] - tmp.height / 2)), tmp)
        self.merge(lay)

    def result(self):
        return self.im.resize((self.W, self.H), Image.LANCZOS)


def _pill(c, cx, cy, s, size, bg, fg, align="c", radius=None, pad=(3, 1.6), outline=None):
    u = c.u
    w, h = c.text_box(s, size)
    pw, ph = w + pad[0] * 2 * u, h + pad[1] * 2 * u
    x0 = {"c": cx - pw / 2, "r": cx - pw, "l": cx}[align]
    r = ph / 2 if radius is None else radius
    if outline:
        c.d.rounded_rectangle([x0, cy - ph / 2, x0 + pw, cy + ph / 2], r, outline=outline, width=int(0.6 * u))
    else:
        c.d.rounded_rectangle([x0, cy - ph / 2, x0 + pw, cy + ph / 2], r, fill=bg)
    c.text((x0 + pw / 2, cy), s, size, fg)
    return x0, pw


def static(style, W, H, t, font_path, part=None, logo=None, orn=None):
    """طبقة القالب الثابتة (شفافة) — رقم الجزء جزء منها لأنه ثابت داخل كل لقطة"""
    c = Canvas(W, H, font_path)
    u, Wp, Hp = c.u, W * SS, H * SS
    A, B, C = _rgb(t["accent"]), _rgb(t["base"]), _rgb(t["ink"])
    short = H > W
    handle = t["handle"].strip() if t["show_handle"] else ""
    label = (f"الجزء {part_word(part)}" if style != "poster" else AR_DIGITS[part] if part < 10 else str(part)) if part and t["show_part"] else ""

    if orn and t.get("ornament"):
        _ornament(c, orn, style)

    if style == "cinema":
        c.vgrad(Hp * 0.70, Hp, B, 0, 200)
        i = 3.5 * u
        c.d.rounded_rectangle([i, i, Wp - i, Hp - i], 3 * u, outline=A[:3] + (180,), width=max(2, int(0.5 * u)))
        o, L, th = 2 * u, 10 * u, 1.2 * u
        for (x, y, dx, dy) in ((o, o, 1, 1), (Wp - o, Hp - o, -1, -1)):
            c.d.rectangle([min(x, x + dx * L), min(y, y + dy * th), max(x, x + dx * L), max(y, y + dy * th)], fill=A)
            c.d.rectangle([min(x, x + dx * th), min(y, y + dy * L), max(x, x + dx * th), max(y, y + dy * L)], fill=A)
        if handle:
            c.text((Wp / 2, Hp * (0.935 if short else 0.90)), handle, 3.4 * u, C)
        if label:
            w, h = c.text_box(label, 4 * u)
            pw, ph = w + 6 * u, h + 3.2 * u
            y = Hp * 0.06 + ph / 2
            c.d.rounded_rectangle([Wp - pw, y - ph / 2, Wp + 4 * u, y + ph / 2], 2 * u, fill=A)
            c.text((Wp - pw / 2, y), label, 4 * u, B)
        track = (7 * u, Hp - 4 * u - 1.1 * u, Wp - 7 * u, Hp - 4 * u)
    elif style == "bar":
        c.vgrad(0, Hp * (0.15 if short else 0.22), B, 235, 0)
        d = 9 * u
        cx, cy = Wp - 5 * u - d / 2, Hp * (0.045 if short else 0.07) + d / 2
        c.d.ellipse([cx - d / 2 - 0.8 * u, cy - d / 2 - 0.8 * u, cx + d / 2 + 0.8 * u, cy + d / 2 + 0.8 * u], fill=C[:3] + (100,))
        c.d.ellipse([cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2], fill=A)
        if logo:
            try:
                lg = Image.open(logo).convert("RGBA")
                lg.thumbnail((int(d * 0.72), int(d * 0.72)), Image.LANCZOS)
                lay = c.layer()
                lay.paste(lg, (int(cx - lg.width / 2), int(cy - lg.height / 2)), lg)
                c.merge(lay)
            except Exception:
                pass
        if handle:
            c.text((cx - d / 2 - 3 * u, cy), handle, 4 * u, C, anchor="rm")
        if label:
            _pill(c, 5 * u, cy, label, 3.4 * u, A, B, align="l")
        track = (0, 0, Wp, 1.4 * u)
    elif style == "corners":
        L, th, o = 14 * u, 1.1 * u, 4 * u
        h = th / 2
        c.d.line([(Wp - o - L, o + h), (Wp - o - h, o + h), (Wp - o - h, o + L)], fill=A, width=int(th), joint="curve")
        c.d.line([(o + h, Hp - o - L), (o + h, Hp - o - h), (o + L, Hp - o - h)], fill=A, width=int(th), joint="curve")
        if handle:
            c.vtext((Wp - 6 * u, Hp - o - L - 4 * u - c.text_box(handle, 3.2 * u)[0] / 2), handle, 3.2 * u, C[:3] + (230,))
        if label:
            _pill(c, o + 2 * u, o + 4 * u, label, 3.4 * u, None, A, align="l", radius=2 * u, outline=A)
        track = (22 * u, Hp - 4.6 * u - 0.8 * u, Wp - 22 * u, Hp - 4.6 * u)
    else:  # poster
        cw = 11 * u
        g = c.layer()
        gd = ImageDraw.Draw(g)
        for y in range(int(Hp)):
            k = y / Hp
            col = tuple(int(A[i] * (1 - 0.45 * k) + B[i] * 0.45 * k) for i in range(3))
            gd.line([(Wp - cw, y), (Wp, y)], fill=col + (255,))
        c.merge(g)
        c.d.rectangle([Wp - cw - 0.6 * u, 0, Wp - cw, Hp], fill=C[:3] + (128,))
        if handle:
            c.vtext((Wp - cw / 2, Hp / 2), handle, 4 * u, B)
        if label:
            c.text((5 * u, Hp * 0.035), label, 22 * u, A[:3] + (70,), anchor="lt", stroke=0.7 * u, stroke_fill=A)
        track = (Wp - cw, 0, Wp, Hp)
    img = c.result()
    tr = tuple(int(v / SS) for v in track)
    if t["show_prog"] and style != "poster":
        ImageDraw.Draw(img).rectangle(tr, fill=C[:3] + (64,))
    return img, tr


def _ornament(c, path, style):
    try:
        tile = Image.open(path).convert("RGBA")
    except Exception:
        return
    Wp, Hp = c.im.size
    s = int(c.u * 18)
    tile = tile.resize((s, s), Image.LANCZOS)
    pat = Image.new("RGBA", (Wp, Hp))
    for y in range(0, Hp, s):
        for x in range(0, Wp, s):
            pat.paste(tile, (x, y))
    mask = Image.new("L", (Wp, Hp), 0)
    md = ImageDraw.Draw(mask)
    band = int(Hp * 0.06) if Hp > Wp else int(Hp * 0.09)
    if style == "poster":
        md.rectangle([Wp - int(c.u * 11), 0, Wp, Hp], fill=90)
    else:
        for i in range(band):
            a = int(90 * (1 - i / band))
            md.line([(0, i), (Wp, i)], fill=a)
            md.line([(0, Hp - 1 - i), (Wp, Hp - 1 - i)], fill=a)
    pat.putalpha(mask)
    c.merge(pat)


def cta_png(W, H, t, font_path, out):
    c = Canvas(W, H, font_path)
    u = c.u
    A, B = _rgb(t["accent"]), _rgb(t["base"])
    _pill(c, W * SS / 2, H * SS * (0.84 if H > W else 0.76), cta_text(t) + "  ←", 4.4 * u, A, B)
    c.result().save(out)
    return out


def progress_spec(style, track, t):
    """مواصفات شريط التقدم المتحرك للمخرج"""
    if not t["show_prog"]:
        return None
    x0, y0, x1, y1 = track
    return {"x": x0, "y": y0, "w": max(2, x1 - x0), "h": max(2, y1 - y0), "vertical": style == "poster",
            "color": t["accent"] if style != "poster" else t["ink"], "alpha": 1.0 if style != "poster" else 0.45}


def build(env, brand, fmt, W, H, part, font_path, work):
    """قالب لقطة: (مسار الطبقة الثابتة، شريط التقدم، مسار الدعوة)"""
    t = tpl_conf(brand)
    style = t["style"]
    if style not in STYLES:
        return None
    logo = env.assets / brand["watermark"] if brand.get("watermark") and brand.get("watermark_on", True) else None
    orn = env.assets / "ornament.png"
    img, track = static(style, W, H, t, font_path, part, logo if logo and logo.exists() else None, orn if orn.exists() else None)
    p = work / f"tpl_{fmt}_{part}.png"
    img.save(p)
    cta = cta_png(W, H, t, font_path, work / f"cta_{fmt}.png") if t["show_cta"] else None
    return {"static": p, "prog": progress_spec(style, track, t), "cta": cta, "style": style}


def previews(env, brand, font_path, out_dir, scale=0.4):
    """صور معاينة للأنماط الأربعة + نسخة عريضة للنمط المختار"""
    t = tpl_conf(brand)
    logo = env.assets / brand["watermark"] if brand.get("watermark") and brand.get("watermark_on", True) else None
    orn = env.assets / "ornament.png"
    res = {}
    for style, (W, H) in [(s, (1080, 1920)) for s in STYLES] + [(t["style"] if t["style"] in STYLES else "cinema", (1920, 1080))]:
        w, h = int(W * scale), int(H * scale)
        img, track = static(style, w, h, {**t, "style": style}, font_path, 1, logo if logo and logo.exists() else None,
                            orn if orn.exists() else None)
        if t["show_prog"]:
            x0, y0, x1, y1 = track
            lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            if style == "poster":
                d.rectangle([x0, y1 - (y1 - y0) * 0.6, x1, y1], fill=_rgb(t["ink"], 115))
            else:
                d.rectangle([x1 - (x1 - x0) * 0.6, y0, x1, y1], fill=_rgb(t["accent"]))
            img = Image.alpha_composite(img, lay)
        if t["show_cta"]:
            ctap = out_dir / "_cta.png"
            cta_png(w, h, t, font_path, ctap)
            img = Image.alpha_composite(img, Image.open(ctap))
        key = style if H > W else "wide"
        p = out_dir / f"tplprev_{key}.png"
        img.save(p)
        res[key] = p.name
    return res
