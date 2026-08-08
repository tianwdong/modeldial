Do not use external tools.
Do not run code.

You are auditing a CI planner by constructing compact scenarios that expose incorrect audit
implementations. You do not need to implement the planner or calculate expected outputs. Return only
scenario inputs.

Each scenario has exactly these fields:

{
  "name": "short_snake_case_name",
  "modules": [],
  "changes": {},
  "jobs": [],
  "plans": {},
  "setup_costs": {},
  "workers": 2
}

Modules are listed in dependency order:

{
  "id": "A",
  "deps": [],
  "propagates": true,
  "weight": 5
}

Jobs are listed in requirement order:

{
  "id": "P",
  "duration": 2,
  "environment": "unit",
  "priority": 5,
  "covers": ["A"],
    "requires": []
}

`plans` maps each non-empty plan name to a list of selected job IDs.

Rules

1. A direct `private` or `public` change makes that module dirty. A direct `public` change changes
   its exported fingerprint even when `propagates` is false. A direct `private` change does not.
2. A module also becomes dirty when any dependency's exported fingerprint changes. A module rebuilt
   for that reason changes its own fingerprint only when `propagates` is true. Module and dirty-output
   order is the given dependency order, not lexicographic ID order.
3. Coverage counts only dirty modules. A covered module contributes its weight once even if several
   selected jobs cover it.
4. `normal` is coverage with all selected jobs. For `fallback`, independently fail each selected job
   before it starts, then repeatedly remove jobs whose requirements are absent. The minimum remaining
   coverage is the fallback score. `critical` is the lexicographically smallest failed job producing
   that minimum, regardless of plan-list order.
5. Cost is selected durations plus each distinct selected environment's setup cost once. Environments
   that exist in the scenario but are absent from a plan are not charged.
6. Scheduling starts at time zero. At each timestamp, process every completion, then sort ready jobs
   by descending priority and ascending job ID. Walk the ready list once. Start every job that fits
   the worker limit and environment locks. If an environment is locked, skip that job and continue
   backfilling. Requirements must finish before dependants become ready. Setup costs consume no time.
7. Rank plans by maximum fallback, maximum normal, minimum makespan, minimum cost, minimum job count,
   then the lexicographically smallest sorted selected-job list.

Validity limits

- Return 1 or 2 scenarios with unique names.
- Each scenario contains 1 through 8 modules, 2 through 8 jobs, and 1 through 5 plans.
- IDs and names are non-empty strings. Module dependencies refer only to earlier modules. Job
  requirements refer only to earlier jobs.
- `changes` is non-empty and maps existing module IDs to exactly `private` or `public`.
- Module weights are integers from 1 through 20. Job durations are integers from 1 through 8.
  Priorities are integers from -10 through 10. Booleans do not count as integers.
- Every job covers only existing modules. Every plan selects 1 through 8 unique existing jobs and
  includes every requirement of every selected job.
- `setup_costs` has exactly one non-negative integer entry, from 0 through 8, for every environment
  used by the jobs. `workers` is 1, 2, or 3.

The grader checks 20 independent CI audit failure modes covering change propagation, dirty-only
distinct coverage, recursive fallback pruning, critical-job ties, selected-environment charging,
dependency readiness, completion batching, ready-job tie order, environment backfill, ranking
precedence, and final tie-breaks. A failure mode earns one point when at least one submitted scenario
makes its complete audit output differ from the reference audit.

Goal

Create up to 2 compact scenarios that score as many independent failure modes as possible. Prefer cases
where several rules interact. Do not create one separate scenario for every rule.

Keep the scenarios compact and reserve time for one schema review. A valid partial solution is better
than exhaustive analysis that misses the final JSON response. Do not enumerate or prove coverage of
all 20 failure modes.

Return only valid JSON. This is a valid minimal shape; replace or extend its contents to exercise
interacting rules:

{
  "scenarios": [
    {
      "name": "minimal_shape",
      "modules": [
        {"id": "A", "deps": [], "propagates": true, "weight": 1}
      ],
      "changes": {"A": "private"},
      "jobs": [
        {"id": "J1", "duration": 1, "environment": "unit", "priority": 0,
         "covers": ["A"], "requires": []},
        {"id": "J2", "duration": 1, "environment": "unit", "priority": 0,
         "covers": ["A"], "requires": ["J1"]}
      ],
      "plans": {"baseline": ["J1"]},
      "setup_costs": {"unit": 0},
      "workers": 1
    }
  ]
}

Do not explain.
Do not use Markdown.
