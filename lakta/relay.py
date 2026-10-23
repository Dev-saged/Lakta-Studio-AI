"""رابط ثابت: يُرسَل رابط النفق الجديد إلى Cloudflare Worker يحوّل إليه دائماً"""
import json
import urllib.request

from .env import secret


def fixed_url():
    return (secret("relay_url")[0] or "").strip().rstrip("/") or None


def publish(tunnel_url, log=print):
    base, token = fixed_url(), (secret("relay_token")[0] or "").strip()
    if not (base and token and tunnel_url):
        return None
    try:
        req = urllib.request.Request(f"{base}/__set", data=json.dumps({"url": tunnel_url}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            if json.loads(r.read()).get("ok"):
                return base
    except Exception as e:
        log(f"تعذّر تحديث الرابط الثابت: {str(e)[:120]}")
    return None
