#!/usr/bin/env bash
set -euo pipefail

IMAGE_VERSION="${IMAGE_VERSION:?IMAGE_VERSION is required}"
EXPECTED_WRAPPER_VERSION="${EXPECTED_WRAPPER_VERSION:-${IMAGE_VERSION}}"
DORADO_MODEL="${DORADO_MODEL:-dna_r10.4.1_e8.2_400bps_fast@v5.2.0}"
BARCODE_KIT="${BARCODE_KIT:-SQK-RBK114-24}"
BARCODE_VALUES="${BARCODE_VALUES:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24}"
BATCH_FILE_COUNTS="${BATCH_FILE_COUNTS:-3,7,2,5,6}"
INPUT_DIRECTORY="${INPUT_DIRECTORY:?INPUT_DIRECTORY is required}"
EXPECTED_POD5_COUNT="${EXPECTED_POD5_COUNT:-23}"
EXPECTED_BATCH_COUNT="${EXPECTED_BATCH_COUNT:-5}"
OUTPUT_DIRECTORY="${OUTPUT_DIRECTORY:-${AZ_BATCH_TASK_WORKING_DIR:-/mnt/resource}/nanopore-acceptance/output}"

PORESIPPR_ENVIRONMENT="${PORESIPPR_ENVIRONMENT:-/opt/micromamba/root/envs/poresippr}"
PORESIPPR_SCHEDULER="${PORESIPPR_SCHEDULER:-/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py}"
PORESIPPR_TARGETS="${PORESIPPR_TARGETS:-/opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta}"
PORESIPPR_REPOSITORY_COMMIT="${PORESIPPR_REPOSITORY_COMMIT:-045baf1ba8372813e05bd981dd57886739e052df}"
PORESIPPR_TARGETS_SHA256="${PORESIPPR_TARGETS_SHA256:-6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9}"
PORESIPPR_TARGETS_SEQUENCE_COUNT="${PORESIPPR_TARGETS_SEQUENCE_COUNT:-6663}"
DIAGNOSTIC_SCHEDULER_OVERRIDE="${DIAGNOSTIC_SCHEDULER_OVERRIDE:-false}"

# Final-iteration minimums are below both the historical iteration45 values
# and the discovery iteration5 values. Per-iteration checks require presence
# and cumulative non-decrease, not exact counts.
RB03_GNTK_MIN="${RB03_GNTK_MIN:-50}"
RB03_O128_WZX_MIN="${RB03_O128_WZX_MIN:-20}"
RB03_H2_FLIC_MIN="${RB03_H2_FLIC_MIN:-10}"
RB03_STX1C_MIN="${RB03_STX1C_MIN:-3}"
RB04_GNTK_MIN="${RB04_GNTK_MIN:-35}"
RB04_O41_WZX_MIN="${RB04_O41_WZX_MIN:-20}"
RB04_H26_FLIC_MIN="${RB04_H26_FLIC_MIN:-25}"
RB04_STX1D_MIN="${RB04_STX1D_MIN:-20}"

PORESIPPR_BIN_DIRECTORY="${PORESIPPR_ENVIRONMENT}/bin"
MODEL_DIRECTORY="/opt/ont/models/${DORADO_MODEL}"
SCHEDULER_INPUT_DIRECTORY="${OUTPUT_DIRECTORY}/scheduler-input"
SCHEDULER_OUTPUT_DIRECTORY="${OUTPUT_DIRECTORY}/scheduler"
RUN_CSV="${OUTPUT_DIRECTORY}/scheduler-run.csv"
METADATA_CSV="${OUTPUT_DIRECTORY}/scheduler-metadata.csv"
STATE_JSON="${SCHEDULER_OUTPUT_DIRECTORY}/state.json"
STATUS_JSON="${SCHEDULER_OUTPUT_DIRECTORY}/status.json"
RESULT_JSON="${OUTPUT_DIRECTORY}/acceptance-result.json"
RESULT_MARKDOWN="${OUTPUT_DIRECTORY}/acceptance-result.md"
SUMMARY_TSV="${OUTPUT_DIRECTORY}/summary.tsv"
BARCODE_COUNTS_TSV="${OUTPUT_DIRECTORY}/barcode-counts.tsv"
MAPPING_DISCOVERY_TSV="${OUTPUT_DIRECTORY}/mapping-discovery.tsv"
ASSERTION_RESULTS_TSV="${OUTPUT_DIRECTORY}/assertion-results.tsv"
EVIDENCE_ARCHIVE="${OUTPUT_DIRECTORY}/scheduler-evidence.tar.gz"

