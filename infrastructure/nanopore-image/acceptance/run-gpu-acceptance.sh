#!/usr/bin/env bash

set -euo pipefail

IMAGE_VERSION="${IMAGE_VERSION:?IMAGE_VERSION is required}"
DORADO_MODEL="${DORADO_MODEL:-dna_r10.4.1_e8.2_400bps_fast@v5.2.0}"
BARCODE_KIT="${BARCODE_KIT:-SQK-RBK114-24}"
INPUT_DIRECTORY="${INPUT_DIRECTORY:?INPUT_DIRECTORY is required}"
OUTPUT_DIRECTORY="${OUTPUT_DIRECTORY:-${AZ_BATCH_TASK_WORKING_DIR:-/mnt/resource}/nanopore-acceptance/output}"

MODEL_DIRECTORY="/opt/ont/models/${DORADO_MODEL}"

RESULT_JSON="${OUTPUT_DIRECTORY}/acceptance-result.json"
RESULT_MARKDOWN="${OUTPUT_DIRECTORY}/acceptance-result.md"
OUTPUT_BAM="${OUTPUT_DIRECTORY}/acceptance.bam"
SUMMARY_TSV="${OUTPUT_DIRECTORY}/summary.tsv"
DEMUX_DIRECTORY="${OUTPUT_DIRECTORY}/demux"
BARCODE_COUNTS_TSV="${OUTPUT_DIRECTORY}/barcode-counts.tsv"

mkdir -p "$OUTPUT_DIRECTORY"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia-smi is not installed" >&2
  exit 1
fi

if ! command -v dorado >/dev/null 2>&1; then
  echo "Dorado is not installed" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is not installed" >&2
  exit 1
fi

if [[ ! -d "$MODEL_DIRECTORY" ]]; then
  echo "Dorado model directory is missing: ${MODEL_DIRECTORY}" >&2
  exit 1
fi

if [[ ! -d "$INPUT_DIRECTORY" ]]; then
  echo "Input directory does not exist: ${INPUT_DIRECTORY}" >&2
  exit 1
fi

pod5_count="$(
  find \
    "$INPUT_DIRECTORY" \
    -type f \
    -iname '*.pod5' |
  wc -l
)"

if [[ "$pod5_count" -eq 0 ]]; then
  echo \
    "No POD5 files were found beneath ${INPUT_DIRECTORY}" \
    >&2
  exit 1
fi

pod5_bytes="$(
  find \
    "$INPUT_DIRECTORY" \
    -type f \
    -iname '*.pod5' \
    -printf '%s\n' |
  awk '{total += $1} END {print total + 0}'
)"

echo "Starting Nanopore GPU acceptance test"
echo "Image version: ${IMAGE_VERSION}"
echo "Input directory: ${INPUT_DIRECTORY}"
echo "POD5 files: ${pod5_count}"
echo "POD5 bytes: ${pod5_bytes}"
echo "Model: ${DORADO_MODEL}"
echo "Barcode kit: ${BARCODE_KIT}"

DRIVER_VERSION="$(
  nvidia-smi \
    --query-gpu=driver_version \
    --format=csv,noheader |
  head -n 1 |
  tr -d '\r'
)"

GPU_NAME="$(
  nvidia-smi \
    --query-gpu=name \
    --format=csv,noheader |
  head -n 1 |
  tr -d '\r'
)"

GPU_MEMORY_MIB="$(
  nvidia-smi \
    --query-gpu=memory.total \
    --format=csv,noheader,nounits |
  head -n 1 |
  tr -d '\r '
)"

DORADO_VERSION="$(
  dorado --version 2>&1 |
  tail -n 1
)"

STARTED_AT="$(
  date \
    --utc \
    --iso-8601=seconds
)"

start_seconds="$SECONDS"

rm -f "$OUTPUT_BAM"

echo "Running Dorado GPU basecalling"

dorado basecaller \
  "$MODEL_DIRECTORY" \
  "$INPUT_DIRECTORY" \
  --recursive \
  --device cuda:0 \
  --kit-name "$BARCODE_KIT" \
  > "$OUTPUT_BAM"

elapsed_seconds="$((SECONDS - start_seconds))"

FINISHED_AT="$(
  date \
    --utc \
    --iso-8601=seconds
)"

