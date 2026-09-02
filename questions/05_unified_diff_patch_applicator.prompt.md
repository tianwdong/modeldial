Do not use external tools.
Do not run code.

Design exactly two cache-regression portfolios and complete one fixed three-step propagated audit.
Return only JSON with exactly these top-level fields:

```json
{
  "portfolios": [
    {"name": "first-unique-name", "cases": []},
    {"name": "second-unique-name", "cases": []}
  ],
  "audit": []
}
```

Each portfolio has exactly four cases. A case has exactly these fields:

```json
{
  "id": "case-id",
  "duration": 3,
  "environment": "lint",
  "priority": 2,
  "requires": [],
  "files": [["src/a.py", "h1"]],
  "cache": [["lookup_path", "saved_path", "content_hash", "cfg", "profile", "opts", 3, 3, 2, false]],
  "scans": [["src/a.py", true, 2]],
  "params": {
    "current_day": 10,
    "expiry_days": 5,
    "config_hash": "cfg",
    "profile_hash": "profile",
    "options_key": "opts",
    "force": false,
    "warm": false,
    "atomic": false,
    "capacity": 5
  },
  "outcome": {}
}
```

`files` rows are `[path, content_hash]` in visit order. `cache` rows are
`[lookup_path, saved_path, content_hash, config_hash, profile_hash, options_key, stored_day,
last_used_day, issue_count, corrupted]`. `scans` rows are `[current_path, ok, issue_count]`.

Case IDs are unique within a portfolio. `requires` may only name earlier cases. Each case has 3
through 5 unique current files, 3 through 7 unique initial cache lookup paths, and exactly one scan
for each current file. Duration is 1 through 8, priority is -10 through 10, and capacity is at least
the current-file count and at most 10. Days, capacities, and issue counts are non-negative integers.
All paths, hashes, names, and environments are non-empty strings.

For every case and audit step, evaluate this deterministic batch cache engine:

1. Visit current files in order and find an initial entry by lookup path. The first applicable miss
   reason is `force_rescan`, `not_cached`, `corrupted`, `file_changed` (saved-path mismatch or
   content mismatch), `config_changed`, `profile_changed`, `options_changed`, then `expired`.
2. A zero-day lifetime always expires. Otherwise expiration requires
   `current_day - stored_day > expiry_days`; equality is a hit.
3. A hit keeps its saved issue count and stages `last_used_day=current_day`. A successful miss
   stages a clean entry from the current file, current settings, current day, and scanned issue
   count. A failed miss stages nothing and keeps any old entry.
4. With `atomic=true`, any failed miss rolls back all staged writes and hit updates, so the final
   cache equals the pre-run cache and `committed=false`. Otherwise staged work commits and
   `committed=true`.
5. After commit, protect every current lookup path that exists in the final cache. If capacity is
   exceeded, evict unprotected entries by ascending `(last_used_day, lookup_path)`. Rollback never
   evicts.
6. Unless `warm=true`, report positive issue counts from hits and successful misses. Failed scans
   never report. Warm mode changes reporting only.

Every `outcome` has exactly these fields:

```json
{
  "committed": true,
  "decisions": [["src/a.py", "hit"], ["src/b.py", "corrupted"]],
  "writes": [["src/a.py", "h1", 10]],
  "kept": ["legacy/x.py"],
  "evicted": ["legacy/y.py"],
  "counts": {"hits": 1, "misses": 1, "reasons": [["corrupted", 1]]},
  "failed": ["src/b.py"],
  "reported": [["src/a.py", 2]]
}
```

`decisions` contains one row per current file: `[path, "hit"]` for a hit, otherwise
`[path, miss_reason]`. `writes` contains every final-cache entry that differs byte-for-byte from
the pre-run cache as `[lookup_path, content_hash, last_used_day]`. `kept` contains every final-cache
lookup path whose entry is byte-for-byte unchanged. `evicted` contains pre-run lookup paths absent
after the run. Together, `writes` and `kept` describe every final entry. `counts.reasons` contains
each positive miss-reason count. `failed` contains failed miss paths. `reported` contains positive
`[path, issue_count]` rows; its sum is the reported total.

Order is semantic only for `files`. The grader canonicalizes portfolio order, cache rows, scan rows,
requires, audit rows, and all outcome rows before scoring. Do not spend effort sorting set-like
collections. Duplicate or unknown IDs, missing or extra fields, invalid JSON, and budget overflow
remain structural errors. A structurally valid case that misses a semantic interaction remains
scoreable and simply supplies no evidence for affected behavior or certificate checks.