export PATH="${PORESIPPR_BIN_DIRECTORY}:/opt/micromamba/bin:/opt/ont/dorado/bin:/usr/local/bin:/usr/bin:/bin"
export MAMBA_ROOT_PREFIX="/opt/micromamba/root"

for command_name in dorado foodport-nanopore-task jq minimap2 nvidia-smi python readlink samtools sha256sum tar; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "Required command is missing: ${command_name}" >&2; exit 1; }
done

for value_name in EXPECTED_POD5_COUNT EXPECTED_BATCH_COUNT RB03_GNTK_MIN RB03_O128_WZX_MIN RB03_H2_FLIC_MIN RB03_STX1C_MIN RB04_GNTK_MIN RB04_O41_WZX_MIN RB04_H26_FLIC_MIN RB04_STX1D_MIN; do
  value="${!value_name}"
  case "$value" in ''|*[!0-9]*) echo "${value_name} must be a non-negative integer" >&2; exit 1;; esac
done
case "$DIAGNOSTIC_SCHEDULER_OVERRIDE" in true|false) ;; *) echo "DIAGNOSTIC_SCHEDULER_OVERRIDE must be true or false" >&2; exit 1;; esac

IFS=',' read -r -a batch_counts <<<"$BATCH_FILE_COUNTS"
batch_total=0
for raw_count in "${batch_counts[@]}"; do
  count="${raw_count//[[:space:]]/}"
  case "$count" in ''|*[!0-9]*|0) echo "Invalid BATCH_FILE_COUNTS entry: ${count:-<empty>}" >&2; exit 1;; esac
  batch_total="$((batch_total + count))"
done
[[ "${#batch_counts[@]}" -eq "$EXPECTED_BATCH_COUNT" && "$batch_total" -eq "$EXPECTED_POD5_COUNT" ]] || { echo "Batch plan does not match expected counts" >&2; exit 1; }

for required_option in --manifest-prefix --control-blob; do
  foodport-nanopore-task --help | grep -q -- "$required_option" || { echo "Installed wrapper lacks ${required_option}" >&2; exit 1; }
done
WRAPPER_VERSION_OUTPUT="$(foodport-nanopore-task --version)"
EXPECTED_WRAPPER_OUTPUT="foodport-nanopore-task ${EXPECTED_WRAPPER_VERSION}"
[[ "$WRAPPER_VERSION_OUTPUT" == "$EXPECTED_WRAPPER_OUTPUT" ]] || { echo "Unexpected wrapper version: ${WRAPPER_VERSION_OUTPUT}" >&2; exit 1; }
WRAPPER_EXECUTABLE="$(readlink -f "$(command -v foodport-nanopore-task)")"
WRAPPER_SHA256="$(sha256sum "$WRAPPER_EXECUTABLE" | awk '{print $1}')"

echo "Installed wrapper: ${WRAPPER_VERSION_OUTPUT}"
echo "Wrapper executable: ${WRAPPER_EXECUTABLE}"
echo "Wrapper SHA-256: ${WRAPPER_SHA256}"

[[ -s "$PORESIPPR_SCHEDULER" && -s "$PORESIPPR_TARGETS" && -d "$MODEL_DIRECTORY" && -d "$INPUT_DIRECTORY" ]] || { echo "Required scheduler, targets, model, or input is missing" >&2; exit 1; }
pod5_count="$(find "$INPUT_DIRECTORY" -type f -iname '*.pod5' | wc -l)"
pod5_bytes="$(find "$INPUT_DIRECTORY" -type f -iname '*.pod5' -printf '%s\n' | awk '{total += $1} END {print total + 0}')"
[[ "$pod5_count" -eq "$EXPECTED_POD5_COUNT" && "$pod5_bytes" -gt 0 ]] || { echo "Expected ${EXPECTED_POD5_COUNT} POD5 files, found ${pod5_count}" >&2; exit 1; }

