Do not use external tools.
Do not run code.

You are constructing counterexamples for a retry planner named `plan_retries`.

You do not need to implement the planner or calculate its output. Return only compact JSON inputs.

Each counterexample has this shape:

{
  "name": "short_snake_case_name",
  "records": [],
  "params": {}
}

Each record has exactly these fields:

{
  "job_id": "job-a",
  "group": "tenant-a",
  "revision": 1,
  "status": "failed",
  "attempt": 0,
  "ready_at": null,
  "priority": 0
}

Field rules:

- `job_id` and `group` are non-empty strings.
- `revision` and `attempt` are non-negative integers. `priority` is an integer and may be negative.
- `status` is one of `failed`, `timeout`, `succeeded`, or `cancelled`.
- `ready_at` is either an integer timestamp or `null`.
- Multiple physical records may have the same `job_id`.

`params` has exactly these fields:

{
  "now": 100,
  "max_attempts": 3,
  "global_limit": 3,
  "per_group_limit": 1
}

`max_attempts`, `global_limit`, and `per_group_limit` are non-negative integers. A zero capacity
limit selects no jobs.

The correct planner behaves as follows:

1. For each `job_id`, keep the record with the greatest `revision`. If the greatest revision occurs
   more than once, the physically last record with that revision wins.
2. Only a latest record whose status is `failed` or `timeout` is retryable. A newer `succeeded` or
   `cancelled` record suppresses every older failure for that job; an older terminal record does not
   suppress a newer failure.
3. A retryable `failed` or `timeout` record is exhausted when `attempt >= max_attempts`.
4. A non-exhausted record is ready now when `ready_at` is `null` or `ready_at <= now`. This includes
   equality. An integer `ready_at > now` is future work; timestamp zero is not the same as `null`.
5. Ready records are ordered by higher numeric `priority`, then higher `revision`, then
   lexicographically smaller `job_id`.
6. Walk that order and select at most `global_limit` jobs, with at most `per_group_limit` selected
   jobs from each group. If one group is full, skip that job and keep walking so other groups can
   fill the remaining global slots.
7. The planner returns three values:
   - `selected`: selected job IDs in selection order;
   - `deferred`: every retryable latest job not selected, sorted by `job_id`;
   - `next_ready_at`: the smallest future `ready_at` among non-exhausted retryable latest records,
     or `null` when there is no such future record.

The grader checks 20 independent planner failure modes covering revision selection and field lineage,
terminal-state suppression, retry boundaries, negative priorities, all three ordering keys, the
interaction between readiness and capacity, deferred membership, and next-wakeup data sources. A
failure mode earns one point when at least one counterexample makes it diverge from the reference
planner.

Goal:

Create up to 3 compact counterexamples that score as many independent failure modes as possible. Each
counterexample may contain at most 16 records. Prefer cases where several rules interact. Do not
make one separate case for every rule.

Return only valid JSON in exactly this shape:

{
  "counterexamples": [
    {
      "name": "example_case",
      "records": [],
      "params": {
        "now": 0,
        "max_attempts": 0,
        "global_limit": 0,
        "per_group_limit": 0
      }
    }
  ]
}

Do not explain.
Do not use Markdown.
