#!/usr/bin/env bash

set -euo pipefail

IMAGE_VERSION="${IMAGE_VERSION:?IMAGE_VERSION is required}"
DORADO_MODEL="${DORADO_MODEL:-dna_r10.4.1_e8.2_400bps_fast@v5.2.0}"
BARCODE_KIT="${BARCODE_KIT:-SQK-RBK114-24}"
BARCODE_VALUES="${BARCODE_VALUES:-12,16,20,22}"
EXPECTED_MAPPING_BARCODE="${EXPECTED_MAPPING_BARCODE:-22}"
EXPECTED_MAPPING_TARGET="${EXPECTED_MAPPING_TARGET:-gntK}"
EXPECTED_MAPPING_READS="${EXPECTED_MAPPING_READS:-1}"
INPUT_DIRECTORY="${INPUT_DIRECTORY:?INPUT_DIRECTORY is required}"
OUTPUT_DIRECTORY="${OUTPUT_DIRECTORY:-${AZ_BATCH_TASK_WORKING_DIR:-/mnt/resource}/nanopore-acceptance/output}"

PORESIPPR_ENVIRONMENT="${PORESIPPR_ENVIRONMENT:-/opt/micromamba/root/envs/poresippr}"
PORESIPPR_SCHEDULER="${PORESIPPR_SCHEDULER:-/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py}"
PORESIPPR_TARGETS="${PORESIPPR_TARGETS:-/opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta}"
PORESIPPR_REPOSITORY_COMMIT="${PORESIPPR_REPOSITORY_COMMIT:-691b3a3c2944139cb0093f81909331f7b8d46983}"
PORESIPPR_TARGETS_SHA256="${PORESIPPR_TARGETS_SHA256:-6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9}"
PORESIPPR_TARGETS_SEQUENCE_COUNT="${PORESIPPR_TARGETS_SEQUENCE_COUNT:-6663}"
DIAGNOSTIC_SCHEDULER_OVERRIDE="${DIAGNOSTIC_SCHEDULER_OVERRIDE:-false}"

PORESIPPR_BIN_DIRECTORY="${PORESIPPR_ENVIRONMENT}/bin"
MODEL_DIRECTORY="/opt/ont/models/${DORADO_MODEL}"
SCHEDULER_OUTPUT_DIRECTORY="${OUTPUT_DIRECTORY}/scheduler"
RUN_CSV="${OUTPUT_DIRECTORY}/scheduler-run.csv"
METADATA_CSV="${OUTPUT_DIRECTORY}/scheduler-metadata.csv"
STATE_JSON="${SCHEDULER_OUTPUT_DIRECTORY}/state.json"
STATUS_JSON="${SCHEDULER_OUTPUT_DIRECTORY}/status.json"
RESULT_JSON="${OUTPUT_DIRECTORY}/acceptance-result.json"
RESULT_MARKDOWN="${OUTPUT_DIRECTORY}/acceptance-result.md"
SUMMARY_TSV="${OUTPUT_DIRECTORY}/summary.tsv"
BARCODE_COUNTS_TSV="${OUTPUT_DIRECTORY}/barcode-counts.tsv"
EVIDENCE_ARCHIVE="${OUTPUT_DIRECTORY}/scheduler-evidence.tar.gz"

export PATH="${PORESIPPR_BIN_DIRECTORY}:/opt/micromamba/bin:/opt/ont/dorado/bin:/usr/local/bin:/usr/bin:/bin"
export MAMBA_ROOT_PREFIX="/opt/micromamba/root"

case "$EXPECTED_MAPPING_BARCODE" in
  ''|*[!0-9]*)
    echo "EXPECTED_MAPPING_BARCODE must be a non-negative integer" >&2
    exit 1
    ;;
esac

case "$EXPECTED_MAPPING_READS" in
  ''|*[!0-9]*)
    echo "EXPECTED_MAPPING_READS must be a non-negative integer" >&2
    exit 1
    ;;
esac

if [[ -z "$EXPECTED_MAPPING_TARGET" ]]; then
  echo "EXPECTED_MAPPING_TARGET must not be empty" >&2
  exit 1
fi

case "$DIAGNOSTIC_SCHEDULER_OVERRIDE" in
  true|false)
    ;;
  *)
    echo "DIAGNOSTIC_SCHEDULER_OVERRIDE must be true or false" >&2
    exit 1
    ;;
esac