Every case should have at least one miss. In each portfolio, include at least one hit, one eviction,
one atomic failed scan, and one non-atomic failed scan. The union of its cases should contain at
least nine distinct public outcome atoms:

`hit`, `reason:force_rescan`, `reason:not_cached`, `reason:corrupted`, `reason:file_changed`,
`reason:config_changed`, `reason:profile_changed`, `reason:options_changed`, `reason:expired`,
`scan_failed`, `commit`, `rollback`, `write`, `preserve`, `remove`, `evict`, `reported`, `silent`.

The fixed audit and the model-authored portfolios are independent evidence sources. A certificate
facet is complete only when the fixed audit is correct for that rule family and the portfolios
provide non-degenerate held-out evidence for it. Metrics requires both counters and reporting.
End-to-end requires decisions, state, transaction, eviction, and metrics.

The fixed audit input appended below is not a third portfolio. Return exactly three audit rows,
each `{"step": step_id, "outcome": {...}}`. Step 2 inherits the complete final cache from step 1,
and step 3 inherits the complete final cache from step 2. Do not reset between steps. Do not return
the fixed input or a full final cache.

Return only the JSON object. Do not explain and do not use Markdown.

Fixed audit input:

```json
{
  "initial_cache": [
    [
      "legacy/x.py",
      "legacy/x.py",
      "x-v1",
      "cfg-old",
      "profile-old",
      "opts-old",
      1,
      1,
      0,
      false
    ],
    [
      "legacy/y.py",
      "legacy/y.py",
      "y-v1",
      "cfg-old",
      "profile-old",
      "opts-old",
      2,
      2,
      0,
      false
    ],
    [
      "src/a.py",
      "src/a.py",
      "a-v1",
      "cfg-live",
      "profile-live",
      "opts-live",
      5,
      4,
      2,
      false
    ],
    [
      "src/b.py",
      "src/b.py",
      "b-v0",
      "cfg-old",
      "profile-live",
      "opts-live",
      7,
      3,
      1,
      true
    ],
    [
      "src/c.py",
      "archive/c.py",
      "c-v1",
      "cfg-live",
      "profile-live",
      "opts-live",
      8,
      0,
      7,
      false
    ]
  ],
  "steps": [
    {
      "id": "transaction_split",
      "files": [
        [
          "src/a.py",
          "a-v1"
        ],
        [
          "src/b.py",
          "b-v1"
        ],
        [
          "src/c.py",
          "c-v1"
        ],
        [
          "src/d.py",
          "d-v1"
        ]
      ],
      "scans": [
        [
          "src/a.py",
          true,
          2
        ],
        [
          "src/b.py",
          false,
          0
        ],
        [
          "src/c.py",
          true,
          6
        ],
        [
          "src/d.py",
          true,
          4
        ]
      ],
      "params": {
        "current_day": 10,
        "expiry_days": 5,
        "config_hash": "cfg-live",
        "profile_hash": "profile-live",
        "options_key": "opts-live",
        "force": false,
        "warm": false,
        "atomic": true,
        "capacity": 6
      }
    },
    {
      "id": "branch_followup",
      "files": [
        [
          "src/b.py",
          "b-v1"
        ],
        [
          "src/d.py",
          "d-v1"
        ],
        [
          "src/e.py",
          "e-v1"
        ]
      ],
      "scans": [
        [
          "src/b.py",
          true,
          5
        ],
        [
          "src/d.py",
          true,
          9
        ],
        [
          "src/e.py",
          true,
          3
        ]
      ],
      "params": {
        "current_day": 11,
        "expiry_days": 5,
        "config_hash": "cfg-live",
        "profile_hash": "profile-live",
        "options_key": "opts-live",
        "force": false,
        "warm": false,
        "atomic": false,
        "capacity": 6
      }
    },
    {
      "id": "eviction_tail",
      "files": [
        [
          "src/d.py",
          "d-v1"
        ],
        [
          "src/e.py",
          "e-v1"
        ],
        [
          "src/f.py",
          "f-v1"
        ]
      ],
      "scans": [
        [
          "src/d.py",
          true,
          4
        ],
        [
          "src/e.py",
          true,
          3
        ],
        [
          "src/f.py",
          true,
          8
        ]
      ],
      "params": {
        "current_day": 12,
        "expiry_days": 5,
        "config_hash": "cfg-live",
        "profile_hash": "profile-live",
        "options_key": "opts-live",
        "force": false,
        "warm": true,
        "atomic": false,
        "capacity": 5
      }
    }
  ]
}
```
