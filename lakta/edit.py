"""المونتاج الذكي: قائمة قطع (EDL) من توقيت الكلمات — حذف الصمت والتأتأة، زوم متناوب، وافتتاحية بالخطاف"""
import re

FILLERS = {"اه", "آه", "أه", "ااه", "ام", "امم", "اممم", "إم", "إمم", "مم", "ممم", "اا", "ااا", "uh", "um", "umm"}
PAD_IN, PAD_OUT, MAX_GAP, MIN_SEG = 0.08, 0.18, 0.35, 0.3
ZOOM = 1.12


def _norm(w):
    return re.sub(r"[^\w؀-ۿ]", "", w).lower()


def _clean(words):
    """حذف كلمات الحشو والتكرار المتتالي (التأتأة)"""
    out = []
    for i, w in enumerate(words):
        n = _norm(w["w"])
        if not n or n in FILLERS:
            continue
        nxt = _norm(words[i + 1]["w"]) if i + 1 < len(words) else None
        if nxt == n and words[i + 1]["s"] - w["e"] < 0.6:
            continue
        out.append(w)
    return out


def segments(words, s, e, silence=True):
    """فترات الإبقاء بزمن المصدر المطلق"""
    inside = [w for w in words if s <= w["s"] < e]
    if not inside:
        return [(s, e)]
    if not silence:
        # بلا حذف: تقسيم عند الوقفات فقط ليتناوب الزوم
        cuts = [s] + [round((a["e"] + b["s"]) / 2, 3) for a, b in zip(inside, inside[1:]) if b["s"] - a["e"] > 0.3] + [e]
        return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a > 0.05]
    kept = _clean(inside) or inside
    spans = []
    for w in kept:
        a, b = max(s, w["s"] - PAD_IN), min(e, w["e"] + PAD_OUT)
        if spans and a - spans[-1][1] < MAX_GAP:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in spans if b - a >= MIN_SEG] or [(s, e)]


def with_cold_open(segs, s, e, hook):
    """تكرار جملة الخطاف في البداية إن لم تكن أصلاً في أول ثانيتين"""
    if not hook:
        return segs, False
    hs, he = hook
    if not (s <= hs < he <= e) or not 1.2 <= he - hs <= 6 or hs - s < 2:
        return segs, False
    return [(hs, he)] + segs, True


def subdivide(segs, words, every=3.5, scenes=()):
    """تقسيم المقاطع الطويلة عند الأنفاس القصيرة (بلا حذف) ليتناوب الزوم
    scenes: قطعات الكاميرا الحقيقية تصبح نقاط تبديل الزوم، ولا يقع تبديل قريباً منها"""
    out = []
    for a, b in segs:
        cand = [(round(c, 3), True) for c in scenes if a + 0.8 < c < b - 0.8]
        for w1, w2 in zip(words, words[1:]):
            mid = (w1["e"] + w2["s"]) / 2
            if a < mid < b and w2["s"] - w1["e"] >= 0.12 and b - mid >= 1.5 and all(abs(mid - c) > 0.6 for c in scenes):
                cand.append((round(mid, 3), False))
        cuts = [a]
        for c, hard in sorted(cand):
            if c - cuts[-1] >= (0.8 if hard else every):
                cuts.append(c)
        cuts.append(b)
        out += list(zip(cuts, cuts[1:]))
    return out


def zooms(segs, cold):
    flags, alt = [], True
    for i, (a, b) in enumerate(segs):
        if cold and i == 0:
            flags.append(True)
            continue
        alt = not alt if b - a >= 0.9 else alt
        flags.append(alt and b - a >= 0.9)
    return flags


def total(segs):
    return sum(b - a for a, b in segs)


def remap(words, segs, speed=1.0):
    """توقيت الكلمات على الخط الزمني الجديد بعد القص"""
    out, off = [], 0.0
    for a, b in segs:
        for w in words:
            if a - 0.02 <= w["s"] < b:
                out.append({"w": w["w"], "s": round((off + w["s"] - a) / speed, 3), "e": round((off + min(w["e"], b) - a) / speed, 3)})
        off += b - a
    return out


def plan(words, s, e, opts, hook=None, scenes=None):
    """قائمة القطع النسبية (من بداية المقطع) + أعلام الزوم"""
    ed = opts.get("edit") or {}
    if not any(ed.get(k) for k in ("silence", "zoom", "cold_open")):
        return None
    segs = segments(words, s, e, bool(ed.get("silence")))
    cold = False
    if ed.get("cold_open"):
        segs, cold = with_cold_open(segs, s, e, hook)
    audio_segs = segs
    if ed.get("zoom"):
        inside = [w for w in words if s <= w["s"] < e]
        sc = [s + t for t in (scenes or [])] if ed.get("scenes", True) else []
        segs = segs[:1] + subdivide(segs[1:], inside, scenes=sc) if cold else subdivide(segs, inside, scenes=sc)
    z = zooms(segs, cold) if ed.get("zoom") else [False] * len(segs)
    if len(segs) == 1 and not z[0] and abs(segs[0][0] - s) < 0.01 and abs(segs[0][1] - e) < 0.01:
        return None
    rel = lambda ss: [(round(a - s, 3), round(b - s, 3)) for a, b in ss]
    # الصوت يُقص عند القطعات الحقيقية فقط؛ تقسيمات الزوم متصلة فلا تحتاج تلاشياً
    return {"segs": rel(segs), "abs": segs, "zoom": z, "cold": cold, "audio": rel(audio_segs),
            "smooth": bool(ed.get("zoom") and ed.get("smooth", True))}
