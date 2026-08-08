<div align="center">
  <img src="Resources/AppIcon.svg" alt="ModelDial App icon" width="96" height="96">
  <h1>ModelDial</h1>
  <p><strong>Find a better-fit model configuration for each coding task through real evaluations.</strong></p>
  <p>Compare complete <code>model + effort + route</code> combinations while retaining quality, elapsed time, token, and reference-cost evidence.</p>
  <p>
    <a href="https://github.com/tianwdong/modeldial/releases/download/v0.1.0-preview.1/modeldial-0.1.0-preview.1-macos-arm64.dmg"><strong>Download the macOS preview</strong></a>
    · <a href="https://modeldial.com">Website</a>
    · <a href="https://modeldial.com/radar">Official Radar</a>
    · <a href="https://github.com/tianwdong/modeldial">GitHub</a>
    · <a href="README.md">简体中文</a>
  </p>
  <p><code>macOS 13+</code> · <code>Apple Silicon</code> · <code>local-first</code> · <code>no built-in telemetry</code></p>
</div>

![ModelDial Radar view](docs/screenshots/modeldial-radar-en.jpg)

*Sample data; this is not a live leaderboard.*

## Browse Official Radar First; Local Evaluation Is Optional

ModelDial's primary path is browsing the public first-party Radar, not configuring a model or running a local test first. In the repaired app, starting with the next preview, click the ModelDial capsule in the menu bar to see configuration results from scheduled official evaluations; the [web Radar](https://modeldial.com/radar) is available now. Browsing official Radar requires no API key and uses none of your model quota.

Connect a local Codex, Claude Code, or Grok Build provider—or a compatible endpoint—only when you want to compare your own provider, route, and effort combinations. Local results and the official leaderboard remain explicit, separately selectable data sources.

## Why ModelDial

A model name is only part of a working configuration. The same model can behave differently with another effort, route, or provider. ModelDial puts those combinations through repeatable coding evaluations so you can choose from your own task evidence:

- Compare real `model + effort + route` combinations instead of model names alone.
- Keep the comparison scope stable with versioned question packs and evaluation profiles.
- Preserve quality, elapsed time, tokens, reference cost, failure state, and per-question evidence.
- Configure, run, and retain history locally; whether a remote model is called depends on the provider or endpoint you choose.

## Product Screenshots

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/modeldial-compare-en.jpg" alt="ModelDial current and candidate configuration comparison"></td>
    <td width="50%"><img src="docs/screenshots/modeldial-settings-scan-en.jpg" alt="ModelDial scan settings"></td>
  </tr>
  <tr>
    <td><strong>Configuration comparison</strong><br>Review quality, elapsed time, token, and reference-cost differences between the current and candidate configurations.</td>
    <td><strong>Scan strategy</strong><br>Set question count, parallelism, timeout, and retry behavior before running a repeatable evaluation.</td>
  </tr>
</table>

## Download & First Run

