# FoodPort user guide

FoodPort provides authenticated access to genomic metadata, sequence data, and
analysis workflows. Available pages and actions depend on the user's account,
project permissions, and the datasets provisioned for the deployment. If a
workflow or dataset is missing, contact the portal maintainers.

## Find your way around

After signing in, use the navigation menu:

- **Explore** provides the metadata database, search, browse, forms, metadata
  upload, and FileZone.
- **Retrieve** opens the sequence-data retrieval workflow.
- **Assembly** provides run upload and assembly pages.
- **Analyze** provides gene search, tree creation, AMR investigation,
  annotation, near-neighbor search, Virus Typer, primer analysis, 16S analysis,
  and SNV location.

## Common workflow inputs and outputs

| Workflow | Inputs | Results |
|---|---|---|
| Research Assembly | A complete R1/R2 FASTQ pair. A `SampleSheet.csv` may be required or checked depending on the upload path. | Downloadable assemblies and reports; summary metadata is available when generated. |
| GeneSeekr | A query FASTA (paste it or upload a file, not both) plus the applicable sequence IDs, genus, or benchmark selection. FASTA bases must be `A`, `C`, `G`, `T`, or `N`. | A BLAST report and a sequence-by-sequence report when the request completes. |
| PrimerValidator | Named target(s), each with forward and reverse primers; optional probe, gene sequence, or target-specific multi-FASTA. Select at least one inclusivity or exclusivity panel. | A downloadable experiment report after processing completes. |
| PrimerVerifier | One or more FASTA primer pairs, with headers ending in `-F` and `-R`; choose benchmark, genus, or specific sequence identifiers for the panels. | A combined primer and GeneSeekr report after both jobs complete. |
| Nanopore streaming | Run name, barcode kit, numeric barcode selections, SEQID and OLNID for each selected barcode, and local `.pod5` files. | Incremental reports and a result-manifest link in the companion GUI. |

Only upload data you are authorized to process in the selected deployment.
Ask the portal maintainers for the approved handling and retention policy for
your data.

## Research Assembly

The Assembly area has separate paths for:

- **Assembly from Blob**, for FASTQ files that are already in the appropriate
  Blob container.
- **Upload and Assemble FASTQ**, for creating an assembly while uploading the
  files.
- **Upload and Manage FASTQ**, for browsing an existing run, adding or
  removing files, renaming the run, and submitting an assembly.

An assembly submission requires at least one complete R1/R2 FASTQ pair. If a
`SampleSheet.csv` is provided, it is checked against the uploaded samples.
Processing runs in the background; the Assembly page shows status and provides
reports and assembly downloads after completion.

## Primer analysis

Open **Analyze > Find Primers** and choose the workflow:

- **PrimerValidator** evaluates primer sets against selected panels and
  assemblies.
- **PrimerVerifier** evaluates primer sets against selected benchmark panels,
  genera, or sequence identifiers.

The request list shows processing status. Completed requests provide a report
download; failed requests can be opened to inspect available error details.
See the PrimerVerifier [query-selection notes](primer-verifier-query-selection.md)
for details on its panel selection behavior.

## GeneSeekr

Open **Analyze > Find Genes**. Enter applicable sequence IDs one per line in
the displayed `YYYY-LAB-####` format, or choose the available genus or
benchmark options. Provide exactly one query FASTA: paste it into the sequence
field or upload a FASTA file, but do not use both. Each selected sequence ID
must exist in the portal catalog, and query sequences may contain only
`A`, `C`, `G`, `T`, and `N`.

The request page provides status and, when complete, links to the BLAST and
sequence-by-sequence reports.

## Nanopore streaming

The Nanopore workflow uses the companion desktop application to pair with a
FoodPort account, create a run, select barcodes and sample identifiers, and
upload stable POD5 files as sequencing proceeds. Keep the application running
while files upload and incremental reports arrive; use **Finalize run** only
when sequencing has finished.

See the [Nanopore GUI guide](README.md) for setup and workflow details. Its
development configuration currently disables TLS certificate verification by
default; do not use that configuration for production or sensitive data.

## Common input and status problems

| What you see | What to check |
|---|---|
| A sequence ID is rejected | Enter one ID per line in the format shown by the page and confirm the ID exists in the portal catalog. |
| GeneSeekr rejects the query | Supply one FASTA input only (pasted text or uploaded file) and use only `A`, `C`, `G`, `T`, or `N` bases. |
| PrimerValidator rejects panel choices | Select at least one panel and do not select the same panel for both inclusivity and exclusivity. Confirm minimum amplicon size does not exceed maximum size. |
| Research Assembly shows **Upload Error** | Use the retry action on the run page; it retries files that did not upload successfully. |
| A request is still **Processing** | The analysis runs asynchronously. Keep its request/run name and submission time; contact the portal maintainers if it remains stuck. |
| A result download is unavailable | Downloads appear after the request reaches **Complete**. |

## Getting help

For an unsuccessful or stuck analysis, record the run or request name, the
displayed status, and the approximate submission time, then contact the portal
maintainers. Do not send passwords, tokens, SAS URLs, or other credentials in
support requests.