mkdir -p "$OUTPUT_DIRECTORY"
rm -rf "$SCHEDULER_OUTPUT_DIRECTORY"
mkdir -p "$SCHEDULER_OUTPUT_DIRECTORY"

for command_name in dorado jq minimap2 nvidia-smi python samtools tar; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is missing: ${command_name}" >&2
    exit 1
  fi
done

for path in "$PORESIPPR_SCHEDULER" "$PORESIPPR_TARGETS"; do
  if [[ ! -s "$path" ]]; then
    echo "Required PoreSippR path is missing or empty: ${path}" >&2
    exit 1
  fi
done

if [[ ! -d "$MODEL_DIRECTORY" ]] || [[ ! -d "$INPUT_DIRECTORY" ]]; then
  echo "Model or input directory is missing" >&2
  exit 1
fi

pod5_count="$(
  find \
    "$INPUT_DIRECTORY" \
    -type f \
    -iname '*.pod5' |
  wc -l
)"

pod5_bytes="$(
  find \
    "$INPUT_DIRECTORY" \
    -type f \
    -iname '*.pod5' \
    -printf '%s\n' |
  awk \
    '{total += $1} END {print total + 0}'
)"

pod5_file="$(
  find \
    "$INPUT_DIRECTORY" \
    -type f \
    -iname '*.pod5' \
    -print \
    -quit
)"

if [[ "$pod5_count" -ne 1 ]] ||
   [[ -z "$pod5_file" ]] ||
   [[ "$pod5_bytes" -lt 1 ]]; then
  echo "Acceptance expects exactly one POD5 file, found ${pod5_count}" >&2
  exit 1
fi

actual_commit="$(jq -r '.commit // empty' /etc/foodport/poresippr-repository.json)"
if [[ "$actual_commit" != "$PORESIPPR_REPOSITORY_COMMIT" ]]; then
  echo "Unexpected PoreSippR repository commit" >&2
  exit 1
fi

actual_targets_sha256="$(sha256sum "$PORESIPPR_TARGETS" | awk '{print $1}')"
actual_targets_count="$(grep -c '^>' "$PORESIPPR_TARGETS")"
if [[ "$actual_targets_sha256" != "$PORESIPPR_TARGETS_SHA256" ]] ||
   [[ "$actual_targets_count" -ne "$PORESIPPR_TARGETS_SEQUENCE_COUNT" ]]; then
  echo "Installed PoreSippR targets do not match the pinned release" >&2
  exit 1
fi

DRIVER_VERSION="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1 | tr -d '\r')"
GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1 | tr -d '\r')"
GPU_MEMORY_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n 1 | tr -d '\r ')"
DORADO_VERSION="$(dorado --version 2>&1 | tail -n 1)"
PYTHON_VERSION="$(python --version 2>&1)"
MINIMAP2_VERSION="$(minimap2 --version 2>&1 | head -n 1)"
SAMTOOLS_VERSION="$(samtools --version 2>&1 | head -n 1)"

IFS=',' read -r -a barcode_array <<<"$BARCODE_VALUES"
if [[ "${#barcode_array[@]}" -eq 0 ]]; then
  echo "BARCODE_VALUES contains no barcodes" >&2
  exit 1
fi

printf 'run_id,reference,pod5_dir,output_dir,barcode,barcode_values\n' >"$RUN_CSV"
printf 'acceptance,%s,%s,%s,%s,"%s"\n' \
  "$PORESIPPR_TARGETS" "$INPUT_DIRECTORY" "$SCHEDULER_OUTPUT_DIRECTORY" "$BARCODE_KIT" "$BARCODE_VALUES" \
  >>"$RUN_CSV"

printf 'Barcode,SEQID,OLNID\n' >"$METADATA_CSV"
for raw_barcode in "${barcode_array[@]}"; do
  barcode="${raw_barcode//[[:space:]]/}"
  printf '%s,acceptance_barcode%02d,ACCEPTANCE-%02d\n' "$barcode" "$barcode" "$barcode" >>"$METADATA_CSV"
done

STARTED_AT="$(date --utc --iso-8601=seconds)"
start_seconds="$SECONDS"

echo "Running first standalone scheduler pass"
set +e
PYTHONDONTWRITEBYTECODE=1 python "$PORESIPPR_SCHEDULER" \
  "$RUN_CSV" "$METADATA_CSV" \
  --model "$MODEL_DIRECTORY" \
  --device cuda:0 \
  --poll-seconds 1 \
  --stable-seconds 1 \
  --stable-polls 1 \
  --idle-timeout-seconds 300 \
  --max-walltime-seconds 7200 \
  --max-batch-files 1 \
  --mapping-threads 5 \
  --keep-mapping-bam \
  --keep-batch-work \
  --once
