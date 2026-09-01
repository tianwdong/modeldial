Do not use external tools.
Do not run code.

A CI planner upgrade has produced inconsistent audit cards for the same repository. Construct a
small reproduction bundle for the planner team, then attach a compact certificate for the audit card
that the correct planner should produce.

Each scenario has exactly these fields:

{
  "name": "short_snake_case_name",
  "modules": [],
  "changes": {},
  "jobs": [],
  "plans": {},
  "setup_costs": {},
  "workers": 2,
  "certificate": {
    "dirty": [],
    "plans": {},
    "ranking": []
  }
}

A module entry is:

{
  "id": "core",
  "deps": [],
  "propagates": true,
  "weight": 5
}

A job entry is:

{
  "id": "unit",
  "duration": 2,
  "environment": "linux",
  "priority": 5,
  "covers": ["core"],
  "requires": []
}

`plans` maps each non-empty plan name to its selected job IDs. Modules are listed after all their
dependencies, and jobs are listed after all their requirements.

The planner processes modules in the given order. A direct private or public change rebuilds that
module. A public change also changes its exported fingerprint; a private change does not. A rebuilt
module reached through a changed dependency changes its own exported fingerprint only when its
`propagates` flag is true. Any changed dependency is sufficient to rebuild a dependant.

Coverage is the sum of distinct dirty modules covered by selected jobs. To obtain the fallback value,
independently remove each selected job before it starts, repeatedly remove jobs with missing
requirements, and keep the weakest remaining coverage. The critical job is the smallest job ID among
failures producing that weakest value.

Cost is selected job duration plus setup once for each selected environment. Scheduling has a worker
limit and an exclusive lock per environment. At each timestamp every completion is processed before
new work; ready jobs are considered by descending priority and then ascending job ID. A locked job is
skipped so later ready work can backfill, and a requirement must finish before its dependant is ready.
Setup consumes cost but no elapsed time.

Plans are ordered by stronger fallback, stronger ordinary coverage, shorter makespan, lower cost,
fewer jobs, and finally the smaller sorted selected-job list.

For every plan, `certificate.plans` has exactly this summary:

{
  "normal": 0,
  "fallback": 0,
  "critical": "selected_job_id",
  "cost": 0,
  "makespan": 0,
  "failures": {"selected_job_id": 0},
  "dispatch": []
}

`certificate.dirty` lists rebuilt modules in module order. `certificate.plans` contains every plan
and no others. For each plan, `failures` maps every selected job to the remaining coverage after that
job fails and dependent jobs are repeatedly removed. `dispatch` is that plan's complete ordered list
of `{"time": 0, "jobs": ["job_id"]}` events. `certificate.ranking` contains every plan from best to
worst.

The useful reproductions are the ones where several facts interact or choices look equivalent at
first glance. Keep the scenarios compact and make the certificate agree with the scenario rather than
adding isolated examples.

Validity limits

- Return exactly 2 scenarios with unique names. They should expose different interactions rather than
  restating the same reproduction with renamed IDs.
- Each scenario contains 1 through 8 modules, 2 through 6 jobs, and 1 through 5 plans.
- IDs and names are non-empty strings. Module dependencies refer only to earlier modules. Job
  requirements refer only to earlier jobs.
- `changes` is non-empty and maps existing module IDs to exactly `private` or `public`.
- Module weights are integers from 1 through 20. Job durations are integers from 1 through 8.
  Priorities are integers from -10 through 10. Booleans do not count as integers.
- Every job covers only existing modules. Every plan selects unique existing jobs and includes every
  requirement of every selected job.
- `setup_costs` has exactly one integer entry from 0 through 8 for every environment used by the
  jobs. `workers` is 1, 2, or 3.

Return only valid JSON with one top-level key named `scenarios`. Do not explain. Do not use Markdown.
