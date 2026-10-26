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


HOOK_SECONDS = 4.5


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


ORDINALS = {"اولا": 1, "ثانيا": 2, "ثالثا": 3, "رابعا": 4, "خامسا": 5, "سادسا": 6, "سابعا": 7,
            "الاول": 1, "الثاني": 2, "الثالث": 3, "الرابع": 4, "الخامس": 5}
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def ordinals(words):
    """[(زمن، رقم)] عند تعداد المتكلم «أولاً، ثانياً…» بترتيب صحيح فقط"""
    out, want = [], 1
    for w in words:
        x = _norm(w["w"])
        n = ORDINALS.get(x) or (ORDINALS.get(x[1:]) if x[:1] in "وف" else None)
        if n == want or (n and out and n == out[-1][1] + 1):
            out.append((w["s"], n))
            want = n + 1
    return out if len(out) >= 2 else []


def _circle(r):
    k = round(r * 0.5523)
    return (f"m 0 {-r} b {k} {-r} {r} {-k} {r} 0 b {r} {k} {k} {r} 0 {r} b {-k} {r} {-r} {k} {-r} 0 b {-r} {-k} {-k} {-r} 0 {-r}")


def find_phrase(words, phrase):
    """(بداية، نهاية) أول ظهور للعبارة في الكلمات المنطوقة بمطابقة متسامحة"""
    toks = [_norm(t) for t in str(phrase or "").split() if _norm(t)]
    if len(toks) < 2:
        return None
    best, at = 0.0, None
    for i in range(len(words) - len(toks) + 1):
        win = words[i:i + len(toks)]
        sc = sum(_norm(w["w"]) == t for w, t in zip(win, toks)) / len(toks)
        if sc > best:
            best, at = sc, (win[0]["s"], win[-1]["e"])
    return at if best >= 0.6 else None


def _bgr(hex_rgb, default):
    return "&H00" + _ass_color(hex_rgb, default)[2:-1]


