"""من موضوع إلى سكربت مشاهد: نص مُعلَّق + كلمات بحث إنجليزية لكل لقطة"""
import re

from config import CONFIG
from . import director

SCHEMA = {"type": "OBJECT", "properties": {
    "title": {"type": "STRING"}, "hook": {"type": "STRING"}, "description": {"type": "STRING"},
    "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    "scenes": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "narration": {"type": "STRING"}, "query": {"type": "STRING"}, "alt_query": {"type": "STRING"}},
        "required": ["narration", "query"]}}},
    "required": ["title", "hook", "scenes"]}
WORDS_PER_SEC = 2.4
MAX_SCENES = 10


def _split(text):
    parts = [p.strip() for p in re.split(r"(?<=[.!؟?!])\s+|\n+", text) if p.strip()]
    out = []
    for p in parts:
        if out and len(out[-1].split()) + len(p.split()) <= 18:
            out[-1] += " " + p
        else:
            out.append(p)
    return out[:MAX_SCENES]


def _query(q):
    q = re.sub(r"[^A-Za-z0-9 \-]", " ", str(q or "")).strip()
    return " ".join(q.split()[:4])


def normalize(data, topic):
    """ينظّف ناتج النموذج: نص بلا إيموجي، 3 إلى 10 مشاهد، كلمات بحث لاتينية فقط"""
    scenes = []
    for s in (data.get("scenes") or [])[:MAX_SCENES]:
        n = director._clean(s.get("narration", ""))
        if len(n.split()) >= 2:
            scenes.append({"narration": n, "query": _query(s.get("query")) or "abstract background",
                           "alt_query": _query(s.get("alt_query"))})
    if len(scenes) < 2:
        raise RuntimeError("لم يُنتج النموذج سكربتاً صالحاً · أعد المحاولة")
    hook = director._clean(data.get("hook", "")) or scenes[0]["narration"]
    tags = [("#" + re.sub(r"\W+", "", t)) for t in (data.get("hashtags") or []) if re.sub(r"\W+", "", str(t))][:8]
    return {"title": director._clean(data.get("title", "")) or topic[:60], "hook": hook[:90], "description": director._clean(data.get("description", ""))[:400],
            "hashtags": tags, "scenes": scenes}


def write(settings, topic, seconds=45, dialect="msa"):
    """يكتب سكربتاً قصيراً للمشاهد. يحتاج مفتاح Gemini"""
    d = CONFIG["dialects"].get(dialect, {}).get("prompt") or CONFIG["dialects"]["msa"]["prompt"]
    words = int(seconds * WORDS_PER_SEC)
    prompt = f"""أنت كاتب سيناريو لمقاطع تقنية قصيرة (شورتس/ريلز) تُقرأ بصوت معلّق فوق لقطات جاهزة.
الموضوع: {topic}
اكتب بـ{d}، المدة نحو {seconds} ثانية أي نحو {words} كلمة موزعة على 4 إلى 8 مشاهد.
- المشهد الأول جملة افتتاحية صادمة أو سؤال يشدّ المشاهد في أول ثلاث ثوانٍ، بلا مقدمات.
- كل مشهد جملة أو جملتان قصيرتان منطوقتان بشكل طبيعي، بلا أرقام مكتوبة بالأرقام الهندية الطويلة ولا اختصارات غامضة.
- معلومات صحيحة ومحددة فقط، ولا تخترع إحصاءات أو أسماء. إن لم تكن متأكداً فاكتب بصيغة عامة.
- آخر مشهد خاتمة بدعوة خفيفة للمتابعة.
- query: 2 إلى 4 كلمات إنجليزية تصف لقطة مرئية ملموسة تناسب النص (أشياء وأفعال يمكن تصويرها، مثل "programmer typing code at night")، وليس مفهوماً مجرداً. alt_query بديل مختلف.
- hook: عنوان قصير للغلاف. title و description و hashtags للنشر.
ممنوع الإيموجي وعلامات التنسيق."""
    return normalize(director.ask_json(settings, prompt, SCHEMA, 0.8), topic)


def from_text(text, topic=""):
    """سكربت يكتبه المستخدم بنفسه: يُقسَّم إلى مشاهد، وكلمات البحث من الموضوع إن كان لاتينياً"""
    scenes = [{"narration": director._clean(p), "query": _query(topic) or "abstract background", "alt_query": ""} for p in _split(text)]
    if len(scenes) < 2:
        raise RuntimeError("اكتب جملتين على الأقل")
    return {"title": (topic or scenes[0]["narration"])[:60], "hook": scenes[0]["narration"][:90], "description": "", "hashtags": [], "scenes": scenes}
