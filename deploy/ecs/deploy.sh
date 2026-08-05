#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="$repo_root/deploy/env/production.env"
base_compose="$repo_root/deploy/compose/docker-compose.yml"
prod_compose="$repo_root/deploy/compose/docker-compose.prod.yml"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy production.env.example and fill in real secrets." >&2
  exit 1
fi

docker compose \
  --env-file "$env_file" \
  -f "$base_compose" \
  -f "$prod_compose" \
  config --quiet

docker compose \
  --env-file "$env_file" \
  -f "$base_compose" \
  -f "$prod_compose" \
  up --build --detach --remove-orphans

docker compose \
  --env-file "$env_file" \
  -f "$base_compose" \
  -f "$prod_compose" \
  ps
