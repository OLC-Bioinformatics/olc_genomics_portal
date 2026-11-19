# COWBAT integration regression

The `000000-aaa` scenario is an opt-in end-to-end check of the COWBAT
workflow. Its input FASTQ identities, expected summary metadata, required
reports, and output checksums are recorded in
`olc_webportalv2/cowbat/integration_data/000000-aaa.json`. Stable
classification/report outputs are compared exactly. Date fields and
nondeterministic contig labels are excluded from semantic report comparisons;
XLSX files are compared by cell values rather than ZIP bytes. Assembly metrics
use bounded tolerances because repeated SKESA runs produced small variations:
summary contigs (±1), total length (±700 bp), and average coverage (±0.002);
combined-metadata bounds are defined per metric in the scenario. The selected
assembly allows contig count (±1), total length (±100 bp), and N50 (±1).

To inspect the scenario without touching the database, storage, or Batch
service:

```bash
python manage.py run_cowbat_integration --scenario 000000-aaa
```

For a real Batch run, use a VM with a working Celery worker/beat, Storage
service, and Batch service. The command verifies the FASTQ SHA-256 checksums
and sizes,
refuses to replace an active database or Batch run, deletes the old
`SequencingRun` record, and removes every object in `000000-aaa` except its
`.fastq.gz` inputs before submitting the workflow.

Every live run requires an explicit environment opt-in. If the VM labels the
environment `PROD`, an additional confirmation is required:

```bash
COWBAT_INTEGRATION_ENABLED=true \
  python manage.py run_cowbat_integration \
    --scenario 000000-aaa --run-batch --allow-production
```

By default, once the run reaches a terminal state, the command removes the
generated objects while preserving the FASTQ inputs, deletes its database
record, and removes its isolated report archive container. The integration
marker suppresses production completion emails and routes the archive away
from the shared `reports-and-assemblies` container. Add `--keep` to retain the
run, generated outputs, and isolated archive for diagnosis. Failed terminal
runs and timed-out runs are retained so their outputs can be inspected; timed
out runs may still have active Batch work.

The test waits up to two hours by default; `--timeout` and `--poll-interval`
can adjust that. A comparison failure prints the mismatching report or
checksum. Use `--keep` to inspect failed outputs before deliberately updating
the expected values in the scenario. Do not refresh the reference until the
workflow change and output differences have been reviewed.
