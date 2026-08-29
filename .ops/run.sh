#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

[[ -f .env ]] || { echo "Missing .env" >&2; exit 1; }
set -a
source .env
set +a

unexport_sensitive_environment() {
  local key
  for key in \
    MIGRATION_DB_USER MIGRATION_DB_PASSWORD VPS_PASSWORD VPS_HOST_FINGERPRINT \
    VPS_SSH_HOST_FINGERPRINT SSHPASS; do
    export -n "$key" 2>/dev/null || true
  done
}

for key in DB_HOST DB_NAME DB_USER DB_PASSWORD; do
  [[ -n "${!key:-}" ]] || { echo "Missing required environment key: $key" >&2; exit 1; }
done

db_host_lower="$(printf '%s' "$DB_HOST" | tr '[:upper:]' '[:lower:]')"
case "$db_host_lower" in
  localhost|localhost.localdomain|127.0.0.1|::1|\[::1\])
    echo "Local databases are forbidden for EDEN development." >&2
    exit 1
    ;;
esac

unexport_sensitive_environment
export ENVIRONMENT=development
export SCHEDULER_ENABLED=false

exec uv run uvicorn app.main:create_app \
  --factory \
  --host "${API_HOST:-127.0.0.1}" \
  --port "${API_PORT:-8000}" \
  --workers 1 \
  --reload
