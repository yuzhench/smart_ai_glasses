#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/streammeco/run/StreamMeCo-consolidation
export PYTHONPATH=/opt/streammeco/run/StreamMeCo-consolidation:/opt/streammeco/run/StreamMeCo-consolidation/StreamMeCo:/opt/streammeco/repos/3D-Speaker

output_dir=bench/results/jake_path2_gemini_mai_frames_day1_first20min_20260921
mkdir -p "$output_dir"

set +e
/opt/streammeco/.venv/bin/python -m bench \
  bench/configs/runs/jake_path2_gemini_mai_frames_day1_first20min.json \
  2>&1 | tee "$output_dir/run.log"
run_status=${PIPESTATUS[0]}
set -e

printf '%s\n' "$run_status" > "$output_dir/exit_status"
printf '\nEXIT_STATUS=%s\n' "$run_status" >> "$output_dir/run.log"
exit "$run_status"
