import json
import mimetypes
import uuid
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
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{p.name}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n".encode() + p.read_bytes() + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        req = urllib.request.Request(url, data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(res.get("description", "Telegram error"))
    return res


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

    def deliver(self, zip_path, files, caption):
        """يرسل الملف المضغوط، وإن تجاوز الحد يرسل الملفات منفردة"""
        if not self.ready:
            return "تيليجرام غير مضبوط"
        if Path(zip_path).stat().st_size <= LIMIT:
            self.document(zip_path, caption)
            return "أُرسل الملف المضغوط"
        sent = 0
        for f in files:
            if Path(f).exists() and Path(f).stat().st_size <= LIMIT:
                self.document(f, caption if not sent else "")
                sent += 1
        return f"الملف أكبر من حد تيليجرام · أُرسل {sent} ملفاً منفرداً"
