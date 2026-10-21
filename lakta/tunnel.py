import os
import platform
import re
import subprocess
import threading
import time
import urllib.request

URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"


def _binary(bin_dir):
    path = bin_dir / "cloudflared"
    if not path.exists():
        arch = "arm64" if platform.machine() in ("aarch64", "arm64") else "amd64"
        urllib.request.urlretrieve(URL.format(arch=arch), path)
        os.chmod(path, 0o755)
    return path


def start(port, bin_dir, timeout=45):
    """نفق Cloudflare مجاني بدون حساب — يرجع الرابط العام أو None"""
    try:
        exe = _binary(bin_dir)
    except Exception as e:
        print(f"تعذّر تنزيل cloudflared: {e}")
        return None
    p = subprocess.Popen([str(exe), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
    found = {}

    def read():
        for line in p.stderr:
            m = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", line)
            if m and "url" not in found:
                found["url"] = m.group(0)
    threading.Thread(target=read, daemon=True).start()
    end = time.time() + timeout
    while time.time() < end and "url" not in found and p.poll() is None:
        time.sleep(0.5)
    return found.get("url")
