# Session handoff (v34.17.4)

## State
- Open PR stack, merge in this order: #28 chore/stock-keys-exception -> #29 fix/hook-duration -> #30 feature/job-quality -> #31 fix/watermark-toggle -> #32 fix/ornament-fallback -> #33 feature/trend-picker -> #34 feature/frame-png-generator -> #35 feature/stock-providers -> #36 feature/topic-script -> #37 feature/topic-compose -> #38 feature/topic-ui -> #39 feature/topic-credits.
- Phase A (6 fixes) and phase B (topic -> video: script, compose, UI, credits) implemented, selftest passed on #38 snapshot.
- main holds the history-purged tree plus cinematic PR #25.

## Decisions
- Pexels/Pixabay keys are the only approved external API; optional, fallback = generated gradient footage.
- Pexels new key issuance is paused (checked 2026-10-26): Pixabay is the practical key; the UI hint says so.
- Topic mode builds one source.mp4 then reuses the normal pipeline in original-audio mode.

## Open issues
- User must rotate the Telegram token via @BotFather and close stale PR #26.
- Edge TTS is unreachable from the sandbox; topic render verified offline with fake TTS only.
- Real run with a Pixabay key and Gemini key still untested by the user.

## Next step
- User merges the stack in order, tests the "فكرة" tab with a Pixabay key, then plan v34.18.