first_exit_status=$?
set -e
if [[ "$first_exit_status" -ne 0 ]]; then
  echo "First scheduler pass failed: ${first_exit_status}" >&2
  exit 1
fi

for path in "$STATE_JSON" "$STATUS_JSON"; do
  if [[ ! -s "$path" ]]; then
    echo "Scheduler output is missing: ${path}" >&2
    exit 1
  fi
done

first_processed_count="$(jq -r '.processed_pod5 | length' "$STATE_JSON")"
first_batch_count="$(jq -r '.batches | length' "$STATE_JSON")"
first_next_batch="$(jq -r '.next_batch_number' "$STATE_JSON")"
first_result_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/results" -type f -name '*.csv' | wc -l)"

if [[ "$first_processed_count" -ne "$pod5_count" ]] || [[ "$first_batch_count" -lt 1 ]] || [[ "$first_result_count" -lt 1 ]]; then
  echo "First scheduler pass did not produce the expected durable outputs" >&2
  exit 1
fi

echo "Running second scheduler pass to verify duplicate prevention"
set +e
PYTHONDONTWRITEBYTECODE=1 python "$PORESIPPR_SCHEDULER" \
  "$RUN_CSV" "$METADATA_CSV" \
  --model "$MODEL_DIRECTORY" \
  --device cuda:0 \
  --poll-seconds 1 \
  --stable-seconds 1 \
  --stable-polls 1 \
  --idle-timeout-seconds 60 \
  --max-walltime-seconds 300 \
  --max-batch-files 1 \
  --mapping-threads 5 \
  --keep-mapping-bam \
  --keep-batch-work \
  --once
second_exit_status=$?
set -e
if [[ "$second_exit_status" -ne 0 ]]; then
  echo "Second scheduler pass failed: ${second_exit_status}" >&2
  exit 1
fi

second_processed_count="$(jq -r '.processed_pod5 | length' "$STATE_JSON")"
second_batch_count="$(jq -r '.batches | length' "$STATE_JSON")"
second_next_batch="$(jq -r '.next_batch_number' "$STATE_JSON")"
second_result_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/results" -type f -name '*.csv' | wc -l)"

duplicate_prevented=false
if [[ "$second_processed_count" -eq "$first_processed_count" ]] &&
   [[ "$second_batch_count" -eq "$first_batch_count" ]] &&
   [[ "$second_next_batch" -eq "$first_next_batch" ]] &&
   [[ "$second_result_count" -eq "$first_result_count" ]]; then
  duplicate_prevented=true
else
  echo "Second scheduler pass changed durable processing counts" >&2
  exit 1
fi

status_value="$(jq -r '.status' "$STATUS_JSON")"
status_reason="$(jq -r '.reason' "$STATUS_JSON")"
retained_fastq_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/fastq" -type f \( -name '*.fastq' -o -name '*.fastq.gz' \) | wc -l)"
result_csv_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/results" -type f -name '*.csv' | wc -l)"
mapping_bam_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/mapping" -type f -name '*.bam' | wc -l)"
mapping_bai_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/mapping" -type f -name '*.bai' | wc -l)"

if [[ "$status_value" != "completed" ]] || [[ "$status_reason" != "once-complete" ]] ||
   [[ "$retained_fastq_count" -lt 1 ]] || [[ "$mapping_bam_count" -lt 1 ]] || [[ "$mapping_bai_count" -lt 1 ]]; then
  echo "Scheduler status or output inventory is invalid" >&2
  exit 1
fi

basecalls_bam="$(find "$SCHEDULER_OUTPUT_DIRECTORY/working" -type f -name basecalls.bam -print -quit)"
if [[ -z "$basecalls_bam" ]] || [[ ! -s "$basecalls_bam" ]]; then
  echo "Retained scheduler basecalls BAM is missing" >&2
  exit 1
fi

dorado summary "$basecalls_bam" >"$SUMMARY_TSV"
READ_COUNT="$(awk 'END {print (NR > 0 ? NR - 1 : 0)}' "$SUMMARY_TSV")"
BAM_BYTES="$(stat --format='%s' "$basecalls_bam")"

echo "Removing retained batch work before packaging durable evidence"
rm -rf "${SCHEDULER_OUTPUT_DIRECTORY}/working"

