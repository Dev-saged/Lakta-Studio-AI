import json
import re

from config import CONFIG

_clients = {}
_resolved = {}


def client(key):
    from google import genai
    if not key:
        raise RuntimeError("أضف مفتاح Gemini من الإعدادات أو Secrets باسم GEMINI_API_KEY")
    if key not in _clients:
        _clients[key] = genai.Client(api_key=key)
    return _clients[key]


def friendly(e, kind="text"):
    """رسالة عربية واضحة لأخطاء Gemini الشائعة"""
    m = str(e)
    low = m.lower()
    if "429" in m or "resource_exhausted" in low or "quota" in low:
        if kind == "image":
            return "توليد الصور غير متاح بمفتاح Gemini الحالي (الخطة المجانية لا تشمله)."
        return "تجاوزت حصة Gemini المتاحة لمفتاحك الآن. انتظر دقيقة وأعد المحاولة، أو راجع الحصة في ai.dev/rate-limit."
    if "api key not valid" in low or "api_key_invalid" in low or "permission_denied" in low or "403" in m:
        return "مفتاح Gemini غير صالح أو لا يملك صلاحية لهذا النموذج. تحقق منه في الإعدادات."
    if "not found" in low and "model" in low or "404" in m:
        return "النموذج غير متاح لمفتاحك. اختر نموذجاً آخر من الإعدادات بعد زر «اختبار»."
    if "503" in m or "unavailable" in low or "overloaded" in low:
        return "خوادم Gemini مزدحمة الآن. أعد المحاولة بعد قليل."
    return m[:300]


def list_models(key):
    return sorted(m.name.split("/")[-1] for m in client(key).models.list())


def _ver(name, pattern):
    m = re.match(pattern, name)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def resolve(settings, kind):
    """النموذج المحدد في الإعدادات، وإلا أحدث نموذج متاح يطابق النمط"""
    own = settings.get(f"{kind}_model")
    if own:
        return own
    key = settings.get("gemini_key")
    if (key, kind) in _resolved:
        return _resolved[(key, kind)]
    pick = CONFIG["models"][kind]
    try:
        names = list_models(key)
        if kind == "text" and pick in names:
            _resolved[(key, kind)] = pick
            return pick
        pat = CONFIG["models"][f"{kind}_pattern"]
        cands = [(v, n) for n in names if (v := _ver(n, pat))]
        if cands:
            pick = max(cands)[1]
    except Exception:
        pass
    _resolved[(key, kind)] = pick
    return pick


def test_key(key):
    names = list_models(key)
    return {"ok": True, "count": len(names),
            "text": [n for n in names if _ver(n, CONFIG["models"]["text_pattern"])][-6:] + (["gemini-flash-latest"] if "gemini-flash-latest" in names else []),
            "tts": [n for n in names if _ver(n, CONFIG["models"]["tts_pattern"])]}


def ask_json(settings, prompt, schema, temperature=0.7):
    from google.genai import types
    key = settings.get("gemini_key")
    model = resolve(settings, "text")
    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=temperature)
    r = client(key).models.generate_content(model=model, contents=prompt, config=cfg)
    txt = (r.text or "").strip()
    m = re.search(r"[\[{].*[\]}]", txt, re.S)
    return json.loads(m.group(0) if m else txt)


CLIP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "seo": {"type": "OBJECT", "properties": {
            "title": {"type": "STRING"}, "thumb": {"type": "STRING"}, "description": {"type": "STRING"},
            "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["title", "thumb", "description", "hashtags"]},
        "clips": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"}, "title": {"type": "STRING"},
            "hook": {"type": "STRING"}, "thumb": {"type": "STRING"}, "highlight": {"type": "STRING"},
            "open_start": {"type": "NUMBER"}, "open_end": {"type": "NUMBER"},
            "keywords": {"type": "ARRAY", "items": {"type": "STRING"}}, "punch": {"type": "STRING"},
            "alt_titles": {"type": "ARRAY", "items": {"type": "STRING"}}, "mood": {"type": "STRING", "enum": ["calm", "neutral", "energetic"]},
            "speaker": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}, "role": {"type": "STRING"}}},
            "score": {"type": "INTEGER"}, "reason": {"type": "STRING"}},
            "required": ["start", "end", "title", "hook", "thumb", "score"]}},
    },
    "required": ["seo", "clips"],
}


