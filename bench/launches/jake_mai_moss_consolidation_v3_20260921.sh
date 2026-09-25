#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/streammeco/run/StreamMeCo-consolidation
export PYTHONPATH=$PWD:$PWD/StreamMeCo:/opt/streammeco/repos/3D-Speaker
unset CONSOLIDATION_PACKET_BYTES

output_dir=bench/results/jake_path2_gemini_mai_moss_day1_first20min_consolidation_v3_20260921
source_dir=bench/results/jake_path2_gemini_mai_frames_day1_first20min_20260921
snapshot_dir=$source_dir/consolidation/snapshot_41
log=$output_dir/resume.log

mkdir -p "$output_dir/consolidation"
if [[ ! -d "$output_dir/consolidation/audits" ]]; then
  cp -a "$source_dir/consolidation/audits" "$output_dir/consolidation/audits"
fi

set +e
/opt/streammeco/.venv/bin/python -u -m bench \
  bench/configs/runs/jake_path2_gemini_mai_moss_day1_first20min_consolidation_v3.json \
  --resume-from "$snapshot_dir" 2>&1 | tee "$log"
run_status=${PIPESTATUS[0]}
set -e

printf '%s\n' "$run_status" > "$output_dir/exit_status"
printf '\nEXIT_STATUS=%s\n' "$run_status" >> "$log"
exit "$run_status"