awk -F '\t' '
  NR == 1 {
    for (i = 1; i <= NF; i++) if ($i == "barcode_arrangement") barcode_column = i
    if (!barcode_column) { print "barcode_arrangement column is missing" > "/dev/stderr"; exit 1 }
    next
  }
  { barcode = $barcode_column; if (barcode == "") barcode = "unknown"; counts[barcode]++ }
  END { for (barcode in counts) print barcode "\t" counts[barcode] }
' "$SUMMARY_TSV" | LC_ALL=C sort >"$BARCODE_COUNTS_TSV"

BARCODE_COUNTS_JSON="$(jq --raw-input --slurp 'split("\n") | map(select(length > 0)) | map(split("\t")) | map({key: .[0], value: (.[1] | tonumber)}) | from_entries' "$BARCODE_COUNTS_TSV")"
UNKNOWN_READS="$(jq -r '.unknown // 0' <<<"$BARCODE_COUNTS_JSON")"
classified_reads="$((READ_COUNT - UNKNOWN_READS))"
demux_file_count="$retained_fastq_count"

result_row_count="$(awk -F, 'FNR > 1 && $1 != "genome_coverage" {count++} END {print count + 0}' "$SCHEDULER_OUTPUT_DIRECTORY"/results/*.csv)"
total_mapped_reads="$(awk -F, 'FNR > 1 && $1 != "genome_coverage" {total += $2} END {print total + 0}' "$SCHEDULER_OUTPUT_DIRECTORY"/results/*.csv)"
if [[ "$result_row_count" -lt 1 ]] || [[ "$total_mapped_reads" -lt 1 ]]; then
  echo "Scheduler mapping outputs contain no positive mapped-read results" >&2
  exit 1
fi

expected_mapping_csv="$(
  printf '%s/scheduler/results/acceptance_barcode%02d_iteration1.csv' \
    "$OUTPUT_DIRECTORY" \
    "$EXPECTED_MAPPING_BARCODE"
)"

if [[ ! -s "$expected_mapping_csv" ]]; then
  echo "Expected mapping CSV is missing: ${expected_mapping_csv}" >&2
  exit 1
fi

observed_mapping_reads="$(
  awk \
    -F, \
    -v target="$EXPECTED_MAPPING_TARGET" \
    '$1 == target {total += $2} END {print total + 0}' \
    "$expected_mapping_csv"
)"

if [[ "$observed_mapping_reads" -ne "$EXPECTED_MAPPING_READS" ]]; then
  echo "Unexpected mapped-read count for ${EXPECTED_MAPPING_TARGET}" >&2
  echo "Expected: ${EXPECTED_MAPPING_READS}" >&2
  echo "Actual:   ${observed_mapping_reads}" >&2
  exit 1
fi

mapping_targets_json="$(
  awk \
    -F, \
    '$1 != "" && $1 != "gene_name" && $1 != "genome_coverage" {
      print $1 "\t" $2
    }' \
    "$SCHEDULER_OUTPUT_DIRECTORY"/results/*.csv | \
  awk -F '\t' \
    '{counts[$1] += $2} END {for (key in counts) print key "\t" counts[key]}' | \
  LC_ALL=C sort | \
  jq --raw-input --slurp \
    'split("\n")
     | map(select(length > 0))
     | map(split("\t"))
     | map({key: .[0], value: (.[1] | tonumber)})
     | from_entries'
)"

elapsed_seconds="$((SECONDS - start_seconds))"
FINISHED_AT="$(date --utc --iso-8601=seconds)"

cp "$STATE_JSON" "${OUTPUT_DIRECTORY}/scheduler-state.json"
cp "$STATUS_JSON" "${OUTPUT_DIRECTORY}/scheduler-status.json"

tar -czf "$EVIDENCE_ARCHIVE" \
  -C "$OUTPUT_DIRECTORY" \
  scheduler/state.json \
  scheduler/status.json \
  scheduler/fastq \
  scheduler/mapping \
  scheduler/results \
  scheduler-run.csv \
  scheduler-metadata.csv \
  summary.tsv \
  barcode-counts.tsv

