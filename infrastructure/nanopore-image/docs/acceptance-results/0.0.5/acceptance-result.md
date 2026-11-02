## Image 0.0.5 GPU Scheduler Acceptance

Test completed: 2026-09-08T19:54:16+00:00

### Configuration

- GPU: `NVIDIA A10-12Q`
- GPU memory: `12288 MiB`
- NVIDIA driver: `570.237`
- Dorado: `2.1.2+8b8fc5d`
- Model: `dna_r10.4.1_e8.2_400bps_fast@v5.2.0`
- Barcode kit: `SQK-RBK114-24`
- PoreSippR commit: `2108b9428c51f2335ed4cd3f0e1c417db3ba0563`
- Targets SHA-256: `6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9`
- Target records: `6663`

- Diagnostic scheduler override: `false`

### Scheduler results

- First exit status: `0`
- Second exit status: `0`
- Final status: `completed`
- Final reason: `once-complete`
- Processed POD5 files: `1`
- Batches: `1`
- Duplicate processing prevented: `true`
- Retained FASTQ files: `4`
- Mapping result CSV files: `4`
- Mapping BAM files: `4`
- Mapping rows: `1`
- Total mapped reads: `1`


- Expected mapping: `barcode22 gntK=1`
- Observed expected-target reads: `1`

### Basecalling evidence

- Reads summarized: `80356`
- Classified reads: `5`
- Unclassified reads: `80351`
- Basecalls BAM bytes: `276306883`
- Elapsed seconds: `125`

### Acceptance

Image 0.0.5 passed standalone scheduler acceptance, including GPU basecalling, retained FASTQ fragments, cumulative target mapping, durable state/status files, and duplicate-processing prevention on a second run.
