#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="$repo_root/deploy/env/staging.env"
base_compose="$repo_root/deploy/compose/docker-compose.yml"
staging_compose="$repo_root/deploy/compose/docker-compose.staging.yml"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file" >&2
  exit 1
fi

docker compose \
  --env-file "$env_file" \
  -f "$base_compose" \
  -f "$staging_compose" \
  exec -T worker python -m agentic_rag.knowledge.ingest "$@"
