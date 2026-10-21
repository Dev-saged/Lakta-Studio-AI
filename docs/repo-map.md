# repo-map

Keep under 5KB. No code. Update by 2 lines after every change.

## Files
- run.py: one-cell launcher (deps incl. yt-dlp[default]+deno, env, server, Cloudflare tunnel, --selftest)
- config.py: CONFIG (app, models + regex patterns, whisper sizes, dialects, voices, template, defaults)
- lakta/env.py: Kaggle/Colab/VPS paths, secrets (settings → env → Kaggle → Colab), Settings, ffmpeg run/ffprobe, encoder
- lakta/server.py: stdlib HTTP API, cookie-key auth, chunked SSE, uploads, files with Range
- lakta/jobs.py: Store (jobs.json), Worker queue, Job progress/notes
- lakta/pipeline.py: download → transcribe → direct → voice → separate → render → package
- lakta/ingest.py: yt-dlp with client fallback, cookies (file or YOUTUBE_COOKIES), trend search
- lakta/transcribe.py: faster-whisper (numpy input, GPU→CPU fallback), VTT fallback, peaks
- lakta/director.py: Gemini (google-genai) clip picking, SEO, rewrite/dialect, model resolve
- lakta/voice.py: Edge TTS (word boundaries) and Gemini TTS + whisper alignment
- lakta/audio.py: segment/atempo, fit, Demucs (torchcodec retry), mix + loudnorm
- lakta/reframe.py: 9:16 face tracking (YuNet, Haar fallback) → crop expression
- lakta/captions.py: ASS karaoke (3 styles), hook position per template
- lakta/render.py: ffmpeg graph: crop/scale, frame or template, ass, progress bar, CTA, watermark
- lakta/templates.py: template generator (Gemini spec, 4 styles, ornament, CTA)
- lakta/edit.py: EDL from word timings (silence/filler/stutter cuts, zoom subdivisions, cold open), remap words
- lakta/covers.py: cover designer (face/sharpness frame pick, enhance, hero/split/pop layouts, highlight word)
- lakta/fonts.py: static bold instancing with Arabic check, Google Fonts fetch (API, falls back to raw file names)
- lakta/thumbs.py: thumbnails + posters; lakta/notify.py: Telegram; lakta/tunnel.py: cloudflared
- web/index.html: single-file RTL UI (studio, jobs, library, brand, prefs, #test, login)

## Key functions and IDs
- Pipeline.__call__/_run, steps_for, render.clip, captions.build, templates.build/static/previews
- API: /api/state, /api/jobs (GET list, POST create), /api/events, /api/upload, /api/template/*
- UI ids: #job-form, #job-detail, #tpl-card, #cookies-guide; signals S.state, S.jobs, S.sel

## Decisions
- ASS via `ass` filter with shaping=complex and Encoding=-1: only way Arabic shapes and orders correctly in libass
- Templates drawn procedurally (true alpha); Gemini only designs spec and optional ornament
- Uploaded PNG template beats generated template per format
- Trash: soft delete (job.deleted), restore, purge; auto-purge after CONFIG trash.days (7) in Store saver
- Quality profiles in CONFIG.quality (crf/preset/cq/maxrate/enhance); premium+shorts downloads up to 4K (VP9, mkv)
- NVENC args fall back to libx264 on failure; output tagged BT.709, GOP 60, capped bitrate
- Covers: 3 variants per size as Cover_*_PartN_k.jpg; variant 1 overwrites Thumbnail_* (thumbs.make kept as fallback)
- Smart edit only in original-audio mode: video uses split/trim/concat (zoom per segment), audio cuts only at real cuts
- Bundled fonts are static instances named 'Lakta <Family>' (unique family for libass); downloads saved as Google-<Family>.ttf
- SSE uses chunked encoding + client polls /api/jobs when silent >20s (tunnels may hold streams)

## Solved bugs (never reintroduce)
- Arabic captions tofu/reversed: subtitles filter + simple shaping → use ass filter, shaping=complex, Encoding=-1
- Transcribe "open() got an unexpected keyword argument 'metadata_errors'": faster-whisper decode via new PyAV → pass numpy array from our 16k wav
- Jobs not appearing until refresh (duplicates created): SSE without length/chunking buffered by tunnel → chunked SSE, polling fallback, refresh after submit, duplicate guard
- YouTube "Sign in to confirm you're not a bot" on Kaggle: deno + client fallback + cookies; clear error with actions
- Hardcoded Telegram token in code: secrets only

- Ornament toggle disabled without key + raw 429 toast vanishing: switch always enabled, geometric fallback, sticky copyable errors, friendly() maps Gemini errors

## Open issues
- (none) · quality, covers, smart edit, fonts delivered in 34.2–34.5