**Current version: [`v0.1.0-preview.1`](https://github.com/tianwdong/modeldial/releases/tag/v0.1.0-preview.1)** · [Direct Apple Silicon DMG download](https://github.com/tianwdong/modeldial/releases/download/v0.1.0-preview.1/modeldial-0.1.0-preview.1-macos-arm64.dmg)

Requirements: macOS 13 or later on Apple Silicon. Intel Macs are not currently supported. To verify the download, get `SHA256SUMS` from the same release and run `shasum -a 256 -c SHA256SUMS` in the asset directory.

> [!IMPORTANT]
> `v0.1.0-preview.1` is an unsigned / unnotarized preview with no Developer ID signature or Apple notarization. If macOS blocks the first launch, use **System Settings → Privacy & Security → Open Anyway** for this app; do not disable Gatekeeper or use `xattr`, `spctl`, or other commands to bypass system security checks.

> [!NOTE]
> The published `v0.1.0-preview.1` does not contain the official Radar feed URL or this first-run fix. The repaired source will ship in the next preview; until then, use the [web Radar](https://modeldial.com/radar) or build from source as shown below.

First run in the repaired build (starting with the next preview):

1. Open the DMG, drag `modeldial.app` to `Applications`, eject the DMG, and launch the copy from `Applications`.
2. Click the ModelDial capsule in the menu bar and browse official Radar immediately; no local model or scan is required.
3. To produce evidence for your own configurations, open **Evaluation → Connections**, import a provider or add a compatible endpoint, then choose a profile and run a scan.

## How It Works

1. **Browse official Radar.** The app loads a public first-party snapshot without requiring a local model or API key.
2. **Connect models when needed.** Import a local Codex, Claude Code, or Grok Build provider—or configure a compatible endpoint—only for your own evaluations.
3. **Run a local evaluation.** [`questions/catalog.json`](questions/catalog.json) is the authority for the versioned question pack and profiles; question count, parallelism, timeout, and retry settings drive the run.
4. **Review each evidence source.** Switch explicitly between official and local results across Radar, comparison, history, and exports.

## Highlights

- **Configuration-level comparisons:** Keep the model, effort, route, and provider identity with each result.
- **Explainable results:** Show quality, elapsed time, tokens, reference cost, and failure state together for review.
- **Local session observation:** Observe local Codex, Claude Code, and Grok Build session state alongside scan results.
- **Native menu-bar experience:** The macOS native app is the only product runtime entry point; repository scripts and backend modules are called by the app or build scripts.

## Privacy

- Configuration, scan history, runtime state, and limited session metadata stay on the machine.
- API keys are stored in the macOS Keychain and are not written into scan history.
- During an evaluation, synthetic questions and model responses pass through the local CLI or model service you select; those services retain their own terms and privacy policies.
- The app has no built-in telemetry and uploads neither session transcripts nor local evaluation results. ModelDial-branded preview packages only read the public first-party Radar snapshot; source builds do not read a remote leaderboard unless you explicitly configure a compatible endpoint.

See [PRIVACY.md](PRIVACY.md) and the [open-source architecture boundary](docs/architecture.md) for more detail. The website, Cloudflare Worker, remote evaluation runner, and snapshot publisher are outside this repository and are not App runtime entry points.

## Build from Source

Source builds target macOS 13+ on Apple Silicon and require Xcode 16.4. On the first `build.sh` run, the locked Python runtime is extracted under the ignored `build/` directory; no separate Python installation is required:

Build inputs are pinned to Python 3.14.3 by [`python-runtime.lock.json`](build-support/python-runtime.lock.json), while [`pyinstaller-requirements.txt`](build-support/pyinstaller-requirements.txt) locks PyInstaller 6.21.0 and its dependencies. See the [release checklist](docs/release-checklist.md) for the full supply-chain gates.

```bash
MODELDIAL_REFERENCE_SNAPSHOT_URL=https://reference.modeldial.com/reference-snapshots ./build.sh
open build/modeldial-candidate.app
```

The command above enables official Radar. Run `./build.sh` without the environment variable for a fully local source build; the remote snapshot URL defaults to empty.

`build.sh` builds the Swift app, freezes the Python backend, and runs the snapshot smoke check. For Swift or resource-only changes after one complete build, use:

```bash
./build-dev.sh
```

Changes to `scanner/`, `scripts/`, or `questions/` require a fresh `./build.sh`. The separate binary-preview constraints are documented in the [preview notes](docs/releases/v0.1.0-preview.1.md).

<details>
<summary>Remove session-observer hooks installed from source</summary>

This command removes only ModelDial's Codex / Claude Code hook and helper while preserving unrelated hooks:

```bash
python3 scripts/install_session_observer.py --uninstall
```

</details>

## Development & Testing

Run the full Python regression suite:

```bash
python3 -m unittest discover -s tests -v
```

For changes to versioned DTOs or architecture contracts, also run the focused contract suite:

```bash
python3 -m unittest tests.test_architecture_baseline -v
```

Before submitting a change, check diff formatting:

```bash
git diff --check
```

## Docs

- [Open-source architecture boundary](docs/architecture.md): responsibilities between the App, scanner, and private services.
- [Benchmark and data distribution policy](docs/benchmark-and-data-policy.md): question packs, answer fixtures, pricing snapshots, and provider assets.
- [Open-source content audit](docs/open-source-content-audit.md): source, attribution, and question-pack search records.
- [Release checklist](docs/release-checklist.md): separate source-candidate and binary-release gates.
- [Preview notes](docs/releases/v0.1.0-preview.1.md): installation steps and limitations for v0.1.0-preview.1.
- [Next preview candidate](docs/releases/v0.1.0-preview.2.md): unreleased v0.1.0-preview.2 fixes, verification, and remaining gates.
- [Security policy](SECURITY.md): private vulnerability-reporting channel.

## Contributing & License

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. The source code is licensed under the [Apache License 2.0](LICENSE). The ModelDial name, logo, and app icon are brand assets and are not licensed by the source license; see [TRADEMARKS.md](TRADEMARKS.md). Distributed third-party notices are in [NOTICE](NOTICE) and [Resources/Legal](Resources/Legal).
