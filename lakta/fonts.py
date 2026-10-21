"""مكتبة الخطوط: نسخة ثابتة عريضة من أي خط، وتنزيل خطوط Google العربية"""
import json
import re
import urllib.request
from pathlib import Path

from .env import ensure

ARABIC = (0x0627, 0x0644, 0x0628, 0x0639, 0x0647)
GOOGLE_ARABIC = ["Alexandria", "Almarai", "Amiri", "Aref Ruqaa", "Baloo Bhaijaan 2", "Blaka", "Cairo", "Changa",
                 "El Messiri", "Harmattan", "IBM Plex Sans Arabic", "Jomhuria", "Kufam", "Lalezar", "Lateef", "Lemonada",
                 "Mada", "Marhey", "Markazi Text", "Mirza", "Noto Kufi Arabic", "Noto Naskh Arabic", "Noto Sans Arabic",
                 "Rakkas", "Readex Pro", "Reem Kufi", "Reem Kufi Fun", "Rubik", "Scheherazade New", "Tajawal", "Vibes",
                 "Zain"]
API = "https://api.github.com/repos/google/fonts/contents/ofl/{slug}"
RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl/{slug}/{name}"


def _family_tag(name):
    return re.sub(r"[^A-Za-z0-9]", "", name)


def static(src, dst, family, weight=800):
    """نسخة ثابتة بوزن عريض باسم عائلة فريد (يحتاجها libass و Pillow)"""
    ensure("fontTools", "fonttools")
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    f = TTFont(str(src))
    if "fvar" in f:
        axes = {a.axisTag: a for a in f["fvar"].axes}
        loc = {}
        for tag, a in axes.items():
            loc[tag] = max(a.minValue, min(a.maxValue, weight)) if tag == "wght" else a.defaultValue
        f = instancer.instantiateVariableFont(f, loc)
    if not all(u in f.getBestCmap() for u in ARABIC):
        raise ValueError("الخط لا يدعم الحروف العربية")
    for rec in f["name"].names:
        if rec.nameID in (1, 16, 4):
            rec.string = family
        elif rec.nameID in (2, 17):
            rec.string = "Regular"
        elif rec.nameID == 6:
            rec.string = _family_tag(family) + "-Regular"
    f["OS/2"].usWeightClass = 400
    f["OS/2"].fsSelection = (f["OS/2"].fsSelection & ~0b100001) | 0b1000000
    f.save(str(dst))
    return dst


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "lakta-studio"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_google(name, fonts_dir, work):
    """تنزيل خط عربي من مستودع Google Fonts وتحويله لنسخة عريضة ثابتة"""
    name = re.sub(r"\s+", " ", name).strip()[:40]
    if not re.fullmatch(r"[A-Za-z0-9 ]{2,40}", name):
        raise ValueError("اكتب اسم الخط بالإنجليزية كما في Google Fonts")
    slug = name.lower().replace(" ", "")
    tag = _family_tag(name)
    pref = ["Black", "ExtraBold", "Bold", "SemiBold", "Medium", "Regular"]
    try:
        listing = json.loads(_get(API.format(slug=slug)))
        ttfs = [x["name"] for x in listing if x.get("name", "").endswith(".ttf") and "Italic" not in x["name"]]
        var = [t for t in ttfs if "[" in t]
        names = var[:1] or sorted(ttfs, key=lambda t: next((i for i, w in enumerate(pref) if f"-{w}." in t), 99))
    except Exception:
        # واجهة GitHub محدودة الطلبات: تجربة أسماء الملفات المعتادة مباشرة
        names = [f"{tag}[wght].ttf", f"{tag}[wdth,wght].ttf"] + [f"{tag}-{w}.ttf" for w in pref]
    work.mkdir(parents=True, exist_ok=True)
    data, pick = None, None
    for n in names:
        try:
            data, pick = _get(RAW.format(slug=slug, name=urllib.request.quote(n)), timeout=90), n
            break
        except Exception:
            continue
    if not data:
        raise ValueError(f"لم أجد الخط «{name}» في Google Fonts")
    raw = work / pick.replace("[", "_").replace("]", "_").replace(",", "_")
    raw.write_bytes(data)
    dst = fonts_dir / f"Google-{_family_tag(name)}.ttf"
    static(raw, dst, f"Lakta {name}")
    raw.unlink(missing_ok=True)
    return dst.name
