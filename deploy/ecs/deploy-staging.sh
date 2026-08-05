#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="$repo_root/deploy/env/staging.env"
base_compose="$repo_root/deploy/compose/docker-compose.yml"
staging_compose="$repo_root/deploy/compose/docker-compose.staging.yml"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy staging.env.example and fill in the ECS public IP and secrets." >&2
  exit 1
fi

if grep -Eq '203\.0\.113\.10|replace-with-' "$env_file"; then
  echo "Refusing to deploy with example values in $env_file." >&2
  exit 1
fi

docker compose \
  --env-file "$env_file" \
  -f "$base_compose" \
  -f "$staging_compose" \
  config --quiet

docker compose \
  --env-file "$env_file" \
  -f "$base_compose" \
  -f "$staging_compose" \
  up --build --detach --remove-orphans --wait --wait-timeout 180

docker compose \
  --env-file "$env_file" \
  -f "$base_compose" \
  -f "$staging_compose" \
  ps