actual_commit="$(jq -r '.commit // empty' /etc/foodport/poresippr-repository.json)"
[[ "$actual_commit" == "$PORESIPPR_REPOSITORY_COMMIT" ]] || { echo "Unexpected PoreSippR repository commit" >&2; exit 1; }
actual_targets_sha256="$(sha256sum "$PORESIPPR_TARGETS" | awk '{print $1}')"
actual_targets_count="$(grep -c '^>' "$PORESIPPR_TARGETS")"
[[ "$actual_targets_sha256" == "$PORESIPPR_TARGETS_SHA256" && "$actual_targets_count" -eq "$PORESIPPR_TARGETS_SEQUENCE_COUNT" ]] || { echo "Installed targets do not match pinned release" >&2; exit 1; }

DRIVER_VERSION="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1 | tr -d '\r')"
GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1 | tr -d '\r')"
GPU_MEMORY_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d '\r ')"
DORADO_VERSION="$(dorado --version 2>&1 | tail -n1)"
PYTHON_VERSION="$(python --version 2>&1)"
MINIMAP2_VERSION="$(minimap2 --version 2>&1 | head -n1)"
SAMTOOLS_VERSION="$(samtools --version 2>&1 | head -n1)"

rm -rf "$OUTPUT_DIRECTORY"
mkdir -p "$OUTPUT_DIRECTORY" "$SCHEDULER_INPUT_DIRECTORY" "$SCHEDULER_OUTPUT_DIRECTORY"
printf 'run_id,reference,pod5_dir,output_dir,barcode,barcode_values\n' >"$RUN_CSV"
printf 'acceptance,%s,%s,%s,%s,"%s"\n' "$PORESIPPR_TARGETS" "$SCHEDULER_INPUT_DIRECTORY" "$SCHEDULER_OUTPUT_DIRECTORY" "$BARCODE_KIT" "$BARCODE_VALUES" >>"$RUN_CSV"
printf 'Barcode,SEQID,OLNID\n3,2026-MIN-0105,BDS-VTEC099\n4,2026-MIN-0106,BDS-VTEC100\n' >"$METADATA_CSV"

mapfile -t pod5_files < <(find "$INPUT_DIRECTORY" -type f -iname '*.pod5' -print | LC_ALL=C sort -V)
STARTED_AT="$(date --utc --iso-8601=seconds)"
start_seconds="$SECONDS"
next_file=0
iteration=0

for raw_count in "${batch_counts[@]}"; do
  batch_size="${raw_count//[[:space:]]/}"
  iteration="$((iteration + 1))"
  echo "Staging scheduler iteration ${iteration}/${EXPECTED_BATCH_COUNT} with ${batch_size} POD5 files"
  for ((i=0; i<batch_size; i++)); do
    source_path="${pod5_files[$next_file]}"
    ln -s "$source_path" "$SCHEDULER_INPUT_DIRECTORY/$(basename "$source_path")"
    next_file="$((next_file + 1))"
  done

  PYTHONDONTWRITEBYTECODE=1 python "$PORESIPPR_SCHEDULER" "$RUN_CSV" "$METADATA_CSV" \
    --model "$MODEL_DIRECTORY" --device cuda:0 --poll-seconds 1 --stable-seconds 1 --stable-polls 1 \
    --idle-timeout-seconds 300 --max-walltime-seconds 7200 --max-batch-files "$batch_size" \
    --mapping-threads 5 --keep-mapping-bam --keep-batch-work --once

  [[ -s "$STATE_JSON" && -s "$STATUS_JSON" ]] || { echo "Scheduler state/status missing after iteration ${iteration}" >&2; exit 1; }
  observed_batches="$(jq -r '.batches | length' "$STATE_JSON")"
  observed_processed="$(jq -r '.processed_pod5 | length' "$STATE_JSON")"
  [[ "$observed_batches" -eq "$iteration" && "$observed_processed" -eq "$next_file" ]] || { echo "Unexpected state after iteration ${iteration}" >&2; exit 1; }