def _compact(segments, limit=180_000):
    lines, size = [], 0
    for s in segments:
        line = f"[{s['s']:.1f}-{s['e']:.1f}] {s['t']}"
        size += len(line)
        if size > limit:
            break
        lines.append(line)
    return "\n".join(lines)


def pick_clips(settings, transcript_segments, duration, parts, title, description, log):
    c = CONFIG["clip"]
    prompt = f"""أنت مخرج محتوى قصير محترف (Shorts/Reels/TikTok) ومتخصص SEO عربي.
عنوان الفيديو الأصلي: {title}
وصفه: {description[:600]}

هذا تفريغ زمني كامل للفيديو (بالثواني):
{_compact(transcript_segments)}

المطلوب:
1) اختر أقوى {parts} لقطات منفصلة غير متداخلة. كل لقطة {c['target']} ثانية، تبدأ ببداية جملة وتنتهي بنهاية فكرة مكتملة، وتصلح للمشاهدة منفردة دون سياق.
   الأفضلية: لحظة صادمة، معلومة مفاجئة، قصة قصيرة، رأي قوي، أو سؤال يثير الفضول في أول 3 ثوانٍ.
2) لكل لقطة: عنوان جذاب (أقل من 70 حرفاً)، وخطاف (hook) من 3-5 كلمات سؤال مستفز أو معلومة صادمة، ونص غلاف من 2-4 كلمات قوية، وhighlight: الكلمة الأقوى من نص الغلاف (تُكتب كما هي) لتُلوَّن، ودرجة فيروسية 0-100، وسبب الاختيار.
   وopen_start/open_end: أقوى جملة صادمة داخل اللقطة (2-5 ثوانٍ، ليست في بدايتها) لتُعرض كافتتاحية قبل اللقطة.
   وkeywords: من 3 إلى 5 كلمات مفردة مؤثرة منطوقة داخل اللقطة (تُنسخ حرفياً كما في التفريغ) لتُلوَّن في الترجمة.
   وmood: مزاج اللقطة للموسيقى (calm هادئ، neutral متوسط، energetic حماسي).
   وalt_titles: 3 عناوين بديلة بزوايا مختلفة (سؤال، رقم، صدمة). وspeaker: اسم المتكلم الرئيسي في اللقطة وصفته إن ذُكرا صراحة في الحلقة، وإلا اتركهما فارغين.
   وpunch: عبارة الذروة في اللقطة، من 2 إلى 5 كلمات متتالية منطوقة حرفياً كما في التفريغ، تُعرض بخط ضخم يملأ الشاشة.
3) SEO للفيديو كاملاً: عنوان قوي، نص غلاف (3 كلمات)، وصف جذاب 2-3 أسطر، و8 هاشتاقات عربية بدون #.
ممنوع الإيموجي والرموز مثل ** في كل النصوص. الأزمنة بالثواني وضمن 0 إلى {duration:.0f}."""
    data = ask_json(settings, prompt, CLIP_SCHEMA)
    clips = validate(data.get("clips") or [], transcript_segments, duration, parts)
    seo = data.get("seo") or {}
    log(f"المونتير اختار {len(clips)} لقطات")
    return clips, {k: _clean(v) if isinstance(v, str) else [_clean(x).lstrip("#").replace(" ", "_") for x in v]
                   for k, v in seo.items()}


def _clean(s):
    return re.sub(r"[*_`#]{2,}|[\U0001F300-\U0001FAFF☀-➿]", "", str(s)).strip()