if [[ ! -s "$OUTPUT_BAM" ]]; then
  echo "Dorado did not produce a nonempty BAM" >&2
  exit 1
fi

echo "Generating Dorado summary"

dorado summary \
  "$OUTPUT_BAM" \
  > "$SUMMARY_TSV"

if [[ ! -s "$SUMMARY_TSV" ]]; then
  echo "Dorado summary is missing or empty" >&2
  exit 1
fi

READ_COUNT="$(
  awk \
    'END {print (NR > 0 ? NR - 1 : 0)}' \
    "$SUMMARY_TSV"
)"

BAM_BYTES="$(
  stat \
    --format='%s' \
    "$OUTPUT_BAM"
)"

echo "Calculating barcode distribution"

awk -F '\t' '
  NR == 1 {
    for (i = 1; i <= NF; i++) {
      if ($i == "barcode_arrangement") {
        barcode_column = i
      }
    }

    if (!barcode_column) {
      print "barcode_arrangement column is missing" > "/dev/stderr"
      exit 1
    }

    next
  }

  {
    barcode = $barcode_column

    if (barcode == "") {
      barcode = "unknown"
    }

    counts[barcode]++
  }

  END {
    for (barcode in counts) {
      print barcode "\t" counts[barcode]
    }
  }
' "$SUMMARY_TSV" |
LC_ALL=C sort \
  > "$BARCODE_COUNTS_TSV"

BARCODE_COUNTS_JSON="$(
  jq \
    --raw-input \
    --slurp \
    '
      split("\n")
      | map(select(length > 0))
      | map(split("\t"))
      | map({
          key: .[0],
          value: (.[1] | tonumber)
        })
      | from_entries
    ' \
    "$BARCODE_COUNTS_TSV"
)"

UNKNOWN_READS="$(
  jq -r \
    '.unknown // 0' \
    <<<"$BARCODE_COUNTS_JSON"
)"

classified_reads="$((READ_COUNT - UNKNOWN_READS))"

if [[ "$READ_COUNT" -gt 0 ]]; then
  CLASSIFIED_PERCENT="$(
    awk \
      -v classified="$classified_reads" \
      -v total="$READ_COUNT" \
      'BEGIN {printf "%.6f", (classified / total) * 100}'
  )"
else
  CLASSIFIED_PERCENT="0.000000"
fi

echo "Running classification-aware demultiplexing"

rm -rf "$DEMUX_DIRECTORY"
mkdir -p "$DEMUX_DIRECTORY"

dorado demux \
  --no-classify \
  --emit-fastq \
  --output-dir "$DEMUX_DIRECTORY" \
  "$OUTPUT_BAM"

demux_file_count="$(
  find \
    "$DEMUX_DIRECTORY" \
    -type f \
    \( -iname '*.fastq' -o -iname '*.fastq.gz' \) |
  wc -l
)"

if [[ "$demux_file_count" -eq 0 ]]; then
  echo "Dorado demultiplexing produced no FASTQ files" >&2
  exit 1
fi

jq \
  --null-input \
  --arg image_version "$IMAGE_VERSION" \
  --arg driver_version "$DRIVER_VERSION" \
  --arg gpu_name "$GPU_NAME" \
  --argjson gpu_memory_mib "$GPU_MEMORY_MIB" \
  --arg dorado_version "$DORADO_VERSION" \
  --arg dorado_model "$DORADO_MODEL" \
  --arg barcode_kit "$BARCODE_KIT" \
  --arg input_directory "$INPUT_DIRECTORY" \
  --arg started_at "$STARTED_AT" \
  --arg finished_at "$FINISHED_AT" \
  --arg classified_percent "$CLASSIFIED_PERCENT" \
  --argjson elapsed_seconds "$elapsed_seconds" \
  --argjson pod5_count "$pod5_count" \
  --argjson pod5_bytes "$pod5_bytes" \
  --argjson read_count "$READ_COUNT" \
  --argjson classified_reads "$classified_reads" \
  --argjson unknown_reads "$UNKNOWN_READS" \
  --argjson bam_bytes "$BAM_BYTES" \
  --argjson demux_file_count "$demux_file_count" \
  --argjson barcode_counts "$BARCODE_COUNTS_JSON" \
  '{
    schema_version: 1,
    accepted: true,
    image_version: $image_version,
    gpu: {
      name: $gpu_name,
      memory_mib: $gpu_memory_mib,
      driver_version: $driver_version
    },
    dorado: {
      version: $dorado_version,
      model: $dorado_model,
      barcode_kit: $barcode_kit
    },
    input: {
      directory: $input_directory,
      pod5_count: $pod5_count,
      pod5_bytes: $pod5_bytes
    },
    test: {
      started_at: $started_at,
      finished_at: $finished_at,
      elapsed_seconds: $elapsed_seconds,
      read_count: $read_count,
      classified_reads: $classified_reads,
      unknown_reads: $unknown_reads,
      classified_percent: ($classified_percent | tonumber),
      bam_bytes: $bam_bytes,
      demux_file_count: $demux_file_count,
      barcode_counts: $barcode_counts
    }
  }' \
  > "$RESULT_JSON"