done

first_processed_count="$(jq -r '.processed_pod5 | length' "$STATE_JSON")"
first_batch_count="$(jq -r '.batches | length' "$STATE_JSON")"
first_next_batch="$(jq -r '.next_batch_number' "$STATE_JSON")"
first_result_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/results" -type f -name '*.csv' | wc -l)"
[[ "$first_processed_count" -eq "$EXPECTED_POD5_COUNT" && "$first_batch_count" -eq "$EXPECTED_BATCH_COUNT" && "$first_result_count" -gt 0 ]] || { echo "Multi-wave scheduler run did not produce expected outputs" >&2; exit 1; }

echo "Running duplicate-prevention pass"
PYTHONDONTWRITEBYTECODE=1 python "$PORESIPPR_SCHEDULER" "$RUN_CSV" "$METADATA_CSV" \
  --model "$MODEL_DIRECTORY" --device cuda:0 --poll-seconds 1 --stable-seconds 1 --stable-polls 1 \
  --idle-timeout-seconds 60 --max-walltime-seconds 300 --max-batch-files 1 \
  --mapping-threads 5 --keep-mapping-bam --keep-batch-work --once

second_processed_count="$(jq -r '.processed_pod5 | length' "$STATE_JSON")"
second_batch_count="$(jq -r '.batches | length' "$STATE_JSON")"
second_next_batch="$(jq -r '.next_batch_number' "$STATE_JSON")"
second_result_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/results" -type f -name '*.csv' | wc -l)"
duplicate_prevented=false
if [[ "$second_processed_count" -eq "$first_processed_count" && "$second_batch_count" -eq "$first_batch_count" && "$second_next_batch" -eq "$first_next_batch" && "$second_result_count" -eq "$first_result_count" ]]; then duplicate_prevented=true; else echo "Duplicate-prevention pass changed durable counts" >&2; exit 1; fi

status_value="$(jq -r '.status' "$STATUS_JSON")"
status_reason="$(jq -r '.reason' "$STATUS_JSON")"
retained_fastq_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/fastq" -type f \( -name '*.fastq' -o -name '*.fastq.gz' \) | wc -l)"
result_csv_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/results" -type f -name '*.csv' | wc -l)"
mapping_bam_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/mapping" -type f -name '*.bam' | wc -l)"
mapping_bai_count="$(find "$SCHEDULER_OUTPUT_DIRECTORY/mapping" -type f -name '*.bai' | wc -l)"
[[ "$status_value" == completed && "$status_reason" == once-complete && "$retained_fastq_count" -gt 0 && "$mapping_bam_count" -gt 0 && "$mapping_bai_count" -gt 0 ]] || { echo "Scheduler status or output inventory is invalid" >&2; exit 1; }

basecalls_bams=("$SCHEDULER_OUTPUT_DIRECTORY"/working/batch-*/basecalls.bam)
[[ -e "${basecalls_bams[0]}" ]] || { echo "Retained basecalls BAMs are missing" >&2; exit 1; }
: >"$SUMMARY_TSV"; BAM_BYTES=0
for basecalls_bam in "${basecalls_bams[@]}"; do
  [[ -s "$basecalls_bam" ]] || { echo "Empty basecalls BAM: ${basecalls_bam}" >&2; exit 1; }
  if [[ ! -s "$SUMMARY_TSV" ]]; then dorado summary "$basecalls_bam" >"$SUMMARY_TSV"; else dorado summary "$basecalls_bam" | tail -n +2 >>"$SUMMARY_TSV"; fi
  BAM_BYTES="$((BAM_BYTES + $(stat --format='%s' "$basecalls_bam")))"
done
READ_COUNT="$(awk 'END {print (NR > 0 ? NR - 1 : 0)}' "$SUMMARY_TSV")"
rm -rf "$SCHEDULER_OUTPUT_DIRECTORY/working"

