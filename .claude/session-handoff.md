# session-handoff

## Where we stopped
- Stacked branches: feature/packaging (#13) → feature/look-sound (#14) → feature/motion (#15) → docs/readme (#16)
  → perf/fast-transcribe (34.9.0) → feature/smart-cut (34.10.0) → feature/captions-pro (34.11.0) → feature/image-quality (34.12.0)

## Next step
- Merge PRs in order, then a real Kaggle run: log line "التفريغ: …×" for speed, split/active layouts on a two-person podcast, punch phrase with Gemini key

## Decisions made this session
- Batched whisper on GPU (16) and CPU (8); sequential path kept as fallback
- Smooth zoom needs ffmpeg 5+ (scale eval=frame with t); capability-checked, falls back to hard zoom
- Layout default stays "track"; title cards default off; safe zone, punch, auto color, low-res fix default on; stabilize off
