#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="$repo_root/deploy/env/staging.env"
base_compose="$repo_root/deploy/compose/docker-compose.yml"
staging_compose="$repo_root/deploy/compose/docker-compose.staging.yml"
base_url="${STAGING_VERIFY_BASE_URL:-http://127.0.0.1}"
suffix="$(date +%s)"
username="resilience_${suffix}"
email="${username}@example.com"
password="phase1-resilience-password-2026"
restore_database="agentic_rag_restore_${suffix}"
cookie_jar="$(mktemp)"
response_body="$(mktemp)"
backup_file="$(mktemp --suffix=.dump)"

compose=(
  docker compose
  --env-file "$env_file"
  -f "$base_compose"
  -f "$staging_compose"
)

cleanup() {
  "${compose[@]}" up --detach worker >/dev/null 2>&1 || true
  "${compose[@]}" exec -T postgres \
    dropdb --if-exists -U agentic_rag "$restore_database" >/dev/null 2>&1 || true
  rm -f "$cookie_jar" "$response_body" "$backup_file"
}
trap cleanup EXIT

request() {
  local expected_status="$1"
  shift
  local actual_status
  actual_status="$(curl --silent --show-error --output "$response_body" \
    --write-out '%{http_code}' "$@")"
  if [[ "$actual_status" != "$expected_status" ]]; then
    echo "Expected HTTP $expected_status but received $actual_status" >&2
    sed -n '1,10p' "$response_body" >&2
    return 1
  fi
}

json_value() {
  local field="$1"
  sed -n "s/.*\"${field}\":\"\([^\"]*\)\".*/\1/p" "$response_body" | head -n 1
}

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file" >&2
  exit 1
fi

echo "[1/8] Verify current service health"
request 200 "$base_url/health/live"
request 200 "$base_url/health/ready"

echo "[2/8] Register an isolated resilience-test account"
request 201 \
  --cookie-jar "$cookie_jar" \
  --header 'Content-Type: application/json' \
  --data "{\"email\":\"$email\",\"username\":\"$username\",\"password\":\"$password\"}" \
  "$base_url/api/v1/auth/register"

request 201 \
  --cookie "$cookie_jar" \
  --header 'Content-Type: application/json' \
  --data '{"title":"Worker recovery verification"}' \
  "$base_url/api/v1/conversations"
conversation_id="$(json_value id)"
if [[ -z "$conversation_id" ]]; then
  echo "Conversation id is missing from API response" >&2
  exit 1
fi

echo "[3/8] Stop Worker and verify a new run remains queued"
"${compose[@]}" stop worker
request 202 \
  --cookie "$cookie_jar" \
  --header 'Content-Type: application/json' \
  --header "Idempotency-Key: resilience-${suffix}" \
  --data '{"content":"验证 Worker 中断恢复"}' \
  "$base_url/api/v1/conversations/$conversation_id/messages"
run_id="$(json_value run_id)"
if [[ -z "$run_id" ]]; then
  echo "Run id is missing from API response" >&2
  exit 1
fi
request 200 --cookie "$cookie_jar" "$base_url/api/v1/conversations/$conversation_id"
grep -q '"status":"queued"' "$response_body"

echo "[4/8] Start Worker and verify the queued run completes"
"${compose[@]}" up --detach --wait --wait-timeout 120 worker
completed=false
for _ in $(seq 1 30); do
  request 200 --cookie "$cookie_jar" "$base_url/api/v1/conversations/$conversation_id"
  if grep -q '"role":"assistant"' "$response_body"; then
    completed=true
    break
  fi
  sleep 1
done
if [[ "$completed" != "true" ]]; then
  echo "Queued run $run_id did not complete after Worker recovery" >&2
  exit 1
fi

echo "[5/8] Restart API and Web and verify session/data persistence"
"${compose[@]}" restart api web
"${compose[@]}" up --detach --wait --wait-timeout 120 api web
request 200 --cookie "$cookie_jar" "$base_url/api/v1/auth/me"
request 200 --cookie "$cookie_jar" "$base_url/api/v1/conversations/$conversation_id"
grep -q '"role":"assistant"' "$response_body"

echo "[6/8] Create and restore a PostgreSQL backup into an isolated database"
"${compose[@]}" exec -T postgres \
  pg_dump -U agentic_rag -d agentic_rag --format=custom >"$backup_file"
test -s "$backup_file"
"${compose[@]}" exec -T postgres createdb -U agentic_rag "$restore_database"
"${compose[@]}" exec -T postgres \
  pg_restore -U agentic_rag -d "$restore_database" --exit-on-error --no-owner <"$backup_file"
schema_version="$("${compose[@]}" exec -T postgres \
  psql -U agentic_rag -d "$restore_database" --tuples-only --no-align \
  --command 'SELECT version_num FROM alembic_version;')"
if [[ -z "$schema_version" ]]; then
  echo "Restored database is missing the Alembic schema version" >&2
  exit 1
fi

echo "[7/8] Soft-delete the temporary conversation"
request 204 \
  --cookie "$cookie_jar" \
  --request DELETE \
  "$base_url/api/v1/conversations/$conversation_id"

echo "[8/8] Capture final service and host resource snapshots"
"${compose[@]}" ps
docker stats --no-stream
free -h
df -h /

echo "PASS: Worker recovery, service restart persistence, and isolated backup restore"
