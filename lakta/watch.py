"""مراقبة قنوات يوتيوب عبر RSS العام: كل حلقة جديدة تتحول لمهمة إنتاج تلقائياً"""
import copy
import json
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

from config import CONFIG

NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"


class Watcher:
    def __init__(self, env, settings, store, worker, steps_for, log=print):
        self.env, self.settings, self.store, self.worker, self.steps_for, self.log = env, settings, store, worker, steps_for, log
        self.path = env.base / "watch.json"
        self.lock = threading.Lock()
        try:
            self.items = json.loads(self.path.read_text("utf-8"))
        except Exception:
            self.items = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False), "utf-8")
        tmp.replace(self.path)

    def public(self):
        with self.lock:
            return [{k: w.get(k) for k in ("id", "title", "url", "parts", "checked", "made")} for w in self.items]

    def _resolve(self, url):
        """معرّف القناة واسمها من أي رابط قناة"""
        m = re.search(r"channel/(UC[\w-]{22})", url)
        if m:
            cid = m.group(1)
        else:
            from . import ingest
            info = ingest._attempts(url.rstrip("/") + "/videos", self.settings, self.env, self.log,
                                    {"extract_flat": "in_playlist", "playlistend": 1}, False)
            cid = info.get("channel_id") or info.get("id")
            if not cid or not cid.startswith("UC"):
                raise ValueError("تعذّر التعرف على القناة · استخدم رابط القناة مباشرة")
        entries = self._feed(cid)
        return cid, entries

    def _feed(self, cid):
        req = urllib.request.Request(FEED.format(cid), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            root = ET.fromstring(r.read())
        title = (root.findtext("a:title", "", NS) or "").strip()
        vids = [(e.findtext("yt:videoId", "", NS), (e.findtext("a:title", "", NS) or "").strip(), e.findtext("a:published", "", NS))
                for e in root.findall("a:entry", NS)]
        return {"title": title, "videos": [v for v in vids if v[0]]}

    def add(self, url, parts):
        url = str(url).strip()
        if not re.match(r"^https?://(www\.|m\.)?youtube\.com/", url):
            raise ValueError("أدخل رابط قناة يوتيوب")
        cid, feed = self._resolve(url)
        with self.lock:
            if any(w["id"] == cid for w in self.items):
                raise ValueError("هذه القناة مضافة بالفعل")
            # لا إنتاج بأثر رجعي: تبدأ المراقبة من آخر حلقة موجودة
            self.items.append({"id": cid, "title": feed["title"] or url, "url": url, "parts": max(1, min(3, int(parts or 2))),
                               "seen": [v[0] for v in feed["videos"]], "checked": time.time(), "made": 0})
            self._save()
        return self.public()

    def remove(self, cid):
        with self.lock:
            self.items = [w for w in self.items if w["id"] != cid]
            self._save()
        return self.public()

    def check(self):
        made = 0
        for w in list(self.items):
            try:
                feed = self._feed(w["id"])
            except Exception as e:
                self.log(f"تعذّرت قراءة قناة {w['title']}: {str(e)[:100]}")
                continue
            new = [v for v in feed["videos"] if v[0] not in w["seen"]]
            for vid, title, _ in reversed(new):
                opts = copy.deepcopy(CONFIG["defaults"]["job"])
                opts.update(parts=w["parts"], review=False, source={"type": "link", "url": f"https://www.youtube.com/watch?v={vid}"})
                self.store.add(f"{w['title']}: {title}"[:120], opts, self.steps_for(opts))
                made += 1
            with self.lock:
                w["seen"] = ([v[0] for v in new] + w["seen"])[:60]
                w["checked"], w["made"] = time.time(), w.get("made", 0) + len(new)
                self._save()
        if made:
            self.worker.kick()
        return made

    def _loop(self):
        while True:
            time.sleep(CONFIG["watch"]["minutes"] * 60)
            if self.items:
                try:
                    self.check()
                except Exception as e:
                    self.log(f"مراقبة القنوات: {str(e)[:120]}")
