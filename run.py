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
    # yt-dlp يحتاج محرك JavaScript (Deno) وحزمة EJS لحل تحديات يوتيوب
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default]", "deno"] + missing, check=False)
    _deno_path()
    importlib.invalidate_caches()
    if not shutil.which("ffmpeg"):
        if os.geteuid() == 0 and shutil.which("apt-get"):
            subprocess.run("apt-get update -qq && apt-get install -y -qq ffmpeg", shell=True, check=False)
        if not shutil.which("ffmpeg"):
            sys.exit("❌ ffmpeg غير مثبت. على VPS: sudo apt install ffmpeg")


def _deno_path():
    # إضافة مسار deno المثبَّت عبر pip إلى PATH إن لم يكن ظاهراً
    if shutil.which("deno"):
        return
    try:
        import deno
        for cand in (getattr(deno, "find_deno_bin", lambda: None)(), *Path(deno.__file__).parent.rglob("deno")):
            if cand and Path(cand).is_file():
                os.chmod(cand, 0o755)
                os.environ["PATH"] = str(Path(cand).parent) + os.pathsep + os.environ.get("PATH", "")
                return
    except Exception:
        pass


def _show(url, key, env_name, fixed=None):
    full = f"{fixed or url}/?k={key}"
    line = "═" * 58
    print(f"\n{line}\n  👑 استوديو لقطة يعمل على {env_name}\n  افتح الرابط:  {full}\n  مفتاح الدخول: {key}\n{line}\n", flush=True)
    try:
        from IPython.display import HTML, display
        btn = ("border:0;border-radius:8px;padding:10px 16px;font:600 15px system-ui;cursor:pointer;"
               "background:#2b4bd8;color:#fff;text-decoration:none;display:inline-block")
        copy = "navigator.clipboard.writeText(this.dataset.v).then(()=>{this.textContent='✓ نُسخ'})"
        display(HTML(
            f'<div dir="rtl" style="font:16px system-ui;padding:16px 18px;border:2px solid #2b4bd8;border-radius:12px;'
            f'display:inline-grid;gap:12px">'
            f'<b>👑 استوديو لقطة جاهز على {env_name}</b>'
            f'<div style="display:flex;gap:8px;flex-wrap:wrap"><a href="{full}" target="_blank" style="{btn}">افتح الاستوديو</a>'
            f'<button style="{btn};background:#0e1b3d" data-v="{full}" onclick="{copy}">نسخ الرابط</button></div>'
            f'<div>رمز الدخول: <code style="font:700 24px monospace;letter-spacing:2px;user-select:all">{key}</code> '
            f'<button style="{btn};padding:6px 12px;background:#555" data-v="{key}" onclick="{copy}">نسخ الرمز</button></div>'
            + (f'<small style="color:#2b4bd8">رابطك الثابت: {fixed}</small>' if fixed else '') +
            f'<small style="color:#666">لرمز ثابت دائماً أضف في Secrets: LAKTA_PASSWORD</small></div>'))
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
    from lakta import backup, relay
    backup.restore(env)
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
    fixed = relay.publish(url) if url else None
    app.fixed_url = fixed
    _show(url or f"http://127.0.0.1:{port}", app.key, env.name, fixed)
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
