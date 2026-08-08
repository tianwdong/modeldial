Do not use external tools.
Do not run code.

You are designing compact regression scenarios for a function named `replay_frames`.
You do not need to write implementation code or expected outputs. Return only JSON inputs.

The function accepts transaction frames. Each frame has:

- `id`: a non-empty string;
- `after`: a list of frame IDs that must commit first;
- `ops`: an ordered list of operations;
- optional `write_retries`: an integer from 0 through 3, defaulting to 0.

Operations are:

- `{"op":"put","key":"k","value":...}`;
- `{"op":"delete","key":"k"}`;
- `{"op":"check","key":"k","if_version":0}`.

`put` and `delete` may also carry `if_version`. Every key begins at version 0.

Known behavior:

- Repeated occurrences of an ID are one logical frame only when dependency sets, ordered operations,
  and retry budgets agree. Conflicting duplicates are rejected and poison their dependants.
- Missing dependencies, actual dependency-cycle members, and frames blocked by rejected dependencies
  have distinct outcomes. Only actual cycle members are classified as cycles.
- Execution uses waves. Every ready frame prepares independently from the same pre-wave snapshot.
  Preparation is atomic, while operations within one frame see that frame's earlier staged changes.
- Prepared frames commit in ascending ID order. Earlier writes in a wave can create write or read
  conflicts for later frames; check-only frames claim no keys. Write conflicts take precedence when
  both conflict kinds apply.
- `write_retries` defers that many write conflicts to later waves. A deferred frame prepares again
  from the new snapshot. Failed preparation and read conflicts are never retried.
- Successful deletes increment and preserve a tombstone version even when the key was absent. Outputs
  and execution order are deterministic and independent of input frame order.

Recent regressions involved duplicate identity, structural rejection propagation, wave barriers and
snapshots, atomic preparation, conflict classification, retry replay, per-wave claims, tombstone
versions, and ordering. The grader checks 20 independent failure modes in those areas.

Create 1 through 3 test cases that are likely to distinguish a correct implementation from several
failure modes. Prefer cases that combine interacting behaviors. Each case may contain
at most 8 frame occurrences, each frame at most 4 operations, and each dependency list at most 4 IDs.

Repeated IDs still count as separate frame occurrences toward the eight-frame limit. A strong partial
suite is better than exhaustive analysis that misses the JSON response. Do not derive or include
expected outputs.

The grader compares the reference implementation with each failure mode on your inputs. Your score is
the number of failure modes that produce a different result.

Return only valid JSON in this shape:

{
  "tests": [
    {
      "name": "short_unique_name",
      "frames": []
    }
  ]
}
