#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
vus="${VUS:-20}"
iterations="${ITERATIONS:-1}"
max_duration="${MAX_DURATION:-2m}"
run_id="${RUN_ID:-platform-$(date +%Y%m%d%H%M%S)}"
k6_image="${K6_IMAGE:-grafana/k6:latest}"
test_script="$repo_root/load/k6/phase2-platform.js"

k6_args=(
  run
  -e "BASE_URL=${BASE_URL:-http://127.0.0.1}"
  -e "RUN_ID=$run_id"
  -e "VUS=$vus"
  -e "ITERATIONS=$iterations"
  -e "MAX_DURATION=$max_duration"
  -e "AUTH_P95_MS=${AUTH_P95_MS:-20000}"
  -e "READ_P95_MS=${READ_P95_MS:-3000}"
  -e "WRITE_P95_MS=${WRITE_P95_MS:-5000}"
  -e "JOURNEY_P95_MS=${JOURNEY_P95_MS:-30000}"
)

if command -v k6 >/dev/null 2>&1; then
  echo "Using host k6 binary: $(command -v k6)"
  exec k6 "${k6_args[@]}" "$test_script"
fi

echo "Host k6 binary not found; falling back to Docker image: $k6_image" >&2
docker run --rm --network host \
  -v "$repo_root/load/k6:/scripts:ro" \
  -e BASE_URL="${BASE_URL:-http://127.0.0.1}" \
  -e RUN_ID="$run_id" \
  -e VUS="$vus" \
  -e ITERATIONS="$iterations" \
  -e MAX_DURATION="$max_duration" \
  -e AUTH_P95_MS="${AUTH_P95_MS:-20000}" \
  -e READ_P95_MS="${READ_P95_MS:-3000}" \
  -e WRITE_P95_MS="${WRITE_P95_MS:-5000}" \
  -e JOURNEY_P95_MS="${JOURNEY_P95_MS:-30000}" \
  "$k6_image" run /scripts/phase2-platform.js