awk -F '\t' 'NR==1 {for(i=1;i<=NF;i++) if($i=="barcode_arrangement") c=i; next} {b=(c?$c:"unknown"); if(b=="") b="unknown"; counts[b]++} END {for(b in counts) print b "\t" counts[b]}' "$SUMMARY_TSV" | LC_ALL=C sort >"$BARCODE_COUNTS_TSV"
BARCODE_COUNTS_JSON="$(jq --raw-input --slurp 'split("\n")|map(select(length>0))|map(split("\t"))|map({key:.[0],value:(.[1]|tonumber)})|from_entries' "$BARCODE_COUNTS_TSV")"
UNKNOWN_READS="$(jq -r '.unknown // 0' <<<"$BARCODE_COUNTS_JSON")"
classified_reads="$((READ_COUNT - UNKNOWN_READS))"

printf 'result_file\titeration\tbarcode\ttarget\tmapped_reads\n' >"$MAPPING_DISCOVERY_TSV"
for csv_file in "$SCHEDULER_OUTPUT_DIRECTORY"/results/*.csv; do
  file_name="$(basename "$csv_file")"
  file_iteration="$(sed -n 's/.*_iteration\([0-9][0-9]*\)\.csv$/\1/p' <<<"$file_name")"
  barcode="$(sed -n 's/.*2026-MIN-0105.*/3/p; s/.*2026-MIN-0106.*/4/p' <<<"$file_name")"
  awk -F, -v f="$file_name" -v i="$file_iteration" -v b="$barcode" 'NR>1 && $1!="" && $1!="genome_coverage" && ($2+0)>0 {print f "\t" i "\t" b "\t" $1 "\t" $2}' "$csv_file" >>"$MAPPING_DISCOVERY_TSV"
done

result_row_count="$(awk -F '\t' 'NR>1 {n++} END {print n+0}' "$MAPPING_DISCOVERY_TSV")"
total_mapped_reads="$(awk -F '\t' 'NR>1 {n+=$5} END {print n+0}' "$MAPPING_DISCOVERY_TSV")"
[[ "$result_row_count" -gt 0 && "$total_mapped_reads" -gt 0 ]] || { echo "No positive mapping results were discovered" >&2; exit 1; }

# Assertions: every key target must be present in all five cumulative iterations,
# counts must never decrease, and the final count must meet a conservative floor.
printf 'sample\tbarcode\ttarget\titeration\tobserved\trequirement\tthreshold\tpassed\n' >"$ASSERTION_RESULTS_TSV"
assert_target_series() {
  local sample="$1" barcode="$2" target="$3" final_min="$4"
  local previous=-1 iteration observed requirement threshold passed
  for iteration in $(seq 1 "$EXPECTED_BATCH_COUNT"); do
    observed="$(awk -F '\t' -v i="$iteration" -v b="$barcode" -v t="$target" 'NR>1 && $2==i && $3==b && $4==t {sum+=$5} END {print sum+0}' "$MAPPING_DISCOVERY_TSV")"
    requirement="present-and-nondecreasing"; threshold="$previous"; passed=true
    if [[ "$observed" -lt 1 || "$observed" -lt "$previous" ]]; then passed=false; fi
    if [[ "$iteration" -eq "$EXPECTED_BATCH_COUNT" ]]; then
      requirement="final-minimum-and-nondecreasing"; threshold="$final_min"
      if [[ "$observed" -lt "$final_min" || "$observed" -lt "$previous" ]]; then passed=false; fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$sample" "$barcode" "$target" "$iteration" "$observed" "$requirement" "$threshold" "$passed" >>"$ASSERTION_RESULTS_TSV"
    [[ "$passed" == true ]] || { echo "Mapping assertion failed: ${sample} ${target} iteration ${iteration}, observed ${observed}, requirement ${requirement}, threshold ${threshold}" >&2; exit 1; }
    previous="$observed"
  done
}

assert_target_series 2026-MIN-0105 3 gntK "$RB03_GNTK_MIN"
assert_target_series 2026-MIN-0105 3 O/O128-3-wzx-origin/1-1443 "$RB03_O128_WZX_MIN"
assert_target_series 2026-MIN-0105 3 H/H2-2-fliC-origin/1-1494 "$RB03_H2_FLIC_MIN"
assert_target_series 2026-MIN-0105 3 StxOp2064_Stx1c_12 "$RB03_STX1C_MIN"
assert_target_series 2026-MIN-0106 4 gntK "$RB04_GNTK_MIN"
assert_target_series 2026-MIN-0106 4 O/O41-1-wzx-origin/1-1320 "$RB04_O41_WZX_MIN"
assert_target_series 2026-MIN-0106 4 H/H26-1-fliC-origin/1-1674 "$RB04_H26_FLIC_MIN"
assert_target_series 2026-MIN-0106 4 StxOp2209_Stx1d_4 "$RB04_STX1D_MIN"

