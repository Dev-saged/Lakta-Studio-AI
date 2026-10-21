import hmac
import json
import mimetypes
import re
import secrets
import shutil
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import CONFIG
from . import director, ingest, templates, voice
from .env import ROOT, ffprobe
from .pipeline import deliver_again, steps_for

WEB = ROOT / "web"
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; media-src 'self' blob:; "
       "connect-src 'self' https://raw.githubusercontent.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
UPLOADS = {
    "video": ({".mp4", ".mkv", ".mov", ".webm", ".m4v"}, "upload_mb"),
    "font": ({".ttf", ".otf"}, "font_mb"),
    "watermark": ({".png", ".webp", ".jpg", ".jpeg"}, "image_mb"),
    "frame_short": ({".png"}, "image_mb"),
    "frame_wide": ({".png"}, "image_mb"),
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
        kf = env.base / ".access_key"
        if not kf.exists():
            kf.write_text(secrets.token_urlsafe(18))
            kf.chmod(0o600)
        self.key = kf.read_text().strip()

    # ===== الحالة =====
    def state(self):
        ver, jobs = self.store.snapshot()
        return {"app": CONFIG["app"], "repo": CONFIG["repo"], "env": self.env.info(), "settings": self.settings.public(),
                "fonts": sorted(p.name for p in self.env.fonts.glob("*") if p.suffix.lower() in (".ttf", ".otf")),
                "music": sorted(p.name for p in self.env.music.glob("*") if p.suffix.lower() in UPLOADS["music"][0]),
                "cookies": "file" if (self.env.assets / "youtube_cookies.txt").exists() else ("secret" if ingest.cookies_path(self.env) else ""),
                "voices": voice.voices(), "defaults": CONFIG["defaults"]["job"], "clip": CONFIG["clip"], "trash_days": CONFIG["trash"]["days"],
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
        if a["music"] and not (self.env.music / Path(a["music"]).name).exists():
            a["music"] = ""
        fm = body.get("formats") or {}
        opts = {"parts": max(1, min(CONFIG["clip"]["max_parts"], int(body.get("parts") or 1))),
                "formats": {"short": bool(fm.get("short", True)), "wide": bool(fm.get("wide", True))},
                "face": bool(body.get("face", True)), "hook": bool(body.get("hook", False)), "audio": a,
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
            return bool(m and hmac.compare_digest(m.group(1), app.key))

        def _cookie(self):
            sec = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return f"lakta_k={app.key}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000{sec}"

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
                    if k and hmac.compare_digest(k, app.key):
                        return self._headers(302, "text/plain", 0, {"Location": "/", "Set-Cookie": self._cookie()})
                    b = (WEB / "index.html").read_bytes()
                    return self._headers(200, "text/html; charset=utf-8", len(b), {"Cache-Control": "no-cache", "Content-Security-Policy": CSP}) or self.wfile.write(b)
                if path in ("/manifest.webmanifest", "/sw.js", "/icon.svg"):
                    return self._file(WEB / path.lstrip("/"), "no-cache")
                if not self._authed():
                    return self._err("غير مصرّح", 401)
                if path == "/api/state":
                    return self._json(app.state())
                if path == "/api/jobs":
                    ver, jobs = app.store.snapshot()
                    return self._json({"version": ver, "jobs": jobs})
                if path == "/api/events":
                    return self._events()
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
                    k = str(self._body().get("key", ""))
                    if not hmac.compare_digest(k, app.key):
                        time.sleep(1)
                        return self._err("المفتاح غير صحيح", 403)
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
                if path == "/api/trash/empty":
                    return self._json({"count": app.store.purge_expired(0)})
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
                if path.startswith("/api/template/"):
                    return self._json(self._template(path.rsplit("/", 1)[1], body))
                if path == "/api/delete":
                    kind, name = body.get("kind"), Path(str(body.get("name", ""))).name
                    root = {"font": app.env.fonts, "music": app.env.music, "watermark": app.env.assets, "cookies": app.env.assets,
                            "frame_short": app.env.assets, "frame_wide": app.env.assets}.get(kind)
                    if kind == "cookies":
                        name = "youtube_cookies.txt"
                    if not root or not name or name == CONFIG["fonts"]["default"]:
                        raise ValueError("لا يمكن الحذف")
                    (root / name).unlink(missing_ok=True)
                    if kind.startswith("frame_") or kind == "watermark":
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
                notice = None
                try:
                    if not st.get("gemini_key"):
                        raise RuntimeError("no key")
                    templates.ornament(st, t["idea"] or t["mood"] or "elegant", t, env.assets / "ornament.png")
                    source = "ai"
                except Exception as e:
                    templates.geometric(t, env.assets / "ornament.png")
                    source = "geometric"
                    notice = (director.friendly(e, "image") if st.get("gemini_key") else "لا يوجد مفتاح Gemini") + \
                        " · استخدمتُ زخرفة هندسية بألوان هويتك بدلاً منها."
                st.update({"brand": {"template": {"ornament": True}}})
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
                        "frame_short": app.env.assets, "frame_wide": app.env.assets}[kind]
                dst = root / ("watermark" + ext if kind == "watermark" else kind + ext if kind.startswith("frame_") else name)
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
            if kind.startswith("frame_"):
                app.settings.update({"brand": {kind: dst.name}})
            return {"ok": True, "name": dst.name, "file_id": dst.name if kind == "video" else None, "state": app.state()}

    return H


def _validate(kind, path):
    if kind == "video":
        m = ffprobe(path)
        assert m["width"] and m["duration"] > 1
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
