"""استوديو لقطة — خلية تشغيل واحدة لـ Kaggle و Colab و VPS

Kaggle / Colab:
    from run import main; main()
VPS:
    python run.py [--port 7860] [--no-tunnel]
فحص ذاتي:
    python run.py --selftest
"""
import importlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

NEEDS = {"yt_dlp": "yt-dlp", "faster_whisper": "faster-whisper", "google.genai": "google-genai", "edge_tts": "edge-tts",
         "PIL": "Pillow", "cv2": "opencv-python-headless", "webvtt": "webvtt-py", "numpy": "numpy"}


def _deps():
    missing = []
    for mod, pkg in NEEDS.items():
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(pkg)
    print("⏳ تجهيز المكتبات…", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"] + missing, check=False)
    importlib.invalidate_caches()
    if not shutil.which("ffmpeg"):
        if os.geteuid() == 0 and shutil.which("apt-get"):
            subprocess.run("apt-get update -qq && apt-get install -y -qq ffmpeg", shell=True, check=False)
        if not shutil.which("ffmpeg"):
            sys.exit("❌ ffmpeg غير مثبت. على VPS: sudo apt install ffmpeg")


def _show(url, key, env_name):
    full = f"{url}/?k={key}"
    line = "═" * 58
    print(f"\n{line}\n  👑 استوديو لقطة يعمل على {env_name}\n  افتح الرابط:  {full}\n  مفتاح الدخول: {key}\n{line}\n", flush=True)
    try:
        from IPython.display import HTML, display
        display(HTML(f'<div dir="rtl" style="font:16px system-ui;padding:14px 18px;border:2px solid #2b4bd8;border-radius:10px;'
                     f'display:inline-block">👑 <b>استوديو لقطة جاهز</b> · <a href="{full}" target="_blank" '
                     f'style="color:#2b4bd8;font-weight:700">افتح الاستوديو</a></div>'))
    except Exception:
        pass


def main(port=None, tunnel=None, block=True):
    _deps()
    from config import CONFIG
    from lakta.env import Env, Settings
    from lakta.jobs import Store, Worker
    from lakta.pipeline import Pipeline
    from lakta.server import App, serve
    from lakta import tunnel as tun
    import threading

    port = int(port or os.getenv("LAKTA_PORT") or CONFIG["server"]["port"])
    env = Env()
    settings = Settings(env)
    store = Store(env)
    worker = Worker(store, Pipeline(env, settings))
    app = App(env, settings, store, worker)
    srv = serve(app, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    use_tunnel = CONFIG["server"]["tunnel"] if tunnel is None else tunnel
    url = tun.start(port, env.bin) if use_tunnel else None
    if use_tunnel and not url:
        print("⚠️ تعذّر فتح النفق · استخدم الرابط المحلي أو أعد التشغيل")
    _show(url or f"http://127.0.0.1:{port}", app.key, env.name)
    if not block:
        return app
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
        print("تم إيقاف الاستوديو")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selftest" in args:
        from lakta.selftest import run as selftest
        sys.exit(selftest())
    p = args[args.index("--port") + 1] if "--port" in args else None
    main(port=p, tunnel=False if "--no-tunnel" in args else None)
