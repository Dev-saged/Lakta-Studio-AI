import copy
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from config import CONFIG

ROOT = Path(__file__).resolve().parent.parent


class Env:
    def __init__(self):
        self.name, self.base, self.temp = self._paths()
        for d in (self.base, self.temp, self.fonts, self.outputs, self.uploads, self.music, self.assets, self.bin):
            d.mkdir(parents=True, exist_ok=True)
        self._seed_fonts()
        self.gpu = self._gpu()
        self.started = time.time()

    def _paths(self):
        if Path("/kaggle/working").exists():
            return "Kaggle", Path("/kaggle/working/Lakta_System"), Path("/tmp/lakta")
        if Path("/content").exists():
            base = Path("/content/Lakta_System")
            try:
                from google.colab import drive  # noqa
                if not Path("/content/drive/MyDrive").exists():
                    drive.mount("/content/drive")
                base = Path("/content/drive/MyDrive/Lakta_System")
            except Exception:
                pass
            return "Colab", base, Path("/content/lakta_tmp")
        return "VPS", ROOT / "Lakta_System", ROOT / "temp_work"

    fonts = property(lambda s: s.base / "Fonts")
    outputs = property(lambda s: s.base / "outputs")
    uploads = property(lambda s: s.base / "uploads")
    music = property(lambda s: s.base / "Music")
    assets = property(lambda s: s.base / "assets")
    bin = property(lambda s: s.base / "bin")

    def _seed_fonts(self):
        for f in (ROOT / "assets" / "fonts").glob("*.ttf"):
            dst = self.fonts / f.name
            if not dst.exists():
                shutil.copy(f, dst)

    def _gpu(self):
        if not shutil.which("nvidia-smi"):
            return None
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
            if out:
                name, mem = [x.strip() for x in out[0].split(",")]
                return {"name": name.replace("Tesla ", ""), "mem": mem}
        except Exception:
            pass
        return None

    def info(self):
        du = shutil.disk_usage(self.base)
        return {"name": self.name, "gpu": self.gpu, "uptime": int(time.time() - self.started),
                "disk_free_gb": round(du.free / 2**30, 1), "disk_total_gb": round(du.total / 2**30, 1),
                "python": sys.version.split()[0], "encoder": video_encoder()}


# ===== الأسرار: الإعدادات ← متغيرات البيئة ← Kaggle Secrets ← Colab =====
SECRET_ENV = {"gemini_key": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "telegram_token": ("TELEGRAM_TOKEN",),
              "telegram_chat": ("TELEGRAM_CHAT_ID",), "youtube_cookies": ("YOUTUBE_COOKIES",),
              "lakta_password": ("LAKTA_PASSWORD",)}
_secret_cache = {}


def secret(name):
    if name in _secret_cache:
        return _secret_cache[name]
    val = (None, None)
    for key in SECRET_ENV[name]:
        if os.getenv(key):
            val = (os.getenv(key), "env")
            break
        try:
            from kaggle_secrets import UserSecretsClient
            v = UserSecretsClient().get_secret(key)
            if v:
                val = (v, "Kaggle Secrets")
                break
        except Exception:
            pass
        try:
            from google.colab import userdata
            v = userdata.get(key)
            if v:
                val = (v, "Colab Secrets")
                break
        except Exception:
            pass
    _secret_cache[name] = val
    return val


class Settings:
    SECRETS = ("gemini_key", "telegram_token", "telegram_chat")

    def __init__(self, env):
        self.path = env.base / "settings.json"
        self.lock = threading.Lock()
        self.data = copy.deepcopy(CONFIG["defaults"])
        try:
            saved = json.loads(self.path.read_text("utf-8"))
            _merge(self.data, saved)
        except Exception:
            pass

    def get(self, key):
        if key in self.SECRETS:
            own = self.data.get(key) or ""
            return own or (secret(key)[0] or "")
        return self.data.get(key)

    def source(self, key):
        if self.data.get(key):
            return "الإعدادات"
        return secret(key)[1]

    def update(self, patch):
        with self.lock:
            clean = _sanitize(patch, CONFIG["defaults"])
            _merge(self.data, clean)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)

    def public(self):
        d = copy.deepcopy(self.data)
        for k in self.SECRETS:
            v = self.get(k)
            d[k] = {"set": bool(v), "hint": (v[:4] + "…" + v[-3:]) if len(v) > 10 else ("•••" if v else ""),
                    "source": self.source(k) if v else None}
        return d


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def _sanitize(patch, schema):
    # يقبل فقط المفاتيح المعروفة وبنفس النوع
    out = {}
    if not isinstance(patch, dict):
        return out
    for k, v in patch.items():
        if k not in schema:
            continue
        ref = schema[k]
        if isinstance(ref, dict):
            if isinstance(v, dict):
                out[k] = _sanitize(v, ref)
        elif isinstance(ref, bool):
            if isinstance(v, bool):
                out[k] = v
        elif isinstance(ref, (int, float)):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = type(ref)(v) if isinstance(ref, int) and float(v).is_integer() else float(v)
        elif isinstance(ref, str):
            if isinstance(v, str) and len(v) <= 400:
                out[k] = v.strip()
    return out