jq --null-input \
  --arg image_version "$IMAGE_VERSION" \
  --arg driver_version "$DRIVER_VERSION" \
  --arg gpu_name "$GPU_NAME" \
  --argjson gpu_memory_mib "$GPU_MEMORY_MIB" \
  --arg dorado_version "$DORADO_VERSION" \
  --arg dorado_model "$DORADO_MODEL" \
  --arg barcode_kit "$BARCODE_KIT" \
  --arg python_version "$PYTHON_VERSION" \
  --arg minimap2_version "$MINIMAP2_VERSION" \
  --arg samtools_version "$SAMTOOLS_VERSION" \
  --arg repository_commit "$actual_commit" \
  --arg scheduler_path "$PORESIPPR_SCHEDULER" \
  --arg targets_path "$PORESIPPR_TARGETS" \
  --arg targets_sha256 "$actual_targets_sha256" \
  --arg status "$status_value" \
  --arg reason "$status_reason" \
  --arg started_at "$STARTED_AT" \
  --arg finished_at "$FINISHED_AT" \
  --argjson diagnostic_scheduler_override "$DIAGNOSTIC_SCHEDULER_OVERRIDE" \
  --argjson targets_sequence_count "$actual_targets_count" \
  --argjson elapsed_seconds "$elapsed_seconds" \
  --argjson pod5_count "$pod5_count" \
  --argjson pod5_bytes "$pod5_bytes" \
  --argjson first_exit_status "$first_exit_status" \
  --argjson second_exit_status "$second_exit_status" \
  --argjson processed_pod5_count "$second_processed_count" \
  --argjson batch_count "$second_batch_count" \
  --argjson retained_fastq_count "$retained_fastq_count" \
  --argjson result_csv_count "$result_csv_count" \
  --argjson mapping_bam_count "$mapping_bam_count" \
  --argjson mapping_bai_count "$mapping_bai_count" \
  --argjson duplicate_processing_prevented "$duplicate_prevented" \
  --argjson read_count "$READ_COUNT" \
  --argjson classified_reads "$classified_reads" \
  --argjson unknown_reads "$UNKNOWN_READS" \
  --argjson bam_bytes "$BAM_BYTES" \
  --argjson demux_file_count "$demux_file_count" \
  --argjson barcode_counts "$BARCODE_COUNTS_JSON" \
  --argjson result_row_count "$result_row_count" \
  --argjson total_mapped_reads "$total_mapped_reads" \
  --arg expected_mapping_target "$EXPECTED_MAPPING_TARGET" \
  --argjson expected_mapping_barcode "$EXPECTED_MAPPING_BARCODE" \
  --argjson expected_mapping_reads "$EXPECTED_MAPPING_READS" \
  --argjson observed_mapping_reads "$observed_mapping_reads" \
  --argjson mapping_targets "$mapping_targets_json" \
  '{
    schema_version: 2,
    accepted: ($diagnostic_scheduler_override | not),
    diagnostic_scheduler_override: $diagnostic_scheduler_override,
    image_version: $image_version,
    gpu: {name: $gpu_name, memory_mib: $gpu_memory_mib, driver_version: $driver_version},
    dorado: {version: $dorado_version, model: $dorado_model, barcode_kit: $barcode_kit},
    input: {directory: "acceptance-input", pod5_count: $pod5_count, pod5_bytes: $pod5_bytes},
    poresippr: {
      python_version: $python_version,
      minimap2_version: $minimap2_version,
      samtools_version: $samtools_version,
      repository_commit: $repository_commit,
      scheduler: $scheduler_path,
      targets: $targets_path,
      targets_sha256: $targets_sha256,
      targets_sequence_count: $targets_sequence_count
    },
    scheduler: {
      first_exit_status: $first_exit_status,
      second_exit_status: $second_exit_status,
      status: $status,
      reason: $reason,
      processed_pod5_count: $processed_pod5_count,
      batch_count: $batch_count,
      duplicate_processing_prevented: $duplicate_processing_prevented,
      retained_fastq_count: $retained_fastq_count,
      result_csv_count: $result_csv_count,
      mapping_bam_count: $mapping_bam_count,
      mapping_bai_count: $mapping_bai_count
    },
    mapping: {
      result_row_count: $result_row_count,
      total_mapped_reads: $total_mapped_reads,
      targets: $mapping_targets,
      expected: {
        barcode: $expected_mapping_barcode,
        target: $expected_mapping_target,
        reads: $expected_mapping_reads
      },
      observed_expected_target_reads: $observed_mapping_reads
    },
    test: {
      started_at: $started_at,
      finished_at: $finished_at,
      elapsed_seconds: $elapsed_seconds,
      read_count: $read_count,
      classified_reads: $classified_reads,
      unknown_reads: $unknown_reads,
      bam_bytes: $bam_bytes,
      demux_file_count: $demux_file_count,
      barcode_counts: $barcode_counts
    }
  }' >"$RESULT_JSON"

