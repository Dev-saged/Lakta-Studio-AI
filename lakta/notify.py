import json
import mimetypes
import re
import zipfile
import uuid
import urllib.parse
import urllib.request
from pathlib import Path

LIMIT = 49 * 1024 * 1024


def _call(token, method, fields, file_field=None, file_path=None, timeout=60):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if not file_path:
        data = json.dumps(fields).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    else:
        b = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        p = Path(file_path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{p.name}"; '
                     f"filename*=UTF-8''{urllib.parse.quote(p.name)}\r\n"
                     f"Content-Type: {ctype}\r\n\r\n".encode() + p.read_bytes() + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        req = urllib.request.Request(url, data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(res.get("description", "Telegram error"))
    return res


def safe_name(title, fallback="لقطة"):
    """اسم ملف من عنوان المشروع (عربي مسموح)"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f#%&{}$!\'@`=+]', " ", str(title or "")).strip()
    name = re.sub(r"\s+", " ", name)[:60].strip(" .-")
    return name or fallback


def pack_parts(src_dir, stem, out_dir, limit=LIMIT):
    """توزيع ملفات المشروع على ملفات مضغوطة ≤ الحد، مع إبقاء ملفات الجزء الواحد معاً"""
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = {}
    for f in sorted(src_dir.iterdir()):
        if f.is_file() and f.suffix != ".zip":
            m = re.search(r"Part(\d+)|_(\d+)\.jpg$", f.name)
            groups.setdefault(m.group(1) or m.group(2) if m else "0", []).append(f)
    items, big = [], []
    for key in sorted(groups, key=lambda k: int(k)):
        g = groups[key]
        size = sum(f.stat().st_size for f in g)
        if size <= limit - 64 * 1024:
            items.append((size, g, False))
            continue
        # جزء أكبر من الحد: يُقسَّم داخلياً فقط، وملفاته الصغيرة تبقى مع أول فيديو منه
        sub = []
        for f in sorted(g, key=lambda x: -x.stat().st_size):
            fs = f.stat().st_size
            if fs > limit - 64 * 1024:
                big.append(f.name)
                continue
            for b in sub:
                if b[0] + fs <= limit - 64 * 1024:
                    b[0] += fs
                    b[1].append(f)
                    break
            else:
                sub.append([fs, [f]])
        items += [(sz, fl, True) for sz, fl in sub]
        continue
    bins = []
    for size, g, solo in items:
        for b in ([] if solo else bins):
            if not b[2] and b[0] + size <= limit - 64 * 1024:
                b[0] += size
                b[1].extend(g)
                break
        else:
            bins.append([size, list(g), solo])
    paths = []
    for i, (_, files, _solo) in enumerate(bins, 1):
        p = out_dir / f"{stem} - ملف {i} من {len(bins)}.zip"
        with zipfile.ZipFile(p, "w") as z:
            for f in files:
                z.write(f, f.name, compress_type=zipfile.ZIP_DEFLATED if f.suffix in (".txt", ".json") else zipfile.ZIP_STORED)
        paths.append(p)
    return paths, big


class Telegram:
    def __init__(self, settings):
        self.token = settings.get("telegram_token")
        self.chat = settings.get("telegram_chat")

    @property
    def ready(self):
        return bool(self.token and self.chat)

    def message(self, text):
        if self.ready:
            try:
                _call(self.token, "sendMessage", {"chat_id": self.chat, "text": text[:4000]}, timeout=15)
            except Exception:
                pass

    def document(self, path, caption=""):
        _call(self.token, "sendDocument", {"chat_id": self.chat, "caption": caption[:1000]}, "document", path, timeout=300)

    def deliver(self, zip_path, src_dir, caption):
        """يرسل ملفاً مضغوطاً باسم المشروع؛ فوق حد البوت يُقسَّم لملفات مضغوطة كاملة يفتح كل منها وحده"""
        if not self.ready:
            return "تيليجرام غير مضبوط"
        zip_path = Path(zip_path)
        if zip_path.stat().st_size <= LIMIT:
            self.document(zip_path, caption)
            return "أُرسل الملف المضغوط"
        parts, big = pack_parts(Path(src_dir), zip_path.stem, zip_path.parent / "_telegram")
        for i, p in enumerate(parts, 1):
            self.document(p, f"{caption} · ملف {i} من {len(parts)}")
        for p in parts:
            p.unlink(missing_ok=True)
        try:
            (zip_path.parent / "_telegram").rmdir()
        except OSError:
            pass
        if big:
            self.message("هذه الملفات أكبر من 50MB (حد تيليجرام للبوتات)، نزّلها من المكتبة في الاستوديو:\n" +
                         "\n".join(f"• {b}" for b in big))
        return f"أُرسل في {len(parts)} ملفات مضغوطة" + (f" · {len(big)} ملفات كبيرة في المكتبة" if big else "")
