import glob
import os
import time
from pathlib import Path
from urllib.parse import quote_plus

from .env import ffprobe

TREND_FILTERS = {"today": "EgIIAg%3D%3D", "week": "EgIIAw%3D%3D", "podcast": "EgIYAg%3D%3D"}


# عملاء يوتيوب بالترتيب: الافتراضي ثم بدائل تتجاوز فحص البوت أحياناً على سيرفرات مراكز البيانات
CLIENTS = [None, ["tv_simply", "web_safari"], ["android_vr"], ["web_embedded", "mweb"]]
AUTH_HINTS = ("sign in to confirm", "not a bot", "confirm your age", "cookies", "login required", "http error 429")
AUTH_ERROR = ("يوتيوب يحجب سيرفر {env} ويطلب تسجيل الدخول. الحل: ارفع ملف cookies.txt من الإعدادات ← كوكيز يوتيوب، "
              "أو أضفه في Secrets باسم YOUTUBE_COOKIES، أو نزّل الفيديو على جهازك واستخدم «رفع ملف». [youtube-auth]")


def cookies_path(env):
    """ملف الكوكيز المرفوع، وإلا محتوى السر YOUTUBE_COOKIES"""
    up = env.assets / "youtube_cookies.txt"
    if up.exists():
        return up
    from .env import secret
    val = secret("youtube_cookies")[0]
    if val and "youtube.com" in val:
        p = env.temp / "youtube_cookies_secret.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(val if val.endswith("\n") else val + "\n", "utf-8")
        p.chmod(0o600)
        return p
    return None


def _ydl_opts(settings, env, extra=None, client=None):
    o = {"quiet": True, "no_warnings": True, "noplaylist": True, "retries": 5, "fragment_retries": 5,
         "concurrent_fragment_downloads": 4, "socket_timeout": 30}
    ck = cookies_path(env)
    if ck:
        o["cookiefile"] = str(ck)
    if client:
        o["extractor_args"] = {"youtube": {"player_client": client}}
    o.update(extra or {})
    return o


def _is_auth(err):
    m = str(err).lower()
    return any(h in m for h in AUTH_HINTS)


def _attempts(url, settings, env, log, extra, download):
    """يجرب العملاء بالترتيب، ثم بدون ترجمات، ويرجع رسالة واضحة عند حجب يوتيوب"""
    import yt_dlp
    youtube = "youtu" in url
    last = None
    for i, client in enumerate(CLIENTS if youtube else [None]):
        for subs in (True, False):
            ex = dict(extra)
            if not subs:
                for k in ("writesubtitles", "writeautomaticsub", "subtitleslangs", "subtitlesformat"):
                    ex.pop(k, None)
            try:
                with yt_dlp.YoutubeDL(_ydl_opts(settings, env, ex, client)) as y:
                    return y.extract_info(url, download=download)
            except Exception as e:
                last = e
                msg = str(e).lower()
                if subs and "subtitle" in msg:
                    log("تعذّر تنزيل ترجمة يوتيوب · إعادة المحاولة بدونها")
                    continue
                if youtube and _is_auth(e) and i + 1 < len(CLIENTS):
                    log(f"يوتيوب طلب التحقق · تجربة عميل بديل ({i + 2}/{len(CLIENTS)})")
                    break
                if youtube and _is_auth(e):
                    raise RuntimeError(AUTH_ERROR.format(env=env.name)) from e
                raise
    raise last


def find_trend(keyword, flt, settings, env, log):
    url = f"https://www.youtube.com/results?search_query={quote_plus(keyword)}"
    if flt in TREND_FILTERS:
        url += f"&sp={TREND_FILTERS[flt]}"
    info = _attempts(url, settings, env, log, {"extract_flat": "in_playlist", "playlistend": 20}, False)
    entries = [e for e in (info.get("entries") or []) if e and e.get("id") and (e.get("duration") or 0) >= 90]
    if not entries:
        raise RuntimeError(f"لم أجد فيديوهات مناسبة لكلمة «{keyword}»")
    best = max(entries, key=lambda e: e.get("view_count") or 0)
    log(f"التريند: «{best.get('title', '')}» · {best.get('view_count') or 0:,} مشاهدة")
    return f"https://www.youtube.com/watch?v={best['id']}"


