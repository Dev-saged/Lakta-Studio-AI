"""حفظ دائم على Kaggle: نسخة من الإعدادات والمكتبة في Kaggle Dataset خاص تُستعاد تلقائياً"""
import json
import os
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path

from .env import secret

SLUG = "lakta-studio-data"
SKIP = {"sources", "bin", "uploads", "stock"}
SKIP_FILES = {"youtube_cookies.txt", "settings.tmp", "jobs.tmp"}
state = {"busy": False, "last": None, "error": None}
_lock = threading.Lock()
_timer = {"t": None}


def _creds():
    u, k = secret("kaggle_username")[0], secret("kaggle_key")[0]
    if u and k:
        os.environ["KAGGLE_USERNAME"], os.environ["KAGGLE_KEY"] = u.strip(), k.strip()
        return u.strip()
    return None


def mode(env):
    if env.name == "Colab" and "drive" in str(env.base):
        return "drive"
    if env.name == "Kaggle":
        return "kaggle" if _creds() else "off"
    return "local"


def _kaggle(*args, timeout=1800):
    return subprocess.run(["kaggle", *args], capture_output=True, text=True, timeout=timeout)


def restore(env, log=print):
    """استعادة آخر نسخة عند بداية جلسة جديدة"""
    if mode(env) != "kaggle" or (env.base / "jobs.json").exists():
        return
    user = _creds()
    tmp = env.temp / "restore"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        r = _kaggle("datasets", "download", "-d", f"{user}/{SLUG}", "-p", str(tmp), "--unzip", "-q", timeout=1800)
        tar = next(tmp.rglob("lakta.tar"), None)
        if r.returncode or not tar:
            return
        base = env.base.resolve()
        with tarfile.open(tar) as t:
            safe = [m for m in t.getmembers() if (base / m.name).resolve().is_relative_to(base) and not m.issym() and not m.islnk()]
            t.extractall(base, members=safe)
        log("استُعيدت المكتبة والإعدادات من النسخة الاحتياطية")
    except Exception as e:
        log(f"تعذّرت الاستعادة: {str(e)[:120]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _clean_settings(path):
    # الأسرار لا تُحفظ في النسخة؛ تُقرأ من Secrets
    try:
        d = json.loads(path.read_text("utf-8"))
        for k in ("gemini_key", "telegram_token", "telegram_chat"):
            d[k] = ""
        return json.dumps(d, ensure_ascii=False).encode()
    except Exception:
        return None


def snapshot(env, log=print):
    if mode(env) != "kaggle":
        return
    with _lock:
        state.update(busy=True, error=None)
        user = _creds()
        stage = env.temp / "backup"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        try:
            with tarfile.open(stage / "lakta.tar", "w") as t:
                for p in sorted(env.base.rglob("*")):
                    rel = p.relative_to(env.base)
                    if not p.is_file() or rel.parts[0] in SKIP or p.name in SKIP_FILES:
                        continue
                    if p.name == "settings.json":
                        data = _clean_settings(p)
                        if data:
                            import io
                            info = tarfile.TarInfo(str(rel))
                            info.size, info.mtime = len(data), int(p.stat().st_mtime)
                            t.addfile(info, io.BytesIO(data))
                        continue
                    t.add(p, str(rel))
            (stage / "dataset-metadata.json").write_text(json.dumps(
                {"title": "Lakta Studio Data", "id": f"{user}/{SLUG}", "licenses": [{"name": "CC0-1.0"}]}), "utf-8")
            r = _kaggle("datasets", "version", "-p", str(stage), "-m", time.strftime("%Y-%m-%d %H:%M"), "-r", "skip", "-q")
            if r.returncode:
                r = _kaggle("datasets", "create", "-p", str(stage), "-r", "skip", "-q")
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout)[-200:])
            state["last"] = time.time()
            log("حُفظت نسخة احتياطية في Kaggle Dataset")
        except Exception as e:
            state["error"] = str(e)[:200]
            log(f"تعذّر النسخ الاحتياطي: {state['error']}")
        finally:
            state["busy"] = False
            shutil.rmtree(stage, ignore_errors=True)


def schedule(env, delay=90):
    """نسخ بعد هدوء قصير حتى لا تتكرر النسخ مع المهام المتتالية"""
    if mode(env) != "kaggle":
        return
    if _timer["t"]:
        _timer["t"].cancel()
    _timer["t"] = threading.Timer(delay, snapshot, (env,))
    _timer["t"].daemon = True
    _timer["t"].start()


def info(env):
    return {"mode": mode(env), **state}
