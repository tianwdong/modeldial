# Case Stream Explorer v17 deterministic v2

This is the production candidate for `frontend-case-stream-explorer-v17@v2`.

The model receives the exact v17 text prompt and starter. The scorer is fully deterministic: 55 atomic Playwright points, 30 points for three complete hidden workflows, and 15 points for seven final screenshots measured from the public starter toward frozen reference captures. No LLM is used to judge visuals.

The legacy `case_stream_explorer_v17` directory remains available for replaying `@v1` evidence. New runs use this package only after the public and private migration gates pass.

See `docs/frontend-v17-deterministic-production-spec.md` for frozen identities, live distribution evidence, compatibility rules, and release boundaries.
