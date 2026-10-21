import glob
import os
from pathlib import Path
from urllib.parse import quote_plus

from .env import ffprobe

TREND_FILTERS = {"today": "EgIIAg%3D%3D", "week": "EgIIAw%3D%3D", "podcast": "EgIYAg%3D%3D"}


def _ydl_opts(settings, env, extra=None):
    o = {"quiet": True, "no_warnings": True, "noplaylist": True, "retries": 5, "fragment_retries": 5,
         "concurrent_fragment_downloads": 4, "socket_timeout": 30}
    cookies = env.assets / "youtube_cookies.txt"
    if cookies.exists():
        o["cookiefile"] = str(cookies)
    o.update(extra or {})
    return o


def find_trend(keyword, flt, settings, env, log):
    import yt_dlp
    url = f"https://www.youtube.com/results?search_query={quote_plus(keyword)}"
    if flt in TREND_FILTERS:
        url += f"&sp={TREND_FILTERS[flt]}"
    with yt_dlp.YoutubeDL(_ydl_opts(settings, env, {"extract_flat": "in_playlist", "playlistend": 20})) as y:
        info = y.extract_info(url, download=False)
    entries = [e for e in (info.get("entries") or []) if e and e.get("id") and (e.get("duration") or 0) >= 90]
    if not entries:
        raise RuntimeError(f"لم أجد فيديوهات مناسبة لكلمة «{keyword}»")
    best = max(entries, key=lambda e: e.get("view_count") or 0)
    log(f"التريند: «{best.get('title', '')}» · {best.get('view_count') or 0:,} مشاهدة")
    return f"https://www.youtube.com/watch?v={best['id']}"


def download(url, work, settings, env, log, progress):
    import yt_dlp
    h = int(settings.get("resolution") or 1080)

    def hook(d):
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if tot:
                progress(min(0.99, d.get("downloaded_bytes", 0) / tot))

    opts = _ydl_opts(settings, env, {
        "format": f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b",
        "merge_output_format": "mp4", "outtmpl": str(work / "source.%(ext)s"),
        "writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": ["ar", "ar-orig"], "subtitlesformat": "vtt",
        "progress_hooks": [hook],
    })
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
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
