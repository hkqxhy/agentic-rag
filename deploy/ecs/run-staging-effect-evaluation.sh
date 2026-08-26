#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR"

COMPOSE_ARGS=(
  --env-file deploy/env/staging.env
  -f deploy/compose/docker-compose.yml
  -f deploy/compose/docker-compose.staging.yml
)

mkdir -p reports
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report_path="reports/staging-effect-${timestamp}.json"
progress_path="reports/staging-effect-${timestamp}.progress.log"

echo "Running comprehensive effect evaluation inside the ECS deployment..."
docker compose "${COMPOSE_ARGS[@]}" exec -T worker \
  agentic-rag-staging-eval \
  --base-url http://caddy \
  --dataset eval/cases/staging_comprehensive.jsonl \
  >"$report_path" \
  2>"$progress_path"

echo "Evaluation report: $report_path"
echo "Progress log: $progress_path"
python3 -c 'import json, sys; s=json.load(open(sys.argv[1], encoding="utf-8"))["summary"]; print(json.dumps(s, ensure_ascii=False, indent=2))' "$report_path"
