# Pricing snapshot updater

Development and release preparation can run this tool directly. The native App
and Cloudflare runner also use the same policy and validation code once,
immediately before a new local or remote execution starts.

Each execution freezes the validated snapshot for that run. Local snapshots
are stored under the App data directory; remote snapshots are stored in the
private durable checkpoint. Resume and retry reuse that exact snapshot instead
of downloading new prices mid-run. If the upstream file is unavailable or
validation fails, a new execution falls back to the last validated snapshot
and records the failure in its update report.

`policy.json` pins the LiteLLM source to a full Git commit and the SHA-256 of
the exact raw file. Network downloads and `--source-file` both verify those
raw bytes before JSON decoding. To update the source, review a new immutable
commit, replace its URL, revision, and digest together, then generate and
review a new snapshot candidate.

Generate a validated candidate and report without changing the installed
snapshot:

```bash
python3 devtools/update_pricing_snapshot.py
```

Review these files before applying:

- `artifacts/pricing/pricing_snapshot.candidate.json`
- `artifacts/pricing/pricing_update_report.json`

Apply a validated candidate atomically:

```bash
python3 devtools/update_pricing_snapshot.py --apply
```

Check an observed model that is not in the current snapshot:

```bash
python3 devtools/update_pricing_snapshot.py --include-model MODEL_ID
```

The update policy only permits an identical upstream key or an entry in
`policy.json` under `reviewed_matches`. Missing existing models retain their
last valid price with `stale: true`; missing requested models remain unpriced.
The candidate version changes only when normalized pricing content or its
provenance changes, not merely because the check time changed.
