import json
import shutil
from pathlib import Path

from config import CONFIG
from . import audio, backup, captions, covers, director, edit, ingest, motion, package, reframe, render, templates, thumbs, transcribe, voice
from .notify import Telegram, safe_name

FORMAT_LABEL = {"short": "Shorts", "wide": "Wide", "square": "Square", "portrait": "Portrait"}
RATIO = {"square": 1.0, "portrait": 0.8}


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
            return self._run(job, opts, a, brand, work, out, tg, gpu, has_key)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, job, opts, a, brand, work, out, tg, gpu, has_key):
        env, st = self.env, self.settings
        quality = opts.get("quality") or st.get("quality")
        log, cancel = job.log, job.cancelled
        speed = max(1.0, min(1.1, float(st.get("speed") or 1.0)))
        font_file = env.fonts / brand.get("font", CONFIG["fonts"]["default"])
        if not font_file.exists():
            font_file = env.fonts / CONFIG["fonts"]["default"]
        family = captions.font_family(font_file)
        job.set(voice_label=voice_label(a))

        proj = out / "project.json"
        if job.data.get("phase") == "produce" and proj.exists():
            # إعادة إخراج أو بعد المراجعة: المصدر والتفريغ واللقطات محفوظة
            pj = json.loads(proj.read_text("utf-8"))
            src, meta, tr, clips, seo = pj["src"], pj["meta"], pj["tr"], pj["clips"], pj["seo"]
            if not Path(src["path"]).exists():
                raise RuntimeError("الفيديو المصدر لم يعد محفوظاً · أعد الإنتاج من البداية")
            dur = meta["duration"]
            for k in ("download", "transcribe", "direct"):
                job.skip(k)
            for c in clips:
                c["files"] = {}
                c.pop("covers", None)
            job.set(clips=_public(clips), seo=seo, title=src["title"])
            log("إعادة الإخراج من المشروع المحفوظ")
        else:
            # ١) المصدر · ويبدأ تحميل نموذج التفريغ بالتوازي
            transcribe.preload(st.get("whisper"), gpu)
            job.step("download")
            src_opt = opts["source"]
            if src_opt["type"] == "file":
                src = ingest.local_file(env.uploads / src_opt["file_id"], src_opt.get("name", "video"))
            else:
                url = src_opt["url"] if src_opt["type"] == "link" else ingest.find_trend(src_opt["keyword"], src_opt.get("filter"), st, env, log)
                # «فخم» مع الشورتس: تنزيل حتى 4K ليبقى القص العمودي حاداً
                dl_h = 2160 if quality in ("premium", "cinematic") and opts["formats"].get("short") else None
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
            src = _keep_source(env, job.id, src)
            proj.write_text(json.dumps({"src": src, "meta": meta, "tr": tr, "clips": clips, "seo": seo}, ensure_ascii=False), "utf-8")
            if opts.get("review"):
                log("اللقطات جاهزة للمراجعة")
                return "review"


        # ٤) الصوت الجديد
        plan = []
        for c in clips:
            dur_src = c["e"] - c["s"]
            item = {"c": c, "s": c["s"], "dur_src": dur_src, "out_dur": dur_src / speed,
                    "words": captions.shift(tr["words"], c["s"], c["e"], speed)}
            ed = opts.get("edit") or {}
            if ed.get("scenes", True) and (opts.get("face") or ed.get("zoom")) and meta.get("width"):
                item["scenes"] = reframe.scenes(src["path"], c["s"], dur_src)
            if a["mode"] == "original":
                edl = edit.plan(tr["words"], c["s"], c["e"], opts, c.get("open"), item.get("scenes"))
                if edl and edl.get("smooth"):
                    edl["kw"] = _kw_times(tr["words"], c, c.get("keywords"))
                if edl:
                    item.update(edl=edl, out_dur=edit.total(edl["abs"]) / speed, words=edit.remap(tr["words"], edl["abs"], speed))
                    cut = dur_src - edit.total(edl["abs"]) + (edl["abs"][0][1] - edl["abs"][0][0] if edl["cold"] else 0)
                    log(f"مونتاج اللقطة {c['n']}: {len(edl['segs'])} مقطعاً · حُذف {cut:.1f}ث" + (" · افتتاحية بالخطاف" if edl["cold"] else ""))
            item["music"], info = _music_for(env, a, c, log)
            if item.get("edl") and info and info.get("beats"):
                item["edl"] = edit.snap_beats(item["edl"], info["beats"], speed)
                if item["edl"].get("beats"):
                    log(f"اللقطة {c['n']}: {item['edl']['beats']} تبديلات زوم على إيقاع الموسيقى ({info['bpm']} BPM)")
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
        fmts = [f for f in FORMAT_LABEL if opts["formats"].get(f)] or ["short"]
        wm = env.assets / brand["watermark"] if brand.get("watermark") and brand.get("watermark_on", True) else None
        wm = wm if wm and wm.exists() else None
        res = int(st.get("resolution") or 1080)
        total = len(plan) * len(fmts)
        done = 0
        for i, p in enumerate(plan):
            c = p["c"]
            n = c["n"]
            mix = audio.mix(a["mode"], work / f"mix_{i}.wav", p["out_dur"], orig=p["orig"], voice=p.get("voice"),
                            bg=p.get("bg"), music=p.get("music"), cancel=cancel,
                            clean=st.get("voice_clean") is not False)
            at = captions.find_phrase(p["words"], c.get("punch")) if brand.get("punch", True) else None
            p["punch"] = (at[0], at[1], c["punch"]) if at else None
            fx = _fx(p, c.get("keywords"), speed)
            stab = render.stab_prep(src["path"], p["s"], p["dur_src"], work, i, cancel) if st.get("stabilize") else None
            if (opts.get("edit") or {}).get("sfx", True):
                mix = audio.add_sfx(mix, work / f"mixfx_{i}.wav", fx["sfx"], env.assets, cancel)
            crop = None
            if any(f in render.CROPPED for f in fmts):
                crop = reframe.plan(src["path"], p["s"], p["dur_src"], meta["width"], meta["height"], env.assets, log,
                                    layout=opts.get("layout") or "track", cuts=p.get("scenes")) if opts.get("face") \
                    else ([(0.0, max(0, (meta["width"] - int(meta["height"] * 9 / 16)) // 2))], int(meta["height"] * 9 / 16) // 2 * 2)
            still = thumbs.best_frame(src["path"], p["s"], p["dur_src"], work, i)
            if still:
                files = thumbs.make(still, out, c["thumb"], n, font_file, brand,
                                    crop_x=reframe.x_at(crop[0], p["dur_src"] * 0.3) if crop and not isinstance(crop, dict) else None,
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
            if brand.get("bilingual") and has_key and p["words"]:
                p["trans"] = _translate(st, p["words"], brand.get("trans_lang") or "en", log)
            for fmt in fmts:
                job.note(f"اللقطة {n} · {package.FMT.get(fmt, fmt)}")
                fcrop = crop if fmt == "short" else reframe.rescale(crop, meta["width"], meta["height"], RATIO[fmt]) if fmt in RATIO and crop else None
                W, H = render.SIZES[(fmt, 720 if res <= 720 else 1080)]
                ex = _extras(brand, p, c, n, len(plan), fmt, None if opts.get("short_fill") else fcrop)
                ass = captions.build(work / f"cap_{i}_{fmt}.ass", p["words"], p["out_dur"], W, H, brand, family,
                                     c["hook"] if opts.get("hook") else None, keywords=c.get("keywords"),
                                     extras=ex)
                mpts = motion.cues(ex, fx, p["out_dur"]) if quality == "cinematic" else []
                massets = motion.assets(env.assets) if mpts else None
                dst = out / f"{FORMAT_LABEL[fmt]}_Part{n}.mp4"
                base = done
                render.clip(src["path"], dst, fmt, res, p["s"], p["dur_src"], p["out_dur"], speed, mix, ass, env.fonts, brand, wm,
                            crop=fcrop, work=work, cancel=cancel, template=_template(env, brand, fmt),
                            tpl=None if _template(env, brand, fmt) else templates.build(env, brand, fmt, W, H, n, font_file, work),
                            progress=lambda x, b=base: job.progress((b + x) / total), quality=quality,
                            edl=p.get("edl"), codec=st.get("codec"),
                            look={"grade": st.get("grade"), "grain": st.get("grain"), "auto": st.get("auto_color") is not False,
                                  "src_h": meta["height"] if st.get("lowres_fix") is not False else None, "stab": stab},
                            fill=opts.get("short_fill"), fx=fx, motion=mpts, motion_assets=massets)
                intro, outro = (env.assets / brand[k] if brand.get(k) and (env.assets / brand[k]).exists() else None
                                for k in ("intro_video", "outro_video"))
                if intro or outro:
                    job.note(f"اللقطة {n} · فاصل القناة")
                    render.bumper(dst, W, H, intro, outro, quality, st.get("codec"), cancel)
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
        if st.get("publish_pack") is not False:
            _publish(st, has_key, out, clips, seo, tr, src, meta, log)
        # ملف مضغوط باسم المشروع: مجلد لكل جزء بأسماء عربية
        title = safe_name(seo['title'] or src['title'])
        items = package.entries(env, job.id, _public(clips))
        package.write_zip(items, out / f"{title}.zip")
        job.set(zip=f"{job.id}/{title}.zip")
        if Telegram(st).ready and st.get("telegram_zip"):
            try:
                job.note(tg.deliver(out / f"{title}.zip", items, title, f"✅ {seo['title']} · {len(clips)} أجزاء", work / "tg",
                                    _light(work) if st.get("telegram_light") else None))
            except Exception as e:
                log(f"تعذّر الإرسال لتيليجرام: {str(e)[:160]}")
        log("اكتمل الإنتاج")
        backup.schedule(env)


def _keep_source(env, jid, src):
    """نقل الفيديو المنزّل لمجلد دائم لإعادة الإخراج، مع إبقاء آخر عدد محدد فقط"""
    p = Path(src["path"])
    if p.parent != env.uploads and p.exists():
        dst = env.sources / f"{jid}{p.suffix}"
        shutil.move(str(p), dst)
        src = {**src, "path": str(dst), "vtt": None}
    keep = CONFIG["cache"]["sources"]
    old = sorted(env.sources.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)[keep:]
    for f in old:
        f.unlink(missing_ok=True)
    return src


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


def _kw_times(words, c, keywords, gap=2.5):
    """أزمنة الكلمات المفتاحية نسبةً لبداية اللقطة (لنبضة الزوم)"""
    kws = {captions._norm(k) for k in (keywords or [])} - {""}
    out = []
    for w in words:
        if c["s"] <= w["s"] < c["e"] and captions._norm(w["w"]) in kws and (not out or w["s"] - c["s"] - out[-1] >= gap):
            out.append(round(w["s"] - c["s"], 3))
    return out[:5]


def _music_for(env, a, c, log):
    """موسيقى اللقطة: المختارة، أو الأنسب لمزاجها من المكتبة عند «تلقائي»"""
    name = a.get("music")
    if not name:
        return None, None
    try:
        if name == "auto":
            path, info = audio.pick_music(env.music, c.get("mood") or "neutral", c["n"])
            if path:
                log(f"اللقطة {c['n']}: موسيقى «{path.stem}» · {audio.MOODS[info['mood']]}")
            return path, info
        path = env.music / Path(name).name
        return (path, audio.analyze_music(path, env.music / ".meta.json")) if path.exists() else (None, None)
    except Exception as e:
        log(f"تعذّر تحليل الموسيقى ({str(e)[:100]})")
        return (env.music / Path(name).name if name != "auto" else None), None


def _translate(st, words, lang, log):
    """سطور ترجمة ثانية: جمل قصيرة بحدود الوقفات"""
    groups = captions.chunks(words, 7, 0.5)
    try:
        lines = director.translate_lines(st, [" ".join(w["w"] for w in g) for g in groups], lang)
    except Exception as e:
        log(f"تعذّرت الترجمة الثانية ({director.friendly(e)})")
        return []
    return [(g[0]["s"], g[-1]["e"] + 0.2, t) for g, t in zip(groups, lines)]


def _publish(st, has_key, out, clips, seo, tr, src, meta, log):
    """ملفات نشر لكل منصة لكل جزء + فصول الحلقة"""
    pack, chapters = {}, []
    if has_key:
        try:
            segs = tr["segments"] if tr.get("source") == "youtube" else transcribe.regroup(tr["words"])
            pack, chapters = director.publish_pack(st, clips, seo, segs, src["title"], meta["duration"])
        except Exception as e:
            log(f"تعذّر ملف النشر الذكي ({director.friendly(e)}) · نسخة أساسية")
    tags = seo.get("hashtags") or []
    for c in clips:
        k = pack.get(c["n"]) or {}
        hs = " ".join("#" + h.lstrip("#").replace(" ", "_") for h in (k.get("hashtags") or tags)[:8])
        yt = (f"{k.get('youtube_title') or c.get('full_title') or c['title']}\n\n{k.get('youtube_description') or seo.get('description', '')}\n\n"
              f"{hs}\n\nالكلمات الدلالية: {', '.join(k.get('youtube_tags') or tags)}")
        alts = c.get("alt_titles") or []
        if alts:
            yt += "\n\nعناوين بديلة:\n" + "\n".join(f"- {a}" for a in alts)
        (out / f"_pub_{c['n']}_yt.txt").write_text(yt, "utf-8-sig")
        (out / f"_pub_{c['n']}_tt.txt").write_text(f"{k.get('tiktok_caption') or c.get('hook') or c['title']}\n\n{hs}", "utf-8-sig")
        (out / f"_pub_{c['n']}_ig.txt").write_text(f"{k.get('instagram_caption') or c['title']}\n\n{hs}", "utf-8-sig")
    if chapters:
        (out / "_chapters.txt").write_text("\n".join(f"{_mmss(t)} {name}" for t, name in chapters), "utf-8-sig")


def _extras(brand, p, c, n, parts, fmt, crop):
    """عبارة الذروة، بطاقتا العنوان والختام، وتخطيط الشاشة المقسومة"""
    ex = {"punch": p.get("punch"), "layout": "split" if fmt == "short" and isinstance(crop, dict) else None,
          "trans": p.get("trans"), "speaker": c.get("speaker") if brand.get("lower_third", True) else None,
          "marks": captions.ordinals(p.get("words") or []) if brand.get("motion_gfx", True) else []}
    if brand.get("title_card"):
        tpl = brand.get("template") or {}
        ex.update(title=c["title"], part=f"الجزء {thumbs.part_word(n)}" if parts > 1 else "")
        if not (tpl.get("style") not in (None, "none") and tpl.get("show_cta")):
            ex["outro"] = f"تابع للجزء {thumbs.part_word(n + 1)}" if n < parts else CONFIG["template"]["cta_default"]
    return ex


def _fx(p, keywords, speed):
    """وميض ونفخة عند نهاية الافتتاحية + نقرات على الكلمات المفتاحية (5 كحد أقصى بفاصل 2.5ث)"""
    edl = p.get("edl") or {}
    flash = (edl["abs"][0][1] - edl["abs"][0][0]) / speed if edl.get("cold") else None
    sfx = [("whoosh", max(0.0, flash - 0.32))] if flash else []
    if p.get("punch") and (flash is None or abs(p["punch"][0] - flash) > 1.0):
        sfx.append(("whoosh", max(0.0, p["punch"][0] - 0.3)))
    kws = {captions._norm(k) for k in (keywords or [])} - {""}
    last = -9.0
    for w in p.get("words") or []:
        if len(sfx) >= 6 or captions._norm(w["w"]) not in kws:
            continue
        if w["s"] - last >= 2.5 and (flash is None or abs(w["s"] - flash) > 0.8):
            sfx.append(("pop", w["s"]))
            last = w["s"]
    return {"flash": flash, "sfx": sfx}


def _template(env, brand, fmt):
    name = brand.get(f"frame_{fmt}")
    p = env.assets / name if name else None
    return p if p and p.exists() else None


def _public(clips):
    keys = ("n", "s", "e", "title", "hook", "thumb", "score", "reason", "files", "full_title", "desc", "dur", "covers", "alt_titles", "speaker")
    return [{k: c.get(k) for k in keys} for c in clips]


def _mmss(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def deliver_again(env, settings, job):
    tg = Telegram(settings)
    if not tg.ready:
        raise RuntimeError("أضف توكن البوت ومعرّف المحادثة من الإعدادات")
    out = env.outputs / job["id"]
    z = env.outputs / job["zip"] if job.get("zip") else next(iter(sorted(out.glob("*.zip"))), None)
    if not z or not z.exists():
        raise RuntimeError("لا يوجد ملف مضغوط لهذا المشروع")
    work = env.temp / f"tg_{job['id']}"
    try:
        return tg.deliver(z, package.entries(env, job["id"], job["clips"]), z.stem,
                          f"✅ {job['seo'].get('title') or job['title']}", work,
                          _light(work) if settings.get("telegram_light") else None)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _light(work):
    def make(p):
        work.mkdir(parents=True, exist_ok=True)
        return render.light_copy(p, work / f"light_{Path(p).name}")
    return make
