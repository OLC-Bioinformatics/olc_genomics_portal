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

For the primer, probe, **and gene-target** regression, use
`--scenario pk473_gene` instead. It uses only the BDS-Listeria 1.0.0
inclusivity archive (approximately 196 MB compressed) and the Listeria
monocytogenes EGD-e hly coding sequence (NCBI Gene 987033,
NC_003210.1:205819–207408). It verifies that both Batch tasks complete, a
normalized HLY hit and workbook match values exist, and gene-category JSON,
TSV and workbook audit outputs agree with the stored results. It compares all
non-gene workbook cells for BDS-Listeria against the original pk473 reference, along with
that panel's summary and totals. Since the input panels differ, the original
full-report fingerprint and combined totals do not apply. Gene-dependent
cells are checked structurally, not against a reviewed golden result.

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
its output container are deleted after a terminal result only when all tracked
Batch jobs have finished and their resources were cleaned up. Add `--keep` to
retain them for diagnosis. Runs are retained when work may still be active or
resource cleanup has not been confirmed.

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
