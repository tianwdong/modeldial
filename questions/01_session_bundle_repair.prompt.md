Do not use external tools.
Do not run code.

You are designing a compact black-box regression suite for a session-bundle system. The grader
compares one reference implementation with 20 independently scored behaviors. A behavior may be
represented by several plausible deviations; it earns one point only when your checks distinguish
every deviation assigned to it.

Create 1 through 3 named tests. Each test contains 1 through 8 independent steps. Every step also
contains 1 through 12 checks. The grader first runs a step on the reference implementation. The step
is eligible only when every submitted check matches that observation. It then evaluates the same
eligible step against the behavioral deviations. Only differences on checked fields count. The
maximum score is 20.

Return only one JSON object. Do not include an explanation.

Top-level schema

    {
      "tests": [
        {
          "name": "unique non-empty name",
          "steps": [ ... ]
        }
      ]
    }

Unknown fields, duplicate names, duplicate list choices, invalid types, or out-of-range values make
the whole answer invalid.

Save steps

A save step must contain `"op": "save"` and `target`. All other fields are optional and use the
defaults shown here.

    {
      "op": "save",
      "target": "missing",
      "overwrite": false,
      "race_create": false,
      "metadata_features": [],
      "event_features": [],
      "event_count": 1,
      "faults": [],
      "mapping_order": "ab",
      "clock": 19800101,
      "directory_fsync": "ok",
      "checks": [
        {"path": "status", "equals": "ok"}
      ]
    }

- `target` is `missing` or `existing`. An existing target contains bytes named `old`.
- `metadata_features` may contain `mutates_during_iteration` and `nested_mapping`.
- `event_features` may contain `mutates_after_yield`.
- `event_count` is an integer from 0 through 1001. At most 1000 events are accepted.
- `faults` may contain any of `validation`, `iteration`, `serialization`, `member_size`, and
  `replace`. Each listed fault is observed as an independent failing save using the same initial
  target and overwrite setting. Cleanup behavior depends on the failing phase and surrounding input;
  repeating one default fault matrix for only an existing and a missing target does not exercise
  every path.
- `mapping_order` is `ab` or `ba` for two logically equivalent mappings.
- `clock` is an integer from 0 through 99999999 representing the wall-clock date visible to the
  implementation.
- `directory_fsync` is `ok` or `unsupported`.

Checks use dot-separated object paths. A path can select a complete list or object, but does not use
array indexes. Check values must be exact JSON values. Duplicate check paths are invalid.

Correct save contract

1. When a target exists and overwrite is false, reject before consuming events. With overwrite true,
   every failure before commit preserves the old target. A missing target remains missing. No failed
   save leaves a temporary file.
2. Snapshot metadata before consuming events, snapshot every event when it is yielded, and recursively
   normalize nested mapping values. Once 1000 events are known, do not consume another one.
3. When overwrite is false, a target created by another writer immediately before commit wins; do not
   replace it.
4. Equivalent mappings have canonical `ab` encoding. Archive members are ordered metadata.json then
   events.jsonl and use timestamp 1980-01-01 regardless of `clock`.
5. Fsync the complete temporary archive before commit. After a successful commit, attempt to fsync the
   parent directory. An unsupported parent-directory fsync does not turn the commit into failure.

Save observations

- Rejecting an existing target reports `status: "FileExistsError"`, `events_consumed: 0`, the
  unchanged `target: "old"`, and `temporary_exists: false`.
- A normal successful save reports `status: "ok"`, consumed count, `target: "archive"`, and
  `temporary_exists: false`. Its snapshot fields are exact: `metadata_snapshot` is `before` when
  `mutates_during_iteration` is enabled and otherwise `stable`; `event_snapshot` is `before` when
  `mutates_after_yield` is enabled and otherwise `stable`; `nested_snapshot` is `normalized` when
  `nested_mapping` is enabled and otherwise `absent`.
- An event-limit rejection reports `status: "event_limit_error"` and `events_consumed: 1000`.
- If the initial existing-target rejection and event-limit checks pass and faults are requested,
  `status` is `fault_matrix`. Each fault is available below `faults.<fault>` with `status`, `target`,
  and `temporary_exists`. The correct result preserves `old` or `missing` and leaves no temporary
  file. An existing target with `overwrite: false` still returns the earlier `FileExistsError`
  observation instead of a fault matrix.
- A losing no-overwrite race reports `status: "FileExistsError"` and `target: "rival"`.
- A built archive exposes `archive.mapping_order`, `archive.member_order`, and `archive.timestamp`.
  The correct values are `"ab"`, `["metadata.json","events.jsonl"]`, and `19800101`.
- Durability fields are `durability.temporary_fsync`, `durability.parent_fsync_attempted`, and
  `durability.parent_fsync_error_ignored`.

Replay steps

A replay step must contain exactly these five fields:

    {
      "op": "replay",
      "recorded_success": [true, true],
      "actual_results": ["failure", "success"],
      "stop_on_error": true,
      "store_history": false,
      "checks": [
        {"path": "outcomes", "equals": [{"seq": 1, "success": false}]}
      ]
    }

- Both arrays must have the same length from 1 through 4.
- Every actual result is `success`, `failure`, or `raise`.
- The shell starts with execution_count 40 and each call increments it before returning or raising.

Correct replay contract

1. Report the shell's actual result, not the success value stored in the bundle.
2. Stop after an actual failure only when stop_on_error is true. With false, continue.
3. Forward store_history to every shell call.
4. When store_history is false, restore execution_count to 40 after every call and after exceptions.
   Covering replay thoroughly requires both directions of disagreement between recorded and actual
   success, both stop_on_error values, both store_history values, and an exception path.

Replay observations are `status` (`ok` or `RuntimeError`), `outcomes` (a list of `{seq, success}`),
`store_history_calls`, `call_start_counts`, and `final_execution_count`. `call_start_counts` records
the execution count immediately before each shell call: the first value is 40; later values are
41, 42, ... when history is stored, and return to 40 before every call when history is not stored.

The 20 independently scored behaviors cover rejection priority, snapshot timing, recursive mapping,
event limits, five failure paths, commit races, deterministic archives, durability, actual replay
results, stopping, history forwarding, and history restoration. Exercise meaningful combinations:
one value per field may not cover every deviation within a behavior. A compact set of interacting
steps is stronger than many redundant happy paths.

Partial example

    {
      "tests": [
        {
          "name": "existing_target",
          "steps": [
            {
              "op": "save",
              "target": "existing",
              "overwrite": false,
              "event_count": 2,
              "checks": [
                {"path": "status", "equals": "FileExistsError"},
                {"path": "events_consumed", "equals": 0}
              ]
            }
          ]
        }
      ]
    }
