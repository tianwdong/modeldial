Do not use external tools.
Do not run code.

You are designing regression tests for a function named `run_scan`.
The function processes files with an incremental analyzer cache. You do not need to write code or
expected outputs. Return only JSON test inputs.

Each test case has exactly this shape:

{
  "name": "short_snake_case_name",
  "files": [],
  "cache": {},
  "params": {}
}

`files` is a list of objects with:

- `path`: string;
- `content_hash`: string;
- `issue_count`: non-negative integer.

`cache` maps a path to an entry with:

- string fields `path`, `content_hash`, `config_hash`, `profile_name`, `profile_hash`, `options_key`;
- integer fields `stored_day`, `issue_count`, where `issue_count` is non-negative;
- boolean field `corrupted`.

`params` contains non-negative integers `current_day` and `cache_expiry_days`; strings `config_hash`,
`profile_name`, `profile_hash`, and `options_key`; and booleans `force_rescan` and `warm_cache`.

Bug report

Recent cache regressions involved cache identity, invalidation priority, forced scans, warm-cache
mode, expiration boundaries, issue reporting, and preserving cache state.

Known behavior

- A cache hit requires the cached entry to describe the same current file and the same current scan
  settings.
- Corrupted cache entries are not usable.
- Forced scans bypass cache hits but still refresh the cache.
- Warm-cache scans refresh the cache but do not report issues.
- Cache misses report current file issue counts. Cache hits report cached issue counts.
- Missed files and reported issues are sorted by path.
- Cache entries for files outside the current run are preserved.
- Expiration uses `current_day - stored_day`. Equality at a nonzero lifetime and a zero-day lifetime
  are distinct boundary cases worth exercising.

The grader checks 20 independent cache regression failure modes in those areas.

Create 1 through 3 compact tests that score as many independent failure modes as possible. Prefer tests
where several invalidation reasons and state transitions interact. Do not make one test per rule.

Limits:

- at most 8 files and at most 10 cache entries per test;
- unique test names;
- all required fields and types above must be present;
- at most 3 tests total.

A valid partial suite is better than exhaustive analysis or a timeout. Perform one schema review,
then return immediately. Do not calculate or include expected outputs.

Return only valid JSON in exactly this shape:

{
  "tests": [
    {
      "name": "example_case",
      "files": [],
      "cache": {},
      "params": {
        "current_day": 0,
        "cache_expiry_days": 0,
        "config_hash": "",
        "profile_name": "",
        "profile_hash": "",
        "options_key": "",
        "force_rescan": false,
        "warm_cache": false
      }
    }
  ]
}

Do not explain.
Do not use Markdown.