assertion_count="$(awk 'NR>1 {n++} END {print n+0}' "$ASSERTION_RESULTS_TSV")"
assertion_pass_count="$(awk -F '\t' 'NR>1 && $8=="true" {n++} END {print n+0}' "$ASSERTION_RESULTS_TSV")"
[[ "$assertion_count" -eq 40 && "$assertion_pass_count" -eq "$assertion_count" ]] || { echo "Expected 40 passing mapping assertions" >&2; exit 1; }

mapping_targets_json="$(awk -F '\t' 'NR>1 {counts[$4]+=$5} END {for(k in counts) print k "\t" counts[k]}' "$MAPPING_DISCOVERY_TSV" | LC_ALL=C sort | jq --raw-input --slurp 'split("\n")|map(select(length>0))|map(split("\t"))|map({key:.[0],value:(.[1]|tonumber)})|from_entries')"
elapsed_seconds="$((SECONDS - start_seconds))"; FINISHED_AT="$(date --utc --iso-8601=seconds)"
cp "$STATE_JSON" "$OUTPUT_DIRECTORY/scheduler-state.json"; cp "$STATUS_JSON" "$OUTPUT_DIRECTORY/scheduler-status.json"
tar -czf "$EVIDENCE_ARCHIVE" -C "$OUTPUT_DIRECTORY" scheduler/state.json scheduler/status.json scheduler/fastq scheduler/mapping scheduler/results scheduler-run.csv scheduler-metadata.csv summary.tsv barcode-counts.tsv mapping-discovery.tsv assertion-results.tsv

