import json
import shutil

from config import CONFIG
from . import audio, captions, covers, director, edit, ingest, reframe, render, templates, thumbs, transcribe, voice
from .notify import Telegram

FORMAT_LABEL = {"short": "Shorts", "wide": "Wide"}


def steps_for(opts):
    mode = opts["audio"]["mode"]
    s = ["download", "transcribe", "direct"]
    if mode != "original":
        s.append("voice")
    if mode == "bg":
        s.append("separate")
    return s + ["render", "package"]


def voice_label(a):
    if a["mode"] == "original":
        return "الصوت الأصلي"
    dial = CONFIG["dialects"].get(a["dialect"], {}).get("label", "")
    names = dict(CONFIG["edge_voices"].get(a["dialect"], []) + CONFIG["gemini_voices"])
    eng = "Edge" if a["engine"] == "edge" else "Gemini"
    return f"{eng} · {names.get(a['voice'], a['voice']).split(' ·')[0]} · {dial}"


class Pipeline:
    def __init__(self, env, settings):
        self.env, self.settings = env, settings

    def __call__(self, job):
        env, st = self.env, self.settings
        opts = job.data["opts"]
        a = opts["audio"]
        brand = st.data["brand"]
        work = env.temp / job.id
        out = env.outputs / job.id
        work.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)
        tg = Telegram(st)
        gpu = bool(env.gpu)
        has_key = bool(st.get("gemini_key"))
        if a["mode"] != "original" and (a["engine"] == "gemini" or a["text"] != "same" or a["dialect"] != "original") and not has_key:
            raise RuntimeError("هذا الخيار يحتاج مفتاح Gemini · أضفه من الإعدادات")
        try:
            self._run(job, opts, a, brand, work, out, tg, gpu, has_key)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, job, opts, a, brand, work, out, tg, gpu, has_key):
        env, st = self.env, self.settings
        log, cancel = job.log, job.cancelled
        speed = max(1.0, min(1.1, float(st.get("speed") or 1.0)))
        font_file = env.fonts / brand.get("font", CONFIG["fonts"]["default"])
        if not font_file.exists():
            font_file = env.fonts / CONFIG["fonts"]["default"]
        family = captions.font_family(font_file)
        job.set(voice_label=voice_label(a))

        # ١) المصدر
        job.step("download")
        src_opt = opts["source"]
        if src_opt["type"] == "file":
            src = ingest.local_file(env.uploads / src_opt["file_id"], src_opt.get("name", "video"))
        else:
            url = src_opt["url"] if src_opt["type"] == "link" else ingest.find_trend(src_opt["keyword"], src_opt.get("filter"), st, env, log)
            # «فخم» مع الشورتس: تنزيل حتى 4K ليبقى القص العمودي حاداً
            dl_h = 2160 if st.get("quality") == "premium" and opts["formats"].get("short") else None
            src = ingest.download(url, work, st, env, log, job.progress, job.note, dl_h)
        meta = ingest.describe(src)
        dur = meta["duration"]
        job.set(title=src["title"], url=src["url"], duration=round(dur, 1))
        job.note(_mmss(dur))
        tg.message(f"⚙️ لقطة: بدأ إنتاج «{src['title']}» · {opts['parts']} أجزاء")

        # ٢) التفريغ
        job.step("transcribe")
        wav16 = transcribe.extract_audio(src["path"], work / "a16.wav", cancel) if meta["has_audio"] else None
        if wav16:
            job.set(peaks=transcribe.peaks(wav16))
            tr = transcribe.transcribe(wav16, src.get("vtt"), st.get("whisper"), gpu, log, cancel, job.progress)
        else:
            tr = {"words": [], "segments": [], "source": "none"}
        segments = tr["segments"] if tr["source"] == "youtube" else transcribe.regroup(tr["words"])
        (out / "transcript.json").write_text(json.dumps([[w["s"], w["e"], w["w"]] for w in tr["words"]], ensure_ascii=False), "utf-8")
        job.note(f"{len(tr['words'])} كلمة · {'Whisper' if tr['source'] == 'whisper' else 'يوتيوب'}" + (" · GPU" if gpu else ""))

        # ٣) المونتير
        job.step("direct")
        clips, seo = None, {}
        parts = max(1, min(CONFIG["clip"]["max_parts"], int(opts["parts"])))
        if has_key and segments:
            try:
                clips, seo = director.pick_clips(st, segments, dur, parts, src["title"], src["description"], log)
            except Exception as e:
                log(f"تعذّر تحليل Gemini ({director.friendly(e)}) · تقطيع تلقائي")
        if not clips:
            clips = director.fallback_clips(dur, parts, segments)
        seo = {"title": seo.get("title") or src["title"], "thumb": seo.get("thumb") or " ".join(src["title"].split()[:3]),
               "description": seo.get("description") or "", "hashtags": seo.get("hashtags") or ["لقطة", "تريند"]}
        for i, c in enumerate(clips, 1):
            c.update(n=i, title=c["title"] or seo["title"], hook=c["hook"] or seo["thumb"], thumb=c["thumb"] or seo["thumb"], files={})
        job.set(clips=_public(clips), seo=seo)
        job.note(f"{len(clips)} من {max(1, len(segments))} مقطع")

        # ٤) الصوت الجديد
        plan = []
        for c in clips:
            dur_src = c["e"] - c["s"]
            item = {"c": c, "s": c["s"], "dur_src": dur_src, "out_dur": dur_src / speed,
                    "words": captions.shift(tr["words"], c["s"], c["e"], speed)}
            if a["mode"] == "original":
                edl = edit.plan(tr["words"], c["s"], c["e"], opts, c.get("open"))
                if edl:
                    item.update(edl=edl, out_dur=edit.total(edl["abs"]) / speed, words=edit.remap(tr["words"], edl["abs"], speed))
                    cut = dur_src - edit.total(edl["abs"]) + (edl["abs"][0][1] - edl["abs"][0][0] if edl["cold"] else 0)
                    log(f"مونتاج اللقطة {c['n']}: {len(edl['segs'])} مقطعاً · حُذف {cut:.1f}ث" + (" · افتتاحية بالخطاف" if edl["cold"] else ""))
            plan.append(item)
        if a["mode"] != "original":
            job.step("voice")
            for i, p in enumerate(plan):
                job.note(f"اللقطة {i + 1} من {len(plan)}")
                c = p["c"]
                text = " ".join(w["w"] for w in tr["words"] if c["s"] <= w["s"] < c["e"]) or c["title"]
                text = director.rewrite(st, text, a["text"], a["dialect"], p["out_dur"]) if has_key else text
                vwav, vwords = voice.synth(st, text, a, work, f"{i}", gpu, log, cancel)
                vwav, vwords, vdur = audio.fit(vwav, vwords, p["out_dur"], work, f"{i}", cancel)
                need = vdur * speed
                s = c["s"] if c["s"] + need <= dur else max(0.0, dur - need)
                p.update(s=s, dur_src=min(need, dur - s), out_dur=vdur, words=vwords, voice=vwav, text=text)
                job.progress((i + 1) / len(plan))

        # ٥) الخلفية
        for i, p in enumerate(plan):
            p["orig"] = audio.segment(src["path"], p["s"], p["dur_src"], speed, work / f"orig_{i}.wav", meta["has_audio"], cancel,
                                      p.get("edl"))
        if a["mode"] == "bg":
            job.step("separate")
            for i, p in enumerate(plan):
                job.note(f"اللقطة {i + 1} من {len(plan)}")
                p["bg"] = audio.separate(p["orig"], work, gpu, log, cancel)
                job.progress((i + 1) / len(plan))

        # ٦) الإخراج
        job.step("render")
        fmts = [f for f in ("short", "wide") if opts["formats"].get(f)] or ["short"]
        wm = env.assets / brand["watermark"] if brand.get("watermark") else None
        wm = wm if wm and wm.exists() else None
        music = env.music / a["music"] if a.get("music") else None
        res = int(st.get("resolution") or 1080)
        total = len(plan) * len(fmts)
        done = 0
        for i, p in enumerate(plan):
            c = p["c"]
            n = c["n"]
            mix = audio.mix(a["mode"], work / f"mix_{i}.wav", p["out_dur"], orig=p["orig"], voice=p.get("voice"),
                            bg=p.get("bg"), music=music if music and music.exists() else None, cancel=cancel)
            crop = None
            if "short" in fmts:
                crop = reframe.plan(src["path"], p["s"], p["dur_src"], meta["width"], meta["height"], env.assets, log) if opts.get("face") \
                    else ([(0.0, max(0, (meta["width"] - int(meta["height"] * 9 / 16)) // 2))], int(meta["height"] * 9 / 16) // 2 * 2)
            still = thumbs.best_frame(src["path"], p["s"], p["dur_src"], work, i)
            if still:
                files = thumbs.make(still, out, c["thumb"], n, font_file, brand,
                                    crop_x=reframe.x_at(crop[0], p["dur_src"] * 0.3) if crop else None,
                                    templates={f: _template(env, brand, f) or _gen_thumb(env, brand, f, font_file, work) for f in ("short", "wide")})
                c["files"].update({k: f"{job.id}/{v.name}" for k, v in files.items()})
            try:
                cov = covers.make(src["path"], p["s"], p["dur_src"], out, work, c["thumb"], c.get("highlight"), n,
                                  font_file, brand, env.assets, log)
                for fmt_k in ("short", "wide"):
                    if cov.get(f"thumb_{fmt_k}"):
                        c["files"][f"thumb_{fmt_k}"] = f"{job.id}/{cov[f'thumb_{fmt_k}']}"
                c["covers"] = {k: [f"{job.id}/{x}" for x in v] for k, v in cov.get("covers", {}).items()}
            except Exception as e:
                log(f"تعذّر مصمم الأغلفة ({str(e)[:120]}) · استخدام الغلاف البسيط")
            for fmt in fmts:
                job.note(f"اللقطة {n} · {'شورتس' if fmt == 'short' else 'عريض'}")
                W, H = render.SIZES[(fmt, 720 if res <= 720 else 1080)]
                ass = captions.build(work / f"cap_{i}_{fmt}.ass", p["words"], p["out_dur"], W, H, brand, family,
                                     c["hook"] if opts.get("hook") else None)
                dst = out / f"{FORMAT_LABEL[fmt]}_Part{n}.mp4"
                base = done
                render.clip(src["path"], dst, fmt, res, p["s"], p["dur_src"], p["out_dur"], speed, mix, ass, env.fonts, brand, wm,
                            crop=crop if fmt == "short" else None, work=work, cancel=cancel, template=_template(env, brand, fmt),
                            tpl=None if _template(env, brand, fmt) else templates.build(env, brand, fmt, W, H, n, font_file, work),
                            progress=lambda x, b=base: job.progress((b + x) / total), quality=st.get("quality"),
                            edl=p.get("edl"))
                c["files"][fmt] = f"{job.id}/{dst.name}"
                done += 1
            part = thumbs.part_word(n)
            title = f"{c['title']} - الجزء {part}"
            tags = " ".join("#" + t for t in seo["hashtags"][:8])
            desc = f"🔥 الجزء {part}\n\n{seo['description']}\n\n" + (f"🔗 الحلقة كاملة:\n{src['url']}\n\n" if src["url"] else "") + \
                   f"#الجزء_{part} {tags}"
            (out / f"Title_Part{n}.txt").write_text(title, "utf-8-sig")
            (out / f"Desc_Part{n}.txt").write_text(desc, "utf-8-sig")
            c.update(full_title=title, desc=desc, dur=round(p["out_dur"], 1), s=round(p["s"], 2), e=round(p["s"] + p["dur_src"], 2))
            job.set(clips=_public(clips))

        # ٧) التغليف والإرسال
        job.step("package")
        zpath = shutil.make_archive(str(env.outputs / f"{job.id}_lakta"), "zip", out)
        zip_rel = f"{job.id}/{job.id}_lakta.zip"
        shutil.move(zpath, out / f"{job.id}_lakta.zip")
        job.set(zip=zip_rel)
        if Telegram(st).ready and st.get("telegram_zip"):
            files = [out / f"{FORMAT_LABEL[f]}_Part{c['n']}.mp4" for c in clips for f in fmts]
            try:
                job.note(tg.deliver(out / f"{job.id}_lakta.zip", files, f"✅ {seo['title']} · {len(clips)} أجزاء"))
            except Exception as e:
                log(f"تعذّر الإرسال لتيليجرام: {str(e)[:160]}")
        log("اكتمل الإنتاج")


def _gen_thumb(env, brand, fmt, font_file, work):
    # طبقة القالب المولّد للأغلفة (بدون شارة الجزء لأن الغلاف يكتبها بخط كبير)
    t = templates.tpl_conf(brand)
    if t["style"] not in templates.STYLES:
        return None
    W, H = (1080, 1920) if fmt == "short" else (1920, 1080)
    img, _ = templates.static(t["style"], W, H, {**t, "show_prog": False}, font_file, None)
    p = work / f"tpl_thumb_{fmt}.png"
    img.save(p)
    return p


def _template(env, brand, fmt):
    name = brand.get(f"frame_{fmt}")
    p = env.assets / name if name else None
    return p if p and p.exists() else None


def _public(clips):
    keys = ("n", "s", "e", "title", "hook", "thumb", "score", "reason", "files", "full_title", "desc", "dur", "covers")
    return [{k: c.get(k) for k in keys} for c in clips]


def _mmss(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def deliver_again(env, settings, job):
    tg = Telegram(settings)
    if not tg.ready:
        raise RuntimeError("أضف توكن البوت ومعرّف المحادثة من الإعدادات")
    out = env.outputs / job["id"]
    files = sorted(out.glob("*_Part*.mp4"))
    return tg.deliver(out / f"{job['id']}_lakta.zip", files, f"✅ {job['seo'].get('title') or job['title']}")
