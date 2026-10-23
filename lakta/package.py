"""تغليف المشروع: مجلد لكل جزء بأسماء عربية واضحة، وملفات تيليجرام مقسّمة حسب الجزء"""
import zipfile
from pathlib import Path

from .thumbs import part_word

FMT = {"short": "شورتس", "wide": "عريض", "square": "مربع", "portrait": "منشور عمودي"}
PUB = {"yt": "نشر يوتيوب", "tt": "نشر تيك توك", "ig": "نشر إنستغرام"}
MARGIN = 256 * 1024


def entries(env, job_id, clips):
    """[(المسار، الاسم داخل المضغوط، رقم الجزء)] — الأسماء الداخلية في السيرفر لا تتغير"""
    out = env.outputs / job_id
    res = []
    for c in clips:
        n = c["n"]
        folder = f"الجزء {part_word(n)}"
        f = c.get("files") or {}
        for fmt, label in FMT.items():
            if f.get(fmt):
                res.append((env.outputs / f[fmt], f"{folder}/{label}.mp4", n))
        cov = c.get("covers") or {}
        for fmt, label in list(FMT.items())[:2]:
            lst = cov.get(fmt) or ([f[f"thumb_{fmt}"]] if f.get(f"thumb_{fmt}") else [])
            for k, rel in enumerate(lst, 1):
                res.append((env.outputs / rel, f"{folder}/غلاف {label}{f' {k}' if len(lst) > 1 else ''}.jpg", n))
        txt = out / f"_text_{n}.txt"
        txt.write_text(f"العنوان:\n{c.get('full_title') or c.get('title') or ''}\n\nالوصف:\n{c.get('desc') or ''}\n", "utf-8-sig")
        res.append((txt, f"{folder}/العنوان والوصف.txt", n))
        for k, label in PUB.items():
            res.append((out / f"_pub_{n}_{k}.txt", f"{folder}/{label}.txt", n))
    if clips:
        res.append((out / "_chapters.txt", "فصول الحلقة.txt", clips[0]["n"]))
    return [(p, a, n) for p, a, n in res if Path(p).exists()]


def write_zip(items, path):
    with zipfile.ZipFile(path, "w") as z:
        for p, arc, _ in items:
            z.write(p, arc, compress_type=zipfile.ZIP_DEFLATED if str(p).endswith(".txt") else zipfile.ZIP_STORED)
    return path


def telegram_bundles(items, title, work, limit, light=None):
    """ملف لكل جزء، ويُقسَّم الجزء فقط إن تجاوز الحد · الفيديو الأكبر من الحد: نسخة خفيفة أو قائمة"""
    work.mkdir(parents=True, exist_ok=True)
    parts, bundles, big = {}, [], []
    for it in items:
        parts.setdefault(it[2], []).append(it)
    for n in sorted(parts):
        base = f"{title} - الجزء {part_word(n)}"
        fixed = []
        for p, arc, k in parts[n]:
            if Path(p).stat().st_size <= limit - MARGIN:
                fixed.append((p, arc, k))
                continue
            lp = light(p) if light else None
            if lp and Path(lp).stat().st_size <= limit - MARGIN:
                fixed.append((lp, arc.replace(".mp4", " - نسخة خفيفة.mp4"), k))
            else:
                big.append(arc)
        bins = []
        for it in sorted(fixed, key=lambda x: -Path(x[0]).stat().st_size):
            sz = Path(it[0]).stat().st_size
            for b in bins:
                if b[0] + sz <= limit - MARGIN:
                    b[0] += sz
                    b[1].append(it)
                    break
            else:
                bins.append([sz, [it]])
        for i, (_, its) in enumerate(bins, 1):
            name = base if len(bins) == 1 else f"{base} ({i} من {len(bins)})"
            bundles.append(write_zip(sorted(its, key=lambda x: x[1]), work / f"{name}.zip"))
    return bundles, big
