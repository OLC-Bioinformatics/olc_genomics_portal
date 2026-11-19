# Primer Validator integration regression

The pk473 scenario is an opt-in end-to-end regression for the Primer
Validator workflow. Its input, benchmark identity checks, model expectations,
and workbook comparison keys are in
`olc_webportalv2/primer_finder/integration_data/pk473.json`. The adjacent
summary, totals, and workbook files are the reviewed pk473 references.

The regular Django test suite validates the scenario and workbook comparator
without submitting jobs. To inspect the scenario without touching the
database, storage, or Batch service, run:

```bash
python manage.py run_primer_validator_integration --scenario pk473
```

For an actual comparison, use a VM with a working Celery worker/beat, Storage
service, and Batch service. Every live run requires an explicit environment
opt-in and command-line flag:

```bash
PRIMER_VALIDATOR_INTEGRATION_ENABLED=true \
  python manage.py run_primer_validator_integration \
    --scenario pk473 --run-batch
```

If the VM labels the environment `PROD`, an additional explicit confirmation
is required. Only use this when the VM is intentionally authorized to submit
integration jobs to that Batch service:

```bash
PRIMER_VALIDATOR_INTEGRATION_ENABLED=true \
  python manage.py run_primer_validator_integration \
    --scenario pk473 --run-batch --allow-production --keep
```

The run can take up to two hours by default. Override the wait and polling
interval with `--timeout` and `--poll-interval`. By default, the request and
its output container are deleted after any terminal result, whether comparison
passes or fails. Add `--keep` to retain them for diagnosis. Timed-out runs are
retained because Batch work may still be active.

## Updating the workflow or reference

Edit the scenario JSON to change the request inputs or add another case. The
workbook comparator checks all cell values, using configured row keys for the
large result sheets so row ordering alone does not fail the comparison.
Expected benchmark ETags deliberately make the test stop if an input archive
changes.

When an intentional workflow or reference-data change changes the output, use
`--keep`, review the model results and generated workbook, then update the
scenario's expected values and reference artifacts deliberately. Do not
replace the gold files just to make a failing test pass; first establish that
the output change is expected.