echo "Validating generated acceptance result"

jq -e \
  --arg expected_image_version "$IMAGE_VERSION" \
  --arg expected_model "$DORADO_MODEL" \
  '
    .schema_version == 1
    and .accepted == true
    and .image_version == $expected_image_version
    and .dorado.model == $expected_model
    and .gpu.name != ""
    and .gpu.driver_version != ""
    and .gpu.memory_mib > 0
    and .input.pod5_count > 0
    and .input.pod5_bytes > 0
    and .test.read_count > 0
    and .test.bam_bytes > 0
    and .test.demux_file_count > 0
    and .test.elapsed_seconds > 0
    and .test.classified_reads >= 0
    and .test.unknown_reads >= 0
    and (
      .test.classified_reads + .test.unknown_reads
      == .test.read_count
    )
    and (
    ([.test.barcode_counts[]] | add)
    == .test.read_count
    )
  ' \
  "$RESULT_JSON" \
  >/dev/null


for result_file in \
  "$RESULT_JSON" \
  "$SUMMARY_TSV" \
  "$BARCODE_COUNTS_TSV"
do
  if [[ ! -s "$result_file" ]]; then
    echo "Acceptance result is missing or empty: ${result_file}" >&2
    exit 1
  fi
done

cat > "$RESULT_MARKDOWN" <<EOF
## Image ${IMAGE_VERSION} GPU Acceptance

Test completed: ${FINISHED_AT}

### Configuration

- GPU: \`${GPU_NAME}\`
- GPU memory: \`${GPU_MEMORY_MIB} MiB\`
- NVIDIA driver: \`${DRIVER_VERSION}\`
- Dorado: \`${DORADO_VERSION}\`
- Model: \`${DORADO_MODEL}\`
- Barcode kit: \`${BARCODE_KIT}\`

### Input

- POD5 files: \`${pod5_count}\`
- POD5 bytes: \`${pod5_bytes}\`
- Input directory: \`${INPUT_DIRECTORY}\`

### Results

- Dorado exit status: \`0\`
- Reads summarized: \`${READ_COUNT}\`
- Classified reads: \`${classified_reads}\`
- Unclassified reads: \`${UNKNOWN_READS}\`
- Classified percentage: \`${CLASSIFIED_PERCENT}%\`
- Output BAM bytes: \`${BAM_BYTES}\`
- Demultiplexed FASTQ files: \`${demux_file_count}\`
- Elapsed seconds: \`${elapsed_seconds}\`

### Barcode distribution

\`\`\`json
$(jq '.test.barcode_counts' "$RESULT_JSON")
\`\`\`

### Acceptance

Image \`${IMAGE_VERSION}\` passed:

- NVIDIA driver loading
- CUDA device access
- Dorado startup
- pinned model loading
- recursive POD5 discovery
- GPU basecalling
- BAM creation
- summary generation
- classification-aware demultiplexing
EOF

if [[ ! -s "$RESULT_MARKDOWN" ]]; then
  echo \
    "Acceptance Markdown report is missing or empty: " \
    "$RESULT_MARKDOWN" \
    >&2
  exit 1
fi

echo "GPU acceptance test completed successfully"
echo "JSON result: ${RESULT_JSON}"
echo "Markdown result: ${RESULT_MARKDOWN}"