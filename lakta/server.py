import hmac
import json
import mimetypes
import re
import secrets
import shutil
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import CONFIG
from . import audio, backup, kits, director, fonts, ingest, stock, templates, transcribe, voice
from .env import ROOT, ffprobe
from .jobs import STEP_LABELS
from .pipeline import deliver_again, steps_for

WEB = ROOT / "web"
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob: https://i.ytimg.com; media-src 'self' blob:; "
       "connect-src 'self' https://raw.githubusercontent.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
UPLOADS = {
    "video": ({".mp4", ".mkv", ".mov", ".webm", ".m4v"}, "upload_mb"),
    "font": ({".ttf", ".otf"}, "font_mb"),
    "watermark": ({".png", ".webp", ".jpg", ".jpeg"}, "image_mb"),
    "frame_short": ({".png"}, "image_mb"),
    "frame_wide": ({".png"}, "image_mb"),
    "intro_video": ({".mp4", ".mov", ".webm", ".m4v"}, "bumper_mb"),
    "outro_video": ({".mp4", ".mov", ".webm", ".m4v"}, "bumper_mb"),
    "music": ({".mp3", ".m4a", ".wav", ".ogg"}, "music_mb"),
    "cookies": ({".txt"}, "json_kb"),
}
ENUMS = {"mode": {"original", "bg", "voice"}, "engine": {"edge", "gemini"}, "text": {"same", "rephrase"},
         "dialect": set(CONFIG["dialects"]), "filter": {"", "today", "week", "podcast"}}
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/otf", ".otf")


