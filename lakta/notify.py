import json
import mimetypes
import re
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

    def deliver(self, main_zip, items, title, caption, work, light=None):
        """الملف المضغوط باسم المشروع؛ فوق حد البوت: ملف لكل جزء باسمه"""
        from .package import telegram_bundles
        if not self.ready:
            return "تيليجرام غير مضبوط"
        main_zip = Path(main_zip)
        if main_zip.stat().st_size <= LIMIT:
            self.document(main_zip, caption)
            return "أُرسل الملف المضغوط"
        bundles, big = telegram_bundles(items, title, Path(work), LIMIT, light)
        for p in bundles:
            self.document(p, f"{caption} · {p.stem.split(' - ')[-1]}")
            p.unlink(missing_ok=True)
        if big:
            self.message("هذه الملفات أكبر من 50MB (حد تيليجرام للبوتات)، نزّلها من المكتبة في الاستوديو:\n" +
                         "\n".join(f"• {b}" for b in big))
        return f"أُرسل في {len(bundles)} ملفات مضغوطة" + (f" · {len(big)} ملفات في المكتبة" if big else "")