# ترتيب بالمشاهدات ضمن النطاق الزمني (فيديوهات فقط)
TREND_SORT = {"today": "CAMSBAgCEAE%3D", "week": "CAMSBAgDEAE%3D", "": "CAMSAhAB", "podcast": "EgIYAg%3D%3D"}


def search_videos(keyword, flt, settings, env, log, offset=0, limit=3):
    """أقوى الفيديوهات لكلمة مفتاحية بصفحات صغيرة، لاختيار المستخدم"""
    url = f"https://www.youtube.com/results?search_query={quote_plus(keyword)}&sp={TREND_SORT.get(flt, TREND_SORT['week'])}"
    info = _attempts(url, settings, env, log, {"extract_flat": "in_playlist", "playlistend": offset + limit + 12}, False)
    rows, seen = [], set()
    for e in info.get("entries") or []:
        vid = e and e.get("id")
        if not vid or vid in seen or (e.get("duration") or 0) < 90:
            continue
        seen.add(vid)
        ts = e.get("timestamp") or e.get("release_timestamp")
        rows.append({"id": vid, "title": str(e.get("title") or "")[:140], "channel": str(e.get("channel") or e.get("uploader") or "")[:60],
                     "views": int(e.get("view_count") or 0), "duration": int(e.get("duration") or 0),
                     "days": max(0, int((time.time() - ts) // 86400)) if ts else None, "url": f"https://www.youtube.com/watch?v={vid}"})
    if not rows:
        raise RuntimeError(f"لم أجد فيديوهات مناسبة لكلمة «{keyword}»")
    return {"items": rows[offset:offset + limit], "more": len(rows) > offset + limit}


def download(url, work, settings, env, log, progress, note=None, height=None):
    h = int(height or settings.get("resolution") or 1080)
    last = {"t": 0}

    def hook(d):
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if tot:
                progress(min(0.99, d.get("downloaded_bytes", 0) / tot))
            import time
            if note and time.time() - last["t"] > 1.5:
                last["t"] = time.time()
                sp, eta = d.get("speed") or 0, d.get("eta")
                note(f"{sp / 2**20:.1f} MB/s" + (f" · باقي {int(eta)}ث" if eta else ""))

    extra = {
        # فوق 1080 لا يوفر يوتيوب H.264، فنفضّل VP9 لأنه أسرع فكّاً من AV1
        "format": (f"bv*[height<={h}][vcodec^=vp09]+ba/bv*[height<={h}]+ba/b[height<={h}]/b" if h > 1080 else
                   f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b"),
        "merge_output_format": "mkv" if h > 1080 else "mp4", "outtmpl": str(work / "source.%(ext)s"),
        "writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": ["ar", "ar-orig"], "subtitlesformat": "vtt",
        "progress_hooks": [hook],
        # أجزاء 10MB تتفادى خنق السرعة من يوتيوب
        "http_chunk_size": 10 * 2**20,
    }
    info = _attempts(url, settings, env, log, extra, True)
    vids = [p for p in glob.glob(str(work / "source.*")) if p.endswith((".mp4", ".mkv", ".webm"))]
    if not vids:
        raise RuntimeError("فشل تنزيل الفيديو")
    subs = sorted(glob.glob(str(work / "source*.vtt")))
    return {"path": vids[0], "title": info.get("title") or "فيديو", "description": (info.get("description") or "")[:3000],
            "url": info.get("webpage_url") or url, "vtt": subs[0] if subs else None}


def local_file(path, original_name):
    p = Path(path)
    if not p.exists():
        raise RuntimeError("الملف المرفوع غير موجود")
    return {"path": str(p), "title": os.path.splitext(original_name)[0][:120] or "فيديو", "description": "", "url": "", "vtt": None}


def describe(src):
    meta = ffprobe(src["path"])
    if meta["duration"] < 5 or not meta["width"]:
        raise RuntimeError("الفيديو قصير جداً أو تالف")
    return meta