jq -e \
  --argjson diagnostic_scheduler_override \
    "$DIAGNOSTIC_SCHEDULER_OVERRIDE" \
  --arg expected_mapping_target "$EXPECTED_MAPPING_TARGET" \
  --argjson expected_mapping_barcode "$EXPECTED_MAPPING_BARCODE" \
  --argjson expected_mapping_reads "$EXPECTED_MAPPING_READS" \
  '
    .schema_version == 2
    and .diagnostic_scheduler_override
        == $diagnostic_scheduler_override
    and .accepted == ($diagnostic_scheduler_override | not)
    and .scheduler.duplicate_processing_prevented == true
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
    and .mapping.total_mapped_reads > 0
    and .mapping.expected.barcode == $expected_mapping_barcode
    and .mapping.expected.target == $expected_mapping_target
    and .mapping.expected.reads == $expected_mapping_reads
    and .mapping.observed_expected_target_reads == $expected_mapping_reads
    and .mapping.targets[$expected_mapping_target] == $expected_mapping_reads
  ' \
  "$RESULT_JSON" \
  >/dev/null

cat >"$RESULT_MARKDOWN" <<EOF
## Image ${IMAGE_VERSION} GPU Scheduler Acceptance

Test completed: ${FINISHED_AT}

### Configuration

- GPU: \`${GPU_NAME}\`
- GPU memory: \`${GPU_MEMORY_MIB} MiB\`
- NVIDIA driver: \`${DRIVER_VERSION}\`
- Dorado: \`${DORADO_VERSION}\`
- Model: \`${DORADO_MODEL}\`
- Barcode kit: \`${BARCODE_KIT}\`
- PoreSippR commit: \`${actual_commit}\`
- Targets SHA-256: \`${actual_targets_sha256}\`
- Target records: \`${actual_targets_count}\`

- Diagnostic scheduler override: \`${DIAGNOSTIC_SCHEDULER_OVERRIDE}\`

### Scheduler results

- First exit status: \`${first_exit_status}\`
- Second exit status: \`${second_exit_status}\`
- Final status: \`${status_value}\`
- Final reason: \`${status_reason}\`
- Processed POD5 files: \`${second_processed_count}\`
- Batches: \`${second_batch_count}\`
- Duplicate processing prevented: \`${duplicate_prevented}\`
- Retained FASTQ files: \`${retained_fastq_count}\`
- Mapping result CSV files: \`${result_csv_count}\`
- Mapping BAM files: \`${mapping_bam_count}\`
- Mapping rows: \`${result_row_count}\`
- Total mapped reads: \`${total_mapped_reads}\`


- Expected mapping: \`barcode${EXPECTED_MAPPING_BARCODE} ${EXPECTED_MAPPING_TARGET}=${EXPECTED_MAPPING_READS}\`
- Observed expected-target reads: \`${observed_mapping_reads}\`

### Basecalling evidence

- Reads summarized: \`${READ_COUNT}\`
- Classified reads: \`${classified_reads}\`
- Unclassified reads: \`${UNKNOWN_READS}\`
- Basecalls BAM bytes: \`${BAM_BYTES}\`
- Elapsed seconds: \`${elapsed_seconds}\`

### Acceptance

$(
  if [[ "$DIAGNOSTIC_SCHEDULER_OVERRIDE" == true ]]; then
    printf '%s' \
      "Image ${IMAGE_VERSION} completed a diagnostic scheduler run using " \
      "an overridden scheduler file. This does not constitute acceptance " \
      "of the immutable image."
  else
    printf '%s' \
      "Image ${IMAGE_VERSION} passed standalone scheduler acceptance, " \
      "including GPU basecalling, retained FASTQ fragments, cumulative " \
      "target mapping, durable state/status files, and duplicate-processing " \
      "prevention on a second run."
  fi
)

EOF

for result_file in "$RESULT_JSON" "$RESULT_MARKDOWN" "$SUMMARY_TSV" "$BARCODE_COUNTS_TSV" "$EVIDENCE_ARCHIVE"; do
  if [[ ! -s "$result_file" ]]; then
    echo "Acceptance result is missing or empty: ${result_file}" >&2
    exit 1
  fi
done

echo "GPU scheduler acceptance test completed successfully"
echo "JSON result: ${RESULT_JSON}"
echo "Markdown result: ${RESULT_MARKDOWN}"
echo "Evidence archive: ${EVIDENCE_ARCHIVE}"
