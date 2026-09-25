#!/usr/bin/env bash
set -Eeuo pipefail
cd /opt/streammeco/run/StreamMeCo-consolidation-v9-20260924
export PYTHONPATH=$PWD:$PWD/StreamMeCo:/opt/streammeco/repos/3D-Speaker
export PYTHONUNBUFFERED=1
source /opt/streammeco/secrets/path2_302.env
unset CONSOLIDATION_PACKET_BYTES
output_dir=bench/results/jake_path2_gemini_mai_moss_day1_first25min_consolidation_v9_20260924
export BENCH_RECORD_DIR=$PWD/$output_dir
mkdir -p "$output_dir"
set +e
/opt/streammeco/.venv/bin/python -u -m bench.diagnostics.recorded_run \
  bench/configs/runs/jake_path2_gemini_mai_moss_day1_first25min_consolidation_v9.json \
  2>&1 | tee "$output_dir/run.log"
run_status=${PIPESTATUS[0]}
set -e
printf '%s\n' "$run_status" > "$output_dir/exit_status"
printf '\nEXIT_STATUS=%s\n' "$run_status" >> "$output_dir/run.log"
exit "$run_status"
