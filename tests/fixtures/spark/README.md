# Spark event log fixture

`real_event_log.jsonl` was captured from an **actual Spark 3.5.1 run** of the
demo (`make run-spark-demo`), not hand-written. That is what makes the parser
tests meaningful: they assert against the schema Spark really emits, including
the capitalised, space-separated key names that differ from the ones the
monitoring documentation describes for the REST API.

It preserves the real run in full where it matters:

- **all 408 `SparkListenerTaskEnd` events**, so the genuine partition
  distribution survives — 200 shuffle partitions, 193 of them empty, and one
  partition holding 144,090 of 200,000 records;
- all 6 `SparkListenerStageCompleted` events and the application start/end events.

To keep the file to a sensible size, each event was reduced to the fields the
parser actually reads. Dropped: `Accumulables`, `RDD Info`, `Stage Infos`,
`Properties`, `Task Executor Metrics`, and the shuffle/task metrics RepoLab does
not use. **Every value that remains is byte-identical to what Spark wrote.**

Regenerate it by running `make run-spark-demo` and slimming the newest log under
`.repolab/runs/`.