class App:
    def __init__(self, env, settings, store, worker):
        self.env, self.settings, self.store, self.worker = env, settings, store, worker
        # كلمة مرور ثابتة من Secrets إن وُجدت، وإلا رمز أرقام سهل الكتابة يبقى ثابتاً بين التشغيلات
        from .env import secret
        own = (secret("lakta_password")[0] or "").strip()
        kf = env.base / ".access_key"
        if not own:
            old = kf.read_text().strip() if kf.exists() else ""
            if not re.fullmatch(r"\d{4}-\d{4}", old):
                kf.write_text(f"{secrets.randbelow(10**4):04d}-{secrets.randbelow(10**4):04d}")
                kf.chmod(0o600)
        self.key = own or kf.read_text().strip()
        self.token = norm_key(self.key)
        self.fails = {}
        self.fixed_url = None
        from .watch import Watcher
        self.watch = Watcher(env, settings, store, worker, steps_for)

    # ===== المشروع المحفوظ: مراجعة وإعادة إخراج =====
    def load_project(self, jid):
        p = self.env.outputs / Path(jid).name / "project.json"
        if not p.exists():
            raise ValueError("لا يوجد مشروع محفوظ لهذه المهمة")
        return json.loads(p.read_text("utf-8"))

    def can_resume(self, jid):
        try:
            return Path(self.load_project(jid)["src"]["path"]).exists()
        except Exception:
            return False

    def project(self, jid):
        pj = self.load_project(jid)
        words = pj["tr"]["words"]
        dur = pj["meta"]["duration"]
        out = []
        for c in pj["clips"]:
            a, b = max(0.0, c["s"] - 20), min(dur, c["e"] + 20)
            out.append({"n": c["n"], "s": c["s"], "e": c["e"], "title": c.get("title", ""), "hook": c.get("hook", ""),
                        "thumb": c.get("thumb", ""), "score": c.get("score", 0), "win": [a, b],
                        "alt_titles": c.get("alt_titles") or [], "speaker": c.get("speaker") or {"name": "", "role": ""},
                        "words": [[i, w["w"], round(w["s"], 2), round(w["e"], 2)] for i, w in enumerate(words) if a <= w["s"] < b]})
        return {"duration": dur, "title": pj["src"]["title"], "clips": out, "source": Path(pj["src"]["path"]).exists()}

    def project_action(self, jid, act, body):
        job = self.store.get(jid)
        if not job:
            raise ValueError("غير موجود")
        if job["status"] in ("running", "queued"):
            raise ValueError("المهمة تعمل الآن · انتظر انتهاءها")
        pj = self.load_project(jid)
        path = self.env.outputs / jid / "project.json"
        dur = pj["meta"]["duration"]
        if act == "alt":
            n = int(body.get("n") or 0)
            cur = next((c for c in pj["clips"] if c["n"] == n), None)
            if not cur:
                raise ValueError("لقطة غير موجودة")
            taken = [(c["s"], c["e"]) for c in pj["clips"]]
            segs = pj["tr"]["segments"] if pj["tr"].get("source") == "youtube" else transcribe.regroup(pj["tr"]["words"])
            new = None
            if self.settings.get("gemini_key"):
                try:
                    new = director.alt_clip(self.settings, segs, dur, taken, pj["src"]["title"])
                except Exception as e:
                    print("alt_clip:", director.friendly(e))
            new = new or director.free_slot(segs, dur, taken)
            if not new:
                raise ValueError("لا توجد مساحة لِلقطة بديلة في هذا الفيديو")
            seo = pj["seo"]
            new.update(n=n, title=new["title"] or seo["title"], hook=new["hook"] or seo["thumb"], thumb=new["thumb"] or seo["thumb"], files={})
            pj["clips"] = [new if c["n"] == n else c for c in pj["clips"]]
            path.write_text(json.dumps(pj, ensure_ascii=False), "utf-8")
            return self.project(jid)
        if act == "produce":
            # تعديلات المراجعة: الحدود والنصوص وتصحيح الكلمات
            edits = {int(c.get("n", 0)): c for c in (body.get("clips") or []) if isinstance(c, dict)}
            for c in pj["clips"]:
                e = edits.get(c["n"])
                if not e:
                    continue
                s0, e0 = float(e.get("s", c["s"])), float(e.get("e", c["e"]))
                if not (0 <= s0 < e0 <= dur + 0.01) or not 5 <= e0 - s0 <= 180:
                    raise ValueError(f"حدود اللقطة {c['n']} غير صالحة (بين 5 و180 ثانية)")
                c["s"], c["e"] = round(s0, 2), round(e0, 2)
                if c.get("open") and not (c["s"] <= c["open"][0] < c["open"][1] <= c["e"]):
                    c["open"] = None
                for k in ("title", "hook", "thumb"):
                    if isinstance(e.get(k), str):
                        c[k] = re.sub(r"\s+", " ", e[k]).strip()[:120] or c[k]
                if isinstance(e.get("speaker"), dict):
                    c["speaker"] = {k: re.sub(r"\s+", " ", str(e["speaker"].get(k, ""))).strip()[:60] for k in ("name", "role")}
            words = pj["tr"]["words"]
            for i, w in (body.get("fixes") or {}).items():
                i = int(i)
                if 0 <= i < len(words) and isinstance(w, str):
                    w = re.sub(r"\s+", " ", w).strip()[:40]
                    words[i]["w"] = w or words[i]["w"]
            path.write_text(json.dumps(pj, ensure_ascii=False), "utf-8")
        elif act != "rerender":
            raise ValueError("إجراء غير معروف")
        if not Path(pj["src"]["path"]).exists():
            raise ValueError("الفيديو المصدر لم يعد محفوظاً · أعد الإنتاج من البداية")
        o = {**job["opts"]}
        for k in ("formats", "layout", "hook", "face", "short_fill", "edit"):
            if k in body and isinstance(body[k], type(o.get(k, body[k]))):
                o[k] = body[k]
        o["review"] = False
        steps = [{"key": k, "label": STEP_LABELS[k], "state": "done" if k in ("download", "transcribe", "direct") else "todo", "note": ""}
                 for k in steps_for(o)]
        self.store.update(jid, status="queued", phase="produce", error=None, pct=0, finished=None, opts=o, steps=steps, zip=None)
        self.worker.kick()
        return {"ids": [jid], "msg": "أُرسلت للإخراج"}

    def _music_meta(self):
        try:
            meta = json.loads((self.env.music / ".meta.json").read_text("utf-8"))
        except Exception:
            return {}
        return {k.rsplit(":", 1)[0]: {"bpm": v.get("bpm"), "mood": v.get("mood")} for k, v in meta.items()}

    def stats(self):
        """لوحة الإحصاءات من سجل المهام"""
        _, jobs = self.store.snapshot()
        jobs = [j for j in jobs if not j.get("deleted")]
        done = [j for j in jobs if j["status"] == "done"]
        ended = [j for j in jobs if j["status"] in ("done", "failed")]
        clips = [dict(c, job=j["id"], jtitle=j["title"]) for j in done for c in j.get("clips") or []]
        secs = {}
        for j in done:
            for s in j.get("steps") or []:
                if s.get("sec"):
                    secs.setdefault(s["key"], []).append(s["sec"])
        day = 86400
        now = time.time()
        week = [sum(1 for j in done if now - (k + 1) * day <= (j.get("finished") or 0) < now - k * day) for k in range(13, -1, -1)]
        return {"jobs": len(jobs), "done": len(done), "failed": sum(1 for j in jobs if j["status"] == "failed"),
                "success": round(100 * len(done) / len(ended)) if ended else 0, "clips": len(clips),
                "hours": round(sum(j.get("duration") or 0 for j in done) / 3600, 1),
                "score": round(sum(c.get("score") or 0 for c in clips) / len(clips)) if clips else 0,
                "steps": [{"key": k, "label": STEP_LABELS[k], "avg": round(sum(v) / len(v), 1)} for k, v in secs.items()],
                "daily": week,
                "top": [{k: c.get(k) for k in ("job", "jtitle", "n", "title", "score", "files")}
                        for c in sorted(clips, key=lambda c: -(c.get("score") or 0))[:5]]}

    # ===== الحالة =====
    def state(self):
        ver, jobs = self.store.snapshot()
        return {"app": CONFIG["app"], "repo": CONFIG["repo"], "env": self.env.info(), "settings": self.settings.public(),
                "fonts": sorted(p.name for p in self.env.fonts.glob("*") if p.suffix.lower() in (".ttf", ".otf")),
                "google_fonts": fonts.GOOGLE_ARABIC,
                "music": sorted(p.name for p in self.env.music.glob("*") if p.suffix.lower() in UPLOADS["music"][0]),
                "cookies": "file" if (self.env.assets / "youtube_cookies.txt").exists() else ("secret" if ingest.cookies_path(self.env) else ""),
                "voices": voice.voices(), "defaults": CONFIG["defaults"]["job"], "backup": backup.info(self.env), "fixed_url": self.fixed_url,
                "watch": self.watch.public(), "kits": sorted(kits.load(self.env)), "music_meta": self._music_meta(), "clip": CONFIG["clip"], "trash_days": CONFIG["trash"]["days"],
                "version": ver, "jobs": jobs}

    def create_jobs(self, body):
        d = CONFIG["defaults"]["job"]
        a_in = body.get("audio") or {}
        a = {k: str(a_in.get(k, v)) for k, v in d["audio"].items()}
        for k in ("mode", "engine", "text", "dialect"):
            if a[k] not in ENUMS[k]:
                raise ValueError(f"قيمة غير صالحة: {k}")
        if not re.fullmatch(r"[A-Za-z0-9\-]{2,40}", a["voice"]):
            raise ValueError("اسم الصوت غير صالح")
        if a["music"] and a["music"] != "auto" and not (self.env.music / Path(a["music"]).name).exists():
            a["music"] = ""
        fm = body.get("formats") or {}
        opts = {"parts": max(1, min(CONFIG["clip"]["max_parts"], int(body.get("parts") or 1))),
                "formats": {"short": bool(fm.get("short", True)), "wide": bool(fm.get("wide", True)),
                            "square": bool(fm.get("square", False)), "portrait": bool(fm.get("portrait", False))},
                "face": bool(body.get("face", True)), "hook": bool(body.get("hook", False)), "audio": a,
                "short_fill": bool(body.get("short_fill", False)), "review": bool(body.get("review", False)),
                "layout": body.get("layout") if body.get("layout") in ("track", "active", "split") else "track",
                "quality": body.get("quality") if body.get("quality") in CONFIG["quality"] else "",
                "edit": {k: bool((body.get("edit") or {}).get(k, v)) for k, v in d["edit"].items()}}
        if a["mode"] != "original" and (a["engine"] == "gemini" or a["text"] != "same" or a["dialect"] != "original") \
                and not self.settings.get("gemini_key"):
            raise ValueError("هذا الخيار يحتاج مفتاح Gemini · أضفه من الإعدادات")
        if not any(opts["formats"].values()):
            raise ValueError("اختر شورتس أو عريض على الأقل")
        src = body.get("source") or {}
        t = src.get("type")
        made = []
        if t == "links":
            links = [l.strip() for l in str(src.get("links", "")).splitlines() if l.strip()][:30]
            links = [l if l.startswith("http") else "https://" + l for l in links]
            if not links or not all(re.match(r"^https?://[\w\-]+(\.[\w\-]+)+(:\d+)?(/[^\s<>\"']*)?$", l) for l in links):
                raise ValueError("أدخل رابطاً صحيحاً واحداً على الأقل")
            busy = {j["opts"]["source"].get("url") for j in app_jobs(self.store) if j["status"] in ("queued", "running")}
            dup = [l for l in links if l in busy]
            if dup and len(dup) == len(links):
                raise ValueError("هذا الرابط قيد المعالجة بالفعل · تابعه من شاشة المهام")
            for l in links:
                if l in busy:
                    continue
                made.append(self.store.add(_short_url(l), {**opts, "source": {"type": "link", "url": l}}, steps_for(opts)))
        elif t == "trend":
            kw = str(src.get("keyword", "")).strip()[:120]
            flt = str(src.get("filter", ""))
            if not kw or flt not in ENUMS["filter"]:
                raise ValueError("أدخل كلمة مفتاحية")
            made.append(self.store.add(f"تريند: {kw}", {**opts, "source": {"type": "trend", "keyword": kw, "filter": flt}}, steps_for(opts)))
        elif t == "file":
            fid = Path(str(src.get("file_id", ""))).name
            if not fid or not (self.env.uploads / fid).exists():
                raise ValueError("ارفع الملف أولاً")
            name = str(src.get("name", fid))[:120]
            made.append(self.store.add(name, {**opts, "source": {"type": "file", "file_id": fid, "name": name}}, steps_for(opts)))
        else:
            raise ValueError("مصدر غير معروف")
        self.worker.kick()
        return [j["id"] for j in made]


