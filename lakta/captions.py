import re

# ألوان ASS بصيغة &HBBGGRR
STYLES = {
    "classic": r"{\c&H00E6FF&\fscx114\fscy114}",
    "cobalt": r"{\c&HFFFFFF&\3c&HC83C1E&\bord14\shad0}",
    "marker": r"{\c&H3A1F1A&\3c&H42E6F5&\bord14\shad0}",
    "glow": r"{\c&HFFFFFF&\3c&H00C8FF&\bord7\blur5\shad0}",
    "single": r"{\c&H00E6FF&}",
}
FADE_IN = r"{\fad(70,0)}"
# حركة الكلمة المنطوقة (تُضاف بعد لون النمط)
ANIM = {
    "pop": r"{\fscx82\fscy82\t(0,90,\fscx120\fscy120)\t(90,170,\fscx112\fscy112)}",
    "bounce": r"{\fscy70\t(0,70,\fscy128)\t(70,150,\fscy106)\t(150,220,\fscy112)}",
    "none": "",
}


def _ass_color(hex_rgb, default="&H00E6FF&"):
    h = str(hex_rgb or "").lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&" if re.fullmatch(r"[0-9a-fA-F]{6}", h) else default


def _norm(w):
    # مطابقة متسامحة: بدون تشكيل، والهمزات والتاء المربوطة والألف المقصورة موحّدة
    w = re.sub(r"[\u064B-\u0652\u0640]", "", str(w))
    w = re.sub("[أإآ]", "ا", w).replace("ة", "ه").replace("ى", "ي")
    w = re.sub(r"^(وال|بال|فال|كال|لل|ال)(?=..)", "", re.sub(r"[^\w]", "", w).lower())
    return w


def _t(sec):
    sec = max(0.0, sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(txt):
    return re.sub(r"\s+", " ", txt.replace("\\", "/").replace("{", "(").replace("}", ")")).strip()


def chunks(words, per, max_gap=0.6):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or w["s"] - cur[-1]["e"] > max_gap):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def _hook_top(brand, short):
    # إزاحة الخطاف تحت الشريط العلوي للقالب
    style = (brand.get("template") or {}).get("style")
    if style == "bar" and not brand.get("frame_short" if short else "frame_wide"):
        return 0.19 if short else 0.26
    return 0.14 if short else 0.08


def build(path, words, dur, W, H, brand, font_family, hook=None, keywords=None):
    """ملف ASS: ترجمة كاريوكي كلمة بكلمة + خطاف علوي"""
    short = H > W
    size = int(W * 0.096) if short else int(H * 0.078)
    hook_size = int(size * 0.9)
    margin_v = int(H * (0.27 if short else 0.10))
    outline = max(3, size // 14)
    style = brand.get("caption_style")
    active = STYLES.get(style, STYLES["classic"]) + ANIM.get(brand.get("caption_anim") or "pop", "")
    single = style == "single"
    if single:
        size, margin_v = int(size * 1.55), int(H * (0.33 if short else 0.2))
    tpl = brand.get("template") or {}
    kw_color = _ass_color(tpl.get("accent") if tpl.get("style") not in (None, "none") else "#FF9F1C")
    kws = {_norm(k) for k in (keywords or []) if _norm(k)}
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font_family},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,{outline},{outline // 2 + 1},2,{W // 12},{W // 12},{margin_v},-1",
        f"Style: Hook,{font_family},{hook_size},&H0066FF00,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,{outline},{outline // 2 + 1},8,{W // 12},{W // 12},{int(H * _hook_top(brand, short))},-1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if hook:
        lines.append(f"Dialogue: 1,{_t(0)},{_t(dur)},Hook,,0,0,0,,{_esc(hook)}")
    groups = chunks([w for w in words if w["s"] < dur and w["w"].strip()], 1 if single else max(1, int(brand.get("words") or 4)))
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else dur
        g_end = min(dur, nxt if nxt - g[-1]["e"] < 0.35 else g[-1]["e"] + 0.15)
        for i, w in enumerate(g):
            start = w["s"] if i else g[0]["s"]
            end = g[i + 1]["s"] if i + 1 < len(g) else g_end
            if end - start < 0.04:
                continue
            txt = " ".join((active + (f"{{\\c{kw_color}}}" if _norm(x["w"]) in kws else "") + _esc(x["w"]) + r"{\r}") if j == i else
                           (f"{{\\c{kw_color}}}{_esc(x['w'])}{{\\r}}" if _norm(x["w"]) in kws else _esc(x["w"]))
                           for j, x in enumerate(g))
            # دخول ناعم لأول كلمة في كل سطر
            fade = FADE_IN if i == 0 else ""
            lines.append(f"Dialogue: 0,{_t(start)},{_t(end)},Cap,,0,0,0,,{fade}{txt}")
    path.write_text("\n".join(lines) + "\n", "utf-8")
    return path


def shift(words, start, end, speed=1.0):
    """كلمات المقطع بزمن المخرج (بعد القص والسرعة)"""
    return [{"w": w["w"], "s": round((w["s"] - start) / speed, 3), "e": round((min(w["e"], end) - start) / speed, 3)}
            for w in words if start - 0.05 <= w["s"] < end]


def font_family(path):
    try:
        from PIL import ImageFont
        return ImageFont.truetype(str(path), 20).getname()[0]
    except Exception:
        return "Lakta Alexandria"
