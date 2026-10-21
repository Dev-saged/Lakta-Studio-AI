# session-handoff

## Where we stopped
- Stacked branches: fix/telegram-zip (#11, 34.5.1) → fix/easy-login (#12, 34.5.2) → feature/packaging (34.6.0) → feature/look-sound (34.7.0) → feature/motion (34.8.0)

## Next step
- Merge in that order, then a real Kaggle run: check keyword coloring, flash/whoosh at cold open, HEVC playback on phone, Telegram per-part bundles

## Decisions made this session
- HEVC stays in MP4 (hvc1 tag); H.264 remains default for widest compatibility
- Zip internals: folder per part with Arabic names; server-side file names unchanged
- SFX are generated locally with ffmpeg lavfi (no licensed assets); max 5 pops, 2.5s apart
- SFX chip works in every audio mode; other smart-edit chips only with original audio
