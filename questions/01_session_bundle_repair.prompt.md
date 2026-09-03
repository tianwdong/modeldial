Without tools or code, repair a fixed 18-relation black-box regression suite for a session-bundle
system. Some selected relation oracles are false or add no defect coverage. Replace only the choices
you believe should change. Return one JSON object only; no explanation.

Answer schema

    {"replace":[["OLD_ID","NEW_ID"]]}

Each pair removes one currently selected proposal ID (left) and includes one alternative proposal ID
(right) in the final suite. Use at most six pairs. Old IDs and new IDs cannot repeat; use only the
listed banks. An empty list keeps the starting suite. Malformed or out-of-bank edits are invalid.
Scoring considers both the validity of retained relations and the distinct incorrect behaviors
detected by the final suite.
For coverage, treat each incorrect behavior as one broken contract guarantee with unrelated
guarantees still correct; a useful relation witnesses that broken guarantee itself.

Draft notation: `b` is the base input and overlaying `d` produces intervention `i`. Operators are
`=` and `!=`. Cross-execution relations compare the same observation path. Same-execution relations
may compare `faults.<fault>.filesystem` with `before.filesystem`, or
`durability.temporary_fsync` with `durability.parent_fsync_attempted`.

Draft executions

{"x":[{"n":"e1","b":{"op":"save","target":"existing"},"d":{"target":"missing"}},{"n":"e2","b":{"op":"save","target":"missing","metadata_features":[]},"d":{"metadata_features":["mutates_during_iteration","nested_mapping"]}},{"n":"e3","b":{"op":"save","target":"missing","event_features":[],"event_count":3},"d":{"event_features":["mutates_after_yield"]}},{"n":"e4","b":{"op":"save","target":"missing","event_count":1000},"d":{"event_count":1001}},{"n":"e5","b":{"op":"save","target":"existing","overwrite":true,"faults":["validation","iteration","serialization","member_size","replace"]},"d":{"faults":[]}},{"n":"e6","b":{"op":"save","target":"missing","race_create":false},"d":{"race_create":true}},{"n":"e7","b":{"op":"save","target":"missing","mapping_order":"ab"},"d":{"mapping_order":"ba"}},{"n":"e8","b":{"op":"save","target":"missing","clock":19800101},"d":{"clock":20260902}},{"n":"e9","b":{"op":"replay","recorded_success":[true],"actual_results":["failure"],"stop_on_error":true,"store_history":true},"d":{"recorded_success":[false]}},{"n":"e10","b":{"op":"replay","recorded_success":[true,true],"actual_results":["failure","success"],"stop_on_error":true,"store_history":true},"d":{"stop_on_error":false}},{"n":"e11","b":{"op":"replay","recorded_success":[true,true],"actual_results":["success","success"],"stop_on_error":false,"store_history":false},"d":{"store_history":true}}]}

Starting 18 proposals

{"proposals":[{"id":"p02","n":"e1","r":["b.status","!=","i.status"]},{"id":"p06","n":"e2","r":["b.metadata_snapshot","!=","i.metadata_snapshot"]},{"id":"p05","n":"e2","r":["b.status","=","i.status"]},{"id":"p09","n":"e3","r":["b.status","=","i.status"]},{"id":"p11","n":"e4","r":["b.events_consumed","=","i.events_consumed"]},{"id":"p14","n":"e5","r":["b.faults.validation.filesystem","=","b.before.filesystem"]},{"id":"p15","n":"e5","r":["b.faults.iteration.filesystem","=","b.before.filesystem"]},{"id":"p16","n":"e5","r":["b.faults.serialization.filesystem","=","b.before.filesystem"]},{"id":"p17","n":"e5","r":["b.faults.member_size.filesystem","=","b.before.filesystem"]},{"id":"p19","n":"e5","r":["b.status","!=","i.status"]},{"id":"p20","n":"e6","r":["b.status","!=","i.status"]},{"id":"p23","n":"e7","r":["b.archive.mapping_order","=","i.archive.mapping_order"]},{"id":"p24","n":"e7","r":["b.status","=","i.status"]},{"id":"p27","n":"e8","r":["b.archive.timestamp","=","i.archive.timestamp"]},{"id":"p29","n":"e9","r":["b.outcomes","=","i.outcomes"]},{"id":"p30","n":"e9","r":["b.final_execution_count","=","i.final_execution_count"]},{"id":"p32","n":"e10","r":["b.outcomes","!=","i.outcomes"]},{"id":"p37","n":"e11","r":["b.final_execution_count","!=","i.final_execution_count"]}]}

Alternative proposals

{"proposals":[{"id":"p01","n":"e1","r":["b.events_consumed","!=","i.events_consumed"]},{"id":"p03","n":"e1","r":["b.target","!=","i.target"]},{"id":"p04","n":"e2","r":["b.metadata_snapshot","=","i.metadata_snapshot"]},{"id":"p07","n":"e2","r":["b.nested_snapshot","=","i.nested_snapshot"]},{"id":"p08","n":"e3","r":["b.event_snapshot","=","i.event_snapshot"]},{"id":"p12","n":"e4","r":["b.status","!=","i.status"]},{"id":"p18","n":"e5","r":["b.faults.replace.filesystem","=","b.before.filesystem"]},{"id":"p22","n":"e6","r":["b.target","!=","i.target"]},{"id":"p25","n":"e7","r":["b.durability.temporary_fsync","=","b.durability.parent_fsync_attempted"]},{"id":"p33","n":"e10","r":["b.call_start_counts","!=","i.call_start_counts"]},{"id":"p35","n":"e11","r":["b.store_history_calls","!=","i.store_history_calls"]},{"id":"p36","n":"e11","r":["b.call_start_counts","!=","i.call_start_counts"]}]}

Save contract

Omitted save fields default to `overwrite:false`, `race_create:false`, empty feature/fault arrays,
`event_count:1`, `mapping_order:"ab"`, `clock:19800101`, `directory_fsync:"ok"`. An existing
target initially contains bytes `old`.

- No-overwrite to an existing target rejects before consuming events. Pre-commit failures preserve
  an old target or leave a missing target missing, with no temporary file.
- Metadata is snapshotted before consumption, each event when yielded, and nested mappings are
  recursively normalized. At most 1000 events are accepted without consuming an extra event.
- A racing writer before no-overwrite commit wins. Mapping encoding is canonical. Archive members
  are `metadata.json` then `events.jsonl`, timestamped `19800101` regardless of `clock`.
- The temporary archive is fsynced before commit. A successful commit fsyncs its parent directory
  when possible; unsupported parent fsync does not turn success into failure.

Save observations are `status`, `events_consumed`, `target`, `temporary_exists`,
`metadata_snapshot`, `event_snapshot`, `nested_snapshot`, `faults.<fault>.*`, `before.*`,
`archive.*`, `candidate_archive.*`, `durability.*`. `before.filesystem` records target identity and
temporary state before execution; each `faults.<fault>.filesystem` records them after that failure.
There is no aggregate fault observation. Stable and correct pre-mutation snapshots compare equal;
late mutated capture differs.

Replay contract

The shell starts at count 40 and increments before return or raise. Report actual, not recorded,
results. Stop after actual failure only with `stop_on_error`. Forward `store_history`; when false,
restore 40 after every call and exception. Replay observations are `status`, `outcomes`,
`store_history_calls`, `call_start_counts`, `final_execution_count`.
