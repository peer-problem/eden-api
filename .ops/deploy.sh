#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

COMMAND="${1:-deploy}"
shift || true

source "$PROJECT_ROOT/scripts/deploy_support.sh"

if [[ "${BASH_SOURCE[0]}" != "$0" && "${EDEN_OPS_LIBRARY_ONLY:-false}" == true ]]; then
  return 0
fi

case "$COMMAND" in
  backup|restore)
    echo "Database backup and restore operations are disabled by project policy." >&2
    exit 2
    ;;
  finalize-phase1-contract)
    load_runtime_environment
    require_commands uv systemctl curl
    require_environment DB_USER DB_PASSWORD MIGRATION_DB_USER MIGRATION_DB_PASSWORD
    DB_RUNTIME_USER="$DB_USER"
    DB_RUNTIME_PASSWORD="$DB_PASSWORD"
    contract_applied=0
    finalization_complete=0
    recover_contract() {
      if (( finalization_complete == 1 )); then
        return
      fi
      if (( contract_applied == 1 )); then
        export DB_USER="$MIGRATION_DB_USER" DB_PASSWORD="$MIGRATION_DB_PASSWORD"
        uv run alembic downgrade 20260829_0008 2>/dev/null || true
      fi
      export DB_USER="$DB_RUNTIME_USER" DB_PASSWORD="$DB_RUNTIME_PASSWORD"
      rm -f /etc/systemd/system/eden-api.service.d/30-phase1-contract.conf
      rm -f /opt/eden/shared/phase1-contract.env
      systemctl daemon-reload 2>/dev/null || true
      systemctl start eden-api 2>/dev/null || true
      systemctl enable --now eden-phase1-soak.timer 2>/dev/null || true
      systemctl enable --now eden-phase2-pilot-report.timer 2>/dev/null || true
    }
    trap recover_contract EXIT
    uv run python scripts/phase1_soak.py status >/dev/null
    systemctl stop eden-phase1-soak.timer 2>/dev/null || true
    systemctl stop eden-phase2-pilot-report.timer 2>/dev/null || true
    systemctl stop eden-api
    export DB_USER="$MIGRATION_DB_USER" DB_PASSWORD="$MIGRATION_DB_PASSWORD"
    uv run alembic upgrade 20260829_0007
    contract_applied=1
    export DB_USER="$DB_RUNTIME_USER" DB_PASSWORD="$DB_RUNTIME_PASSWORD"
    systemctl start eden-api
    curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null
    uv run python scripts/phase1_soak.py warmup >/dev/null
    uv run python scripts/phase1_soak.py sample >/dev/null
    uv run python scripts/phase1_soak.py status >/dev/null
    uv run python scripts/phase1_soak.py status >/dev/null
    install -d -m 755 /etc/systemd/system/eden-api.service.d
    printf 'SNAPSHOT_RETENTION_ENABLED=true\n' >/opt/eden/shared/phase1-contract.env
    chmod 600 /opt/eden/shared/phase1-contract.env
    cat >/etc/systemd/system/eden-api.service.d/30-phase1-contract.conf <<'UNIT'
[Service]
EnvironmentFile=/opt/eden/shared/phase1-contract.env
UNIT
    systemctl daemon-reload
    systemctl restart eden-api
    for _ in $(seq 1 30); do
      curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null && break
      sleep 1
    done
    curl -fsS http://127.0.0.1:8000/internal/readiness >/dev/null
    uv run python scripts/phase1_soak.py warmup >/dev/null
    systemctl enable --now eden-phase1-soak.timer
    systemctl enable --now eden-phase2-pilot-report.timer 2>/dev/null || true
    finalization_complete=1
    trap - EXIT
    echo "Phase 1 snapshot storage contract migration completed."
    exit 0
    ;;
  soak-7d|phase2-soak)
    phase2_soak_status "${1:-}"
    exit 0
    ;;
  deploy)
    ;;
  --help|-h)
    echo "Usage: deploy.sh [deploy|finalize-phase1-contract|soak-7d]"
    exit 0
    ;;
  *)
    echo "Unknown operations command: ${COMMAND}" >&2
    exit 2
    ;;
esac

[[ -f .env ]] || { echo "Missing .env" >&2; exit 1; }
source .env
unexport_sensitive_environment
: "${VPS_IP_ADDRESS:?Missing VPS_IP_ADDRESS}"
VPS_USERNAME="${VPS_USERNAME:-${VPS_USRENAME:-}}"
: "${VPS_USERNAME:?Missing VPS_USERNAME}"
: "${VPS_PASSWORD:?Missing VPS_PASSWORD}"
VPS_HOST_FINGERPRINT="${VPS_HOST_FINGERPRINT:-${VPS_SSH_HOST_FINGERPRINT:-}}"
: "${VPS_HOST_FINGERPRINT:?Missing VPS_HOST_FINGERPRINT}"

uv lock --check
uv run ruff check app scripts migrations tests
uv run pytest -q
require_commands npm node
npm ci --prefix dashboard
npm run build --prefix dashboard
[[ -f dashboard/dist/index.html ]] || {
  echo "Dashboard build did not produce dashboard/dist/index.html." >&2
  exit 1
}

release_id="$(date -u +%Y%m%dT%H%M%SZ)"
remote_release="/opt/eden/releases/${release_id}"
export SSHPASS="$VPS_PASSWORD"
require_commands ssh-keyscan ssh-keygen sshpass rsync ssh
known_hosts_file="$(mktemp)"
known_hosts_raw="$(mktemp)"
cleanup_local_ssh() { rm -f "$known_hosts_file" "$known_hosts_raw"; }
trap cleanup_local_ssh EXIT
verify_ssh_host_key "$VPS_IP_ADDRESS" "$VPS_HOST_FINGERPRINT" \
  "$known_hosts_raw" "$known_hosts_file"
ssh_options=(-o "UserKnownHostsFile=$known_hosts_file" -o StrictHostKeyChecking=yes)

sshpass -e ssh "${ssh_options[@]}" "${VPS_USERNAME}@${VPS_IP_ADDRESS}" \
  "install -d -o root -g root -m 755 /opt/eden/releases && \
   install -d -o root -g root -m 750 '${remote_release}'"
export RSYNC_RSH="sshpass -e ssh -o UserKnownHostsFile=${known_hosts_file} -o StrictHostKeyChecking=yes"
rsync -az --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '.env' \
  --exclude '.agent/' --exclude '.agents/' \
  --exclude 'node_modules/' \
  --exclude 'tmp/' --exclude 'cache/' --exclude '__pycache__/' \
  --exclude '.ruff_cache/' --exclude '.pytest_cache/' --exclude '.mypy_cache/' \
  --exclude '.playwright-cli/' --exclude 'playwright-report/' \
  --exclude 'test-results/' --exclude 'coverage/' --exclude 'htmlcov/' \
  --exclude '.coverage' --exclude 'build/' \
  ./ "${VPS_USERNAME}@${VPS_IP_ADDRESS}:${remote_release}/"

sshpass -e ssh "${ssh_options[@]}" "${VPS_USERNAME}@${VPS_IP_ADDRESS}" \
  "bash '${remote_release}/scripts/deploy_remote.sh' '${remote_release}'"

echo "Deployed ${release_id}."
