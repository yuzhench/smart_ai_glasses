#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/streammeco/run/StreamMeCo-consolidation
export PYTHONPATH=/opt/streammeco/run/StreamMeCo-consolidation:/opt/streammeco/run/StreamMeCo-consolidation/StreamMeCo:/opt/streammeco/repos/3D-Speaker
export CONSOLIDATION_PACKET_BYTES=2097152

output_dir=bench/results/jake_path2_gemini_mai_frames_day1_first20min_20260921
snapshot_dir="$output_dir/consolidation/snapshot_41"
log="$output_dir/resume_retry_20260921.log"

set +e
/opt/streammeco/.venv/bin/python -m bench \
  bench/configs/runs/jake_path2_gemini_mai_frames_day1_first20min.json \
  --resume-from "$snapshot_dir" 2>&1 | tee "$log"
run_status=${PIPESTATUS[0]}
set -e

printf '%s\n' "$run_status" > "$output_dir/resume_retry_20260921_exit_status"
printf '\nEXIT_STATUS=%s\n' "$run_status" >> "$log"
exit "$run_status"
