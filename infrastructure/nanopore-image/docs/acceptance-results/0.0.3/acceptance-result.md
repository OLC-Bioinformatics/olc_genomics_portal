## Image 0.0.3 GPU Acceptance

Test completed: 2026-09-03T19:43:32+00:00

### Configuration

- GPU: `NVIDIA A10-12Q`
- GPU memory: `12288 MiB`
- NVIDIA driver: `570.237`
- Dorado: `2.1.2+8b8fc5d`
- Model: `dna_r10.4.1_e8.2_400bps_fast@v5.2.0`
- Barcode kit: `SQK-RBK114-24`

### Input

- POD5 files: `1`
- POD5 bytes: `3095356360`
- Input directory: `/mnt/resource/nanopore-acceptance/input`

### Results

- Dorado exit status: `0`
- Reads summarized: `80356`
- Classified reads: `5`
- Unclassified reads: `80351`
- Classified percentage: `0.006222%`
- Output BAM bytes: `276254673`
- Demultiplexed FASTQ files: `5`
- Elapsed seconds: `118`

### Barcode distribution

```json
{
  "barcode12": 1,
  "barcode16": 2,
  "barcode20": 1,
  "barcode22": 1,
  "unknown": 80351
}
```

### Acceptance

Image `0.0.3` passed:

- NVIDIA driver loading
- CUDA device access
- Dorado startup
- pinned model loading
- recursive POD5 discovery
- GPU basecalling
- BAM creation
- summary generation
- classification-aware demultiplexing
