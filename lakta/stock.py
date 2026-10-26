"""مزوّدو اللقطات المجانية: Pexels ← Pixabay ← Wikimedia (بلا مفتاح). المفاتيح اختيارية والكاش محلي"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "LaktaStudio/1.0 (https://github.com/Dev-saged/Lakta-Studio-AI)"
OPEN_LICENSE = re.compile(r"^(cc[ -]?by(-sa)?|cc0|public domain|pd)", re.I)
CACHE_LIMIT = 1.5 * 2**30


class ProviderError(Exception):
    pass


def _get(url, headers=None, timeout=25, limit=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(limit) if limit else r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(min(int(e.headers.get("Retry-After") or 3), 8))
                continue
            raise ProviderError("المفتاح غير صالح" if e.code in (401, 403) else "تجاوزتَ حد الطلبات مؤقتاً" if e.code == 429 else f"HTTP {e.code}")
        except Exception as e:
            raise ProviderError(str(e)[:120])


def _json(url, headers=None):
    return json.loads(_get(url, headers).decode("utf-8"))


def _clip(provider, vid, kind, url, page, w, h, dur, name, link, thumb=""):
    return {"provider": provider, "id": str(vid), "kind": kind, "url": url, "page": page, "w": int(w or 0), "h": int(h or 0),
            "dur": float(dur or 0), "credit": {"name": str(name or "")[:80], "url": link or page}, "thumb": thumb}


def _best_file(files, w_key="width", h_key="height"):
    """أقرب ملف لـ1920 على الضلع الطويل دون تجاوز 2560"""
    ok = [f for f in files if f.get("link") or f.get("url")]
    ok = [f for f in ok if max(f.get(w_key) or 0, f.get(h_key) or 0) <= 2560] or ok
    return min(ok, key=lambda f: abs(max(f.get(w_key) or 0, f.get(h_key) or 0) - 1920)) if ok else None


def _pexels(key, q, orient, kind, limit):
    qs = urllib.parse.urlencode({"query": q, "orientation": orient, "per_page": min(limit, 30)})
    if kind == "video":
        d = _json(f"https://api.pexels.com/v1/videos/search?{qs}&size=medium", {"Authorization": key})
        out = []
        for v in d.get("videos", []):
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"]
            f = _best_file(files)
            if f:
                out.append(_clip("pexels", v["id"], "video", f["link"], v.get("url"), f.get("width"), f.get("height"), v.get("duration"),
                                 (v.get("user") or {}).get("name"), (v.get("user") or {}).get("url"), v.get("image", "")))
        return out
    d = _json(f"https://api.pexels.com/v1/search?{qs}", {"Authorization": key})
    return [_clip("pexels", p["id"], "image", (p.get("src") or {}).get("large2x") or p["src"]["large"], p.get("url"), p.get("width"), p.get("height"), 0,
                  p.get("photographer"), p.get("photographer_url"), (p.get("src") or {}).get("medium", "")) for p in d.get("photos", [])]


def _pixabay(key, q, orient, kind, limit):
    ori = {"portrait": "vertical", "landscape": "horizontal"}.get(orient, "all")
    qs = urllib.parse.urlencode({"key": key, "q": q[:100], "per_page": max(3, min(limit * 2, 50)), "safesearch": "true"})
    if kind == "video":
        d = _json(f"https://pixabay.com/api/videos/?{qs}")
        out = []
        for v in d.get("hits", []):
            f = _best_file([{"url": x["url"], "width": x["width"], "height": x["height"]} for x in v["videos"].values() if x.get("url")])
            if f and (orient != "portrait" or f["height"] >= f["width"]):
                out.append(_clip("pixabay", v["id"], "video", f["url"], v.get("pageURL"), f["width"], f["height"], v.get("duration"),
                                 v.get("user"), f"https://pixabay.com/users/{v.get('user')}-{v.get('user_id')}/", v.get("picture_id") and f"https://i.vimeocdn.com/video/{v['picture_id']}_295x166.jpg" or ""))
        return out
    d = _json(f"https://pixabay.com/api/?{qs}&orientation={ori}&image_type=photo")
    return [_clip("pixabay", p["id"], "image", p.get("largeImageURL") or p["webformatURL"], p.get("pageURL"), p.get("imageWidth"), p.get("imageHeight"), 0,
                  p.get("user"), f"https://pixabay.com/users/{p.get('user')}-{p.get('user_id')}/", p.get("previewURL", "")) for p in d.get("hits", [])]


def _wikimedia(_key, q, orient, kind, limit):
    ft = "video" if kind == "video" else "bitmap"
    qs = urllib.parse.urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": min(limit * 3, 30),
                                 "gsrsearch": f"filetype:{ft} {q}", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280})
    d = _json(f"https://commons.wikimedia.org/w/api.php?{qs}")
    out = []
    for p in (d.get("query") or {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if not ii.get("url") or not OPEN_LICENSE.match(lic) or (ii.get("width") or 0) < 640:
            continue
        w, h = ii.get("width") or 0, ii.get("height") or 0
        if orient == "portrait" and h < w:
            continue
        art = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip() or "Wikimedia Commons"
        out.append(_clip("wikimedia", p["pageid"], kind, ii.get("thumburl") if kind == "image" else ii["url"], ii.get("descriptionurl"), w, h,
                         ii.get("duration") or 0, f"{art} ({lic})", ii.get("descriptionurl"), ii.get("thumburl", "")))
    return out


PROVIDERS = (("pexels", "pexels_key", _pexels), ("pixabay", "pixabay_key", _pixabay), ("wikimedia", None, _wikimedia))


def search(settings, query, orient="portrait", kind="video", limit=6, log=None):
    """نتائج موحّدة من أول مزوّدين يعطون نتائج؛ فشل مزوّد لا يوقف الباقي"""
    out = []
    for name, kname, fn in PROVIDERS:
        key = settings.get(kname) if kname else ""
        if kname and not key:
            continue
        try:
            out += fn(key, query, orient, kind, limit)
        except ProviderError as e:
            log and log(f"{name}: {e}")
        if len(out) >= limit:
            break
    return out[:limit]


def fetch(env, clip, log=None):
    """ينزّل اللقطة إلى الكاش (مرة واحدة لكل لقطة) ويرجع المسار"""
    d = env.base / "stock"
    d.mkdir(parents=True, exist_ok=True)
    ext = Path(urllib.parse.urlparse(clip["url"]).path).suffix.lower()
    ext = ext if ext in (".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".webp") else (".mp4" if clip["kind"] == "video" else ".jpg")
    p = d / f"{clip['provider']}_{clip['id']}{ext}"
    if p.exists() and p.stat().st_size > 1024:
        return p
    tmp = p.with_suffix(p.suffix + ".part")
    req = urllib.request.Request(clip["url"], headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise ProviderError(f"تعذّر تنزيل اللقطة: {str(e)[:100]}")
    tmp.replace(p)
    _prune(d)
    return p


def _prune(d):
    files = sorted((f for f in d.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    while files and total > CACHE_LIMIT:
        f = files.pop(0)
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def credits(clips):
    """سطور الاعتماد لحزمة النشر (Pexels يطلب رابطاً)"""
    seen, lines = set(), []
    for c in clips:
        k = (c["provider"], c["credit"]["name"])
        if k in seen:
            continue
        seen.add(k)
        lines.append(f"{c['credit']['name']} · {c['provider'].capitalize()} · {c['credit']['url'] or c['page']}")
    return lines


def test(settings):
    """اختبار كل مفتاح مضبوط بطلب صغير"""
    res = {}
    for name, kname, fn in PROVIDERS[:2]:
        key = settings.get(kname)
        if not key:
            res[name] = None
            continue
        try:
            n = len(fn(key, "nature", "portrait", "video", 3))
            res[name] = True if n else "المفتاح يعمل لكن بلا نتائج"
        except ProviderError as e:
            res[name] = str(e)
    return res
