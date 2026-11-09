# PrimerVerifier query selection

PrimerVerifier requests can combine the existing benchmark panels with an
optional genus or a list of individual SeqIDs in each panel. At least one
selection is required. A benchmark, genus, or SeqID cannot be assigned to both
the inclusivity and exclusivity panels, and exclusivity-only benchmarks are
not accepted as inclusivity data.

Genus and SeqID choices come from the portal metadata database. Selected
sequence FASTA files are copied from the `processed-data` storage container
into the request's panel directory. Benchmark datasets are selected from the
shared `benchmark-datasets` container; if versioned archives are available,
the highest numeric version is used and recorded with the request.

This feature uses the existing request-table columns and requires no database
migration. Panel selections retain the existing 24-character storage limit;
the longer NCBI reference exclusivity identifier is stored using a short alias
and expanded when the request runs.

PrimerVerifier also supports minimum and maximum amplicon sizes, a maximum
mismatch count, optional multi-contig searches, and a range buffer. These
controls are validated by the request form before the job is submitted.
