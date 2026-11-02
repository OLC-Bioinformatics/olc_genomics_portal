## Image 0.0.4 GPU Scheduler Acceptance

Test completed: 2026-09-08T18:28:44+00:00

### Configuration

- GPU: `NVIDIA A10-12Q`
- GPU memory: `12288 MiB`
- NVIDIA driver: `570.237`
- Dorado: `2.1.2+8b8fc5d`
- Model: `dna_r10.4.1_e8.2_400bps_fast@v5.2.0`
- Barcode kit: `SQK-RBK114-24`
- PoreSippR commit: `691b3a3c2944139cb0093f81909331f7b8d46983`
- Targets SHA-256: `6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9`
- Target records: `6663`

- Diagnostic scheduler override: `true`

### Scheduler results

- First exit status: `0`
- Second exit status: `0`
- Final status: `completed`
- Final reason: `once-complete`
- Processed POD5 files: `1`
- Batches: `1`
- Duplicate processing prevented: `true`
- Retained FASTQ files: `2`
- Mapping result CSV files: `2`
- Mapping BAM files: `2`
- Mapping rows: `1`
- Total mapped reads: `1`

### Basecalling evidence

- Reads summarized: `80351`
- Classified reads: `2`
- Unclassified reads: `80349`
- Basecalls BAM bytes: `276252095`
- Elapsed seconds: `149`

### Acceptance

Image 0.0.4 completed a diagnostic scheduler run using an overridden scheduler file. This does not constitute acceptance of the immutable image.
