## Summary

<!-- State the single user-visible or maintenance outcome. -->

## Scope

- [ ] Public macOS app, scanner, questions, scripts, tests, or development tools only.
- [ ] No website, Cloudflare implementation, private service code, or production configuration.
- [ ] No API keys, tokens, personal paths, session content, or generated artifacts.

## Verification

<!-- List the commands and the relevant result. -->

- `python3 -m unittest discover -s tests -q`
- `git diff --check`

## Notes for reviewers

<!-- Call out compatibility, migration, localization, or release implications. -->
