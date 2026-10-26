"""فحص ذاتي بدون إنترنت: فيديو اصطناعي يمر بكامل خط الإنتاج"""
import shutil
import subprocess
import time

from .env import Env, Settings, ffprobe
from .jobs import Store, Worker
from .pipeline import Pipeline, steps_for
from . import compose, stock, transcribe, voice

WORDS = "هل تعرف لماذا يفشل أغلبنا في اتخاذ القرار الصحيح في الوقت المناسب رغم أننا نعرف الإجابة جيداً".split()


def _fake_transcript(*a, **k):
    words, t = [], 1.0
    for i in range(60):
        w = WORDS[i % len(WORDS)]
        words.append({"w": w, "s": round(t, 2), "e": round(t + 0.45, 2)})
        t += 0.55 + (1.2 if i % 9 == 8 else 0)
    segs = transcribe.regroup(words)
    return {"words": words, "segments": segs, "source": "whisper"}


def _fake_synth(settings, text, vopts, work, tag, gpu, log, cancel):
    n = len(text.split())
    out = work / f"voice_{tag}.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f=220:d={0.4 * n + 0.25}", "-ar", "48000", "-ac", "2", str(out)], check=True)
    return out, [{"w": w, "s": round(i * 0.4, 2), "e": round(i * 0.4 + 0.35, 2)} for i, w in enumerate(text.split())]


def run_topic(env, store, worker):
    """وضع «فكرة» بلا إنترنت: صوت وهمي ولقطات مولَّدة"""
    voice.synth, stock.search = _fake_synth, lambda *a, **k: []
    script = {"title": "فحص فكرة", "hook": "هل جربت هذا؟", "description": "", "hashtags": [],
              "scenes": [{"narration": "هذا أول مشهد في الفحص الذاتي", "query": "code"}, {"narration": "وهذا مشهد ثان قصير", "query": "ai"},
                         {"narration": "ثم خاتمة تدعوك للمتابعة", "query": "end"}]}
    opts = {"parts": 1, "formats": {"short": True}, "face": False, "hook": True, "edit": {"silence": False, "zoom": False, "cold_open": False},
            "audio": {"mode": "original", "engine": "edge", "text": "same", "dialect": "original", "voice": "ar-SA-HamedNeural", "music": ""},
            "source": {"type": "topic", "topic": "فحص", "script": script}}
    job = store.add("فحص فكرة", opts, steps_for(opts))
    worker.kick()
    t0 = time.time()
    while store.get(job["id"])["status"] in ("queued", "running") and time.time() - t0 < 600:
        time.sleep(1)
    j = store.get(job["id"])
    ok = j["status"] == "done" and bool(j["clips"])
    print("وضع فكرة:", j["status"], j.get("error") or "", f"({time.time() - t0:.1f}s)")
    for c in j["clips"]:
        for k, rel in c["files"].items():
            p = env.outputs / rel
            ok &= p.exists()
            if p.suffix == ".mp4":
                info = ffprobe(p)
                print(" ", c["n"], k, p.name, f"{info['width']}x{info['height']} {info['duration']:.1f}s")
    return ok


def run():
    env = Env()
    settings = Settings(env)
    settings.data["gemini_key"] = ""
    src = env.uploads / "selftest.mp4"
    if not src.exists():
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=40",
                        "-f", "lavfi", "-i", "sine=f=220:d=40", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                        str(src)], check=True)
    transcribe.transcribe = _fake_transcript
    store = Store(env)
    worker = Worker(store, Pipeline(env, settings))
    opts = {"parts": 2, "formats": {"short": True, "wide": True}, "face": True, "hook": True,
            "edit": {"silence": True, "zoom": True, "cold_open": True},
            "audio": {"mode": "original", "engine": "edge", "text": "same", "dialect": "original", "voice": "ar-SA-HamedNeural", "music": ""},
            "source": {"type": "file", "file_id": src.name, "name": "selftest"}}
    job = store.add("فحص ذاتي", opts, steps_for(opts))
    worker.kick()
    t0 = time.time()
    while store.get(job["id"])["status"] in ("queued", "running") and time.time() - t0 < 600:
        time.sleep(1)
    j = store.get(job["id"])
    ok = j["status"] == "done"
    print("الحالة:", j["status"], j.get("error") or "", f"({time.time() - t0:.1f}s)")
    for c in j["clips"]:
        for k, rel in c["files"].items():
            p = env.outputs / rel
            info = ffprobe(p) if p.suffix == ".mp4" else None
            print(" ", c["n"], k, p.name, f"{info['width']}x{info['height']} {info['duration']:.1f}s" if info else "")
            ok &= p.exists()
    ok &= run_topic(env, store, worker)
    print("✅ نجح الفحص" if ok else "❌ فشل الفحص")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