# ===== أدوات النظام =====
def ensure(module, package=None, log=None):
    try:
        return importlib.import_module(module)
    except ImportError:
        if log:
            log(f"تثبيت {package or module}…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", package or module], check=True)
        importlib.invalidate_caches()
        return importlib.import_module(module)


class Cancelled(Exception):
    pass


def run(cmd, cancel=None, progress=None, total=None, timeout=None):
    """تشغيل أمر مع إمكانية الإلغاء وقراءة تقدم ffmpeg"""
    if progress and cmd and Path(cmd[0]).name == "ffmpeg":
        cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    err = []
    t_err = threading.Thread(target=lambda: err.extend(p.stderr), daemon=True)
    t_err.start()
    start = time.time()
    for line in p.stdout:
        if cancel and cancel():
            p.kill()
            raise Cancelled()
        if progress and total and line.startswith("out_time_us="):
            try:
                progress(min(1.0, int(line.split("=")[1]) / 1e6 / total))
            except ValueError:
                pass
            except BaseException:
                p.kill()
                raise
        if timeout and time.time() - start > timeout:
            p.kill()
            raise TimeoutError(" ".join(cmd[:3]))
    p.wait()
    t_err.join(timeout=2)
    if cancel and cancel():
        raise Cancelled()
    if p.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} فشل: " + "".join(err[-12:])[-900:])
    return "".join(err)


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    fps = 30.0
    if v and v.get("avg_frame_rate", "0/0") != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else 30.0
    rot = 0
    for sd in (v or {}).get("side_data_list", []):
        rot = abs(int(sd.get("rotation", 0)))
    w, h = (int(v["width"]), int(v["height"])) if v else (0, 0)
    if rot in (90, 270):
        w, h = h, w
    return {"duration": float(j["format"].get("duration", 0)), "width": w, "height": h, "fps": fps, "has_audio": a is not None}


_encoder = None


def video_encoder():
    global _encoder
    if _encoder:
        return _encoder
    _encoder = "libx264"
    if shutil.which("nvidia-smi"):
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.2",
                            "-c:v", "h264_nvenc", "-f", "null", "-"], check=True, capture_output=True, timeout=30)
            _encoder = "h264_nvenc"
        except Exception:
            pass
    return _encoder


def encoder_args(profile=None, force_cpu=False, codec="h264"):
    """ترميز عالي الجودة بحجم صغير مع سقف معدل للبث · HEVC داخل MP4 بوسم hvc1 ليعمل على أجهزة أبل"""
    q = CONFIG["quality"].get(profile) or CONFIG["quality"]["balanced"]
    br = int(q["maxrate"].rstrip("M"))
    color = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    if codec == "hevc":
        br = max(2, int(br * 0.65))
        common = ["-maxrate", f"{br}M", "-bufsize", f"{br * 2}M", "-g", "60", "-tag:v", "hvc1", *color]
        if video_encoder() == "h264_nvenc" and not force_cpu:
            return ["-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(q["cq"] + 2), "-b:v", "0",
                    "-spatial-aq", "1", "-temporal-aq", "1", *common]
        return ["-c:v", "libx265", "-preset", "medium" if q["preset"] == "slow" else "fast", "-crf", str(q["crf"] + 3),
                "-x265-params", "aq-mode=3:psy-rd=1.5:log-level=error", *common]
    common = ["-maxrate", q["maxrate"], "-bufsize", f"{br * 2}M", "-g", "60", "-profile:v", "high", *color]
    if video_encoder() == "h264_nvenc" and not force_cpu:
        return ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(q["cq"]), "-b:v", "0",
                "-spatial-aq", "1", "-temporal-aq", "1", "-bf", "3", *common]
    return ["-c:v", "libx264", "-preset", q["preset"], "-crf", str(q["crf"]), "-tune", "film",
            "-x264-params", "aq-mode=3:aq-strength=0.9:deblock=-1,-1:psy-rd=1.0,0.15", *common]


def enhance_filters(profile=None, grade="none", grain=False):
    """تحسين حدة وتباين خفيف بعد التحجيم + تلوين وحبيبات فيلم اختيارية"""
    q = CONFIG["quality"].get(profile) or CONFIG["quality"]["balanced"]
    f = ["unsharp=5:5:0.55:3:3:0.0", "eq=contrast=1.03:saturation=1.07"] if q["enhance"] else []
    g = (CONFIG["grades"].get(grade) or {}).get("vf")
    if g:
        f.append(g)
    if grain:
        f.append("noise=c0s=5:c0f=t+u")
    return f


def filter_path(p):
    # المسارات مولّدة داخلياً؛ تُوضع بين علامتي اقتباس في رسم الفلاتر
    return str(p).replace("\\", "/").replace("'", "")
