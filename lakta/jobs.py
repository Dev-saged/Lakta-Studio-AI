import copy
import json
import secrets
import shutil
import threading
import time
import traceback
from pathlib import Path

from config import CONFIG

from .env import Cancelled

STEP_LABELS = {"download": "تنزيل الفيديو", "transcribe": "تفريغ كلمة بكلمة", "direct": "اختيار أقوى اللقطات",
               "voice": "توليد الصوت الجديد", "separate": "فصل الصوت عن الخلفية", "render": "الإخراج والأغلفة",
               "package": "التغليف والإرسال"}
PUBLIC = ("id", "created", "finished", "status", "title", "label", "url", "steps", "pct", "clips", "duration", "error",
          "voice_label", "zip", "log", "opts", "seo", "deleted")


class Store:
    def __init__(self, env):
        self.env = env
        self.path = env.base / "jobs.json"
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.version = 0
        self.jobs = {}
        self._dirty = False
        try:
            for j in json.loads(self.path.read_text("utf-8")):
                if j["status"] in ("queued", "running"):
                    j["status"], j["error"] = "failed", "توقف السيرفر أثناء المعالجة · أعد المحاولة"
                self.jobs[j["id"]] = j
        except Exception:
            pass
        threading.Thread(target=self._saver, daemon=True).start()

    def _saver(self):
        n = 0
        while True:
            time.sleep(2)
            self.save()
            n += 1
            if n % 900 == 1:
                self.purge_expired()

    def save(self, force=False):
        with self.lock:
            if not (self._dirty or force):
                return
            data = json.dumps(list(self.jobs.values()), ensure_ascii=False)
            self._dirty = False
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(data, "utf-8")
            tmp.replace(self.path)

    def touch(self):
        with self.cond:
            self.version += 1
            self._dirty = True
            self.cond.notify_all()

    def add(self, label, opts, steps):
        jid = time.strftime("%y%m%d-%H%M%S-") + secrets.token_hex(2)
        job = {"id": jid, "created": time.time(), "finished": None, "status": "queued", "title": label, "label": label,
               "url": "", "opts": opts, "pct": 0, "duration": 0, "clips": [], "error": None, "zip": None, "log": [],
               "seo": {}, "voice_label": "",
               "steps": [{"key": k, "label": STEP_LABELS[k], "state": "todo", "note": ""} for k in steps]}
        with self.lock:
            self.jobs[jid] = job
        self.touch()
        return job

    def update(self, jid, **kw):
        with self.lock:
            self.jobs[jid].update(kw)
        self.touch()

    def get(self, jid):
        with self.lock:
            return copy.deepcopy(self.jobs.get(jid))

    def snapshot(self):
        with self.lock:
            return self.version, [{k: j.get(k) for k in PUBLIC} for j in sorted(self.jobs.values(), key=lambda x: -x["created"])]

    def wait(self, since, timeout=15):
        with self.cond:
            if self.version == since:
                self.cond.wait(timeout)
            return self.version

    def delete(self, jid):
        """حذف نهائي (للمهام غير العاملة)"""
        with self.lock:
            job = self.jobs.get(jid)
            if not job or job["status"] in ("running", "queued"):
                return False
            del self.jobs[jid]
        shutil.rmtree(self.env.outputs / jid, ignore_errors=True)
        for f in self.env.sources.glob(f"{jid}.*"):
            f.unlink(missing_ok=True)
        uploads = (job.get("opts") or {}).get("source", {})
        if uploads.get("type") == "file" and uploads.get("file_id") and not any(
                (j.get("opts") or {}).get("source", {}).get("file_id") == uploads["file_id"] for j in self.jobs.values()):
            (self.env.uploads / Path(uploads["file_id"]).name).unlink(missing_ok=True)
        self.touch()
        return True

    # ===== سلة المهملات =====
    def trash(self, jid):
        with self.lock:
            job = self.jobs.get(jid)
            if not job:
                return False
            job["deleted"] = time.time()
        self.touch()
        return True

    def restore(self, jid):
        with self.lock:
            job = self.jobs.get(jid)
            if not job:
                return False
            job["deleted"] = None
        self.touch()
        return True

    def purge_expired(self, days=None):
        days = CONFIG["trash"]["days"] if days is None else days
        limit = time.time() - days * 86400
        with self.lock:
            old = [j["id"] for j in self.jobs.values() if j.get("deleted") and j["deleted"] <= limit
                   and j["status"] not in ("running", "queued")]
        for jid in old:
            self.delete(jid)
        return len(old)


