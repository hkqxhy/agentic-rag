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

compose=(
  docker compose
  --env-file "$env_file"
  -f "$base_compose"
  -f "$staging_compose"
)

"${compose[@]}" config --quiet
"${compose[@]}" up --build --detach --remove-orphans --wait --wait-timeout 180

if [[ "${SKIP_KNOWLEDGE_INGEST:-0}" != "1" ]]; then
  echo "Publishing the reviewed knowledge corpus..."
  "${compose[@]}" exec -T worker python -m agentic_rag.knowledge.ingest
fi

echo "Published knowledge summary:"
"${compose[@]}" exec -T postgres \
  psql -U agentic_rag -d agentic_rag -v ON_ERROR_STOP=1 \
  -c "SELECT status, authority_level, COUNT(*) FROM knowledge_documents GROUP BY status, authority_level ORDER BY status, authority_level;" \
  -c "SELECT COUNT(*) AS active_chunks FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id WHERE c.status = 'active' AND d.status = 'active' AND d.authority_level IN ('official', 'maintained');" \
  -c "SELECT COUNT(*) AS active_embeddings FROM knowledge_embeddings e JOIN knowledge_chunks c ON c.id = e.chunk_id JOIN knowledge_documents d ON d.id = c.document_id WHERE c.status = 'active' AND d.status = 'active' AND d.authority_level IN ('official', 'maintained');"

"${compose[@]}" ps