def validate(raw, segments, duration, parts):
    c = CONFIG["clip"]
    cmin = min(c["min"], duration / max(1, parts) * 0.9)
    starts = [s["s"] for s in segments]
    ends = [s["e"] for s in segments]
    out = []
    for r in sorted(raw, key=lambda x: -int(x.get("score") or 0)):
        try:
            s, e = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        s = max(0.0, min(s, duration - 5))
        s = max([x for x in starts if x <= s + 0.5] or [s])
        e = min([x for x in ends if x >= e - 0.5] or [e], default=e)
        e = min(e, duration)
        e = min(max(e, s + cmin), s + c["max"], duration)
        for o in out:
            if not (e <= o["s"] or s >= o["e"]):
                s, e = (s, min(e, o["s"])) if s < o["s"] else (max(s, o["e"]), e)
        if e - s < 8:
            continue
        try:
            op = (float(r["open_start"]), float(r["open_end"]))
            op = op if s <= op[0] < op[1] <= e else None
        except (KeyError, TypeError, ValueError):
            op = None
        out.append({"s": round(s, 2), "e": round(e, 2), "open": op, "title": _clean(r.get("title", "")), "hook": _clean(r.get("hook", "")),
                    "thumb": _clean(r.get("thumb", "")), "highlight": _clean(r.get("highlight", "")),
                    "keywords": [_clean(k) for k in (r.get("keywords") or []) if isinstance(k, str) and _clean(k)][:5],
                    "punch": _clean(r.get("punch", "")) if isinstance(r.get("punch"), str) else "",
                    "mood": r.get("mood") if r.get("mood") in ("calm", "neutral", "energetic") else "neutral",
                    "alt_titles": [_clean(t)[:100] for t in (r.get("alt_titles") or []) if isinstance(t, str) and _clean(t)][:3],
                    "speaker": {k: _clean((r.get("speaker") or {}).get(k, ""))[:60] for k in ("name", "role")}
                    if isinstance(r.get("speaker"), dict) else {"name": "", "role": ""},
                    "score": max(0, min(100, int(r.get("score") or 0))),
                    "reason": _clean(r.get("reason", ""))})
        if len(out) == parts:
            break
    return sorted(out, key=lambda x: x["s"])


def fallback_clips(duration, parts, segments):
    # فتحات متساوية غير متداخلة، لقطة في منتصف كل فتحة
    slot = duration / parts
    length = min(50.0, slot * 0.9)
    raw = [{"start": slot * i + (slot - length) / 2, "end": slot * i + (slot + length) / 2, "title": "", "hook": "", "thumb": "", "score": 50}
           for i in range(parts)]
    return validate(raw, segments, duration, parts) or [{"s": 0.0, "e": min(duration, length), "title": "", "hook": "", "thumb": "", "score": 50, "reason": ""}]


def rewrite(settings, text, mode, dialect, seconds):
    d = CONFIG["dialects"].get(dialect, {}).get("prompt")
    if mode == "same" and not d:
        return text
    task = ("انقل الكلام التالي حرفياً قدر الإمكان إلى " + d + "، بنفس الأفكار والترتيب والطول تقريباً") if mode == "same" else \
        ("أعد صياغة الكلام التالي بأسلوب أقوى وأوضح وأكثر تشويقاً" + (f" بـ{d}" if d else " بنفس لهجة المتكلم") + "، بنفس المعنى")
    words = len(text.split())
    prompt = f"""{task}.
يُقرأ بصوت معلّق خلال {seconds:.0f} ثانية تقريباً، فاجعل عدد الكلمات قريباً من {words} كلمة.
اكتب نصاً منطوقاً طبيعياً فقط، بدون عناوين أو شرح أو إيموجي أو علامات تنسيق.
النص:
{text}"""
    data = ask_json(settings, prompt, {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}, 0.6)
    return _clean(data.get("text") or text)


def alt_clip(settings, segments, duration, taken, title):
    """لقطة بديلة لا تتداخل مع اللقطات الحالية"""
    c = CONFIG["clip"]
    busy = "، ".join(f"{a:.0f}-{b:.0f}" for a, b in taken) or "لا شيء"
    prompt = f"""أنت مخرج محتوى قصير محترف. عنوان الفيديو: {title}
التفريغ الزمني:
{_compact(segments)}

اختر لقطة واحدة جديدة قوية {c['target']} ثانية لا تتداخل أبداً مع هذه الفترات المستخدمة (بالثواني): {busy}.
املأ نفس الحقول: عنوان، hook، thumb، highlight، keywords، punch، open_start/open_end، score، reason.
الأزمنة ضمن 0 إلى {duration:.0f}. ممنوع الإيموجي."""
    data = ask_json(settings, prompt, CLIP_SCHEMA)
    free = lambda s, e: all(e <= a or s >= b for a, b in taken)
    for r in validate(data.get("clips") or [], segments, duration, 3):
        if free(r["s"], r["e"]):
            return r
    return None