def app_jobs(store):
    with store.lock:
        return [dict(j) for j in store.jobs.values()]


def norm_key(k):
    """مطابقة متسامحة: تجاهل المسافات والشرطات وحالة الأحرف والأرقام العربية"""
    k = str(k).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    return re.sub(r"[\s\-_]", "", k).lower()


def _short_url(u):
    p = urllib.parse.urlparse(u)
    q = urllib.parse.parse_qs(p.query).get("v", [""])[0]
    return f"{p.netloc.replace('www.', '')}{'/' + q if q else p.path[:30]}"


def handler(app):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Lakta"

        def log_message(self, *a):
            pass

        # ===== أدوات =====
        def _headers(self, code, ctype, length=None, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def _json(self, obj, code=200, extra=None):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self._headers(code, "application/json; charset=utf-8", len(b), {"Cache-Control": "no-store", **(extra or {})})
            self.wfile.write(b)

        def _err(self, msg, code=400):
            self._json({"error": msg}, code)

        def _authed(self):
            c = self.headers.get("Cookie", "")
            m = re.search(r"(?:^|;\s*)lakta_k=([A-Za-z0-9_\-]+)", c)
            return bool(m and hmac.compare_digest(m.group(1), app.token))

        def _cookie(self):
            sec = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return f"lakta_k={app.token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000{sec}"

        def _body(self, limit_kb=None):
            n = int(self.headers.get("Content-Length") or 0)
            if n > (limit_kb or CONFIG["limits"]["json_kb"]) * 1024:
                raise ValueError("الطلب كبير جداً")
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("صيغة غير صالحة")
            return data

        def _file(self, path, cache="private, max-age=3600"):
            path = Path(path)
            if not path.is_file():
                return self._err("غير موجود", 404)
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            extra = {"Accept-Ranges": "bytes", "Cache-Control": cache}
            if path.suffix in (".zip", ".txt") or "dl" in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query):
                extra["Content-Disposition"] = f"attachment; filename*=UTF-8''{urllib.parse.quote(path.name)}"
            start, end = 0, size - 1
            code = 200
            if rng and size:
                a, b = rng.groups()
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    end = min(int(b), size - 1) if b else size - 1
                if start > end:
                    return self._headers(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
                code = 206
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._headers(code, ctype, end - start + 1, extra)
            if self.command == "HEAD":
                return
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def _safe(self, root, rel):
            root = Path(root).resolve()
            p = (root / urllib.parse.unquote(rel)).resolve()
            return p if p.is_relative_to(root) else None

        # ===== GET =====
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            path, q = u.path, urllib.parse.parse_qs(u.query)
            try:
                if path == "/":
                    k = q.get("k", [""])[0]
                    if k and hmac.compare_digest(norm_key(k).encode(), app.token.encode()):
                        return self._headers(302, "text/plain", 0, {"Location": "/", "Set-Cookie": self._cookie()})
                    b = (WEB / "index.html").read_bytes()
                    return self._headers(200, "text/html; charset=utf-8", len(b), {"Cache-Control": "no-cache", "Content-Security-Policy": CSP}) or self.wfile.write(b)
                if path in ("/manifest.webmanifest", "/sw.js", "/icon.svg"):
                    return self._file(WEB / path.lstrip("/"), "no-cache")
                if not self._authed():
                    return self._err("غير مصرّح", 401)
                if path == "/api/state":
                    return self._json(app.state())
                if path == "/api/stats":
                    return self._json(app.stats())
                if path == "/api/jobs":
                    ver, jobs = app.store.snapshot()
                    return self._json({"version": ver, "jobs": jobs})
                if path == "/api/events":
                    return self._events()
                m = re.fullmatch(r"/api/jobs/([\w\-]+)/project", path)
                if m:
                    return self._json(app.project(m.group(1)))
                m = re.fullmatch(r"/api/jobs/([\w\-]+)/source", path)
                if m:
                    pj = app.load_project(m.group(1))
                    return self._file(pj["src"]["path"], "private, max-age=600")
                m = re.fullmatch(r"/api/jobs/([\w\-]+)(/transcript)?", path)
                if m:
                    job = app.store.get(m.group(1))
                    if not job:
                        return self._err("غير موجود", 404)
                    if m.group(2):
                        return self._file(app.env.outputs / job["id"] / "transcript.json", "no-cache")
                    job.pop("log", None)
                    return self._json(job)
                for prefix, root in (("/files/", app.env.outputs), ("/fonts/", app.env.fonts), ("/assets/", app.env.assets),
                                     ("/music/", app.env.music)):
                    if path.startswith(prefix):
                        p = self._safe(root, path[len(prefix):])
                        return self._file(p) if p else self._err("مسار غير صالح", 403)
                if path.startswith("/cache/"):
                    p = self._safe(app.env.temp, path[7:])
                    return self._file(p) if p else self._err("مسار غير صالح", 403)
                self._err("غير موجود", 404)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _events(self):
            # ترميز مقطّع (chunked) حتى يمرّر نفق Cloudflare كل حدث فوراً
            self._headers(200, "text/event-stream; charset=utf-8", None,
                          {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Transfer-Encoding": "chunked"})
            self.close_connection = True
            ver = -1

            def send(b):
                self.wfile.write(f"{len(b):X}\r\n".encode() + b + b"\r\n")
                self.wfile.flush()
            try:
                send(b": " + b" " * 2048 + b"\n\nretry: 3000\n\n")
                while True:
                    cur, jobs = app.store.snapshot()
                    if cur != ver:
                        ver = cur
                        send(f"event: jobs\ndata: {json.dumps({'version': cur, 'jobs': jobs}, ensure_ascii=False)}\n\n".encode())
                    else:
                        send(b"event: ping\ndata: 1\n\n")
                    app.store.wait(ver, 15)
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        # ===== POST =====
        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            path, q = u.path, urllib.parse.parse_qs(u.query)
            try:
                if path == "/api/login":
                    ip = self.headers.get("CF-Connecting-IP") or self.client_address[0]
                    n, until = app.fails.get(ip, (0, 0))
                    if time.time() < until:
                        return self._err("محاولات كثيرة · انتظر 10 دقائق ثم أعد المحاولة", 429)
                    k = norm_key(self._body().get("key", ""))
                    if not hmac.compare_digest(k.encode(), app.token.encode()):
                        time.sleep(1)
                        n += 1
                        app.fails[ip] = (0, time.time() + 600) if n >= 10 else (n, 0)
                        return self._err("رمز الدخول غير صحيح · انسخه من خلية التشغيل كما هو", 403)
                    app.fails.pop(ip, None)
                    return self._json({"ok": True}, extra={"Set-Cookie": self._cookie()})
                if not self._authed():
                    return self._err("غير مصرّح", 401)
                if self.headers.get("X-Lakta") != "1":
                    return self._err("طلب غير صالح", 403)
                if path == "/api/upload":
                    return self._json(self._upload(q.get("kind", [""])[0]))
                body = self._body()
                if path == "/api/jobs":
                    return self._json({"ids": app.create_jobs(body)})
                if path == "/api/watch":
                    a = body.get("act")
                    if a == "add":
                        return self._json({"watch": app.watch.add(body.get("url"), body.get("parts"))})
                    if a == "remove":
                        return self._json({"watch": app.watch.remove(str(body.get("id", "")))})
                    if a == "check":
                        n = app.watch.check()
                        return self._json({"watch": app.watch.public(), "msg": f"أُضيفت {n} حلقات جديدة" if n else "لا حلقات جديدة"})
                    raise ValueError("إجراء غير معروف")
                if path == "/api/kits":
                    names = kits.act(app.env, app.settings, body.get("act"), body.get("name"))
                    return self._json({"kits": names, "settings": app.settings.public()})
                if path == "/api/backup":
                    if backup.mode(app.env) != "kaggle":
                        raise ValueError("النسخ الاحتياطي يعمل على Kaggle مع KAGGLE_USERNAME و KAGGLE_KEY في Secrets")
                    if not backup.state["busy"]:
                        threading.Thread(target=backup.snapshot, args=(app.env,), daemon=True).start()
                    return self._json({"msg": "بدأ النسخ الاحتياطي"})
                if path == "/api/trash/empty":
                    return self._json({"count": app.store.purge_expired(0)})
                m = re.fullmatch(r"/api/jobs/([\w\-]+)/(produce|rerender|alt)", path)
                if m:
                    return self._json(app.project_action(m.group(1), m.group(2), body))
                m = re.fullmatch(r"/api/jobs/([\w\-]+)/(cancel|retry|delete|restore|purge|telegram)", path)
                if m:
                    return self._json(self._job_action(*m.groups()))
                if path == "/api/settings":
                    app.settings.update(body)
                    if any(k in body for k in ("gemini_key", "text_model", "tts_model")):
                        director._resolved.clear()
                    return self._json({"settings": app.settings.public()})
                if path == "/api/test/gemini":
                    return self._json(director.test_key(app.settings.get("gemini_key")))
                if path == "/api/test/stock":
                    return self._json(stock.test(app.settings))
                if path == "/api/test/telegram":
                    from .notify import Telegram, _call
                    t = Telegram(app.settings)
                    if not t.ready:
                        raise ValueError("أدخل التوكن ومعرّف المحادثة أولاً")
                    _call(t.token, "sendMessage", {"chat_id": t.chat, "text": "✅ استوديو لقطة متصل بنجاح"}, timeout=15)
                    return self._json({"ok": True})
                if path == "/api/voice/preview":
                    eng = body.get("engine")
                    v, d = str(body.get("voice", "")), str(body.get("dialect", "msa"))
                    if eng not in ENUMS["engine"] or d not in ENUMS["dialect"] or not re.fullmatch(r"[A-Za-z0-9\-]{2,40}", v):
                        raise ValueError("خيارات غير صالحة")
                    p = voice.preview(app.settings, eng, v, d, app.env.temp)
                    return self._json({"url": f"/cache/{p.name}"})
                if path == "/api/trend/search":
                    kw = str(body.get("keyword", "")).strip()[:120]
                    flt = str(body.get("filter", "week"))
                    if not kw or flt not in ENUMS["filter"]:
                        raise ValueError("أدخل كلمة مفتاحية")
                    off = max(0, min(60, int(body.get("offset") or 0)))
                    return self._json(ingest.search_videos(kw, flt, app.settings, app.env, lambda m: None, off, 3))
                if path.startswith("/api/template/"):
                    return self._json(self._template(path.rsplit("/", 1)[1], body))
                if path == "/api/fonts/fetch":
                    try:
                        name = fonts.fetch_google(str(body.get("name", "")), app.env.fonts, app.env.temp / "gf")
                    except ValueError:
                        raise
                    except Exception as e:
                        raise ValueError(f"تعذّر تنزيل الخط: {str(e)[:120]}")
                    app.settings.update({"brand": {"font": name}})
                    return self._json({"name": name, **app.state()})
                if path == "/api/delete":
                    kind, name = body.get("kind"), Path(str(body.get("name", ""))).name
                    root = {"font": app.env.fonts, "music": app.env.music, "watermark": app.env.assets, "cookies": app.env.assets,
                            "frame_short": app.env.assets, "frame_wide": app.env.assets,
                            "intro_video": app.env.assets, "outro_video": app.env.assets}.get(kind)
                    if kind == "cookies":
                        name = "youtube_cookies.txt"
                    if not root or not name or name == CONFIG["fonts"]["default"]:
                        raise ValueError("لا يمكن الحذف")
                    (root / name).unlink(missing_ok=True)
                    if kind.startswith(("frame_", "intro_", "outro_")) or kind == "watermark":
                        app.settings.update({"brand": {kind: ""}})
                    return self._json(app.state())
                self._err("غير موجود", 404)
            except (ValueError, KeyError) as e:
                self._err(str(e))
            except Exception as e:
                self._err(director.friendly(e), 500 if not isinstance(e, RuntimeError) else 400)

        def _template(self, act, body):
            st, env = app.settings, app.env
            brand = st.data["brand"]
            font = env.fonts / brand.get("font", CONFIG["fonts"]["default"])
            if not font.exists():
                font = env.fonts / CONFIG["fonts"]["default"]
            if act == "design":
                idea = str(body.get("idea", "")).strip()[:300]
                if not idea:
                    raise ValueError("اكتب فكرتك أولاً")
                if not st.get("gemini_key"):
                    raise ValueError("التصميم بالذكاء الاصطناعي يحتاج مفتاح Gemini")
                try:
                    spec = templates.design(st, idea)
                except Exception as e:
                    raise ValueError(director.friendly(e))
                cur = templates.tpl_conf(brand)
                st.update({"brand": {"template": {"accent": spec["accent"], "base": spec["base"], "ink": spec["ink"],
                                                  "mood": spec["mood"], "cta_ai": spec["cta"], "idea": idea,
                                                  "style": cur["style"] if cur["style"] in templates.STYLES else spec["style"]}}})
            elif act == "ornament":
                t = templates.tpl_conf(brand)
                source, notice = templates.make_ornament(st, t, env.assets / "ornament.png")
                st.update({"brand": {"template": {"ornament": True}}})
            elif act == "export":
                st.update({"brand": templates.export_frames(env, brand, font)})
            elif act != "previews":
                raise ValueError("إجراء غير معروف")
            out = env.temp / "tplprev"
            out.mkdir(parents=True, exist_ok=True)
            names = templates.previews(env, st.data["brand"], font, out)
            v = int(time.time() * 1000) % 10**8
            extra = {"notice": notice, "source": source} if act == "ornament" else \
                {"spec": {k: st.data["brand"]["template"][k] for k in ("mood", "cta_ai", "style")}} if act == "design" else {}
            return {**extra, "settings": st.public(), "ornament": (env.assets / "ornament.png").exists(),
                    "previews": {k: f"/cache/tplprev/{n}?v={v}" for k, n in names.items()}}

        def _job_action(self, jid, act):
            job = app.store.get(jid)
            if not job:
                raise ValueError("المهمة غير موجودة")
            if act == "cancel":
                app.worker.stop(jid)
            elif act == "delete":
                app.worker.trash(jid)
                return {"msg": "نُقلت إلى سلة المهملات"}
            elif act == "restore":
                app.store.restore(jid)
                return {"msg": "استُعيدت المهمة"}
            elif act == "purge":
                if not app.store.delete(jid):
                    raise ValueError("المهمة ما زالت تتوقف · حاول بعد ثوانٍ")
                return {"msg": "حُذفت نهائياً"}
            elif act == "retry":
                if job["status"] in ("failed", "canceled") and app.can_resume(jid):
                    # استئناف من الإخراج: المصدر والتفريغ محفوظان
                    app.project_action(jid, "rerender", {})
                    return {"ids": [jid], "msg": "استُؤنفت من مرحلة الإخراج"}
                o = job["opts"]
                new = app.store.add(job["label"], o, steps_for(o))
                app.worker.kick()
                return {"ids": [new["id"]]}
            elif act == "telegram":
                return {"msg": deliver_again(app.env, app.settings, job)}
            return {"ok": True}

        def _upload(self, kind):
            if kind not in UPLOADS:
                raise ValueError("نوع غير معروف")
            exts, lim = UPLOADS[kind]
            name = urllib.parse.unquote(self.headers.get("X-Filename", "file"))
            name = re.sub(r"[^\w\-. ؀-ۿ]", "_", Path(name).name)[:100] or "file"
            ext = Path(name).suffix.lower()
            if ext not in exts:
                raise ValueError("صيغة الملف غير مدعومة")
            n = int(self.headers.get("Content-Length") or 0)
            cap = CONFIG["limits"][lim] * (1024 if lim == "json_kb" else 1024 * 1024)
            if not n or n > cap:
                raise ValueError("حجم الملف غير مسموح")
            if kind == "video":
                fid = secrets.token_hex(8) + ext
                dst = app.env.uploads / fid
            elif kind == "cookies":
                dst = app.env.assets / "youtube_cookies.txt"
            else:
                root = {"font": app.env.fonts, "watermark": app.env.assets, "music": app.env.music,
                        "frame_short": app.env.assets, "frame_wide": app.env.assets,
                        "intro_video": app.env.assets, "outro_video": app.env.assets}[kind]
                dst = root / ("watermark" + ext if kind == "watermark" else kind + ext if kind.startswith(("frame_", "intro_", "outro_")) else name)
            tmp = dst.with_name(dst.name + ".part")
            with open(tmp, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                tmp.unlink(missing_ok=True)
                raise ValueError("انقطع الرفع")
            try:
                _validate(kind, tmp)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise ValueError("الملف تالف أو غير مدعوم")
            if kind == "watermark":
                for old in app.env.assets.glob("watermark.*"):
                    old.unlink()
            tmp.replace(dst)
            if kind == "watermark":
                app.settings.update({"brand": {"watermark": dst.name}})
            if kind == "font":
                app.settings.update({"brand": {"font": dst.name}})
            if kind == "music":
                try:
                    audio.analyze_music(dst, app.env.music / ".meta.json")
                except Exception:
                    pass
            if kind.startswith(("frame_", "intro_", "outro_")):
                for old in app.env.assets.glob(f"{kind}.*"):
                    if old != dst:
                        old.unlink()
                app.settings.update({"brand": {kind: dst.name}})
            return {"ok": True, "name": dst.name, "file_id": dst.name if kind == "video" else None, "state": app.state()}

    return H


def _validate(kind, path):
    if kind in ("video", "intro_video", "outro_video"):
        m = ffprobe(path)
        assert m["width"] and m["duration"] > (1 if kind == "video" else 0.3)
        assert kind == "video" or m["duration"] <= 30
    elif kind == "font":
        from PIL import ImageFont
        ImageFont.truetype(str(path), 20)
    elif kind == "watermark" or kind.startswith("frame_"):
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
    elif kind == "music":
        assert ffprobe(path)["duration"] > 1
    elif kind == "cookies":
        assert "youtube.com" in path.read_text("utf-8", "ignore")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            super().handle_error(request, client_address)


def serve(app, port):
    srv = Server(("0.0.0.0", port), handler(app))
    srv.daemon_threads = True
    return srv