accepted=true; [[ "$DIAGNOSTIC_SCHEDULER_OVERRIDE" == false ]] || accepted=false
jq -n \
  --arg image_version "$IMAGE_VERSION" --arg wrapper_version "$WRAPPER_VERSION_OUTPUT" --arg wrapper_executable "$WRAPPER_EXECUTABLE" --arg wrapper_sha256 "$WRAPPER_SHA256" \
  --arg driver_version "$DRIVER_VERSION" --arg gpu_name "$GPU_NAME" --argjson gpu_memory_mib "$GPU_MEMORY_MIB" --arg dorado_version "$DORADO_VERSION" --arg dorado_model "$DORADO_MODEL" --arg barcode_kit "$BARCODE_KIT" --arg batch_plan "$BATCH_FILE_COUNTS" \
  --arg python_version "$PYTHON_VERSION" --arg minimap2_version "$MINIMAP2_VERSION" --arg samtools_version "$SAMTOOLS_VERSION" --arg repository_commit "$actual_commit" --arg scheduler_path "$PORESIPPR_SCHEDULER" --arg targets_path "$PORESIPPR_TARGETS" --arg targets_sha256 "$actual_targets_sha256" \
  --arg status "$status_value" --arg reason "$status_reason" --arg started_at "$STARTED_AT" --arg finished_at "$FINISHED_AT" --argjson accepted "$accepted" --argjson diagnostic_scheduler_override "$DIAGNOSTIC_SCHEDULER_OVERRIDE" \
  --argjson targets_sequence_count "$actual_targets_count" --argjson elapsed_seconds "$elapsed_seconds" --argjson pod5_count "$pod5_count" --argjson pod5_bytes "$pod5_bytes" --argjson processed_pod5_count "$second_processed_count" --argjson batch_count "$second_batch_count" \
  --argjson retained_fastq_count "$retained_fastq_count" --argjson result_csv_count "$result_csv_count" --argjson mapping_bam_count "$mapping_bam_count" --argjson mapping_bai_count "$mapping_bai_count" --argjson duplicate_processing_prevented "$duplicate_prevented" \
  --argjson read_count "$READ_COUNT" --argjson classified_reads "$classified_reads" --argjson unknown_reads "$UNKNOWN_READS" --argjson bam_bytes "$BAM_BYTES" --argjson barcode_counts "$BARCODE_COUNTS_JSON" --argjson result_row_count "$result_row_count" --argjson total_mapped_reads "$total_mapped_reads" --argjson mapping_targets "$mapping_targets_json" --argjson assertion_count "$assertion_count" --argjson assertion_pass_count "$assertion_pass_count" \
  '{schema_version:4,accepted:$accepted,diagnostic_scheduler_override:$diagnostic_scheduler_override,image_version:$image_version,gpu:{name:$gpu_name,memory_mib:$gpu_memory_mib,driver_version:$driver_version},dorado:{version:$dorado_version,model:$dorado_model,barcode_kit:$barcode_kit},input:{dataset:"poresippr-dataset2",directory:"20260421_MIN",pod5_count:$pod5_count,pod5_bytes:$pod5_bytes,batch_plan:$batch_plan},poresippr:{python_version:$python_version,wrapper_version:$wrapper_version,wrapper_executable:$wrapper_executable,wrapper_sha256:$wrapper_sha256,minimap2_version:$minimap2_version,samtools_version:$samtools_version,repository_commit:$repository_commit,scheduler:$scheduler_path,targets:$targets_path,targets_sha256:$targets_sha256,targets_sequence_count:$targets_sequence_count},scheduler:{status:$status,reason:$reason,processed_pod5_count:$processed_pod5_count,batch_count:$batch_count,duplicate_processing_prevented:$duplicate_processing_prevented,retained_fastq_count:$retained_fastq_count,result_csv_count:$result_csv_count,mapping_bam_count:$mapping_bam_count,mapping_bai_count:$mapping_bai_count},mapping:{discovery_mode:false,per_iteration_assertions:true,result_row_count:$result_row_count,total_mapped_reads:$total_mapped_reads,targets:$mapping_targets,final_iteration:5,assertion_count:$assertion_count,assertion_pass_count:$assertion_pass_count,assertion_file:"assertion-results.tsv",samples:[{barcode:3,seqid:"2026-MIN-0105",olnid:"BDS-VTEC099"},{barcode:4,seqid:"2026-MIN-0106",olnid:"BDS-VTEC100"}]},test:{started_at:$started_at,finished_at:$finished_at,elapsed_seconds:$elapsed_seconds,read_count:$read_count,classified_reads:$classified_reads,unknown_reads:$unknown_reads,bam_bytes:$bam_bytes,barcode_counts:$barcode_counts}}' >"$RESULT_JSON"

cat >"$RESULT_MARKDOWN" <<EOF2
## Image ${IMAGE_VERSION} GPU Scheduler Acceptance

- Dataset: \`poresippr-dataset2/20260421_MIN\`
- POD5 files: \`${pod5_count}\`
- Batch plan: \`${BATCH_FILE_COUNTS}\`
- Scheduler batches: \`${second_batch_count}\`
- Mapping assertions: \`${assertion_pass_count}/${assertion_count} passed\`
- Assertion policy: key targets present and nondecreasing in every cumulative iteration; conservative minimums at iteration ${EXPECTED_BATCH_COUNT}
- Duplicate processing prevented: \`${duplicate_prevented}\`
- Completed: \`${FINISHED_AT}\`
EOF2

for result_file in "$RESULT_JSON" "$RESULT_MARKDOWN" "$SUMMARY_TSV" "$BARCODE_COUNTS_TSV" "$MAPPING_DISCOVERY_TSV" "$ASSERTION_RESULTS_TSV" "$EVIDENCE_ARCHIVE"; do [[ -s "$result_file" ]] || { echo "Acceptance output missing: ${result_file}" >&2; exit 1; }; done

echo "GPU scheduler acceptance completed successfully"
echo "JSON result: ${RESULT_JSON}"
echo "Mapping discovery: ${MAPPING_DISCOVERY_TSV}"
echo "Assertion results: ${ASSERTION_RESULTS_TSV}"
echo "Evidence archive: ${EVIDENCE_ARCHIVE}"
