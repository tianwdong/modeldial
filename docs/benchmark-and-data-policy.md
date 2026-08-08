# Benchmark and data distribution policy

Status: public-source release gate

Last reviewed: 2026-08-07

This document describes what the public ModelDial repository distributes and
what must be reviewed before a release. It is a project policy, not a legal
opinion or a replacement for checking the current terms of an upstream source.

## Public source and local data

- ModelDial source code is licensed under Apache-2.0.
- Local scan history, configuration, session observations, credentials and
  provider responses are not part of the repository and must not be committed.
- Website code, Cloudflare Worker code, remote evaluation services, publishing
  credentials and production snapshots remain outside the public repository.
- The bundled `development_seed` feed is generated entirely from deterministic
  synthetic formulas by `devtools/reference_snapshots/build_development_seed.py`.
  It must not contain copied local scores, timing, Token usage, costs, run IDs,
  endpoint IDs or other operator history, and it is never actionable as an
  official recommendation source.

## Question packs and answer fixtures

The catalog and the files it names under `questions/` are public benchmark
inputs and offline evaluation fixtures:

- `*.prompt.md` and the matching question documents define the task contract.
- `*.answer.json` defines the fixture grader or reference constraints used by
  local tests and release checks.
- Files not referenced by the current `catalog.json`, including retired and
  historical packs, must not be included in the public source tree or App
  bundle.

The distributed current-pack files are intentionally inspectable. They are not
a hidden benchmark, not a security boundary, and not a claim that a score
predicts success on an arbitrary production repository. Gold answers, grader
fixtures and historical results must remain offline evaluation inputs; runtime
recommendation rules must not import case-specific gold outputs.

Before adding or updating a question pack, the maintainer must confirm that the
content is original, synthetic, or otherwise redistributable. If any external
text, code, image, dataset or model-generated material is retained, add its
source, version, license or permission, and transformation notes next to the
fixture before merging it. Do not include real customer data, private session
content, credentials or copied provider prompts.

## Pricing snapshots

The pricing updater and its policy are public code. The checked-in snapshots
under `scanner/` record provenance for each model entry, including the source,
matched key, fetch time and whether a previous local value was preserved.

The current policy identifies LiteLLM's public model-price file as an upstream
source and pins a full Git commit plus the SHA-256 of the exact raw file. Both
network and offline-source refreshes verify those bytes before parsing. ModelDial
does not relicense that upstream material or imply endorsement by LiteLLM or any
model provider. Before publishing a refreshed snapshot, check the upstream
license and redistribution terms, retain the immutable revision, source hash,
URL, and attribution, and remove or replace an entry if its terms do not permit
bundling. Reference costs are estimates for comparison and are not provider
invoices.

## Provider names, logos and trademarks

Provider names and logos are used only for identification. Their attribution
and license information is maintained in
`Resources/Legal/THIRD_PARTY_NOTICES.txt`; ModelDial does not claim affiliation
or endorsement. ModelDial branding remains governed by `TRADEMARKS.md`.

## Release review

Before a public source release, review the current diff and tracked files for:

1. personal paths, secrets, session content and generated artifacts;
2. new benchmark material and its source/license record;
3. pricing provenance and upstream redistribution terms; and
4. third-party notices and the final binary dependency inventory.

The review result belongs in the release checklist and release notes. A passing
test run does not by itself establish copyright, trademark or redistribution
permission.