class Worker:
    def __init__(self, store, pipeline):
        self.store, self.pipeline = store, pipeline
        self.cancel = set()
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def kick(self):
        self.wake.set()

    def stop(self, jid):
        job = self.store.get(jid)
        if not job:
            return False
        if job["status"] == "queued":
            self.store.update(jid, status="canceled", finished=time.time())
        elif job["status"] == "running":
            self.cancel.add(jid)
        return True

    def trash(self, jid):
        """نقل للسلة؛ العاملة تُوقف أولاً"""
        self.stop(jid)
        return self.store.trash(jid)

    def _next(self):
        with self.store.lock:
            q = [j for j in self.store.jobs.values() if j["status"] == "queued" and not j.get("deleted")]
            return min(q, key=lambda j: j["created"])["id"] if q else None

    def _loop(self):
        while True:
            jid = self._next()
            if not jid:
                self.wake.wait(5)
                self.wake.clear()
                continue
            job = Job(self.store, jid, lambda: jid in self.cancel)
            self.store.update(jid, status="running")
            try:
                if self.pipeline(job) == "review":
                    job.finish("review", None)
                else:
                    job.done()
            except Cancelled:
                job.finish("canceled", "أُلغيت المهمة")
            except Exception as e:
                job.log("خطأ: " + str(e)[:300])
                print(traceback.format_exc())
                job.finish("failed", str(e)[:400])
            finally:
                self.cancel.discard(jid)
                if (self.store.jobs.get(jid) or {}).get("deleted"):
                    self.store.save(force=True)


class Job:
    """واجهة المهمة داخل خط الإنتاج"""

    def __init__(self, store, jid, cancelled):
        self.store, self.id, self.cancelled = store, jid, cancelled
        self.current = None

    @property
    def data(self):
        return self.store.jobs[self.id]

    def set(self, **kw):
        self.store.update(self.id, **kw)

    def log(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(f"[{self.id}] {msg}", flush=True)
        with self.store.lock:
            self.data["log"] = (self.data["log"] + [line])[-80:]
        self.store.touch()

    def step(self, key, note=""):
        if self.cancelled():
            raise Cancelled()
        now = time.time()
        with self.store.lock:
            for s in self.data["steps"]:
                if s["key"] == self.current and s["state"] == "now":
                    s["state"] = "done"
                    s["sec"] = round(now - s.get("t0", now), 1)
                if s["key"] == key:
                    s["state"], s["note"], s["t0"] = "now", note, now
        self.current = key
        self._pct(0)

    def skip(self, key):
        with self.store.lock:
            for s in self.data["steps"]:
                if s["key"] == key:
                    s["state"] = "done"
        self.store.touch()

    def note(self, note):
        with self.store.lock:
            for s in self.data["steps"]:
                if s["key"] == self.current:
                    s["note"] = note
        self.store.touch()

    def _pct(self, p):
        steps = self.data["steps"]
        idx = next((i for i, s in enumerate(steps) if s["key"] == self.current), 0)
        total = round((idx + max(0.0, min(1.0, p))) / len(steps) * 100)
        sp = round(p * 100)
        now = time.time()
        if (total, sp) == getattr(self, "_last", None) or (p not in (0, 1) and now - getattr(self, "_t", 0) < 0.7):
            return
        self._last, self._t = (total, sp), now
        with self.store.lock:
            self.data["pct"] = total
            for s in steps:
                if s["key"] == self.current:
                    s["pct"] = round(p * 100)
        self.store.touch()

    def progress(self, p):
        if self.cancelled():
            raise Cancelled()
        self._pct(p)

    def done(self):
        with self.store.lock:
            for s in self.data["steps"]:
                if s["state"] == "now":
                    s["state"] = "done"
                    s["sec"] = round(time.time() - s.get("t0", time.time()), 1)
        self.finish("done", None)

    def finish(self, status, err):
        with self.store.lock:
            self.data.update(status=status, error=err, finished=time.time())
            if status == "done":
                self.data["pct"] = 100
        self.store.touch()
        self.store.save(force=True)