def build(path, words, dur, W, H, brand, font_family, hook=None, keywords=None, extras=None):
    """ملف ASS: ترجمة كاريوكي كلمة بكلمة + خطاف علوي
    extras: punch=(s,e,نص) عبارة الذروة · title/part بطاقة العنوان · outro بطاقة الختام · layout"""
    ex = extras or {}
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
    side_l = side_r = W // 12
    if short and brand.get("safe_zone", True):
        # المنطقة الآمنة: فوق شريط الوصف وبعيداً عن أزرار المنصات على اليمين
        margin_v, side_r, side_l = max(margin_v, int(H * 0.30)), int(W * 0.14), int(W * 0.07)
    cap_align = 2
    if short and ex.get("layout") == "split":
        cap_align, margin_v = 5, 0
    kws = {_norm(k) for k in (keywords or []) if _norm(k)}
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font_family},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,{outline},{outline // 2 + 1},{cap_align},{side_l},{side_r},{margin_v},-1",
        f"Style: Hook,{font_family},{hook_size},&H0066FF00,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,{outline},{outline // 2 + 1},8,{side_l},{side_r},{int(H * _hook_top(brand, short))},-1",
        f"Style: Punch,{font_family},{int(W * 0.14) if short else int(H * 0.13)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,"
        f"{outline + 2},{outline // 2 + 2},5,{W // 10},{W // 10},0,-1",
        f"Style: Card,{font_family},{int(W * 0.085) if short else int(H * 0.08)},{_bgr(tpl.get('ink'), '&HF6EBD0&')},&H00FFFFFF,"
        f"{_bgr(tpl.get('base'), '&H3D1B0E&')},&H00000000,0,0,0,0,100,100,0,0,3,{max(14, W // 40)},0,5,{W // 9},{W // 9},0,-1",
        f"Style: Trans,{font_family},{int(size * 0.5)},&H00E8E8E8,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,"
        f"{max(2, outline // 2)},1,{cap_align},{side_l},{side_r},{max(0, margin_v - int(size * 0.75)) if cap_align == 2 else 0},-1",
        f"Style: LName,{font_family},{int(W * 0.05) if short else int(H * 0.055)},&H00FFFFFF,&H00FFFFFF,{_bgr(tpl.get('accent'), '&H4AA2C9&')},"
        f"&H00000000,0,0,0,0,100,100,0,0,3,{max(10, W // 70)},0,3,{side_l},{side_r},{margin_v + int(size * 2.6)},-1",
        f"Style: LRole,{font_family},{int(W * 0.036) if short else int(H * 0.04)},{_bgr(tpl.get('ink'), '&HF6EBD0&')},&H00FFFFFF,"
        f"{_bgr(tpl.get('base'), '&H3D1B0E&')},&H00000000,0,0,0,0,100,100,0,0,3,{max(8, W // 90)},0,3,{side_l},{side_r},{margin_v + int(size * 1.75)},-1",
        f"Style: Sub,{font_family},{int(W * 0.055) if short else int(H * 0.055)},{_bgr(tpl.get('accent'), '&H4AA2C9&')},&H00FFFFFF,"
        f"&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,{max(2, outline // 2)},0,5,{W // 9},{W // 9},0,-1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if hook:
        he = min(dur, HOOK_SECONDS)
        lines.append(f"Dialogue: 1,{_t(0)},{_t(he)},Hook,,0,0,0,,{{\\fad(120,350)}}{_esc(hook)}")
    punch = ex.get("punch")
    if punch:
        ps, pe, ptxt = punch
        pe = min(dur, max(pe, ps + 1.2), ps + 3.0)
        # عبارة الذروة: تعتيم كامل للشاشة ثم دخول الخط الضخم بتكبير
        lines.append(f"Dialogue: 3,{_t(ps)},{_t(pe)},Punch,,0,0,0,,{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c&H000000&\\1a&H78&\\fad(90,160)}}"
                     f"m 0 0 l {W} 0 {W} {H} 0 {H}{{\\p0}}")
        lines.append(f"Dialogue: 4,{_t(ps)},{_t(pe)},Punch,,0,0,0,,{{\\fad(60,160)\\fscx150\\fscy150\\t(0,220,\\fscx100\\fscy100)}}"
                     + " ".join(f"{{\\c{kw_color}}}{_esc(x)}{{\\c&HFFFFFF&}}" if _norm(x) in kws else _esc(x) for x in ptxt.split()))
    if ex.get("title"):
        # بطاقة العنوان: تنزلق للأعلى مع تلاشٍ
        y = int(H * 0.42)
        mv = f"\\move({W // 2},{y + 40},{W // 2},{y},0,320)"
        lines.append(f"Dialogue: 5,{_t(0.05)},{_t(min(dur, 1.9))},Card,,0,0,0,,{{\\an5{mv}\\fad(160,260)}}{_esc(ex['title'])}")
        if ex.get("part"):
            sy = y - int(W * 0.09 if short else H * 0.11)
            lines.append(f"Dialogue: 5,{_t(0.05)},{_t(min(dur, 1.9))},Sub,,0,0,0,,{{\\an5\\move({W // 2},{sy + 40},{W // 2},{sy},0,320)\\fad(160,260)}}{_esc(ex['part'])}")
    marks = ex.get("marks") or []
    for i, (t, num) in enumerate(marks):
        # شارة رقم متحركة عند كل نقطة يعدّدها المتكلم، مع حلقة تتسع حولها
        end = min(dur, marks[i + 1][0] if i + 1 < len(marks) else t + 3.0, t + 3.0)
        if t >= dur or end - t < 0.4:
            continue
        r = int(W * (0.075 if short else 0.045))
        cx, cy = W - side_r - r, int(H * (0.24 if short else 0.2))
        acc, ink = _ass_color(tpl.get("accent"), "&H4AA2C9&"), _ass_color(tpl.get("ink"), "&HF6EBD0&")
        lines.append(f"Dialogue: 7,{_t(t)},{_t(end)},Punch,,0,0,0,,{{\\an7\\pos({cx},{cy})\\p1\\bord0\\shad3\\1c{acc}"
                     f"\\fscx20\\fscy20\\t(0,220,\\fscx100\\fscy100)\\fad(0,200)}}{_circle(r)}{{\\p0}}")
        lines.append(f"Dialogue: 7,{_t(t)},{_t(min(end, t + 0.9))},Punch,,0,0,0,,{{\\an7\\pos({cx},{cy})\\p1\\bord0\\shad0\\1a&HFF&"
                     f"\\3c{acc}\\bord4\\t(0,900,\\fscx210\\fscy210\\3a&HFF&)}}{_circle(r)}{{\\p0}}")
        lines.append(f"Dialogue: 8,{_t(t)},{_t(end)},Punch,,0,0,0,,{{\\an5\\pos({cx},{cy + r // 12})\\fs{int(r * 1.25)}\\bord0\\shad0"
                     f"\\1c{ink}\\fscx20\\fscy20\\t(60,280,\\fscx100\\fscy100)\\fad(0,200)}}{''.join(AR_DIGITS[int(d)] for d in str(num))}")
    sp = ex.get("speaker") or {}
    if sp.get("name") and dur > 6:
        # شريط اسم المتكلم: ينزلق من الطرف ويبقى ثلاث ثوانٍ
        a, b = 0.9, min(dur, 4.6)
        lines.append(f"Dialogue: 6,{_t(a)},{_t(b)},LName,,0,0,0,,{{\\fad(220,260)\\fsp1}}{_esc(sp['name'])}")
        if sp.get("role"):
            lines.append(f"Dialogue: 6,{_t(a + 0.15)},{_t(b)},LRole,,0,0,0,,{{\\fad(260,260)}}{_esc(sp['role'])}")
    for ts, te, tt in ex.get("trans") or []:
        if ts < dur and tt:
            # سطر الترجمة الثانية أسفل الترجمة العربية
            pos = f"{{\\an5\\pos({W // 2},{H // 2 + int(size * 1.15)})}}" if cap_align == 5 else ""
            lines.append(f"Dialogue: 0,{_t(ts)},{_t(min(dur, te))},Trans,,0,0,0,,{pos}{_esc(tt)}")
    if ex.get("outro") and dur > 6:
        y = int(H * 0.5)
        lines.append(f"Dialogue: 5,{_t(dur - 2.2)},{_t(dur)},Card,,0,0,0,,{{\\an5\\move({W // 2},{y + 50},{W // 2},{y},0,300)\\fad(220,0)}}{_esc(ex['outro'])}")
    groups = chunks([w for w in words if w["s"] < dur and w["w"].strip()], 1 if single else max(1, int(brand.get("words") or 4)))
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else dur
        g_end = min(dur, nxt if nxt - g[-1]["e"] < 0.35 else g[-1]["e"] + 0.15)
        for i, w in enumerate(g):
            start = w["s"] if i else g[0]["s"]
            end = g[i + 1]["s"] if i + 1 < len(g) else g_end
            if punch:
                # لا ترجمة عادية أثناء عبارة الذروة
                if ps - 0.05 <= start and end <= pe + 0.05:
                    continue
                if start < ps < end:
                    end = ps
                elif start < pe < end:
                    start = pe
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