def free_slot(segments, duration, taken):
    """بديل بدون Gemini: أوسع فراغ غير مستخدم، بحدود جمل كاملة داخله"""
    edges = sorted([(0.0, 0.0)] + [tuple(t) for t in taken] + [(duration, duration)])
    size, a, b = max((edges[i + 1][0] - edges[i][1], edges[i][1], edges[i + 1][0]) for i in range(len(edges) - 1))
    if size < 12:
        return None
    mid, half = (a + b) / 2, min(25.0, size * 0.48)
    s0 = min([x["s"] for x in segments if a <= x["s"] <= mid - half + 2] or [mid - half], key=lambda v: abs(v - (mid - half)))
    e0 = min([x["e"] for x in segments if mid + half - 2 <= x["e"] <= b] or [mid + half], key=lambda v: abs(v - (mid + half)))
    s0, e0 = max(a, s0), min(b, e0)
    if e0 - s0 < 8:
        return None
    return {"s": round(s0, 2), "e": round(e0, 2), "open": None, "title": "", "hook": "", "thumb": "", "highlight": "",
            "keywords": [], "punch": "", "score": 50, "reason": ""}


LANGS = {"en": "English", "fr": "French", "tr": "Turkish"}


def translate_lines(settings, lines, lang="en"):
    """ترجمة سطور الترجمة بنفس الترتيب والعدد"""
    if not lines:
        return []
    prompt = f"""Translate each Arabic subtitle line into natural, concise {LANGS.get(lang, "English")} for on-screen captions.
Return exactly {len(lines)} items in the same order. No emojis, no quotes.
""" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
    data = ask_json(settings, prompt, {"type": "OBJECT", "properties": {"lines": {"type": "ARRAY", "items": {"type": "STRING"}}},
                                       "required": ["lines"]}, 0.3)
    out = [_clean(x)[:90] for x in (data.get("lines") or [])]
    return out if len(out) == len(lines) else []


PACK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "clips": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "n": {"type": "INTEGER"},
            "youtube_title": {"type": "STRING"}, "youtube_description": {"type": "STRING"},
            "youtube_tags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "tiktok_caption": {"type": "STRING"}, "instagram_caption": {"type": "STRING"},
            "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["n"]}},
        "chapters": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "t": {"type": "NUMBER"}, "title": {"type": "STRING"}}, "required": ["t", "title"]}},
    },
    "required": ["clips", "chapters"],
}


def publish_pack(settings, clips, seo, segments, title, duration):
    """نصوص نشر لكل منصة لكل لقطة + فصول زمنية للحلقة كاملة"""
    lst = "\n".join(f"{c['n']}) {c['title']} · {c.get('hook', '')} · [{c['s']:.0f}-{c['e']:.0f}]" for c in clips)
    prompt = f"""أنت مسؤول نشر محترف لقناة عربية. الحلقة: {title}
وصفها: {seo.get('description', '')}
اللقطات:
{lst}

التفريغ الزمني للحلقة:
{_compact(segments, 120_000)}

المطلوب:
1) لكل لقطة n: youtube_title (أقل من 70 حرفاً)، youtube_description (3-4 أسطر مع دعوة للاشتراك)، youtube_tags (10 كلمات)،
   tiktok_caption (سطر جذاب قصير)، instagram_caption (2-3 أسطر)، hashtags (8 بدون #).
2) chapters: فصول للحلقة الكاملة (5-12 فصلاً) بأزمنة بالثواني تبدأ من 0 وضمن {duration:.0f}، بعناوين قصيرة واضحة.
ممنوع الإيموجي وعلامات التنسيق."""
    data = ask_json(settings, prompt, PACK_SCHEMA, 0.6)
    chapters = sorted({round(float(c["t"])): _clean(c["title"])[:80] for c in data.get("chapters") or []
                       if 0 <= float(c.get("t", -1)) < duration and _clean(c.get("title", ""))}.items())
    if chapters and chapters[0][0] != 0:
        chapters.insert(0, (0, "المقدمة"))
    return {int(c.get("n", 0)): c for c in data.get("clips") or []}, chapters
